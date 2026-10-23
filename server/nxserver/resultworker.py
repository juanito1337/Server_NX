"""NX2512 collector: already exported/saved local library, no export/approval guessing."""
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

COLLECTOR_FILES=('nx_result_collect.py','nx_libraries.py','nx_safety.py','nx_result_inventory.py','stepfiles.py')
MAX_CONFIG_BYTES=32*1024*1024
MAX_HELPER_BYTES=1024*1024
MAX_MANIFEST_BYTES=2*1024*1024
if '_nx_verified_helper_modules' not in globals():
    from .nxbootstrap import _nx_verified_helper_modules


def _verified_collector_helpers(directory):
    return _nx_verified_helper_modules(directory,'collector.json','helper_hashes',COLLECTOR_FILES,
        (('safety','nx_safety.py'),('stepfiles','stepfiles.py'),('libraries','nx_libraries.py'),('nxworker','nx_result_inventory.py')),
        max_job_bytes=MAX_CONFIG_BYTES,max_helper_bytes=MAX_HELPER_BYTES)


try:
    from .safety import DomainError,offline_path,tree_files,asset_filename,read_bounded_file
    from .libraries import SPECS,NativeTable,native_cam,reference_targets
    from .nxworker import check_environment,inventory_part,job_directory
except ImportError:
    _loaded=_verified_collector_helpers(Path(__file__).absolute().parent)
    _safe,_libs,_inventory=_loaded['safety'],_loaded['libraries'],_loaded['nxworker']
    DomainError,offline_path,tree_files,asset_filename,read_bounded_file=(_safe.DomainError,_safe.offline_path,_safe.tree_files,_safe.asset_filename,_safe.read_bounded_file)
    SPECS,NativeTable,native_cam,reference_targets=_libs.SPECS,_libs.NativeTable,_libs.native_cam,_libs.reference_targets
    check_environment,inventory_part,job_directory=_inventory.check_environment,_inventory.inventory_part,_inventory.job_directory


def collect(directory,session):
    directory=job_directory(directory);configuration=json.loads(read_bounded_file(directory/'collector.json',max_bytes=MAX_CONFIG_BYTES).decode('utf-8-sig'))
    if set(configuration['helper_hashes'])!={'nx_result_collect.py','nx_libraries.py','nx_safety.py','nx_result_inventory.py','stepfiles.py'}:
        raise DomainError('Incomplete collector helper contract.')
    for name,expected in configuration['helper_hashes'].items():
        if name not in ('nx_result_collect.py','nx_libraries.py','nx_safety.py','nx_result_inventory.py','stepfiles.py'):
            raise DomainError('Unknown collector helper.')
        read_bounded_file(directory/name,max_bytes=MAX_HELPER_BYTES,expected_sha256=expected,changed_message='Collector helper changed.')
    job=configuration['job']
    check_environment(session) if job['nx_version']=='2512' else check_environment(session,job['nx_version'])
    library=offline_path(directory/'return-library');root_key=configuration['root_key'];identifier=configuration['root_library_id']
    spec=SPECS[identifier];graphics=offline_path(library/spec['graphics_dir'])
    asset_filename(root_key+'.prt')
    part=session.Parts.Work
    if part is None or offline_path(part.FullPath)!=offline_path(graphics/(root_key+'.prt')):
        raise DomainError('Open the saved LIBRF.prt from this return-library graphics directory first.')
    loaded=list(session.Parts)
    if any(item.IsModified for item in loaded):raise DomainError('Save all prepared parts first; the collector never saves or renames them.')
    parts=[inventory_part(item,graphics) for item in loaded]
    if any(not item['fully_loaded'] for item in parts):raise DomainError('Load every prepared component completely first.')
    components=[];assembly=part.ComponentAssembly.RootComponent
    if assembly:
        pending=[(assembly,'')]
        while pending:
            parent,parent_id=pending.pop()
            for index,component in enumerate(parent.GetChildren()):
                identifier_path=parent_id+'/'+str(index)
                if len(identifier_path)>500 or len(components)>=10000:raise DomainError('Prepared assembly exceeds collector bounds.')
                prototype=offline_path(component.Prototype.FullPath)
                try:relative=prototype.relative_to(graphics).as_posix()
                except ValueError:raise DomainError('Component is outside the local return-library; no old-host fallback.') from None
                components.append({'id':identifier_path,'name':component.Name,'part':relative});pending.append((component,identifier_path))
    tables={}
    for key,value in SPECS.items():
        if value['kind'] not in ('tool','tool_assembly','holder','shank','trackpoint'):continue
        path=offline_path(library/value['path'])
        if path.is_file():tables[key]=NativeTable.read(path)
    root=(identifier,root_key);needed={};pending=[root]
    while pending:
        key=pending.pop()
        if key in needed:continue
        if key[0] not in tables or key[1] not in tables[key[0]].groups():
            raise DomainError('Root/related native definition is missing from the local export: '+str(key))
        records=tables[key[0]].groups()[key[1]];needed[key]=native_cam(key[0],job['nx_version'],tables[key[0]],records)
        for record in records:
            for field,target_library in reference_targets(key[0]).items():
                target=(target_library,record['values'].get(field,''))
                if target[0] in tables and target[1] in tables[target[0]].groups():pending.append(target)
    files=[];contents={};total=0
    for item in parts:
        for component in item['path'].split('/'):asset_filename(component)
        path=offline_path(graphics/item['path']);before=path.stat()
        if not 0<before.st_size<=20*1024*1024:raise DomainError('Prepared PRT must contain 1 byte to 20 MiB.')
        data=read_bounded_file(path,max_bytes=20*1024*1024,expected_size=before.st_size,
            changed_message='Prepared part changed while collecting.');after=path.stat();total+=len(data)
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise DomainError('Prepared part changed while collecting.')
        if total>256*1024*1024 or len(files)>=80:raise DomainError('Prepared package exceeds component bounds.')
        contents[item['path']]=data;files.append({'path':item['path'],'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    job=configuration['job']
    manifest={'contract_version':1,'job_id':job['id'],'resource_id':job['resource_id'],
        'resource_revision':job['resource_revision'],'resource_hash':job['resource_hash'],'input_sha256':job['input']['sha256'],
        'worker_hashes':job['worker_hashes'],'nx_version':job['nx_version'],'tool_type':job['tool_type'],'automatic_cam_approval':False,
        'root_library_id':configuration['root_library_id'],'root_key':root_key,'root_part':root_key+'.prt',
        'objects':[{'cam':cam} for cam in needed.values()],'parts':parts,'components':components,'files':files,
        'collector':'NX'+job['nx_version']+' local inventory, unverified external evidence'}
    output=offline_path(directory/'prepared-result.zip')
    if output.exists():raise DomainError('Prepared result already exists; use a fresh collector directory.')
    # The server limits the uncompressed manifest, not just the ZIP. Do not
    # publish a small compressed archive whose complete inventory is rejected.
    manifest_bytes=json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    if len(manifest_bytes)>MAX_MANIFEST_BYTES:
        raise DomainError('Prepared manifest exceeds 2 MiB; this complete inventory cannot be packaged. No archive was created.')
    memory=io.BytesIO()
    with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED) as package:
        package.writestr('manifest.json',manifest_bytes)
        for path,data in contents.items():package.writestr('files/'+path,data)
    data=memory.getvalue()
    if len(data)>64*1024*1024:raise DomainError('Prepared ZIP exceeds 64 MiB.')
    output.write_bytes(data)
    return {'file':str(output),'sha256':hashlib.sha256(data).hexdigest(),'files':len(files),
            'objects':len(needed),'automatic_cam_approval':False}


def main():
    import NXOpen
    session=NXOpen.Session.GetSession()
    try:result=collect(Path(__file__).absolute().parent,session)
    except Exception as error:
        session.ListingWindow.Open();session.ListingWindow.WriteLine('Collector ABGEBROCHEN: '+str(error));raise
    session.ListingWindow.Open();session.ListingWindow.WriteLine(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
