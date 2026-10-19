"""Full managed-data snapshots and isolated recovery. Never overwrite a live root."""
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .auditstate import encoded
from .folders import FolderAccess, scoped_path
from .integrity import Issues, metadata, release_root, snapshot
from .migration import portable
from .recovery import extraction_paths
from .safety import DomainError, disjoint, offline_path, tree_files
from .store import Store, digest, now, sha256
from .upgrades import RUNNING, process_stamp, writer_active
from . import backup_requests as requests

PRIVATE=('assets','evidence','geometry-packages','geometry-results','test-launchers','upgrades','retained')
MAX_MANIFEST=64*1024*1024
MAX_FILES=100_000
MAX_DATA=256*1024**3
FUTURES={}


def identifier(value):
    try:
        if str(uuid.UUID(value))!=value:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise DomainError('Invalid backup/job identifier.')
    return value


def read_json(path,limit=MAX_MANIFEST):
    path=offline_path(path)
    try:
        with path.open('rb') as stream:raw=stream.read(limit+1)
    except FileNotFoundError as error:raise DomainError('Snapshot metadata not found.',404) from error
    except OSError as error:raise DomainError('Snapshot metadata cannot be read.',409) from error
    if len(raw)>limit:raise DomainError('Snapshot metadata exceeds its reader bound.',409)
    try:return json.loads(raw)
    except (ValueError,TypeError) as error:raise DomainError('Snapshot metadata is malformed.',409) from error


def write_json(path,value):
    path=offline_path(path);temporary=offline_path(path.with_suffix(path.suffix+'.tmp'))
    raw=encoded(value).encode()
    if len(raw)>MAX_MANIFEST:raise DomainError('Snapshot metadata exceeds 64 MiB.')
    temporary.write_bytes(raw);os.replace(temporary,path)


def db_copy(source,destination):
    source=offline_path(source);destination=offline_path(destination)
    if destination.exists():raise DomainError('Snapshot database target already exists.',409)
    old=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=30)
    new=sqlite3.connect(destination)
    try:
        old.backup(new)
        if new.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise DomainError('SQLite snapshot integrity failed.',409)
    finally:new.close();old.close()


def hashed_file(path):
    path=offline_path(path);before=path.stat();value=sha256(path);after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise DomainError('File changed during snapshot inventory.',409)
    return {'sha256':value,'size':before.st_size},(before.st_size,before.st_mtime_ns)


def directories(root):
    return [Path(folder).relative_to(root).as_posix() for folder,_,_ in os.walk(root,followlinks=False)
            if Path(folder)!=root]


def validate_inventory(probe,state,groups,report_reader):
    """Check captured hashes against the frozen DB, without reading original pointers."""
    private={};releases={}
    for group in groups:
        entries={item['path'].casefold():item for item in group['files']}
        if len(entries)!=len(group['files']):raise DomainError('Case-colliding snapshot paths.',409)
        if group['kind']=='release':releases[group['id']]=entries
        elif group['kind']=='private':private.update({(group['name']+'/'+path).casefold():value for path,value in entries.items()})
    def check(path,expected,size=None):
        value=private.get(path.casefold())
        if not value or value['sha256']!=expected or (size is not None and value['size']!=size):
            raise DomainError('Captured artifact differs from its database binding: '+path,409)
    for row in state['tables']['releases']:
        if row['status'] in ('building','failed') or not row['manifest']:continue
        expected={item['path'].casefold():item for item in json.loads(row['manifest'])}
        actual=releases.get(row['id'],{})
        if set(expected)!=set(actual) or any((item['size'],item['sha256'])!=(actual[key]['size'],actual[key]['sha256']) for key,item in expected.items()):
            raise DomainError('Completed release does not match its immutable manifest: '+row['id'],409)
    for row in state['tables']['assets']:
        identifier(row['id']);portable(row['filename'])
        if '/' in row['filename']:raise DomainError('Asset filename is not a basename.')
        check('assets/'+row['id']+'/'+row['filename'],row['sha256'],row['size'])
    for row in state['tables']['syslogs']:
        if not row['id'].startswith('uploaded-'):raise DomainError('Invalid uploaded syslog ID.')
        identifier(row['id'][9:]);check('evidence/'+row['id']+'/nx.syslog',row['sha256'],row['size'])
    for row in state['tables']['test_launchers']:
        value=json.loads(row['payload']);identifier(row['release_id']);revision=value.get('package_revision')
        if revision and not re.fullmatch('[a-f0-9]{64}',revision):raise DomainError('Unsafe launcher revision.')
        check('test-launchers/'+row['release_id']+('/'+revision if revision else '')+'/NX2512_Test.zip',value['package_sha256'])
    for row in state['tables']['geometry_jobs']:
        value=json.loads(row['payload']);identifier(row['id'])
        if value.get('package_sha256'):
            check('geometry-packages/'+row['id']+'/NX_STEP.zip',value['package_sha256'],value['package_size'])
        for digest,collector in value.get('collector_packages',{}).items():
            if not re.fullmatch('[a-f0-9]{64}',digest) or digest!=collector['sha256']:raise DomainError('Invalid collector snapshot binding.')
            check('geometry-results/collectors/'+row['id']+'/'+digest+'/collector.zip',digest,collector['size'])
    for row in state['tables']['geometry_results']:
        value=json.loads(row['payload']);identifier(row['id'])
        check('geometry-results/'+row['id']+'/result.zip',value['sha256'],value['size'])
    for row in state['tables']['migration_jobs']:
        value=json.loads(row['payload']);identifier(row['id'])
        if value['status'] in RUNNING:raise DomainError('An upgrade/extraction writer is active.',409)
        if value.get('report_sha256'):
            relative='upgrades/'+row['id']+'/report.json';check(relative,value['report_sha256'])
            if value['mode']=='extract' and value['status']=='completed':
                report=report_reader(relative);raw,_=extraction_paths(probe,report,row['id'])
                prefix='upgrades/'+row['id']+'/extraction/files/'
                actual={key[len(prefix):]:item for key,item in private.items() if key.startswith(prefix)}
                expected={item['path'].casefold():item for item in report['manifest']}
                if set(actual)!=set(expected):raise DomainError('Extraction file list differs from its manifest.',409)
                for relative,item in expected.items():check(prefix+relative,item['sha256'],item['size'])
                check('upgrades/'+row['id']+'/extraction/input.exe',value['installer']['sha256'],value['installer']['size'])
                if report.get('listing_sha256'):check('upgrades/'+row['id']+'/extraction/listing.txt',report['listing_sha256'])
        elif value['status']=='completed':raise DomainError('Completed job has no immutable report.',409)


class Backups:
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='nx-backup')
        self.stamp=process_stamp(os.getpid());self.root=offline_path(store.root/'b')
        self.jobs_root=offline_path(self.root/'jobs');self.jobs_root.mkdir(parents=True,exist_ok=True)
        self.restore_roots={'local':offline_path(store.root.parent/'Recovered')}
        self._jobs={};self._reserved=False
        for relative,path in tree_files(self.jobs_root):
            if relative.count('/')!=1 or not relative.endswith('/job.json'):continue
            key=relative.split('/')[0];identifier(key);value=read_json(path,256*1024)
            if value['id']!=key:raise DomainError('Backup job identity differs from its folder.',409)
            if value['status']=='running':
                owner=process_stamp(value['owner_pid'])
                if owner is None or (owner!='unknown' and value.get('owner_stamp') not in (None,'unknown') and owner!=value['owner_stamp']):
                    value.update(status='interrupted',phase='finished',message='Owner process ended. Partial files retained; no automatic restart.',finished_at=now())
                    write_json(path,value)
            self._jobs[key]=value
        with store.db() as db:
            present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_requests'").fetchone()
            accepted=db.execute("SELECT * FROM backup_requests WHERE state='accepted'").fetchall() if present else []
        for row in accepted:
            value=requests.response_value(row);key=value['id'];current=self._jobs.get(key)
            if current is not None and not requests.same_job(value,current):raise DomainError('Backup journal differs from its acceptance.',409)
            if current is None:
                current=copy.deepcopy(value);current['journal_missing']=True
                if not requests.same_root(value['management_root'],store.root):
                    current.update(status='interrupted',phase='finished',historical_origin=True,message='Historical acceptance for another management root; no automatic replay.')
                else:
                    owner=process_stamp(value['owner_pid'])
                    if owner is None or (owner!='unknown' and value['owner_stamp'] not in (None,'unknown') and owner!=value['owner_stamp']):
                        current.update(status='interrupted',phase='finished',message='Owner ended; missing journal retained as diagnostic, no automatic restart.')
            if row['failure_observation'] is not None and not current.get('historical_origin'):
                current=json.loads(row['failure_observation'])
            self._jobs[key]=current

    def _future(self,key):
        return None if self._jobs[key].get('historical_origin') else FUTURES.get(key)

    def _terminal_failure(self,key,message,status='failed',**proof):
        value=copy.deepcopy(self._jobs[key]);value.update(status=status,phase='finished',message=message,
            partial_output_retained=True,finished_at=now(),updated_at=now(),**proof)
        requests.record_failure(self.store,value)
        self._jobs[key]=value
        try:write_json(self._job_folder(key)/'job.json',value)
        except (OSError,DomainError):self._jobs[key]['journal_write_failed']=True  # SQL observation is authoritative fallback.

    @property
    def busy(self):
        with self.lock:
            for key,value in self._jobs.items():
                future=self._future(key)
                if value['status']=='running' and future is not None and future.done():
                    self._terminal_failure(key,'Worker ended; partial output retained. No automatic restart.',worker_terminal_proven=True)
                elif value['status']=='running' and future is None and not value.get('historical_origin'):
                    owner=process_stamp(value['owner_pid'])
                    if owner is None or (owner!='unknown' and value.get('owner_stamp') not in (None,'unknown') and owner!=value['owner_stamp']):
                        self._terminal_failure(key,'Owner ended/reused; partial output retained. No automatic restart.',status='interrupted',owner_ended_proven=True)
            return self._reserved or any(value['status']=='running' or
                (self._future(key) is not None and not self._future(key).done()) for key,value in self._jobs.items())

    def _folder(self,key):return offline_path(self.root/uuid.UUID(identifier(key)).hex[:12])
    def _job_folder(self,key):return offline_path(self.jobs_root/identifier(key))
    def _update(self,key,**changes):
        with self.lock:
            value={**self._jobs[key],**changes,'updated_at':now()}
            write_json(self._job_folder(key)/'job.json',value);self._jobs[key]=value

    def jobs(self):
        self.busy
        with self.lock:return [self.get_job(key) for key in reversed(self._jobs)]

    def get_job(self,key):
        identifier(key)
        if key not in self._jobs:
            with self.store.db() as db:
                present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_requests'").fetchone()
                row=db.execute("SELECT * FROM backup_requests WHERE job_id=? AND state='accepted'",(key,)).fetchone() if present else None
            if row is not None:
                value=requests.response_value(row)
                if not requests.same_root(value['management_root'],self.store.root):
                    value={**value,'status':'interrupted','phase':'finished','historical_origin':True,'message':'Historical acceptance; no automatic replay.'}
                else:
                    path=self._job_folder(key)/'job.json'
                    if path.exists():
                        current=read_json(path,256*1024)
                        if not requests.same_job(value,current):raise DomainError('External backup journal differs from acceptance.',409)
                        value=current
                    if row['failure_observation']:value=json.loads(row['failure_observation'])
                    value['external_observer']=True
                with self.lock:self._jobs[key]=value
        if self._jobs.get(key,{}).get('external_observer'):
            path=self._job_folder(key)/'job.json'
            if path.exists():
                current=read_json(path,256*1024)
                if not requests.same_job(self._jobs[key],current):raise DomainError('Observed backup identity changed.',409)
                with self.lock:self._jobs[key]={**current,'external_observer':True}
        self.busy
        with self.lock:
            if key not in self._jobs:raise DomainError('Backup job not found.',404)
            value=copy.deepcopy(self._jobs[key]);future=self._future(key)
            if future is not None and not future.done() and value['status']!='running':
                value.update(status='running',phase='finishing',message='Worker is still live; completion observation pending.')
            return value

    def report(self,key):
        job=self.get_job(key)
        if not job.get('report_sha256'):raise DomainError('Backup report is not ready.',409)
        path=self._job_folder(key)/'report.json'
        if sha256(offline_path(path))!=job['report_sha256']:raise DomainError('Backup report changed.',409)
        return read_json(path)

    def receipt(self,key):
        value=read_json(self._folder(key)/'receipt.json',256*1024)
        if value.get('schema_version')!=1 or value.get('id')!=key or value.get('status')!='ready':
            raise DomainError('Backup is not a completed snapshot.',409)
        for field in ('archive_sha256','manifest_sha256'):
            if not isinstance(value.get(field),str) or not re.fullmatch('[a-f0-9]{64}',value[field]):raise DomainError('Invalid backup receipt.')
        if type(value.get('archive_size')) is not int or not 0<value['archive_size']<=MAX_DATA+MAX_MANIFEST:
            raise DomainError('Invalid backup archive size.')
        return value

    def list(self):
        result=[]
        for folder in sorted(self.root.iterdir()):
            folder=offline_path(folder)
            if not re.fullmatch('[a-f0-9]{12}',folder.name) or not folder.is_dir():continue
            receipt=folder/'receipt.json'
            if not receipt.is_file():continue
            try:
                value=read_json(receipt,256*1024);key=identifier(value['id'])
                if self._folder(key)!=folder:raise DomainError('Backup folder differs.')
                result.append(self.receipt(key))
            except (DomainError,KeyError,ValueError,OSError):
                result.append({'status':'invalid','folder':folder.name,'message':'Snapshot receipt is invalid; no recovery or download.'})
        return sorted(result,key=lambda value:value.get('created_at',''),reverse=True)

    def create(self,mode,actor,request=None,backup_id=None,request_key=None):
        from .write_requests import key as request_identifier
        request_key=request_identifier(request_key)
        if mode not in ('backup','verify','preview','restore'):raise DomainError('Unknown backup job mode.')
        request=request or {}
        request_sha=requests.request_hash(mode,request,backup_id)
        if request_key is not None:
            with self.store.db() as db:
                db.execute('BEGIN');cached=requests.replay(db,actor,request_key,request_sha)
            if cached is not None:return cached  # Before archive lookup/busy; never resubmit.
        if backup_id:self.receipt(backup_id)
        # Reserve without waiting for the long-held store lock. A double-click
        # must not queue a second backup after the first one finishes.
        with self.lock:
            if self.busy:raise DomainError('A backup/recovery operation is already active; observe it.',409)
            self._reserved=True
        try:
            return self._create_reserved(mode,actor,request,backup_id,request_key,request_sha)
        finally:
            with self.lock:self._reserved=False

    def _create_reserved(self,mode,actor,request,backup_id,request_key=None,request_sha=None):
        with self.store.lock,self.lock:
            if request_key is not None:
                with self.store.db() as db:
                    db.execute('BEGIN');cached=requests.replay(db,actor,request_key,request_sha)
                if cached is not None:return cached
            if mode=='restore':
                if request.get('accept_unsigned') is not True:raise DomainError('Explicitly accept the unsigned snapshot; no authenticity is implied.')
                previous=self.get_job(request['preview_id'])
                if previous['mode']!='preview' or previous['status']!='completed' or previous.get('backup_id')!=backup_id:
                    raise DomainError('Choose a completed recovery preview for this backup.',409)
                reviewed=self.report(previous['id'])
                if reviewed.get('preview_hash')!=request['preview_hash'] or previous['request']!= {k:request[k] for k in ('target','name')}:
                    raise DomainError('Recovery decisions changed; review a new preview.',409)
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE');cached=requests.replay(db,actor,request_key,request_sha)
                if cached is not None:return cached
                present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_requests'").fetchone()
                for accepted in db.execute("SELECT * FROM backup_requests WHERE state='accepted'") if present else []:
                    other=requests.response_value(accepted)
                    if not requests.same_root(other['management_root'],self.store.root):continue
                    future=FUTURES.get(other['id'])
                    if future is not None and not future.done():
                        raise DomainError('Another durable backup writer is live; observe it instead.',409)
                    if accepted['failure_observation']:continue
                    path=self._job_folder(other['id'])/'job.json'
                    if path.exists():
                        current=read_json(path,256*1024)
                        if not requests.same_job(other,current):raise DomainError('Backup journal identity changed.',409)
                        if current['status']!='running':continue
                    owner=process_stamp(other['owner_pid'])
                    if owner is not None and (owner=='unknown' or other['owner_stamp'] in (None,'unknown') or owner==other['owner_stamp']):
                        raise DomainError('Another durable backup writer may still own this root; observe its journal.',409)
                for row in db.execute('SELECT id,payload FROM migration_jobs'):
                    if writer_active(self.store,json.loads(row['payload'])):
                        raise DomainError('An upgrade/extraction is active; backup/recovery must wait.',409)
                for table,allocation,label in [('asset_requests','asset_id','Upload'),('geometry_requests','job_id','STEP package'),('log_requests','log_id','System-log upload'),('release_requests','release_id','Release transition')]:
                    present=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
                    writers=db.execute('SELECT * FROM '+table+' WHERE '+allocation+' IS NOT NULL AND writer_finished_at IS NULL') if present else []
                    for row in writers:
                        owner=process_stamp(row['owner_pid'])
                        if owner is not None and (owner=='unknown' or row['owner_stamp'] in (None,'unknown') or owner==row['owner_stamp']):
                            raise DomainError(label+' filesystem writer may still be active (even after fencing); backup/recovery must wait.',409)
                # Acceptance commit precedes journal publication and submit.
                key=str(uuid.uuid4())
                job={'id':key,'mode':mode,'backup_id':backup_id,'request':request,'actor':actor,
                'status':'running','phase':'queued','message':'Operation accepted','owner_pid':os.getpid(),
                'owner_stamp':self.stamp,'created_at':now(),'updated_at':now()}
                if request_key is not None:job.update(request_key=request_key,management_root=str(self.store.root),source_backup_id=backup_id)
                requests.commit(self.store,db,actor,request_key,request_sha,job)
            self._jobs[key]=copy.deepcopy(job)
            try:
                folder=self._job_folder(key);folder.mkdir(parents=True,exist_ok=False)
                write_json(folder/'job.json',job)
                FUTURES[key]=self.pool.submit(self._run,key)
            except Exception:
                self._terminal_failure(key,'Journal/worker submission failed before a worker started; no automatic restart.',start_failure_proven=True)
                if request_key is None:raise DomainError('Backup worker could not start; failed job retained.',409)
            return job if request_key is not None else self.get_job(key)

    def _run(self,key):
        # An executor may enqueue before submit raises (e.g. thread creation).
        # The submitter holds this lock until marking failure: a late queued
        # callback must never perform filesystem work after that proven fence.
        with self.lock:
            if self._jobs[key].get('start_failure_proven'):return
        job=self.get_job(key)
        def progress(phase,message):self._update(key,phase=phase,message=message)
        try:
            # All in-process writers are held; external modifications are
            # detected by snapshot/hash checks, not claimed atomically frozen.
            with self.store.lock:
                if job['mode']=='backup':report=self._backup(key,job['actor'],job['request'],progress)
                else:
                    manifest,state=self._verify(job['backup_id'],key,progress)
                    if job['mode']=='verify':report={'verified':True,'receipt':self.receipt(job['backup_id']),
                        'audit':state['head'],'warnings':manifest['warnings'],'authenticity_proven':False}
                    else:
                        report=self._restore_plan(job['backup_id'],manifest,job['request'])
                        if job['mode']=='restore':
                            if not report['can_restore'] or report['preview_hash']!=job['request']['preview_hash']:
                                raise DomainError('Recovery inputs/target changed; review a new preview.',409)
                            report=self._restore(job['backup_id'],key,manifest,state,report,job['actor'],progress)
                path=self._job_folder(key)/'report.json';write_json(path,report)
                self._update(key,status='completed',phase='finished',message='Operation completed; no production activation.',
                    report_sha256=sha256(path),finished_at=now())
        except Exception as error:
            self._update(key,status='failed',phase='finished',message=str(error)[:1000],partial_output_retained=True,finished_at=now())

    def _inventory(self,state,progress):
        groups=[];sources={};stamps={};total=0;count=0
        roots=[]
        def collect(kind,name,root,skip=()):
            nonlocal total,count
            group={'kind':kind,'id':name} if kind in ('release','partial') else {'kind':'private','name':name}
            group.update(original_root=str(root),present=root.is_dir(),files=[],directories=[])
            if group['present']:
                for relative,path in tree_files(root):
                    if any(path==item or item in path.parents for item in skip):continue
                    portable(relative);info,stamp=hashed_file(path);total+=info['size'];count+=1
                    if count>MAX_FILES or total>MAX_DATA:raise DomainError('Managed snapshot exceeds its file/data limits.')
                    group['files'].append({'path':relative,**info});sources.setdefault(info['sha256'],path);stamps[str(path)]=stamp
                    if count%100==0:progress('inventory',str(count)+' files / '+str(total)+' bytes inventoried')
                group['directories']=[portable(path) for path in directories(root)
                    if not any(root/path==item or item in (root/path).parents for item in skip)]
            groups.append(group)
        for row in state['tables']['releases']:
            identifier(row['id']);root=release_root(self.store,row);roots.append(root);collect('release',row['id'],root)
        for row in state['tables']['migration_jobs']:
            value=json.loads(row['payload'])
            if value['mode']!='build':continue
            if value['status']=='completed':
                if value.get('release_id') not in {item['id'] for item in state['tables']['releases']}:
                    raise DomainError('Completed migration output has no registered release.',409)
                continue
            identifier(row['id'])
            if row['id'] in getattr(self.store,'recovered_job_ids',set()):
                root=offline_path(self.store.root/'retained/migrations'/row['id']/'output')
            else:root=release_root(self.store,{'folder':value['request']['target_path']})
            if root in roots:continue
            for other in roots:disjoint(root,other)
            roots.append(root);collect('partial',row['id'],root)
        for name in PRIVATE:
            root=offline_path(self.store.root/name);collect('private',name,root,roots)
        for name in ('r','releases'):
            root=offline_path(self.store.root/name);collect('private','retained/'+name,root,roots)
        return groups,sources,stamps,total

    def _backup(self,key,actor,request,progress):
        backup_id=str(uuid.uuid4());folder=self._folder(backup_id);folder.mkdir(parents=True,exist_ok=False)
        self._update(key,backup_id=backup_id)
        progress('database','Copy a consistent SQLite snapshot')
        db_copy(self.store.root/'management.sqlite3',folder/'management.sqlite3')
        probe=copy.copy(self.store);probe.root=folder
        issues=Issues();state=snapshot(probe,issues);coverage=metadata(state,issues,None)
        if issues.error_count:raise DomainError('Metadata/audit integrity failed; no completed backup.',409)
        if (coverage['legacy_unbound_rows'] or any(item['code']=='unbound_columns' for item in issues.warnings)) and request.get('accept_legacy_unbound') is not True:
            raise DomainError('Legacy rows have no audit binding. Explicitly accept a reviewed baseline before backing up.',409)
        groups,sources,stamps,total=self._inventory(state,progress)
        def report_reader(relative):
            path=offline_path(self.store.root/relative);value=read_json(path)
            expected=next(item for group in groups if group.get('name')=='upgrades' for item in group['files'] if item['path']==relative[9:])
            if sha256(path)!=expected['sha256']:raise DomainError('Report changed while capturing the snapshot.',409)
            return value
        validate_inventory(self.store,state,groups,report_reader)
        for row in state['tables']['releases']:
            if row['status'] in ('failed','building') or not row['manifest']:
                issues.add('partial_release',row['id'],'Incomplete output captured as forensic files, not qualified NX data.',warning=True)
        for row in state['tables']['migration_jobs']:
            if json.loads(row['payload'])['status'] in ('failed','interrupted'):
                issues.add('partial_job',row['id'],'Failed/interrupted job output captured; historical requests are never replayed.',warning=True)
        database={'size':(folder/'management.sqlite3').stat().st_size,'sha256':sha256(folder/'management.sqlite3')}
        manifest={'schema_version':1,'id':backup_id,'created_at':now(),'source_management_root':str(self.store.root),
            'source_platform':os.name,'source_state_sha256':state['sha256'],'database':database,'groups':groups,
            'audit':state['head'],'warnings':issues.warnings,'warning_count':issues.warning_count,
            'scope':{'managed_artifacts':True,'partial_outputs':True,'external_registered_releases':True,
                'configured_sources':False,'nx_installations':False,'service_configuration':False,'secrets':False,
                'older_backups':False,'signed':False,'filesystem_atomic_snapshot':False}}
        raw=encoded(manifest).encode()
        if len(raw)>MAX_MANIFEST:raise DomainError('Backup manifest exceeds 64 MiB.')
        required=sum(path.stat().st_size for path in sources.values())+database['size']+len(raw)+64*1024**2
        if shutil.disk_usage(folder).free<required:raise DomainError('Insufficient free space for the complete archive.',409)
        archive=offline_path(folder/'NX_Backup.zip');progress('archive','Copy verified files into a ZIP64 archive')
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_STORED,allowZip64=True) as package:
            package.writestr('manifest.json',raw);package.write(folder/'management.sqlite3','management.sqlite3')
            for index,(value,path) in enumerate(sources.items()):
                current=path.stat()
                if (current.st_size,current.st_mtime_ns)!=stamps[str(path)]:raise DomainError('File changed before archival.',409)
                hashed=hashlib.sha256()
                with path.open('rb') as source,package.open('objects/'+value,'w',force_zip64=True) as target:
                    for chunk in iter(lambda:source.read(1024*1024),b''):target.write(chunk);hashed.update(chunk)
                after=path.stat()
                if hashed.hexdigest()!=value or (after.st_size,after.st_mtime_ns)!=stamps[str(path)]:raise DomainError('File changed during archival.',409)
                if index%100==0:progress('archive',str(index+1)+' / '+str(len(sources))+' distinct file objects archived')
        progress('verifying','Recheck source snapshot and completed archive')
        if snapshot(self.store)['sha256']!=state['sha256']:raise DomainError('Database changed during backup; snapshot not published.',409)
        for path,stamp in stamps.items():
            current=offline_path(path).stat()
            if (current.st_size,current.st_mtime_ns)!=stamp:raise DomainError('Source file changed during backup.',409)
        # List changes are detected too, including unknown/partial new files.
        current_groups,_,_,_=self._inventory(state,progress)
        if current_groups!=groups:raise DomainError('Managed file inventory changed during backup.',409)
        receipt={'schema_version':1,'id':backup_id,'status':'ready','created_at':manifest['created_at'],
            'manifest_sha256':hashlib.sha256(raw).hexdigest(),'archive_sha256':sha256(archive),'archive_size':archive.stat().st_size,
            'files':sum(len(group['files']) for group in groups),'logical_bytes':total,'distinct_files':len(sources),
            'warning_count':issues.warning_count,'audit':state['head'],'signed':False,'same_disk_only':True}
        write_json(folder/'manifest.json',manifest);write_json(folder/'receipt.json',receipt)
        with self.store.db() as db:self.store.audit(db,actor,'backup.created',receipt)
        return receipt

    def _archive(self,backup_id):
        receipt=self.receipt(backup_id);path=offline_path(self._folder(backup_id)/'NX_Backup.zip')
        if path.stat().st_size!=receipt['archive_size'] or sha256(path)!=receipt['archive_sha256']:
            raise DomainError('Backup archive differs from its receipt.',409)
        return path,receipt

    def download(self,backup_id):return self._archive(backup_id)[0]

    def _verify(self,backup_id,job_id,progress):
        progress('verifying','Verify full archive SHA and portable entry layout')
        path,receipt=self._archive(backup_id)
        with zipfile.ZipFile(path) as package:
            entries=package.infolist();names=[entry.filename for entry in entries]
            if len(names)>MAX_FILES+2 or len(names)!=len(set(names)) or any(
                entry.compress_type!=zipfile.ZIP_STORED or entry.flag_bits&1 or entry.file_size>MAX_DATA
                or (entry.external_attr>>16)&0o170000==0o120000 for entry in entries):
                raise DomainError('Unsupported, duplicate or unsafe archive entries.',409)
            if 'manifest.json' not in names or package.getinfo('manifest.json').file_size>MAX_MANIFEST:
                raise DomainError('Missing/oversized backup manifest.',409)
            raw=package.read('manifest.json')
            if hashlib.sha256(raw).hexdigest()!=receipt['manifest_sha256']:raise DomainError('Backup manifest SHA differs.',409)
            manifest=json.loads(raw)
            if manifest.get('schema_version')!=1 or manifest.get('id')!=backup_id:raise DomainError('Unsupported/mismatched backup manifest.')
            groups=manifest['groups'];objects={};seen_groups=set();files=0;total=0
            for group in groups:
                if group['kind'] in ('release','partial'):group_key=(group['kind'],identifier(group['id']))
                elif group['kind']=='private' and group['name'] in (*PRIVATE,'retained/r','retained/releases'):
                    group_key=('private',group['name'])
                else:raise DomainError('Unknown backup artifact group.')
                if group_key in seen_groups:raise DomainError('Repeated backup artifact group.')
                seen_groups.add(group_key);paths=set()
                for item in group['files']:
                    portable(item['path']);key=item['path'].casefold();files+=1;total+=item['size']
                    if key in paths:raise DomainError('Repeated/case-colliding backup path.')
                    paths.add(key)
                    if type(item['size']) is not int or item['size']<0 or not re.fullmatch('[a-f0-9]{64}',item['sha256']):raise DomainError('Invalid backup file binding.')
                    if item['sha256'] in objects and objects[item['sha256']]!=item['size']:raise DomainError('Conflicting object sizes.')
                    objects[item['sha256']]=item['size']
                if len(group['directories'])>MAX_FILES or len(set(group['directories']))!=len(group['directories']):
                    raise DomainError('Invalid/repeated snapshot directory list.')
                for directory in group['directories']:portable(directory)
            if files>MAX_FILES or total>MAX_DATA:raise DomainError('Restored data exceeds its bounds.')
            expected={'manifest.json','management.sqlite3',*['objects/'+value for value in objects]}
            if set(names)!=expected:raise DomainError('Archive entries differ from the manifest.',409)
            database=manifest['database']
            if package.getinfo('management.sqlite3').file_size!=database['size']:raise DomainError('Database size differs.')
            for index,(value,size) in enumerate(objects.items()):
                entry=package.getinfo('objects/'+value)
                if entry.file_size!=size:raise DomainError('Archive object size differs.',409)
                hashed=hashlib.sha256()
                with package.open(entry) as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):hashed.update(chunk)
                if hashed.hexdigest()!=value:raise DomainError('Archive object SHA differs.',409)
                if index%100==0:progress('verifying',str(index+1)+' / '+str(len(objects))+' distinct file objects verified')
            target=self._job_folder(job_id)/'management.sqlite3'
            with package.open('management.sqlite3') as source,target.open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
            if sha256(target)!=database['sha256']:raise DomainError('Database snapshot SHA differs.',409)
            probe=copy.copy(self.store);probe.root=target.parent
            issues=Issues();state=snapshot(probe,issues);metadata(state,issues,None)
            if issues.error_count or state['head']!=manifest['audit'] or state['sha256']!=manifest['source_state_sha256']:
                raise DomainError('Snapshot audit/metadata integrity differs.',409)
            if {group['id'] for group in groups if group['kind']=='release'}!={row['id'] for row in state['tables']['releases']}:
                raise DomainError('Snapshot release groups differ from the database.',409)
            partial_ids={group['id'] for group in groups if group['kind']=='partial'}
            if not partial_ids<={row['id'] for row in state['tables']['migration_jobs']
                               if json.loads(row['payload']).get('mode')=='build'
                               and json.loads(row['payload']).get('status') in ('failed','interrupted')}:
                raise DomainError('Partial output groups do not match failed migration history.',409)
            probe.recovery_origins=[manifest['source_management_root']]
            with sqlite3.connect(target.as_uri()+'?mode=ro',uri=True) as db:
                for row in db.execute("SELECT payload FROM audit WHERE action='management.restored'"):
                    for origin in json.loads(row[0]).get('original_management_roots',[]):
                        if origin not in probe.recovery_origins:probe.recovery_origins.append(origin)
            private={group['name']+'/'+item['path']:item for group in groups if group['kind']=='private' for item in group['files']}
            def report_reader(relative):return json.loads(package.read('objects/'+private[relative]['sha256']))
            validate_inventory(probe,state,groups,report_reader)
            return manifest,state

    def _restore_plan(self,backup_id,manifest,request):
        if request['target'] not in self.restore_roots:raise DomainError('Unknown recovery target alias.')
        portable(request['name'])
        if '/' in request['name'] or len(request['name'])>60:raise DomainError('Choose a short new recovery directory name.')
        root=self.restore_roots[request['target']];target=scoped_path(root,str(root/request['name']))
        disjoint(target,self.store.root);self.store.folders._target_safe(target)
        if target.exists():raise DomainError('Recovery target exists; nothing will be overwritten.',409)
        outputs={};folders=set();errors=[]
        for group in manifest['groups']:
            prefix=('r/'+uuid.UUID(group['id']).hex[:12]+'/nx' if group['kind']=='release' else
                    'retained/migrations/'+group['id']+'/output' if group['kind']=='partial' else group['name'])
            if group['present']:folders.add(prefix)
            for directory in group['directories']:folders.add(prefix+'/'+directory)
            for item in group['files']:
                path=portable(prefix+'/'+item['path']);key=path.casefold()
                if key in outputs:raise DomainError('Recovery output paths collide.',409)
                outputs[key]={'path':path,'sha256':item['sha256'],'size':item['size']}
                if os.name=='nt' and len(str(target/path))>=255:errors.append('Target exceeds supported Windows path length: '+path)
        for folder in folders:
            if folder.casefold() in outputs:raise DomainError('Recovery file/directory paths collide.',409)
        for item in outputs.values():
            if any(parent.as_posix().casefold() in outputs for parent in Path(item['path']).parents):
                raise DomainError('Recovery file shadows a directory.',409)
        existing=root
        while not existing.exists():existing=existing.parent
        required=sum(item['size'] for item in outputs.values())+manifest['database']['size']*2+64*1024**2
        if shutil.disk_usage(existing).free<required:errors.append('Insufficient free space for the full isolated dataset.')
        report={'backup_id':backup_id,'target_folder':str(target),'files':len(outputs),'bytes':sum(item['size'] for item in outputs.values()),
            'can_restore':not errors,'errors':errors,'new_active_release':None,'requires_new_acceptance':True,
            'embedded_nx_paths_rewritten':False,'functional_validation_required':True,
            'original_history_preserved':True,'authenticity_proven':False,'same_disk_only':True}
        report['preview_hash']=digest([report,self.receipt(backup_id),request['target'],request['name']])
        return report

    def _restore(self,backup_id,job_id,manifest,state,plan,actor,progress):
        target=offline_path(plan['target_folder']);target.mkdir(parents=True,exist_ok=False)
        pending=target/'RECOVERY_PENDING.json';write_json(pending,{'backup_id':backup_id,'job_id':job_id,'status':'incomplete'})
        progress('restoring','Restore only into the new isolated directory')
        archive=self._folder(backup_id)/'NX_Backup.zip';count=0
        with zipfile.ZipFile(archive) as package:
            for group in manifest['groups']:
                prefix=('r/'+uuid.UUID(group['id']).hex[:12]+'/nx' if group['kind']=='release' else
                        'retained/migrations/'+group['id']+'/output' if group['kind']=='partial' else group['name'])
                folder=offline_path(target/prefix)
                if group['present']:folder.mkdir(parents=True,exist_ok=True)
                for relative in group['directories']:offline_path(folder/relative).mkdir(parents=True,exist_ok=True)
                for item in group['files']:
                    path=offline_path(folder/item['path']);path.parent.mkdir(parents=True,exist_ok=True)
                    with package.open('objects/'+item['sha256']) as source,path.open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
                    if path.stat().st_size!=item['size'] or sha256(path)!=item['sha256']:raise DomainError('Restored file SHA differs.',409)
                    count+=1
                    if count%100==0:progress('restoring',str(count)+' / '+str(plan['files'])+' files copied and verified')
        db_copy(self._job_folder(job_id)/'management.sqlite3',target/'management.sqlite3')
        evidence=offline_path(target/'retained/recovery'/job_id);evidence.mkdir(parents=True,exist_ok=False)
        with (self._job_folder(job_id)/'management.sqlite3').open('rb') as source,(evidence/'original.sqlite3').open('xb') as output:
            shutil.copyfileobj(source,output,1024*1024)
        if sha256(evidence/'original.sqlite3')!=manifest['database']['sha256']:raise DomainError('Original database evidence differs.',409)
        write_json(evidence/'manifest.json',manifest);write_json(evidence/'receipt.json',self.receipt(backup_id))
        # This temporary object is confined to the new root. Normal startup is
        # blocked by RECOVERY_PENDING until relocation and all checks succeed.
        restored=Store.__new__(Store);restored.root=target;restored.lock=threading.RLock()
        restored.sources={};restored.logs={};restored.standards={};restored.sidts={};restored.display_paths={}
        restored.installers={};restored.extractor=None
        restored.recovery_origins=[manifest['source_management_root']]
        restored.recovered_job_ids={row['id'] for row in state['tables']['migration_jobs']}
        restored.folders=FolderAccess(restored)
        origins=[manifest['source_management_root']]
        with restored.db() as db:
            for row in db.execute("SELECT payload FROM audit WHERE action='management.restored'"):
                for root in json.loads(row['payload']).get('original_management_roots',[]):
                    if root not in origins:origins.append(root)
        restored.recovery_origins=origins
        launchers=[]
        previous_launchers={row['release_id']:json.loads(row['payload']) for row in state['tables']['test_launchers']}
        for row in state['tables']['releases']:
            folder=target/'r'/uuid.UUID(row['id']).hex[:12]/'nx'
            if row['nx_version']=='2512' and row['status'] not in ('building','failed') and row['manifest']:
                launchers.append(restored._create_test_launcher(row['id'],row['nx_version'],folder,json.loads(row['manifest']),
                    ui_profile=previous_launchers.get(row['id'],{}).get('ui_profile')))
        with restored.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS geometry_results(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,resource_id TEXT NOT NULL,payload TEXT NOT NULL)')
            from .write_requests import SCHEMA as WRITE_REQUEST_SCHEMA
            db.execute(WRITE_REQUEST_SCHEMA)
            from .migration_requests import SCHEMA as MIGRATION_REQUEST_SCHEMA
            db.execute(MIGRATION_REQUEST_SCHEMA)
            from .asset_requests import SCHEMA as ASSET_REQUEST_SCHEMA
            db.execute(ASSET_REQUEST_SCHEMA)
            from .geometry_requests import SCHEMA as GEOMETRY_REQUEST_SCHEMA
            db.execute(GEOMETRY_REQUEST_SCHEMA)
            from .log_requests import SCHEMA as LOG_REQUEST_SCHEMA
            db.execute(LOG_REQUEST_SCHEMA)
            from .release_requests import SCHEMA as RELEASE_REQUEST_SCHEMA
            db.execute(RELEASE_REQUEST_SCHEMA)
            db.execute(requests.SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            for row in state['tables']['releases']:
                status='draft' if row['status'] in ('approved','validated') else 'failed' if row['status']=='building' else row['status']
                db.execute('UPDATE releases SET folder=?,status=? WHERE id=?',
                    (str(target/'r'/uuid.UUID(row['id']).hex[:12]/'nx'),status,row['id']))
            db.execute('UPDATE active SET release_id=NULL,generation=generation+1 WHERE singleton=1')
            for launcher in launchers:
                db.execute('INSERT INTO test_launchers(release_id,payload) VALUES(?,?) ON CONFLICT(release_id) DO UPDATE SET payload=excluded.payload',
                    (launcher['release_id'],encoded(launcher)))
            restored.audit(db,actor,'management.restored',{'backup_id':backup_id,'manifest_sha256':self.receipt(backup_id)['manifest_sha256'],
                'source_database_sha256':manifest['database']['sha256'],'original_management_roots':origins,
                'restored_migration_jobs':sorted(restored.recovered_job_ids),
                'isolated_target':str(target),'active_pointer_cleared':True,'new_acceptance_required':True,'unsigned_snapshot':True})
        progress('verifying','Verify restored metadata, manifests and immutable packages')
        from .integrity import scan
        verification=scan(restored,progress=progress)
        if verification['error_count'] or not verification['stable_database_snapshot']:
            raise DomainError('Isolated recovery did not pass its integrity check; startup stays blocked.',409)
        write_json(target/'recovery-report.json',{**plan,'integrity':{k:v for k,v in verification.items() if k!='checkpoint'}})
        write_json(target/'settings.recovered.json',{'data_dir':str(target),'sources':{},'logs':{},'standards':{},'sidts':{}})
        pending.unlink()  # Only our exact completed recovery marker, never user data.
        return {**plan,'restored':True,'integrity_status':verification['status'],'warning_count':verification['warning_count'],
                'settings_file':str(target/'settings.recovered.json'),'original_audit_prefix_preserved':True,
                'note':'Not connected/activated. File bytes and embedded NX references were preserved, not retargeted. Configure NX standards, review embedded paths and retest before acceptance. Keep the receipt separately; snapshot is unsigned.'}
