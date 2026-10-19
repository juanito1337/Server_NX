"""SIDT version declarations are data, never executable commands."""
import hashlib
import os
from pathlib import Path

import pytest

from nxserver.migration import prerequisites
from nxserver.sidt_version import declaration,evidence,MAX_BYTES,RELATIVE
from nxserver.safety import DomainError,offline_path
from test_upgrades import upgrade_pilot,preview,finish
from test_management import HEADERS


@pytest.mark.parametrize('fault',['swapped_root','wrong_marker','missing_marker'])
def test_wrong_or_unverifiable_sidt_is_rejected_before_accepting_preview(upgrade_pilot,fault):
    client,store,request,source=upgrade_pilot
    marker=store.sidts['2512']/'start_apps/env/nx_inst_dir.cmd'
    if fault=='swapped_root':store.sidts['2512']=store.sidts['2406']
    elif fault=='wrong_marker':marker.write_text('set NX_INST_DIR=NX2406\n',encoding='ascii')
    else:marker.unlink()
    before=store.manifest(source)
    check=prerequisites(store,'2406','2512')
    assert not check['ready'],'Copy layout alone does not identify the SIDT version.'
    response=client.post('/api/v1/migrations/preview',headers=HEADERS,json=request)
    assert response.status_code==409,response.text
    assert client.get('/api/v1/migrations',headers=HEADERS).json()==[]
    assert store.manifest(source)==before and not Path(request['target_path']).exists() and not store.releases()


def marker(root,raw):
    path=root/RELATIVE;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return path


@pytest.mark.parametrize('raw',[b'set NX_INST_DIR=NX2512\r\n',b'@set NX_INST_DIR=NX2512\n',
    b'\xef\xbb\xbfset NX_INST_DIR=NX2512\n',b'set "NX_INST_DIR=NX2512"\n',
    b'  SET nx_inst_dir=nx2512\n',b'REM Old comment mentions NX2406\n:: NX2212\nset NX_INST_DIR=NX2512\n',
    b'@echo off\nset NX_INST_DIR=NX2512\n'])
def test_explicit_version_is_read_as_data_and_exact_hash_is_preserved(tmp_path,raw):
    path=marker(tmp_path,raw);before=os.environ.get('NX_INST_DIR');value=declaration(tmp_path,'2512')
    assert evidence(value,'2512') and value['nx_version']=='2512'
    assert value['path']==str(path.absolute()) and value['sha256']==hashlib.sha256(raw).hexdigest()
    assert value['size']==len(raw) and value['metadata_only'] and not value['script_executed'] and not value['provenance_verified']
    assert os.environ.get('NX_INST_DIR')==before and path.read_bytes()==raw


@pytest.mark.parametrize('raw',[b'',b'REM NX2512\n',b'set NX_INST_DIR=NX251\n',b'set NX_INST_DIR=NX2512 \n',
    b'set NX_INST_DIR=NX2512\nset NX_INST_DIR=NX2406\n',b'set NX_INST_DIR=NX2512\nset NX_INST_DIR=NX2512\n',
    b'if exist nowhere set NX_INST_DIR=NX2512\n',b'set /p NX_INST_DIR=NX2512\n',b'setx NX_INST_DIR NX2512\n',
    b'set NX_INST_DIR=%OTHER%\n',b'set NX_INST_DIR=NX2512 & echo never_execute\n',
    b'REM note & set NX_INST_DIR=NX2406\nset NX_INST_DIR=NX2512\n',b'set NX_INST_DIR=NX2512\nunknown_command\n'])
def test_unapproved_or_ambiguous_command_layout_never_qualifies(tmp_path,raw):
    path=marker(tmp_path,raw)
    with pytest.raises(DomainError):declaration(tmp_path,'2512')
    assert path.read_bytes()==raw


def test_declaration_byte_bound_is_checked_before_open(tmp_path,monkeypatch):
    path=marker(tmp_path,b'x'*(MAX_BYTES+1));original=Path.open
    def forbidden(candidate,*args,**kwargs):
        if candidate==path:pytest.fail('Oversize declaration must not be opened.')
        return original(candidate,*args,**kwargs)
    monkeypatch.setattr(Path,'open',forbidden)
    with pytest.raises(DomainError):declaration(tmp_path,'2512')


def test_unsupported_encoding_is_a_structured_conflict_not_a_server_error(tmp_path):
    marker(tmp_path,b'\x81set NX_INST_DIR=NX2512\n')
    with pytest.raises(DomainError) as error:declaration(tmp_path,'2512')
    assert error.value.status==409


@pytest.mark.parametrize('value',['W:/SiemensShare/nx2406','/mnt/w/blocked','//server/share'])
def test_protected_sidt_is_not_looked_up(monkeypatch,value):
    monkeypatch.setattr(Path,'stat',lambda *args,**kwargs:pytest.fail('Protected SIDT may not be inspected.'))
    monkeypatch.setattr(Path,'lstat',lambda *args,**kwargs:pytest.fail('Protected SIDT may not be inspected.'))
    with pytest.raises(DomainError):declaration(Path(value),'2512')


def test_sidt_marker_is_a_bound_input_not_an_extra_output_file(upgrade_pilot):
    from nxserver.migration import Planner
    client,store,request,source=upgrade_pilot;plan,report=Planner(store).build(request)
    assert report['prerequisites']['sidt_version_contract_version']==1
    for version in report['prerequisites']['versions']:
        proof=version['profile']['version_declaration']
        assert evidence(proof,version['nx_version']) and plan['inputs'][proof['path']]==proof['sha256']
    assert all(item['path']!=RELATIVE for item in report['result_manifest'])


def test_declaration_changed_during_planning_blocks_before_output(upgrade_pilot,monkeypatch):
    import nxserver.migration as migration
    client,store,request,source=upgrade_pilot;original=migration.prerequisites
    def changing(*args):
        result=original(*args);path=store.sidts['2512']/RELATIVE
        path.write_bytes(path.read_bytes()+b'\nREM Own test changes metadata bytes\n')
        return result
    monkeypatch.setattr(migration,'prerequisites',changing)
    with pytest.raises(DomainError,match='inputs changed'):migration.Planner(store).build(request)
    assert not Path(request['target_path']).exists() and not store.releases()


def test_declaration_changed_after_preview_requires_a_new_preview(upgrade_pilot):
    client,store,request,source=upgrade_pilot;job,report=preview(client,request)
    path=store.sidts['2512']/RELATIVE;path.write_bytes(path.read_bytes()+b'\nREM New harmless metadata comment\n')
    assert prerequisites(store,'2406','2512')['ready']
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='failed' and not Path(request['target_path']).exists() and not store.releases()


@pytest.mark.parametrize('fault',['missing_contract','bool_contract','missing_declaration','version','script','provenance','hash'])
def test_old_or_rehashed_false_sidt_evidence_does_not_authorize_new_build(upgrade_pilot,fault):
    import copy,json
    from nxserver.store import digest
    from nxserver.upgrades import Upgrades
    client,store,request,source=upgrade_pilot;job,original=preview(client,request);report=copy.deepcopy(original)
    check=report['prerequisites'];profile=check['versions'][1]['profile'];proof=profile['version_declaration']
    if fault=='missing_contract':check.pop('sidt_version_contract_version')
    elif fault=='bool_contract':check['sidt_version_contract_version']=True
    elif fault=='missing_declaration':profile.pop('version_declaration')
    elif fault=='version':proof['nx_version']='2406'
    elif fault=='script':proof['script_executed']=True
    elif fault=='provenance':proof['provenance_verified']=True
    else:proof['sha256']='not-a-sha'
    unsigned=copy.deepcopy(report);unsigned.pop('preview_hash');report['preview_hash']=digest(unsigned)
    raw=json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    (store.root/'upgrades'/job['id']/'report.json').write_bytes(raw)
    manager=Upgrades(store);manager._update(job['id'],report_sha256=hashlib.sha256(raw).hexdigest())
    assert manager.report(job['id'])==report # Historic text stays readable, paths are not executed.
    before=len(manager.list())
    response=client.post('/api/v1/migrations/build',headers=HEADERS,json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']})
    assert response.status_code==409 and len(manager.list())==before
    assert not Path(request['target_path']).exists() and not store.releases()


def test_static_extraction_rejects_wrong_declared_version_even_with_matching_root_name(tmp_path,monkeypatch):
    from test_extraction import fake_setup
    from nxserver.extraction import extract
    store,archive,calls=fake_setup(tmp_path,monkeypatch)
    path=tmp_path/'payload/{app}/nx2512'/RELATIVE
    path.write_bytes(b'set NX_INST_DIR=NX2406\n')
    with pytest.raises(DomainError,match='SIDT declares NX2406'):extract(store,'2512',tmp_path/'extracted')
    assert calls==['-v','-t','-x'] and store.sidt_root('2512') is None and not store.releases()


@pytest.mark.skipif(os.name!='nt' or os.environ.get('NX_STOCK_SCHEMA_TEST')!='1',reason='Opt-in read-only supplied SIDT declarations.')
@pytest.mark.parametrize('version',['2406','2512'])
def test_supplied_sidt_declaration_is_consistent_and_unchanged(version):
    import json
    from nxserver.safety import read_bounded_file
    settings=json.loads(read_bounded_file(offline_path(Path(__file__).parents[1]/'settings.example.json'),max_bytes=128*1024))
    root=offline_path(settings['sidts'][version]);value=declaration(root,version)
    assert evidence(value,version) and declaration(root,version)==value
