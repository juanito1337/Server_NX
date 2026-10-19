"""Read-only standard graphics placement; fake PRT bytes, never NX execution."""
import copy

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.libraries import SPECS
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS
from test_schemas import setup


def context(tmp_path,identifier='holder-metric',legacy=False):
    store,release,resource,_=setup(tmp_path,identifier)
    part=store.add_asset('Part (1)#.prt',b'Synthetic component, not a real part','fixture')
    document=store.add_asset('Source.step',b'Synthetic source, not a real STEP','fixture')
    cam=resource['cam'] if not legacy else {'class_name':'SAMPLE','values':{'LIBRF':'CUSTOM','DESCR':'Value'}}
    body={'kind':SPECS[identifier]['kind'],'cam':cam,'asset_ids':[part['id'],document['id']],
        'file_bindings':[{'asset_id':part['id'],'path':(SPECS[identifier]['graphics_dir'] or 'unsupported')+'/sub/'+part['filename']}],
        'resource_id':resource['id'],'expected_revision':1}
    return store,release,resource,part,document,body


@pytest.mark.parametrize('identifier',list(SPECS))
def test_all_library_placements_use_existing_standard_graphics_rules_without_writes(tmp_path,identifier):
    store,release,resource,part,document,body=context(tmp_path,identifier)
    before=store.audit_entries();manifest=store.manifest(store.release_folder(release['id']))
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        response=client.post('/api/v1/graphics-bindings/preview',headers=HEADERS,json=body)
        if not SPECS[identifier]['graphics_dir']:
            assert response.status_code==400
            assert store.resources()[0]==resource and store.audit_entries()==before
            assert store.manifest(store.release_folder(release['id']))==manifest
            return
        assert response.status_code==200,response.text
        value=response.json()
        assert value['contract_version']==1 and value['scope']=='graphics_paths_only'
        assert value['library_id']==identifier and value['graphics_dir']==SPECS[identifier]['graphics_dir']
        assert value['file_bindings']==body['file_bindings'] and value['mode']=='explicit_prt'
        assert value['private_asset_ids']==[document['id']] and value['entries'][0]['sha256']==part['sha256']
        assert not value['saved'] and not value['nx_files_written'] and not value['automatic_approval']
        assert not value['target_conflicts_checked'] and not value['provenance_checked']
    assert store.resources()[0]==resource and store.audit_entries()==before
    assert store.manifest(store.release_folder(release['id']))==manifest and store.active()['release_id'] is None


def test_legacy_tool_and_default_mode_remain_explicitly_distinct(tmp_path):
    store,_,_,part,document,body=context(tmp_path,'tool-metric',True)
    body.update(resource_id=None,expected_revision=None,file_bindings=[])
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        value=client.post('/api/v1/graphics-bindings/preview',headers=HEADERS,json=body).json()
    assert value['mode']=='default_all_attachments' and value['nx_version'] is None
    assert value['private_asset_ids']==[] and [item['asset_id'] for item in value['entries']]==[part['id'],document['id']]


@pytest.mark.parametrize('fault',['outside','traversal','absolute','rename','non_prt','detached','duplicate_id','duplicate_asset','case_collision','missing','extra','bad_revision','missing_revision','kind'])
def test_unsafe_or_stale_placement_is_not_qualified(tmp_path,fault):
    store,_,resource,part,document,body=context(tmp_path)
    item=body['file_bindings'][0];root=SPECS['holder-metric']['graphics_dir']+'/'
    if fault=='outside':item['path']='UGII/templates/'+part['filename']
    elif fault=='traversal':item['path']=root+'../'+part['filename']
    elif fault=='absolute':item['path']=r'W:\SiemensShare\nx2406\Part.prt'
    elif fault=='rename':item['path']=root+'Renamed.prt'
    elif fault=='non_prt':body['file_bindings']=[{'asset_id':document['id'],'path':root+document['filename']}]
    elif fault=='detached':body['asset_ids']=[document['id']]
    elif fault=='duplicate_id':body['asset_ids'].append(part['id'])
    elif fault=='duplicate_asset':body['file_bindings'].append(copy.deepcopy(item))
    elif fault=='case_collision':
        other=store.add_asset('part (1)#.prt',b'Other synthetic component','fixture');body['asset_ids'].append(other['id'])
        body['file_bindings'].append({'asset_id':other['id'],'path':root+'SUB/'+other['filename']})
    elif fault=='missing':store.asset(part['id'])[1].unlink()  # Only test-owned bytes.
    elif fault=='extra':item['unexpected']='no'
    elif fault=='bad_revision':body['expected_revision']=2
    elif fault=='missing_revision':body['expected_revision']=None
    else:body['kind']='machine'
    before=store.audit_entries()
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        response=client.post('/api/v1/graphics-bindings/preview',headers=HEADERS,json=body)
        assert response.status_code in (400,409,422),response.text
    assert store.resources()[0]==resource and store.audit_entries()==before


def test_roles_shape_and_byte_changes_are_checked(tmp_path):
    store,_,_,part,_,body=context(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        for headers,expected in (({},401),(ERP_HEADERS,403)):
            assert client.post('/api/v1/graphics-bindings/preview',headers=headers,json=body).status_code==expected
        for change in ({'expected_revision':True},{'asset_ids':[True]},{'unexpected':True}):
            assert client.post('/api/v1/graphics-bindings/preview',headers=HEADERS,json={**body,**change}).status_code==422
        first=client.post('/api/v1/graphics-bindings/preview',headers=HEADERS,json=body).json()
        body['file_bindings'][0]['path']=SPECS['holder-metric']['graphics_dir']+'/'+part['filename']
        second=client.post('/api/v1/graphics-bindings/preview',headers=HEADERS,json=body).json()
        assert first['preview_hash']!=second['preview_hash']
        store.asset(part['id'])[1].write_bytes(b'Changed test file only')
        assert client.post('/api/v1/graphics-bindings/preview',headers=HEADERS,json=body).status_code==409
