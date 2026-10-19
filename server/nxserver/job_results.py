"""Bind multi-object adoption results/attachment intents to the same SQL commit."""
import json

from .auditstate import encoded
from .safety import DomainError
from .write_requests import key

ACTIVE={'queued','planning','building','verifying','registering'}


def load(db,job_id,actor,mode):
    if not job_id or key(job_id)!=job_id:raise DomainError('Invalid adoption job binding.',409)
    row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(job_id,)).fetchone()
    if row is None:raise DomainError('Adoption job is missing.',409)
    job=json.loads(row['payload'])
    modes={mode} if isinstance(mode,str) else set(mode)
    if job.get('id')!=job_id or job.get('actor')!=actor or job.get('mode') not in modes or job.get('status') not in ACTIVE:
        raise DomainError('Adoption job identity/role/mode/state differs.',409)
    return job


def write(db,job):
    from .store import now
    job['updated_at']=now();db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(encoded(job),job['id']))
    return {'_operation_job':job['id']}


def resource_result(db,job_id,actor,mode,resources):
    if job_id is None:return {}
    job=load(db,job_id,actor,mode)
    if job.get('domain_result') is not None:raise DomainError('Adoption domain result already committed; do not repeat the operation.',409)
    job['domain_result']={'contract_version':1,'mode':mode,'resources':resources,'status':'draft','nx_files_changed':False}
    return write(db,job)


def reserve_asset(store,job_id,actor,asset):
    if job_id is None:return
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');job=load(db,job_id,actor,('file_adopt','result_adopt'))
        allocations=job.setdefault('asset_allocations',[])
        if len(allocations)>=100 or any(item['id']==asset['id'] for item in allocations):raise DomainError('Invalid/duplicate adoption asset allocation.',409)
        allocations.append({**asset,'state':'reserved'})
        store.audit(db,actor,'migration.job.asset.reserved',{'id':job_id,'asset':asset,**write(db,job)})


def stored_asset(db,job_id,actor,asset):
    if job_id is None:return {}
    job=load(db,job_id,actor,('file_adopt','result_adopt'));entry=next((item for item in job.get('asset_allocations',[]) if item['id']==asset['id']),None)
    if not entry or entry['state']!='reserved' or any(entry[name]!=asset[name] for name in ('id','filename','sha256','size')):
        raise DomainError('Stored asset differs from its adoption allocation.',409)
    entry['state']='stored';return write(db,job)


def validate_result(job,versions,assets):
    """Pure captured-metadata check; never source/file reads during integrity."""
    try:
        seen=set()
        allocations=job.get('asset_allocations',[])
        if allocations and job['mode'] not in ('file_adopt','result_adopt'):raise ValueError()
        if not isinstance(allocations,list) or len(allocations)>100:raise ValueError()
        from .safety import asset_filename
        for item in allocations:
            if not item['id'] or key(item['id'])!=item['id'] or item['id'] in seen:raise ValueError()
            seen.add(item['id']);asset_filename(item['filename'])
            if type(item['size']) is not int or not 1<=item['size']<=20*1024*1024:raise ValueError()
            if not isinstance(item['sha256'],str) or len(item['sha256'])!=64 or any(c not in '0123456789abcdef' for c in item['sha256']):raise ValueError()
            if item['state']=='stored':
                asset=assets[item['id']]
                if any(asset[name]!=item[name] for name in ('filename','sha256','size')):raise ValueError()
            elif item['state']!='reserved' or item['id'] in assets:raise ValueError()
        result=job.get('domain_result')
        selection=None
        if job.get('mode')=='catalog' and (job.get('request') or {}).get('selection') is not None:
            from .libraries import catalog_selection
            selection=catalog_selection(job['request']['libraries'],job['request']['selection'])
        if result is None:
            if job.get('mode') in ('catalog','file_adopt','result_upload','result_adopt') and job.get('status')=='completed':raise ValueError()
            return
        if result['contract_version']!=1 or result['mode']!=job['mode'] or result['status']!='draft' or result['nx_files_changed'] is not False:raise ValueError()
        if job['mode'] not in ('catalog','file_adopt','result_upload','result_adopt') or not isinstance(result['resources'],list):raise ValueError()
        ids=set()
        for resource in result['resources']:
            if not resource['id'] or key(resource['id'])!=resource['id'] or resource['id'] in ids or type(resource['revision']) is not int or resource['revision']<1:raise ValueError()
            ids.add(resource['id']);version=versions[resource['id']][resource['revision']]
            payload=json.loads(version['payload'])
            if version['actor']!=job['actor'] or payload['cam_status']!='draft':raise ValueError()
            if job['mode']=='catalog':
                origin=payload['origin']
                if origin['release_id']!=job['request']['release_id'] or origin['library_id'] not in job['request']['libraries']:raise ValueError()
                if selection is not None and origin['key'] not in selection[origin['library_id']]:raise ValueError()
            if job['mode']=='result_adopt' and payload.get('model_package'):
                if payload['model_package']['result_id']!=job['request']['result_id']:raise ValueError()
        if job['mode']=='file_adopt':
            if len(ids)!=1 or not allocations or any(item['state']!='stored' for item in allocations):raise ValueError()
            version=versions[result['resources'][0]['id']][result['resources'][0]['revision']]
            payload=json.loads(version['payload'])
            if payload['file_package']['provenance']['release_id']!=job['request']['release_id']:raise ValueError()
            if not seen<=set(payload['asset_ids']) or not seen<=set(entry['asset_id'] for entry in payload['file_package']['entries']):raise ValueError()
        if job['mode']=='result_adopt' and (not ids or not allocations or any(item['state']!='stored' for item in allocations)):
            raise ValueError()
    except (KeyError,ValueError,TypeError,AttributeError,DomainError) as error:
        raise DomainError('Adoption result/attachment intent differs from immutable versions or asset metadata.',409) from error


def verify_in_db(db,job):
    try:
        versions={};assets={}
        for resource in (job.get('domain_result') or {}).get('resources',[]):
            row=db.execute('SELECT * FROM resource_versions WHERE id=? AND revision=?',(resource['id'],resource['revision'])).fetchone()
            if row:versions.setdefault(resource['id'],{})[resource['revision']]=dict(row)
        for item in job.get('asset_allocations',[]):
            row=db.execute('SELECT * FROM assets WHERE id=?',(item['id'],)).fetchone()
            if row:assets[item['id']]=dict(row)
        validate_result(job,versions,assets)
        from .results import validate_operation,operation_result_ids
        records={}
        for identifier in operation_result_ids(job):
            row=db.execute('SELECT * FROM geometry_results WHERE id=?',(identifier,)).fetchone()
            if row:records[identifier]=row
        validate_operation(job,records)
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        raise DomainError('Stored adoption job evidence has an invalid shape.',409) from error


def reserve_result(store,job_id,actor,allocation):
    if job_id is None:return
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');job=load(db,job_id,actor,'result_upload')
        if job.get('result_allocation'):raise DomainError('Result archive allocation already exists; never overwrite.',409)
        job['result_allocation']={**allocation,'state':'reserved'}
        store.audit(db,actor,'migration.job.result.reserved',{'id':job_id,**write(db,job)})


def result_uploaded(db,job_id,actor,result,allocated):
    if job_id is None:return {}
    job=load(db,job_id,actor,'result_upload')
    allocation=job.get('result_allocation')
    if allocated:
        if not allocation or allocation['state']!='reserved' or any(allocation[name]!=result[name] for name in ('id','sha256','size')):
            raise DomainError('Stored result archive differs from its allocation.',409)
        allocation['state']='stored'
    job['domain_result']={'contract_version':1,'mode':'result_upload','resources':[],'status':'draft','nx_files_changed':False,
        'result_id':result['id'],'sha256':result['sha256'],'size':result['size'],'geometry_job_id':result['job_id'],'reused_existing':not allocated}
    return write(db,job)
