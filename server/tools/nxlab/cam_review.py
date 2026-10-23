"""Reopen only the server side of an existing, pinned NX laboratory case."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import uuid
import zipfile

from nxserver.migration import portable
from nxserver.safety import read_bounded_file,file_fingerprint
from .guard import local,Blocked
from .measurements import compare

LAB_ROOT='C:/NX_Migration/ToolJobs'


def require(condition,message):
    if not condition:raise Blocked(message)


def normalized(value):
    value=str(value).replace('\\','/')
    return value.casefold() if os.name=='nt' else value


def load(path,source,*,lab_root=LAB_ROOT):
    # Namespace validation is lexical: even an ordinary C: directory outside
    # our generated case namespace must not be inspected as a handoff.
    text=normalized(path);prefix=normalized(lab_root).rstrip('/')
    require(re.fullmatch(re.escape(prefix)+r'/lab-[a-f0-9]{12}/cam-handoff\.json',text),
            'Review requires a generated local lab case; no other path is inspected.')
    path=local(path);case=path.parent
    value=json.loads(read_bounded_file(path,max_bytes=4*1024*1024).decode('utf-8'))
    require(value.get('contract_version')==1 and value.get('nx_version')=='2406' and
            value.get('server_closed') is True,'Incomplete or unsupported CAM handoff.')
    require(all(value.get('source',{}).get(key)==source.get(key) for key in ('commit','source_sha256')),
            'Sources changed since NX qualification; create a new qualified case.')
    for field,relative in [('server_data','server/data'),('settings','server/settings.local.json'),
                           ('collector_directory','collector')]:
        require(normalized(value.get(field,''))==normalized(case/relative),'Handoff points outside its own case: '+field)
    for field in ('job_id','resource_id','target_release_id'):
        try:valid=str(uuid.UUID(value.get(field,'')))==value[field]
        except (ValueError,KeyError,TypeError,AttributeError):valid=False
        require(valid,'Invalid handoff identity: '+field)
    for field in ('resource_hash','input_sha256','package_sha256','collector_sha256'):
        require(isinstance(value.get(field),str) and re.fullmatch('[a-f0-9]{64}',value[field]),'Invalid handoff hash: '+field)
    specs=json.loads(local(Path(__file__).parent/'fixtures.json').read_text(encoding='utf-8'))
    expected=next((item for item in specs['cases'] if item['id']==value.get('case_id')),None)
    require(expected is not None and value.get('root_library_id')=='tool-metric' and
        value.get('root_key')=='NXTEST_'+expected['id'].upper(),'Unexpected reference case or native root.')
    require(not compare(expected,value.get('reference_observed',{}),specs['tolerances']),
            'Reference measurement no longer matches its independent oracle.')
    imported=value.get('import_observed',{})
    errors=compare(expected,imported,specs['tolerances'])
    require(not [key for key in errors if key not in ('axis','mount_point','insert_direction') or key in imported],
            'Imported measurement no longer matches its independent oracle.')
    groups=value.get('artifacts',{})
    require(set(groups)=={'created','reopened','import_reopened'},'Missing model artifact evidence.')
    for name,relative in [('created','create'),('reopened','create'),('import_reopened','import/output')]:
        group=groups[name];directory=local(case/relative)
        require(normalized(group.get('directory',''))==normalized(directory),'Model artifact directory differs.')
        files=group.get('files');require(isinstance(files,dict) and 1<=len(files)<=80,'Incomplete artifact inventory.')
        total=0
        for filename,pin in files.items():
            portable(filename)
            require(filename.lower().endswith(('.prt','.step','.stp')) and type(pin.get('size')) is int and
                0<pin['size']<=20*1024*1024 and re.fullmatch('[a-f0-9]{64}',pin.get('sha256','')),
                'Invalid model artifact binding.')
            total+=pin['size'];require(total<=256*1024*1024,'Artifact byte budget exceeded.')
            require(file_fingerprint(local(directory/filename),max_bytes=pin['size'])==pin,'A qualified model artifact changed.')
    return value,case


def check_server(server,value):
    job=server.request('/api/v1/geometry-jobs/'+value['job_id'])
    require(all(job.get(key)==value[key] for key in ('resource_id','resource_revision','resource_hash','nx_version')),
            'Stored geometry job differs from the handoff.')
    require(job['status']=='needs_selection' and job['input']['sha256']==value['input_sha256'],
            'A successful bound NX import report is required for CAM review.')
    reported={item['path']:{key:item[key] for key in ('size','sha256')} for item in job['report']['files']}
    require(all(reported.get(path)==pin for path,pin in value['artifacts']['import_reopened']['files'].items()),
            'Stored NX report differs from the reopened model artifact hashes.')
    require(sum(body['solid'] for part in job['report']['parts'] for body in part['bodies'])==value['import_observed']['body_count'] and
        len(job['report']['components'])==value['import_observed']['component_count'],
        'Stored NX report differs from the reopened body/component counts.')
    package=server.request('/api/v1/geometry-jobs/'+job['id']+'/package',binary=True)
    require(hashlib.sha256(package).hexdigest()==value['package_sha256'],'STEP worker archive differs.')
    release=server.request('/api/v1/releases/'+value['target_release_id'])
    require(release['nx_version']=='2406' and release['manifest']==value['target_manifest'],
            'Stored CAM target differs from the handoff.')
    root=local(value['server_data'])
    archive=read_bounded_file(local(root/'geometry-results/collectors'/job['id']/value['collector_sha256']/'collector.zip'),
        max_bytes=64*1024*1024,expected_sha256=value['collector_sha256'])
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        require(package.getinfo('collector.json').file_size<=1024*1024,'Collector configuration exceeds its byte budget.')
        config=package.read('collector.json')
        configuration=json.loads(config)
        require(configuration['root_key']==value['root_key'] and configuration['root_library_id']==value['root_library_id']
            and configuration['job']['id']==job['id'],'Collector belongs to another native root/job.')
        folder=local(value['collector_directory'])
        read_bounded_file(folder/'collector.json',max_bytes=1024*1024,expected_size=len(config),
            expected_sha256=hashlib.sha256(config).hexdigest())
        expected={'nx_result_collect.py','nx_libraries.py','nx_safety.py','nx_result_inventory.py','stepfiles.py'}
        require(set(configuration['helper_hashes'])==expected,'Collector helper inventory differs.')
        for name,pin in configuration['helper_hashes'].items():
            read_bounded_file(local(folder/name),max_bytes=1024*1024,expected_sha256=pin)
    return {'status':'passed','id':'server-review-context','job_id':job['id'],
            'target_release_id':release['id'],'nx_execution_verified':False}
