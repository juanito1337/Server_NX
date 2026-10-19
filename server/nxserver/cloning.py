"""Copy an active native group into a new editor draft, never a share write."""
import copy
import json

from .libraries import cam_table,digest,native_cam,records_hash,spec_for
from .safety import DomainError
from .schemas import _target,MAX_ROWS,MAX_CELLS
from .store import resource_identity


def validate(request):
    spec_for(request['library_id'])
    value=request['new_key']
    if not isinstance(value,str) or not 1<=len(value)<=116 or value!=value.strip() or any(char in value for char in '|\r\n\x00'):
        raise DomainError('Choose a nonempty exact new library key (max. 116 characters, no delimiters/edge whitespace).')
    if not isinstance(request['name'],str) or not request['name'].strip():raise DomainError('Choose a name for the new draft.')
    if value==request['source_key']:raise DomainError('A copied draft requires a different, new library identifier.',409)


def catalog_context(store,identifier,key):
    with store.db() as db:
        db.execute('BEGIN');rows=[dict(row) for row in db.execute('SELECT id,revision,payload FROM resources ORDER BY id')]
    try:
        owners=[row['id'] for row in rows if resource_identity(json.loads(row['payload']))==(identifier,key)]
    except (KeyError,TypeError,ValueError,AttributeError) as error:
        raise DomainError('Catalog identity metadata is invalid; cannot prove a free new key.',409) from error
    return rows,owners


def plan(store,release_id,request):
    validate(request);identifier=request['library_id'];spec=spec_for(identifier)
    release,table,fingerprint=_target(store,release_id,identifier)
    if fingerprint!=request['source_sha256']:raise DomainError('Source library changed since it was displayed. Read the current source again.',409)
    groups=table.groups()
    if request['source_key'] not in groups:raise DomainError('Choose an exact active source group; comments are not source objects.',409)
    records=copy.deepcopy(groups[request['source_key']])
    if len(records)>MAX_ROWS or sum(len(record['values']) for record in records)>MAX_CELLS:
        raise DomainError('Copied group exceeds the supported editor row/field limit.')
    source_hash=records_hash(records);conflicts=[]
    if request['new_key'] in groups:conflicts.append('New key already exists in the source NX library; no standard/customer overwrite.')
    catalog,owners=catalog_context(store,identifier,request['new_key'])
    if owners:conflicts.append('New key is already claimed by a managed resource, including drafts/retirements.')
    for record in records:
        schema=table.block(record['block_id']);field='LIBRF' if 'LIBRF' in schema['fields'] else 'NAME'
        if record['values'].get(field)!=request['source_key']:
            raise DomainError('Source group identity is inconsistent; no partial identity rewrite.',409)
        record['values'][field]=request['new_key']
    cam=native_cam(identifier,release['nx_version'],table,records)
    if set(cam_table(cam).groups())!={request['new_key']}:raise DomainError('Copied group does not have exactly the new identity.',409)
    resource={'kind':spec['kind'],'name':request['name'],'cam':cam,'cam_status':'draft','erp':None,'origin':None,
        'asset_ids':[],'file_bindings':[],'model_package':None,
        'geometry':{'mode':'none','status':'needs_preparation','nx_version':release['nx_version'],
            'checks':{},'evidence':'','asset_hashes':{}}}
    report={'contract_version':1,'source_release_id':release_id,'source_library':identifier,'source_key':request['source_key'],
        'source_sha256':fingerprint,'source_records_sha256':source_hash,'source_manifest_sha256':digest(release['manifest']),
        'new_key':request['new_key'],'resource':resource,'rows':len(records),'catalog_sha256':digest(catalog),
        'conflicts':conflicts,'can_apply':not conflicts,'saved':False,'nx_files_written':False,
        'assets_copied':False,'erp_copied':False,'approval_copied':False,'schema_conversion_performed':False,
        'note':'Only identifier columns change. Other values, references and graphics names remain source values; review them before preparation/export.'}
    report['preview_hash']=digest(report)
    return report


def apply(store,release_id,request,preview_hash):
    report=plan(store,release_id,request)
    if not report['can_apply'] or report['preview_hash']!=preview_hash:
        raise DomainError('Review a current complete cloning preview with a free new key.',409)
    # No persistent allocation occurs here. A subsequent explicit save performs
    # the normal atomic identity/revision checks, even if a new owner appears.
    catalog,owners=catalog_context(store,request['library_id'],request['new_key'])
    if owners or digest(catalog)!=report['catalog_sha256']:raise DomainError('Catalog changed before draft preparation. Preview again.',409)
    current,table,fingerprint=_target(store,release_id,request['library_id'])
    if fingerprint!=report['source_sha256'] or digest(current['manifest'])!=report['source_manifest_sha256'] or current['nx_version']!=report['resource']['cam']['nx_version']:
        raise DomainError('Source changed before draft preparation. Read and preview again.',409)
    return {'contract_version':1,'resource':report['resource'],'preview_hash':preview_hash,'saved':False,
        'nx_files_written':False,'draft_required':True,'geometry_recheck_required':True}
