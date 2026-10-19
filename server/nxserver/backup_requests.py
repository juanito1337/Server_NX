"""Durable backup acceptance, never completion, worker restart or authenticity."""
import json
import os

from .auditstate import encoded,fingerprint
from .safety import DomainError
from .write_requests import key

SCHEMA='''CREATE TABLE IF NOT EXISTS backup_requests (
    actor TEXT NOT NULL, request_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('accepted','cancelled')),
    request_sha256 TEXT, job_id TEXT, response TEXT, failure_observation TEXT,
    created_at TEXT NOT NULL, PRIMARY KEY(actor,request_key))'''
MODES={'backup','verify','preview','restore'}
IMMUTABLE=('id','mode','source_backup_id','request','actor','owner_pid','owner_stamp','created_at','management_root','request_key')


def request_hash(mode,request,backup_id):
    if mode not in MODES:raise DomainError('Unknown backup request mode.')
    return fingerprint([1,'backup.'+mode,request,backup_id])


def same_job(one,two):
    if encoded({name:one.get(name) for name in IMMUTABLE})!=encoded({name:two.get(name) for name in IMMUTABLE}):return False
    # backup_id on a backup job becomes its NEW OUTPUT snapshot ID. For the
    # other modes it remains an immutable input. Never conflate these roles.
    return one.get('mode')=='backup' or one.get('backup_id')==two.get('backup_id')
def same_root(one,two):return os.path.normcase(one)==os.path.normcase(str(two))


def response_value(row):
    try:
        if not row['actor'] or key(row['request_key'])!=row['request_key']:raise ValueError()
        if row['state']=='cancelled':
            if any(row[name] is not None for name in ('request_sha256','job_id','response','failure_observation')):raise ValueError()
            return None
        value=json.loads(row['response'])
        if row['state']!='accepted' or not isinstance(value,dict) or key(row['job_id'])!=value['id']:raise ValueError()
        if value['mode'] not in MODES or value['actor']!=row['actor'] or value['request_key']!=row['request_key']:raise ValueError()
        if value['status']!='running' or value['phase']!='queued' or value['created_at']!=row['created_at']:raise ValueError()
        if not isinstance(value['request'],dict) or type(value['owner_pid']) is not int or value['owner_pid']<=0:raise ValueError()
        if not isinstance(value['management_root'],str) or not value['management_root']:raise ValueError()
        if value['backup_id'] is not None:key(value['backup_id'])
        if value.get('source_backup_id')!=value['backup_id']:raise ValueError()
        if row['request_sha256']!=request_hash(value['mode'],value['request'],value['backup_id']):raise ValueError()
        if row['failure_observation'] is not None:
            failure=json.loads(row['failure_observation'])
            if not isinstance(failure,dict) or not same_job(value,failure) or failure['status'] not in ('failed','interrupted') or failure['phase']!='finished':raise ValueError()
            if not failure.get('finished_at') or not (failure.get('start_failure_proven') is True or failure.get('worker_terminal_proven') is True or failure.get('owner_ended_proven') is True):raise ValueError()
        return value
    except (ValueError,TypeError,KeyError,AttributeError,DomainError) as error:
        raise DomainError('Backup acceptance/failure receipt differs from its immutable request/job identity.',409) from error


def read_row(db,actor,request_key):
    row=db.execute('SELECT * FROM backup_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    return row,response_value(row) if row else None


def replay(db,actor,request_key,digest):
    if request_key is None:return None
    row,result=read_row(db,actor,request_key)
    if row is None:return None
    if row['state']=='cancelled':raise DomainError('Backup request fenced before acceptance; no worker may start under this key.',409)
    if row['request_sha256']!=digest:raise DomainError('Backup Idempotency-Key belongs to different action/archive/target/preview decisions.',409)
    return result


def commit(store,db,actor,request_key,digest,job):
    if request_key is None:return
    db.execute('INSERT INTO backup_requests(actor,request_key,state,request_sha256,job_id,response,created_at) VALUES(?,?,?,?,?,?,?)',
        (actor,key(request_key),'accepted',digest,job['id'],encoded(job),job['created_at']))
    store.audit(db,actor,'backup.request.accepted',{'_backup_request':{'actor':actor,'key':request_key}})


def record_failure(store,job):
    if not job.get('request_key'):return
    # Caller has proved submit failed or its exact Future ended. Do not acquire
    # Store.lock while the caller holds Backups.lock (opposite worker order).
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,value=read_row(db,job['actor'],job['request_key'])
        if row is None or value is None or not same_job(value,job):raise DomainError('Failure observation has no matching backup acceptance.',409)
        if row['failure_observation'] is not None:return
        db.execute('UPDATE backup_requests SET failure_observation=? WHERE actor=? AND request_key=?',
            (encoded(job),job['actor'],job['request_key']))
        store.audit(db,job['actor'],'backup.request.failed',{'_backup_request':{'actor':job['actor'],'key':job['request_key']}})


def get(store,actor,request_key):
    with store.db() as db:
        db.execute('BEGIN');row,result=read_row(db,actor,request_key)
    if row is None:raise DomainError('No backup acceptance/fence yet; an in-flight request may still be accepted.',404)
    return {'contract_version':1,'request_key':row['request_key'],'state':row['state'],'request_sha256':row['request_sha256'],
        'job_id':row['job_id'],'created_at':row['created_at'],'result':result,
        'failure_observation':json.loads(row['failure_observation']) if row['failure_observation'] else None,
        'worker_completion_verified':False,'job_files_verified':False,'automatic_restart':False,'automatic_activation':False}


def cancel(store,actor,request_key,busy=lambda:False):
    from .store import now
    request_key=key(request_key)
    with store.db() as db:
        db.execute('BEGIN');row,result=read_row(db,actor,request_key)
    if row is not None:return get(store,actor,request_key)  # Accepted worker NEVER stopped.
    if busy():raise DomainError('Backup metadata writes are paused; inspect receipts now and fence only after its writer ends.',409)
    with store.lock:
        if busy():raise DomainError('Backup metadata writes are paused; fence must wait.',409)
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
            if row is None:
                db.execute('INSERT INTO backup_requests(actor,request_key,state,created_at) VALUES(?,?,?,?)',
                    (actor,request_key,'cancelled',now()))
                store.audit(db,actor,'backup.request.cancelled',{'_backup_request':{'actor':actor,'key':request_key}})
    return get(store,actor,request_key)
