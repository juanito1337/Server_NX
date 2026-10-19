"""Paged whole-file export plans, never partial output or filesystem writes."""
import hashlib
import re
import uuid

from .exportview import page as bounded_page
from .history import _db as read_db
from .libraries import SPECS,digest
from .migration import portable
from .safety import DomainError,asset_filename

MAX_FILES=100000
MAX_ATTACHMENTS=100000
MAX_PAGE_BYTES=4*1024*1024
MAX_SAFE=2**53-1
OWNER_LIMIT=10


def checked_meta(value):
    if (not isinstance(value,dict) or type(value.get('size')) is not int or not 0<=value['size']<=MAX_SAFE or
            not isinstance(value.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',value['sha256'])):
        raise DomainError('Export file metadata is incomplete or exceeds the reader bound.',409)
    return {'size':value['size'],'sha256':value['sha256']}


def checked_path(path):
    if not isinstance(path,str) or not 1<=len(path)<=4096:raise DomainError('Invalid export-relative path.',409)
    return portable(path)


class ExportFiles:
    def __init__(self,store):self.store=store

    def inspect(self,release_id,defaults,preview_hash,view='files',change='',query='',offset=0,limit=50):
        bounded_page(offset,limit,100)
        if view not in ('files','private') or change not in ('','changed','created','updated','removed','unchanged') or view=='private' and change:
            raise DomainError('Choose a supported file view/change filter.')
        if not isinstance(query,str) or len(query)>200 or not isinstance(preview_hash,str) or not re.fullmatch('[a-f0-9]{64}',preview_hash):
            raise DomainError('Choose a bounded query and current export preview SHA.')
        with self.store.lock:
            plan,report=self.store._build_export_plan(release_id,defaults)
            if report['preview_hash']!=preview_hash or not report['can_export']:
                raise DomainError('Export inputs changed or have conflicts; repeat a complete export preview.',409)
            release=self.store.get_release(release_id)
            if digest(release['manifest'])!=report['parent_manifest_sha256']:
                raise DomainError('Export parent metadata changed while inspecting files.',409)
            catalog={resource['id']:resource for resource in plan['catalog']}
            assignments=plan.get('asset_assignments',{});owners=plan.get('file_owners',{})
            private=[];identifiers=set();pairs=0
            modes={item['resource_id']:item for item in report['actions']}
            for resource in plan['catalog']:
                attached=resource.get('asset_ids',[])
                if not isinstance(attached,list) or any(not isinstance(value,str) for value in attached):
                    raise DomainError('Invalid stored attachment assignments.',409)
                pairs+=len(attached)
                if pairs>MAX_ATTACHMENTS or len(set(attached))!=len(attached):raise DomainError('Attachment assignments exceed the bounded export reader.',409)
                for identifier in attached:
                    try:valid=isinstance(identifier,str) and str(uuid.UUID(identifier))==identifier
                    except (ValueError,TypeError,AttributeError):valid=False
                    if not valid:raise DomainError('Invalid stored attachment identity.',409)
                    identifiers.add(identifier)
                    if identifier in assignments.get(resource['id'],set()):continue
                    action=modes.get(resource['id'],{})
                    reason='draft' if resource['cam_status']=='draft' else 'retired' if resource['cam_status']=='retired' else 'migration_excluded' if action.get('migration_exclusion') else 'not_selected' if resource.get('file_bindings') or resource.get('file_package') else 'catalog_only'
                    private.append((resource,identifier,reason))
            metadata={};keys=sorted(identifiers)
            with read_db(self.store) as db:
                if {(row['id'],row['revision']) for row in db.execute('SELECT id,revision FROM resources')}!={(item['id'],item['revision']) for item in report['catalog_revisions']}:
                    raise DomainError('Catalog changed while inspecting export files.',409)
                for start in range(0,len(keys),400):
                    chunk=keys[start:start+400]
                    for row in db.execute('SELECT id,filename,size,sha256 FROM assets WHERE id IN ('+','.join('?' for _ in chunk)+')',chunk):
                        item=dict(row);asset_filename(item['filename']);checked_meta(item);metadata[item['id']]=item
            if set(metadata)!=identifiers:raise DomainError('Stored attachment metadata is missing; inspect management integrity.',409)
            private_pin=digest([metadata[key] for key in keys])
            before={};after={};kinds={};sources={};writes=set()
            for item in release['manifest']:
                path=checked_path(item['path']);key=path.casefold()
                if key in before:raise DomainError('Case-colliding parent file paths.',409)
                before[key]={'path':path,**checked_meta(item)}
            if len(before)>MAX_FILES:raise DomainError('Export file tree exceeds the bounded reader.',409)
            after=dict(before)
            for item in plan.get('file_deletions',[]):
                path=checked_path(item['path']);key=path.casefold();old=before.get(key)
                if not old or old['path']!=path or old['sha256']!=item['sha256']:raise DomainError('Planned removal differs from the inherited file.',409)
                after.pop(key,None);kinds[key]='file_package'
            def put(path,meta,kind,asset_id=None):
                key=checked_path(path).casefold()
                if key in writes or key in before and before[key]['path']!=path or key in before and key not in after:
                    raise DomainError('Ambiguous or case-changing export file operation.',409)
                writes.add(key);after[key]={'path':path,**checked_meta(meta)};kinds[key]=kind;sources[key]=asset_id
            for identifier,table in plan['tables'].items():
                raw=table.text.encode('utf-8-sig');path=SPECS[identifier]['path']
                put(path,{'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},'native_database')
                owners.setdefault(path.casefold(),set()).update(item['resource_id'] for item in report['actions'] if item.get('library_id')==identifier and item['action'] not in ('deferred','catalog_only'))
            for item in plan['assets'].values():
                if metadata.get(item['id'])!={key:item[key] for key in ('id','filename','size','sha256')}:
                    raise DomainError('Copied attachment metadata changed while inspecting files.',409)
                put(item['path'],item,'file_package' if item.get('file_package') else 'graphics',item['id'])
            if len(set(before)|set(after))>MAX_FILES:raise DomainError('Planned file tree exceeds the bounded reader.',409)
            counts={name:0 for name in ('created','updated','removed','unchanged')};rows=[]
            for key in sorted(set(before)|set(after),key=lambda key:(after.get(key) or before[key])['path']):
                a=before.get(key);b=after.get(key);operation='created' if a is None else 'removed' if b is None else 'unchanged' if checked_meta(a)==checked_meta(b) else 'updated';counts[operation]+=1
                links=[catalog[identifier] for identifier in sorted(owners.get(key,set())) if identifier in catalog]
                rows.append({'path':(b or a)['path'],'change':operation,'kind':kinds.get(key,'inherited'),
                    'before':checked_meta(a) if a else None,'after':checked_meta(b) if b else None,'asset_id':sources.get(key),
                    'resource_count':len(links),'owners_complete':len(links)<=OWNER_LIMIT,
                    'resources':[{name:resource[name] for name in ('id','name','kind','revision')} for resource in links[:OWNER_LIMIT]],
                    '_search':' '.join(resource['name'] for resource in links)})
            summary={'before_files':len(before),'after_files':len(after),'before_bytes':sum(item['size'] for item in before.values()),'after_bytes':sum(item['size'] for item in after.values())}
            if max(summary.values())>MAX_SAFE:raise DomainError('Export totals exceed exact JSON reader integers.',409)
            total=len(rows) if view=='files' else len(private);needle=query.casefold();filtered=0;items=[]
            if view=='private':
                rows=[{'resource_id':resource['id'],'resource_name':resource['name'],'resource_revision':resource['revision'],
                    'reason':reason,'asset_id':identifier,'filename':metadata[identifier]['filename'],**checked_meta(metadata[identifier])}
                    for resource,identifier,reason in sorted(private,key=lambda value:(value[0]['id'],value[1]))]
            for item in rows:
                if view=='files' and change and (item['change']=='unchanged' if change=='changed' else item['change']!=change):continue
                search=' '.join(str(value) for value in item.values()).casefold()
                if needle and needle not in search:continue
                if offset<=filtered<offset+limit:items.append({key:value for key,value in item.items() if key!='_search'})
                filtered+=1
            result={'contract_version':1,'scope':'planned_release_files','complete':True,'read_only':True,
                'nx_files_written':False,'resources_written':False,'automatic_approval':False,'filesystem_atomic_snapshot':False,
                'private_integrity_checked':False,'resource_links':'current_planned_operations_only','owner_limit':OWNER_LIMIT,
                'release_id':release_id,'nx_version':report['nx_version'],'preview_hash':preview_hash,
                'parent_manifest_sha256':report['parent_manifest_sha256'],'after_manifest_sha256':digest(sorted(after.values(),key=lambda item:item['path'])),
                'private_snapshot_sha256':private_pin,'counts':counts,'summary':summary,'private_attachments':len(private),
                'view':view,'change':change,'query':query,'offset':offset,'limit':limit,'total':total,'filtered':filtered,'items':items,
                'note':'Whole-file bytes only, not native-object changes or NX qualification. Private attachment rows are stored metadata, not a fresh byte-integrity check; inherited files can remain after a draft or retirement.'}
            from .store import canonical
            if len(canonical(result).encode('utf-8'))>MAX_PAGE_BYTES:raise DomainError('Export file page exceeds its byte bound; narrow the query.',409)
            return result
