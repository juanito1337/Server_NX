"""Immutable NX-log upload allocations, commit receipts and commit fences."""
import json
import os
import re
from pathlib import PurePosixPath

from .auditstate import encoded,fingerprint
from .safety import DomainError
from .write_requests import key
from .asset_requests import backup_active

SCHEMA='''CREATE TABLE IF NOT EXISTS log_requests (
 actor TEXT NOT NULL, request_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','committed','failed','cancelled')),
 request_sha256 TEXT, log_id TEXT, filename TEXT, sha256 TEXT, size INTEGER,
 response TEXT, owner_pid INTEGER, owner_stamp TEXT, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, writer_finished_at TEXT, error TEXT, PRIMARY KEY(actor,request_key))'''


def filename(value):
    if not isinstance(value,str) or not re.fullmatch(r'[\w. -]{1,120}',value) or value.endswith(('.', ' ')) or PurePosixPath(value).suffix.lower() not in ('.log','.syslog','.txt'):
        raise DomainError('Select a local .syslog, .log or .txt filename without a path.')
    return value


def request_hash(value):return fingerprint([1,'syslog.upload',value['filename'],value['size'],value['sha256']])


def response_value(row,log=None):
    try:
        if not row['actor'] or key(row['request_key'])!=row['request_key']:raise ValueError()
        if row['state']=='cancelled' and row['log_id'] is None:
            if any(row[name] is not None for name in ('request_sha256','filename','sha256','size','response','owner_pid','owner_stamp')):raise ValueError()
            return None
        if not isinstance(row['log_id'],str) or not row['log_id'].startswith('uploaded-') or key(row['log_id'][9:])!=row['log_id'][9:]:raise ValueError()
        filename(row['filename'])
        if type(row['size']) is not int or not 1<=row['size']<=20*1024*1024 or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise ValueError()
        value={'id':row['log_id'],'filename':row['filename'],'sha256':row['sha256'],'size':row['size']}
        if row['request_sha256']!=request_hash(value) or type(row['owner_pid']) is not int or row['owner_pid']<=0:raise ValueError()
        if row['state']=='committed':
            if log is None or not row['writer_finished_at'] or any(log[name]!=value[name] for name in value):raise ValueError()
            value.update(created_at=log['created_at'],source_type='uploaded')
            if encoded(json.loads(row['response']))!=encoded(value):raise ValueError()
            return value
        if row['state'] not in ('reserved','failed','cancelled') or row['response'] is not None or log is not None:raise ValueError()
        return None
    except (ValueError,TypeError,KeyError,AttributeError,DomainError) as error:
        raise DomainError('System-log receipt/allocation differs from immutable log metadata.',409) from error


def read_row(db,actor,request_key):
    row=db.execute('SELECT * FROM log_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    log=db.execute('SELECT * FROM syslogs WHERE id=?',(row['log_id'],)).fetchone() if row and row['log_id'] else None
    return row,response_value(row,log) if row else None


def reserve(store,actor,request_key,value):
    from .store import now
    from .upgrades import process_stamp
    request_key=key(request_key);digest=request_hash(value)
    if backup_active(store):raise DomainError('Backup/recovery writer is active; system-log allocation must wait.',409)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
        if row:
            if row['state']!='cancelled' and row['request_sha256']!=digest:raise DomainError('System-log Idempotency-Key belongs to different filename/bytes.',409)
            if row['state']=='committed':return result
            raise DomainError('System-log upload has a partial/fenced allocation; inspect the receipt, never rewrite that path.',409)
        if backup_active(store):raise DomainError('Backup/recovery writer is active; system-log allocation must wait.',409)
        stamp=now()
        db.execute('INSERT INTO log_requests(actor,request_key,state,request_sha256,log_id,filename,sha256,size,owner_pid,owner_stamp,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (actor,request_key,'reserved',digest,value['id'],value['filename'],value['sha256'],value['size'],os.getpid(),process_stamp(os.getpid()),stamp,stamp))
        store.audit(db,actor,'syslog.upload.reserved',{'_log_request':{'actor':actor,'key':request_key},'log':value})
    return None


def commit(db,actor,request_key,value,stamp):
    if request_key is None:return {}
    row=db.execute('SELECT * FROM log_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    if row is None or row['state']!='reserved' or row['log_id']!=value['id'] or row['request_sha256']!=request_hash(value):raise DomainError('System-log upload was fenced or differs from its allocation; partial evidence retained.',409)
    db.execute('UPDATE log_requests SET state=?,response=?,updated_at=?,writer_finished_at=?,error=NULL WHERE actor=? AND request_key=?',('committed',encoded(value),stamp,stamp,actor,request_key))
    return {'_log_request':{'actor':actor,'key':request_key}}


def fail(store,actor,request_key):
    from .store import now
    if request_key is None:return
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,_=read_row(db,actor,request_key)
        if row and row['state'] in ('reserved','cancelled') and row['log_id'] and not row['writer_finished_at']:
            stamp=now();db.execute('UPDATE log_requests SET state=?,updated_at=?,writer_finished_at=?,error=? WHERE actor=? AND request_key=?',
                ('failed' if row['state']=='reserved' else 'cancelled',stamp,stamp,'System-log writer ended without a metadata commit; partial evidence retained.',actor,request_key))
            store.audit(db,actor,'syslog.upload.failed',{'_log_request':{'actor':actor,'key':request_key}})


def get(store,actor,request_key):
    with store.db() as db:db.execute('BEGIN');row,result=read_row(db,actor,request_key)
    if row is None:raise DomainError('No system-log receipt yet; an in-flight request may still reserve/commit.',404)
    allocation={name:row[name] for name in ('log_id','filename','sha256','size','owner_pid','owner_stamp')} if row['log_id'] else None
    return {'contract_version':1,'request_key':row['request_key'],'state':row['state'],'request_sha256':row['request_sha256'],'allocation':allocation,'result':result,
        'error':row['error'],'created_at':row['created_at'],'updated_at':row['updated_at'],'writer_finished_at':row['writer_finished_at'],
        'files_verified':False,'automatic_retry':False,'automatic_assignment':False,'automatic_approval':False}


def cancel(store,actor,request_key):
    from .store import now
    request_key=key(request_key)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,_=read_row(db,actor,request_key);stamp=now()
        if row is None:db.execute('INSERT INTO log_requests(actor,request_key,state,created_at,updated_at) VALUES(?,?,?,?,?)',(actor,request_key,'cancelled',stamp,stamp))
        elif row['state'] in ('committed','cancelled'):return get(store,actor,request_key)
        else:db.execute('UPDATE log_requests SET state=?,updated_at=? WHERE actor=? AND request_key=?',('cancelled',stamp,actor,request_key))
        store.audit(db,actor,'syslog.upload.cancelled',{'_log_request':{'actor':actor,'key':request_key}})
    return get(store,actor,request_key)
