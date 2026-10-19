"""Upload allocation/commit receipts. Never replay a partial physical write."""
import json
import os

from .auditstate import encoded,fingerprint
from .safety import DomainError,asset_filename
from .write_requests import key

SCHEMA='''CREATE TABLE IF NOT EXISTS asset_requests (
    actor TEXT NOT NULL, request_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('reserved','committed','failed','cancelled')),
    request_sha256 TEXT, asset_id TEXT, filename TEXT, sha256 TEXT, size INTEGER,
    response TEXT, owner_pid INTEGER, owner_stamp TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, writer_finished_at TEXT, error TEXT, PRIMARY KEY(actor,request_key))'''


def request_hash(asset):return fingerprint([1,'asset.create',asset['filename'],asset['size'],asset['sha256']])


def response_value(row,asset=None):
    try:
        if not row['actor'] or not row['request_key'] or key(row['request_key'])!=row['request_key']:raise ValueError()
        if row['state']=='cancelled' and row['asset_id'] is None:
            if any(row[name] is not None for name in ('request_sha256','filename','sha256','size','response','owner_pid','owner_stamp')):raise ValueError()
            return None
        if not row['asset_id'] or key(row['asset_id'])!=row['asset_id']:raise ValueError()
        asset_filename(row['filename'])
        if type(row['size']) is not int or not 1<=row['size']<=20*1024*1024:raise ValueError()
        if not isinstance(row['sha256'],str) or len(row['sha256'])!=64 or any(c not in '0123456789abcdef' for c in row['sha256']):raise ValueError()
        result={'id':row['asset_id'],'filename':row['filename'],'sha256':row['sha256'],'size':row['size']}
        if row['request_sha256']!=request_hash(result) or type(row['owner_pid']) is not int or row['owner_pid']<=0:raise ValueError()
        if row['state']=='committed':
            if not row['writer_finished_at'] or asset is None or any(asset[name]!=result[name] for name in result) or encoded(json.loads(row['response']))!=encoded(result):raise ValueError()
            return result
        if row['state'] not in ('reserved','failed','cancelled') or row['response'] is not None or asset is not None:raise ValueError()
        return None
    except (ValueError,TypeError,AttributeError,KeyError,DomainError) as error:
        raise DomainError('Upload receipt/allocation differs from immutable asset metadata.',409) from error


def read_row(db,actor,request_key):
    row=db.execute('SELECT * FROM asset_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    asset=db.execute('SELECT * FROM assets WHERE id=?',(row['asset_id'],)).fetchone() if row and row['asset_id'] else None
    return row,response_value(row,asset) if row else None


def backup_active(store):
    """Fast filesystem gate before a backup's long-held in-process store lock."""
    from .safety import offline_path,tree_files
    jobs=offline_path(store.root/'b/jobs')
    return jobs.exists() and any(json.loads(path.read_text(encoding='utf-8'))['status']=='running' for relative,path in tree_files(jobs) if relative.endswith('/job.json'))


def reserve(store,actor,request_key,asset):
    from .store import now
    from .upgrades import process_stamp
    request_key=key(request_key);digest=request_hash(asset)
    if backup_active(store):raise DomainError('Backup/recovery writer is active; upload allocation must wait.',409)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
        if row:
            if row['request_sha256']!=digest and row['state']!='cancelled':raise DomainError('Upload Idempotency-Key belongs to different filename/bytes.',409)
            if row['state']=='committed':return result
            raise DomainError('Upload has an existing partial/fenced allocation; inspect its receipt, never rewrite that path automatically.',409)
        # Backups pause writes even if HTTP authorization preceded their start.
        if backup_active(store):
            raise DomainError('Backup/recovery writer is active; upload allocation must wait.',409)
        stamp=now()
        db.execute('INSERT INTO asset_requests(actor,request_key,state,request_sha256,asset_id,filename,sha256,size,owner_pid,owner_stamp,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (actor,request_key,'reserved',digest,asset['id'],asset['filename'],asset['sha256'],asset['size'],os.getpid(),process_stamp(os.getpid()),stamp,stamp))
        store.audit(db,actor,'asset.upload.reserved',{'_asset_request':{'actor':actor,'key':request_key},'asset':asset})
    return None


def commit(db,actor,request_key,asset,stamp):
    if request_key is None:return {}
    # The new asset row was just inserted in THIS transaction; validate the
    # allocation before treating the insert as an existing committed asset.
    row=db.execute('SELECT * FROM asset_requests WHERE actor=? AND request_key=?',(actor,key(request_key))).fetchone()
    if row is None or row['state']!='reserved' or row['asset_id']!=asset['id'] or row['request_sha256']!=request_hash(asset):
        raise DomainError('Upload was fenced or differs from its allocation; partial bytes retained.',409)
    db.execute('UPDATE asset_requests SET state=?,response=?,updated_at=?,writer_finished_at=?,error=NULL WHERE actor=? AND request_key=?',
        ('committed',encoded(asset),stamp,stamp,actor,request_key))
    return {'_asset_request':{'actor':actor,'key':request_key}}


def fail(store,actor,request_key):
    from .store import now
    if request_key is None:return
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key)
        if row and row['state'] in ('reserved','cancelled') and row['asset_id'] and not row['writer_finished_at']:
            stamp=now()
            db.execute('UPDATE asset_requests SET state=?,updated_at=?,writer_finished_at=?,error=? WHERE actor=? AND request_key=?',
                ('failed' if row['state']=='reserved' else 'cancelled',stamp,stamp,'Upload writer ended without an asset commit; partial evidence retained.',actor,request_key))
            store.audit(db,actor,'asset.upload.failed',{'_asset_request':{'actor':actor,'key':request_key}})


def get(store,actor,request_key):
    with store.db() as db:
        db.execute('BEGIN');row,result=read_row(db,actor,request_key)
    if row is None:raise DomainError('No upload receipt yet; an in-flight request may still reserve/commit.',404)
    allocation={name:row[name] for name in ('asset_id','filename','sha256','size','owner_pid','owner_stamp')} if row['asset_id'] else None
    return {'contract_version':1,'request_key':row['request_key'],'state':row['state'],'request_sha256':row['request_sha256'],
        'allocation':allocation,'result':result,'error':row['error'],'created_at':row['created_at'],'updated_at':row['updated_at'],
        'writer_finished_at':row['writer_finished_at'],
        'files_verified':False,'automatic_retry':False,'automatic_assignment':False,'automatic_approval':False}


def cancel(store,actor,request_key):
    from .store import now
    request_key=key(request_key)
    with store.lock,store.db() as db:
        db.execute('BEGIN IMMEDIATE');row,result=read_row(db,actor,request_key);stamp=now()
        if row is None:
            db.execute('INSERT INTO asset_requests(actor,request_key,state,created_at,updated_at) VALUES(?,?,?,?,?)',
                (actor,request_key,'cancelled',stamp,stamp))
        elif row['state'] in ('committed','cancelled'):return get(store,actor,request_key)
        else:db.execute('UPDATE asset_requests SET state=?,updated_at=? WHERE actor=? AND request_key=?',('cancelled',stamp,actor,request_key))
        store.audit(db,actor,'asset.upload.cancelled',{'_asset_request':{'actor':actor,'key':request_key}})
    return get(store,actor,request_key)  # Existing asset commit is never undone.
