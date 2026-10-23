"""Read-only audit/database/artifact checks; no repair, approval or activation."""
import copy
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from .auditstate import KEYS,ERP_KEYS,encoded,fingerprint,key_for,row_binding
from .folders import scoped_path
from .migration import portable
from .safety import DomainError,offline_path,tree_files
from .store import now,resource_identity,FUNCTIONAL_TESTS
from .upgrades import RUNNING,writer_active

ZERO='0'*64
MAX_ISSUES=500
MAX_ANCHOR_BYTES=8*1024*1024


def position(sequence=0,digest=ZERO):return {'sequence':sequence,'hash':digest}


def valid_binding(binding):
    table=binding.get('table');key=binding.get('key');columns=binding.get('columns');value=binding.get('sha256')
    if table not in KEYS or not isinstance(key,list) or len(key)!=len(KEYS[table]) or not isinstance(columns,list):
        raise DomainError('Invalid state/checkpoint binding.')
    if not columns or len(columns)!=len(set(columns)) or any(not isinstance(column,str) for column in columns):
        raise DomainError('Invalid checkpoint column list.')
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):raise DomainError('Invalid checkpoint SHA-256.')
    for column,item in zip(KEYS[table],key):
        if column in ('revision','singleton'):
            if type(item) is not int:raise DomainError('Checkpoint key has an invalid integer.')
        elif not isinstance(item,str):raise DomainError('Checkpoint key has an invalid string.')
    return table,tuple(key)


def checkpoint_bindings(anchor):
    if not anchor:return {}
    if len(encoded(anchor).encode())>MAX_ANCHOR_BYTES:raise DomainError('Checkpoint exceeds 8 MiB.')
    result={}
    for table,group in anchor['tables'].items():
        for entry in group['rows']:
            binding={'table':table,'columns':group['columns'],**entry}
            key=valid_binding(binding)
            if key in result:raise DomainError('Repeated checkpoint row.')
            result[key]=binding
    return result


class Issues:
    def __init__(self):self.errors=[];self.warnings=[];self.error_count=0;self.warning_count=0
    def add(self,code,entity,detail,warning=False):
        target=self.warnings if warning else self.errors
        if warning:self.warning_count+=1
        else:self.error_count+=1
        if len(target)<MAX_ISSUES:target.append({'code':code,'entity':entity,'detail':detail})


def snapshot(store,issues=None,anchor=None):
    """One SQLite read transaction; auditing does not mutate the audit itself."""
    path=offline_path(store.root/'management.sqlite3')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30);db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        present={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables={table:[dict(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY '+','.join(KEYS[table]))]
                for table in KEYS if table in present}
        if set(KEYS)-set(tables)-set(ERP_KEYS)-{'geometry_results','write_requests','migration_requests','asset_requests','geometry_requests','backup_requests','log_requests','release_requests'}:raise DomainError('Core management tables are missing.',409)
        needed_audits={row['audit_sequence'] for row in tables.get('release_requests',[]) if row['audit_sequence'] is not None};receipt_audits={}
        hash_state=hashlib.sha256();bindings={};head=position();count=0;anchor_found=not anchor or anchor['audit']==position()
        for row in db.execute('SELECT * FROM audit ORDER BY sequence'):
            row=dict(row);hash_state.update(encoded(row).encode()+b'\n');count+=1
            if row['sequence'] in needed_audits:receipt_audits[row['sequence']]=row
            computed=hashlib.sha256(encoded([row['previous_hash'],row['actor'],row['action'],row['payload'],row['created_at']]).encode()).hexdigest()
            if issues:
                if row['sequence']!=head['sequence']+1:issues.add('audit_sequence_gap',str(row['sequence']),'Audit sequence is not contiguous.')
                if row['previous_hash']!=head['hash']:issues.add('audit_previous_hash',str(row['sequence']),'Previous hash does not match the preceding entry.')
                if computed!=row['hash']:issues.add('audit_hash',str(row['sequence']),'Audit entry bytes no longer match the stored hash.')
            if anchor and row['sequence']==anchor['audit']['sequence']:
                anchor_found=row['hash']==anchor['audit']['hash']
            head=position(row['sequence'],row['hash'])
            if issues:
                try:
                    payload=json.loads(row['payload']);state=payload.get('_state')
                    if state:
                        if state.get('schema')!=1:raise DomainError('Unsupported audit-state schema.')
                        for binding in state['bindings']:
                            key=valid_binding(binding);bindings[key]=(row['sequence'],binding)
                except (ValueError,TypeError,KeyError,AttributeError,DomainError):
                    issues.add('audit_payload',str(row['sequence']),'Invalid audit JSON/state binding.')
        watermark=db.execute("SELECT seq FROM sqlite_sequence WHERE name='audit'").fetchone()
        watermark=watermark[0] if watermark else 0
        if issues:
            if watermark!=head['sequence']:issues.add('audit_tail',str(head['sequence']),'Audit AUTOINCREMENT watermark differs from its tail.')
            if not anchor_found:issues.add('anchor_mismatch','audit','Trusted checkpoint head is missing or changed (including rollback/truncation).')
            sql=[row[0] for row in db.execute('PRAGMA integrity_check')]
            for text in sql:
                if text!='ok':issues.add('sqlite_integrity','database',text)
        else:sql=[]
        hash_state.update(encoded({'tables':tables,'watermark':watermark}).encode())
        state_hash=hash_state.hexdigest()
        tables.setdefault('geometry_results',[])  # Old snapshots predate result packages; do not alter their captured digest.
        tables.setdefault('write_requests',[])  # Same compatibility rule for archives before write receipts.
        tables.setdefault('migration_requests',[])  # Capture legacy digest before adding absent acceptance receipts.
        tables.setdefault('asset_requests',[])  # Same rule for legacy archives before upload allocations.
        tables.setdefault('geometry_requests',[])  # Legacy snapshot digest precedes STEP request intents.
        tables.setdefault('backup_requests',[])  # Backup acceptances predate neither old archive hashes nor audits.
        tables.setdefault('log_requests',[])  # Preserve old archive snapshot digests before adding absent log receipts.
        tables.setdefault('release_requests',[])  # Historical archives retain their pre-transition-receipt digest.
        return {'tables':tables,'head':head,'count':count,'bindings':bindings,'sha256':state_hash,'sqlite':sql,'release_receipt_audits':receipt_audits}
    except sqlite3.DatabaseError as error:raise DomainError('Management database could not be read/verified.',409) from error
    finally:db.close()


def _json(text,issues,entity):
    try:return json.loads(text) if text is not None else None
    except (TypeError,ValueError):issues.add('invalid_json',entity,'Management JSON is malformed.');return None


def metadata(state,issues,anchor,store=None):
    tables=state['tables'];expected=checkpoint_bindings(anchor);anchor_sequence=anchor['audit']['sequence'] if anchor else -1
    for key,(sequence,binding) in state['bindings'].items():
        if sequence>anchor_sequence:expected[key]=binding
    current={(table,key_for(table,row)):row for table,rows in tables.items() for row in rows}
    bad=set();bound=0;unbound=0
    for key,binding in expected.items():
        row=current.get(key)
        if row is None:issues.add('missing_bound_row',str(key),'Previously bound management row is missing.');bad.add(key);continue
        columns=binding['columns']
        if not set(columns)<=set(row) or fingerprint({column:row[column] for column in columns})!=binding['sha256']:
            # Progress is mutable until its terminal audit event. Do not treat
            # legitimate running work as database damage or claim it verified.
            payload=(_json(row['payload'],issues,str(key)) or {}) if key[0]=='migration_jobs' else None
            # Archive metadata has no runtime context. Never borrow a Future
            # from another management root while verifying an isolated restore.
            running=payload is not None and (payload.get('status') in RUNNING or
                (store is not None and 'id' in payload and 'status' in payload and writer_active(store,payload)))
            issues.add('writer_in_progress' if running else 'state_hash',str(key),
                'Running job is not a finalized checkpoint.' if running else 'Current metadata differs from its latest audit/checkpoint binding.',warning=running)
            bad.add(key)
        elif set(columns)!=set(row):issues.add('unbound_columns',str(key),'New management columns are not covered by the old binding.',warning=True)
        else:bound+=1
    for key,row in current.items():
        if key not in expected:
            bootstrap=key==('active',(1,)) and row=={'singleton':1,'release_id':None,'generation':0}
            if not bootstrap:unbound+=1
    if unbound:issues.add('legacy_unbound_rows','metadata',str(unbound)+' rows have no audit/checkpoint byte binding. Old audits were not rewritten.',warning=True)
    resources={row['id']:row for row in tables['resources']};versions={};asset_ids={row['id'] for row in tables['assets']}
    from .erp_variants import validate_tables as validate_erp_model
    try:validate_erp_model(tables,resources)
    except (DomainError,TypeError,KeyError,ValueError):issues.add('erp_variant_model','metadata','Shared ERP article/variant metadata or immutable history/receipts are inconsistent.')
    assets={row['id']:row for row in tables['assets']}
    release_ids={row['id'] for row in tables['releases']}
    identities={}
    for row in tables['resource_versions']:
        versions.setdefault(row['id'],{})[row['revision']]=row
        if row['id'] not in resources:issues.add('orphan_revision',row['id'],'Revision has no current resource.')
    for identifier,row in resources.items():
        history=versions.get(identifier,{})
        if (type(row['revision']) is not int or row['revision']<1 or len(history)!=row['revision'] or
                any(type(revision) is not int for revision in history) or
                any(revision!=index for index,revision in enumerate(sorted(history),1))):
            issues.add('revision_gap',identifier,'Resource revision history is missing/non-contiguous.');continue
        latest=history[row['revision']]
        if latest['payload']!=row['payload'] or latest['created_at']!=row['updated_at']:
            issues.add('revision_head',identifier,'Current resource differs from its latest immutable revision.')
        payload=_json(row['payload'],issues,identifier)
        if not isinstance(payload,dict):continue
        from .libraries import validate_catalog_cam
        try:validate_catalog_cam(payload)
        except (DomainError,TypeError,KeyError,ValueError,AttributeError):issues.add('catalog_cam_contract',identifier,'Current catalog kind/CAM structure or approval representation is inconsistent; no automatic repair.')
        erp=payload.get('erp')
        try:
            external=encoded([erp['company_id'],erp['system_id']]) if erp else None
            if external!=row['external_key']:issues.add('erp_link',identifier,'ERP lookup key does not match the linked ERP payload.')
            identity=resource_identity(payload)
            if identity in identities and identity:issues.add('duplicate_identity',identifier,'Native key is managed by more than one resource.')
            if identity:identities[identity]=identifier
            for asset in payload.get('asset_ids',[]):
                if asset not in asset_ids:issues.add('missing_asset_reference',identifier,'Current attachment reference is missing: '+str(asset))
        except (DomainError,TypeError,KeyError,ValueError):issues.add('invalid_resource',identifier,'Resource CAM/ERP identity is invalid.')
        if payload.get('file_package') is not None:
            # Snapshot metadata only: do not perform live Store/FS reads here.
            from .filepackages import Package,CHECKS,version_path
            try:
                package=Package.model_validate(payload['file_package']).model_dump();paths=set();assigned=set()
                for entry in package['entries']:
                    version_path(payload['kind'],entry['path'],package['nx_version']);key=entry['path'].casefold();asset=entry['asset_id']
                    if key in paths or asset in assigned or asset not in payload['asset_ids'] or asset not in assets:raise ValueError('Invalid binding')
                    paths.add(key);assigned.add(asset)
                    if entry['path'].split('/')[-1]!=assets[asset]['filename']:raise ValueError('Renamed file')
                if payload['cam_status']=='approved':
                    if (set(package['checks'])!=CHECKS or not all(package['checks'].values()) or len(package['evidence'].strip())<20 or
                            package['asset_hashes']!={asset:assets[asset]['sha256'] for asset in assigned}):raise ValueError('Unqualified package')
                provenance=package.get('provenance')
                if provenance and (provenance.get('release_id') not in release_ids or not isinstance(provenance.get('owned_files'),list)):
                    raise ValueError('Missing ownership source')
            except (DomainError,TypeError,KeyError,ValueError,AttributeError):
                issues.add('invalid_file_package',identifier,'NX file-package paths, attachment/check bindings or source ownership are invalid.')
        elif payload.get('kind') in ('template','ui_standard') and payload.get('cam_status')=='approved':
            issues.add('invalid_file_package',identifier,'Approved template/UI standard has no qualified file package.')
    from .write_requests import response_value
    for row in tables['write_requests']:
        try:response_value(row,versions.get(row['resource_id'],{}).get(row['revision']))
        except DomainError:issues.add('invalid_write_receipt',row['request_key'],'Write receipt differs from its immutable resource revision or cancellation fence.')
    from .asset_requests import response_value as upload_response
    for row in tables['asset_requests']:
        try:upload_response(row,assets.get(row['asset_id']))
        except DomainError:issues.add('invalid_upload_receipt',row['request_key'],'Upload allocation/receipt differs from immutable asset metadata.')
        if row['state'] in ('reserved','failed','cancelled') and row['asset_id']:
            issues.add('partial_upload_allocation',row['request_key'],'Upload path intent is diagnostic only, not a stored/verified attachment.',warning=True)
    from .migration_requests import response_value as migration_response
    from .release_requests import response_value as release_response
    for row in tables['release_requests']:
        audit=state.get('release_receipt_audits',{}).get(row['audit_sequence'])
        try:release_response(row,audit)
        except DomainError:issues.add('invalid_release_receipt',row['request_key'],'Approval/activation response differs from its immutable original audit.')
        if row['state'] in ('reserved','failed','cancelled') and row['release_id']:
            issues.add('partial_release_request',row['request_key'],'Final transition not committed; technical diagnostics may already exist.',warning=True)
    from .log_requests import response_value as log_response
    logs={row['id']:row for row in tables['syslogs']}
    for row in tables['log_requests']:
        try:log_response(row,logs.get(row['log_id']))
        except DomainError:issues.add('invalid_log_receipt',row['request_key'],'System-log allocation/receipt differs from immutable log metadata.')
        if row['state'] in ('reserved','failed','cancelled') and row['log_id']:
            issues.add('partial_log_allocation',row['request_key'],'System-log path intent is diagnostic only, not a stored/verified protocol.',warning=True)
    from .backup_requests import response_value as backup_response
    for row in tables['backup_requests']:
        try:backup_response(row)
        except DomainError:issues.add('invalid_backup_acceptance',row['request_key'],'Backup acceptance/failure differs from its pinned request identity.')
    from .geometry_requests import response_value as geometry_response
    geometry_jobs={row['id']:row for row in tables['geometry_jobs']}
    for row in tables['geometry_requests']:
        try:
            value=json.loads(row['job_snapshot']) if row['job_id'] else None
            version=versions.get(value['resource_id'],{}).get(value['resource_revision']) if value else None
            geometry_response(row,geometry_jobs.get(row['job_id']),version,assets.get(value['input']['id']) if value else None)
        except (DomainError,ValueError,TypeError,KeyError):issues.add('invalid_geometry_receipt',row['request_key'],'STEP intent/receipt differs from its immutable job, resource revision or input.')
        if row['state'] in ('reserved','failed','cancelled') and row['job_id']:
            issues.add('partial_geometry_allocation',row['request_key'],'STEP package intent is diagnostic only, not a committed job or verified package.',warning=True)
    jobs={row['id']:row for row in tables['migration_jobs']}
    from .job_results import validate_result
    from .results import validate_operation
    prepared_results={row['id']:row for row in tables['geometry_results']}
    for row in tables['migration_jobs']:
        try:
            job=json.loads(row['payload']);validate_result(job,versions,assets);validate_operation(job,prepared_results)
        except (DomainError,ValueError,TypeError):issues.add('invalid_adoption_result',row['id'],'Adoption result/attachment intents differ from immutable metadata.')
        payload=_json(row['payload'],issues,row['id']) or {}
        allocations=payload.get('asset_allocations') if isinstance(payload,dict) else None
        if isinstance(allocations,list) and payload.get('status') in ('failed','interrupted') and any(item.get('state')=='reserved' for item in allocations if isinstance(item,dict)):
            issues.add('partial_asset_allocation',row['id'],'Attachment intent exists, but no stored/verified asset is claimed; partial files are diagnostic only.',warning=True)
        if payload.get('result_allocation',{}).get('state')=='reserved':
            issues.add('partial_result_allocation',row['id'],'Result archive intent exists, not a committed/verified package.',warning=True)
    for row in tables['migration_requests']:
        try:migration_response(row,jobs.get(row['job_id']))
        except DomainError:issues.add('invalid_migration_receipt',row['request_key'],'Migration acceptance differs from its original job/request or cancellation fence.')
    releases={row['id']:row for row in tables['releases']}
    visited=set()
    for row in tables['releases']:
        if row['parent_id'] and row['parent_id'] not in releases:issues.add('missing_parent',row['id'],'Release parent is missing.')
        if row['status']=='blocked':issues.add('blocked_release',row['id'],'Release has a known blocked validation state; matching bytes do not qualify it for NX.',warning=True)
        path=set();identifier=row['id']
        while identifier in releases and identifier not in visited:
            if identifier in path:issues.add('release_cycle',row['id'],'Release history contains a parent cycle.');break
            path.add(identifier);identifier=releases[identifier]['parent_id']
        visited.update(path)
        if row['status']=='approved':
            validation=_json(row['validation'],issues,row['id']);review=_json(row['review'],issues,row['id'])
            if (not isinstance(validation,dict) or validation.get('integrity_passed') is not True or
                not isinstance(review,dict) or set(review.get('tests',{}))!=FUNCTIONAL_TESTS or
                any(value is not True for value in review.get('tests',{}).values())):
                issues.add('approval_evidence',row['id'],'Approved release has missing/incomplete acceptance evidence.')
    if len(tables['active'])!=1 or tables['active'][0]['singleton']!=1:
        issues.add('active_singleton','active','Exactly one active-pointer singleton is required.')
    for row in tables['active']:
        if type(row['generation']) is not int or row['generation']<0:issues.add('active_generation','active','Active-pointer generation is invalid.')
        if row['release_id'] and (row['release_id'] not in releases or releases[row['release_id']]['status']!='approved'):
            issues.add('active_not_approved','active','Active release is missing or not approved.')
    for row in tables['test_launchers']:
        if row['release_id'] not in releases:issues.add('orphan_launcher',row['release_id'],'Test launcher has no release.')
        else:
            try:
                from .launchprofiles import validate_profile
                validate_profile(json.loads(row['payload']).get('ui_profile'),json.loads(releases[row['release_id']]['manifest'] or '[]'))
            except (DomainError,ValueError,TypeError,KeyError,AttributeError) as error:issues.add('launcher_ui_profile',row['release_id'],str(error))
    for row in tables['geometry_jobs']:
        payload=_json(row['payload'],issues,row['id'])
        if not isinstance(payload,dict):continue
        if row['resource_id']!=payload.get('resource_id') or payload.get('resource_revision') not in versions.get(row['resource_id'],{}):
            issues.add('job_resource_revision',row['id'],'STEP job references a missing/different resource revision.')
    for row in tables['sidt_inputs']:
        job=next((item for item in tables['migration_jobs'] if item['id']==row['job_id']),None)
        payload=_json(job['payload'],issues,row['job_id']) if job else None
        if not isinstance(payload,dict) or payload.get('mode')!='extract' or payload.get('status')!='completed' or payload.get('request',{}).get('version')!=row['version']:
            issues.add('invalid_sidt_selection',row['version'],'Selected extraction is missing/incomplete/wrong version.')
    jobs={row['id']:row for row in tables['geometry_jobs']}
    results={row['id']:row for row in tables['geometry_results']}
    for row in results.values():
        if row['job_id'] not in jobs or row['resource_id']!=jobs[row['job_id']]['resource_id']:
            issues.add('orphan_geometry_result',row['id'],'Prepared package has no matching source job/resource.')
    for row in resources.values():
        value=_json(row['payload'],issues,row['id']) or {};package=value.get('model_package')
        if package and package.get('result_id') not in results:issues.add('missing_model_package',row['id'],'Bound prepared model package is missing.')
    return {'bound_rows':bound,'legacy_unbound_rows':unbound,'bad_rows':bad}


def _uuid(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise DomainError('Artifact ID must be a canonical UUID.')
    return value


def check_file(path,expected_hash,expected_size=None):
    path=offline_path(path)
    if not isinstance(expected_hash,str) or not re.fullmatch('[a-f0-9]{64}',expected_hash):raise DomainError('Invalid artifact SHA-256.')
    before=path.stat()
    if expected_size is None:expected_size=before.st_size
    if type(expected_size) is not int or expected_size<0 or before.st_size!=expected_size:raise DomainError('Artifact size changed.')
    digest=hashlib.sha256();length=0
    with path.open('rb') as stream:
        while True:
            data=stream.read(min(1024*1024,expected_size-length+1))
            if not data:break
            length+=len(data)
            if length>expected_size:raise DomainError('Artifact grew during verification.')
            digest.update(data)
    after=path.stat()
    if length!=expected_size or digest.hexdigest()!=expected_hash or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise DomainError('Artifact bytes/SHA changed or were not stable during verification.')
    return length


def check_tree(root,manifest,checked):
    if not isinstance(manifest,list):raise DomainError('Missing/invalid artifact manifest.')
    expected={}
    for item in manifest:
        portable(item['path']);key=item['path'].casefold()
        if key in expected:raise DomainError('Duplicate/case-colliding manifest path.')
        expected[key]=item
    observed={relative.casefold():(relative,path) for relative,path in tree_files(root)}
    if set(observed)!=set(expected):raise DomainError('Artifact file list differs from its immutable manifest.')
    for key,item in expected.items():
        try:checked(check_file(observed[key][1],item['sha256'],item['size']))
        except (OSError,DomainError) as error:raise DomainError(item['path']+': '+str(error)) from error


def release_root(store,row):
    # Containment/syntax is checked before looking up an arbitrary stored path.
    roots=[store.root/'r',store.root/'releases',*store.folders.targets.values()]
    for root in roots:
        try:
            path=scoped_path(root,row['folder'])
            if path==root:raise DomainError('Release folder cannot be an artifact root itself.')
            if root not in (store.root/'r',store.root/'releases'):
                store.folders._target_safe(path,releases=[])
            return path
        except DomainError:continue
    raise DomainError('Stored release folder is outside the approved artifact roots.')


def artifacts(store,state,issues,bad,progress):
    total={'files':0,'bytes':0,'groups':0}
    def checked(size):
        total['files']+=1;total['bytes']+=size
        if total['files']%100==0:progress('files',str(total['files'])+' Dateien geprüft')
    def attempt(table,row,operation):
        key=(table,key_for(table,row));entity=table+'/'+str(key[1])
        if key in bad:issues.add('untrusted_metadata',entity,'File access skipped because its metadata binding failed.',warning=True);return
        try:operation();total['groups']+=1
        except (DomainError,OSError,KeyError,TypeError,ValueError,AttributeError) as error:issues.add('artifact_integrity',entity,'Artifact missing, unsafe, changed or not fully readable: '+str(error)[:500])
    for row in state['tables']['releases']:
        if row['status'] in ('building','failed') or not row['manifest']:
            issues.add('incomplete_release',row['id'],'Unfinished/failed output retained; files have no completed manifest.',warning=True);continue
        attempt('releases',row,lambda row=row:check_tree(release_root(store,row),_json(row['manifest'],issues,row['id']),checked))
    for row in state['tables']['assets']:
        def asset(row=row):
            name=portable(row['filename'])
            if '/' in name:raise DomainError('Attachment filename is not a basename.')
            checked(check_file(store.root/'assets'/_uuid(row['id'])/name,row['sha256'],row['size']))
        attempt('assets',row,asset)
    for row in state['tables']['syslogs']:
        def log(row=row):
            if not row['id'].startswith('uploaded-'):raise DomainError('Invalid uploaded log ID.')
            _uuid(row['id'][9:]);checked(check_file(store.root/'evidence'/row['id']/'nx.syslog',row['sha256'],row['size']))
        attempt('syslogs',row,log)
    for row in state['tables']['test_launchers']:
        def launcher(row=row):
            payload=_json(row['payload'],issues,row['release_id']);path=store.root/'test-launchers'/_uuid(row['release_id'])
            if payload.get('package_revision'):
                if not re.fullmatch('[a-f0-9]{64}',payload['package_revision']):raise DomainError('Unsafe launcher revision.')
                path=path/payload['package_revision']
            checked(check_file(path/'NX2512_Test.zip',payload['package_sha256']))
        attempt('test_launchers',row,launcher)
    for row in state['tables']['geometry_jobs']:
        def geometry(row=row):
            payload=_json(row['payload'],issues,row['id']);_uuid(row['id'])
            if not payload.get('package_sha256'):
                issues.add('legacy_geometry_package',row['id'],'Old job has no immutable package snapshot.',warning=True);return
            checked(check_file(store.root/'geometry-packages'/row['id']/'NX_STEP.zip',payload['package_sha256'],payload['package_size']))
            asset=payload['input'];asset_row=next((item for item in state['tables']['assets'] if item['id']==asset['id']),None)
            if not asset_row or asset_row['sha256']!=asset['sha256']:raise DomainError('STEP input binding differs.')
            for value,collector in payload.get('collector_packages',{}).items():
                if not re.fullmatch('[a-f0-9]{64}',value) or value!=collector['sha256']:raise DomainError('Invalid collector snapshot binding.')
                checked(check_file(store.root/'geometry-results/collectors'/row['id']/value/'collector.zip',value,collector['size']))
        attempt('geometry_jobs',row,geometry)
    for row in state['tables']['geometry_results']:
        def prepared(row=row):
            payload=_json(row['payload'],issues,row['id']);_uuid(row['id'])
            checked(check_file(store.root/'geometry-results'/row['id']/'result.zip',payload['sha256'],payload['size']))
        attempt('geometry_results',row,prepared)
    for row in state['tables']['migration_jobs']:
        def migration(row=row):
            payload=_json(row['payload'],issues,row['id']);_uuid(row['id'])
            if writer_active(store,payload):
                issues.add('writer_in_progress',row['id'],'Migration/extraction still running; file scope is unfinished.',warning=True);return
            if payload.get('report_sha256'):
                report_path=offline_path(store.root/'upgrades'/row['id']/'report.json')
                if report_path.stat().st_size>64*1024*1024:raise DomainError('Migration report exceeds reader bound.')
                checked(check_file(report_path,payload['report_sha256']))
                if payload['mode']=='extract' and payload['status']=='completed':
                    report=_json(report_path.read_text(encoding='utf-8'),issues,row['id'])
                    from .recovery import extraction_paths
                    raw,_=extraction_paths(store,report,row['id'])
                    check_tree(raw,report['manifest'],checked)
                    info=payload['installer'];directory=raw.parent
                    checked(check_file(directory/'input.exe',info['sha256'],info['size']))
                    if report.get('listing_sha256'):checked(check_file(directory/'listing.txt',report['listing_sha256']))
                    else:issues.add('legacy_listing',row['id'],'Listing diagnostic has no stored SHA binding.',warning=True)
            elif payload['status']=='completed':raise DomainError('Completed migration has no report.')
            if payload['status'] in ('failed','interrupted'):
                issues.add('partial_job',row['id'],'Failed/interrupted output retained; unmanifested partial files excluded.',warning=True)
        attempt('migration_jobs',row,migration)
    return total


def scan(store,include_files=True,anchor=None,progress=lambda *args:None):
    checkpoint_bindings(anchor);issues=Issues();started=now();progress('database','SQLite/Audit und Metadaten-Snapshot prüfen')
    state=snapshot(store,issues,anchor);audit_errors=issues.error_count
    progress('metadata','Revisionen und Hash-Bindungen prüfen');coverage=metadata(state,issues,anchor,store)
    bad=coverage.pop('bad_rows')
    files=artifacts(store,state,issues,bad,progress) if include_files else {'files':0,'bytes':0,'groups':0}
    progress('verifying','Datenbank-Snapshot nach der Dateiprüfung erneut vergleichen')
    stable=snapshot(store)['sha256']==state['sha256']
    checkpoint={'schema_version':1,'audit':state['head'],'tables':{},'files_checked':include_files}
    for table,rows in state['tables'].items():
        if not rows:continue
        checkpoint['tables'][table]={'columns':sorted(rows[0]),'rows':[{'key':list(key_for(table,row)),'sha256':fingerprint(row)} for row in rows]}
    checkpoint_available=stable and issues.error_count==0 and len(encoded(checkpoint).encode())<=MAX_ANCHOR_BYTES
    status='stale' if not stable else 'failed' if issues.error_count else 'partial' if issues.warning_count or not include_files else 'passed'
    return {'status':status,'started_at':started,'finished_at':now(),'stable_database_snapshot':stable,
        'database_sha256':state['sha256'],'audit':{'entries':state['count'],'head':state['head'],'valid':audit_errors==0},
        'metadata':{**coverage,'row_counts':{table:len(rows) for table,rows in state['tables'].items()}},
        'files':{'included':include_files,**files},'error_count':issues.error_count,'warning_count':issues.warning_count,
        'errors':issues.errors,'warnings':issues.warnings,'issues_truncated':issues.error_count>MAX_ISSUES or issues.warning_count>MAX_ISSUES,
        'checkpoint':checkpoint if checkpoint_available else None,'checkpoint_available':checkpoint_available,
        'trust':{'external_anchor_checked':bool(anchor),'signed':False,'historical_authenticity_proven':False,
                 'note':'SHA detects inconsistency, not a rewrite by an administrator controlling both data and checkpoint. Store/review checkpoint separately.'},
        'scope':{'configured_sources_scanned':False,'production_connected':False,'automatic_repair':False,
                 'automatic_approval':False,'data_files_changed':False,'filesystem_atomic_snapshot':False}}


class IntegrityJobs:
    """Ephemeral read-only jobs. Restart never recreates/restarts an old check."""
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.jobs={};self.futures={};self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='nx-integrity')
    def _reconcile(self):
        for identifier,future in self.futures.items():
            if future.done() and self.jobs[identifier]['status']=='running':
                self.jobs[identifier].update(status='failed',phase='finished',message='Prüfthread beendet; kein automatischer Neustart.',finished_at=now())
    def create(self,include_files,anchor):
        checkpoint_bindings(anchor)
        with self.lock:
            self._reconcile()
            if any(job['status']=='running' for job in self.jobs.values()):raise DomainError('An integrity check is already running; observe that job.',409)
            while len(self.jobs)>=20:
                oldest=next(iter(self.jobs));del self.jobs[oldest];self.futures.pop(oldest,None)
            identifier=str(uuid.uuid4());self.jobs[identifier]={'id':identifier,'status':'running','phase':'queued',
                'message':'Prüfung angenommen','started_at':now(),'include_files':include_files,'anchor_supplied':bool(anchor)}
            self.futures[identifier]=self.pool.submit(self._run,identifier,include_files,copy.deepcopy(anchor))
            return self.get(identifier)
    def get(self,identifier):
        with self.lock:
            self._reconcile()
            if identifier not in self.jobs:raise DomainError('Check not found in this server session. No automatic restart.',404)
            return copy.deepcopy(self.jobs[identifier])
    def list(self):
        with self.lock:
            self._reconcile()
            return [copy.deepcopy({key:value for key,value in job.items() if key!='report'}) for job in reversed(list(self.jobs.values()))]
    def _run(self,identifier,include_files,anchor):
        def progress(phase,message):
            with self.lock:self.jobs[identifier].update(phase=phase,message=message)
        try:
            report=scan(self.store,include_files,anchor,progress)
            with self.lock:self.jobs[identifier].update(status='completed',phase='finished',message=report['status'],report=report,finished_at=now())
        except Exception:
            with self.lock:self.jobs[identifier].update(status='failed',phase='finished',message='Prüfung konnte nicht vollständig durchgeführt werden. Keine Daten wurden repariert.',finished_at=now())
