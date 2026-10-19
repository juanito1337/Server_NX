"""Explicit, offline 1:N ERP model. Never rewrite historical CAM payloads."""
import hashlib
import re
import sqlite3
import uuid
from types import SimpleNamespace

from .auditstate import encoded,ERP_KEYS
from .erp_catalog import _db,_json as decode_json,validate_fields,decode_resource,identity
from .exportview import page
from .safety import DomainError,offline_path
from .write_requests import key,request_hash

POLICY='one_article_many_variants'
KEYS=ERP_KEYS
SCHEMAS=(
    'CREATE TABLE erp_model(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version INTEGER NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL)',
    'CREATE TABLE erp_articles(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL,external_key TEXT NOT NULL UNIQUE,updated_at TEXT NOT NULL)',
    'CREATE TABLE erp_article_versions(id TEXT NOT NULL,revision INTEGER NOT NULL,payload TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(id,revision))',
    'CREATE TABLE erp_variant_links(resource_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,article_id TEXT,updated_at TEXT NOT NULL)',
    'CREATE TABLE erp_variant_versions(resource_id TEXT NOT NULL,revision INTEGER NOT NULL,article_id TEXT,actor TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(resource_id,revision))',
    'CREATE TABLE erp_requests(actor TEXT NOT NULL,request_key TEXT NOT NULL,action TEXT NOT NULL,request_sha256 TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,response TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(actor,request_key))')
MAX_ROWS=100000
MAX_BYTES=512*1024*1024
MAX_ROW_BYTES=32*1024*1024
MAX_PAGE_BYTES=4*1024*1024
COLUMNS={'erp_model':{'singleton','version','payload','updated_at'},
    'erp_articles':{'id','revision','payload','external_key','updated_at'},
    'erp_article_versions':{'id','revision','payload','actor','created_at'},
    'erp_variant_links':{'resource_id','revision','article_id','updated_at'},
    'erp_variant_versions':{'resource_id','revision','article_id','actor','created_at'},
    'erp_requests':{'actor','request_key','action','request_sha256','entity_type','entity_id','revision','response','created_at'}}


def sha(value):return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()
def hex64(value):return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None
def required_key(value):
    result=key(value)
    if result is None:raise DomainError('A canonical Idempotency-Key is required for explicit ERP model writes.')
    return result


def model_state(db):
    present={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}&set(KEYS)
    if not present:return None
    if present!=set(KEYS):raise DomainError('ERP model tables are incomplete; do not auto-repair or migrate again.',409)
    if any({row['name'] for row in db.execute('PRAGMA table_info('+table+')')}!=columns for table,columns in COLUMNS.items()):
        raise DomainError('ERP model schema is unsupported; do not auto-repair it.',409)
    try:
        rows=db.execute('SELECT * FROM erp_model').fetchall()
        if len(rows)!=1 or rows[0]['singleton']!=1 or rows[0]['version']!=2:raise ValueError()
        value=decode_json(rows[0]['payload'])
        if (not isinstance(value,dict) or value.get('model_version')!=2 or value.get('cardinality_policy')!=POLICY or
                value.get('updated_at')!=rows[0]['updated_at'] or value.get('technical_resources_written') is not False):raise ValueError()
        required_key(value['migration_id'])
        return value
    except (KeyError,TypeError,ValueError,DomainError,sqlite3.DatabaseError) as error:
        raise DomainError('ERP model metadata is inconsistent; inspect before writing.',409) from error


def legacy_writer(db):
    if model_state(db) is not None:
        raise DomainError('The shared ERP article model is enabled. Use article/variant APIs; legacy ERP snapshots are immutable.',409)


def legacy_key(db,actor,request_key):
    if request_key is not None and model_state(db) is not None and db.execute('SELECT 1 FROM erp_requests WHERE actor=? AND request_key=?',(actor,request_key)).fetchone():
        raise DomainError('Idempotency-Key belongs to an ERP model write; do not reuse it for CAM metadata.',409)


def _article(row):return {'id':row['id'],'revision':row['revision'],'updated_at':row['updated_at'],'erp':validate_fields(decode_json(row['payload']))}
def _link(row):return {'resource_id':row['resource_id'],'revision':row['revision'],'article_id':row['article_id'],'updated_at':row['updated_at']}
def _revision(value,minimum=0):
    if type(value) is not int or not minimum<=value<=2**53-1:raise DomainError('Use an exact nonnegative expected revision.')


def _require(db):
    state=model_state(db)
    if state is None:raise DomainError('ERP 1:N model is not enabled; explicitly preview and migrate first.',409)
    return state


def _head(db,table,versions,column,identifier):
    # All SQL identifiers are code-owned constants from this module.
    if table in ('erp_articles','resources'):
        sizes=db.execute('SELECT length(CAST(payload AS BLOB)) FROM '+table+' WHERE '+column+'=?',(identifier,)).fetchone()
        if sizes and (sizes[0] is None or sizes[0]>MAX_ROW_BYTES):raise DomainError('ERP/CAM payload exceeds its bounded reader.',409)
    row=db.execute('SELECT * FROM '+table+' WHERE '+column+'=?',(identifier,)).fetchone()
    if row is None:return None
    if table in ('erp_articles','resources'):
        sizes=db.execute('SELECT length(CAST(payload AS BLOB)) FROM '+versions+' WHERE '+column+'=? AND revision=?',(identifier,row['revision'])).fetchone()
        if sizes and (sizes[0] is None or sizes[0]>MAX_ROW_BYTES):raise DomainError('ERP/CAM revision exceeds its bounded reader.',409)
    version=db.execute('SELECT * FROM '+versions+' WHERE '+column+'=? AND revision=?',(identifier,row['revision'])).fetchone()
    count=tuple(db.execute('SELECT COUNT(*),MIN(revision),MAX(revision) FROM '+versions+' WHERE '+column+'=?',(identifier,)).fetchone())
    if type(row['revision']) is not int or row['revision']<1 or count!=(row['revision'],1,row['revision']) or not version or version['created_at']!=row['updated_at']:
        raise DomainError('ERP entity revision history is inconsistent.',409)
    names=('payload',) if table in ('erp_articles','resources') else ('article_id',)
    if any(row[name]!=version[name] for name in names):raise DomainError('ERP entity differs from its immutable history head.',409)
    if table=='erp_articles':
        try:valid=row['external_key']==encoded(list(identity(validate_fields(decode_json(row['payload'])))))
        except (DomainError,TypeError,ValueError) as error:raise DomainError('Stored ERP article fields are inconsistent.',409) from error
        if not valid:raise DomainError('ERP article identity differs from its index.',409)
    return row


def _receipt_value(db,row):
    try:
        required_key(row['request_key'])
        if not isinstance(row['actor'],str) or not row['actor'] or not hex64(row['request_sha256']):raise ValueError()
        if row['entity_type']=='model' and row['action']=='model.migrate' and row['entity_id']=='1' and row['revision']==2:
            expected=_require(db)
        elif row['entity_type']=='article' and row['action']=='article.upsert':
            key(row['entity_id']);version=db.execute('SELECT * FROM erp_article_versions WHERE id=? AND revision=?',(row['entity_id'],row['revision'])).fetchone()
            if not version:raise ValueError()
            expected={'id':version['id'],'revision':version['revision'],'updated_at':version['created_at'],'erp':validate_fields(decode_json(version['payload']))}
        elif row['entity_type']=='variant' and row['action']=='variant.assign':
            key(row['entity_id']);version=db.execute('SELECT * FROM erp_variant_versions WHERE resource_id=? AND revision=?',(row['entity_id'],row['revision'])).fetchone()
            if not version:raise ValueError()
            expected={'resource_id':version['resource_id'],'revision':version['revision'],'article_id':version['article_id'],'updated_at':version['created_at']}
        else:raise ValueError()
        result=decode_json(row['response'])
        if encoded(result)!=encoded(expected):raise ValueError()
        return result
    except (ValueError,TypeError,KeyError,DomainError) as error:raise DomainError('ERP write receipt differs from its immutable entity revision.',409) from error


def _replay(db,actor,request_key,action,fingerprint):
    from .write_requests import read_row
    legacy,_=read_row(db,actor,request_key)
    if legacy is not None:
        if legacy['state']=='cancelled':raise DomainError('This ERP request was fenced; use a new key only for a new deliberate action.',409)
        raise DomainError('Idempotency-Key already belongs to a legacy metadata write.',409)
    if model_state(db) is None:return None
    row=db.execute('SELECT * FROM erp_requests WHERE actor=? AND request_key=?',(actor,request_key)).fetchone()
    if row is None:return None
    result=_receipt_value(db,row)
    if row['action']!=action or row['request_sha256']!=fingerprint:raise DomainError('ERP Idempotency-Key already used for different data or action.',409)
    return result


def _commit(store,db,actor,request_key,action,fingerprint,entity_type,entity_id,revision,result,stamp):
    db.execute('INSERT INTO erp_requests VALUES(?,?,?,?,?,?,?,?,?)',(actor,request_key,action,fingerprint,entity_type,entity_id,revision,encoded(result),stamp))
    event={'model.migrate':'erp.model.migrated','article.upsert':'erp.article.upserted','variant.assign':'erp.variant.assigned'}[action]
    store.audit(db,actor,event,{'entity_type':entity_type,'entity_id':entity_id,'revision':revision,
        '_erp_request':{'actor':actor,'key':request_key}})


def _read_tables(db):
    tables={};size=0
    for name in KEYS:
        if db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0]>MAX_ROWS:raise DomainError('ERP model exceeds its bounded reader.',409)
        expression='+'.join('COALESCE(length(CAST('+column+' AS BLOB)),0)' for column in sorted(COLUMNS[name]))
        total,maximum=db.execute('SELECT COALESCE(SUM('+expression+'),0),COALESCE(MAX('+expression+'),0) FROM '+name).fetchone()
        if total>MAX_BYTES-size or maximum>MAX_ROW_BYTES:raise DomainError('ERP model exceeds its bounded read budget.',409)
        rows=[]
        for row in db.execute('SELECT * FROM '+name+' ORDER BY '+','.join(KEYS[name])):
            value=dict(row);size+=len(encoded(value).encode('utf-8'))
            if size>MAX_BYTES:raise DomainError('ERP model exceeds its bounded read budget.',409)
            rows.append(value)
        tables[name]=rows
    if db.execute('SELECT 1 FROM erp_requests e JOIN write_requests w ON e.actor=w.actor AND e.request_key=w.request_key LIMIT 1').fetchone():
        raise DomainError('ERP and legacy write receipts claim the same role/key.',409)
    return tables,size


def _resources(db,budget=MAX_BYTES):
    if db.execute('SELECT COUNT(*) FROM resources').fetchone()[0]>MAX_ROWS:raise DomainError('CAM catalog exceeds the bounded ERP reader.',409)
    size=0
    for row in db.execute('SELECT length(CAST(payload AS BLOB)) AS bytes FROM resources'):
        if row['bytes'] is None:raise DomainError('CAM metadata is unreadable.',409)
        size+=row['bytes']
        if row['bytes']>MAX_ROW_BYTES or size>budget:raise DomainError('CAM metadata exceeds the bounded ERP read budget.',409)
    result={}
    from .libraries import KINDS
    for row in db.execute('SELECT * FROM resources ORDER BY id'):
        value=dict(row);required_key(value['id']);_revision(value['revision'],1);payload=decode_resource(value['payload'])
        if (not isinstance(payload.get('kind'),str) or payload['kind'] not in KINDS or not isinstance(payload.get('name'),str) or
                not 1<=len(payload['name'])<=200 or payload.get('cam_status') not in ('draft','approved','retired')):
            raise DomainError('CAM resource header is inconsistent; no partial ERP overview.',409)
        try:payload['name'].encode('utf-8')
        except UnicodeError as error:raise DomainError('CAM name cannot be represented safely.',409) from error
        _head(db,'resources','resource_versions','id',value['id']);result[value['id']]=value
    return result


def validate_tables(tables,resources):
    """Pure metadata validation reused by archive/integrity readers, no live DB."""
    present=set(tables)&set(KEYS)
    if not present:return
    if present!=set(KEYS):raise DomainError('ERP model tables are incomplete.',409)
    models=tables['erp_model']
    if len(models)!=1 or models[0]['singleton']!=1 or models[0]['version']!=2:raise DomainError('Invalid ERP model marker.',409)
    try:
        marker=decode_json(models[0]['payload'])
        if marker['model_version']!=2 or marker['cardinality_policy']!=POLICY or marker['updated_at']!=models[0]['updated_at'] or marker['technical_resources_written'] is not False:raise ValueError()
        required_key(marker['migration_id'])
        articles={};versions={};identities=set();variant_versions={}
        for row in tables['erp_article_versions']:
            required_key(row['id']);_revision(row['revision'],1);validate_fields(decode_json(row['payload']))
            if row['revision'] in versions.get(row['id'],{}):raise ValueError()
            versions.setdefault(row['id'],{})[row['revision']]=row
        for row in tables['erp_articles']:
            required_key(row['id']);_revision(row['revision'],1);erp=validate_fields(decode_json(row['payload']));history=versions.get(row['id'],{})
            if row['id'] in articles or len(history)!=row['revision'] or sorted(history)!=list(range(1,row['revision']+1)):raise ValueError()
            head=history[row['revision']]
            if head['payload']!=row['payload'] or head['created_at']!=row['updated_at'] or row['external_key']!=encoded(list(identity(erp))) or row['external_key'] in identities:raise ValueError()
            identities.add(row['external_key']);articles[row['id']]=row
        if set(versions)-set(articles):raise ValueError()
        for row in tables['erp_variant_versions']:
            required_key(row['resource_id']);_revision(row['revision'],1)
            if row['article_id'] is not None and row['article_id'] not in articles:raise ValueError()
            if row['revision'] in variant_versions.get(row['resource_id'],{}):raise ValueError()
            variant_versions.setdefault(row['resource_id'],{})[row['revision']]=row
        current=set()
        for row in tables['erp_variant_links']:
            required_key(row['resource_id']);_revision(row['revision'],1);resource=resources.get(row['resource_id']);history=variant_versions.get(row['resource_id'],{})
            if row['resource_id'] in current or not resource or len(history)!=row['revision'] or sorted(history)!=list(range(1,row['revision']+1)):raise ValueError()
            payload=decode_resource(resource['payload'])
            if payload['kind'] in ('template','ui_standard'):raise ValueError()
            head=history[row['revision']]
            if head['article_id']!=row['article_id'] or head['created_at']!=row['updated_at']:raise ValueError()
            current.add(row['resource_id'])
        if set(variant_versions)-current:raise ValueError()
        # Validate receipts against immutable versions using the same representation as live replay.
        request_keys=set();legacy_keys={(row['actor'],row['request_key']) for row in tables.get('write_requests',[])}
        for row in tables['erp_requests']:
            required_key(row['request_key'])
            pair=(row['actor'],row['request_key'])
            if pair in request_keys or pair in legacy_keys:raise ValueError()
            request_keys.add(pair)
            if not row['actor'] or not hex64(row['request_sha256']):raise ValueError()
            if row['entity_type']=='model' and row['action']=='model.migrate' and row['entity_id']=='1' and row['revision']==2:expected=marker
            elif row['entity_type']=='article' and row['action']=='article.upsert':
                version=versions[row['entity_id']][row['revision']];expected={'id':version['id'],'revision':version['revision'],'updated_at':version['created_at'],'erp':validate_fields(decode_json(version['payload']))}
            elif row['entity_type']=='variant' and row['action']=='variant.assign':
                version=variant_versions[row['entity_id']][row['revision']];expected={'resource_id':version['resource_id'],'revision':version['revision'],'article_id':version['article_id'],'updated_at':version['created_at']}
            else:raise ValueError()
            if encoded(decode_json(row['response']))!=encoded(expected):raise ValueError()
    except (ValueError,TypeError,KeyError,DomainError,AttributeError) as error:
        raise DomainError('ERP article, variant, revision or receipt metadata is inconsistent.',409) from error


class ERPVariants:
    def __init__(self,store):self.store=store

    def status(self):
        with _db(self.store) as db:value=model_state(db)
        return {'contract_version':1,'enabled':value is not None,'model_version':2 if value else 1,
            'target_policy':POLICY,'cardinality_policy':POLICY if value else 'legacy_one_to_one',
            'explicit_migration_required':value is None,'automatic_migration':False,'erp_connected':False,'read_only':True}

    def migration_preview(self):
        from .erp_catalog import _scan
        from .integrity import snapshot,Issues,metadata
        with self.store.lock:
            with _db(self.store) as db:
                if model_state(db) is not None:raise DomainError('ERP 1:N model is already enabled; no repeat migration.',409)
            issues=Issues();state=snapshot(self.store,issues);metadata(state,issues,None)
            before=state['sha256'];items,catalog,_,_,_=_scan(self.store)
            if snapshot(self.store)['sha256']!=before:raise DomainError('Management metadata changed during ERP migration preview.',409)
            conflicts=[{'resource_id':item['id'],'issues':item['issues']} for item in items if item['issues']]
            conflicts.extend({'entity':item['entity'],'issues':[item['code']]} for item in issues.errors)
            legacy=[item for item in items if item['erp'] is not None and not item['issues']]
            value={'contract_version':1,'scope':'erp_model_migration','from_version':1,'to_version':2,'cardinality_policy':POLICY,
                'management_sha256':before,'catalog_sha256':catalog,'legacy_articles':len(legacy),'legacy_links':len(legacy),
                'resources':len(items),'conflicts':conflicts,'warnings':issues.warnings,'can_migrate':not conflicts,'read_only':True,
                'technical_resources_written':False,'erp_connected':False,'automatic_approval':False,
                'resource_ids_preserved':True,'historical_payloads_preserved':True,'db_backup_required':True}
            value['preview_hash']=sha(value)
            return value

    def migrate(self,preview_hash,actor,request_key):
        from .integrity import snapshot,Issues
        from .erp_catalog import _scan
        from .store import now
        request_key=required_key(request_key)
        if not hex64(preview_hash):raise DomainError('A complete ERP migration preview SHA is required.')
        fingerprint=request_hash('model.migrate',None,None,{'preview_hash':preview_hash})
        with self.store.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');cached=_replay(db,actor,request_key,'model.migrate',fingerprint)
            if cached is not None:return cached
            report=self.migration_preview()
            if report['preview_hash']!=preview_hash or not report['can_migrate']:raise DomainError('ERP migration inputs changed or contain conflicts; repeat the preview.',409)
            # Independent read-only connection while this writer holds the metadata lock.
            folder=offline_path(self.store.root/'backups'/'erp-model'/request_key);folder.mkdir(parents=True,exist_ok=True)
            target=offline_path(folder/'management.sqlite3')
            if not target.exists():
                with target.open('xb'):pass
                with _db(self.store) as source:
                    destination=sqlite3.connect(target)
                    try:source.backup(destination)
                    finally:destination.close()
            issues=Issues();backup=snapshot(SimpleNamespace(root=folder),issues)
            if issues.error_count or backup['sha256']!=report['management_sha256']:
                raise DomainError('Pre-migration database backup differs or fails SQL/audit checks; retained for diagnosis.',409)
            items,catalog,_,_,_=_scan(self.store)
            if catalog!=report['catalog_sha256']:raise DomainError('Catalog changed before ERP migration.',409)
            for statement in SCHEMAS:db.execute(statement)  # Never executescript: keep DDL inside the transaction.
            stamp=now()
            result={'contract_version':1,'model_version':2,'cardinality_policy':POLICY,'migration_id':request_key,
                'updated_at':stamp,'backup_folder':str(folder),'source_management_sha256':report['management_sha256'],
                'legacy_articles':report['legacy_articles'],'legacy_links':report['legacy_links'],
                'technical_resources_written':False,'historical_payloads_preserved':True,'erp_connected':False,'automatic_approval':False}
            db.execute('INSERT INTO erp_model VALUES(1,2,?,?)',(encoded(result),stamp))
            for item in items:
                if item['erp'] is None:continue
                identifier=str(uuid.uuid4());raw=encoded(item['erp']);external=encoded(list(identity(item['erp'])))
                db.execute('INSERT INTO erp_articles VALUES(?,?,?,?,?)',(identifier,1,raw,external,stamp))
                db.execute('INSERT INTO erp_article_versions VALUES(?,?,?,?,?)',(identifier,1,raw,actor,stamp))
                db.execute('INSERT INTO erp_variant_links VALUES(?,?,?,?)',(item['id'],1,identifier,stamp))
                db.execute('INSERT INTO erp_variant_versions VALUES(?,?,?,?,?)',(item['id'],1,identifier,actor,stamp))
            _commit(self.store,db,actor,request_key,'model.migrate',fingerprint,'model','1',2,result,stamp)
            return result

    def upsert(self,fields,expected_revision,actor,request_key):
        from .store import now
        fields=validate_fields(fields);_revision(expected_revision);request_key=required_key(request_key)
        external=encoded(list(identity(fields)));fingerprint=request_hash('article.upsert',external,expected_revision,fields)
        with self.store.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');_require(db);cached=_replay(db,actor,request_key,'article.upsert',fingerprint)
            if cached is not None:return cached
            found=db.execute('SELECT id FROM erp_articles WHERE external_key=?',(external,)).fetchone()
            old=_head(db,'erp_articles','erp_article_versions','id',found['id']) if found else None
            if (old['revision'] if old else 0)!=expected_revision:raise DomainError('ERP article changed; use its current expected_revision (zero only for creation).',409)
            if old and validate_fields(decode_json(old['payload']))==fields:result=_article(old);stamp=now()
            else:
                identifier=old['id'] if old else str(uuid.uuid4());revision=old['revision']+1 if old else 1;stamp=now();raw=encoded(fields)
                db.execute('INSERT INTO erp_articles VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at',(identifier,revision,raw,external,stamp))
                db.execute('INSERT INTO erp_article_versions VALUES(?,?,?,?,?)',(identifier,revision,raw,actor,stamp))
                result={'id':identifier,'revision':revision,'updated_at':stamp,'erp':fields}
            _commit(self.store,db,actor,request_key,'article.upsert',fingerprint,'article',result['id'],result['revision'],result,stamp)
            return result

    def assign(self,resource_id,article_id,expected_revision,expected_resource_revision,expected_article_revision,actor,request_key):
        from .store import now
        resource_id=required_key(resource_id);article_id=required_key(article_id) if article_id is not None else None
        _revision(expected_revision);_revision(expected_resource_revision,1)
        if article_id is not None:_revision(expected_article_revision,1)
        elif expected_article_revision is not None:raise DomainError('Unlink has no expected article revision.')
        request_key=required_key(request_key);values={'article_id':article_id,'expected_resource_revision':expected_resource_revision,'expected_article_revision':expected_article_revision}
        fingerprint=request_hash('variant.assign',resource_id,expected_revision,values)
        with self.store.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');_require(db);cached=_replay(db,actor,request_key,'variant.assign',fingerprint)
            if cached is not None:return cached
            resource=_head(db,'resources','resource_versions','id',resource_id)
            if resource is None:raise DomainError('CAM variant not found.',404)
            if resource['revision']!=expected_resource_revision:raise DomainError('CAM variant changed; reload before assigning.',409)
            payload=decode_resource(resource['payload'])
            if payload['kind'] in ('template','ui_standard'):raise DomainError('Company templates and UI standards are not ERP article variants.')
            if article_id is not None:
                article=_head(db,'erp_articles','erp_article_versions','id',article_id)
                if article is None:raise DomainError('ERP article not found.',404)
                if article['revision']!=expected_article_revision:raise DomainError('ERP article changed; reload before assigning.',409)
            old=_head(db,'erp_variant_links','erp_variant_versions','resource_id',resource_id)
            if old and old['article_id'] is not None and not _head(db,'erp_articles','erp_article_versions','id',old['article_id']):
                raise DomainError('Existing variant link references a missing ERP article; no automatic repair.',409)
            if (old['revision'] if old else 0)!=expected_revision:raise DomainError('ERP variant link changed; reload before assigning.',409)
            if old and old['article_id']==article_id:result=_link(old);stamp=now()
            else:
                revision=old['revision']+1 if old else 1;stamp=now()
                db.execute('INSERT INTO erp_variant_links VALUES(?,?,?,?) ON CONFLICT(resource_id) DO UPDATE SET revision=excluded.revision,article_id=excluded.article_id,updated_at=excluded.updated_at',(resource_id,revision,article_id,stamp))
                db.execute('INSERT INTO erp_variant_versions VALUES(?,?,?,?,?)',(resource_id,revision,article_id,actor,stamp))
                result={'resource_id':resource_id,'revision':revision,'article_id':article_id,'updated_at':stamp}
            _commit(self.store,db,actor,request_key,'variant.assign',fingerprint,'variant',resource_id,result['revision'],result,stamp)
            return result

    def read(self,query='',offset=0,limit=50,expected_hash=None,article_id=None,company_id=None,system_id=None):
        page(offset,limit,100)
        if not isinstance(query,str) or len(query)>200:raise DomainError('ERP search is limited to 200 characters.')
        if article_id is not None:article_id=required_key(article_id)
        if (company_id is None)!=(system_id is None) or company_id is not None and identity({'company_id':company_id,'system_id':system_id}) is None:raise DomainError('Use exact company/system identities together.')
        if expected_hash is not None and not hex64(expected_hash):raise DomainError('Invalid ERP snapshot SHA.')
        with _db(self.store) as db:
            _require(db);tables,size=_read_tables(db)
            resources=_resources(db,MAX_BYTES-size)
            validate_tables(tables,resources);fingerprint=sha([tables,resources])
            if expected_hash is not None and fingerprint!=expected_hash:raise DomainError('ERP article/variant catalog changed; reload before paging.',409)
            articles=[_article(row) for row in tables['erp_articles']];links=tables['erp_variant_links']
            if company_id is not None:articles=[value for value in articles if identity(value['erp'])==(company_id,system_id)]
            if article_id is not None:
                article=next((value for value in articles if value['id']==article_id),None)
                if article is None:raise DomainError('ERP article not found.',404)
                items=[]
                for link in links:
                    if link['article_id']!=article_id:continue
                    resource=resources[link['resource_id']];payload=decode_resource(resource['payload'])
                    items.append({**_link(link),'resource_revision':resource['revision'],'name':payload['name'],'kind':payload['kind'],
                        'cam_status':payload['cam_status'],'legacy_erp_snapshot':payload.get('erp')})
                total=len(items)
            else:
                counts={}
                for link in links:
                    if link['article_id'] is not None:counts[link['article_id']]=counts.get(link['article_id'],0)+1
                items=[{**value,'variants':counts.get(value['id'],0)} for value in articles];total=len(items);article=None
            selected=[value for value in items if query.casefold() in encoded(value).casefold()]
            result={'contract_version':1,'cardinality_policy':POLICY,'read_only':True,'complete':True,'erp_connected':False,
                'sqlite_read_only':True,'sqlite_read_transaction':True,'audit_verified':False,'files_verified':False,
                'resources_written':False,'nx_files_written':False,'automatic_approval':False,'snapshot_sha256':fingerprint,
                'query':query,'offset':offset,'limit':limit,'total':total,'filtered':len(selected),'items':selected[offset:offset+limit],
                'article':article,'company_id':company_id,'system_id':system_id}
            if len(encoded(result).encode('utf-8'))>MAX_PAGE_BYTES:raise DomainError('ERP page exceeds its byte bound.',409)
            return result

    def variant(self,resource_id):
        resource_id=required_key(resource_id)
        with _db(self.store) as db:
            _require(db);tables,size=_read_tables(db);resources=_resources(db,MAX_BYTES-size);validate_tables(tables,resources)
            resource=resources.get(resource_id)
            if resource is None:raise DomainError('CAM variant not found.',404)
            link=next((row for row in tables['erp_variant_links'] if row['resource_id']==resource_id),None)
            article=next((row for row in tables['erp_articles'] if link and row['id']==link['article_id']),None)
            return {'contract_version':1,'cardinality_policy':POLICY,'read_only':True,'complete':True,'erp_connected':False,
                'sqlite_read_only':True,'sqlite_read_transaction':True,'resources_written':False,'nx_files_written':False,'automatic_approval':False,
                'snapshot_sha256':sha([tables,resources]),'resource_id':resource_id,'resource_revision':resource['revision'],
                'link':_link(link) if link else None,'article':_article(article) if article else None,
                'legacy_erp_snapshot':decode_resource(resource['payload']).get('erp')}

    def history(self,entity_type,identifier,offset=0,limit=50,expected_hash=None):
        page(offset,limit,100);identifier=required_key(identifier)
        if entity_type not in ('article','variant'):raise DomainError('Choose article or variant history.')
        if expected_hash is not None and not hex64(expected_hash):raise DomainError('Invalid ERP snapshot SHA.')
        with _db(self.store) as db:
            _require(db);tables,size=_read_tables(db);resources=_resources(db,MAX_BYTES-size);validate_tables(tables,resources)
            fingerprint=sha([tables,resources])
            if expected_hash is not None and expected_hash!=fingerprint:raise DomainError('ERP article/variant catalog changed; reload history.',409)
            table='erp_article_versions' if entity_type=='article' else 'erp_variant_versions';column='id' if entity_type=='article' else 'resource_id'
            values=[row for row in tables[table] if row[column]==identifier]
            if not values:raise DomainError('ERP entity history not found.',404)
            items=[]
            for row in values[offset:offset+limit]:
                value={'revision':row['revision'],'actor':row['actor'],'created_at':row['created_at']}
                if entity_type=='article':value['erp']=validate_fields(decode_json(row['payload']))
                else:value['article_id']=row['article_id']
                items.append(value)
            result={'contract_version':1,'cardinality_policy':POLICY,'read_only':True,'complete':True,'erp_connected':False,
                'sqlite_read_only':True,'sqlite_read_transaction':True,'automatic_approval':False,'nx_files_written':False,'resources_written':False,
                'snapshot_sha256':fingerprint,'entity_type':entity_type,'id':identifier,'offset':offset,'limit':limit,'total':len(values),'items':items}
            if len(encoded(result).encode('utf-8'))>MAX_PAGE_BYTES:raise DomainError('ERP history page exceeds its byte bound.',409)
            return result

    def receipt(self,actor,request_key):
        request_key=required_key(request_key)
        with _db(self.store) as db:
            from .write_requests import read_row
            legacy,_=read_row(db,actor,request_key)
            if legacy is not None and legacy['state']=='cancelled':
                return {'contract_version':1,'state':'cancelled','action':'cancelled','request_key':request_key,
                    'request_sha256':None,'result':None,'historical_receipt':True,'automatic_approval':False}
            if legacy is not None:raise DomainError('This key belongs to a legacy metadata write, not an ERP model request.',409)
            state=model_state(db)
            row=db.execute('SELECT * FROM erp_requests WHERE actor=? AND request_key=?',(actor,request_key)).fetchone() if state is not None else None
            if row is None:raise DomainError('ERP request has no committed receipt; this is not proof that an in-flight write cannot finish.',404)
            result=_receipt_value(db,row)
        return {'contract_version':1,'state':'committed','action':row['action'],'request_key':request_key,
            'request_sha256':row['request_sha256'],'result':result,'historical_receipt':True,'automatic_approval':False}

    def cancel(self,actor,request_key):
        from .store import now
        from .write_requests import read_row
        request_key=required_key(request_key)
        with self.store.lock,self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');state=model_state(db)
            row=db.execute('SELECT * FROM erp_requests WHERE actor=? AND request_key=?',(actor,request_key)).fetchone() if state is not None else None
            if row is not None:_receipt_value(db,row)
            else:
                legacy,_=read_row(db,actor,request_key)
                if legacy is not None and legacy['state']!='cancelled':raise DomainError('This key belongs to an original legacy metadata receipt, not an ERP model request.',409)
                if legacy is None:
                    db.execute('INSERT INTO write_requests(actor,request_key,state,action,created_at) VALUES(?,?,?,?,?)',(actor,request_key,'cancelled','cancelled',now()))
                    self.store.audit(db,actor,'write.request.cancelled',{'_write_request':{'actor':actor,'key':request_key}})
        return self.receipt(actor,request_key)
