"""Durable approval/local-activation intents; receipts are historical, not live state."""
import json
import os

from .auditstate import encoded,fingerprint,row_binding
from .asset_requests import backup_active
from .safety import DomainError
from .write_requests import key

SCHEMA='''CREATE TABLE IF NOT EXISTS release_requests (
 actor TEXT NOT NULL, request_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','committed','failed','cancelled')),
 action TEXT NOT NULL, release_id TEXT, request_payload TEXT, request_sha256 TEXT,
 response TEXT, response_sha256 TEXT, audit_sequence INTEGER,
 owner_pid INTEGER, owner_stamp TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 writer_finished_at TEXT, error TEXT, PRIMARY KEY(actor,request_key))'''
ACTIONS={'review':'release.approved','activate':'release.activated'}


def request_hash(action,release_id,payload):return fingerprint([1,action,release_id,payload])


def response_value(row,audit=None):
    try:
        if not row['actor'] or key(row['request_key'])!=row['request_key']:raise ValueError()
        if row['state']=='cancelled' and row['release_id'] is None:
            if row['action']!='cancelled' or any(row[name] is not None for name in ('request_payload','request_sha256','response','response_sha256','audit_sequence','owner_pid','owner_stamp')):raise ValueError()
            return None
        if row['action'] not in ACTIONS or key(row['release_id'])!=row['release_id'] or type(row['owner_pid']) is not int or row['owner_pid']<=0:raise ValueError()
        payload=json.loads(row['request_payload'])
        if not isinstance(payload,dict) or row['request_sha256']!=request_hash(row['action'],row['release_id'],payload):raise ValueError()
        if row['state']=='committed':
            result=json.loads(row['response'])
            if not isinstance(result,dict) or row['response_sha256']!=fingerprint(result) or not row['writer_finished_at']:raise ValueError()
            if row['action']=='review':
                if result['id']!=row['release_id'] or result['status']!='approved' or result['review']['contract_version']!=2:raise ValueError()
            else:
                if result['release_id']!=row['release_id'] or type(result['generation']) is not int or result['generation']!=payload['expected_generation']+1 or result['usable'] is not True:raise ValueError()
            if audit is None or audit['sequence']!=row['audit_sequence'] or audit['actor']!=row['actor'] or audit['action']!=ACTIONS[row['action']]:raise ValueError()
            if audit['hash']!=fingerprint([audit['previous_hash'],audit['actor'],audit['action'],audit['payload'],audit['created_at']]):raise ValueError()
            event=json.loads(audit['payload']);binding=event['_release_request']
            if binding!={'actor':row['actor'],'key':row['request_key'],'response_sha256':row['response_sha256']}:raise ValueError()
            if row_binding('release_requests',row) not in event['_state']['bindings']:raise ValueError()
            return result
        if row['state'] not in ('reserved','failed','cancelled') or any(row[name] is not None for name in ('response','response_sha256','audit_sequence')):raise ValueError()
        return None
    except (ValueError,TypeError,KeyError,AttributeError,DomainError) as error:
        raise DomainError('Release transition receipt differs from its immutable response/audit.',409) from error


def read_row(db,actor,request_key):
    row=db.execute('SELECT * FROM release_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    audit=db.execute('SELECT * FROM audit WHERE sequence=?',(row['audit_sequence'],)).fetchone() if row and row['audit_sequence'] else None
    return row,response_value(row,audit) if row else None


def reserve(store,actor,request_key,action,release_id,payload):
    from .store import now
    from .upgrades import process_stamp
    request_key=key(request_key);key(release_id)
    if action not in ACTIONS or not isinstance(payload,dict):raise DomainError('Invalid release transition intent.')
    digest=request_hash(action,release_id,payload)
    if backup_active(store):raise DomainError('Backup/recovery writer is active; release transition must wait.',409)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
        if row:
            if row['release_id'] is not None and (row['action']!=action or row['request_sha256']!=digest):raise DomainError('Release Idempotency-Key belongs to different action/target/evidence/generation.',409)
            if row['state']=='committed':return result
            raise DomainError('Release transition already has an unfinished/failed/fenced intent; inspect its receipt, never rerun automatically.',409)
        if backup_active(store):raise DomainError('Backup/recovery writer is active; release transition must wait.',409)
        stamp=now()
        db.execute('INSERT INTO release_requests(actor,request_key,state,action,release_id,request_payload,request_sha256,owner_pid,owner_stamp,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (actor,request_key,'reserved',action,release_id,encoded(payload),digest,os.getpid(),process_stamp(os.getpid()),stamp,stamp))
        store.audit(db,actor,'release.request.reserved',{'_release_request':{'actor':actor,'key':request_key},'action':action,'release_id':release_id})
    return None


def gate(db,actor,request_key,action,release_id,payload):
    if request_key is None:return
    row,_=read_row(db,actor,request_key)
    if row is None or row['state']!='reserved' or row['release_id']!=release_id or row['action']!=action or row['request_sha256']!=request_hash(action,release_id,payload):
        raise DomainError('Final release transition was fenced or differs from its intent. Technical diagnostics may already exist; no approval/switch committed.',409)


def commit(db,actor,request_key,action,release_id,payload,result,stamp):
    if request_key is None:return {}
    gate(db,actor,request_key,action,release_id,payload)
    sequence=db.execute("SELECT seq FROM sqlite_sequence WHERE name='audit'").fetchone()
    sequence=(sequence[0] if sequence else 0)+1
    digest=fingerprint(result)
    db.execute('UPDATE release_requests SET state=?,response=?,response_sha256=?,audit_sequence=?,updated_at=?,writer_finished_at=?,error=NULL WHERE actor=? AND request_key=?',
        ('committed',encoded(result),digest,sequence,stamp,stamp,actor,request_key))
    return {'_release_request':{'actor':actor,'key':request_key,'response_sha256':digest}}


def fail(store,actor,request_key):
    from .store import now
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,_=read_row(db,actor,request_key)
        if row and row['state'] in ('reserved','cancelled') and row['release_id'] and not row['writer_finished_at']:
            stamp=now();db.execute('UPDATE release_requests SET state=?,updated_at=?,writer_finished_at=?,error=? WHERE actor=? AND request_key=?',
                ('failed' if row['state']=='reserved' else 'cancelled',stamp,stamp,'Release transition ended without final approval/switch; technical diagnostics may already be committed.',actor,request_key))
            store.audit(db,actor,'release.request.failed',{'_release_request':{'actor':actor,'key':request_key}})


def get(store,actor,request_key):
    with store.db() as db:db.execute('BEGIN');row,result=read_row(db,actor,request_key)
    if row is None:raise DomainError('No release transition receipt yet; an in-flight request may still reserve/commit.',404)
    return {'contract_version':1,'request_key':row['request_key'],'state':row['state'],'action':row['action'],'release_id':row['release_id'],
        'request_sha256':row['request_sha256'],'result':result,'response_sha256':row['response_sha256'],'audit_sequence':row['audit_sequence'],
        'created_at':row['created_at'],'updated_at':row['updated_at'],'writer_finished_at':row['writer_finished_at'],'error':row['error'],
        'current_state':False,'files_verified':False,'automatic_retry':False,'automatic_activation':False}


def cancel(store,actor,request_key):
    from .store import now
    request_key=key(request_key)
    # SQLite serializes the final commit/fence. Do not wait for the Store's
    # diagnostic lock: a long-running admitted validation must still be fenced.
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,_=read_row(db,actor,request_key);stamp=now()
        if row is None:db.execute('INSERT INTO release_requests(actor,request_key,state,action,created_at,updated_at) VALUES(?,?,?,?,?,?)',(actor,request_key,'cancelled','cancelled',stamp,stamp))
        elif row['state'] in ('committed','cancelled'):return get(store,actor,request_key)
        else:db.execute('UPDATE release_requests SET state=?,updated_at=? WHERE actor=? AND request_key=?',('cancelled',stamp,actor,request_key))
        store.audit(db,actor,'release.request.cancelled',{'_release_request':{'actor':actor,'key':request_key}})
    return get(store,actor,request_key)
