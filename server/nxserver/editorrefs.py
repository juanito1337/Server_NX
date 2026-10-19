"""Manifest-bound reference lookup and explicit editor-only changes. No writes."""
import copy

from .libraries import digest,reference_targets,spec_for
from .safety import DomainError
from .schemas import _source,_context,_target


def release_context(store,identifier):
    release=store.get_release(identifier)
    if release['status'] in ('building','failed') or not release.get('manifest'):
        raise DomainError('Reference lookup requires a completed, manifest-bound NX release.',409)
    return release


def target_table(store,release,identifier):
    relative=spec_for(identifier)['path']
    entries=[item for item in release['manifest'] if item['path'].casefold()==relative.casefold()]
    if not entries:return None,None
    if len(entries)!=1:raise DomainError('Reference target has ambiguous manifest entries.',409)
    current,table,fingerprint=_target(store,release['id'],identifier)
    if current['folder']!=release['folder'] or current['nx_version']!=release['nx_version'] or current['manifest']!=release['manifest']:
        raise DomainError('Reference release changed while reading its libraries.',409)
    return table,fingerprint


def label(records,key):
    return next((record['values'][field] for record in records for field in ('MATNAM','MATNAME','MATDESC','DESCR','NAME')
                 if record['values'].get(field)),key)


def options(store,release_id,library_id,field,query='',offset=0,limit=100):
    if not isinstance(query,str) or len(query)>200 or type(offset) is not int or not 0<=offset<=1_000_000 or type(limit) is not int or not 1<=limit<=100:
        raise DomainError('Invalid reference search/page bounds.')
    target=reference_targets(library_id).get(field)
    if target is None:raise DomainError('This field is not a registered relation in this source library.')
    release=release_context(store,release_id);table,fingerprint=target_table(store,release,target)
    if table is None:raise DomainError('The chosen NX release does not contain the referenced active library '+target+'.',409)
    groups=table.groups()
    entries=[{'key':key,'label':label(records,key),'rows':len(records)} for key,records in groups.items()]
    filtered=[item for item in entries if query.casefold() in (item['key']+'\n'+item['label']).casefold()]
    return {'contract_version':1,'release_id':release_id,'nx_version':release['nx_version'],
        'library_id':library_id,'field':field,'target_library':target,
        'input_sha256':fingerprint,'manifest_sha256':digest(release['manifest']),
        'total':len(entries),'filtered':len(filtered),'offset':offset,'limit':limit,
        'items':filtered[offset:offset+limit],'integrity_checked':True,
        'scope':'selected_native_library_bytes','automatic_assignment':False,'nx_execution_verified':False}


def plan(store,request):
    native,identifier,source,records=_source(request['kind'],request['cam'])
    identity=(identifier,next(iter(source.groups())))
    _context(store,request,identity,native)
    release=release_context(store,request['target_release_id'])
    relations=reference_targets(identifier);tables={};fingerprints={};rows=[]
    for index,record in enumerate(records):
        for field,target in relations.items():
            if field not in record['values']:continue
            if target not in tables:
                table,fingerprint=target_table(store,release,target)
                tables[target]=table.groups() if table is not None else None
                fingerprints[target]=fingerprint
            value=record['values'][field];groups=tables[target]
            status='empty' if not value else 'target_missing' if groups is None else 'present' if value in groups else 'missing'
            rows.append({'row_index':index,'block_id':record['block_id'],'field':field,'value':value,
                'target_library':target,'status':status,'target_objects':len(groups) if groups is not None else 0,
                'label':label(groups[value],value) if groups is not None and value in groups else None})
    report={'contract_version':1,'library_id':identifier,'key':identity[1],
        'resource_id':request.get('resource_id'),'expected_revision':request.get('expected_revision'),
        'target_release_id':release['id'],'target_nx_version':release['nx_version'],
        'source_sha256':digest(request['cam']),'target_manifest_sha256':digest(release['manifest']),
        'target_inputs':fingerprints,'rows':rows,'unresolved':sum(row['status'] in ('missing','target_missing') for row in rows),
        'automatic_assignment':False,'saved':False,'nx_files_written':False,'geometry_verified':False,
        'schema_conversion_performed':False}
    report['preview_hash']=digest(report)
    return report


def apply(store,request,preview_hash,row_index,field,value):
    report=plan(store,request)
    if report['preview_hash']!=preview_hash:raise DomainError('Reference preview changed. Review current editor and target libraries again.',409)
    if type(row_index) is not int or row_index<0 or not isinstance(field,str) or not isinstance(value,str) or not 1<=len(value)<=1024:
        raise DomainError('Choose one existing reference row and an exact target key.')
    chosen=next((row for row in report['rows'] if row['row_index']==row_index and row['field']==field),None)
    if chosen is None:raise DomainError('The chosen editor row/field is not a registered relation.',409)
    if value==chosen['value']:raise DomainError('This exact key is already present; no editor change required.',409)
    release=release_context(store,request['target_release_id'])
    table,fingerprint=target_table(store,release,chosen['target_library'])
    if table is None or fingerprint!=report['target_inputs'][chosen['target_library']] or digest(release['manifest'])!=report['target_manifest_sha256']:
        raise DomainError('Reference target changed before applying editor values.',409)
    if value not in table.groups():raise DomainError('Choose an exact active key from the target library; similar keys are not aliases.',409)
    # A separate catalog writer cannot make this editor operation look current.
    native,identifier,source,records=_source(request['kind'],request['cam'])
    _context(store,request,(identifier,next(iter(source.groups()))),native)
    cam=copy.deepcopy(request['cam'])
    values=cam['records'][row_index]['values'] if native else cam['values']
    previous=values[field];values[field]=value
    return {'contract_version':1,'cam':cam,'row_index':row_index,'field':field,'previous':previous,'value':value,
        'target_library':chosen['target_library'],'target_release_id':release['id'],
        'preview_hash':preview_hash,'draft_required':True,'geometry_recheck_required':True,
        'saved':False,'nx_files_written':False,'schema_conversion_performed':False}
