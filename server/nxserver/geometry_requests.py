"""Durable STEP-package intents/receipts, never an NX execution or approval."""
import hashlib
import json
import os

from .auditstate import encoded,fingerprint
from .safety import DomainError,asset_filename
from .write_requests import key

SCHEMA='''CREATE TABLE IF NOT EXISTS geometry_requests (
    actor TEXT NOT NULL, request_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('reserved','committed','failed','cancelled')),
    request_sha256 TEXT, job_id TEXT, job_snapshot TEXT, snapshot_sha256 TEXT,
    owner_pid INTEGER, owner_stamp TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, writer_finished_at TEXT, error TEXT,
    PRIMARY KEY(actor,request_key))'''


def request_hash(resource_id,revision,asset_id,tool_type):
    return fingerprint([1,'geometry.job.create',resource_id,revision,asset_id,tool_type])


def response_value(row,job=None,version=None,asset=None):
    try:
        if not row['actor'] or key(row['request_key'])!=row['request_key']:raise ValueError()
        if row['state']=='cancelled' and row['job_id'] is None:
            if any(row[name] is not None for name in ('request_sha256','job_snapshot','snapshot_sha256','owner_pid','owner_stamp','writer_finished_at')):raise ValueError()
            return None
        value=json.loads(row['job_snapshot'])
        if not isinstance(value,dict) or fingerprint(value)!=row['snapshot_sha256']:raise ValueError()
        if key(row['job_id'])!=value['id'] or key(value['resource_id'])!=value['resource_id']:raise ValueError()
        if value['contract_version']!=1 or value['status']!='awaiting_nx' or value['nx_version']!='2512':raise ValueError()
        if type(value['resource_revision']) is not int or value['resource_revision']<1:raise ValueError()
        source=value['input'];key(source['id']);asset_filename(source['filename'])
        if type(source['size']) is not int or not 1<=source['size']<=20*1024*1024:raise ValueError()
        if type(value['package_size']) is not int or value['package_size']<=0:raise ValueError()
        for digest in (value['resource_hash'],value['package_sha256'],source['sha256'],*value['worker_hashes'].values()):
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError()
        if row['request_sha256']!=request_hash(value['resource_id'],value['resource_revision'],source['id'],value['tool_type']):raise ValueError()
        if type(row['owner_pid']) is not int or row['owner_pid']<=0 or row['created_at']!=value['created_at']:raise ValueError()
        if version is None or version['id']!=value['resource_id'] or version['revision']!=value['resource_revision']:raise ValueError()
        # resource_hash deliberately binds the exact original stored JSON bytes.
        if hashlib.sha256(version['payload'].encode()).hexdigest()!=value['resource_hash']:raise ValueError()
        resource=json.loads(version['payload'])
        if resource['name']!=value['resource_name'] or resource.get('cam',{})!=value['cam_snapshot'] or resource['cam_status']!='draft':raise ValueError()
        if resource['kind'] not in ('tool','tool_assembly') or source['id'] not in resource.get('asset_ids',[]):raise ValueError()
        if asset is None or any(asset[name]!=source[name] for name in ('id','filename','sha256','size','created_at')):raise ValueError()
        if row['state']=='committed':
            # Collector snapshots are a separately audited additive field.
            # They must not invalidate or roll back the original STEP receipt.
            current=json.loads(job['payload']) if job is not None else None
            original={name:item for name,item in current.items() if name!='collector_packages'} if isinstance(current,dict) else None
            if not row['writer_finished_at'] or job is None or job['id']!=value['id'] or job['resource_id']!=value['resource_id'] or encoded(original)!=encoded(value):raise ValueError()
            return value
        if row['state'] not in ('reserved','failed','cancelled') or job is not None:raise ValueError()
        if row['state']=='reserved' and row['writer_finished_at'] is not None:raise ValueError()
        if row['state']=='failed' and not row['writer_finished_at']:raise ValueError()
        return None
    except (ValueError,TypeError,KeyError,AttributeError,DomainError) as error:
        raise DomainError('STEP request allocation/receipt differs from its immutable job, resource revision or input metadata.',409) from error


def read_row(db,actor,request_key):
    row=db.execute('SELECT * FROM geometry_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    if row is None:return None,None
    job=version=asset=None
    if row['job_id']:
        try:
            value=json.loads(row['job_snapshot'])
            job=db.execute('SELECT * FROM geometry_jobs WHERE id=?',(row['job_id'],)).fetchone()
            version=db.execute('SELECT * FROM resource_versions WHERE id=? AND revision=?',(value['resource_id'],value['resource_revision'])).fetchone()
            asset=db.execute('SELECT * FROM assets WHERE id=?',(value['input']['id'],)).fetchone()
        except (ValueError,KeyError,TypeError) as error:raise DomainError('Invalid STEP request snapshot.',409) from error
    return row,response_value(row,job,version,asset)


def replay(db,actor,request_key,digest):
    if request_key is None:return None
    row,result=read_row(db,actor,request_key)
    if row is None:return None
    if row['state']=='cancelled':raise DomainError('STEP request commit was fenced; no package writer may restart under this key.',409)
    if row['request_sha256']!=digest:raise DomainError('STEP Idempotency-Key belongs to a different revision, attachment or tool type.',409)
    if row['state']!='committed':raise DomainError('STEP request has a partial allocation; inspect/fence it, never rewrite its package automatically.',409)
    return result


def reserve(store,db,actor,request_key,digest,job):
    if request_key is None:return
    from .upgrades import process_stamp
    db.execute('INSERT INTO geometry_requests(actor,request_key,state,request_sha256,job_id,job_snapshot,snapshot_sha256,owner_pid,owner_stamp,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (actor,key(request_key),'reserved',digest,job['id'],encoded(job),fingerprint(job),os.getpid(),process_stamp(os.getpid()),job['created_at'],job['created_at']))
    store.audit(db,actor,'geometry.request.reserved',{'_geometry_request':{'actor':actor,'key':request_key}})


def commit(db,actor,request_key,job,stamp):
    if request_key is None:return {}
    row=db.execute('SELECT * FROM geometry_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    if (row is None or row['state']!='reserved' or row['job_id']!=job['id'] or row['snapshot_sha256']!=fingerprint(job)
            or row['request_sha256']!=request_hash(job['resource_id'],job['resource_revision'],job['input']['id'],job['tool_type'])
            or row['job_snapshot']!=encoded(job)):
        raise DomainError('STEP package commit was fenced or its allocation changed; partial evidence retained.',409)
    db.execute('UPDATE geometry_requests SET state=?,updated_at=?,writer_finished_at=?,error=NULL WHERE actor=? AND request_key=?',
        ('committed',stamp,stamp,actor,request_key))
    return {'_geometry_request':{'actor':actor,'key':request_key}}


def fail(store,actor,request_key):
    if request_key is None:return
    from .store import now
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
        if row and row['state'] in ('reserved','cancelled') and row['job_id'] and not row['writer_finished_at']:
            stamp=now()
            db.execute('UPDATE geometry_requests SET state=?,updated_at=?,writer_finished_at=?,error=? WHERE actor=? AND request_key=?',
                ('failed' if row['state']=='reserved' else 'cancelled',stamp,stamp,'STEP package writer ended without a job commit; partial evidence retained.',actor,request_key))
            store.audit(db,actor,'geometry.request.failed',{'_geometry_request':{'actor':actor,'key':request_key}})


def get(store,actor,request_key):
    with store.db() as db:
        db.execute('BEGIN');row,result=read_row(db,actor,request_key)
    if row is None:raise DomainError('No STEP request receipt yet; an in-flight request may still reserve/commit.',404)
    value=json.loads(row['job_snapshot']) if row['job_id'] else None
    allocation={'job_id':row['job_id'],'package_sha256':value['package_sha256'],'package_size':value['package_size'],
        'owner_pid':row['owner_pid'],'owner_stamp':row['owner_stamp']} if value else None
    return {'contract_version':1,'request_key':row['request_key'],'state':row['state'],'request_sha256':row['request_sha256'],
        'allocation':allocation,'job_id':row['job_id'],'result':result,'created_at':row['created_at'],'updated_at':row['updated_at'],
        'writer_finished_at':row['writer_finished_at'],'error':row['error'],'package_files_verified':False,
        'nx_execution_verified':False,'automatic_restart':False,'automatic_approval':False}


def cancel(store,actor,request_key):
    from .store import now
    request_key=key(request_key)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key);stamp=now()
        if row is None:
            db.execute('INSERT INTO geometry_requests(actor,request_key,state,created_at,updated_at) VALUES(?,?,?,?,?)',
                (actor,request_key,'cancelled',stamp,stamp))
        elif row['state'] in ('committed','cancelled'):return get(store,actor,request_key)
        else:db.execute('UPDATE geometry_requests SET state=?,updated_at=? WHERE actor=? AND request_key=?',('cancelled',stamp,actor,request_key))
        store.audit(db,actor,'geometry.request.cancelled',{'_geometry_request':{'actor':actor,'key':request_key}})
    return get(store,actor,request_key)
