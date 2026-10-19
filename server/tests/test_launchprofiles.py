"""Managed UI-role selection, immutable artifacts and SQL gates; synthetic MTX."""
import copy
import hashlib
import json
import zipfile

import pytest

from nxserver.launchprofiles import LaunchProfiles,validate_profile
from nxserver.safety import DomainError
from nxserver.integrity import scan
from test_filepackages import packages,body,derive
from test_management import HEADERS,ERP_HEADERS
from test_testlaunch import executable_launcher,launcher


@pytest.fixture
def ui_role(tmp_path_factory):
    from fastapi.testclient import TestClient
    from nxserver.api import create_app
    from nxserver.store import Store,TOOL_DATABASE
    from test_management import ADMIN,ERP,DATABASE
    # Recovery preserves all historical launcher revisions. Keep the fixture
    # root short enough for the explicitly enforced Windows 255-character gate.
    root=tmp_path_factory.mktemp('lp');source=root/'s';database=source/TOOL_DATABASE
    database.parent.mkdir(parents=True);database.write_text(DATABASE)
    store=Store(root/'m',{'test':source});release=store.import_release('test','2512','Synthetic role source','fixture')
    path='UGII/menus/roles/FIRM.mtx'
    resource=store.save_resource(body(store,'ui_standard',path,filename='FIRM.mtx',content=b'<Role name="synthetic"/>'),'fixture')
    target=derive(store,release['id'])
    with TestClient(create_app(store,ADMIN,ERP)) as client:yield client,store,release,resource,target,path


def configure(store,target,resource,path):
    profiles=LaunchProfiles(store)
    report=profiles.preview(target['id'],resource['id'],path)
    return report,profiles.prepare(target['id'],resource['id'],path,report['preview_hash'],'fixture')


def test_explicit_profile_preview_prepare_retry_and_reset_preserve_every_source(ui_role):
    client,store,release,resource,target,path=ui_role
    before=store.manifest(store.release_folder(target['id']));old=store.test_launcher_package(target['id']);old_bytes=old.read_bytes();audit=len(store.audit_entries())
    profiles=LaunchProfiles(store);report=profiles.preview(target['id'],resource['id'],path)
    assert len(store.audit_entries())==audit and report['user_profiles_changed'] is False
    configured=profiles.prepare(target['id'],resource['id'],path,report['preview_hash'],'fixture')
    assert configured['ui_profile']['path']==path and configured['ui_profile']['revision']==resource['revision']
    assert configured['package_revision']!=target['test_launcher']['package_revision']
    assert old.read_bytes()==old_bytes and store.manifest(store.release_folder(target['id']))==before
    with zipfile.ZipFile(store.test_launcher_package(target['id'])) as archive:
        config=json.loads(archive.read('release_test.json'))
        assert config['UIProfile']==configured['ui_profile'] and '-ui-' in config['TestRoot']
        assert b'UGII_DEFAULT_ROLE' in archive.read('LIESMICH.txt')
    event=store.audit_entries()[0];assert event['action']=='release.test_launcher.ui_profile'
    assert any(item['table']=='test_launchers' for item in json.loads(event['payload'])['_state']['bindings'])
    audit=len(store.audit_entries());assert profiles.prepare(target['id'],resource['id'],path,report['preview_hash'],'fixture')==configured
    assert len(store.audit_entries())==audit and scan(store)['error_count']==0
    reset=profiles.preview(target['id']);default=profiles.prepare(target['id'],None,None,reset['preview_hash'],'fixture')
    assert default.get('ui_profile') is None and old.read_bytes()==old_bytes
    assert store.active()['release_id'] is None and store.resources()==[resource]


@pytest.mark.parametrize('mutation',['draft','retired','wrong_version','unexported_revision','unowned','not_role','missing_file','bytes','failed_release'])
def test_invalid_role_selection_never_changes_launcher(ui_role,mutation):
    client,store,release,resource,target,path=ui_role;before=target['test_launcher']
    if mutation in ('draft','retired','wrong_version','unexported_revision'):
        value={k:copy.deepcopy(resource[k]) for k in ('kind','name','cam','erp','cam_status','asset_ids','file_package')}
        if mutation in ('draft','retired'):value['cam_status']=mutation
        if mutation=='wrong_version':value['cam_status']='draft';value['file_package'].update(nx_version='2406',checks={},asset_hashes={})
        if mutation=='unexported_revision':value['name']='A newly revised UI standard'
        store.save_resource(value,'fixture',resource['id'],resource['revision'])
    elif mutation=='unowned':target=release
    elif mutation=='not_role':path='UGII/menus/profiles/FIRM.mtx'
    elif mutation=='missing_file':(store.release_folder(target['id'])/path).unlink()
    elif mutation=='bytes':(store.release_folder(target['id'])/path).write_bytes(b'<Role changed="true"/>')
    else:
        with store.db() as db:db.execute("UPDATE releases SET status='failed' WHERE id=?",(target['id'],))
    old=store.get_release(target['id'])['test_launcher'];audit=len(store.audit_entries())
    with pytest.raises(DomainError):LaunchProfiles(store).preview(target['id'],resource['id'],path)
    assert store.get_release(target['id'])['test_launcher']==old and len(store.audit_entries())==audit


@pytest.mark.parametrize('mutation',['resource','release_catalog','release_status','launcher','launcher_payload','bytes'])
def test_late_profile_context_drift_rolls_back_selected_launcher(ui_role,monkeypatch,mutation):
    client,store,release,resource,target,path=ui_role;profiles=LaunchProfiles(store)
    report=profiles.preview(target['id'],resource['id'],path);old=target['test_launcher'];original=store._create_test_launcher
    def changed(*args,**kwargs):
        built=original(*args,**kwargs)
        with store.db() as db:
            if mutation=='resource':
                value=json.loads(db.execute('SELECT payload FROM resources WHERE id=?',(resource['id'],)).fetchone()[0]);value['name']='same-revision drift'
                db.execute('UPDATE resources SET payload=? WHERE id=?',(json.dumps(value),resource['id']))
            elif mutation=='release_catalog':db.execute("UPDATE releases SET catalog='{}' WHERE id=?",(target['id'],))
            elif mutation=='release_status':db.execute("UPDATE releases SET status='failed' WHERE id=?",(target['id'],))
            elif mutation in ('launcher','launcher_payload'):
                value={**old,**({'package_revision':'f'*64} if mutation=='launcher' else {'windows_folder':r'C:\Different\Local\Folder'})};db.execute('UPDATE test_launchers SET payload=? WHERE release_id=?',(json.dumps(value),target['id']))
        if mutation=='bytes':(store.release_folder(target['id'])/path).write_bytes(b'<Role late="true"/>')
        return built
    monkeypatch.setattr(store,'_create_test_launcher',changed);audit=len(store.audit_entries())
    with pytest.raises(DomainError,match='context changed'):profiles.prepare(target['id'],resource['id'],path,report['preview_hash'],'fixture')
    current=store.get_release(target['id'])['test_launcher'];expected={**old,'package_revision':'f'*64} if mutation=='launcher' else {**old,'windows_folder':r'C:\Different\Local\Folder'} if mutation=='launcher_payload' else old;assert current==expected
    assert len(store.audit_entries())==audit and store.active()['release_id'] is None


def test_refresh_keeps_historical_role_after_resource_revision_changes(ui_role,monkeypatch):
    import nxserver.store as module
    client,store,release,resource,target,path=ui_role;report,selected=configure(store,target,resource,path)
    old=store.test_launcher_package(target['id']);old_bytes=old.read_bytes()
    resource['name']='Changed after the test package';value={k:resource[k] for k in ('kind','name','cam','erp','cam_status','asset_ids','file_package')}
    store.save_resource(value,'fixture',resource['id'],1)
    monkeypatch.setattr(module,'template_sha256',lambda:'d'*64)
    refreshed=store.prepare_test_launcher(target['id'],'fixture')
    assert refreshed['ui_profile']==selected['ui_profile'] and old.read_bytes()==old_bytes
    assert scan(store)['error_count']==0


def test_profile_api_is_admin_only_and_rejects_unbound_apply(ui_role):
    client,store,release,resource,target,path=ui_role;base='/api/v1/releases/'+target['id']+'/test-launcher/ui-profile'
    data={'resource_id':resource['id'],'path':path}
    assert client.post(base+'-preview',json=data).status_code==401
    assert client.post(base+'-preview',headers=ERP_HEADERS,json=data).status_code==403
    assert client.post(base,headers=ERP_HEADERS,json={**data,'preview_hash':'0'*64}).status_code==403
    assert client.post(base,headers=HEADERS,json={**data,'preview_hash':'0'*64}).status_code==409
    report=client.post(base+'-preview',headers=HEADERS,json=data).json()
    response=client.post(base,headers=HEADERS,json={**data,'preview_hash':report['preview_hash']})
    assert response.status_code==200,response.text
    assert response.json()['ui_profile']['resource_id']==resource['id']


@pytest.mark.parametrize('field,value',[('contract_version',True),('path',None),('path','W:/test.mtx'),('path','UGII/menus/profiles/FIRM.mtx'),('revision',True),('sha256','x'),('resource_id','invalid')])
def test_invalid_immutable_profile_contract_is_rejected(ui_role,field,value):
    _,store,_,resource,target,path=ui_role;profile=LaunchProfiles(store).preview(target['id'],resource['id'],path)['ui_profile'];profile[field]=value
    with pytest.raises(DomainError):validate_profile(profile,target['manifest'])


def test_selected_role_survives_isolated_backup_restore(ui_role):
    from pathlib import Path
    from nxserver.store import Store
    from fastapi.testclient import TestClient
    from nxserver.api import create_app
    from test_backups import create_backup,preview,finish,ADMIN,ERP,HEADERS as BACKUP_HEADERS
    client,store,release,resource,target,path=ui_role;_,selected=configure(store,target,resource,path)
    original=store.test_launcher_package(target['id']);original_bytes=original.read_bytes()
    with TestClient(create_app(store,ADMIN,ERP)) as backup_client:
        receipt=create_backup(backup_client);job,report=preview(backup_client,receipt['id'],'RoleRestore')
        assert report['can_restore'],report['errors']
        result=finish(backup_client,backup_client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=BACKUP_HEADERS,
            json={'name':'RoleRestore','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
        assert result['status']=='completed',result['message']
        restored=Store(Path(backup_client.app.state.backups.report(result['id'])['target_folder']))
    new=restored.get_release(target['id'])['test_launcher']
    assert new['ui_profile']==selected['ui_profile'] and original.read_bytes()==original_bytes
    assert restored.active()['release_id'] is None and scan(restored)['error_count']==0
    with zipfile.ZipFile(restored.test_launcher_package(target['id'])) as archive:
        assert json.loads(archive.read('release_test.json'))['UIProfile']==selected['ui_profile']


def test_real_windows_child_receives_role_and_old_test_directory_is_not_rebound(executable_launcher):
    """The compiled fixture is a harmless environment recorder, not Siemens NX."""
    import subprocess
    from pathlib import Path
    from test_testlaunch import package
    store,release,extracted,install,runtime,output,env,prepare=executable_launcher
    path='UGII/menus/roles/COMPANY.MTX'
    resource=store.save_resource(body(store,'ui_standard',path,filename='COMPANY.MTX',content=b'<Role name="synthetic"/>'),'fixture')
    target=derive(store,release['id']);default_files=package(store,target);_,selected=configure(store,target,resource,path)
    role_package=extracted.parent/'role-package';role_package.mkdir()
    for name,content in package(store,target).items():(role_package/name).write_bytes(content)
    role_runtime=runtime.parent/'role-runtime';role_output=output.parent/'role-output.txt';env['NX_TEST_ROLE_OUTPUT']=str(role_output)
    env['UGII_DEFAULT_ROLE']=r'W:\SiemensShare\nx2406\do-not-read.mtx'
    role_prepare=[*prepare];role_prepare[role_prepare.index(str(extracted/'Invoke-NXReleaseTest.ps1'))]=str(role_package/'Invoke-NXReleaseTest.ps1')
    role_prepare[role_prepare.index(str(extracted/'release_test.json'))]=str(role_package/'release_test.json')
    role_prepare[role_prepare.index(str(runtime))]=str(role_runtime)
    result=subprocess.run(role_prepare,capture_output=True,text=True,env=env,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    config=json.loads((role_runtime/'test_environment.json').read_text(encoding='utf-8-sig'))
    assert config['UIProfile']==selected['ui_profile'] and not role_output.exists()
    assert not (role_runtime/'user').exists() and not (role_runtime/'temp').exists()
    result=subprocess.run(role_prepare[:-1],capture_output=True,text=True,env=env,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    assert Path(role_output.read_text())==store.release_folder(target['id'])/path
    # SAME release and paths: a default test directory cannot become a UI test.
    default_package=extracted.parent/'default-package';default_package.mkdir()
    for name,content in default_files.items():(default_package/name).write_bytes(content)
    default_runtime=runtime.parent/'default-runtime';default_prepare=role_prepare.copy()
    default_prepare[default_prepare.index(str(role_package/'Invoke-NXReleaseTest.ps1'))]=str(default_package/'Invoke-NXReleaseTest.ps1')
    default_prepare[default_prepare.index(str(role_package/'release_test.json'))]=str(default_package/'release_test.json')
    default_prepare[default_prepare.index(str(role_runtime))]=str(default_runtime)
    result=subprocess.run(default_prepare,capture_output=True,text=True,env=env,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    collision=role_prepare.copy();collision[collision.index(str(role_runtime))]=str(default_runtime)
    result=subprocess.run(collision,capture_output=True,text=True,env=env,timeout=60)
    assert result.returncode!=0 and 'another UI-role selection' in result.stderr
    assert json.loads((default_runtime/'test_environment.json').read_text(encoding='utf-8-sig')).get('UIProfile') is None
    reverse=default_prepare.copy();reverse[reverse.index(str(default_runtime))]=str(role_runtime)
    result=subprocess.run(reverse,capture_output=True,text=True,env=env,timeout=60)
    assert result.returncode!=0 and 'another UI-role selection' in result.stderr
    assert json.loads((role_runtime/'test_environment.json').read_text(encoding='utf-8-sig'))['UIProfile']==selected['ui_profile']
