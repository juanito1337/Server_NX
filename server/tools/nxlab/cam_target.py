"""Prepare an empty, local return target using installed native FORMATs.

Only known tool-library DAT files are read. No NX process, stock object, model,
credentials or production path is copied into this test setup.
"""
import hashlib
import re

from nxserver.libraries import SPECS,NativeTable,decode
from nxserver.safety import disjoint,read_bounded_file
from .guard import local,writable_lab_root

LIBRARIES=tuple(key for key,spec in SPECS.items()
    if spec['kind'] in ('tool','tool_assembly','holder','shank','trackpoint'))
MAX_LIBRARY_BYTES=32*1024*1024


def prepare(installation,destination):
    installation=local(installation)
    destination=writable_lab_root(destination,local(__file__).parents[2])
    disjoint(installation,destination)
    destination.mkdir(parents=True,exist_ok=False)
    report={'contract_version':1,'nx_version':'2406','libraries':[],
        'complete':False,'stock_objects_copied':False,'nx_execution_verified':False}
    for identifier in LIBRARIES:
        spec=SPECS[identifier];path=local(installation/spec['path'])
        raw=read_bounded_file(path,max_bytes=MAX_LIBRARY_BYTES)
        table=NativeTable(decode(raw))
        # Preserve the actual native headers/FORMATs. DATA tokens, including
        # multiline records, are removed by the established native writer.
        empty=table.render({}).encode('utf-8')
        check=NativeTable(decode(empty))
        if check.records or check.blocks!=table.blocks:
            raise RuntimeError('Empty CAM target lost a native FORMAT or retained stock objects.')
        output=local(destination/spec['path']);output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('xb') as stream:stream.write(empty)
        report['libraries'].append({'id':identifier,'path':spec['path'],
            'installed_sha256':hashlib.sha256(raw).hexdigest(),
            'target_sha256':hashlib.sha256(empty).hexdigest(),'target_size':len(empty),
            'format_count':len(check.blocks),'object_count':0})
    report['complete']=True
    return report


def bind_handoff(*,case,source,baseline,release,resource,job,collector,collector_sha256,observed,imported):
    """Persist identifiers and hashes, never the ephemeral server token."""
    if release['nx_version']!=job['nx_version'] or job['nx_version']!='2406':
        raise RuntimeError('CAM handoff target differs from the geometry job.')
    if resource['id']!=job['resource_id'] or resource['revision']!=job['resource_revision']:
        raise RuntimeError('CAM handoff resource revision differs from the geometry job.')
    if (not baseline['complete'] or len(baseline['libraries'])!=len(LIBRARIES) or
            any(item['object_count'] for item in baseline['libraries'])):
        raise RuntimeError('CAM handoff needs a complete empty native target.')
    expected={item['path']:(item['target_sha256'],item['target_size']) for item in baseline['libraries']}
    actual={item['path']:(item['sha256'],item['size']) for item in release['manifest']}
    if actual!=expected:raise RuntimeError('CAM handoff target manifest differs from the prepared native baseline.')
    if not isinstance(collector_sha256,str) or not re.fullmatch('[a-f0-9]{64}',collector_sha256):
        raise RuntimeError('CAM handoff requires the exact collector archive hash.')
    return {'contract_version':1,'case_id':case['id'],'nx_version':job['nx_version'],
        'source':source,'job_id':job['id'],'resource_id':resource['id'],
        'resource_revision':resource['revision'],'resource_hash':job['resource_hash'],
        'input_sha256':job['input']['sha256'],'package_sha256':job['package_sha256'],
        'target_release_id':release['id'],'target_manifest':release['manifest'],
        'baseline':baseline,'collector_directory':str(local(collector)),'collector_sha256':collector_sha256,
        'root_library_id':'tool-metric','root_key':'NXTEST_'+case['id'].upper(),
        'reference_observed':observed,'import_observed':imported,
        'status':'awaiting_cam','automatic_approval':False,'nx_cam_acceptance_verified':False,
        'next_steps':['Create and check the native CAM tool in the isolated NX test account.',
            'Export to this collector return-library and run its pinned collector journal.',
            'Upload the ZIP and adopt into target_release_id; the result must remain a draft.',
            'Confirm all six geometry checks, export, and reload from the export in a fresh CAM session.',
            'Record the collision and clearance counterchecks and bind final measurements to the exported files.']}
