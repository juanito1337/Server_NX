"""Exact editor relations, immutable manifest inputs; synthetic data only."""
import copy

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.editorrefs import plan,options,apply
from nxserver.libraries import SPECS,NativeTable,native_cam,reference_targets
from nxserver.safety import DomainError
from nxserver.store import Store,canonical
from test_libraries import write,resource_body
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS
from test_references import table


def setup(tmp_path,identifier='tool-metric'):
    source=tmp_path/'source';relations=reference_targets(identifier)
    for target in set(relations.values()):
        write(source,SPECS[target]['path'],table(['LIBRF','DESCR'],[('REF_A','Active A'),('REF_B','Active B'),('__proto__','Active reserved key'),('Straße','Unicode name')])+ '#DATA | INACTIVE | Comment only\n')
    fields=['LIBRF',*relations,'DESCR']
    records=[['CUSTOM',*['MISSING' for _ in relations],'First section'],['CUSTOM',*['REF_B' for _ in relations],'Second section']]
    text=table(fields,records);write(source,SPECS[identifier]['path'],text)
    store=Store(tmp_path/'m',{'test':source});release=store.import_release('test','2512','Synthetic reference target','fixture')
    parsed=NativeTable(text);resource=store.save_resource({'kind':SPECS[identifier]['kind'],'name':'Synthetic native group',
        'cam':native_cam(identifier,'2406',parsed,parsed.records),'cam_status':'draft','asset_ids':[],'erp':None},'fixture')
    request={'target_release_id':release['id'],'kind':resource['kind'],'cam':resource['cam'],'resource_id':resource['id'],'expected_revision':1}
    return store,release,resource,request


@pytest.fixture
def references_pilot(tmp_path):
    store,release,resource,request=setup(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:yield client,store,release,resource,request


@pytest.mark.parametrize('identifier',list(SPECS))
def test_registered_relations_across_every_adapter_are_library_specific(tmp_path,identifier):
    store,release,resource,request=setup(tmp_path,identifier)
    before=store.audit_entries();report=plan(store,request)
    assert len(report['rows'])==2*len(reference_targets(identifier))
    assert report['unresolved']==len(reference_targets(identifier))
    assert not report['saved'] and not report['nx_files_written'] and not report['schema_conversion_performed']
    assert store.audit_entries()==before and store.resources()[0]['revision']==1


def test_exact_change_is_one_editor_row_only_and_does_not_save(references_pilot):
    client,store,release,resource,request=references_pilot;before=store.manifest(store.release_folder(release['id']));audits=store.audit_entries()
    report=client.post('/api/v1/editor-references/preview',headers=HEADERS,json=request).json()
    response=client.post('/api/v1/editor-references/apply',headers=HEADERS,json={**request,'preview_hash':report['preview_hash'],'row_index':0,'field':'MATREF','value':'__proto__'})
    assert response.status_code==200,response.text
    result=response.json();changed=copy.deepcopy(request['cam']);changed['records'][0]['values']['MATREF']='__proto__'
    assert result['cam']==changed and result['draft_required'] and result['geometry_recheck_required']
    assert result['cam']['nx_version']=='2406' and not result['schema_conversion_performed']
    assert store.resources()==[resource] and store.audit_entries()==audits
    assert store.manifest(store.release_folder(release['id']))==before
    assert client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={**resource_body(resource),'cam':result['cam'],'expected_revision':1,'cam_status':'draft'}).status_code==200
    assert store.resources()[0]['revision']==2 and store.active()['release_id'] is None


def test_paged_active_options_search_labels_unicode_and_never_activate_comments(references_pilot):
    client,store,release,resource,request=references_pilot;prefix='/api/v1/releases/'+release['id']+'/reference-options'
    first=client.get(prefix,headers=HEADERS,params={'library_id':'tool-metric','field':'MATREF','offset':0,'limit':2}).json()
    second=client.get(prefix,headers=HEADERS,params={'library_id':'tool-metric','field':'MATREF','offset':2,'limit':2}).json()
    assert first['total']==first['filtered']==4 and first['items'][0]['key']=='REF_A'
    assert {item['key'] for item in first['items']+second['items']}=={'REF_A','REF_B','__proto__','Straße'}
    assert first['input_sha256']==second['input_sha256'] and first['scope']=='selected_native_library_bytes'
    assert first['integrity_checked'] and not first['automatic_assignment']
    assert client.get(prefix,headers=HEADERS,params={'library_id':'tool-metric','field':'MATREF','query':'STRASSE'}).json()['items'][0]['key']=='Straße'
    assert client.get(prefix,headers=HEADERS,params={'library_id':'tool-metric','field':'MATREF','query':'Comment'}).json()['filtered']==0


@pytest.mark.parametrize('value',['REF_A ','REF','INACTIVE','', 'REF_A|REF_B'])
def test_similar_inactive_or_malformed_keys_are_not_aliases(references_pilot,value):
    client,store,release,resource,request=references_pilot;report=plan(store,request);audits=store.audit_entries()
    response=client.post('/api/v1/editor-references/apply',headers=HEADERS,json={**request,'preview_hash':report['preview_hash'],'row_index':0,'field':'MATREF','value':value})
    assert response.status_code in (400,409,422) and store.resources()==[resource] and store.audit_entries()==audits


@pytest.mark.parametrize('mutation',['editor','revision','file','manifest','source_field','source_row'])
def test_current_preview_and_source_context_required_without_writes(references_pilot,mutation):
    client,store,release,resource,request=references_pilot;report=plan(store,request)
    data={**request,'preview_hash':report['preview_hash'],'row_index':0,'field':'MATREF','value':'REF_A'}
    if mutation=='editor':
        data=copy.deepcopy(data);data['cam']['records'][0]['values']['DESCR']='Changed editor'
    elif mutation=='revision':store.save_resource(resource_body(resource),'fixture',resource['id'],1)
    elif mutation=='file':write(store.release_folder(release['id']),SPECS['tool_material']['path'],table(['LIBRF','DESCR'],[('REF_A','Changed target')]))
    elif mutation=='manifest':
        with store.db() as db:db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical([]),release['id']))
    elif mutation=='source_field':data['field']='DESCR'
    else:data['row_index']=999
    before=store.audit_entries();response=client.post('/api/v1/editor-references/apply',headers=HEADERS,json=data)
    assert response.status_code in (400,409),response.text
    assert store.audit_entries()==before


def test_missing_target_does_not_fall_back_to_installation_or_untracked_files(references_pilot,tmp_path):
    client,store,release,resource,request=references_pilot
    missing=SPECS['tool_material']['path'];manifest=[entry for entry in release['manifest'] if entry['path']!=missing]
    with store.db() as db:db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical(manifest),release['id']))
    baseline=tmp_path/'baseline';write(baseline,missing,table(['LIBRF','DESCR'],[('MISSING','Not in release')]))
    store.standards={'2512':baseline}
    report=plan(store,request);assert next(row for row in report['rows'] if row['field']=='MATREF')['status']=='target_missing'
    with pytest.raises(DomainError,match='active library'):options(store,release['id'],'tool-metric','MATREF')


def test_part_material_description_is_never_a_reference(tmp_path):
    store,release,resource,request=setup(tmp_path,'part_material')
    request=copy.deepcopy(request);request['cam']['schemas'][0]['fields'].append('PARTMAT')
    for record in request['cam']['records']:record['values']['PARTMAT']='Free descriptive text'
    assert plan(store,request)['rows']==[]
    with pytest.raises(DomainError,match='registered relation'):options(store,release['id'],'part_material','PARTMAT')


def test_role_and_query_limits_are_enforced(references_pilot):
    client,store,release,resource,request=references_pilot
    for route,method in [('/api/v1/editor-references/preview','POST'),('/api/v1/editor-references/apply','POST'),('/api/v1/releases/'+release['id']+'/reference-options?library_id=tool-metric&field=MATREF','GET')]:
        assert client.request(method,route,json=request if method=='POST' else None).status_code==401
        assert client.request(method,route,headers=ERP_HEADERS,json=request if method=='POST' else None).status_code==403
    prefix='/api/v1/releases/'+release['id']+'/reference-options?library_id=tool-metric&field=MATREF&'
    for query in ('offset=-1','limit=101','query='+('a'*201)):assert client.get(prefix+query,headers=HEADERS).status_code==422
    assert client.get('/api/v1/capabilities',headers=HEADERS).json()['editor_references']['editor_only']


def test_legacy_single_row_tool_editor_keeps_its_representation(references_pilot):
    client,store,release,resource,request=references_pilot
    row=request['cam']['records'][0]
    legacy={**request,'resource_id':None,'expected_revision':None,'cam':{'class_name':row['class_name'],'values':copy.deepcopy(row['values'])}}
    report=plan(store,legacy)
    result=apply(store,legacy,report['preview_hash'],0,'MATREF','REF_A')
    assert set(result['cam'])=={'class_name','values'} and result['cam']['values']['MATREF']=='REF_A'
    assert result['cam']['values']['HLDREF']==legacy['cam']['values']['HLDREF']
    assert store.resources()[0]['revision']==1


def test_independent_source_revision_after_target_read_blocks_editor_apply(references_pilot,monkeypatch):
    from nxserver import editorrefs
    client,store,release,resource,request=references_pilot;report=plan(store,request)
    original=editorrefs.target_table;calls=[]
    def racing(*args):
        result=original(*args)
        if args[2]=='tool_material':
            calls.append(True)
            if len(calls)==2:store.save_resource(resource_body(resource),'independent writer',resource['id'],1)
        return result
    monkeypatch.setattr(editorrefs,'target_table',racing)
    with pytest.raises(DomainError,match='Resource changed'):apply(store,request,report['preview_hash'],0,'MATREF','REF_A')
    assert store.resources()[0]['revision']==2 and store.resources()[0]['cam']==request['cam']


def test_oversized_manifest_target_fails_before_reading_the_file(references_pilot,monkeypatch):
    from nxserver.schemas import MAX_LIBRARY
    from pathlib import Path
    client,store,release,resource,request=references_pilot
    manifest=copy.deepcopy(release['manifest']);relative=SPECS['tool_material']['path']
    next(item for item in manifest if item['path']==relative)['size']=MAX_LIBRARY+1
    with store.db() as db:db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical(manifest),release['id']))
    candidate=store.release_folder(release['id'])/relative;original=Path.open
    def forbidden(path,*args,**kwargs):
        if path==candidate:raise AssertionError('Oversized target was read')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',forbidden)
    with pytest.raises(DomainError,match='reader bound'):options(store,release['id'],'tool-metric','MATREF')
