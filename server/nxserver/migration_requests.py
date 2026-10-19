"""Atomic upgrade/extraction acceptance receipts, never worker restarts or undo."""
import json

from .auditstate import encoded, fingerprint
from .safety import DomainError
from .write_requests import key

SCHEMA='''CREATE TABLE IF NOT EXISTS migration_requests (
    actor TEXT NOT NULL, request_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('accepted','cancelled')),
    action TEXT NOT NULL, request_sha256 TEXT, job_id TEXT, response TEXT,
    created_at TEXT NOT NULL, PRIMARY KEY(actor,request_key))'''
MODES={'preview','build','extract','import','derive','catalog','file_adopt','result_upload','result_adopt'}
IMMUTABLE=('id','mode','request','preview_id','preview_hash','actor','owner_pid','owner_stamp','created_at','installer','reader')


def request_hash(mode,request,preview_id=None,preview_hash=None):
    if mode not in MODES:raise DomainError('Unsupported migration request mode.')
    return fingerprint([1,'migration.'+mode,request,preview_id,preview_hash])


def response_value(row,job=None):
    try:
        key(row['request_key'])
        if not isinstance(row['actor'],str) or not row['actor']:raise ValueError()
        if row['state']=='cancelled':
            if row['action']!='cancelled' or any(row[name] is not None for name in ('request_sha256','job_id','response')):raise ValueError()
            return None
        result=json.loads(row['response'])
        current=json.loads(job['payload']) if job is not None else None
        if row['state']!='accepted' or not isinstance(result,dict) or not isinstance(current,dict):raise ValueError()
        if result['mode'] not in MODES or row['action']!='migration.'+result['mode']:raise ValueError()
        if not row['job_id'] or key(row['job_id'])!=result['id'] or job['id']!=result['id'] or result['actor']!=row['actor']:raise ValueError()
        if result['status']!='queued' or type(result['progress']) is not int or result['progress']!=0:raise ValueError()
        if encoded({name:result.get(name) for name in IMMUTABLE})!=encoded({name:current.get(name) for name in IMMUTABLE}):raise ValueError()
        if row['request_sha256']!=request_hash(result['mode'],result['request'],result.get('preview_id'),result.get('preview_hash')):raise ValueError()
        if row['created_at']!=result['created_at']:raise ValueError()
        return result
    except (ValueError,TypeError,KeyError,AttributeError,DomainError) as error:
        raise DomainError('Stored migration acceptance differs from its original job/request.',409) from error


def read_row(db,actor,request_key):
    row=db.execute('SELECT * FROM migration_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    if row is None:return None,None
    job=db.execute('SELECT id,payload FROM migration_jobs WHERE id=?',(row['job_id'],)).fetchone() if row['state']=='accepted' else None
    return row,response_value(row,job)


def replay(db,actor,request_key,request_sha):
    if request_key is None:return None
    row,result=read_row(db,actor,request_key)
    if row is None:return None
    if row['state']=='cancelled':raise DomainError('Migration request was fenced before acceptance; use a new key for a new deliberate action.',409)
    if row['request_sha256']!=request_sha:raise DomainError('Migration Idempotency-Key already belongs to different inputs/action/preview.',409)
    return result


def commit(db,actor,request_key,request_sha,job):
    if request_key is None:return {}
    db.execute('INSERT INTO migration_requests(actor,request_key,state,action,request_sha256,job_id,response,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (actor,key(request_key),'accepted','migration.'+job['mode'],request_sha,job['id'],encoded(job),job['created_at']))
    return {'_migration_request':{'actor':actor,'key':request_key}}


def get(store,actor,request_key):
    with store.db() as db:
        db.execute('BEGIN');row,result=read_row(db,actor,request_key)
    if row is None:raise DomainError('No migration acceptance/fence yet; an in-flight request may still be accepted.',404)
    return {'contract_version':1,'state':row['state'],'request_key':row['request_key'],'action':row['action'],
        'request_sha256':row['request_sha256'],'job_id':row['job_id'],'created_at':row['created_at'],'result':result,
        'worker_completion_verified':False,'automatic_restart':False,'automatic_activation':False}


def cancel(store,actor,request_key):
    from .store import now
    request_key=key(request_key)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
        if row is None:
            db.execute('INSERT INTO migration_requests(actor,request_key,state,action,created_at) VALUES(?,?,?,?,?)',
                (actor,request_key,'cancelled','cancelled',now()))
            store.audit(db,actor,'migration.request.cancelled',{'_migration_request':{'actor':actor,'key':request_key}})
    return get(store,actor,request_key)  # An accepted worker is not cancelled or restarted.
