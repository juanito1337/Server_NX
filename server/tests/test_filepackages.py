"""File-package contracts, synthetic files/qualification; never actual NX execution."""
import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.filepackages import CHECKS,FilePackages
from nxserver.store import Store
from nxserver.store import canonical
from nxserver.integrity import scan
from test_management import HEADERS,ERP_HEADERS,ADMIN,ERP,DATABASE
from nxserver.store import TOOL_DATABASE
from nxserver.safety import DomainError
from test_upgrades import upgrade_pilot


@pytest.fixture
def packages(tmp_path):
    source=tmp_path/'source';path=source/TOOL_DATABASE;path.parent.mkdir(parents=True);path.write_text(DATABASE)
    standard=tmp_path/'standard';(standard/'UGII/templates').mkdir(parents=True)
    (standard/'UGII/menus/roles').mkdir(parents=True)
    store=Store(tmp_path/'managed',{'test':source},standards={'2512':standard})
    release=store.import_release('test','2512','Synthetic target','fixture')
    with TestClient(create_app(store,ADMIN,ERP)) as client:yield client,store,release,source,standard


def body(store,kind='template',path='UGII/templates/CUSTOM.prt',status='approved',filename='CUSTOM.prt',content=b'Synthetic PRT; not CAD'):
    asset=store.add_asset(filename,content,'fixture')
    return {'kind':kind,'name':'Managed file package','cam':{},'cam_status':status,'asset_ids':[asset['id']],'erp':None,
        'file_package':{'nx_version':'2512','entries':[{'asset_id':asset['id'],'path':path}],
            'checks':{name:True for name in CHECKS},'evidence':'Synthetic four checks; NOT an actual NX qualification.',
            'asset_hashes':{asset['id']:asset['sha256']},'provenance':None}}


def derive(store,parent):
    report=store.preview_export(parent)
    assert report['can_export'],report['conflicts']
    return store.import_release('','','Package export','fixture',parent=parent,preview_hash=report['preview_hash'])


def test_template_create_update_retire_and_reactivate_change_only_new_copies(packages):
    client,store,release,source,standard=packages;original=store.manifest(store.release_folder(release['id']))
    value=body(store);resource=store.save_resource(value,'fixture')
    first=derive(store,release['id']);file=store.release_folder(first['id'])/'UGII/templates/CUSTOM.prt'
    assert file.read_bytes()==b'Synthetic PRT; not CAD'
    assert resource['id'] in first['catalog']['exported_file_packages']
    new=body(store,content=b'Synthetic updated PRT; not CAD');store.save_resource(new,'fixture',resource['id'],1)
    second=derive(store,first['id']);assert (store.release_folder(second['id'])/'UGII/templates/CUSTOM.prt').read_bytes()==b'Synthetic updated PRT; not CAD'
    assert file.read_bytes()==b'Synthetic PRT; not CAD'
    new['cam_status']='retired';store.save_resource(new,'fixture',resource['id'],2)
    third=derive(store,second['id']);assert not (store.release_folder(third['id'])/'UGII/templates/CUSTOM.prt').exists()
    assert (store.release_folder(second['id'])/'UGII/templates/CUSTOM.prt').is_file()
    new['cam_status']='approved';store.save_resource(new,'fixture',resource['id'],3)
    fourth=derive(store,third['id']);assert (store.release_folder(fourth['id'])/'UGII/templates/CUSTOM.prt').is_file()
    assert store.active()['release_id'] is None and store.manifest(store.release_folder(release['id']))==original
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('mutation',['w','traversal','outside','rename','script','checks','hash','provenance'])
def test_invalid_or_forged_file_package_has_no_resource_or_nx_writes(packages,mutation):
    client,store,release,source,standard=packages;value=body(store);package=value['file_package']
    if mutation=='w':package['entries'][0]['path']='W:/SiemensShare/nx2406/file.prt'
    elif mutation=='traversal':package['entries'][0]['path']='UGII/templates/../CUSTOM.prt'
    elif mutation=='outside':package['entries'][0]['path']='MACH/resource/library/tool/graphics/CUSTOM.prt'
    elif mutation=='rename':package['entries'][0]['path']='UGII/templates/renamed.prt'
    elif mutation=='script':package['entries'][0]['path']='UGII/templates/script.py'
    elif mutation=='checks':package['checks']['nx_test']=False
    elif mutation=='hash':package['asset_hashes']={}
    else:package['provenance']={'release_id':release['id'],'owned_files':[]}
    before=store.manifest(store.release_folder(release['id']))
    response=client.post('/api/v1/resources',headers=HEADERS,json=value)
    assert response.status_code==400,response.text
    assert store.resources()==[] and store.manifest(store.release_folder(release['id']))==before


def test_ui_role_xml_supported_and_bad_xml_not_approved(packages):
    client,store,release,*_=packages
    value=body(store,'ui_standard','UGII/menus/roles/FIRM.mtx',filename='FIRM.mtx',content=b'<Role name="synthetic"/>')
    response=client.post('/api/v1/resources',headers=HEADERS,json=value);assert response.status_code==201,response.text
    new=derive(store,release['id']);assert (store.release_folder(new['id'])/'UGII/menus/roles/FIRM.mtx').is_file()
    bad=body(store,'ui_standard','UGII/menus/roles/BAD.mtx',filename='BAD.mtx',content=b'<not-closed>')
    assert client.post('/api/v1/resources',headers=HEADERS,json=bad).status_code==400
    assert client.post('/api/v1/resources',headers=ERP_HEADERS,json=value).status_code==403


def test_existing_unowned_file_cannot_be_claimed_even_with_identical_bytes(packages):
    client,store,release,source,standard=packages
    file=source/'UGII/templates/CUSTOM.prt';file.parent.mkdir(parents=True);file.write_bytes(b'Synthetic PRT; not CAD')
    target=store.import_release('test','2512','Existing unmanaged file','fixture')
    store.save_resource(body(store),'fixture')
    report=store.preview_export(target['id']);assert not report['can_export']
    assert len(store.releases())==2


def test_existing_customer_file_adoption_is_pinned_draft_then_editable_export(packages):
    client,store,release,source,standard=packages
    file=source/'UGII/templates/CUSTOM.prt';file.parent.mkdir(parents=True);file.write_bytes(b'Synthetic old customer PRT')
    target=store.import_release('test','2512','Old customer template','fixture');manager=FilePackages(store)
    report,_=manager.adoption_preview(target['id'],'template',['UGII/templates/CUSTOM.prt'])
    assert report['can_adopt'] and store.resources()==[]
    resource=manager.adopt(target['id'],'template',['UGII/templates/CUSTOM.prt'],'Customer template',None,report['preview_hash'],'fixture')
    assert resource['cam_status']=='draft' and resource['file_package']['provenance']
    unchanged=derive(store,target['id']);assert (store.release_folder(unchanged['id'])/'UGII/templates/CUSTOM.prt').read_bytes()==b'Synthetic old customer PRT'
    payload={key:copy.deepcopy(value) for key,value in resource.items() if key not in ('id','revision','updated_at')}
    payload['cam_status']='approved';package=payload['file_package'];package['checks']={name:True for name in CHECKS}
    package['evidence']='Synthetic existing-template qualification, not actual NX';package['asset_hashes']={asset_id:store.asset(asset_id)[0]['sha256'] for asset_id in resource['asset_ids']}
    store.save_resource(payload,'fixture',resource['id'],1)
    exported=derive(store,unchanged['id']);assert resource['id'] in exported['catalog']['exported_file_packages']
    assert scan(store)['error_count']==0


def test_siemens_standard_is_not_adopted_and_changed_preview_is_rejected(packages):
    client,store,release,source,standard=packages
    file=source/'UGII/templates/CUSTOM.prt';file.parent.mkdir(parents=True);file.write_bytes(b'Stock')
    (standard/'UGII/templates/CUSTOM.prt').write_bytes(b'Stock')
    target=store.import_release('test','2512','Stock template','fixture');manager=FilePackages(store)
    report,_=manager.adoption_preview(target['id'],'template',['UGII/templates/CUSTOM.prt']);assert not report['can_adopt']
    (standard/'UGII/templates/CUSTOM.prt').write_bytes(b'Other stock')
    later,_=manager.adoption_preview(target['id'],'template',['UGII/templates/CUSTOM.prt']);assert later['can_adopt']
    with pytest.raises(DomainError):manager.adopt(target['id'],'template',['UGII/templates/CUSTOM.prt'],'Template',None,report['preview_hash'],'fixture')
    assert store.resources()==[]


def test_version_change_requires_cleared_checks_then_explicit_new_qualification(packages):
    client,store,*_=packages;value=body(store);resource=store.save_resource(value,'fixture')
    value['file_package']['nx_version']='2606';value['cam_status']='draft'
    with pytest.raises(DomainError,match='cleared package checks'):store.save_resource(value,'fixture',resource['id'],1)
    value['file_package']['checks']={check:False for check in CHECKS};value['file_package']['asset_hashes']={}
    new=store.save_resource(value,'fixture',resource['id'],1)
    assert new['revision']==2 and new['file_package']['nx_version']=='2606'
    value['cam_status']='approved'
    with pytest.raises(DomainError,match='all four'):store.save_resource(value,'fixture',resource['id'],2)


def test_adoption_api_is_admin_only_pinned_and_read_only_until_apply(packages):
    client,store,release,source,standard=packages
    file=source/'UGII/templates/CUSTOM.prt';file.parent.mkdir(parents=True);file.write_bytes(b'Synthetic customer file')
    target=store.import_release('test','2512','Customer files','fixture');prefix='/api/v1/releases/'+target['id']+'/file-packages'
    payload={'kind':'template','paths':['UGII/templates/CUSTOM.prt']};before=store.audit_entries()
    assert client.get(prefix+'?kind=template').status_code==401
    assert client.post(prefix+'/preview',headers=ERP_HEADERS,json=payload).status_code==403
    preview=client.post(prefix+'/preview',headers=HEADERS,json=payload);assert preview.status_code==200,preview.text
    assert store.audit_entries()==before and store.resources()==[]
    apply={**payload,'name':'Customer package','preview_hash':preview.json()['preview_hash']}
    assert client.post(prefix+'/adopt',headers=ERP_HEADERS,json=apply).status_code==403
    response=client.post(prefix+'/adopt',headers=HEADERS,json=apply);assert response.status_code==200,response.text
    assert response.json()['cam_status']=='draft' and len(store.resources())==1
    assert client.post(prefix+'/adopt',headers=HEADERS,json=apply).status_code==409
    assert len(store.resources())==1 and file.read_bytes()==b'Synthetic customer file'


@pytest.mark.parametrize('content',[b'',b'<bad>',b'x'*(20*1024*1024+1)],ids=['empty','invalid-xml','oversized'])
def test_bad_or_oversized_existing_package_is_not_offered_for_adoption(packages,content):
    client,store,release,source,standard=packages
    file=source/'UGII/templates/BAD.xml';file.parent.mkdir(parents=True);file.write_bytes(content)
    target=store.import_release('test','2512','Invalid customer files','fixture')
    report,_=FilePackages(store).adoption_preview(target['id'],'template',['UGII/templates/BAD.xml'])
    assert not report['can_adopt'] and store.resources()==[]


def test_metadata_integrity_checks_package_bindings_even_without_file_scan(packages):
    client,store,*_=packages;value=body(store);resource=store.save_resource(value,'fixture')
    assert scan(store,include_files=False)['error_count']==0
    value['file_package']['entries'][0]['path']='outside/CUSTOM.prt'
    with store.db() as db:
        db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(value),resource['id']))
        db.execute('UPDATE resource_versions SET payload=? WHERE id=?',(canonical(value),resource['id']))
    report=scan(store,include_files=False)
    assert 'invalid_file_package' in {error['code'] for error in report['errors']}


def test_file_package_backup_restore_preserves_provenance_assets_and_export_registry(packages):
    from nxserver.backups import FUTURES
    client,store,release,source,standard=packages
    file=source/'UGII/templates/CUSTOM.prt';file.parent.mkdir(parents=True);file.write_bytes(b'Synthetic PRT; not CAD')
    release=store.import_release('test','2512','Customer package','fixture');manager=FilePackages(store)
    report,_=manager.adoption_preview(release['id'],'template',['UGII/templates/CUSTOM.prt'])
    resource=manager.adopt(release['id'],'template',['UGII/templates/CUSTOM.prt'],'Template',None,report['preview_hash'],'fixture')
    value={key:copy.deepcopy(item) for key,item in resource.items() if key not in ('id','revision','updated_at')}
    value['cam_status']='approved';value['file_package'].update(checks={name:True for name in CHECKS},
        evidence='Synthetic qualification, not an actual NX test',asset_hashes={asset:store.asset(asset)[0]['sha256'] for asset in value['asset_ids']})
    store.save_resource(value,'fixture',resource['id'],1);exported=derive(store,release['id'])
    def finish(response):
        assert response.status_code==202,response.text
        job=response.json();FUTURES[job['id']].result(timeout=30)
        result=client.get('/api/v1/backup-jobs/'+job['id'],headers=HEADERS).json()
        assert result['status']=='completed',(result.get('phase'),result.get('message'))
        return result,client.app.state.backups.report(job['id'])
    _,receipt=finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    job,report=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'R'}))
    assert report['can_restore'],report
    response,_=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={
        'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    assert response['status']=='completed',response
    restored=Store(Path(client.app.state.backups.report(response['id'])['target_folder']))
    assert restored.resources()==store.resources()
    assert restored.get_release(exported['id'])['catalog']['exported_file_packages']==exported['catalog']['exported_file_packages']
    assert restored.asset_content(resource['asset_ids'][0])[1]==b'Synthetic PRT; not CAD'
    assert scan(restored)['error_count']==0 and restored.active()['release_id'] is None


@pytest.mark.parametrize('kind,identifier,prefix',[('machine','machine','MACH/resource/library/machine/installed_machines/'),
    ('fixture','fixture-metric','MACH/resource/library/fixture_automation/graphics/')])
def test_machine_and_fixture_package_exports_native_record_and_explicit_files(packages,kind,identifier,prefix):
    from nxserver.libraries import SPECS,NativeTable,native_cam
    from test_libraries import simple,write
    client,store,release,source,standard=packages
    write(source,SPECS[identifier]['path'],simple());(standard/prefix).mkdir(parents=True)
    write(source,prefix+'CUSTOM.prt','Synthetic machine/fixture PRT')
    target=store.import_release('test','2512','Machine/fixture target','fixture')
    table=NativeTable(simple('CUSTOM','Customer DAT'))
    value={'kind':kind,'name':'Customer native object','cam':native_cam(identifier,'2512',table,table.records),
        'cam_status':'draft','erp':None,'asset_ids':[]}
    native=store.save_resource(value,'fixture');manager=FilePackages(store)
    with pytest.raises(DomainError,match='saved machine/fixture'):manager.adoption_preview(target['id'],kind,[prefix+'CUSTOM.prt'])
    report,_=manager.adoption_preview(target['id'],kind,[prefix+'CUSTOM.prt'],native['id'])
    resource=manager.adopt(target['id'],kind,[prefix+'CUSTOM.prt'],'Unused for existing draft',native['id'],report['preview_hash'],'fixture')
    value={key:copy.deepcopy(item) for key,item in resource.items() if key not in ('id','revision','updated_at')}
    value['cam_status']='approved';value['file_package'].update(checks={name:True for name in CHECKS},
        evidence='Synthetic checks, not actual NX or machine approval',asset_hashes={asset:store.asset(asset)[0]['sha256'] for asset in value['asset_ids']})
    response=client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={**value,'expected_revision':2})
    assert response.status_code==200,response.text
    new=derive(store,target['id']);folder=store.release_folder(new['id'])
    assert set(NativeTable.read(folder/SPECS[identifier]['path']).groups())=={'STD','CUSTOM'}
    assert (folder/(prefix+'CUSTOM.prt')).read_bytes()==b'Synthetic machine/fixture PRT'
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('old_binding',['file_bindings','model_package'])
def test_package_does_not_silently_override_another_explicit_binding(packages,old_binding):
    client,store,*_=packages;value=body(store)
    value[old_binding]=[{'asset_id':value['asset_ids'][0],'path':'CUSTOM.prt'}] if old_binding=='file_bindings' else {'result_id':'old-result'}
    response=client.post('/api/v1/resources',headers=HEADERS,json=value)
    assert response.status_code==400 and 'one explicit file-binding mechanism' in response.json()['detail']
    assert store.resources()==[]


def test_minimal_draft_package_has_persisted_gui_defaults(packages):
    client,store,*_=packages;value=body(store,status='draft')
    for key in ('checks','asset_hashes','evidence','provenance'):value['file_package'].pop(key)
    response=client.post('/api/v1/resources',headers=HEADERS,json=value);assert response.status_code==201,response.text
    package=response.json()['file_package']
    assert package['checks']=={} and package['asset_hashes']=={} and package['evidence']=='' and package['provenance'] is None


@pytest.mark.parametrize('choice',['customer','standard'])
def test_upgrade_preserves_file_ownership_but_requires_new_version_qualification(upgrade_pilot,choice):
    from test_upgrades import preview,finish
    client,store,request,source=upgrade_pilot
    path='UGII/templates/CUSTOM.prt';(source/'UGII/templates').mkdir(parents=True)
    (source/path).write_bytes(b'Synthetic customer template')
    (store.standards['2406']/'UGII/templates').mkdir(parents=True)
    (store.standards['2406']/path).write_bytes(b'Synthetic old Siemens template')
    old=store.import_release('source','2406','Old share with template','fixture')
    # Adopt into managed ownership, rather than claiming an existing unowned file.
    manager=FilePackages(store);report,_=manager.adoption_preview(old['id'],'template',[path])
    resource=manager.adopt(old['id'],'template',[path],'Old template',None,report['preview_hash'],'fixture')
    payload={key:copy.deepcopy(item) for key,item in resource.items() if key not in ('id','revision','updated_at')}
    payload['cam_status']='approved';payload['file_package'].update(checks={name:True for name in CHECKS},
        evidence='Synthetic old version test only',asset_hashes={asset:store.asset(asset)[0]['sha256'] for asset in payload['asset_ids']})
    store.save_resource(payload,'fixture',resource['id'],1);tracked=derive(store,old['id'])
    before=store.manifest(store.release_folder(tracked['id']));history=store.history(resource['id'])
    request.update(source=None,source_path=None,source_release_id=tracked['id'],file_resolutions={path:choice})
    job,proposal=preview(client,request);assert proposal['can_build'],proposal['conflicts']
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,json={
        **request,'preview_id':job['id'],'preview_hash':proposal['preview_hash']}));assert result['status']=='completed',result
    migrated=store.get_release(result['release_id']);record=migrated['catalog']['exported_file_packages'][resource['id']]
    assert record['qualification_retained'] is False and store.history(resource['id'])==history
    assert store.manifest(store.release_folder(tracked['id']))==before
    assert not store.preview_export(migrated['id'])['can_export']  # Still NX2406-qualified.
    payload['cam_status']='draft';payload['file_package'].update(nx_version='2512',checks={},asset_hashes={})
    new=store.save_resource(payload,'fixture',resource['id'],2)
    payload['cam_status']='approved';payload['file_package'].update(checks={name:True for name in CHECKS},
        evidence='Synthetic new version checks, not an actual NX test',asset_hashes={asset:store.asset(asset)[0]['sha256'] for asset in payload['asset_ids']})
    store.save_resource(payload,'fixture',resource['id'],new['revision'])
    report=store.preview_export(migrated['id'])
    if choice=='customer':
        assert record['entries'][0]['path']==path and report['can_export'],report['conflicts']
        final=derive(store,migrated['id']);assert (store.release_folder(final['id'])/path).read_bytes()==b'Synthetic customer template'
    else:
        assert record['entries']==[] and record['exclusions'][0]['path']==path
        assert not report['can_export'] and 'omitted/replaced' in str(report['conflicts'])
        assert not (store.release_folder(migrated['id'])/path).exists()
