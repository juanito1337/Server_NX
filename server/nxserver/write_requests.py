"""Atomic metadata-write receipts and cancellation fences. No credentials/artifacts."""
import hashlib
import json
import uuid

from .auditstate import encoded
from .safety import DomainError

SCHEMA='''CREATE TABLE IF NOT EXISTS write_requests (
    actor TEXT NOT NULL, request_key TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('committed','cancelled')),
    action TEXT NOT NULL, request_sha256 TEXT, resource_id TEXT, revision INTEGER, response TEXT,
    created_at TEXT NOT NULL, PRIMARY KEY(actor,request_key))'''


def key(value):
    if value is None:return None
    try:
        if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError()
    except (ValueError,AttributeError):raise DomainError('Idempotency-Key must be a canonical lowercase UUID.')
    return value


def request_hash(action,target,expected,payload):
    return hashlib.sha256(encoded([1,action,target,expected,payload]).encode()).hexdigest()


def response_value(row,version=None):
    """A receipt is byte-bound to an existing immutable resource revision."""
    try:
        key(row['request_key'])
        if not isinstance(row['actor'],str) or not row['actor']:raise ValueError()
        if row['state']=='cancelled':
            if row['action']!='cancelled':raise ValueError()
            if any(row[column] is not None for column in ('request_sha256','resource_id','revision','response')):raise ValueError()
            return None
        result=json.loads(row['response'])
        if row['state']!='committed' or row['action'] not in ('resource.create','resource.update','erp.upsert') or not isinstance(result,dict) or not version:raise ValueError()
        if result['id']!=row['resource_id'] or type(result['revision']) is not int or result['revision']!=row['revision']:raise ValueError()
        payload={name:value for name,value in result.items() if name not in ('id','revision','updated_at','unchanged')}
        if encoded(payload)!=encoded(json.loads(version['payload'])):raise ValueError()
        if result.get('updated_at',version['created_at'])!=version['created_at']:raise ValueError()
        if not isinstance(row['request_sha256'],str) or len(row['request_sha256'])!=64 or any(c not in '0123456789abcdef' for c in row['request_sha256']):raise ValueError()
        return result
    except (ValueError,TypeError,KeyError,AttributeError,DomainError) as error:
        raise DomainError('Stored write receipt is invalid or differs from its immutable revision.',409) from error


def read_row(db,actor,request_key):
    row=db.execute('SELECT * FROM write_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    if row is None:return None,None
    version=db.execute('SELECT payload,created_at FROM resource_versions WHERE id=? AND revision=?',
        (row['resource_id'],row['revision'])).fetchone() if row['state']=='committed' else None
    return row,response_value(row,version)


def replay(db,actor,request_key,action,request_sha256):
    if request_key is None:return None
    row,result=read_row(db,actor,request_key)
    if row is None:return None
    if row['state']=='cancelled':raise DomainError('This write request was cancelled; use a new key only for a new deliberate action.',409)
    if row['action']!=action or row['request_sha256']!=request_sha256:
        raise DomainError('Idempotency-Key was already used for different data/action/expected revision.',409)
    return result


def commit(db,actor,request_key,action,request_sha256,result,created_at):
    if request_key is None:return {}
    db.execute('INSERT INTO write_requests(actor,request_key,state,action,request_sha256,resource_id,revision,response,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
        (actor,key(request_key),'committed',action,request_sha256,result['id'],result['revision'],encoded(result),created_at))
    return {'_write_request':{'actor':actor,'key':request_key}}


def get(store,actor,request_key):
    with store.db() as db:
        db.execute('BEGIN');row,result=read_row(db,actor,key(request_key))
    if row is None:raise DomainError('No committed/cancelled write receipt for this role/key; an in-flight request may still finish.',404)
    return {'contract_version':1,'state':row['state'],'action':row['action'],'request_key':row['request_key'],
        'request_sha256':row['request_sha256'],'resource_id':row['resource_id'],'revision':row['revision'],
        'created_at':row['created_at'],'result':result,'automatic_approval':False}


def cancel(store,actor,request_key):
    from .store import now
    request_key=key(request_key)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
        if row is None:
            stamp=now()
            db.execute('INSERT INTO write_requests(actor,request_key,state,action,created_at) VALUES(?,?,?,?,?)',
                (actor,request_key,'cancelled','cancelled',stamp))
            store.audit(db,actor,'write.request.cancelled',{'_write_request':{'actor':actor,'key':request_key}})
    return get(store,actor,request_key)  # Existing committed data is never undone.
