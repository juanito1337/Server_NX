"""Read-only revision navigation and exact, positional JSON comparisons."""
import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager

from .safety import DomainError,offline_path
from .write_requests import key

MAX_PAYLOAD=32*1024*1024
MAX_NODES=1000000
MAX_DEPTH=64
PREVIEW=1200
MAX_INTEGER=2**63-1
AREAS={'identity':{'kind','name'},'cam':{'cam'},'erp':{'erp'},'approval':{'cam_status'},
    'geometry':{'geometry'},'attachments':{'asset_ids','file_bindings'},'model':{'model_package'},
    'package':{'file_package'},'origin':{'origin'}}


def sha(raw):return hashlib.sha256(raw.encode('utf-8')).hexdigest()
def digest(value):return sha(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')))


@contextmanager
def _db(store):
    # No Store constructor or writable/journal-mode-changing connection.
    path=offline_path(store.root/'management.sqlite3')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)
    db.row_factory=sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN');yield db
    finally:db.close()


def _page(offset,limit,maximum):
    if type(offset) is not int or not 0<=offset<=MAX_INTEGER or type(limit) is not int or not 1<=limit<=maximum:
        raise DomainError('Invalid bounded history page.')


def _decode(raw):
    def invalid(value):raise ValueError('Non-finite JSON value')
    def unique(pairs):
        result={}
        for name,value in pairs:
            if name in result:raise ValueError('Duplicate JSON field')
            result[name]=value
        return result
    try:
        value=json.loads(raw,parse_constant=invalid,object_pairs_hook=unique)
        if not isinstance(value,dict):raise ValueError()
    except (ValueError,TypeError,RecursionError) as error:
        raise DomainError('Selected revision is not a supported JSON object; original history retained.',409) from error
    # Validate even equal or whole-added subtrees, not only rendered differences.
    stack=[(value,0)];nodes=0
    while stack:
        part,depth=stack.pop();nodes+=1
        if nodes>MAX_NODES or depth>MAX_DEPTH:
            raise DomainError('Revision comparison exceeds its bounded depth/node reader; no incomplete comparison is reported.',409)
        if type(part) is float and not math.isfinite(part):
            raise DomainError('Non-finite number in selected revision.',409)
        if isinstance(part,(dict,list)):
            values=part.values() if isinstance(part,dict) else part
            if nodes+len(stack)+len(part)>MAX_NODES:
                raise DomainError('Revision comparison exceeds its bounded depth/node reader; no incomplete comparison is reported.',409)
            stack.extend((child,depth+1) for child in values)
    return value


def _head(db,resource_id,expected=None):
    if expected is not None and (type(expected) is not int or not 1<=expected<=MAX_INTEGER):raise DomainError('Invalid expected history revision.')
    size=db.execute('SELECT length(CAST(payload AS BLOB)) FROM resources WHERE id=?',(key(resource_id),)).fetchone()
    if size and (size[0] is None or size[0]>MAX_PAYLOAD):raise DomainError('Current revision exceeds the bounded history reader.',409)
    row=db.execute('SELECT * FROM resources WHERE id=?',(key(resource_id),)).fetchone()
    if not row:raise DomainError('Resource not found.',404)
    if type(row['revision']) is not int or row['revision']<1:raise DomainError('Current resource revision is invalid.',409)
    if expected is not None and row['revision']!=expected:raise DomainError('Resource history head changed; reload history before comparing.',409)
    count,first,last=db.execute('SELECT COUNT(*),MIN(revision),MAX(revision) FROM resource_versions WHERE id=?',(resource_id,)).fetchone()
    latest=db.execute('SELECT payload,created_at FROM resource_versions WHERE id=? AND revision=?',(resource_id,row['revision'])).fetchone()
    if count!=row['revision'] or first!=1 or last!=row['revision'] or not latest or latest['payload']!=row['payload'] or latest['created_at']!=row['updated_at']:
        raise DomainError('History is incomplete or differs from its current immutable head.',409)
    return row


def _version(db,resource_id,revision):
    size=db.execute('SELECT length(CAST(payload AS BLOB)) FROM resource_versions WHERE id=? AND revision=?',(resource_id,revision)).fetchone()
    if size and (size[0] is None or size[0]>MAX_PAYLOAD):raise DomainError('Selected revision exceeds the bounded history reader.',409)
    row=db.execute('SELECT * FROM resource_versions WHERE id=? AND revision=?',(resource_id,revision)).fetchone()
    if not row:raise DomainError('Selected revision not found.',404)
    raw=row['payload']
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>MAX_PAYLOAD:raise DomainError('Selected revision exceeds the bounded history reader.',409)
    return dict(row)


def summary(store,resource_id,offset=0,limit=20,expected=None):
    _page(offset,limit,50)
    with _db(store) as db:
        head=_head(db,resource_id,expected)
        rows=[]
        for revision, in db.execute('SELECT revision FROM resource_versions WHERE id=? ORDER BY revision DESC LIMIT ? OFFSET ?',(resource_id,limit,offset)):
            value=_version(db,resource_id,revision)
            rows.append({name:value[name] for name in ('revision','actor','created_at')}|{'payload_sha256':sha(value['payload']),'payload_bytes':len(value['payload'].encode('utf-8'))})
    return {'contract_version':1,'resource_id':resource_id,'current_revision':head['revision'],'current_payload_sha256':sha(head['payload']),
        'total':head['revision'],'offset':offset,'limit':limit,'items':rows,'sqlite_read_transaction':True,'sqlite_read_only':True,
        'audit_verified':False,'files_verified':False,'automatic_restore':False,'nx_files_written':False}


def _preview(present,value):
    if not present:return {'present':False,'type':'missing','text':'','truncated':False,'sha256':None}
    types={dict:'object',list:'array',str:'string',bool:'boolean',int:'number',float:'number',type(None):'null'}
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    return {'present':True,'type':types[type(value)],'text':raw[:PREVIEW],'truncated':len(raw)>PREVIEW,'sha256':sha(raw)}


def _area(path):return next((name for name,fields in AREAS.items() if path and path[0] in fields),'other')
def _pointer(path):return ''.join('/'+str(part).replace('~','~0').replace('/','~1') for part in path)


def compare(store,resource_id,before,after,before_sha,after_sha,expected=None,offset=0,limit=50,area='',query='',expected_comparison=None):
    _page(offset,limit,100)
    if any(type(value) is not int or not 1<=value<=MAX_INTEGER for value in (before,after)):raise DomainError('Select bounded positive integer revisions.')
    if not isinstance(query,str) or len(query)>200:raise DomainError('History search is limited to 200 characters.')
    if area and area not in AREAS and area!='other':raise DomainError('Unknown revision comparison area.')
    with _db(store) as db:
        head=_head(db,resource_id,expected);old=_version(db,resource_id,before);new=_version(db,resource_id,after)
    if sha(old['payload'])!=before_sha or sha(new['payload'])!=after_sha:raise DomainError('Selected revision bytes changed; reload history before comparing.',409)
    left=_decode(old['payload']);right=_decode(new['payload'])
    binding=digest([1,resource_id,head['revision'],sha(head['payload']),before,before_sha,after,after_sha])
    if expected_comparison is not None and expected_comparison!=binding:raise DomainError('Comparison context changed; compare selected revisions again.',409)
    stack=[((),True,left,True,right)];nodes=0;total=filtered=0;items=[];counts={name:0 for name in (*AREAS,'other')};needle=query.casefold()
    while stack:
        path,a_present,a,b_present,b=stack.pop();nodes+=1
        if nodes>MAX_NODES or len(path)>MAX_DEPTH:raise DomainError('Revision comparison exceeds its bounded depth/node reader; no incomplete comparison is reported.',409)
        if a_present and b_present and type(a) is dict and type(b) is dict:
            for name in reversed(sorted(set(a)|set(b))):stack.append((path+(name,),name in a,a.get(name),name in b,b.get(name)))
            continue
        if a_present and b_present and type(a) is list and type(b) is list:
            for index in range(max(len(a),len(b))-1,-1,-1):stack.append((path+(index,),index<len(a),a[index] if index<len(a) else None,index<len(b),b[index] if index<len(b) else None))
            continue
        if a_present and b_present and type(a) is type(b) and a==b:continue
        category=_area(path);pointer=_pointer(path);total+=1;counts[category]+=1
        if area and area!=category:continue
        # Search exact pointer and full selected value JSON, not truncated UI text.
        try:
            old_value=_preview(a_present,a);new_value=_preview(b_present,b)
            matches=not needle or needle in pointer.casefold() or (a_present and needle in json.dumps(a,ensure_ascii=False,sort_keys=True,allow_nan=False).casefold()) or (b_present and needle in json.dumps(b,ensure_ascii=False,sort_keys=True,allow_nan=False).casefold())
        except (ValueError,TypeError,RecursionError) as error:raise DomainError('Unsupported value in selected revision; no partial comparison.',409) from error
        if not matches:continue
        if offset<=filtered<offset+limit:items.append({'path':pointer,'tokens':list(path),'area':category,'change':'added' if not a_present else 'removed' if not b_present else 'changed','before':old_value,'after':new_value})
        filtered+=1
    return {'contract_version':1,'resource_id':resource_id,'current_revision':head['revision'],'before_revision':before,'after_revision':after,
        'before_sha256':before_sha,'after_sha256':after_sha,'comparison_sha256':binding,'total':total,'filtered':filtered,'counts':counts,
        'offset':offset,'limit':limit,'area':area,'query':query,'items':items,'complete':True,'arrays_positional':True,
        'sqlite_read_transaction':True,'sqlite_read_only':True,'audit_verified':False,'files_verified':False,'automatic_restore':False,'nx_files_written':False}
