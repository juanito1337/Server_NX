"""Central resource contracts for direct writers, HTTP, integrity and history."""
import copy
import uuid

import pytest

from nxserver.libraries import NativeTable,native_cam,SPECS,validate_catalog_cam
from nxserver.integrity import scan
from nxserver.write_requests import get
from nxserver.safety import DomainError
from test_libraries import simple
from test_management import pilot,HEADERS,ERP_HEADERS
from test_filepackages import packages,body as package_body,derive


def native_body(identifier='holder-metric',kind='machine',status='draft'):
    table=NativeTable(simple('CUSTOM_CONTRACT'))
    return {'kind':kind,'name':'Synthetic contract object','cam_status':status,'cam':native_cam(identifier,'2512',table,table.records),'asset_ids':[],'erp':None}


def test_direct_store_rejects_mismatched_native_kind_before_allocating_rows(pilot):
    _,store,*_=pilot;before=store.audit_entries()
    with pytest.raises(DomainError,match='kind'):store.save_resource(native_body(),'fixture',request_key=str(uuid.uuid4()))
    assert store.resources()==[] and store.audit_entries()==before
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM write_requests').fetchone()[0]==0


@pytest.mark.parametrize('identifier',list(SPECS))
@pytest.mark.parametrize('status',['draft','retired','approved'])
def test_every_library_rejects_wrong_kind_in_every_state_for_direct_and_http_writes(pilot,identifier,status):
    client,store,*_=pilot;wrong='machine' if SPECS[identifier]['kind']!='machine' else 'holder'
    value=native_body(identifier,wrong,status);before=store.audit_entries();key=str(uuid.uuid4())
    with pytest.raises(DomainError,match='kind'):store.save_resource(value,'fixture',request_key=key)
    response=client.post('/api/v1/resources',headers={**HEADERS,'Idempotency-Key':key},json=value)
    assert response.status_code==400 and 'kind' in response.json()['detail']
    assert store.resources()==[] and store.audit_entries()==before
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM write_requests').fetchone()[0]==0


def test_wrong_kind_update_preserves_erp_assets_current_revision_and_history(pilot):
    client,store,*_=pilot;erp={'kind':'machine','company_id':'synthetic','system_id':'m1','number':'M1','description':'ERP machine','unit':'PCS','source_revision':'1'}
    resource=client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json=erp).json()
    asset=store.add_asset('synthetic.pdf',b'Synthetic notes','fixture');value={**native_body(),'erp':resource['erp'],'asset_ids':[asset['id']]}
    before=store.audit_entries();history=store.history(resource['id']);key=str(uuid.uuid4())
    response=client.put('/api/v1/resources/'+resource['id'],headers={**HEADERS,'Idempotency-Key':key},json={**value,'expected_revision':1})
    assert response.status_code==400
    with pytest.raises(DomainError,match='kind'):store.save_resource(value,'fixture',resource['id'],1,request_key=key)
    assert store.resources()[0]==resource and store.history(resource['id'])==history and store.audit_entries()==before


@pytest.mark.parametrize('mutation',['missing_field','wrong_class','second_group'])
def test_native_structure_is_required_even_for_draft_direct_writes(pilot,mutation):
    _,store,*_=pilot;value=native_body(kind='holder')
    if mutation=='missing_field':value['cam']['records'][0]['values'].pop('DESCR')
    elif mutation=='wrong_class':value['cam']['records'][0]['class_name']='OTHER'
    else:
        record=copy.deepcopy(value['cam']['records'][0]);record['values']['LIBRF']='SECOND_GROUP';value['cam']['records'].append(record)
    with pytest.raises(DomainError):store.save_resource(value,'fixture')
    assert store.resources()==[] and store.audit_entries()==[]


def test_legacy_bad_current_metadata_is_reported_not_rewritten_and_old_receipt_stays_historical(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());value=native_body()
    # Reproduce the historical direct-writer gap in an isolated fixture only.
    with monkeypatch.context() as patch:
        patch.setattr('nxserver.store.validate_catalog_cam',lambda resource:None)
        original=store.save_resource(value,'fixture',request_key=key)
    before=store.audit_entries();history=store.history(original['id'])
    report=scan(store);assert 'catalog_cam_contract' in {item['code'] for item in report['errors']}
    assert store.resources()[0]==original and store.history(original['id'])==history and store.audit_entries()==before
    assert store.save_resource(value,'fixture',request_key=key)==original and get(store,'fixture',key)['result']==original
    # Repair explicitly in a new revision, never mutate archived payload/audit.
    corrected={**value,'cam':{}};store.save_resource(corrected,'fixture',original['id'],1)
    assert scan(store)['error_count']==0 and store.history(original['id'])[0]==history[0]
    assert get(store,'fixture',key)['result']==original


def test_erp_update_does_not_copy_inconsistent_cam_into_new_revision(pilot,monkeypatch):
    client,store,*_=pilot;erp={'kind':'machine','company_id':'synthetic','system_id':'m1','number':'M1','description':'ERP machine','unit':'PCS','source_revision':'1'}
    resource=store.erp_upsert(erp,'erp');value={**native_body(),'erp':resource['erp']}
    with monkeypatch.context() as patch:
        patch.setattr('nxserver.store.validate_catalog_cam',lambda resource:None)
        bad=store.save_resource(value,'fixture',resource['id'],1)
    before=store.audit_entries();history=store.history(resource['id'])
    with pytest.raises(DomainError,match='kind'):store.erp_upsert({**erp,'description':'Later ERP description','source_revision':'2','expected_revision':2},'erp')
    assert store.resources()[0]==bad and store.history(resource['id'])==history and store.audit_entries()==before


@pytest.mark.parametrize('kind,path',[('machine','MACH/resource/library/machine/installed_machines/MACHINE.xml'),('fixture','MACH/resource/library/fixture_automation/graphics/FIXTURE.prt')])
def test_file_only_machine_and_fixture_can_be_explicitly_approved_over_http(packages,kind,path):
    client,store,release,*_=packages;content=b'<Machine name="synthetic"/>' if kind=='machine' else b'Synthetic PRT; not real CAD'
    value=package_body(store,kind,path,filename=path.rsplit('/',1)[-1],content=content)
    response=client.post('/api/v1/resources',headers=HEADERS,json=value);assert response.status_code==201,response.text
    resource=response.json();assert resource['cam']=={} and resource['cam_status']=='approved'
    exported=derive(store,release['id']);assert (store.release_folder(exported['id'])/path).read_bytes()==content
    assert resource['id'] in exported['catalog']['exported_file_packages'] and store.active()['release_id'] is None and scan(store)['error_count']==0
    for failed in ('checks','hash','raw_cam'):
        update={name:copy.deepcopy(value) for name,value in resource.items() if name not in ('id','revision','updated_at')};update['expected_revision']=1
        if failed=='checks':update['file_package']['checks']['nx_test']=False
        elif failed=='hash':update['file_package']['asset_hashes']={}
        else:update['cam']={'pretend':'Not an NX DAT record'}
        before=store.audit_entries();assert client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json=update).status_code==400
        assert store.audit_entries()==before and store.resources()[0]==resource


@pytest.mark.parametrize('kind',['holder','machine','fixture','device','part_material'])
def test_approved_missing_native_record_requires_actual_supported_package(kind):
    with pytest.raises(DomainError):validate_catalog_cam({'kind':kind,'cam':{},'cam_status':'approved','file_package':None})
    validate_catalog_cam({'kind':kind,'cam':{},'cam_status':'draft','file_package':None})
