"""Versioned files at NX's designated paths. No script/NX execution or binary rewriting."""
import copy
import json
import re
from pathlib import PurePosixPath

from pydantic import BaseModel,ConfigDict,Field,ValidationError

from .migration import portable,OVERLAYS
from .safety import DomainError,offline_path,asset_filename,export_path_casing,file_fingerprint
from .store import digest,sha256
from .bounded_file import read as read_bounded_file
from .template_refs import parse_xml

CHECKS={'paths','dependencies','content','nx_test'}
UI_PROFILE_ROOT='UGII/menus/profiles/'
# Observed NX2512 ribbon/group/style/layout definitions. Preserve opaque bytes;
# these are not executable attachments or a claim of NX qualification.
UI_PROFILE_SUFFIXES={'.ddb','.rtb','.grb','.gly','.csb','.abr','.stb','.dtx','.crb'}
ROOTS={
    'template':('MACH/resource/template_part/','MACH/resource/template_set/','UGII/templates/'),
    'ui_standard':('UGII/menus/roles/',UI_PROFILE_ROOT),
    'machine':('MACH/resource/library/machine/installed_machines/',),
    'fixture':('MACH/resource/library/fixture_automation/graphics/',),
}
SUFFIXES={'.prt','.pax','.mtx','.jpg','.jpeg','.png','.opt','.xml','.dpv','.men','.tbr','.ribbon'}
MAX_BYTES=20*1024*1024
MAX_PREFLIGHT_BYTES=128*1024*1024
MAX_PATH_CHARS=240
# Baselines are compared, not copied as package attachments. Do not impose the
# 20 MiB attachment limit on legitimate larger Siemens stock files.
MAX_BASELINE_BYTES=1<<40
XML_SUFFIXES={'.pax','.xml','.mtx','.dpv'}


def file_content(path,metadata):
    return read_bounded_file(path,max_bytes=MAX_BYTES,expected_size=metadata['size'],expected_sha256=metadata['sha256'],
        changed_message='Package attachment changed.',limit_message='Package file must contain 1 byte to 20 MiB.')


def checked_xml(content,filename):
    try:parse_xml(content,label='Package')
    except DomainError as error:raise DomainError('Package XML is not well formed or supported: '+filename+' ('+str(error)+')') from error


class Entry(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    asset_id:str=Field(pattern=r'^[a-f0-9-]{36}$')
    path:str=Field(min_length=1,max_length=MAX_PATH_CHARS)


class Package(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    nx_version:str=Field(pattern=r'^[0-9]{4}$')
    entries:list[Entry]=Field(min_length=1,max_length=100)
    checks:dict[str,bool]=Field(default_factory=dict)
    evidence:str=Field(default='',max_length=4000)
    asset_hashes:dict[str,str]=Field(default_factory=dict)
    provenance:dict|None=None


def supported_file(kind,path):
    """Profile definitions belong only to the designated UI profile tree."""
    suffix=PurePosixPath(path).suffix.lower()
    return (any(path.startswith(root) for root in ROOTS[kind]) and
            (suffix in SUFFIXES or kind=='ui_standard' and path.startswith(UI_PROFILE_ROOT) and suffix in UI_PROFILE_SUFFIXES))


def path_for(kind,path):
    if kind not in ROOTS:raise DomainError('This kind has no supported NX file-package layout.')
    if not isinstance(path,str) or not 1<=len(path)<=MAX_PATH_CHARS:
        raise DomainError(f'Package path must contain 1-{MAX_PATH_CHARS} characters.')
    portable(path)
    if not supported_file(kind,path):
        raise DomainError('Choose an approved standard NX directory and non-executable file extension.')
    for part in path.split('/'):asset_filename(part)
    return path


def validate(store,resource,old=None,trusted=False):
    value=resource.get('file_package')
    if value is None:
        if resource['kind'] in ('template','ui_standard') and resource['cam_status']=='approved':
            raise DomainError('A template/UI standard needs a checked file package before approval.')
        return None
    try:package=Package.model_validate(value).model_dump()
    except ValidationError as error:raise DomainError('Invalid NX file-package structure: '+str([list(item['loc']) for item in error.errors()[:8]])) from error
    previous=(old or {}).get('file_package') or {}
    if package['provenance'] and not trusted and package['provenance']!=previous.get('provenance'):
        raise DomainError('File-package provenance is assigned only by reviewed existing-file adoption.')
    if previous.get('provenance') and package['provenance']!=previous['provenance']:
        raise DomainError('Imported file ownership is immutable; retain its provenance.')
    if previous and previous.get('nx_version')!=package['nx_version']:
        if any(package['checks'].values()) or package['asset_hashes']:raise DomainError('Version change requires cleared package checks/hashes and a new NX qualification.')
    if resource.get('origin') and not resource['origin'].get('editable'):
        raise DomainError('Imported Siemens standard objects cannot claim file-package ownership.')
    if resource['kind'] in ('template','ui_standard') and resource['cam']:
        raise DomainError('Templates/UI standards use file packages, not guessed native DAT records.')
    if resource.get('file_bindings') or resource.get('model_package'):
        raise DomainError('Use one explicit file-binding mechanism; clear old graphic/model bindings before adopting a file package.')
    names=set();assets=set()
    export_path_casing(path_for(resource['kind'],entry['path']) for entry in package['entries'])
    for entry in package['entries']:
        path_for(resource['kind'],entry['path']);key=entry['path'].casefold()
        if key in names or entry['asset_id'] in assets:raise DomainError('Repeated package path/asset assignment.')
        names.add(key);assets.add(entry['asset_id'])
        if entry['asset_id'] not in resource['asset_ids']:raise DomainError('Package files must be attached to this resource.')
        metadata,file=store.asset(entry['asset_id'])
        if PurePosixPath(entry['path']).name!=metadata['filename']:raise DomainError('Keep the original basename; binary references are never renamed.')
        content=file_content(file,metadata)
        if file.suffix.lower() in XML_SUFFIXES:checked_xml(content,metadata['filename'])
    if resource['cam_status']=='approved':
        if set(package['checks'])!=CHECKS or not all(value is True for value in package['checks'].values()) or len(package['evidence'].strip())<20:
            raise DomainError('Confirm all four package checks and provide a meaningful NX test statement.')
        expected={entry['asset_id']:store.asset(entry['asset_id'])[0]['sha256'] for entry in package['entries']}
        if package['asset_hashes']!=expected:raise DomainError('File-package checks must be bound to exactly these attachment hashes.')
    return package


def preflight(store,request):
    """Technical editor inspection only; never assigns ownership or qualification."""
    kind=request['kind'];entries=request['entries'];attached=request['asset_ids']
    if kind not in ROOTS:raise DomainError('Unsupported file-package kind.')
    import uuid
    try:
        if any(str(uuid.UUID(identifier))!=identifier for identifier in attached):raise ValueError()
    except (ValueError,TypeError,AttributeError) as failure:raise DomainError('Invalid attached file identifier.') from failure
    errors=[];files=[];metadata={};total=0;paths=set();asset_ids=set();portable_paths=[]
    def error(index,path,code,reason):errors.append({'index':index,'path':path,'code':code,'reason':reason})
    def read_metadata(identifier):
        # Do not use store.asset here: its legacy verifier hashes the full file
        # before this preflight's per-file and aggregate read bounds can apply.
        with store.db() as db:row=db.execute('SELECT * FROM assets WHERE id=?',(identifier,)).fetchone()
        if row is None:raise DomainError('Asset not found.',404)
        value=dict(row);asset_filename(value['filename'])
        path=offline_path(store.root/'assets'/identifier/value['filename'])
        return value,path
    with store.lock:
        if not entries:error(None,None,'empty','Choose at least one package file.')
        if len(attached)!=len(set(attached)):error(None,None,'attachments','Attached file identifiers must be distinct.')
        for index,entry in enumerate(entries):
            identifier,path=entry['asset_id'],entry['path']
            item={'index':index,'asset_id':identifier,'path':path,'filename':None,'size':None,'sha256':None,'bytes_verified':False,'xml_checked':None}
            files.append(item)
            try:path_for(kind,path);portable_paths.append(path)
            except DomainError as failure:error(index,path,'path',str(failure))
            if path.casefold() in paths:error(index,path,'duplicate_path','Repeated case-insensitive package path.')
            paths.add(path.casefold())
            if identifier in asset_ids:error(index,path,'duplicate_asset','One attachment may not be assigned to several package entries.')
            asset_ids.add(identifier)
            if identifier not in attached:
                error(index,path,'not_attached','Package file must be attached to this editor resource.');continue
            try:
                if identifier not in metadata:metadata[identifier]=read_metadata(identifier)
                value,_=metadata[identifier];item.update(filename=value['filename'],size=value['size'],sha256=value['sha256'])
                if PurePosixPath(path).name!=value['filename']:error(index,path,'basename','Keep the original attachment basename; binary references are not renamed.')
                if not 1<=value['size']<=MAX_BYTES:error(index,path,'size','Package file must contain 1 byte to 20 MiB.')
            except DomainError as failure:error(index,path,'attachment',str(failure))
        try:export_path_casing(portable_paths)
        except DomainError as failure:error(None,None,'tree',str(failure))
        total=sum(value['size'] for value,_ in metadata.values())
        if total>MAX_PREFLIGHT_BYTES:error(None,None,'byte_budget','Package preflight exceeds its total attachment byte bound.')
        else:
            checked={}
            for item in files:
                identifier=item['asset_id']
                if identifier not in metadata:continue
                try:
                    if identifier not in checked:
                        try:
                            value,path=metadata[identifier];raw=file_content(path,value)
                            xml=PurePosixPath(value['filename']).suffix.lower() in XML_SUFFIXES
                            if xml:checked_xml(raw,value['filename'])
                            checked[identifier]=xml
                        except DomainError as failure:checked[identifier]=failure
                    if isinstance(checked[identifier],DomainError):raise checked[identifier]
                    item.update(bytes_verified=True,xml_checked=checked[identifier])
                except DomainError as failure:error(item['index'],item['path'],'content',str(failure))
        # Re-read selected metadata after the last byte check, never private docs.
        for identifier,(expected,path) in metadata.items():
            current,current_path=read_metadata(identifier)
            if current!=expected or str(current_path)!=str(path):raise DomainError('Package attachment metadata changed during preflight.',409)
        report={'contract_version':1,'scope':'file_package_preflight','request_sha256':digest(request),
            'kind':kind,'nx_version':request['nx_version'],'file_count':len(files),'total_bytes':total,
            'private_attachments':len(set(attached)-asset_ids),'files':files,'errors':errors,'can_bind':not errors,
            'read_only':True,'nx_files_written':False,'automatic_approval':False,'automatic_save':False,
            'ownership_validation':False,'dependency_validation':False,'nx_validation':False,
            'max_file_bytes':MAX_BYTES,'max_total_bytes':MAX_PREFLIGHT_BYTES}
        report['preview_hash']=digest(report)
        return report


def claimed_paths(resources,release):
    """Current reservations plus historical ownership in the selected release."""
    claims={};by_id={resource['id']:resource for resource in resources}
    def claim(path,owner_id,revision):
        portable(path);key=path.casefold();existing=claims.get(key)
        if existing and existing['resource_id']!=owner_id:raise DomainError('Conflicting current/release file ownership: '+path,409)
        resource=by_id.get(owner_id,{})
        claims[key]={'resource_id':owner_id,'name':resource.get('name','Historical file owner'),
            'revision':resource.get('revision',revision),'cam_status':resource.get('cam_status'),'historical':not bool(resource)}
    try:
        for resource in resources:
            for entry in (resource.get('file_package') or {}).get('entries',[]):claim(entry['path'],resource['id'],resource['revision'])
        for owner_id,record in (release.get('catalog') or {}).get('exported_file_packages',{}).items():
            for entry in record['entries']:claim(entry['path'],owner_id,record.get('revision'))
    except (TypeError,KeyError,AttributeError) as error:raise DomainError('Invalid stored file ownership metadata.',409) from error
    return claims


def _baseline_candidate(root,relative,*,directory=False):
    """Guard each parent before probing a child below a possible baseline file."""
    portable(relative);parts=relative.split('/')
    try:
        path=offline_path(root)
        for index,part in enumerate(parts):
            path=offline_path(path/part)
            if index<len(parts)-1 or directory:
                if path.exists() and not path.is_dir():raise DomainError('Baseline path has a non-directory ancestor: '+relative,409)
            elif path.is_dir():raise DomainError('Baseline file path is occupied by a directory: '+relative,409)
    except OSError as error:raise DomainError('Baseline package path cannot be safely inspected: '+relative,409) from error
    return path


def _baseline_sha256(path):
    path=offline_path(path)
    if not path.exists():return None
    return file_fingerprint(path,max_bytes=MAX_BASELINE_BYTES)['sha256']


def _baseline_file(root,sidt,kind,relative):
    prefix=next(prefix for prefix in ROOTS[kind] if relative.startswith(prefix))
    if not _baseline_candidate(root,prefix.rstrip('/'),directory=True).is_dir():raise DomainError('Siemens baseline directory is missing; customer ownership cannot be guessed.',409)
    candidate=_baseline_candidate(root,relative)
    for origin,target in OVERLAYS:
        if sidt and relative.startswith(target+'/'):
            overlay=_baseline_candidate(sidt,origin+'/'+relative[len(target)+1:])
            if overlay.is_file():candidate=overlay
    return candidate


def verify_adoption_commit(store,db,report,payload,resource_id):
    """Last input/claim gate inside the resource/result/audit SQL transaction."""
    row=db.execute('SELECT * FROM releases WHERE id=?',(report['release_id'],)).fetchone()
    if row is None:raise DomainError('File adoption source disappeared before ownership commit.',409)
    release=store._release_value(row);state=report['source_state']
    if release['status'] in ('building','failed') or release['nx_version']!=state['nx_version'] or release['folder']!=state['folder'] or release['parent_id']!=state['parent_id'] or digest(release['manifest'])!=report['manifest_sha256'] or digest(release['catalog'])!=state['catalog_sha256']:
        raise DomainError('File adoption source metadata changed before ownership commit.',409)
    current=[store._resource_value(row) for row in db.execute('SELECT * FROM resources ORDER BY id')]
    if digest(claimed_paths(current,release))!=report['ownership_sha256']:
        raise DomainError('File ownership changed before adoption commit.',409)
    parent=next((item for item in current if item['id']==resource_id),None) if resource_id else None
    if resource_id!=report['resource_id'] or digest(parent)!=report['parent_sha256']:
        raise DomainError('File adoption parent contents changed before ownership commit.',409)
    package=payload.get('file_package') or {};provenance=package.get('provenance') or {}
    if payload['kind']!=report['kind'] or payload['cam_status']!='draft' or package.get('nx_version')!=state['nx_version'] or [item['path'] for item in package.get('entries',[])]!=[item['path'] for item in report['entries']] or provenance.get('release_id')!=release['id'] or provenance.get('manifest_sha256')!=report['manifest_sha256'] or provenance.get('owned_files')!=report['entries'] or provenance.get('baseline_inputs')!=report['baseline_inputs']:
        raise DomainError('File adoption payload differs from its reviewed ownership context.',409)
    root=store.standards.get(release['nx_version'])
    # The effective provider can be an explicitly selected extraction even
    # without a legacy configured SIDT directory. Recheck it at commit too.
    sidt=store.sidt_root(release['nx_version'])
    if root is None or str(root)!=state['baseline_root'] or (str(sidt) if sidt else None)!=state['sidt_root']:
        raise DomainError('File adoption baseline roots changed before ownership commit.',409)
    folder=offline_path(release['folder'])
    for item in report['entries']:
        path=offline_path(folder/item['path'])
        try:file_content(path,item)
        except DomainError as error:raise DomainError('File adoption source bytes changed before ownership commit: '+str(error),409) from error
        try:
            baseline=_baseline_file(root,sidt,report['kind'],item['path']);expected=report['baseline_inputs'][item['path']]
            if str(baseline)!=expected['baseline_path'] or _baseline_sha256(baseline)!=expected['sha256']:
                raise DomainError('Baseline differs from its reviewed binding.',409)
        except DomainError as error:raise DomainError('File adoption baseline bytes changed before ownership commit: '+str(error),409) from error


class FilePackages:
    def __init__(self,store):self.store=store

    def adoption_preview(self,release_id,kind,paths,resource_id=None):
        if kind not in ROOTS or not paths or len(paths)>100 or len(set(p.casefold() for p in paths))!=len(paths):
            raise DomainError('Select 1-100 unique files of one supported package kind.')
        release=self.store.get_release(release_id);folder=self.store.release_folder(release_id)
        if release['status'] in ('failed','building'):raise DomainError('Choose a complete managed release.')
        parent=next((r for r in self.store.resources() if r['id']==resource_id),None) if resource_id else None
        if resource_id and (not parent or parent['kind']!=kind or parent['cam_status']!='draft' or parent.get('file_package')):
            raise DomainError('Adopt into a matching current draft without an existing file package.')
        if parent and parent.get('origin') and not parent['origin'].get('editable'):
            raise DomainError('A Siemens standard object cannot own customer files.')
        if parent and (parent.get('file_bindings') or parent.get('model_package')):
            raise DomainError('Existing explicit graphic/model bindings must be reviewed and cleared before file-package adoption.')
        if kind in ('machine','fixture') and not parent:raise DomainError('Choose the saved machine/fixture DAT draft; no native object is invented.')
        root=self.store.standards.get(release['nx_version'])
        if root is None:raise DomainError('A matching Siemens installation baseline is required for existing-file ownership.')
        sidt=self.store.sidt_root(release['nx_version'])
        inputs={};entries=[];conflicts=[];known={item['path'].casefold():item for item in release['manifest']}
        owners=claimed_paths(self.store.resources(),release)
        for relative in paths:
            path_for(kind,relative);item=known.get(relative.casefold())
            if not item or item['path']!=relative:raise DomainError('Choose the exact canonical path from this release manifest, not a case alias.',409)
        try:export_path_casing(paths)
        except DomainError as error:conflicts.append({'path':'package_tree','reason':str(error)})
        for relative in paths:
            item=known[relative.casefold()]
            try:
                source=offline_path(folder/relative)
                if not source.is_file():raise DomainError('Selected managed file is absent/changed.',409)
            except OSError as error:raise DomainError('Selected managed file is absent or unreadable.',409) from error
            if not 1<=item['size']<=MAX_BYTES:conflicts.append({'path':relative,'reason':'File must contain 1 byte to 20 MiB.'})
            else:
                raw=file_content(source,item)
                if source.suffix.lower() in XML_SUFFIXES:
                    try:checked_xml(raw,PurePosixPath(relative).name)
                    except DomainError as error:conflicts.append({'path':relative,'reason':str(error)})
            try:
                candidate=_baseline_file(root,sidt,kind,relative);stock=_baseline_sha256(candidate)
                inputs[relative]={'sha256':stock,'baseline_path':str(candidate)}
                if stock==item['sha256']:conflicts.append({'path':relative,'reason':'Unchanged Siemens/SIDT standard is not editable customer ownership.'})
            except DomainError as error:
                inputs[relative]={'sha256':None,'baseline_path':None,'read_error':str(error)}
                conflicts.append({'path':relative,'reason':str(error)})
            if relative.casefold() in owners:conflicts.append({'path':relative,'reason':'File is already claimed by another managed resource.'})
            entries.append(item)
        if len(paths)+len(parent['asset_ids'] if parent else [])>100:conflicts.append({'path':'attachments','reason':'Combined attachments exceed 100; existing assignments are retained.'})
        report={'release_id':release_id,'kind':kind,'resource_id':resource_id,'expected_revision':parent['revision'] if parent else None,
            'commit_contract_version':1,'ownership_sha256':digest(owners),'parent_sha256':digest(parent),
            'source_state':{'nx_version':release['nx_version'],'folder':str(folder),'parent_id':release['parent_id'],
                'catalog_sha256':digest(release['catalog']),'baseline_root':str(root),'sidt_root':str(sidt) if sidt else None},
            'entries':entries,'baseline_inputs':inputs,'manifest_sha256':digest(release['manifest']),
            'can_adopt':not conflicts,'conflicts':conflicts,'status_after_adoption':'draft','automatic_approval':False}
        report['preview_hash']=digest([report,parent]);return report,parent

    def adopt(self,release_id,kind,paths,name,resource_id,preview_hash,actor,operation_job_id=None,progress=None):
        with self.store.lock:
            report,parent=self.adoption_preview(release_id,kind,paths,resource_id)
            if not report['can_adopt'] or report['preview_hash']!=preview_hash:raise DomainError('File adoption preview changed or has conflicts.',409)
            assets=[];entries=[];folder=self.store.release_folder(release_id)
            for index,item in enumerate(report['entries']):
                if progress:progress('building',10+int(70*index/len(report['entries'])),f'{index+1} / {len(report["entries"])} Kunden-Dateien als unveraenderliche Anhaenge kopieren')
                content=file_content(offline_path(folder/item['path']),item);filename=PurePosixPath(item['path']).name
                asset=self.store.add_asset(filename,content,actor,operation_job_id=operation_job_id) if operation_job_id is not None else self.store.add_asset(filename,content,actor)
                if asset['sha256']!=item['sha256']:raise DomainError('Existing file changed while adopting.',409)
                assets.append(asset);entries.append({'asset_id':asset['id'],'path':item['path']})
            if progress:progress('verifying',85,'Pruefe Quellen, Baseline und Besitz vor Entwurfscommit nochmals')
            fresh,_=self.adoption_preview(release_id,kind,paths,resource_id)
            if fresh['preview_hash']!=preview_hash or not fresh['can_adopt']:raise DomainError('File adoption inputs changed during copying; attachments retained.',409)
            value={key:copy.deepcopy(parent[key]) for key in ('kind','name','cam','erp','cam_status','asset_ids','geometry','origin','file_bindings','model_package') if key in parent} if parent else {
                'kind':kind,'name':name,'cam':{},'erp':None,'cam_status':'draft','asset_ids':[],'origin':None}
            if len(value['asset_ids'])+len(assets)>100:raise DomainError('Combined attachments exceed 100; existing assignments are never silently removed.')
            value['asset_ids']+= [asset['id'] for asset in assets]
            value['file_package']={'nx_version':self.store.get_release(release_id)['nx_version'],'entries':entries,'checks':{},'evidence':'','asset_hashes':{},
                'provenance':{'release_id':release_id,'manifest_sha256':report['manifest_sha256'],'owned_files':report['entries'],'baseline_inputs':report['baseline_inputs']}}
            return self.store.save_resource(value,actor,parent['id'] if parent else None,parent['revision'] if parent else None,trusted_file_package=True,operation_job_id=operation_job_id,file_adoption_guard=report)

    def inventory(self,release_id,kind,query='',offset=0,limit=100):
        if kind not in ROOTS:raise DomainError('Unknown file-package category.')
        if not isinstance(query,str) or len(query)>200 or type(offset) is not int or not 0<=offset<=1000000 or type(limit) is not int or not 1<=limit<=500:
            raise DomainError('Choose a query up to 200 characters, nonnegative bounded offset and page size 1-500.')
        with self.store.db() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT * FROM releases WHERE id=?',(release_id,)).fetchone()
            resource_rows=db.execute('SELECT * FROM resources ORDER BY id').fetchall()
            head=db.execute('SELECT sequence,hash FROM audit ORDER BY sequence DESC LIMIT 1').fetchone()
        if not row:raise DomainError('Release not found.',404)
        self.store._release_path(row['id'],row['folder'])  # Existing protected-path guard, no file inventory.
        if row['status'] in ('failed','building'):raise DomainError('Choose a complete managed release.',409)
        try:
            release={'catalog':json.loads(row['catalog']) if row['catalog'] else {},'manifest':json.loads(row['manifest'])}
            if not isinstance(release['manifest'],list) or not isinstance(release['catalog'],dict):raise ValueError()
            files=[];seen=set()
            for item in release['manifest']:
                path=item['path'];portable(path);key=path.casefold()
                if key in seen or type(item['size']) is not int or item['size']<0 or not re.fullmatch('[a-f0-9]{64}',item['sha256']):raise ValueError()
                seen.add(key)
                if supported_file(kind,path):
                    files.append(item)
        except (ValueError,TypeError,KeyError,AttributeError,DomainError) as error:
            raise DomainError('Stored file manifest/catalog metadata is invalid.',409) from error
        resources=[self.store._resource_value(resource) for resource in resource_rows];owners=claimed_paths(resources,release)
        files.sort(key=lambda item:item['path'].casefold());filtered=[item for item in files if query.casefold() in item['path'].casefold()]
        page=[]
        for item in filtered[offset:offset+limit]:
            owner=owners.get(item['path'].casefold());reason=None
            try:path_for(kind,item['path'])
            except DomainError as error:reason=str(error)
            if not 1<=item['size']<=20*1024*1024:reason='File must contain 1 byte to 20 MiB.'
            if owner:reason='File is already reserved by a managed resource.'
            page.append({**item,'owner':owner,'selectable':reason is None,'unavailable_reason':reason})
        return {'contract_version':2,'release_id':release_id,'kind':kind,'nx_version':row['nx_version'],'roots':ROOTS[kind],
            'query':query,'offset':offset,'limit':limit,'total':len(files),'filtered':len(filtered),
            'manifest_sha256':digest(release['manifest']),'ownership_sha256':digest(owners),
            'snapshot':{'consistency':'sqlite_read_transaction','audit_head':dict(head) if head else {'sequence':0,'hash':'0'*64},
                'files_verified':False,'baseline_compared':False,'automatic_approval':False},
            'files':page}


def adopted_ownership(store,release,package):
    provenance=package.get('provenance')
    if not provenance:return {}
    origin=store.get_release(provenance['release_id']);lineage={release['id']};cursor=release
    while cursor['parent_id']:
        if cursor['parent_id'] in lineage:raise DomainError('Release history contains a cycle; ownership cannot be established.',409)
        lineage.add(cursor['parent_id']);cursor=store.get_release(cursor['parent_id'])
    if origin['id'] in lineage and digest(origin['manifest'])==provenance['manifest_sha256']:
        return {item['path'].casefold():item for item in provenance['owned_files']}
    return {}


def rebase_ownership(store,source,manifest,target_version):
    """Retain byte-proven ownership, not qualification, across a reviewed upgrade."""
    records=copy.deepcopy((source['catalog'] or {}).get('exported_file_packages',{}))
    for resource in store.resources():
        if resource['id'] in records or not resource.get('file_package'):continue
        owned=adopted_ownership(store,source,resource['file_package'])
        if owned:records[resource['id']]={'revision':resource['revision'],'entries':list(owned.values()),'nx_version':source['nx_version']}
    old={item['path'].casefold():item for item in source['manifest']};new={item['path'].casefold():item for item in manifest}
    for record in records.values():
        retained=[];excluded=record.setdefault('exclusions',[])
        for item in record['entries']:
            key=item['path'].casefold();previous=old.get(key);current=new.get(key)
            if not previous or previous['sha256']!=item['sha256'] or previous['size']!=item['size']:
                raise DomainError('Source file-package ownership differs from its immutable source manifest.',409)
            if current and current['sha256']==item['sha256'] and current['size']==item['size']:retained.append(current)
            else:excluded.append({'path':item['path'],'reason':'File omitted/replaced during reviewed upgrade; do not automatically restore it.'})
        record.update(entries=retained,nx_version=target_version,qualification_retained=False)
    return records


def plan_export(store,release,catalog,previous,plan):
    records=copy.deepcopy(previous.get('exported_file_packages',{}));actions=[]
    known={item['path'].casefold():item for item in release['manifest']};claims={}
    for resource in catalog:
        package=resource.get('file_package')
        if not package:continue
        for entry in package['entries']:
            key=entry['path'].casefold()
            if key in claims and claims[key]!=resource['id']:raise DomainError('Multiple resources claim one package file: '+entry['path'],409)
            claims[key]=resource['id']
    for resource in catalog:
        package=resource.get('file_package');old=records.get(resource['id'])
        if not package:continue
        if resource['cam_status']=='draft':actions.append({'resource_id':resource['id'],'action':'deferred','files':0});continue
        package=validate(store,resource,old=resource)
        if package['nx_version']!=release['nx_version']:raise DomainError('File-package NX version differs from target; qualify a new revision.',409)
        owned={item['path'].casefold():item for item in (old or {}).get('entries',[])}
        if not old:owned=adopted_ownership(store,release,package)
        wanted={entry['path'].casefold():entry for entry in package['entries']} if resource['cam_status']=='approved' else {}
        for key,item in owned.items():
            current=known.get(key)
            if current and current['sha256']!=item['sha256']:raise DomainError('Previously owned file changed; refusing overwrite/delete: '+item['path'],409)
            if key not in wanted and current:
                plan.setdefault('file_deletions',[]).append(item)
                plan.setdefault('file_owners',{}).setdefault(key,set()).add(resource['id'])
        exported=[]
        for key,entry in wanted.items():
            metadata,source=store.asset(entry['asset_id']);current=known.get(key)
            if any(key==item['path'].casefold() for item in (old or {}).get('exclusions',[])):
                raise DomainError('Package file was omitted/replaced during upgrade; rerun the upgrade with an explicit customer-file choice: '+entry['path'],409)
            if current and key not in owned:raise DomainError('Existing unowned/standard file cannot be overwritten or claimed: '+entry['path'],409)
            if current and current['path']!=entry['path']:raise DomainError('Keep the exact managed relative-path casing; binary references are not renamed.',409)
            if key in plan['assets']:raise DomainError('Package path is already claimed by another planned graphic.',409)
            exported.append({'path':entry['path'],'size':metadata['size'],'sha256':metadata['sha256']})
            plan['assets'][key]={**metadata,'path':entry['path'],'source':source,'action':'updated' if current and current['sha256']!=metadata['sha256'] else 'unchanged' if current else 'created',
                'previous_sha256':current['sha256'] if current else None,'file_package':True}
            plan.setdefault('file_owners',{}).setdefault(key,set()).add(resource['id'])
            plan.setdefault('asset_assignments',{}).setdefault(resource['id'],set()).add(entry['asset_id'])
        records[resource['id']]={'revision':resource['revision'],'entries':exported,'nx_version':package['nx_version'],
            'exclusions':(old or {}).get('exclusions',[])}
        actions.append({'resource_id':resource['id'],'action':'retired' if resource['cam_status']=='retired' else 'exported','files':len(exported)})
    plan['exported_file_packages']=records;return actions
