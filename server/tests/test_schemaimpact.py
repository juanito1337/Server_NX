"""Catalog-wide schema impact uses synthetic CAM/SQLite, never NX execution."""
import copy

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver import schemas,schemaimpact
from nxserver.libraries import digest,native_cam,NativeTable,SPECS
from nxserver.schemaimpact import SchemaImpact
from nxserver.safety import DomainError
from nxserver.integrity import snapshot
from test_schemas import setup
from test_libraries import resource_body,write
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS


@pytest.fixture
def impact(tmp_path):
    store,release,resource,request=setup(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:yield client,store,release,resource,request


def test_worklist_is_read_only_and_matches_existing_individual_schema_plan(impact):
    client,store,release,resource,request=impact;before=snapshot(store)['sha256'];manifest=store.manifest(store.release_folder(release['id']))
    response=client.post('/api/v1/releases/'+release['id']+'/catalog-schema-impact',headers=HEADERS,json={})
    assert response.status_code==200,response.text
    value=response.json();row=value['items'][0];individual=schemas.plan(store,request)
    assert value['read_only'] and not value['automatic_save'] and not value['nx_files_written'] and not value['automatic_approval']
    assert value['scope']=='catalog_schema_impact' and value['counts']['decisions_required']==1 and value['filtered']==1
    assert row['resource_id']==resource['id'] and row['revision']==1 and row['new_values']==1 and row['key']=='CUSTOM'
    assert row['conflicts']==individual['conflicts'] and row['target_library_sha256']==individual['target_library_sha256']
    assert row['resource_sha256']==digest(resource) and row['can_open_schema']
    report=copy.deepcopy(value);report.pop('report_sha256');assert digest(report)==value['report_sha256']
    assert snapshot(store)['sha256']==before and store.manifest(store.release_folder(release['id']))==manifest


def test_focused_resource_read_is_current_admin_only_and_never_writes(impact):
    client,store,release,resource,*_=impact;path='/api/v1/resources/'+resource['id'];before=snapshot(store)['sha256']
    assert client.get(path).status_code==401 and client.get(path,headers=ERP_HEADERS).status_code==403
    assert client.get(path,headers=HEADERS).json()==resource
    assert client.get('/api/v1/resources/00000000-0000-0000-0000-000000000000',headers=HEADERS).status_code==404
    assert snapshot(store)['sha256']==before
    changed=resource_body(resource);changed['name']='A new saved revision';saved=store.save_resource(changed,'fixture',resource['id'],1)
    after=snapshot(store)['sha256'];assert client.get(path,headers=HEADERS).json()==saved and saved['revision']==2
    assert snapshot(store)['sha256']==after


def test_ready_current_legacy_and_file_only_are_distinguished(impact):
    client,store,release,resource,request=impact;service=SchemaImpact(store)
    changed=resource_body(resource);changed['cam']['records'][0]['values']['LIBRF']='READY'
    table=NativeTable('#CLASS SAMPLE\nFORMAT LIBRF DESCR EXTRA\nDATA |READY|Customer|17\n#END_DATA\n')
    changed['cam']=native_cam('holder-metric','2406',table,table.records);ready=store.save_resource(changed,'fixture')
    table=NativeTable('#CLASS SAMPLE\nFORMAT DESCR LIBRF EXTRA\nDATA |Customer|CURRENT|17\n#END_DATA\n')
    changed['cam']=native_cam('holder-metric','2512',table,table.records);current=store.save_resource(changed,'fixture')
    only=store.save_resource({'kind':'template','name':'Files later','cam':{},'cam_status':'draft','erp':None,'asset_ids':[]},'fixture')
    value=service.inspect(release['id']);rows={row['resource_id']:row for row in value['items']}
    assert rows[ready['id']]['state']=='ready' and rows[current['id']]['state']=='current'
    assert rows[only['id']]['state']=='not_applicable' and not rows[only['id']]['can_open_schema']
    assert sum(value['counts'].values())==value['scoped_resources']==4


@pytest.mark.parametrize('mutation',['name','same_revision_cam','target_catalog','target_status'])
def test_paging_pin_rejects_metadata_drift(impact,mutation):
    _,store,release,resource,*_=impact;service=SchemaImpact(store);value=service.inspect(release['id'])
    with store.db() as db:
        if mutation in ('name','same_revision_cam'):
            changed=copy.deepcopy(resource);changed.pop('id');changed.pop('revision');changed.pop('updated_at')
            if mutation=='name':changed['name']='Changed same revision'
            else:changed['cam']['records'][0]['values']['DESCR']='Changed same revision'
            from nxserver.store import canonical
            db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(changed),resource['id']))
        elif mutation=='target_catalog':db.execute("UPDATE releases SET catalog='{}' WHERE id=?",(release['id'],))
        else:db.execute("UPDATE releases SET status='validated' WHERE id=?",(release['id'],))
    with pytest.raises(DomainError,match='context changed'):service.inspect(release['id'],context_sha256=value['context_sha256'])


@pytest.mark.parametrize('mutation',['bytes','missing','oversized'])
def test_bad_target_is_visible_as_blocked_never_ready(impact,mutation,monkeypatch):
    _,store,release,resource,*_=impact;path=store.release_folder(release['id'])/SPECS['holder-metric']['path']
    if mutation=='bytes':path.write_text('Changed synthetic fixture')
    elif mutation=='missing':path.unlink()
    else:monkeypatch.setattr(schemas,'MAX_LIBRARY',1)
    before=snapshot(store)['sha256'];value=SchemaImpact(store).inspect(release['id'])
    assert value['counts']['blocked']==1 and not value['items'][0]['can_open_schema']
    assert snapshot(store)['sha256']==before


def test_target_is_read_twice_per_library_not_per_resource_and_late_bytes_abort(impact,monkeypatch):
    _,store,release,resource,*_=impact;original=schemas._target;calls=[]
    changed=resource_body(resource);changed['cam']['records'][0]['values']['LIBRF']='SECOND';store.save_resource(changed,'fixture')
    def counted(*args):calls.append(args[2]);return original(*args)
    monkeypatch.setattr(schemas,'_target',counted)
    assert SchemaImpact(store).inspect(release['id'])['scoped_resources']==2 and len(calls)==2
    calls.clear()
    def drift(*args):
        value=counted(*args)
        if len(calls)==1:(store.release_folder(release['id'])/SPECS['holder-metric']['path']).write_text('Late synthetic change')
        return value
    monkeypatch.setattr(schemas,'_target',drift)
    with pytest.raises(DomainError):SchemaImpact(store).inspect(release['id'])


@pytest.mark.parametrize('bound',['MAX_RESOURCES','MAX_CELLS','MAX_TARGET_BYTES','MAX_METADATA_BYTES'])
def test_global_work_bounds_fail_explicitly_without_truncation_or_writes(impact,monkeypatch,bound):
    _,store,release,*_=impact;before=snapshot(store)['sha256'];monkeypatch.setattr(schemaimpact,bound,0)
    with pytest.raises(DomainError,match='bound'):SchemaImpact(store).inspect(release['id'])
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('field,value',[('kind','unknown'),('cam_status','validated'),('state','success'),('library_id','unknown'),('offset',-1),('limit',101),('context_sha256','bad'),('extra',True)])
def test_api_is_admin_only_and_filters_are_validated(impact,field,value):
    client,store,release,*_=impact;path='/api/v1/releases/'+release['id']+'/catalog-schema-impact'
    assert client.post(path,json={}).status_code==401
    assert client.post(path,headers=ERP_HEADERS,json={}).status_code==403
    assert client.post(path,headers=HEADERS,json={field:value}).status_code in (400,422)


def test_retired_and_imported_read_only_are_not_offered_for_schema_edits(impact):
    _,store,release,resource,*_=impact;changed=resource_body(resource);changed['cam_status']='retired';store.save_resource(changed,'fixture',resource['id'],1)
    row=SchemaImpact(store).inspect(release['id'])['items'][0]
    assert row['state']=='not_applicable' and 'Retired' in row['reason'] and not row['can_open_schema']


def test_siemens_origin_stays_read_only_without_running_a_projection(impact,monkeypatch):
    _,store,release,resource,*_=impact
    from nxserver.store import canonical
    value=resource_body(resource);value['origin']={'editable':False,'library_id':'holder-metric','key':'CUSTOM'}
    with store.db() as db:db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(value),resource['id']))
    def forbidden(*args):raise AssertionError('Read-only standard must not become a projected customer resource')
    monkeypatch.setattr(schemas,'_target',forbidden);before=snapshot(store)['sha256']
    result=SchemaImpact(store).inspect(release['id']);assert result['counts']['read_only']==1 and not result['items'][0]['can_open_schema']
    assert snapshot(store)['sha256']==before


def test_whole_scope_paging_filters_unicode_search_and_legacy_representation(impact):
    _,store,release,resource,*_=impact
    for index in range(54):
        value=resource_body(resource);value['name']='Straße '+str(index);value['cam']['records'][0]['values']['LIBRF']='C_'+str(index)
        store.save_resource(value,'fixture')
    service=SchemaImpact(store);first=service.inspect(release['id'],kind='holder',query='STRASSE',limit=20)
    second=service.inspect(release['id'],kind='holder',query='STRASSE',offset=20,limit=20,context_sha256=first['context_sha256'])
    third=service.inspect(release['id'],kind='holder',query='STRASSE',offset=40,limit=20,context_sha256=first['context_sha256'])
    assert first['filtered']==54 and first['scoped_resources']==55 and first['counts']['decisions_required']==55
    assert len({item['resource_id'] for page in (first,second,third) for item in page['items']})==54
    assert len(third['items'])==14
    assert service.inspect(release['id'],state='current')['filtered']==0
    assert service.inspect(release['id'],library_id='machine')['scoped_resources']==0
    # Legacy single-row tool CAM stays legacy; it is not silently made native.
    legacy_store,legacy_target,legacy_resource,_=setup(store.root.parent/'legacy',identifier='tool-metric',target='#CLASS MILLING\nFORMAT LIBRF DESCR\nDATA |STD|Stock\n#END_DATA\n')
    value=resource_body(legacy_resource);value['cam']={'class_name':'MILLING','values':{'LIBRF':'CUSTOM','DESCR':'Legacy'}}
    saved=legacy_store.save_resource(value,'fixture',legacy_resource['id'],1)
    row=SchemaImpact(legacy_store).inspect(legacy_target['id'])['items'][0]
    assert row['source_representation']=='legacy_tool' and row['projected_cam_sha256']==digest(saved['cam']) and row['state']=='current'


@pytest.mark.parametrize('phase',[1,2])
def test_metadata_mutation_during_library_read_is_not_published(impact,monkeypatch,phase):
    _,store,release,resource,*_=impact;original=schemas._target;calls=[]
    def changed(*args):
        loaded=original(*args);calls.append(True)
        if len(calls)==phase:
            from nxserver.store import canonical
            value=resource_body(resource);value['name']='Late same-revision metadata'
            with store.db() as db:db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(value),resource['id']))
        return loaded
    monkeypatch.setattr(schemas,'_target',changed)
    with pytest.raises(DomainError,match='context changed'):SchemaImpact(store).inspect(release['id'])
