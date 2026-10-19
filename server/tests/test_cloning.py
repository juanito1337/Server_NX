"""Native group templates are editor drafts only; all data is synthetic/local."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.cloning import plan,apply
from nxserver.libraries import SPECS,NativeTable,cam_table,native_cam
from nxserver.safety import DomainError
from nxserver.store import Store,canonical
from test_libraries import write
from test_references import table
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS


def setup(tmp_path,identifier='holder-metric',key_field='LIBRF'):
    source=tmp_path/'source'
    text=table([key_field,'RTYPE','SEQ','DESCR','MATREF','LIBPART'],[
        ('STD','1','1','Standard first','MAT_STD','stock.prt'),('STD','2','2','Standard second','MAT_STD','stock.prt'),
        ('TAKEN','1','1','Other source','MAT_OTHER','other.prt')])
    write(source,SPECS[identifier]['path'],text+'#DATA | INACTIVE | 1 | 1 | Comment | MAT | comment.prt\n')
    store=Store(tmp_path/'m',{'test':source});release=store.import_release('test','2512','Template source','fixture')
    fingerprint=next(item['sha256'] for item in release['manifest'] if item['path']==SPECS[identifier]['path'])
    request={'library_id':identifier,'source_key':'STD','source_sha256':fingerprint,'new_key':'NEW_CUSTOM','name':'New customer draft'}
    return store,release,request


@pytest.fixture
def clone_pilot(tmp_path):
    store,release,request=setup(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:yield client,store,release,request


@pytest.mark.parametrize('identifier',list(SPECS))
def test_every_adapter_clones_complete_group_without_source_rights_or_files(tmp_path,identifier):
    store,release,request=setup(tmp_path,identifier)
    before=store.manifest(store.release_folder(release['id']));audits=store.audit_entries()
    proposal=plan(store,release['id'],request);assert proposal['can_apply'] and proposal['rows']==2
    result=apply(store,release['id'],request,proposal['preview_hash']);resource=result['resource']
    assert resource['kind']==SPECS[identifier]['kind'] and resource['cam_status']=='draft'
    assert resource['erp'] is resource['origin'] is resource['model_package'] is None
    assert resource['asset_ids']==resource['file_bindings']==[] and resource['geometry']['checks']=={}
    assert resource['geometry']['status']=='needs_preparation' and set(cam_table(resource['cam']).groups())=={'NEW_CUSTOM'}
    assert len(resource['cam']['records'])==2 and all(row['values']['MATREF']=='MAT_STD' and row['values']['LIBPART']=='stock.prt' for row in resource['cam']['records'])
    assert resource['cam']['records'][0]['values']['DESCR']=='Standard first'
    assert store.resources()==[] and store.audit_entries()==audits and store.manifest(store.release_folder(release['id']))==before
    assert not result['saved'] and not result['nx_files_written']


def test_name_identifier_and_native_proto_key_remain_data(tmp_path):
    store,release,request=setup(tmp_path,'machine','NAME');request['new_key']='__proto__'
    result=apply(store,release['id'],request,plan(store,release['id'],request)['preview_hash'])
    assert all(row['values']['NAME']=='__proto__' for row in result['resource']['cam']['records'])
    assert set(cam_table(result['resource']['cam']).groups())=={'__proto__'}


@pytest.mark.parametrize('key',['STD','TAKEN',' ', ' NEW','NEW ', 'NEW|KEY','NEW\nKEY','x'*117])
def test_existing_or_unsafe_new_keys_never_prepare_a_writable_duplicate(clone_pilot,key):
    client,store,release,request=clone_pilot;request={**request,'new_key':key};before=store.audit_entries()
    response=client.post('/api/v1/releases/'+release['id']+'/library-clone/preview',headers=HEADERS,json=request)
    if key=='TAKEN':
        assert response.status_code==200 and not response.json()['can_apply']
        assert client.post('/api/v1/releases/'+release['id']+'/library-clone/apply',headers=HEADERS,json={**request,'preview_hash':response.json()['preview_hash']}).status_code==409
    else:assert response.status_code in (400,409,422),response.text
    assert store.resources()==[] and store.audit_entries()==before


def test_managed_draft_or_retirement_claim_blocks_new_identifier(clone_pilot):
    client,store,release,request=clone_pilot;proposal=plan(store,release['id'],request)
    resource=copy.deepcopy(proposal['resource']);resource['cam_status']='retired';store.save_resource(resource,'fixture')
    blocked=plan(store,release['id'],request);assert not blocked['can_apply'] and 'claimed' in str(blocked['conflicts'])
    with pytest.raises(DomainError):apply(store,release['id'],request,proposal['preview_hash'])
    assert len(store.resources())==1


@pytest.mark.parametrize('mutation',['bytes','manifest','new_key','name','catalog','comment_source'])
def test_preview_pins_source_and_original_intent(clone_pilot,mutation):
    client,store,release,request=clone_pilot;proposal=plan(store,release['id'],request)
    if mutation=='bytes':write(store.release_folder(release['id']),SPECS[request['library_id']]['path'],table(['LIBRF','DESCR'],[('STD','Changed')]))
    elif mutation=='manifest':
        with store.db() as db:db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical([]),release['id']))
    elif mutation=='new_key':request={**request,'new_key':'OTHER_NEW'}
    elif mutation=='name':request={**request,'name':'Changed clone name'}
    elif mutation=='catalog':store.save_resource({'kind':'gauge','name':'Independent draft','cam':{},'cam_status':'draft','asset_ids':[]},'fixture')
    else:request={**request,'source_key':'INACTIVE'}
    before=store.audit_entries()
    with pytest.raises(DomainError):apply(store,release['id'],request,proposal['preview_hash'])
    assert store.audit_entries()==before


def test_explicit_save_and_customer_export_never_overwrite_original_template(clone_pilot):
    client,store,release,request=clone_pilot;before=store.manifest(store.release_folder(release['id']))
    proposal=plan(store,release['id'],request);result=apply(store,release['id'],request,proposal['preview_hash'])
    response=client.post('/api/v1/resources',headers=HEADERS,json=result['resource']);assert response.status_code==201,response.text
    created=response.json();assert created['revision']==1 and not created['origin']
    value={**result['resource'],'cam_status':'approved'};value['cam']['records'][0]['values']['DESCR']='Reviewed synthetic customer value'
    store.save_resource(value,'fixture',created['id'],1)
    # Clear synthetic material references only for this independently reviewed
    # export fixture; the clone itself must not guess/remove them.
    for record in value['cam']['records']:record['values']['MATREF']=''
    store.save_resource(value,'fixture',created['id'],2)
    report=store.preview_export(release['id']);assert report['can_export'],report['conflicts']
    child=store.import_release('','','Synthetic cloned customer export','fixture',parent=release['id'],preview_hash=report['preview_hash'])
    groups=NativeTable.read(store.release_folder(child['id'])/SPECS[request['library_id']]['path']).groups()
    assert groups['STD'][0]['values']['DESCR']=='Standard first'
    assert groups['NEW_CUSTOM'][0]['values']['DESCR']=='Reviewed synthetic customer value' and len(groups['NEW_CUSTOM'])==2
    assert store.manifest(store.release_folder(release['id']))==before and store.active()['release_id'] is None


def test_new_owner_after_apply_is_still_rejected_by_explicit_save(clone_pilot):
    client,store,release,request=clone_pilot;result=apply(store,release['id'],request,plan(store,release['id'],request)['preview_hash'])
    store.save_resource(result['resource'],'independent writer')
    response=client.post('/api/v1/resources',headers=HEADERS,json=result['resource'])
    assert response.status_code==409 and len(store.resources())==1


def test_readonly_cloning_routes_are_admin_only(clone_pilot):
    client,store,release,request=clone_pilot
    for suffix in ('preview','apply'):
        route='/api/v1/releases/'+release['id']+'/library-clone/'+suffix
        assert client.post(route,json=request).status_code==401
        assert client.post(route,headers=ERP_HEADERS,json=request).status_code==403
    cap=client.get('/api/v1/capabilities',headers=HEADERS).json()['native_cloning']
    assert cap['whole_group'] and cap['editor_only'] and not cap['automatic_save']
