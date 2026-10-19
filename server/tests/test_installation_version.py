"""Static NX version prerequisites on own inert fixtures, never starts NX."""
import hashlib
import os
import struct
from pathlib import Path

import pytest

from nxserver.migration import prerequisites
from nxserver.installation_version import installation_version,version_evidence,MAX_IMAGE_BYTES
from nxserver.safety import DomainError,offline_path
from test_management import HEADERS
from test_upgrades import upgrade_pilot,preview,finish
from installation_version_test_support import write_nx_image,synthetic_nx_image,windows_version_metadata


@pytest.mark.parametrize('fault',['wrong_target','wrong_original','not_pe'])
def test_wrong_or_unverifiable_installation_blocks_before_job_acceptance(upgrade_pilot,fault):
    client,store,request,source=upgrade_pilot
    if fault=='wrong_target':write_nx_image(store.standards['2512'],'2406')
    elif fault=='wrong_original':write_nx_image(store.standards['2406'],'2512')
    else:(store.standards['2512']/'NXBIN/ugraf.exe').write_bytes(b'Not a PE file; never executed')
    before=store.manifest(source)
    check=prerequisites(store,'2406','2512')
    assert not check['ready'], 'Existence alone must not qualify an NX installation.'
    response=client.post('/api/v1/migrations/preview',headers=HEADERS,json=request)
    assert response.status_code==409,response.text
    assert client.get('/api/v1/migrations',headers=HEADERS).json()==[]
    assert not Path(request['target_path']).exists() and not store.releases()
    assert store.manifest(source)==before


@pytest.mark.parametrize('version',['2406','2512'])
@pytest.mark.parametrize('pe32',[False,True])
def test_portable_static_metadata_has_exact_versions_hash_and_no_runtime_claim(tmp_path,version,pe32):
    path=write_nx_image(tmp_path,version,pe32=pe32)
    before=path.read_bytes();result=installation_version(tmp_path)
    assert version_evidence(result,version)
    assert result['file_version']==result['product_version']==[int(version),8500,0,0]
    assert result['sha256']==hashlib.sha256(before).hexdigest() and result['size']==len(before)
    assert result['path']==str(path.absolute()) and path.read_bytes()==before
    assert result['metadata_only'] and not result['digital_signature_verified'] and not result['nx_execution_verified']


@pytest.mark.parametrize('fault',['dos','signature','optional','sections','resource_size','rva','cycle','leaf_directory',
    'leaf_size','fixed_signature','missing_strings','wrong_product','wrong_filename','wrong_company',
    'fixed_version_disagreement','text_version_disagreement','embedded_version_without_resource'])
def test_malformed_ambiguous_or_unrelated_metadata_is_not_a_version_proof(tmp_path,fault):
    image=bytearray(synthetic_nx_image())
    changes={'dos':(0,b'NO'),'signature':(0x80,b'NOPE'),'optional':(0x98,b'\0\0'),
        'sections':(0x86,struct.pack('<H',97)),
        'resource_size':(0x98+112+20,struct.pack('<I',MAX_IMAGE_BYTES+1)),
        'rva':(0x98+112+16,struct.pack('<I',0xffffff00)),
        'cycle':(512+20,struct.pack('<I',0x80000000)),
        'leaf_directory':(512+68,struct.pack('<I',0x80000018)),
        'leaf_size':(512+76,struct.pack('<I',65537))}
    if fault in changes:
        offset,value=changes[fault];image[offset:offset+len(value)]=value
    elif fault=='fixed_signature':
        offset=image.index(struct.pack('<I',0xFEEF04BD));image[offset:offset+4]=b'\0'*4
    elif fault=='missing_strings':image=synthetic_nx_image(strings={'ProductName':''})
    elif fault=='wrong_product':image=synthetic_nx_image(strings={'ProductName':'Another CAM product'})
    elif fault=='wrong_filename':image=synthetic_nx_image(strings={'OriginalFilename':'unrelated.exe'})
    elif fault=='wrong_company':image=synthetic_nx_image(strings={'CompanyName':'Unrelated company'})
    elif fault=='fixed_version_disagreement':image=synthetic_nx_image(product_version=(2406,7001,0,0))
    elif fault=='text_version_disagreement':image=synthetic_nx_image(strings={'ProductVersion':'2406.7001'})
    else:
        # A signature scan would find plausible metadata, but no PE resource points to it.
        struct.pack_into('<II',image,0x98+112+16,0,0)
    path=write_nx_image(tmp_path);path.write_bytes(image);before=path.read_bytes()
    with pytest.raises(DomainError):installation_version(tmp_path)
    assert path.read_bytes()==before


def test_version_metadata_and_sidt_instruction_hashes_are_bound_to_plan_inputs(upgrade_pilot):
    from nxserver.migration import Planner
    client,store,request,source=upgrade_pilot
    plan,report=Planner(store).build(request)
    assert report['prerequisites']['installation_version_contract_version']==1
    for version in report['prerequisites']['versions']:
        assert version_evidence(version['version_metadata'],version['nx_version'])
        for name in ('version_metadata','profile'):
            assert plan['inputs'][version[name]['path']]==version[name]['sha256']
    assert report['input_count']==len(plan['inputs'])


def test_same_major_but_changed_build_between_preview_and_build_is_not_accepted(upgrade_pilot):
    client,store,request,source=upgrade_pilot;job,report=preview(client,request)
    assert report['can_build']
    write_nx_image(store.standards['2512'],'2512',build=8501)
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='failed' and not Path(request['target_path']).exists() and not store.releases()


def test_changed_version_file_during_planning_is_detected(upgrade_pilot,monkeypatch):
    import nxserver.migration as migration
    client,store,request,source=upgrade_pilot;original=migration.prerequisites
    def changing(*args):
        result=original(*args);write_nx_image(store.standards['2512'],'2512',build=8501)
        return result
    monkeypatch.setattr(migration,'prerequisites',changing)
    with pytest.raises(DomainError,match='inputs changed'):migration.Planner(store).build(request)
    assert not Path(request['target_path']).exists() and not store.releases()


@pytest.mark.parametrize('suffix',['.exe','.EXE','.bat','.cmd','.com','.txt'])
def test_regular_file_readers_accept_windows_extension_mode_without_executing(tmp_path,suffix):
    from nxserver.safety import read_bounded_file,file_fingerprint
    path=tmp_path/('synthetic'+suffix);raw=b'Own inert bytes, not executable code';path.write_bytes(raw)
    assert read_bounded_file(path,max_bytes=100)==raw
    assert file_fingerprint(path)=={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    assert path.read_bytes()==raw


@pytest.mark.skipif(os.name!='nt',reason='Independent Windows-system version-resource API.')
@pytest.mark.parametrize('pe32',[False,True])
def test_independent_windows_api_confirms_own_inert_fixture(tmp_path,pe32):
    path=write_nx_image(tmp_path,pe32=pe32)
    expected=windows_version_metadata(path);actual=installation_version(tmp_path)
    assert {name:actual[name] for name in expected}==expected


@pytest.mark.skipif(os.name!='nt' or os.environ.get('NX_STOCK_SCHEMA_TEST')!='1',reason='Opt-in read-only installed NX version metadata.')
@pytest.mark.parametrize('version',['2406','2512'])
def test_installed_version_matches_independent_windows_metadata_without_execution(version):
    root=offline_path(Path('C:/Siemens')/('NX'+version));path=offline_path(root/'NXBIN/ugraf.exe')
    actual=installation_version(root);expected=windows_version_metadata(path)
    assert actual['nx_version']==version
    assert {name:actual[name] for name in expected}==expected
    assert installation_version(root)==actual


def test_file_stamp_retains_identity_metadata_and_posix_execute_permissions():
    from types import SimpleNamespace
    from nxserver.safety import file_stamp
    values={'st_mode':0o100666,'st_dev':1,'st_ino':2,'st_size':3,'st_mtime_ns':4,'st_ctime_ns':5}
    base=file_stamp(SimpleNamespace(**values))
    for name in values:
        changed={**values,name:values[name]+(0o200 if name=='st_mode' else 1)}
        assert file_stamp(SimpleNamespace(**changed))!=base
    executable=file_stamp(SimpleNamespace(**{**values,'st_mode':values['st_mode']|0o111}))
    assert (executable==base)==(os.name=='nt')


@pytest.mark.parametrize('different',[False,True])
def test_multiple_language_resources_must_agree_before_version_is_reported(tmp_path,different):
    other=synthetic_nx_image('2406' if different else '2512')[600:]
    path=write_nx_image(tmp_path,other_resources=[other])
    if different:
        with pytest.raises(DomainError):installation_version(tmp_path)
    else:assert installation_version(tmp_path)['nx_version']=='2512'


def test_image_byte_limit_is_enforced_before_content_read(tmp_path,monkeypatch):
    import nxserver.installation_version as module
    path=write_nx_image(tmp_path)
    monkeypatch.setattr(module,'MAX_IMAGE_BYTES',8)
    original=Path.open
    def forbidden(candidate,*args,**kwargs):
        if candidate==path:pytest.fail('Oversize PE must not be opened.')
        return original(candidate,*args,**kwargs)
    monkeypatch.setattr(Path,'open',forbidden)
    with pytest.raises(DomainError,match='exceeds'):installation_version(tmp_path)


@pytest.mark.parametrize('path',['W:/SiemensShare/nx2406','/mnt/w/blocked','//server/share'])
def test_protected_installation_is_rejected_before_any_stat(monkeypatch,path):
    monkeypatch.setattr(Path,'stat',lambda *args,**kwargs:pytest.fail('Protected metadata path may not be inspected.'))
    monkeypatch.setattr(Path,'lstat',lambda *args,**kwargs:pytest.fail('Protected metadata path may not be inspected.'))
    with pytest.raises(DomainError):installation_version(Path(path))


@pytest.mark.parametrize('fault',['contract','metadata','major','runtime','signature','sha','fixed','text','identity'])
def test_legacy_or_rehashed_false_version_plan_cannot_accept_a_new_build(upgrade_pilot,fault):
    import copy
    import json
    from nxserver.store import digest
    from nxserver.upgrades import Upgrades
    client,store,request,source=upgrade_pilot;job,original=preview(client,request);report=copy.deepcopy(original)
    check=report['prerequisites'];metadata=check['versions'][1]['version_metadata']
    if fault=='contract':check.pop('installation_version_contract_version')
    elif fault=='metadata':check['versions'][1].pop('version_metadata')
    elif fault=='major':metadata['nx_version']='2406'
    elif fault=='runtime':metadata['nx_execution_verified']=True
    elif fault=='signature':metadata['digital_signature_verified']=True
    elif fault=='sha':metadata['sha256']='not-a-sha'
    elif fault=='fixed':metadata['product_version'][0]=2406
    elif fault=='text':metadata['strings']['ProductVersion']='2406.8500'
    else:metadata['strings']['ProductName']='Another CAM product'
    unsigned=copy.deepcopy(report);unsigned.pop('preview_hash');report['preview_hash']=digest(unsigned)
    raw=json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    (store.root/'upgrades'/job['id']/'report.json').write_bytes(raw)
    manager=Upgrades(store);manager._update(job['id'],report_sha256=hashlib.sha256(raw).hexdigest())
    assert manager.report(job['id'])==report # History remains readable; never follows stored executable paths.
    before=len(manager.list())
    response=client.post('/api/v1/migrations/build',headers=HEADERS,json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']})
    assert response.status_code==409 and len(manager.list())==before
    assert not Path(request['target_path']).exists() and not store.releases()
