"""Current, read-only ERP bindings. No matching, consolidation or ERP network calls."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager

from .auditstate import encoded
from .libraries import KINDS
from .safety import DomainError,offline_path
from .write_requests import key

MAX_ROWS=50000
MAX_PAYLOAD=32*1024*1024
MAX_READ=512*1024*1024
FIELDS={'company_id','system_id','number','description','unit','source_revision'}
STATES=('linked','erp_draft','cam_unassigned','draft_unassigned','company_standard','retired','conflict')


def validate_fields(value):
    """The existing ERP field contract, shared by readers and direct writers."""
    from .api import ERPLink
    try:
        if not isinstance(value,dict) or set(value)!=FIELDS:raise ValueError()
        result=ERPLink.model_validate(value).model_dump()
        for item in result.values():item.encode('utf-8')
        return result
    except (TypeError,ValueError,UnicodeError) as error:
        raise DomainError('ERP fields do not match the supported commercial metadata contract.') from error


@contextmanager
def _db(store):
    path=offline_path(store.root/'management.sqlite3')
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30);db.row_factory=sqlite3.Row
    try:db.execute('PRAGMA query_only=ON');db.execute('BEGIN');yield db
    finally:db.close()


def identity(value):
    if not isinstance(value,dict):return None
    pair=[value.get(name) for name in ('company_id','system_id')]
    try:
        if all(isinstance(item,str) and 1<=len(item)<=100 for item in pair):
            for item in pair:item.encode('utf-8')
            return tuple(pair)
    except UnicodeError:pass
    return None


def index_identity(raw):
    if raw is None:return None
    try:
        value=json.loads(raw)
        if not isinstance(value,list) or len(value)!=2 or not all(isinstance(item,str) and 1<=len(item)<=100 for item in value) or encoded(value)!=raw:return None
        return tuple(value)
    except (TypeError,ValueError):return None


def _text(value,maximum):return value[:maximum].encode('utf-8','replace').decode('utf-8') if isinstance(value,str) else ''


def _json(raw):
    def invalid(value):raise ValueError('Nonfinite JSON')
    def unique(pairs):
        result={}
        for name,value in pairs:
            if name in result:raise ValueError('Duplicate JSON field')
            result[name]=value
        return result
    return json.loads(raw,parse_constant=invalid,object_pairs_hook=unique)


def decode_resource(raw):
    try:
        value=_json(raw)
        if not isinstance(value,dict) or {'id','revision','updated_at'}&set(value):raise ValueError()
        return value
    except (TypeError,ValueError,RecursionError) as error:
        raise DomainError('Current resource metadata is not a unique supported JSON object.',409) from error


def _scan(store):
    items=[];claims={};indexes={};numbers={};digest=hashlib.sha256();read=0;unreadable=0
    with _db(store) as db:
        count=db.execute('SELECT COUNT(*) FROM resources').fetchone()[0]
        if count>MAX_ROWS:raise DomainError('ERP overview exceeds its bounded catalog reader; no partial overview.',409)
        for row in db.execute('SELECT id,length(CAST(payload AS BLOB)) AS bytes FROM resources'):
            if row['bytes'] is None or row['bytes']>MAX_PAYLOAD:raise DomainError('ERP overview payload exceeds its bounded reader.',409)
            read+=row['bytes']
            if read>MAX_READ:raise DomainError('ERP overview exceeds its total read budget; no partial overview.',409)
        for row in db.execute('SELECT * FROM resources ORDER BY id'):
            row=dict(row)
            if not isinstance(row['id'],str):raise DomainError('Catalog resource identity is not readable.',409)
            identifier=key(row['id']);issues=[]
            size=db.execute('SELECT length(CAST(payload AS BLOB)) FROM resource_versions WHERE id=? AND revision=?',(identifier,row['revision'])).fetchone()
            if size:
                if size[0] is None or size[0]>MAX_PAYLOAD:raise DomainError('ERP overview history head exceeds its bounded reader.',409)
                read+=size[0]
                if read>MAX_READ:raise DomainError('ERP overview exceeds its total read budget; no partial overview.',409)
            head=db.execute('SELECT * FROM resource_versions WHERE id=? AND revision=?',(identifier,row['revision'])).fetchone()
            history=list(db.execute('SELECT COUNT(*),MIN(revision),MAX(revision) FROM resource_versions WHERE id=?',(identifier,)).fetchone())
            try:digest.update(encoded([row,dict(head) if head else None,history]).encode('utf-8')+b'\n')
            except (TypeError,ValueError,UnicodeError) as error:raise DomainError('Stored ERP catalog metadata cannot be represented safely.',409) from error
            if type(row['revision']) is not int or row['revision']<1 or history!=[row['revision'],1,row['revision']] or not head or head['payload']!=row['payload'] or head['created_at']!=row['updated_at']:
                issues.append('revision_head')
            indexed=index_identity(row['external_key'])
            if row['external_key'] is not None and indexed is None:issues.append('invalid_erp_index')
            try:
                payload=decode_resource(row['payload'])
            except DomainError:payload={};issues.append('unreadable_resource');unreadable+=1
            kind=payload.get('kind');status=payload.get('cam_status');erp=payload.get('erp');claimed=identity(erp);valid_erp=None
            known_kind=isinstance(kind,str) and kind in KINDS
            if not known_kind or not isinstance(payload.get('name'),str) or not 1<=len(payload['name'])<=200 or status not in ('draft','approved','retired'):issues.append('invalid_resource_header')
            try:
                if isinstance(payload.get('name'),str):payload['name'].encode('utf-8')
            except UnicodeError:issues.append('invalid_resource_header')
            if erp is not None:
                try:
                    valid_erp=validate_fields(erp)
                except DomainError:valid_erp=None;issues.append('invalid_erp_fields')
            expected=encoded(list(claimed)) if claimed is not None else None
            if row['external_key']!=expected or erp is not None and claimed is None:issues.append('erp_index_mismatch')
            if kind in ('template','ui_standard') and (erp is not None or row['external_key'] is not None):issues.append('company_standard_erp')
            cam=payload.get('cam');assets=payload.get('asset_ids',[])
            technical=bool(cam or assets or payload.get('file_package') or payload.get('model_package') or payload.get('origin'))
            if status=='retired':state='retired'
            elif kind in ('template','ui_standard'):state='company_standard'
            elif valid_erp:state='linked' if technical or status!='draft' else 'erp_draft'
            else:state='cam_unassigned' if technical else 'draft_unassigned'
            item={'id':identifier,'revision':row['revision'] if type(row['revision']) is int and 1<=row['revision']<=2**63-1 else None,'updated_at':_text(row['updated_at'],100),
                'name':_text(payload.get('name'),200),'kind':kind if known_kind else '',
                'cam_status':status if status in ('draft','approved','retired') else '',
                'erp':valid_erp,'state':state,'issues':issues,'warnings':[],
                'claimed_identity':dict(zip(('company_id','system_id'),claimed)) if claimed is not None else None,
                'indexed_identity':dict(zip(('company_id','system_id'),indexed)) if indexed is not None else None,
                'technical_content_present':technical,'asset_count':len(assets) if isinstance(assets,list) else 0,
                'erp_index_verified':not any(code in issues for code in ('unreadable_resource','invalid_erp_index','invalid_erp_fields','erp_index_mismatch')),
                'history_head_verified':'revision_head' not in issues,'duplicate_identity_count':0,'number_identity_count':0}
            items.append(item)
            if claimed is not None:claims.setdefault(claimed,[]).append(item)
            if indexed is not None:indexes.setdefault(indexed,[]).append(item)
            if valid_erp:numbers.setdefault((valid_erp['company_id'],valid_erp['number']),set()).add(claimed)
    for pair,owners in claims.items():
        if len(owners)>1:
            for item in owners:item['issues'].append('duplicate_erp_identity');item['duplicate_identity_count']=len(owners);item['erp_index_verified']=False
    for item in items:
        if item['erp']:
            amount=len(numbers[(item['erp']['company_id'],item['erp']['number'])]);item['number_identity_count']=amount
            if amount>1:item['warnings'].append('number_not_unique')
        if item['issues']:item['state']='conflict'
    return items,digest.hexdigest(),claims,indexes,unreadable


def flags():return {'contract_version':1,'complete':True,'sqlite_read_only':True,'sqlite_read_transaction':True,
    'audit_verified':False,'files_verified':False,'erp_connected':False,'automatic_assignment':False,
    'resources_written':False,'nx_files_written':False,'identity_fields':['company_id','system_id'],
    'cardinality_policy':'not_configured','number_is_identity':False}


def preview(store,offset=0,limit=50,state='',kind='',company_id='',query='',expected_hash=None):
    from .erp_variants import legacy_writer
    with _db(store) as db:legacy_writer(db)
    if type(offset) is not int or not 0<=offset<=2**63-1 or type(limit) is not int or not 1<=limit<=100:raise DomainError('Invalid ERP overview page.')
    if not isinstance(state,str) or not isinstance(kind,str) or state and state not in STATES or kind and kind not in KINDS:raise DomainError('Unknown ERP overview filter.')
    if not isinstance(company_id,str) or len(company_id)>100 or not isinstance(query,str) or len(query)>200:raise DomainError('ERP overview filter is too long.')
    items,fingerprint,_,_,_= _scan(store)
    if expected_hash is not None and fingerprint!=expected_hash:raise DomainError('ERP catalog changed; reload the overview before paging.',409)
    counts={name:0 for name in STATES};selected=[];needle=query.casefold();companies=set()
    for item in items:
        counts[item['state']]+=1;erp=item['erp'] or {}
        identities=[value for value in (item['claimed_identity'],item['indexed_identity']) if value is not None]
        companies.update(value['company_id'] for value in identities)
        if state and item['state']!=state or kind and item['kind']!=kind or company_id and not any(value['company_id']==company_id for value in identities):continue
        text=' '.join([item['id'],item['name'],item['kind'],*erp.values(),*(field for value in identities for field in value.values()),*item['issues'],*item['warnings']]).casefold()
        if needle and needle not in text:continue
        selected.append(item)
    return {**flags(),'catalog_sha256':fingerprint,'total':len(items),'filtered':len(selected),'counts':counts,
        'companies':sorted(companies),'offset':offset,'limit':limit,'state':state,'kind':kind,'company_id':company_id,'query':query,
        'items':selected[offset:offset+limit]}


def lookup(store,company_id,system_id,expected_hash=None):
    from .erp_variants import legacy_writer
    with _db(store) as db:legacy_writer(db)
    pair=identity({'company_id':company_id,'system_id':system_id})
    if pair is None:raise DomainError('Use exact company_id and system_id, each 1 to 100 characters.')
    _,fingerprint,claims,indexes,unreadable=_scan(store)
    if expected_hash is not None and fingerprint!=expected_hash:raise DomainError('ERP catalog changed; repeat the exact lookup.',409)
    owners={item['id']:item for item in [*claims.get(pair,[]),*indexes.get(pair,[])]}
    if unreadable or len(owners)>1 or any(item['issues'] for item in owners.values()):
        raise DomainError('ERP identity cannot be resolved against a consistent current catalog; inspect the administrator overview.',409)
    current=next(iter(owners.values()),None)
    return {**flags(),'catalog_sha256':fingerprint,'company_id':company_id,'system_id':system_id,
        'found':current is not None,'resource':current,'historical_receipt':False}
