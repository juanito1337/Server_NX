"""Both native adoption scopes bind the actual source and catalog at SQL commit."""
import copy
import json

import pytest

from nxserver.libraries import SPECS,validate_catalog_cam
from nxserver.safety import DomainError
from nxserver.store import canonical
from test_catalog_selection import setup,database,selected_pilot,adopt


@pytest.mark.parametrize('scope',['whole','selected'])
@pytest.mark.parametrize('drift',['source','baseline','version','folder','manifest','status','baseline_removed','catalog_payload','catalog_timestamp','catalog_json'])
def test_all_catalog_adoption_inputs_are_rechecked_before_first_resource_write(tmp_path_factory,monkeypatch,scope,drift):
    tmp_path=tmp_path_factory.mktemp('cc')
    store,release,source,standard=setup(tmp_path,('holder-metric',))
    existing=store.save_resource({'kind':'gauge','name':'Existing fixture','cam':{},'erp':None,'asset_ids':[],
        'cam_status':'draft','geometry':{'mode':'none','status':'needs_preparation'}},'fixture')
    selection=None if scope=='whole' else {'holder-metric':['A']}
    _,report=store.catalog_plan(release['id'],['holder-metric'],selection=selection)
    original=store.catalog_plan;before_audit=store.audit_entries();before_history=store.history(existing['id'])
    def mutate(*args,**kwargs):
        result=original(*args,**kwargs)
        if drift in ('source','baseline','baseline_removed'):
            path=(store.release_folder(release['id']) if drift=='source' else standard)/SPECS['holder-metric']['path']
            if drift=='baseline_removed':path.unlink()  # This disposable fixture only.
            else:path.write_text(database(['STD','A','CHANGED']),encoding='utf-8')
        else:
            with store.db() as db:
                if drift=='version':db.execute("UPDATE releases SET nx_version='2406' WHERE id=?",(release['id'],))
                elif drift=='folder':db.execute('UPDATE releases SET folder=? WHERE id=?',(str(tmp_path/'different'),release['id']))
                elif drift=='manifest':db.execute("UPDATE releases SET manifest='[]' WHERE id=?",(release['id'],))
                elif drift=='status':db.execute("UPDATE releases SET status='failed' WHERE id=?",(release['id'],))
                elif drift=='catalog_timestamp':db.execute("UPDATE resources SET updated_at='different' WHERE id=?",(existing['id'],))
                else:
                    value={name:copy.deepcopy(data) for name,data in existing.items() if name not in ('id','revision','updated_at')}
                    value['name']='Changed without revision'
                    raw='{' if drift=='catalog_json' else canonical(value)
                    db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,existing['id']))
        return result
    monkeypatch.setattr(store,'catalog_plan',mutate)
    with pytest.raises(DomainError) as failure:
        store.import_catalog(release['id'],['holder-metric'],False,report['preview_hash'],'fixture',selection=selection)
    assert failure.value.status==409
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM resources').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM resource_versions').fetchone()[0]==1
    assert store.audit_entries()==before_audit and store.history(existing['id'])==before_history
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('scope',['whole','selected'])
def test_adoption_does_not_create_cam_data_rejected_by_the_common_editor_contract(tmp_path_factory,scope):
    tmp_path=tmp_path_factory.mktemp('cc')
    from test_libraries import write
    from nxserver.store import Store
    source=tmp_path/'source';standard=tmp_path/'standard';identifier='holder-metric'
    text='#CLASS SAMPLE\nFORMAT LIBRF DESCR INVALID-FIELD\nDATA |A|Synthetic fixture|future\n#END_DATA\n'
    write(source,SPECS[identifier]['path'],text);write(standard,SPECS[identifier]['path'],text.replace('|A|','|STD|'))
    store=Store(tmp_path/'managed',{'test':source},standards={'2512':standard})
    release=store.import_release('test','2512','Synthetic invalid schema','fixture')
    selection=None if scope=='whole' else {identifier:['A']};audit=store.audit_entries()
    candidates,report=store.catalog_plan(release['id'],[identifier],selection=selection)
    assert not report['can_import'] and report['count']==0 and candidates==[]
    assert report['conflicts'][0]['key']=='A' and 'FORMAT' in report['conflicts'][0]['reason']
    with pytest.raises(DomainError):store.import_catalog(release['id'],[identifier],False,report['preview_hash'],'fixture',selection=selection)
    assert store.resources()==[] and store.audit_entries()==audit


@pytest.mark.parametrize('scope',['whole','selected'])
@pytest.mark.parametrize('drift',['source_removed','baseline_added'])
def test_missing_or_new_baseline_presence_is_also_bound_at_commit(tmp_path_factory,monkeypatch,scope,drift):
    from test_libraries import write
    store,release,_,standard=setup(tmp_path_factory.mktemp('cc'),('holder-metric',));identifier='holder-metric'
    if drift=='baseline_added':(standard/SPECS[identifier]['path']).unlink()
    selection=None if scope=='whole' else {identifier:['A']};include_standard=drift=='baseline_added'
    _,report=store.catalog_plan(release['id'],[identifier],include_standard,selection)
    before=store.audit_entries();original=store.catalog_plan
    def mutate(*args,**kwargs):
        result=original(*args,**kwargs)
        if drift=='source_removed':(store.release_folder(release['id'])/SPECS[identifier]['path']).unlink()
        else:write(standard,SPECS[identifier]['path'],database(['STD']))
        return result
    monkeypatch.setattr(store,'catalog_plan',mutate)
    with pytest.raises(DomainError) as failure:store.import_catalog(release['id'],[identifier],include_standard,report['preview_hash'],'fixture',selection=selection)
    assert failure.value.status==409 and not store.resources() and store.audit_entries()==before


@pytest.mark.parametrize('identifier',list(SPECS))
@pytest.mark.parametrize('scope',['whole','selected'])
def test_all_adapters_import_complete_groups_through_common_contract(tmp_path_factory,identifier,scope):
    store,release,source,standard=setup(tmp_path_factory.mktemp('cc'),(identifier,))
    selection=None if scope=='whole' else {identifier:['A']}
    candidates,report=store.catalog_plan(release['id'],[identifier],selection=selection)
    assert report['commit_contract_version']==1 and report['draft_only'] is True and report['nx_files_changed'] is False
    assert set(report['source_inputs'])==set(report['standard_inputs'])=={identifier}
    for item in report['objects']:
        assert item['record_count']==2 and item['format_blocks']==[{'id':'MULTI:1','class_name':'MULTI','fields':['LIBRF','RTYPE','SEQ','DESCR'],'record_count':2,'baseline_fields':['LIBRF','RTYPE','SEQ','DESCR']}]
        assert item['kind']==SPECS[identifier]['kind']
    before=store.manifest(store.release_folder(release['id']));source_before=store.manifest(source);standard_before=store.manifest(standard)
    result=store.import_catalog(release['id'],[identifier],False,report['preview_hash'],'fixture',selection=selection)
    assert result['imported']==(3 if scope=='whole' else 1)
    for resource in store.resources():
        validate_catalog_cam(resource);assert resource['cam_status']=='draft' and resource['geometry']['status']=='needs_preparation'
        assert len(resource['cam']['records'])==2
    assert store.manifest(store.release_folder(release['id']))==before and store.manifest(source)==source_before and store.manifest(standard)==standard_before and store.active()['release_id'] is None


@pytest.mark.parametrize('scope',['whole','selected'])
def test_different_format_columns_are_previewed_and_preserved_not_auto_mapped(tmp_path_factory,scope):
    from test_libraries import write
    from nxserver.store import Store
    root=tmp_path_factory.mktemp('cc');source=root/'source';standard=root/'standard';identifier='holder-metric'
    original='#CLASS MULTI\nFORMAT LIBRF RTYPE SEQ DESCR FUTURE_2512\nDATA |A|1|1|Root|first\nDATA |A|2|2|Child|second\n#END_DATA\n#CLASS MULTI\nFORMAT LIBRF RTYPE SEQ NEW_COMPONENT\nDATA |A|3|3|third\n#END_DATA\n'
    baseline='#CLASS MULTI\nFORMAT LIBRF RTYPE SEQ DESCR STANDARD_ONLY\nDATA |STD|1|1|Standard|x\n#END_DATA\n#CLASS MULTI\nFORMAT LIBRF RTYPE SEQ OLD_COMPONENT\nDATA |STD|3|3|old\n#END_DATA\n'
    write(source,SPECS[identifier]['path'],original);write(standard,SPECS[identifier]['path'],baseline)
    store=Store(root/'managed',{'test':source},standards={'2512':standard});release=store.import_release('test','2512','Different columns','fixture')
    selection=None if scope=='whole' else {identifier:['A']};candidates,report=store.catalog_plan(release['id'],[identifier],selection=selection)
    item=report['objects'][0];assert item['record_count']==3 and [block['record_count'] for block in item['format_blocks']]==[2,1]
    assert item['format_blocks'][0]['fields'][-1]=='FUTURE_2512' and item['format_blocks'][0]['baseline_fields'][-1]=='STANDARD_ONLY'
    assert item['format_blocks'][1]['fields'][-1]=='NEW_COMPONENT' and item['format_blocks'][1]['baseline_fields'][-1]=='OLD_COMPONENT'
    store.import_catalog(release['id'],[identifier],False,report['preview_hash'],'fixture',selection=selection)
    resource=store.resources()[0];assert resource['cam']==candidates[0]['cam'];validate_catalog_cam(resource)
    assert resource['cam']['records'][1]['values']['FUTURE_2512']=='second' and resource['cam']['records'][2]['values']['NEW_COMPONENT']=='third'
    assert 'STANDARD_ONLY' not in resource['cam']['records'][0]['values'] and 'OLD_COMPONENT' not in resource['cam']['records'][2]['values']
    assert (store.release_folder(release['id'])/SPECS[identifier]['path']).read_text(encoding='utf-8-sig')==original


@pytest.mark.parametrize('scope',['whole','selected'])
@pytest.mark.parametrize('drift',['source','baseline','catalog_payload'])
def test_failed_final_gate_has_no_partial_result_and_original_receipt_does_not_restart(selected_pilot,monkeypatch,scope,drift):
    from test_management import HEADERS
    from nxserver.migration_requests import get
    client,store,release,source,standard=selected_pilot
    existing=store.save_resource({'kind':'gauge','name':'Keep','cam':{},'erp':None,'asset_ids':[],'cam_status':'draft'},'fixture')
    body={'libraries':['holder-metric'],'include_standard':False}
    if scope=='selected':body['selection']={'holder-metric':['A']}
    response=client.post('/api/v1/releases/'+release['id']+'/catalog-preview',headers=HEADERS,json=body);assert response.status_code==200
    report=response.json();original=store.catalog_plan;mutations=[]
    def mutate(*args,**kwargs):
        value=original(*args,**kwargs);mutations.append(True)
        if drift=='catalog_payload':
            with store.db() as db:
                raw=json.loads(db.execute('SELECT payload FROM resources WHERE id=?',(existing['id'],)).fetchone()[0]);raw['name']='Different at commit'
                db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(raw),existing['id']))
        else:
            path=(store.release_folder(release['id']) if drift=='source' else standard)/SPECS['holder-metric']['path']
            path.write_text(database(['STD','A','CHANGED']),encoding='utf-8')
        return value
    monkeypatch.setattr(store,'catalog_plan',mutate)
    first,done,key=adopt(client,release,body,report)
    assert done['status']=='failed' and 'before native import commit' in done['message'] and not done.get('domain_result')
    assert len(store.resources())==1 and store.resources()[0]['id']==existing['id'] and len(store.history(existing['id']))==1
    assert get(store,'admin',key)['result']==first.json()
    audits=store.audit_entries()
    def forbidden(*args,**kwargs):pytest.fail('Historical adoption receipt must not replan or restart.')
    monkeypatch.setattr(store,'catalog_plan',forbidden);monkeypatch.setattr(client.app.state.upgrades.pool,'submit',forbidden)
    replay=client.post('/api/v1/release-jobs/catalog',headers={**HEADERS,'Idempotency-Key':key},json={**body,'release_id':release['id'],'preview_hash':report['preview_hash']})
    assert replay.status_code==202 and replay.json()==first.json() and len(mutations)==1 and store.audit_entries()==audits and store.active()['release_id'] is None
