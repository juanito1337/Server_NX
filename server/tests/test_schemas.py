"""Explicit schema reviews and real draft saves/exports using disposable data."""
import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.libraries import NativeTable,SPECS,native_cam
from nxserver.store import Store
from test_libraries import resource_body,write
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS,tool_payload


def setup(tmp_path,identifier='holder-metric',target=None,source=None):
    spec=SPECS[identifier];folder=tmp_path/'source'
    write(folder,spec['path'],target or '#CLASS SAMPLE\nFORMAT DESCR LIBRF EXTRA\nDATA |Standard|STD|0\n#END_DATA\n')
    store=Store(tmp_path/'managed',{'source':folder})
    release=store.import_release('source','2512','Target','isolated test')
    table=NativeTable(source or '#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |CUSTOM|Customer value\n#END_DATA\n')
    payload={'kind':spec['kind'],'name':'Customer','cam_status':'draft','asset_ids':[],'erp':None,
        'cam':native_cam(identifier,'2406',table,table.records)}
    resource=store.save_resource(payload,'isolated test')
    request={'kind':spec['kind'],'cam':resource['cam'],'resource_id':resource['id'],
        'expected_revision':1,'target_release_id':release['id'],'decisions':{}}
    return store,release,resource,request


def review(client,request):
    response=client.post('/api/v1/schema/preview',headers=HEADERS,json=request)
    assert response.status_code==200,response.text
    return response.json()


def apply(client,request,report):
    return client.post('/api/v1/schema/apply',headers=HEADERS,json={**request,'preview_hash':report['preview_hash']})


@pytest.mark.parametrize('identifier',list(SPECS))
def test_every_library_can_prepare_versioned_values_without_implicitly_saving(tmp_path,identifier):
    store,release,resource,request=setup(tmp_path,identifier)
    before=store.manifest(Path(release['folder']));audit=store.audit_entries()
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        report=review(client,request)
        assert not report['can_apply'] and report['rows'][0]['new_fields'][0]['field']=='EXTRA'
        assert apply(client,request,report).status_code==409
        request['decisions']={'0':{'values':{'EXTRA':''}}}
        report=review(client,request)
        assert report['can_apply'] and report['changes']=={'kept':2,'renamed':0,'discarded':0,'added':1,'class_changes':0}
        response=apply(client,request,report)
        assert response.status_code==200,response.text
        result=response.json()
        assert not result['saved'] and result['draft_required'] and result['geometry_recheck_required']
        assert store.resources()[0]['revision']==1 and len(store.history(resource['id']))==1
        assert store.audit_entries()==audit
        cam=result['cam'];assert cam['nx_version']=='2512'
        assert cam['records'][0]['values']=={'LIBRF':'CUSTOM','DESCR':'Customer value','EXTRA':''}
        assert cam['schemas'][0]['fields']==['DESCR','LIBRF','EXTRA']
        saved=client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={
            **resource_body(resource),'cam':cam,'cam_status':'draft','expected_revision':1})
        assert saved.status_code==200,saved.text
        assert saved.json()['revision']==2 and len(store.history(resource['id']))==2
        assert store.manifest(Path(release['folder']))==before and store.active()['generation']==0


def test_renames_empty_removed_columns_and_class_changes_require_explicit_review(tmp_path):
    store,release,resource,request=setup(tmp_path,target='#CLASS NEWCLASS\nFORMAT LIBRF NEW\nDATA |STD|Standard\n#END_DATA\n',
        source='#CLASS SAMPLE\nFORMAT LIBRF OLD EMPTY\nDATA |CUSTOM|Value|\n#END_DATA\n')
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        report=review(client,request);assert not report['can_apply'] and report['rows'][0]['target_block'] is None
        request['decisions']={'0':{'target_block_id':'NEWCLASS:1'}}
        report=review(client,request)
        assert any('class' in conflict['reason'].lower() for conflict in report['conflicts'])
        assert any('EMPTY' in conflict['reason'] for conflict in report['conflicts'])
        request['decisions']['0'].update(field_map={'OLD':'NEW'},discard_fields=['EMPTY'],accept_class_change=True)
        report=review(client,request);assert report['can_apply']
        assert report['changes']['renamed']==1 and report['changes']['discarded']==1 and report['changes']['class_changes']==1
        result=apply(client,request,report).json()['cam']
        assert result['records'][0]['values']=={'LIBRF':'CUSTOM','NEW':'Value'}


def test_multiblock_reordering_is_by_class_and_names_not_position(tmp_path):
    source='#CLASS A\nFORMAT LIBRF DIA\nDATA |CUSTOM|10\n#CLASS B\nFORMAT LIBRF SEQ\nDATA |CUSTOM|1\nDATA |CUSTOM|2\n#END_DATA\n'
    target='#CLASS B\nFORMAT SEQ LIBRF\nDATA |0|STD\n#CLASS A\nFORMAT DIA LIBRF\nDATA |5|STD\n#END_DATA\n'
    store,release,resource,request=setup(tmp_path,'tool-assembly-metric' if 'tool-assembly-metric' in SPECS else 'holder-metric',target,source)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        report=review(client,request);assert report['can_apply']
        result=apply(client,request,report).json()['cam']
        assert [record['values'] for record in result['records']]==[{'LIBRF':'CUSTOM','DIA':'10'},{'LIBRF':'CUSTOM','SEQ':'1'},{'LIBRF':'CUSTOM','SEQ':'2'}]
        assert [block['id'] for block in result['schemas']]==['B:1','A:1']


def test_ambiguous_same_class_blocks_require_explicit_selection(tmp_path):
    target='#CLASS SAMPLE\nFORMAT LIBRF DESCR EXTRA\nDATA |STD1|Standard|0\nFORMAT LIBRF DESCR MORE\nDATA |STD2|Standard|0\n#END_DATA\n'
    store,release,resource,request=setup(tmp_path,target=target)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        assert not review(client,request)['can_apply']
        request['decisions']={'0':{'target_block_id':'SAMPLE:2','values':{'MORE':'checked'}}}
        report=review(client,request);assert report['can_apply']
        assert apply(client,request,report).json()['cam']['records'][0]['values']['MORE']=='checked'


@pytest.mark.parametrize('decision',[{'discard_fields':['LIBRF']},{'field_map':{'LIBRF':'EXTRA'}},
    {'field_map':{'MISSING':'EXTRA'}},{'values':{'DESCR':'Overwrite'}},{'values':{'EXTRA':'bad|value'}},
    {'values':{'EXTRA':'bad\nvalue'}},{'target_block_id':'MISSING:1'},{'discard_fields':['DESCR','DESCR']}])
def test_unsafe_or_invalid_decisions_are_rejected(tmp_path,decision):
    store,release,resource,request=setup(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        request['decisions']={'0':decision}
        assert client.post('/api/v1/schema/preview',headers=HEADERS,json=request).status_code in (400,409)
        assert store.resources()[0]['revision']==1


def test_duplicate_destinations_never_silently_collapse_values(tmp_path):
    store,release,resource,request=setup(tmp_path,target='#CLASS SAMPLE\nFORMAT LIBRF NEW\nDATA |STD|Standard\n#END_DATA\n',
        source='#CLASS SAMPLE\nFORMAT LIBRF OLD OLD2\nDATA |CUSTOM|First|Second\n#END_DATA\n')
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        request['decisions']={'0':{'field_map':{'OLD':'NEW','OLD2':'NEW'}}}
        report=review(client,request);assert not report['can_apply'] and report['cam'] is None
        assert any('Multiple' in item['reason'] for item in report['conflicts'])


def test_target_and_resource_mutations_invalidate_apply(tmp_path):
    store,release,resource,request=setup(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        request['decisions']={'0':{'values':{'EXTRA':'checked'}}};report=review(client,request)
        changed=copy.deepcopy(request);changed['cam']['records'][0]['values']['DESCR']='Edited since preview'
        assert apply(client,changed,report).status_code==409
        file=Path(release['folder'])/SPECS['holder-metric']['path'];original=file.read_bytes();file.write_bytes(original+b'#External change\n')
        assert apply(client,request,report).status_code==409
        file.write_bytes(original)
        assert client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={
            **resource_body(resource),'name':'Changed concurrently','expected_revision':1}).status_code==200
        assert apply(client,request,report).status_code==409
        assert len(store.history(resource['id']))==2


def test_roles_and_strict_shape(tmp_path):
    store,release,resource,request=setup(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        for endpoint in ('preview','apply'):
            body={**request,'preview_hash':'0'*64} if endpoint=='apply' else request
            assert client.post('/api/v1/schema/'+endpoint,json=body).status_code==401
            assert client.post('/api/v1/schema/'+endpoint,headers=ERP_HEADERS,json=body).status_code==403
        for addition in ({'path':'W:\\SiemensShare\\nx2406'},{'expected_revision':True},{'kind':'not-kind'},
                         {'decisions':{'0':{'accept_class_change':'yes'}}}):
            assert client.post('/api/v1/schema/preview',headers=HEADERS,json={**request,**addition}).status_code==422


def test_exported_legacy_tool_stays_legacy_and_can_export_after_review_and_reapproval(tmp_path):
    store,release,resource,request=setup(tmp_path,'tool-metric')
    legacy={'class_name':'SAMPLE','values':{'LIBRF':'LEGACY','DESCR':'Old tool'}}
    resource=store.save_resource({'kind':'tool','name':'Legacy','cam_status':'approved','cam':legacy,'asset_ids':[],'erp':None,
        'geometry':tool_payload()['geometry']},'fixture')
    first=store.import_release('','','Original tracked export','fixture',parent=release['id'],defaults={'tool-metric':{'SAMPLE:1':{'EXTRA':'7'}}})
    original=store.manifest(Path(first['folder']))
    request.update(kind='tool',cam=legacy,resource_id=resource['id'],expected_revision=1,
        target_release_id=first['id'],decisions={'0':{'values':{'EXTRA':'9'}}})
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        report=review(client,request);assert report['can_apply'] and report['source_representation']=='legacy_tool'
        result=apply(client,request,report).json()['cam']
        assert set(result)=={'class_name','values'} and result['values']['EXTRA']=='9'
        geometry={**tool_payload()['geometry'],'status':'needs_preparation','checks':{}}
        response=client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={
            **resource_body(resource),'cam':result,'geometry':geometry,'expected_revision':1,'cam_status':'draft'})
        assert response.status_code==200,response.text
        deferred=store.import_release('','','Deferred while draft','fixture',parent=first['id'])
        assert NativeTable.read(Path(deferred['folder'])/SPECS['tool-metric']['path']).groups()['LEGACY'][0]['values']['EXTRA']=='7'
        updated=client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={
            **resource_body(response.json()),'geometry':tool_payload()['geometry'],'expected_revision':2,'cam_status':'approved'})
        assert updated.status_code==200,updated.text
        final=store.import_release('','','Explicitly rechecked export','fixture',parent=deferred['id'])
        assert NativeTable.read(Path(final['folder'])/SPECS['tool-metric']['path']).groups()['LEGACY'][0]['values']['EXTRA']=='9'
        assert store.manifest(Path(first['folder']))==original
        assert store.history(resource['id'])[0]['revision']==1


def test_name_identifier_and_erp_attachments_survive_draft_and_export(tmp_path):
    store,release,resource,request=setup(tmp_path,target='#CLASS SAMPLE\nFORMAT NAME DESCR EXTRA\nDATA |STD|Standard|0\n#END_DATA\n')
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        erp={'kind':'holder','company_id':'company','system_id':'system','number':'H-1',
            'description':'ERP holder','unit':'PCS','source_revision':'etag-1'}
        linked=client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json=erp).json()
        table=NativeTable('#CLASS SAMPLE\nFORMAT NAME DESCR\nDATA |ERP_HOLDER|Old description\n#END_DATA\n')
        request.update(cam=native_cam('holder-metric','2406',table,table.records),resource_id=linked['id'],
            decisions={'0':{'values':{'EXTRA':'Specific per-object value'}}})
        report=review(client,request);assert report['can_apply']
        for forbidden in ({'discard_fields':['NAME']},{'field_map':{'NAME':'EXTRA'}}):
            assert client.post('/api/v1/schema/preview',headers=HEADERS,json={**request,'decisions':{'0':forbidden}}).status_code==409
        result=apply(client,request,report).json()
        asset=store.add_asset('Holder.pdf',b'Synthetic document','fixture')
        saved=client.put('/api/v1/resources/'+linked['id'],headers=HEADERS,json={
            **resource_body(linked),'cam':result['cam'],'asset_ids':[asset['id']],'expected_revision':1})
        assert saved.status_code==200,saved.text
        assert saved.json()['erp']==linked['erp'] and saved.json()['asset_ids']==[asset['id']]
        approved=client.put('/api/v1/resources/'+linked['id'],headers=HEADERS,json={
            **resource_body(saved.json()),'cam_status':'approved','expected_revision':2})
        assert approved.status_code==200,approved.text
        exported=store.import_release('','','New schema holder','fixture',parent=release['id'])
        record=NativeTable.read(Path(exported['folder'])/SPECS['holder-metric']['path']).groups()['ERP_HOLDER'][0]
        assert record['values']=={'NAME':'ERP_HOLDER','DESCR':'Old description','EXTRA':'Specific per-object value'}


def test_tracked_identity_and_representation_cannot_change_during_schema_review(tmp_path):
    store,release,resource,request=setup(tmp_path)
    payload=resource_body(resource);payload['cam_status']='approved'
    store.save_resource(payload,'fixture',resource['id'],1)
    tracked=store.import_release('','','Tracked holder','fixture',parent=release['id'],defaults={'holder-metric':{'SAMPLE:1':{'EXTRA':'0'}}})
    request.update(expected_revision=2,target_release_id=tracked['id'],decisions={'0':{'values':{'EXTRA':'0'}}})
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        changed=copy.deepcopy(request);changed['cam']['records'][0]['values']['LIBRF']='OTHER_KEY'
        assert client.post('/api/v1/schema/preview',headers=HEADERS,json=changed).status_code==409
        request['expected_revision']=1
        assert client.post('/api/v1/schema/preview',headers=HEADERS,json=request).status_code==409


@pytest.mark.parametrize('case',['source_rows','source_fields','target_fields','projected_fields','unknown_row','revision_without_id','id_without_revision'])
def test_bounds_and_context_are_explicit(tmp_path,monkeypatch,case):
    from nxserver import schemas
    store,release,resource,request=setup(tmp_path)
    if case=='source_rows':monkeypatch.setattr(schemas,'MAX_ROWS',0)
    elif case in ('source_fields','target_fields','projected_fields'):
        monkeypatch.setattr(schemas,'MAX_CELLS',1 if case=='source_fields' else 2 if case=='target_fields' else 3)
        if case=='projected_fields':
            request['cam']['records'].append(copy.deepcopy(request['cam']['records'][0]))
            # Two rows require six output cells; source limit must allow four.
            monkeypatch.setattr(schemas,'MAX_CELLS',4)
    elif case=='unknown_row':request['decisions']={'01':{'values':{'EXTRA':'1'}}}
    elif case=='revision_without_id':request['resource_id']=None
    else:request['expected_revision']=None
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        response=client.post('/api/v1/schema/preview',headers=HEADERS,json=request)
        assert response.status_code==400,response.text
        assert store.resources()[0]['revision']==1
