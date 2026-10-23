"""Version and isolation contracts. Test doubles never count as NX evidence."""
import copy
import hashlib
import io
import json
from pathlib import Path
import uuid
import zipfile

import pytest

from nxserver import nxworker
from nxserver.geometry import GeometryJobs
from nxserver.geometry_requests import request_hash
from nxserver.results import parse_package
from nxserver.safety import DomainError, offline_path
from tools.nxlab.guard import Blocked, local, nx_environment, isolation
from tools.nxlab.measurements import compare
from tools.nxlab.runtime import persistence
from test_geometry import pilot, draft, HEADERS, STEP
from test_results import bundle


@pytest.mark.parametrize('version',['2406','2512'])
def test_target_binds_package_receipt_collector_and_report(pilot,version):
    client,store=pilot;resource,asset=draft(client);key=str(uuid.uuid4())
    value={'resource_id':resource['id'],'expected_revision':resource['revision'],
           'asset_id':asset['id'],'tool_type':'milling_square','nx_version':version}
    headers={**HEADERS,'Idempotency-Key':key}
    response=client.post('/api/v1/geometry-jobs',headers=headers,json=value)
    assert response.status_code==201,response.text
    job=response.json();assert job['nx_version']==version
    assert client.post('/api/v1/geometry-jobs',headers=headers,json=value).json()==job
    opposite={**value,'nx_version':'2512' if version=='2406' else '2406'}
    assert client.post('/api/v1/geometry-jobs',headers=headers,json=opposite).status_code==409
    package=client.get('/api/v1/geometry-jobs/'+job['id']+'/package',headers=HEADERS).content
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read('job.json'))['nx_version']==version
        assert ('NX'+version).encode() in archive.read('LIESMICH.txt')
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/collector-package',headers=HEADERS,
                         json={'root_library_id':'tool-metric','root_key':'NXTEST'})
    assert response.status_code==200,response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read('collector.json'))['job']['nx_version']==version
        assert ('NX'+version).encode() in archive.read('LIESMICH.txt')
    assert store.resources()[0]['cam_status']=='draft'


def test_default_and_invalid_target(pilot):
    client,store=pilot;r,a=draft(client)
    args={'resource_id':r['id'],'expected_revision':1,'asset_id':a['id'],'tool_type':'drilling_solid'}
    assert client.post('/api/v1/geometry-jobs',headers=HEADERS,json=args).json()['nx_version']=='2512'
    assert client.post('/api/v1/geometry-jobs',headers=HEADERS,json={**args,'nx_version':'2406.5001'}).status_code==422
    with pytest.raises(DomainError):GeometryJobs(store).create(r['id'],1,a['id'],'drilling_solid','test',nx_version='guess')
    assert request_hash('r',1,'a','t')==request_hash('r',1,'a','t','2512')
    assert request_hash('r',1,'a','t','2406')!=request_hash('r',1,'a','t','2512')


@pytest.mark.parametrize('value',[r'W:\production',r'w:relative','W:/',r'\\host\share',
    '//host/share',r'\\?\W:\prod',r'\??\W:\prod','file:///W:/prod',' W:/prod','/mnt/w/prod'])
def test_protected_paths_fail_before_any_filesystem_lookup(monkeypatch,value):
    def touched(*args,**kwargs):pytest.fail('Forbidden path reached filesystem lookup.')
    monkeypatch.setattr(Path,'lstat',touched)
    monkeypatch.setattr(Path,'absolute',touched)
    with pytest.raises(DomainError):offline_path(value)
    with pytest.raises(DomainError):local(value)


def test_inherited_production_environment_is_not_propagated(tmp_path):
    # Strings only: never inspect the named drive/share.
    result=nx_environment(tmp_path/'installation',tmp_path/'runtime',{
        'UGII_CAM_RESOURCE_DIR':'W:/production','PATH':'W:/bin',
        'PYTHONPATH':'//prod/modules','NX_ADMIN_TOKEN':'do-not-leak',
        'SPLM_LICENSE_SERVER':'28000@licenses.example'})
    assert 'W:' not in json.dumps(result) and '//prod' not in json.dumps(result)
    assert 'NX_ADMIN_TOKEN' not in result and 'PYTHONPATH' not in result
    assert result['SPLM_LICENSE_SERVER']=='28000@licenses.example'


def test_hosted_local_d_drive_delegates_to_the_same_offline_guard(monkeypatch):
    from pathlib import PureWindowsPath
    from types import SimpleNamespace
    from tools.nxlab import guard
    calls=[]
    monkeypatch.setattr(guard,'os',SimpleNamespace(name='nt'))
    def checked(value):calls.append(value);return PureWindowsPath(value)
    monkeypatch.setattr(guard,'offline_path',checked)
    assert guard.local('D:/a/Server_NX')==PureWindowsPath('D:/a/Server_NX')
    assert calls==['D:/a/Server_NX']
    for value in ('W:/production','D:relative','//host/share'):
        with pytest.raises(DomainError):guard.local(value)
    assert calls==['D:/a/Server_NX'] # Rejected inputs never reached filesystem guarding.


def test_linux_server_uses_its_loaded_image_and_explicit_guarded_sites(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tools.nxlab import runtime
    alias=tmp_path/'venv/bin';alias.mkdir(parents=True)
    image=tmp_path/'base/bin/python';image.parent.mkdir(parents=True);image.write_bytes(b'inert image fixture')
    library=tmp_path/'base/lib';library.mkdir();packages=tmp_path/'venv/lib/site-packages';packages.mkdir(parents=True)
    monkeypatch.setattr(runtime,'sys',SimpleNamespace(platform='linux',executable=str(alias/'python')))
    calls=[]
    def loaded(path):calls.append(path);return str(image)
    monkeypatch.setattr(runtime.os,'readlink',loaded)
    monkeypatch.setattr(runtime.site,'getsitepackages',lambda:[str(packages)])
    executable,flags,environment=runtime.server_python()
    assert executable==image and flags==['-s','-S'] and calls==['/proc/self/exe']
    assert environment=={'PYTHONPATH':str(packages),'LD_LIBRARY_PATH':str(library)}
    assert not (alias/'python').exists() # No alias was followed or executed.


def test_linux_server_does_not_inherit_a_protected_site_folder(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tools.nxlab import runtime
    image=tmp_path/'python';image.write_bytes(b'inert image fixture')
    monkeypatch.setattr(runtime,'sys',SimpleNamespace(platform='linux',executable=str(image)))
    monkeypatch.setattr(runtime.os,'readlink',lambda value:str(image))
    monkeypatch.setattr(runtime.site,'getsitepackages',lambda:['W:/never-read/site-packages'])
    with pytest.raises(DomainError):runtime.server_python()


def test_bad_license_path_and_same_account_cannot_start_nx(tmp_path,monkeypatch):
    with pytest.raises(Blocked):nx_environment(tmp_path/'nx',tmp_path/'r',{'SPLM_LICENSE_SERVER':'W:/license'})
    policy=tmp_path/'policy.json'
    policy.write_text(json.dumps({'schema_version':1,'test_account_sid':'S-1-5-21-1',
        'developer_sid':'S-1-5-21-1','production_file_access_denied':True,'dedicated_account':True,
        'reviewed_by':'admin','isolation_evidence':'A long administrator attestation is insufficient for the same account.'}))
    monkeypatch.setattr('tools.nxlab.guard.account_sid',lambda:'S-1-5-21-1')
    with pytest.raises(Blocked):isolation(policy,'S-1-5-21-1')


FIXTURES=json.loads((Path(__file__).parents[1]/'tools/nxlab/fixtures.json').read_text())


@pytest.mark.parametrize('case',FIXTURES['cases'],ids=lambda c:c['id'])
@pytest.mark.parametrize('mutation',['none','units','axis','mount_point','bbox','volumes','body_count','component_count','missing','nan',
    'body_shift','volume_assignment','body_duplicate','body_missing','body_nan'])
def test_independent_oracle_detects_geometry_failures(case,mutation):
    observed={**copy.deepcopy(case['expected']),'units':case['units'],'axis':case['axis'],
              'mount_point':case['mount_point']}
    observed['volumes']=sorted(observed['volumes'])
    if 'insert_direction' in case:observed['insert_direction']=case['insert_direction']
    if mutation=='units':observed['units']='Inches'
    elif mutation=='axis':observed['axis']=[0,0,-1]
    elif mutation=='mount_point':observed['mount_point']=[0,0,0]
    elif mutation=='bbox':observed['bbox'][5]+=1
    elif mutation=='volumes':observed['volumes'][0]*=1.1
    elif mutation in ('body_count','component_count'):observed[mutation]-=1
    elif mutation=='missing':del observed['volumes']
    elif mutation=='nan':observed['volumes'][0]=float('nan')
    elif mutation=='body_shift':observed['body_geometry'][0]['bbox'][0]+=1
    elif mutation=='volume_assignment':
        a,b=observed['body_geometry'][:2];a['volume'],b['volume']=b['volume'],a['volume']
    elif mutation=='body_duplicate':observed['body_geometry'][0]=copy.deepcopy(observed['body_geometry'][1])
    elif mutation=='body_missing':del observed['body_geometry']
    elif mutation=='body_nan':observed['body_geometry'][0]['volume']=float('nan')
    assert bool(compare(case,observed,FIXTURES['tolerances']))==(mutation!='none')


@pytest.mark.parametrize('case',FIXTURES['cases'],ids=lambda c:c['id'])
def test_body_oracle_is_independent_of_step_body_order_and_labels(case):
    observed={**copy.deepcopy(case['expected']),'units':case['units'],'axis':case['axis'],'mount_point':case['mount_point']}
    if 'insert_direction' in case:observed['insert_direction']=case['insert_direction']
    observed['volumes']=sorted(observed['volumes']);observed['body_geometry'].reverse()
    for item in observed['body_geometry']:item.pop('role')
    assert not compare(case,observed,FIXTURES['tolerances'])


def test_unchanged_global_geometry_cannot_hide_a_displaced_cutting_body():
    case=FIXTURES['cases'][0]
    observed={**copy.deepcopy(case['expected']),'units':case['units'],'axis':case['axis'],'mount_point':case['mount_point']}
    observed['volumes']=sorted(observed['volumes'])
    # The holder still defines global X extents. Shift only the cutter by 2 mm;
    # every former count, aggregate bound and volume check remains satisfied.
    observed['body_geometry'][0]['bbox'][0]+=2;observed['body_geometry'][0]['bbox'][3]+=2
    assert compare(case,observed,FIXTURES['tolerances'])==['body_geometry']


def test_real_http_server_restart_and_backup_restore(tmp_path):
    result=persistence(tmp_path/'http',STEP)
    assert result['status']=='passed' and result['backup_restore'] and not result['nx_execution']
    assert result['synthetic_prepared_result'] and result['unqualified_drafts_excluded_from_export']
    assert (result['resources_preserved'],result['attachments_preserved'],result['histories_preserved'])==(2,3,2)


def test_missing_isolation_is_blocked_with_junit_error_not_a_green_skip(tmp_path,monkeypatch):
    from tools import run_local_pipeline as runner
    import xml.etree.ElementTree as ET
    monkeypatch.setattr(runner,'source_state',lambda:{'commit':'test-source','dirty':True})
    monkeypatch.setattr(runner,'nx_cases',lambda *args:pytest.fail('NX must never start without isolation.'))
    assert runner.main(['--stage','nx','--root',str(tmp_path)])==2
    directory=next(tmp_path.glob('nx-*'))
    report=json.loads((directory/'summary.json').read_text())
    assert report['status']=='blocked' and not report['complete_product_acceptance']
    junit=ET.parse(directory/'junit.xml').getroot()
    assert junit.attrib['errors']=='1' and not junit.findall('.//skipped')


@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows process job ownership.')
def test_owned_job_contains_and_terminates_only_its_inert_children(tmp_path):
    import os,sys,subprocess,time
    from tools.nxlab.processes import OwnedJob
    with OwnedJob() as owned:
        process=owned.start([sys.executable,'-c','import time; time.sleep(30)'],cwd=tmp_path,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        assert owned.active()==1 and process.poll() is None
    assert process.wait(timeout=5)!=0


def test_prepared_2406_manifest_rejects_relabelled_cam_objects(pilot):
    client,store=pilot;r,a=draft(client)
    job=GeometryJobs(store).create(r['id'],1,a['id'],'drilling_solid','test',nx_version='2406')
    def transform(value,contents=None):
        value['nx_version']='2406'
        for item in value['objects']:item['cam']['nx_version']='2406'
    content,_=bundle(job,transform)
    assert parse_package(content,job)['nx_version']=='2406'
    def inconsistent(value,contents=None):
        transform(value);value['objects'][0]['cam']['nx_version']='2512'
    content,_=bundle(job,inconsistent)
    with pytest.raises(DomainError,match='version'):parse_package(content,job)


def test_physical_counterexamples_preserve_the_independent_reference():
    from tools.nxlab.faults import counterexamples
    before=copy.deepcopy(FIXTURES);faults=counterexamples(FIXTURES)
    assert {case['defect'] for case in faults}=={'units','orientation','missing_holder','flat','missing_component','displaced_cut'}
    assert FIXTURES==before
    for case in faults:assert case['oracle']==FIXTURES['cases'][0]
    assert next(c for c in faults if c['defect']=='units')['generated']['units']=='Inches'
    assert len(next(c for c in faults if c['defect']=='missing_holder')['generated']['bodies'])==2
    assert next(c for c in faults if c['defect']=='displaced_cut')['generated']['bodies'][0]['origin']==[2,0,0]


def test_test_output_cannot_overlap_code_or_development_data(tmp_path):
    from tools.nxlab.guard import writable_lab_root
    server=tmp_path/'server'
    for value in (server/'nxserver',server/'tools/run',server/'data',server):
        with pytest.raises(DomainError):writable_lab_root(value,server)
    assert writable_lab_root(server/'test-results',server)==server/'test-results'
    with pytest.raises(DomainError):writable_lab_root(server/'test-results/dev',server,development=True)


@pytest.mark.parametrize('text,blocked',[
    ('License checkout failed for SOLIDS MODELING',True),
    ('License for assemblies is unavailable',True),
    ('Lizenz nicht verfügbar',True),
    ('STEP model has invalid topology',False),
    ('License available; STEP import failed due to malformed data',False)])
def test_license_infrastructure_is_separate_from_geometry_failures(text,blocked):
    from tools.nxlab.runtime import unavailable_license
    assert unavailable_license(text) is blocked
