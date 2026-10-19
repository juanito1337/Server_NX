"""Managed PAX dependency plans/commit gates, synthetic models, no actual NX."""
import copy
import hashlib
from pathlib import Path

import pytest

from nxserver.template_refs import relative,parse
from nxserver.safety import DomainError
from test_filepackages import packages,body,derive
from test_management import HEADERS,ERP_HEADERS,tool_payload
from nxserver.store import TOOL_DATABASE


def package(store,filename='PART.prt',image=False,configuration=''):
    declaration='<Palette xmlns="urn:test"><PaletteEntry id="CUSTOM"><ObjectData><Filename>'+filename+'</Filename>'+('<Configuration>'+configuration+'</Configuration>' if configuration else '')+'</ObjectData>'+('<Presentation><PreviewImage location="PICTURE.png"/></Presentation>' if image else '')+'</PaletteEntry></Palette>'
    value=body(store,path='UGII/templates/CUSTOM.pax',filename='CUSTOM.pax',content=declaration.encode())
    for name,content in [('PART.prt',b'Synthetic PRT; no CAD qualification')]+([('PICTURE.png',b'Synthetic image')] if image else []):
        asset=store.add_asset(name,content,'fixture');value['asset_ids'].append(asset['id']);value['file_package']['entries'].append({'asset_id':asset['id'],'path':'UGII/templates/'+name});value['file_package']['asset_hashes'][asset['id']]=asset['sha256']
    return value


def test_package_references_bind_planned_model_and_image_without_writing_preview(packages):
    _,store,parent,source,standard=packages;resource=store.save_resource(package(store,image=True),'fixture')
    before=store.manifest(store.release_folder(parent['id']));audit=store.audit_entries();report=store.preview_export(parent['id'])
    assert report['can_export'],report['conflicts']
    checks=report['template_references'];assert checks['palettes']==1 and len(checks['references'])==2 and not checks['errors']
    assert all(row['status']=='present' and row['scope']=='release' for row in checks['references'])
    assert checks['nx_execution_verified'] is False and checks['ui_reachability_verified'] is False
    assert store.audit_entries()==audit and store.manifest(store.release_folder(parent['id']))==before
    child=derive(store,parent['id']);assert (store.release_folder(child['id'])/'UGII/templates/PART.prt').read_bytes()==b'Synthetic PRT; no CAD qualification'
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('reference',['MISSING.prt',r'W:\SiemensShare\nx2406\do-not-read.prt',r'\\server\share\do-not-read.prt',r'C:\OldNX2406\part.prt','@DB/ITEM/0','${UNKNOWN}/part.prt','${UGII_TEMPLATE_DIR}/part.prt','../../../../outside.prt','http://localhost/part.prt'])
def test_missing_or_unportable_reference_blocks_export_without_claiming_qualified_nx(packages,reference):
    client,store,parent,*_=packages;store.save_resource(package(store,reference),'fixture');audit=store.audit_entries()
    report=store.preview_export(parent['id']);assert not report['can_export'] and report['template_references']['errors']
    error=report['template_references']['errors'][0];assert error['value']==reference and error['resource_id'] in {r['id'] for r in store.resources()}
    response=client.post('/api/v1/releases/'+parent['id']+'/derive',headers=HEADERS,json={'name':'Blocked','preview_hash':report['preview_hash']})
    assert response.status_code==409 and len(store.releases())==1 and store.audit_entries()==audit


def test_declared_stage_variables_resolve_to_future_files_not_parent_version(packages):
    _,store,parent,*_=packages;value=package(store,'${UGII_CAM_TEMPLATE_PART_METRIC_DIR}PART.prt')
    value['file_package']['entries'][1]['path']='MACH/resource/template_part/metric/PART.prt';store.save_resource(value,'fixture')
    report=store.preview_export(parent['id']);assert report['can_export']
    assert report['template_references']['references'][0]['target']=='MACH/resource/template_part/metric/PART.prt'
    derive(store,parent['id'])


def test_installation_reference_is_bound_and_replanned_on_changed_bytes(packages):
    client,store,parent,source,standard=packages;file=standard/'UGII/templates/STD.prt';file.write_bytes(b'Synthetic installed standard model')
    store.save_resource(package(store,'${UGII_BASE_DIR}UGII/templates/STD.prt'),'fixture')
    report=store.preview_export(parent['id']);assert report['can_export']
    assert report['template_references']['references'][0]['scope']=='installation'
    file.write_bytes(b'Changed installed fixture')
    response=client.post('/api/v1/releases/'+parent['id']+'/derive',headers=HEADERS,json={'name':'Stale','preview_hash':report['preview_hash']})
    assert response.status_code==409 and len(store.releases())==1
    derive(store,parent['id'])


@pytest.mark.parametrize('drift',['installation_bytes','installation_root','copied_dependency'])
def test_late_dependency_drift_prevents_final_release_registration(packages,monkeypatch,drift):
    _,store,parent,source,standard=packages;file=standard/'UGII/templates/STD.prt';file.write_bytes(b'Installed fixture')
    name='${UGII_BASE_DIR}UGII/templates/STD.prt' if drift!='copied_dependency' else 'PART.prt'
    store.save_resource(package(store,name),'fixture');report=store.preview_export(parent['id']);original=store._create_test_launcher
    def changed(*args,**kwargs):
        launcher=original(*args,**kwargs)
        if drift=='installation_bytes':file.write_bytes(b'Changed before SQL')
        elif drift=='installation_root':store.standards['2512']=standard.parent/'different'
        else:(args[2]/'UGII/templates/PART.prt').write_bytes(b'Changed copied fixture')
        return launcher
    monkeypatch.setattr(store,'_create_test_launcher',changed)
    with pytest.raises(DomainError,match='PAX'):store.import_release('','','Blocked late copy','fixture',parent=parent['id'],preview_hash=report['preview_hash'])
    child=next(row for row in store.releases() if row['parent_id']==parent['id']);assert child['status']=='failed' and child['catalog'] is None
    assert store.active()['release_id'] is None


def test_configuration_reference_uses_final_dat_bytes_when_library_also_changes(packages):
    _,store,parent,*_=packages;store.save_resource(tool_payload(),'fixture')
    value=package(store,configuration='${UGII_CAM_RESOURCE_DIR}library/tool/metric/tool_database.dat');store.save_resource(value,'fixture')
    report=store.preview_export(parent['id']);assert report['can_export'],report['conflicts']
    reference=next(row for row in report['template_references']['references'] if row['field']=='Configuration')
    child=derive(store,parent['id']);actual=(store.release_folder(child['id'])/TOOL_DATABASE).read_bytes()
    assert reference['sha256']==hashlib.sha256(actual).hexdigest() and reference['size']==len(actual)


def test_retiring_referenced_model_blocks_pax_in_new_copy_but_preserves_old_parent(packages):
    _,store,parent,*_=packages;value=package(store);pax=copy.deepcopy(value);model=copy.deepcopy(value)
    pax['asset_ids']=value['asset_ids'][:1];pax['file_package']['entries']=value['file_package']['entries'][:1];pax['file_package']['asset_hashes']={asset:store.asset(asset)[0]['sha256'] for asset in pax['asset_ids']}
    model['asset_ids']=value['asset_ids'][1:];model['file_package']['entries']=value['file_package']['entries'][1:];model['file_package']['asset_hashes']={asset:store.asset(asset)[0]['sha256'] for asset in model['asset_ids']}
    store.save_resource(pax,'fixture');resource=store.save_resource(model,'fixture');first=derive(store,parent['id']);before=store.manifest(store.release_folder(first['id']))
    model['cam_status']='retired';store.save_resource(model,'fixture',resource['id'],1)
    report=store.preview_export(first['id']);assert not report['can_export'] and report['template_references']['errors']
    assert store.manifest(store.release_folder(first['id']))==before


@pytest.mark.parametrize('raw',[b'<NotPalette/>',b'<!DOCTYPE Palette [<!ENTITY part "PART.prt">]><Palette/>',b'<!DOCTYPE Palette SYSTEM "http://127.0.0.1:9/no-fetch"><Palette/>'])
def test_pax_xml_reader_rejects_unsupported_dtd_and_root_without_external_resolution(raw):
    with pytest.raises(DomainError):parse(raw)


@pytest.mark.parametrize('reference,pax,scope,target',[
    ('PART.prt','UGII/templates/CUSTOM.pax','release','UGII/templates/PART.prt'),
    ('../PART.prt','UGII/templates/nested/CUSTOM.pax','release','UGII/templates/PART.prt'),
    ('${UGII_CAM_CONFIG_DIR}machine.dat','UGII/templates/CUSTOM.pax','release','MACH/resource/configuration/machine.dat'),
    ('${UGII_ROOT_DIR}templates/STD.prt','UGII/templates/CUSTOM.pax','installation','UGII/templates/STD.prt')])
def test_portable_resolution_is_explicit_and_does_not_rewrite_declarations(reference,pax,scope,target):
    assert relative(reference,pax)==(scope,target)


@pytest.mark.parametrize('bound',['MAX_NODES','MAX_REFS','MAX_PALETTES','MAX_TOTAL_BYTES'])
def test_exceeded_reader_bound_is_not_reported_as_complete_or_exportable(packages,monkeypatch,bound):
    import nxserver.template_refs as module
    _,store,parent,*_=packages;store.save_resource(package(store,image=True),'fixture');monkeypatch.setattr(module,bound,0 if bound=='MAX_PALETTES' else 1)
    report=store.preview_export(parent['id']);assert not report['can_export'] and report['template_references']['complete'] is False
    assert report['template_references']['errors'] and len(store.releases())==1
