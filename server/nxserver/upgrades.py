"""Durable, single-writer upgrade jobs. No installer execution or automatic activation."""
import json
import os
import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor

from .migration import PAIRS, Planner, prerequisites, request_source,current_plan,validated_file_choices
from .safety import DomainError, offline_path
from .store import canonical, now, sha256
from .migration_requests import request_hash, replay, commit
from .write_requests import key as request_key_value
from .migration_report import read as read_migration_report,MAX_BYTES as MAX_REPORT_BYTES

LIVE = {}
RUNNING = {'queued','planning','building','verifying','registering','scanning','extracting'}


def process_stamp(pid):
    """None = confirmed gone, unknown = cannot inspect. Never kill or restart here."""
    try:
        if os.name!='nt':
            with open('/proc/'+str(pid)+'/stat') as stream:
                return stream.read().rsplit(')',1)[1].split()[19]
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes=[ctypes.c_void_p,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return None if ctypes.get_last_error() in (87,1168) else 'unknown'
        try:
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return 'unknown'
            if code.value!=259:return None
            times=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*[ctypes.byref(item) for item in times]):return 'unknown'
            return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        finally:kernel.CloseHandle(handle)
    except FileNotFoundError:return None
    except OSError:return 'unknown'


def execution_evidence(store,job):
    """Read live evidence only; restored jobs never consult the original runtime."""
    historical=job['id'] in getattr(store,'recovered_job_ids',set())
    future=None if historical else LIVE.get(job['id'])
    if future is None:state='unavailable'
    elif not future.done():state='live'
    elif future.cancelled():state='cancelled'
    elif future.exception() is not None:state='failed'
    else:state='ended'
    child='historical' if historical else Upgrades._process(job.get('child_pid'),job.get('child_stamp'))
    owner='historical' if historical else Upgrades._process(job.get('owner_pid'),job.get('owner_stamp'))
    # Missing historical PID metadata is uncertainty, not proof of exit.
    may_write=(state=='live' or child in ('live','unknown') or
        (state=='unavailable' and job['status'] in RUNNING and owner in ('live','unknown','not_recorded')))
    return {'contract_version':1,'future':state,'owner':owner,'child':child,'historical':historical,
        'writer_may_be_active':may_write,'persisted_status':job['status'],
        'automatic_restart':False,'observation_timeout_proves_exit':False}


def writer_active(store,job):
    """Use inside SQL acceptance gates too, including premature terminal flags."""
    return job['status'] in RUNNING or execution_evidence(store,job)['writer_may_be_active']


class Upgrades:
    def __init__(self,store):
        self.store=store;self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='nx-upgrade')
        self.stamp=process_stamp(os.getpid())
        # Reconciliation also runs on later observations, not only startup.
        self.list()

    @staticmethod
    def _process(pid,expected):
        if not pid:return 'not_recorded'
        if type(pid) is not int or pid<1:return 'unknown'
        actual=process_stamp(pid)
        if actual is None:return 'gone'
        if actual=='unknown' or not isinstance(expected,str) or not expected or expected=='unknown':return 'unknown'
        return 'live' if actual==expected else 'reused'

    def _evidence(self,job):
        return execution_evidence(self.store,job)

    def _reconcile(self,identifier):
        from .job_results import verify_in_db
        with self.store.db() as db:
            db.execute('BEGIN');row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(identifier,)).fetchone()
            if not row:raise DomainError('Migration job not found.',404)
            job=json.loads(row['payload']);verify_in_db(db,job)
        evidence=self._evidence(job)
        needs_failure=evidence['future'] in ('failed','cancelled') or (evidence['future']=='ended' and job['status'] in RUNNING)
        needs_interrupt=job['status'] in RUNNING and evidence['future']=='unavailable' and evidence['owner'] in ('gone','reused')
        if not evidence['writer_may_be_active'] and (needs_failure or needs_interrupt) and job['status'] not in ('failed','interrupted'):
            with self.store.lock,self.store.db() as db:
                db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(identifier,)).fetchone()
                current=json.loads(row['payload']);verify_in_db(db,current);fresh=self._evidence(current)
                failure=fresh['future'] in ('failed','cancelled') or (fresh['future']=='ended' and current['status'] in RUNNING)
                interrupted=current['status'] in RUNNING and fresh['future']=='unavailable' and fresh['owner'] in ('gone','reused')
                if not fresh['writer_may_be_active'] and (failure or interrupted) and current['status'] not in ('failed','interrupted'):
                    status='failed' if failure else 'interrupted'
                    current.update(status=status,finished_at=now(),updated_at=now(),partial_output_retained=True,
                        message='Task ended unexpectedly or without a terminal publication; persisted/domain results retained. No automatic restart.' if failure else
                                'Owner ended/reused and no live child is known; partial/domain results retained. No automatic restart.',
                        execution_observation={**fresh,'terminal_evidence_verified':True})
                    db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(current),identifier))
                    self.store.audit(db,'system','migration.job.failed' if failure else 'migration.job.interrupted',{'id':identifier,'execution_observation':fresh})
        with self.store.db() as db:
            db.execute('BEGIN');row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(identifier,)).fetchone()
            result=json.loads(row['payload']);verify_in_db(db,result)
        execution=self._evidence(result)
        if execution['writer_may_be_active'] and result['status'] not in RUNNING:
            result['status']='extracting' if execution['child'] in ('live','unknown') else 'registering'
            result['message']='Execution is still live/uncertain despite the persisted terminal flag; observe it, never replay.'
        result['execution']=execution
        return result

    def options(self):
        results=[]
        for old,new in sorted(PAIRS):
            try:check=prerequisites(self.store,old,new)
            except DomainError as error:check={'ready':False,'versions':[{'nx_version':old+' -> '+new,'errors':[str(error)]}],'nx_execution_verified':False}
            results.append({'source_version':old,'target_version':new,**check})
        from .extraction import installer_info,tool_info
        installers=[]
        for version in self.store.installers:
            try:installers.append({**installer_info(self.store,version),'ready':True})
            except DomainError as error:installers.append({'version':version,'ready':False,'error':str(error)})
        try:reader={**tool_info(self.store),'ready':True}
        except DomainError as error:reader={'ready':False,'error':str(error)}
        with self.store.db() as db:selections={row['version']:{'job_id':row['job_id'],'generation':row['generation']} for row in db.execute('SELECT * FROM sidt_inputs')}
        return {'adapters':results,'automatic_nx_start':False,'automatic_activation':False,
                'exe_extraction':reader['ready'] and any(item['ready'] for item in installers),'exe_extraction_supported':True,
                'installers':installers,'reader':reader,'input_selections':selections}

    def get(self,identifier):
        try:
            if str(uuid.UUID(identifier))!=identifier:raise ValueError()
        except (ValueError,AttributeError,TypeError):raise DomainError('Invalid migration job identifier.')
        return self._reconcile(identifier)

    def list(self):
        with self.store.db() as db:identifiers=[row['id'] for row in db.execute('SELECT id FROM migration_jobs ORDER BY rowid DESC')]
        return [self.get(identifier) for identifier in identifiers]

    def _update(self,identifier,**changes):
        with self.store.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(identifier,)).fetchone()
            if row is None:raise DomainError('Migration job not found.',404)
            job=json.loads(row['payload']);job.update(changes,updated_at=now())
            db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(job),identifier))
        return job

    def report(self,identifier):
        job=self.get(identifier)
        if not job.get('report_sha256'):raise DomainError('Migration report is not ready.',409)
        path=offline_path(self.store.root/'upgrades'/identifier/'report.json')
        return read_migration_report(path,job['report_sha256'],max_bytes=MAX_REPORT_BYTES)

    def _replay_request(self,actor,request_key,request_sha):
        if request_key is None:return None
        with self.store.db() as db:
            db.execute('BEGIN');return replay(db,actor,request_key,request_sha)

    def _accept(self,request,mode,actor,validate,preview_id=None,preview_hash=None,request_key=None,content=None):
        request_key=request_key_value(request_key);request_sha=request_hash(mode,request,preview_id,preview_hash)
        cached=self._replay_request(actor,request_key,request_sha)
        if cached is not None:return cached  # No filesystem/prerequisite access or worker resubmission on replay.
        try:extra=validate()
        except DomainError:
            # Another writer may have accepted/completed this same request while
            # expensive read-only checks ran (including creating the target).
            cached=self._replay_request(actor,request_key,request_sha)
            if cached is not None:return cached
            raise
        identifier=str(uuid.uuid4());stamp=now()
        job={'id':identifier,'mode':mode,'status':'queued','progress':0,
            'message':'Statische Extraktion angenommen' if mode=='extract' else 'Auftrag angenommen',
            'request':request,'preview_id':preview_id,'preview_hash':preview_hash,'actor':actor,
            'owner_pid':os.getpid(),'owner_stamp':self.stamp,'created_at':stamp,'updated_at':stamp,**extra}
        with self.store.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cached=replay(db,actor,request_key,request_sha)
            if cached is not None:return cached
            if any(writer_active(self.store,json.loads(row['payload'])) for row in db.execute('SELECT payload FROM migration_jobs')):
                raise DomainError('Another upgrade job is active; wait for its actual completion.',409)
            db.execute('INSERT INTO migration_jobs(id,payload) VALUES(?,?)',(identifier,canonical(job)))
            receipt=commit(db,actor,request_key,request_sha,job)
            audit={'id':identifier,'installer':extra['installer'],'reader':extra['reader']} if mode=='extract' else {'id':identifier,'mode':mode,'request':request,'preview_id':preview_id}
            self.store.audit(db,actor,'installer.extraction.created' if mode=='extract' else 'migration.job.created',{**audit,**receipt})
        # Fence an enqueued callback if executor submission itself fails.
        with self.store.lock:
            try:LIVE[identifier]=self.pool.submit(self._run,identifier,content) if content is not None else self.pool.submit(self._run,identifier)
            except Exception as error:
                self._update(identifier,status='failed',message='Worker submission failed; no automatic restart. '+str(error)[:1000],finished_at=now(),worker_submission_failed=True)
                with self.store.db() as db:self.store.audit(db,actor,'migration.job.failed',{'id':identifier,'worker_submission_failed':True})
        return job if request_key is not None else self.get(identifier)

    def create(self,request,mode,actor,preview_id=None,preview_hash=None,request_key=None):
        if mode not in ('preview','build'):raise DomainError('Use the extraction endpoint for static extraction.')
        def validate():
            validated_file_choices(request.get('file_resolutions',{}))
            source=request_source(self.store,request)
            self.store.folders.destination(request['target'],request['target_path'],source)
            check=prerequisites(self.store,request['source_version'],request['target_version'])
            if not check['ready']:raise DomainError('Siemens/SIDT prerequisites are incomplete.',409)
            if mode=='build':
                previous=self.get(preview_id);report=self.report(preview_id)
                if previous['mode']!='preview' or previous['status']!='completed' or not current_plan(report,request) or not report['can_build'] or report['preview_hash']!=preview_hash or previous['request']!=request:
                    raise DomainError('A matching, buildable preview must be reviewed first.',409)
            return {}
        return self._accept(request,mode,actor,validate,preview_id,preview_hash,request_key)

    def create_extraction(self,version,actor,request_key=None):
        from .extraction import installer_info,tool_info
        return self._accept({'version':version},'extract',actor,
            lambda:{'installer':installer_info(self.store,version),'reader':tool_info(self.store)},request_key=request_key)

    def create_import(self,request,actor,request_key=None):
        def validate():
            if request.get('reference_resolutions') and not request['normalize_libraries']:
                raise DomainError('Reference corrections require a reviewed normalization preview.')
            if not request['normalize_libraries']:
                raise DomainError('Background imports require reviewed standard-library normalization; legacy databases are not copied back.')
            source=self.store.folders.source(request['source'],request.get('source_path'))
            if bool(request.get('target'))!=bool(request.get('target_path')):raise DomainError('Target alias and path must be provided together.')
            if request.get('target'):self.store.folders.destination(request['target'],request['target_path'],source)
            if not offline_path(source/'MACH/resource').is_dir():raise DomainError('NX share layout MACH/resource is missing.')
            if request['normalize_libraries']:
                if not re.fullmatch('[a-f0-9]{64}',request.get('preview_hash') or ''):
                    raise DomainError('A reviewed import preview hash is required.')
            return {}
        return self._accept(request,'import',actor,validate,preview_hash=request.get('preview_hash'),request_key=request_key)

    def create_derive(self,request,actor,request_key=None):
        def validate():
            parent=request['parent_release_id']
            previous=self.store.get_release(parent)
            if previous['status'] in ('building','failed'):raise DomainError('Cannot derive from an incomplete release.',409)
            if not re.fullmatch('[a-f0-9]{64}',request.get('preview_hash') or ''):
                raise DomainError('A reviewed export preview hash is required.')
            # Full file/schema/revision checks run in the worker BEFORE any
            # release/folder write. Acceptance must not hash gigabytes in HTTP.
            return {}
        return self._accept(request,'derive',actor,validate,preview_hash=request.get('preview_hash'),request_key=request_key)

    def create_adoption(self,request,mode,actor,request_key=None):
        if mode not in ('catalog','file_adopt'):raise DomainError('Unknown adoption mode.')
        # Preserve historical request hashes when the new optional field is absent.
        if mode=='catalog' and request.get('selection') is None:request={key:value for key,value in request.items() if key!='selection'}
        def validate():
            release=self.store.get_release(request['release_id'])
            if release['status'] in ('building','failed'):raise DomainError('Adopt from a complete managed release.',409)
            if not re.fullmatch('[a-f0-9]{64}',request.get('preview_hash') or ''):raise DomainError('A reviewed adoption preview hash is required.')
            if mode=='catalog':
                from .libraries import spec_for,catalog_selection
                for library in request['libraries']:spec_for(library)
                catalog_selection(request['libraries'],request.get('selection'))
            else:
                from .filepackages import path_for
                for path in request['paths']:path_for(request['kind'],path)
            return {}
        return self._accept(request,mode,actor,validate,preview_hash=request.get('preview_hash'),request_key=request_key)

    def create_result_upload(self,geometry_job_id,content,actor,request_key=None):
        import hashlib
        from .results import MAX_ARCHIVE
        if not content or len(content)>MAX_ARCHIVE:raise DomainError('Prepared ZIP must contain 1 byte to 64 MiB.')
        request={'geometry_job_id':geometry_job_id,'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}
        def validate():
            from .geometry import GeometryJobs
            GeometryJobs(self.store).get(geometry_job_id)
            return {}
        return self._accept(request,'result_upload',actor,validate,request_key=request_key,content=content)

    def create_result_adoption(self,request,actor,request_key=None):
        # Empty optional values preserve historical acceptance fingerprints.
        if not request.get('column_values'):request={key:value for key,value in request.items() if key!='column_values'}
        def validate():
            from .results import Results
            value=Results(self.store).get(request['result_id'])
            if value.get('applied'):raise DomainError('This result already has a committed application; inspect it instead of starting a new operation.',409)
            target=self.store.get_release(request['target_release_id'])
            if target['status'] in ('building','failed'):raise DomainError('Choose a complete target release.',409)
            if not re.fullmatch('[a-f0-9]{64}',request.get('preview_hash') or ''):raise DomainError('A reviewed result preview hash is required.')
            return {}
        return self._accept(request,'result_adopt',actor,validate,preview_hash=request['preview_hash'],request_key=request_key)

    def select_extraction(self,identifier,expected_generation,actor):
        job=self.get(identifier);report=self.report(identifier)
        if job['mode']!='extract' or job['status']!='completed':raise DomainError('Only a completed extraction may be selected.',409)
        from .recovery import extraction_paths
        requested=job.get('request')
        if (not isinstance(requested,dict) or not isinstance(requested.get('version'),str)
                or not re.fullmatch('[0-9]{4}',requested['version'])):
            raise DomainError('Extraction job has no supported requested version.',409)
        files,sidt=extraction_paths(self.store,report,identifier,expected_version=requested['version'])
        if self.store.manifest(files)!=report['manifest']:
            raise DomainError('Extraction files changed outside the API.',409)
        from .migration import after_install_profile
        version=report['version']
        after_install_profile(sidt,version)
        with self.store.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if any(writer_active(self.store,json.loads(row['payload'])) for row in db.execute('SELECT payload FROM migration_jobs')):
                raise DomainError('An upgrade/extraction job is active; input selection must wait.',409)
            current=db.execute('SELECT generation FROM sidt_inputs WHERE version=?',(version,)).fetchone()
            generation=current['generation'] if current else 0
            if generation!=expected_generation:raise DomainError('SIDT input selection changed; reload before selecting.',409)
            db.execute('INSERT INTO sidt_inputs(version,job_id,generation) VALUES(?,?,?) ON CONFLICT(version) DO UPDATE SET job_id=excluded.job_id,generation=excluded.generation',
                (version,identifier,generation+1))
            self.store.audit(db,actor,'installer.extraction.selected',{'version':version,'job_id':identifier,'generation':generation+1})
        return {'version':version,'job_id':identifier,'generation':generation+1,'runtime_activation':False}

    def _run(self,identifier,content=None):
        with self.store.lock:
            job=self.get(identifier)
            if job.get('worker_submission_failed'):return
        folder=offline_path(self.store.root/'upgrades'/identifier)
        try:
            folder.mkdir(parents=True,exist_ok=False)
            progress=lambda phase,percent,message:self._update(identifier,status=phase,progress=percent,message=message)
            if job['mode'] in ('result_upload','result_adopt'):
                from .results import Results
                request=job['request'];service=Results(self.store)
                progress('planning',5,'Pruefe gebundenen STEP-Auftrag und Ergebnisdaten')
                if job['mode']=='result_upload':
                    import hashlib
                    if not isinstance(content,bytes) or len(content)!=request['size'] or hashlib.sha256(content).hexdigest()!=request['sha256']:
                        raise DomainError('Accepted result upload bytes are unavailable/different; never reconstruct or restart.',409)
                    progress('building',25,'Inventar und ZIP pruefen; unveraenderliches Ergebnispaket speichern')
                    service.upload(request['geometry_job_id'],content,job['actor'],operation_job_id=identifier)
                else:
                    service.apply(request['result_id'],request['target_release_id'],request['preview_hash'],job['actor'],operation_job_id=identifier,progress=progress,column_values=request.get('column_values'))
                bound=self.get(identifier)
                report={'contract_version':1,'job_id':identifier,'mode':job['mode'],'result':bound['domain_result'],
                    'result_allocation':bound.get('result_allocation'),'asset_allocations':bound.get('asset_allocations',[]),
                    'automatic_approval':False,'automatic_activation':False,'nx_files_changed':False}
                import hashlib
                raw=canonical(report).encode();offline_path(folder/'report.json').write_bytes(raw)
                self._update(identifier,status='completed',progress=100,message='Ergebnisauftrag committed; keine automatische NX-/Geometriefreigabe',
                    report_sha256=hashlib.sha256(raw).hexdigest(),summary={'resources':len(bound['domain_result']['resources'])},finished_at=now())
                with self.store.lock,self.store.db() as db:self.store.audit(db,job['actor'],'migration.job.completed',{'id':identifier,'report_sha256':self.get(identifier)['report_sha256']})
                return
            if job['mode'] in ('catalog','file_adopt'):
                request=job['request'];progress('planning',5,'Pruefe gebundene Uebernahmevorschau und aktuellen Besitz')
                if job['mode']=='catalog':
                    self.store.import_catalog(request['release_id'],request['libraries'],request['include_standard'],request['preview_hash'],job['actor'],operation_job_id=identifier,selection=request.get('selection'))
                else:
                    from .filepackages import FilePackages
                    FilePackages(self.store).adopt(request['release_id'],request['kind'],request['paths'],request['name'],
                        request.get('resource_id'),request['preview_hash'],job['actor'],operation_job_id=identifier,progress=progress)
                result=self.get(identifier)['domain_result']
                report={'contract_version':1,'job_id':identifier,'mode':job['mode'],'source_release_id':request['release_id'],
                    'preview_hash':request['preview_hash'],'result':result,'asset_allocations':self.get(identifier).get('asset_allocations',[]),
                    'automatic_approval':False,'automatic_activation':False,'nx_files_changed':False}
                if job['mode']=='catalog' and request.get('selection') is not None:report['selection']=request['selection']
                import hashlib
                raw=canonical(report).encode();offline_path(folder/'report.json').write_bytes(raw)
                self._update(identifier,status='completed',progress=100,message='Uebernahme committed; Stammdaten bleiben Entwuerfe, NX-Dateien unveraendert',
                    report_sha256=hashlib.sha256(raw).hexdigest(),summary={'resources':len(result['resources']),'assets':len(report['asset_allocations'])},finished_at=now())
                with self.store.lock,self.store.db() as db:self.store.audit(db,job['actor'],'migration.job.completed',{'id':identifier,'report_sha256':self.get(identifier)['report_sha256']})
                return
            if job['mode'] in ('import','derive'):
                request=job['request'];started=lambda release:self._update(identifier,release_id=release)
                if job['mode']=='derive':
                    release=self.store.import_release('','',request['name'],job['actor'],parent=request['parent_release_id'],
                        defaults=request['defaults'],preview_hash=request['preview_hash'],progress=progress,on_started=started)
                else:
                    normalization={key:request[key] for key in ('legacy_version','defaults','resolutions','preview_hash','reference_resolutions','legacy_omissions')} if request['normalize_libraries'] else None
                    release=self.store.import_release(request['source'],request['nx_version'],request['name'],job['actor'],
                        normalization=normalization,source_path=request.get('source_path'),target=request.get('target'),
                        target_path=request.get('target_path'),progress=progress,on_started=started)
                import hashlib
                report={'contract_version':1,'job_id':identifier,'mode':job['mode'],'release_id':release['id'],
                    'nx_version':release['nx_version'],'file_count':len(release['manifest']),
                    'manifest_sha256':hashlib.sha256(canonical(release['manifest']).encode()).hexdigest(),
                    'preview_hash':request.get('preview_hash'),'automatic_approval':False,'automatic_activation':False}
                raw=canonical(report).encode();offline_path(folder/'report.json').write_bytes(raw)
                self._update(identifier,status='completed',progress=100,release_id=release['id'],
                    message='Neuer Ordner erstellt; NX-Test und fachliche Abnahme erforderlich',report_sha256=hashlib.sha256(raw).hexdigest(),
                    summary=report,finished_at=now())
                with self.store.lock,self.store.db() as db:self.store.audit(db,job['actor'],'migration.job.completed',{'id':identifier,'release_id':release['id'],'report_sha256':self.get(identifier)['report_sha256']})
                return
            if job['mode']=='extract':
                from .extraction import extract
                report=extract(self.store,job['request']['version'],folder/'extraction',progress,
                    lambda pid:self._update(identifier,child_pid=pid,child_stamp=process_stamp(pid) if pid else None))
                raw=canonical(report).encode('utf-8')
                offline_path(folder/'report.json').write_bytes(raw)
                import hashlib
                self._update(identifier,status='completed',progress=100,message='SIDT statisch extrahiert; Eingabe muss ausdrücklich ausgewählt werden',
                    report_sha256=hashlib.sha256(raw).hexdigest(),summary={'version':report['version'],'file_count':report['file_count'],'bytes':report['bytes']},finished_at=now())
                with self.store.lock,self.store.db() as db:self.store.audit(db,job['actor'],'installer.extraction.completed',{'id':identifier,'version':report['version'],'manifest_sha256':hashlib.sha256(canonical(report['manifest']).encode()).hexdigest()})
                return
            plan,report=Planner(self.store).build(job['request'],progress,job_id=identifier)
            raw=canonical(report).encode('utf-8')
            offline_path(folder/'report.json').write_bytes(raw)
            import hashlib
            self._update(identifier,report_sha256=hashlib.sha256(raw).hexdigest(),
                summary={key:report[key] for key in ('can_build','file_count','bytes','preview_hash')},
                conflicts=len(report['conflicts']),warnings=len(report['warnings']))
            if job['mode']=='preview':
                self._update(identifier,status='completed',progress=100,message='Vorschau erstellt; keine NX-Dateien geschrieben',finished_at=now())
                with self.store.lock,self.store.db() as db:self.store.audit(db,job['actor'],'migration.job.completed',{'id':identifier,'report_sha256':self.get(identifier)['report_sha256']})
                return
            if not report['can_build'] or report['preview_hash']!=job['preview_hash']:
                raise DomainError('Migration inputs changed or conflicts appeared; review a fresh preview.',409)
            target=plan['destination']
            target.mkdir(parents=True,exist_ok=False)
            for index,item in enumerate(plan['files']):
                path=offline_path(target/item['path']);path.parent.mkdir(parents=True,exist_ok=True)
                if 'content' in item:path.write_bytes(item['content'])
                else:
                    origin=offline_path(item['source'])
                    if sha256(origin)!=item['sha256']:raise DomainError('Migration source changed before copy: '+item['path'],409)
                    shutil.copyfile(origin,path)
                if sha256(path)!=item['sha256']:raise DomainError('Migration copy SHA failed: '+item['path'],409)
                if index%50==0:progress('building',25+int(60*(index+1)/max(len(plan['files']),1)),f'{index+1} / {len(plan["files"])} Dateien')
            for relative in ('UGII','CMM_INSPECTION/custom'):
                offline_path(target/relative).mkdir(parents=True,exist_ok=True)
            progress('verifying',90,'Eingaben und fertigen Datenstand nochmals pruefen')
            for path,expected in plan['inputs'].items():
                if sha256(offline_path(path))!=expected:raise DomainError('Migration inputs changed during build.',409)
            manifest=self.store.manifest(target)
            if sorted(manifest,key=lambda item:item['path'])!=sorted(report['result_manifest'],key=lambda item:item['path']):
                raise DomainError('Unexpected/changed migration output files.',409)
            progress('registering',95,'NX-Datenstand und Testpaket registrieren')
            release=self.store.adopt_migration(target,job['request']['name'],job['request']['target_version'],manifest,
                {'job_id':identifier,'preview_id':job['preview_id'],'preview_hash':report['preview_hash'],
                 'source_folder':report['source_folder'],'source_version':job['request']['source_version'],
                 'source_release_id':job['request'].get('source_release_id'),'defaults':job['request'].get('defaults',{}),
                 'reference_resolutions':report['libraries']['reference_resolutions']},job['actor'])
            self._update(identifier,status='completed',progress=100,message='NX-Datenstand erstellt; NX-Test und Abnahme erforderlich',release_id=release['id'],finished_at=now())
            with self.store.lock,self.store.db() as db:self.store.audit(db,job['actor'],'migration.job.completed',{'id':identifier,'release_id':release['id'],'report_sha256':self.get(identifier)['report_sha256']})
        except Exception as error:
            self._update(identifier,status='failed',message=str(error)[:1500],finished_at=now(),partial_output_retained=True)
            with self.store.lock,self.store.db() as db:self.store.audit(db,job['actor'],'migration.job.failed',{'id':identifier,'error':str(error)[:1500]})
