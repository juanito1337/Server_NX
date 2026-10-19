"""Whole-folder upgrade jobs, with synthetic installations. Never invokes NX/installer."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.libraries import NativeTable, SPECS, OPTIONAL_PALETTE, normalization_plan
from nxserver.migration import FILES,TREES,OVERLAYS,Planner,after_install_profile
from nxserver.store import Store,canonical
from nxserver.upgrades import LIVE,Upgrades
from test_management import ADMIN,HEADERS,ERP_HEADERS
from test_libraries import simple,write
from installation_version_test_support import write_nx_image


def installation(root,sidt,version):
    for folder in TREES:(root/folder).mkdir(parents=True,exist_ok=True)
    for name in FILES:write(root,'UGII/'+name,'Stock fixture')
    write_nx_image(root,version)
    write(sidt,'start_apps/env/nx_inst_dir.cmd','set NX_INST_DIR=NX'+version+'\n')
    for source,target in OVERLAYS:(sidt/source).mkdir(parents=True,exist_ok=True)
    prefix='%SPLM_APPL_DIR%\\%NX_VERSION_DIR%\\';target='%SPLM_SHR_DIR%\\%NX_SHR_VERSION_DIR%\\'
    commands=['xcopy %OPT% '+prefix+'ugii\\'+name+' '+target+'ugii > NUL' for name in FILES]
    commands+=['xcopy %OPT1% '+prefix+name.replace('/','\\')+' '+target+name.replace('/','\\')+' > NUL' for name in TREES]
    commands+=['xcopy %OPT1% %SIDT_DIR%\\'+source.replace('/','\\')+' '+target+destination.replace('/','\\')+' > NUL' for source,destination in OVERLAYS]
    write(sidt,'applications/nx/after_install.bat','\n'.join(commands))


@pytest.fixture
def upgrade_pilot(tmp_path):
    old,new,source=tuple(tmp_path/name for name in ('old','new','source'))
    sidt_old,sidt_new=tmp_path/'sidt-old',tmp_path/'sidt-new'
    installation(old,sidt_old,'2406');installation(new,sidt_new,'2512')
    spec=SPECS['holder-metric']
    write(old,spec['path'],simple(descr='Old Siemens'))
    write(new,spec['path'],simple(descr='New Siemens'))
    write(source,spec['path'],simple(descr='Old Siemens').replace('#END_DATA','DATA |CUSTOM|Customer\n#END_DATA'))
    write(source,'MACH/custom/customer.txt','Customer customization')
    write(source,'MACH/auxiliary/standard_db.xml','Out of share scope; never migrate')
    write(source,OPTIONAL_PALETTE,'<Palette></Palette><PaletteEntry/>')
    output=tmp_path/'output';output.mkdir()
    store=Store(tmp_path/'managed',{'source':source},standards={'2406':old,'2512':new},
        target_roots={'output':output},sidts={'2406':sidt_old,'2512':sidt_new})
    request={'source':'source','source_path':str(source),'target':'output','target_path':str(output/'NX2512'),
        'source_version':'2406','target_version':'2512','name':'Upgrade','defaults':{},'resolutions':{},
        'file_resolutions':{},'legacy_omissions':[],'source_release_id':None}
    with TestClient(create_app(store,ADMIN,'isolated-upgrade-erp-'+'e'*40)) as client:
        yield client,store,request,source
    # Observe owned real futures, not stored flags, before test fixture teardown.
    for job in Upgrades(store).list():
        if job['id'] in LIVE:LIVE[job['id']].result(timeout=15)


def finish(client,response):
    assert response.status_code==202,response.text
    identifier=response.json()['id'];LIVE[identifier].result(timeout=15)
    job=client.get('/api/v1/migrations/'+identifier,headers=HEADERS).json()
    return job


def preview(client,request):
    job=finish(client,client.post('/api/v1/migrations/preview',headers=HEADERS,json=request))
    assert job['status']=='completed',job
    report=client.get('/api/v1/migrations/'+job['id']+'/report',headers=HEADERS).json()
    return job,report


def test_old_and_new_identical_schemas_do_not_roll_back_new_siemens_records(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    outputs,report=normalization_plan(source,store.standards,'2512','2406',source_version='2406')
    groups=NativeTable(outputs[SPECS['holder-metric']['path']]).groups()
    assert groups['STD'][0]['values']['DESCR']=='New Siemens'
    assert groups['CUSTOM'][0]['values']['DESCR']=='Customer'
    assert report['can_import']


def test_read_only_preview_and_real_build_registers_immutable_release_and_test_launcher(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    before=store.manifest(source)
    job,report=preview(client,request)
    assert report['can_build'] and not Path(request['target_path']).exists()
    assert store.releases()==[] and store.manifest(source)==before
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='completed',result
    release=store.get_release(result['release_id'])
    assert release['status']=='draft' and release['test_launcher']
    target=Path(request['target_path'])
    assert (target/'MACH/custom/customer.txt').read_text()=='Customer customization'
    assert not (target/'MACH/auxiliary/standard_db.xml').exists()
    assert not (target/OPTIONAL_PALETTE).exists() and (source/OPTIONAL_PALETTE).exists()
    assert (target/'CMM_INSPECTION/custom').is_dir()
    assert NativeTable.read(target/SPECS['holder-metric']['path']).groups()['STD'][0]['values']['DESCR']=='New Siemens'
    assert store.manifest(source)==before and store.active()['generation']==0


def test_file_conflicts_require_explicit_choice_and_are_bound_to_the_preview(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    relative='MACH/custom/conflict.txt'
    write(store.standards['2406'],relative,'old')
    write(store.standards['2512'],relative,'new')
    write(source,relative,'customer')
    job,report=preview(client,request)
    assert not report['can_build'] and any(c.get('path')==relative for c in report['conflicts'])
    assert client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}).status_code==409
    request['file_resolutions'][relative]='customer'
    chosen,ready=preview(client,request)
    assert ready['can_build']
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':chosen['id'],'preview_hash':ready['preview_hash']}))
    assert result['status']=='completed'
    assert (Path(request['target_path'])/relative).read_text()=='customer'


def test_new_columns_and_source_drift_block_build_without_writing_target(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    spec=SPECS['holder-metric'];write(store.standards['2512'],spec['path'],simple(extra=True))
    job,report=preview(client,request)
    assert not report['can_build'] and report['new_columns'][0]['keys']==['CUSTOM']
    request['defaults']={'holder-metric':{'SAMPLE:1':{'EXTRA':'confirmed'}}}
    job,report=preview(client,request)
    assert report['can_build']
    write(source,'MACH/custom/customer.txt','Changed after preview')
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='failed' and not Path(request['target_path']).exists()
    assert store.releases()==[]


def test_unknown_sidt_layout_and_unsupported_pair_are_not_guessed(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    report=client.get('/api/v1/migrations/options',headers=HEADERS).json()
    assert report['adapters'][0]['ready'] and not report['exe_extraction']
    request['target_version']='2612'
    assert client.post('/api/v1/migrations/preview',headers=HEADERS,json=request).status_code==400
    request['target_version']='2512'
    write(store.sidts['2512'],'applications/nx/after_install.bat','xcopy %OPT% unexpected somewhere')
    assert client.post('/api/v1/migrations/preview',headers=HEADERS,json=request).status_code==409
    assert store.releases()==[]


def test_protected_paths_roles_and_existing_targets_are_blocked_before_jobs(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    assert client.post('/api/v1/migrations/preview',json=request).status_code==401
    assert client.get('/api/v1/migrations',headers=ERP_HEADERS).status_code in (401,403)
    for key in ('source_path','target_path'):
        assert client.post('/api/v1/migrations/preview',headers=HEADERS,
            json={**request,key:r'W:\SiemensShare\nx2406'}).status_code==400
    Path(request['target_path']).mkdir()
    assert client.post('/api/v1/migrations/preview',headers=HEADERS,json=request).status_code==409
    assert Upgrades(store).list()==[]


def test_missing_nx_installation_is_visible_as_a_prerequisite_failure(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    (store.standards['2512']/'NXBIN/ugraf.exe').unlink()  # Test fixture only.
    options=client.get('/api/v1/migrations/options',headers=HEADERS).json()
    assert not options['adapters'][0]['ready']
    assert client.post('/api/v1/migrations/preview',headers=HEADERS,json=request).status_code==409


def test_custom_dirs_is_targeted_text_conversion_never_a_production_lookup(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    path='MACH/resource/usertools/ugii/menus/custom_dirs.dat'
    raw='W:\\SiemensShare\\nx2406\\MACH\\resource\n'
    write(source,path,raw)
    plan,report=Planner(store).build(request)
    item=next(item for item in plan['files'] if item['path']==path)
    assert request['target_path'] in item['content'].decode('utf-8-sig')
    assert (source/path).read_text()==raw


def test_stored_report_tampering_is_detected(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    job,report=preview(client,request)
    path=store.root/'upgrades'/job['id']/'report.json'
    path.write_bytes(path.read_bytes()+b' ')
    assert client.get('/api/v1/migrations/'+job['id']+'/report',headers=HEADERS).status_code==409


def test_source_release_can_be_migrated_without_mutating_its_history(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    old=store.import_release('source','2406','Managed old version','fixture')
    before=store.manifest(Path(old['folder']))
    request.update(source=None,source_path=None,source_release_id=old['id'])
    job,report=preview(client,request)
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='completed',result
    assert store.manifest(Path(old['folder']))==before


def test_owned_catalog_hashes_and_reviewed_defaults_are_rebased_for_later_daily_updates(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    from test_libraries import resource_body
    spec=SPECS['holder-metric'];write(store.standards['2512'],spec['path'],simple(extra=True))
    old=store.import_release('source','2406','Old version','fixture')
    url='/api/v1/releases/'+old['id']
    catalog_body={'libraries':['holder-metric']}
    proposal=client.post(url+'/catalog-preview',headers=HEADERS,json=catalog_body).json()
    assert client.post(url+'/catalog-import',headers=HEADERS,json={**catalog_body,'preview_hash':proposal['preview_hash']}).status_code==200
    resource=store.resources()[0]
    payload=resource_body(resource);payload['cam_status']='approved'
    assert client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={**payload,'expected_revision':1}).status_code==200
    tracked=store.import_release('','','Owned old export','fixture',parent=old['id'])
    request.update(source=None,source_path=None,source_release_id=tracked['id'],defaults={'holder-metric':{'SAMPLE:1':{'EXTRA':'reviewed'}}})
    job,report=preview(client,request)
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='completed',result
    migrated=store.get_release(result['release_id'])
    assert migrated['parent_id']==tracked['id'] and resource['id'] in migrated['catalog']['exported_native']
    current=store.resources()[0];payload=resource_body(current);payload['cam']['records'][0]['values']['DESCR']='Daily update after migration'
    assert client.put('/api/v1/resources/'+current['id'],headers=HEADERS,json={**payload,'expected_revision':current['revision']}).status_code==200
    daily=store.import_release('','','Daily after upgrade','fixture',parent=migrated['id'])
    values=NativeTable.read(Path(daily['folder'])/spec['path']).groups()['CUSTOM'][0]['values']
    assert values['DESCR']=='Daily update after migration' and values['EXTRA']=='reviewed'
    again=store.import_release('','','Repeat daily','fixture',parent=daily['id'])
    assert NativeTable.read(Path(again['folder'])/spec['path']).groups()['CUSTOM'][0]['values']['EXTRA']=='reviewed'


def test_owner_recovery_uses_process_evidence_not_an_observation_timeout(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot
    job,report=preview(client,request)
    monkeypatch.delitem(LIVE,job['id'])  # Simulated restart has no old completed local Future.
    record=Upgrades(store).get(job['id']);record.update(status='building',owner_pid=123456789,owner_stamp='original')
    record.pop('execution',None)
    with store.db() as db:db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(record),job['id']))
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:'unknown')
    assert Upgrades(store).get(job['id'])['status']=='building'
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:'original')
    assert Upgrades(store).get(job['id'])['status']=='building'
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:None)
    assert Upgrades(store).get(job['id'])['status']=='interrupted'
