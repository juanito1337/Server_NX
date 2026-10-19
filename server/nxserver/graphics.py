"""Read-only component placement, using the same standard rules as export."""
from .libraries import digest,graphics_bindings,spec_for
from .safety import DomainError
from .schemas import _source,_context


def preview(store,request):
    native,identifier,source,_=_source(request['kind'],request['cam'])
    identity=(identifier,next(iter(source.groups())))
    _context(store,request,identity,native)
    spec=spec_for(identifier)
    if not spec['graphics_dir']:
        raise DomainError('This library has no standard graphics destination; use its supported file-package adapter.')
    identifiers=request['asset_ids']
    if len(identifiers)>100 or len(identifiers)!=len(set(identifiers)):
        raise DomainError('Choose at most 100 distinct attached files.')
    assets=[store.asset(identifier)[0] for identifier in identifiers]
    bindings=request['file_bindings']
    paths=graphics_bindings({'file_bindings':bindings},assets,spec)
    report={'contract_version':1,'scope':'graphics_paths_only','library_id':identifier,
        'graphics_dir':spec['graphics_dir'],'nx_version':request['cam'].get('nx_version') if native else None,
        'source_sha256':digest(request['cam']),'resource_id':request.get('resource_id'),
        'expected_revision':request.get('expected_revision'),'asset_ids':identifiers,
        'file_bindings':bindings,'mode':'explicit_prt' if bindings else 'default_all_attachments',
        'entries':[{'asset_id':asset['id'],'filename':asset['filename'],'size':asset['size'],
                    'sha256':asset['sha256'],'path':paths[asset['id']]} for asset in assets if asset['id'] in paths],
        'private_asset_ids':[asset['id'] for asset in assets if asset['id'] not in paths],
        'saved':False,'nx_files_written':False,'automatic_approval':False,
        'target_conflicts_checked':False,'provenance_checked':False,
        'note':'Placement only. Save checks attachments and package provenance; export checks target ownership/collisions. No PRT reference rewriting or NX qualification.'}
    report['preview_hash']=digest(report)
    return report
