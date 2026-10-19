"""Static reader contracts; optional real Siemens EXEs are local, never installed/run."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.config import extraction_settings
from nxserver.extraction import extract,installer_info,listing,run_reader
from nxserver.safety import DomainError,offline_path
from nxserver.store import Store
from nxserver.upgrades import LIVE
from test_management import ADMIN,HEADERS,ERP_HEADERS,ERP
from test_upgrades import installation


@pytest.mark.parametrize('path',['../escape','{app}/nx2512/../escape','W:/SiemensShare/nx2406/a',
    '//server/share/a','{app}/nx2512/CON.txt','{app}/nx2512/x:stream','{app}/nx2512/a\\..\\x'])
def test_unsafe_listing_paths_are_rejected_before_any_extraction(path):
    with pytest.raises(DomainError):listing(' 123 2026-01-09 12:00 '+path)


def test_listing_has_sizes_and_rejects_duplicates_and_unknown_roots():
    text=' 1 2026-01-09 12:00 {app}\\nx2512\\one.txt\n'
    assert listing(text)==[{'path':'{app}/nx2512/one.txt','size':1}]
    for bad in (text+text,text+' 1 2026-01-09 12:00 {app}\\nx2512\\ONE.txt',
                ' 1 2026-01-09 12:00 {sys}\\file.dll','unparseable listing'):
        with pytest.raises(DomainError):listing(bad)
    assert listing(' 32 2026-01-09 12:00 install_script.iss')[0]['path']=='install_script.iss'


def fake_setup(tmp_path,monkeypatch):
    import shutil
    from nxserver import extraction
    archive=tmp_path/'Setup.exe';archive.write_bytes(b'Inno Setup Setup Data (6.7.0)\x00Synthetic test only')
    tool=tmp_path/'tools/innounp.exe';tool.parent.mkdir();tool.write_bytes(b'Fake tool NEVER executed')
    store=Store(tmp_path/'managed')
    extraction_settings(store,{'installers':{'2512':{'path':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}},
        'extractor':{'kind':'innounp','path':str(tool),'sha256':hashlib.sha256(tool.read_bytes()).hexdigest()}})
    sidt=tmp_path/'payload'/'{app}'/'nx2512'
    installation(tmp_path/'fixture-install',sidt,'2512')
    files=[(p.relative_to(sidt.parent.parent).as_posix(),p) for p in sidt.rglob('*') if p.is_file()]
    calls=[]
    def fake_reader(store,tool,arguments,child=lambda *args:None):
        calls.append(arguments[0])
        if arguments[0]=='-v':return '\n'.join(' '+str(p.stat().st_size)+' 2026-01-09 12:00 '+relative.replace('/','\\') for relative,p in files)
        if arguments[0]=='-x':
            output=Path(next(argument[2:] for argument in arguments if argument.startswith('-d')))
            shutil.copytree(sidt.parent.parent,output)
        return ''
    monkeypatch.setattr(extraction,'tool_info',lambda store:{'kind':'innounp','path':str(tool),'sha256':store.extractor['sha256']})
    monkeypatch.setattr(extraction,'run_reader',fake_reader)
    return store,archive,calls


def test_job_requires_explicit_selection_and_pins_every_file(tmp_path,monkeypatch):
    store,archive,calls=fake_setup(tmp_path,monkeypatch)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        assert client.post('/api/v1/migrations/extract',json={'version':'2512'}).status_code==401
        assert client.post('/api/v1/migrations/extract',headers=ERP_HEADERS,json={'version':'2512'}).status_code==403
        assert client.post('/api/v1/migrations/extract',headers=HEADERS,json={'version':'2512','path':str(archive)}).status_code==422
        response=client.post('/api/v1/migrations/extract',headers=HEADERS,json={'version':'2512'})
        assert response.status_code==202,response.text
        identifier=response.json()['id'];LIVE[identifier].result(timeout=10)
        job=client.get('/api/v1/migrations/'+identifier,headers=HEADERS).json()
        assert job['status']=='completed',job
        report=client.get('/api/v1/migrations/'+identifier+'/report',headers=HEADERS).json()
        assert not report['installer_executed'] and calls==['-v','-t','-x']
        assert store.sidt_root('2512') is None
        assert client.post('/api/v1/migrations/'+identifier+'/select-input',headers=ERP_HEADERS,
            json={'expected_generation':0}).status_code==403
        assert client.post('/api/v1/migrations/'+identifier+'/select-input',headers=HEADERS,
            json={'expected_generation':0}).status_code==200
        assert store.sidt_root('2512')==Path(report['sidt_root'])
        assert client.post('/api/v1/migrations/'+identifier+'/select-input',headers=HEADERS,
            json={'expected_generation':0}).status_code==409
        path=Path(report['sidt_root'])/'applications/nx/after_install.bat'
        path.write_bytes(path.read_bytes()+b'\nChanged outside API')
        with pytest.raises(DomainError,match='immutable manifest'):store.sidt_root('2512')
        assert client.get('/api/v1/migrations/options',headers=HEADERS).status_code==200
        assert store.releases()==[] and store.active()['generation']==0


def test_installer_fingerprint_and_format_are_not_inferred_from_filename(tmp_path,monkeypatch):
    store,archive,calls=fake_setup(tmp_path,monkeypatch)
    archive.write_bytes(archive.read_bytes()+b'changed')
    with pytest.raises(DomainError,match='SHA'):installer_info(store,'2512')
    archive.write_bytes(b'Inno Setup Setup Data (7.9.9)')
    store.installers['2512']['sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(DomainError,match='Unsupported'):installer_info(store,'2512')
    assert calls==[]


@pytest.mark.parametrize('scenario',['ok','overflow','timeout','bad_utf8','failed'])
def test_reader_is_pinned_bounded_and_does_not_inherit_credentials(tmp_path,monkeypatch,scenario):
    from nxserver import extraction
    import subprocess
    store=Store(tmp_path/'store');tool=tmp_path/'reader.exe';tool.write_bytes(b'Not executable test fixture')
    captured={};events=[]
    monkeypatch.setenv('NX_ADMIN_TOKEN','synthetic-secret-never-printed')
    monkeypatch.setenv('NX_ERP_TOKEN_FILE','synthetic-private-path')
    monkeypatch.setenv('PROVIDER_API_KEY','synthetic-provider-key')
    class Process:
        pid=999999
        returncode=None
        def __init__(self,command,**kwargs):
            captured.update(command=command,**kwargs)
            kwargs['stdout'].write(b'x'*(extraction.MAX_READER_OUTPUT+1) if scenario=='overflow' else
                b'\xff' if scenario=='bad_utf8' else b'file listing')
            kwargs['stdout'].flush()
        def wait(self,timeout=None):
            if scenario=='timeout' and timeout:raise subprocess.TimeoutExpired('fake reader',timeout)
            self.returncode=1 if scenario=='failed' else (self.returncode or 0)
            return self.returncode
        def poll(self):return self.returncode
        def kill(self):captured['killed']=True;self.returncode=-1
    monkeypatch.setattr(extraction.subprocess,'Popen',Process)
    if scenario=='timeout':
        clock=iter([0,301]);monkeypatch.setattr(extraction.time,'monotonic',lambda:next(clock))
    info={'path':str(tool),'sha256':hashlib.sha256(tool.read_bytes()).hexdigest()}
    if scenario=='ok':assert run_reader(store,info,['-v','input.exe'],events.append)=='file listing'
    else:
        with pytest.raises(DomainError):run_reader(store,info,['-v','input.exe'],events.append)
    assert captured['shell'] is False and captured['stdin']==subprocess.DEVNULL
    assert not {'NX_ADMIN_TOKEN','NX_ERP_TOKEN_FILE','PROVIDER_API_KEY'}&set(captured['env'])
    assert captured['cwd']==str(tool.parent)
    assert events==[Process.pid,None]
    assert bool(captured.get('killed'))==(scenario in ('overflow','timeout'))
    before=dict(captured);tool.write_bytes(b'tampered')
    with pytest.raises(DomainError,match='changed before use'):run_reader(store,info,['-v'])
    assert captured==before


@pytest.mark.parametrize('child_state',['alive','unknown','gone','reused'])
def test_recovery_does_not_release_writer_while_owned_reader_may_still_run(tmp_path,monkeypatch,child_state):
    from nxserver.upgrades import Upgrades
    from nxserver.store import canonical
    import uuid
    store=Store(tmp_path/'managed');identifier=str(uuid.uuid4())
    payload={'id':identifier,'status':'extracting','mode':'extract','owner_pid':12345678,
        'owner_stamp':'parent-birth','child_pid':87654321,'child_stamp':'child-birth'}
    with store.db() as db:db.execute('INSERT INTO migration_jobs VALUES(?,?)',(identifier,canonical(payload)))
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid: None if pid==12345678 else
        {'alive':'child-birth','unknown':'unknown','gone':None,'reused':'another-child-birth'}[child_state])
    status=Upgrades(store).get(identifier)['status']
    assert status==('extracting' if child_state in ('alive','unknown') else 'interrupted')


def test_input_selection_is_locked_during_another_writer_and_failures_preserve_evidence(tmp_path,monkeypatch):
    from nxserver.upgrades import Upgrades
    from nxserver.store import canonical
    import uuid
    store,archive,calls=fake_setup(tmp_path,monkeypatch);upgrades=Upgrades(store)
    job=upgrades.create_extraction('2512','isolated fixture');LIVE[job['id']].result(timeout=10)
    identifier=str(uuid.uuid4())
    with store.db() as db:db.execute('INSERT INTO migration_jobs VALUES(?,?)',(identifier,canonical({'id':identifier,'status':'extracting'})))
    with pytest.raises(DomainError,match='active'):upgrades.select_extraction(job['id'],0,'fixture')
    with pytest.raises(DomainError,match='active'):upgrades.create_extraction('2512','fixture')
    with store.db() as db:db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical({'id':identifier,'status':'failed'}),identifier))
    monkeypatch.setattr('nxserver.extraction.run_reader',lambda *args: (_ for _ in ()).throw(DomainError('Synthetic reader failure')))
    failed=upgrades.create_extraction('2512','fixture');LIVE[failed['id']].result(timeout=10)
    assert upgrades.get(failed['id'])['status']=='failed'
    assert upgrades.get(failed['id'])['partial_output_retained']
    assert (store.root/'upgrades'/failed['id']/'extraction/input.exe').read_bytes()==archive.read_bytes()
    assert store.sidt_root('2512') is None


def test_archive_reader_directory_cannot_be_imported_or_used_as_output(tmp_path,monkeypatch):
    from nxserver.folders import FolderAccess
    store,archive,calls=fake_setup(tmp_path,monkeypatch)
    folders=FolderAccess(store,{'inputs':tmp_path},{'outputs':tmp_path})
    tool=Path(store.extractor['path']).parent
    with pytest.raises(DomainError):folders.source('inputs',str(tool))
    with pytest.raises(DomainError):folders.browse('source','inputs',str(tool))
    with pytest.raises(DomainError):folders.destination('outputs',str(tool/'new-share'),tmp_path/'payload')
    assert tool.name not in [item['name'] for item in folders.browse('source','inputs')['children']]
    assert calls==[]


@pytest.mark.skipif(os.name!='nt' or os.environ.get('NX_EXTRACTOR_TEST')!='1',reason='Opt-in real local static archive extraction.')
@pytest.mark.parametrize('version',['2406','2512'])
def test_actual_supplied_installers_extract_without_running_them(tmp_path,version):
    settings_path=offline_path(Path(__file__).parents[1]/'settings.example.json')
    settings=json.loads(settings_path.read_text(encoding='utf-8-sig'))
    store=Store(tmp_path/'isolated')
    extraction_settings(store,settings)
    original=installer_info(store,version)
    result=extract(store,version,tmp_path/'extracted')
    assert result['version']==version and result['profile']['copy_instructions']==13
    assert not result['installer_executed'] and result['file_count']>100
    assert installer_info(store,version)['sha256']==original['sha256']
    # Only the source application tree is compared; helper-generated diagnostics
    # are not Siemens originals and never copied into a managed NX share.
    previous=offline_path(settings['sidts'][version])
    prior=store.manifest(previous)
    current=store.manifest(offline_path(result['sidt_root']))
    old_by_path={item['path']:item for item in prior}
    new_by_path={item['path']:item for item in current}
    assert all(new_by_path.get(path)==item for path,item in old_by_path.items())
    # Previous innoextract staging omitted this zero-byte placeholder; innounp
    # lists and extracts it from both original archives. No other deviation.
    assert set(new_by_path)-set(old_by_path)=={'applications/nx/nxplot/temp_models.user'}
    assert new_by_path['applications/nx/nxplot/temp_models.user']['size']==0
    assert new_by_path['applications/nx/nxplot/temp_models.user']['sha256']==hashlib.sha256(b'').hexdigest()
