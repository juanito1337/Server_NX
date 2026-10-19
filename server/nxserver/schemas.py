"""Read-only, explicit catalog schema adaptation. No NX/share writes or approvals."""
import copy
import hashlib
import json

from .libraries import cam_table, decode, digest, legacy_cam_table, native_cam, NativeTable, spec_for
from .safety import DomainError, offline_path
from .store import resource_identity
from .bounded_file import read as read_bounded_file

MAX_ROWS = 1000
MAX_CELLS = 100000
MAX_LIBRARY = 32*1024*1024


def _source(kind,cam):
    try:
        native=bool(cam.get('library_id'))
        if native:
            raw_records=cam['records']
            if not isinstance(raw_records,list) or len(raw_records)>MAX_ROWS:
                raise DomainError('Schema adaptation exceeds the row limit or has invalid records.')
            if sum(len(record['values']) for record in raw_records)>MAX_CELLS:
                raise DomainError('Schema adaptation exceeds the field limit.')
        source=cam_table(cam) if native else legacy_cam_table(cam)
        identifier=cam['library_id'] if native else 'tool-metric'
        if kind!=spec_for(identifier)['kind']:raise DomainError('Catalog kind and source library differ.')
        records=cam['records'] if native else [{'block_id':source.blocks[0]['id'],**cam}]
        if len(records)>MAX_ROWS or sum(len(record['values']) for record in records)>MAX_CELLS:
            raise DomainError('Schema adaptation exceeds the row/field limit.')
        return native,identifier,source,records
    except (KeyError,TypeError,AttributeError,ValueError) as error:
        raise DomainError('Invalid source CAM schema/records.') from error


def _context(store,request,identity,native):
    identifier,expected=request.get('resource_id'),request.get('expected_revision')
    if bool(identifier)!=(expected is not None):raise DomainError('Resource ID and expected revision must be provided together.')
    if not identifier:return
    with store.db() as db:
        row=db.execute('SELECT revision,payload FROM resources WHERE id=?',(identifier,)).fetchone()
        if not row:raise DomainError('Resource not found.',404)
        if row['revision']!=expected:raise DomainError('Resource changed since it was opened. Reload before adapting.',409)
        old=json.loads(row['payload'])
        if old['kind']!=request['kind']:raise DomainError('Catalog kind is immutable.',409)
        tracked=bool(old.get('origin'))
        for release in db.execute('SELECT catalog FROM releases WHERE catalog IS NOT NULL'):
            catalog=json.loads(release['catalog'])
            tracked=tracked or identifier in catalog.get('exported_native',{}) or identifier in catalog.get('exported_tools',{})
        if tracked and (resource_identity(old)!=identity or bool(old.get('cam',{}).get('library_id'))!=native):
            raise DomainError('Imported/exported identifiers and CAM representation must stay unchanged.',409)


def _target(store,release_id,identifier):
    release=store.get_release(release_id)
    if release['status'] in ('building','failed') or not release['manifest']:
        raise DomainError('Target schema requires a completed, manifest-bound release.',409)
    relative=spec_for(identifier)['path']
    expected=[item for item in release['manifest'] if item['path'].casefold()==relative.casefold()]
    if len(expected)!=1:raise DomainError('Target release has no manifest-bound library '+identifier+'.',409)
    if expected[0]['size']>MAX_LIBRARY:raise DomainError('Target library exceeds the schema reader bound.')
    path=offline_path(store.release_folder(release_id)/relative)
    raw=read_bounded_file(path,max_bytes=MAX_LIBRARY,expected_size=expected[0]['size'],expected_sha256=expected[0]['sha256'],
        changed_message='Target schema differs from its release manifest.',limit_message='Target library exceeds the schema reader bound.')
    fingerprint=hashlib.sha256(raw).hexdigest()
    if len(raw)!=expected[0]['size'] or fingerprint!=expected[0]['sha256']:
        raise DomainError('Target schema differs from its release manifest.',409)
    table=NativeTable(decode(raw))
    if len(table.blocks)>MAX_ROWS or sum(len(block['fields']) for block in table.blocks)>MAX_CELLS:
        raise DomainError('Target schema exceeds the block/field limit.')
    return release,table,fingerprint


def plan(store,request):
    """Decisions are per row; no class/block or removed-column guesses."""
    native,identifier,source,records=_source(request['kind'],request['cam'])
    identity=(identifier,next(iter(source.groups())))
    _context(store,request,identity,native)
    release,target,fingerprint=_target(store,request['target_release_id'],identifier)
    return _plan(request,native,identifier,source,records,release,target,fingerprint)


def _plan(request,native,identifier,source,records,release,target,fingerprint):
    """Shared pure projection; callers own their read context, never persistence."""
    identity=(identifier,next(iter(source.groups())))
    decisions=request.get('decisions',{})
    if set(decisions)-{str(index) for index in range(len(records))}:
        raise DomainError('Schema decisions contain an unknown row index.')
    rows=[];conflicts=[];output=[];projected_cells=0
    counts={'kept':0,'renamed':0,'discarded':0,'added':0,'class_changes':0}
    for index,record in enumerate(records):
        old=source.block(record['block_id']);decision=decisions.get(str(index),{})
        fields=decision.get('field_map',{});discard=decision.get('discard_fields',[])
        provided=decision.get('values',{})
        key_field='LIBRF' if 'LIBRF' in old['fields'] else 'NAME'
        if set(fields)-set(old['fields']) or set(discard)-set(old['fields']) or len(discard)!=len(set(discard)):
            raise DomainError('Field decisions must name distinct existing source columns.')
        if set(fields)&set(discard):raise DomainError('A source column cannot be mapped and discarded together.')
        if key_field in discard or (key_field in fields and fields[key_field]!=key_field):
            raise DomainError('The library identifier column may not be renamed/discarded.',409)
        chosen=decision.get('target_block_id')
        candidates=[block for block in target.blocks if block['class_name']==old['class_name']]
        if chosen:
            matches=[block for block in target.blocks if block['id']==chosen]
            if len(matches)!=1:raise DomainError('Chosen target FORMAT block does not exist.')
            new=matches[0]
        else:
            desired={fields.get(field,field) for field in old['fields'] if field not in discard}
            compatible=[block for block in candidates if desired<=set(block['fields'])]
            if len(compatible)>1 and 'RTYPE' in record['values']:
                compatible=[block for block in compatible if record['values']['RTYPE'] in
                    {item['values'].get('RTYPE') for item in target.records if item['block_id']==block['id']}]
            new=compatible[0] if len(compatible)==1 else candidates[0] if len(candidates)==1 else None
        row={'index':index,'source_block':old,'source_values':copy.deepcopy(record['values']),
             'target_block':new,'candidate_ids':[block['id'] for block in candidates],'fields':[],'new_fields':[],'conflicts':[]}
        def conflict(reason):row['conflicts'].append(reason);conflicts.append({'index':index,'reason':reason})
        if not new:
            conflict('Target FORMAT block is missing/ambiguous; choose explicitly.')
            if fields or discard or provided:raise DomainError('Choose a target block before making field decisions.')
            rows.append(row);continue
        projected_cells+=len(new['fields'])
        if projected_cells>MAX_CELLS:raise DomainError('Projected schema exceeds the field limit.')
        new_key='LIBRF' if 'LIBRF' in new['fields'] else 'NAME'
        if new_key!=key_field:conflict('Identifier-column changes require a dedicated library adapter.')
        if old['class_name']!=new['class_name']:
            counts['class_changes']+=1
            if not decision.get('accept_class_change',False):conflict('Changed NX class must be explicitly confirmed.')
        if set(fields.values())-set(new['fields']) or set(provided)-set(new['fields']):
            raise DomainError('Field decisions contain unknown target columns.')
        values={}
        for field in old['fields']:
            destination=fields.get(field,field)
            status='discarded' if field in discard else 'unmapped' if destination not in new['fields'] else 'renamed' if destination!=field else 'kept'
            row['fields'].append({'source':field,'target':destination if status in ('kept','renamed') else None,
                'value':record['values'][field],'status':status,'identifier':field==key_field})
            if status=='unmapped':conflict('Explicit mapping/discard decision required for '+field+'.')
            elif status=='discarded':counts['discarded']+=1
            else:
                if destination in values:conflict('Multiple source columns map to '+destination+'.')
                values[destination]=record['values'][field];counts[status]+=1
        if set(provided)&set(values):raise DomainError('New-column values may not overwrite mapped source values.')
        for field in new['fields']:
            if field in values:continue
            confirmed=field in provided;value=provided.get(field)
            row['new_fields'].append({'field':field,'confirmed':confirmed,'value':value})
            if not confirmed:conflict('Explicit value required for new column '+field+'.')
            elif not isinstance(value,str) or value!=value.strip() or any(char in value for char in '|\r\n\x00'):
                raise DomainError('Invalid explicit new-column value: no delimiters, line breaks or edge whitespace.')
            else:values[field]=value;counts['added']+=1
        if new_key in values and values[new_key]!=record['values'][key_field]:conflict('Library key must stay unchanged.')
        row['target_values']=values;rows.append(row)
        output.append({'block_id':new['id'],'class_name':new['class_name'],'values':values})
    cam=None
    if not conflicts:
        cam=native_cam(identifier,release['nx_version'],target,output) if native else {
            'class_name':output[0]['class_name'],'values':output[0]['values']}
        parsed=cam_table(cam) if native else legacy_cam_table(cam)
        if next(iter(parsed.groups()))!=identity[1]:raise DomainError('Adaptation changed the native object identity.',409)
    report={'library_id':identifier,'key':identity[1],'source_representation':'native' if native else 'legacy_tool',
        'source_sha256':digest(request['cam']),'target_release_id':release['id'],'target_nx_version':release['nx_version'],
        'target_library_sha256':fingerprint,'resource_id':request.get('resource_id'),'expected_revision':request.get('expected_revision'),
        'target_schemas':target.blocks,
        'decisions':decisions,'rows':rows,'changes':counts,'conflicts':conflicts,'can_apply':not conflicts,'cam':cam,
        'automatic_save':False,'automatic_approval':False,'nx_files_written':False,
        'note':'Apply only prepares editor values. Save creates a draft revision; geometry and export remain separate checks.'}
    report['preview_hash']=digest(report)
    return report


def apply(store,request,preview_hash):
    report=plan(store,request)
    if not report['can_apply'] or report['preview_hash']!=preview_hash:
        raise DomainError('Review a current, complete schema preview before applying.',409)
    return {'cam':report['cam'],'nx_version':report['target_nx_version'],'preview_hash':preview_hash,
        'draft_required':True,'geometry_recheck_required':True,'saved':False,'nx_files_written':False}
