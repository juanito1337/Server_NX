"""Native relation semantics and explicitly reviewed, immutable import corrections."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from nxserver.libraries import NativeTable, SPECS, normalization_plan, reference_report, reference_targets
from nxserver.migration import Planner
from nxserver.references import choices
from nxserver.safety import DomainError
from test_libraries import write
from test_management import HEADERS, ERP_HEADERS
from test_upgrades import upgrade_pilot, finish, preview


def table(fields, rows, class_name='TABLE'):
    return '#CLASS '+class_name+'\nFORMAT '+' '.join(fields)+'\n'+''.join(
        'DATA | '+' | '.join(row)+'\n' for row in rows)+'#END_DATA\n'


def reference_data(store, source):
    material=table(['LIBRF','MATNAM','MATDESC'], [('TMC0_00007','Coated','Standard')])
    stocktool=table(['LIBRF','MATREF','DESCR'], [('STD','TMC0_00007','Stock')])
    for root in store.standards.values():
        write(root,SPECS['tool_material']['path'],material+'#DATA | TMC0_00001 | HSS | Not active\n')
        write(root,SPECS['tool-metric']['path'],stocktool)
    write(source,SPECS['tool_material']['path'],material)
    write(source,SPECS['tool_material']['legacy_path'],table(['LIBRF','MATNAM','MATDESC'],
        [('TMC0_000055','VHM','Custom carbide')]))
    write(source,SPECS['tool-metric']['path'],table(['LIBRF','MATREF','DESCR'],
        [('KEY1','TMC0_00055','Customer'),('KEY1','TMC0_00007','Second row'),
         ('KEY2','TMC0_00055','Customer 2')]))
    return {'tool-metric':{'KEY1':{'MATREF':{'TMC0_00055':'TMC0_000055'}},
                           'KEY2':{'MATREF':{'TMC0_00055':'TMC0_000055'}}}}


@pytest.mark.parametrize('prefix',['','cmm-'])
def test_partmat_description_is_not_a_relation_but_cutting_data_still_is(tmp_path,prefix):
    part=SPECS[prefix+'part_material'];feed=SPECS[prefix+'feeds_speeds']
    write(tmp_path,part['path'],table(['LIBRF','PARTMAT'],[('MAT_CUSTOM','HSM With Proven Machining Data')]))
    write(tmp_path,feed['path'],table(['LIBRF','PARTMAT'],[('FEED','MAT_MISSING')]))
    report=reference_report(tmp_path)
    assert len(report['errors'])==1
    assert report['errors'][0]['library']==feed['id'] and report['errors'][0]['target']==part['id']
    assert reference_targets(part['id'])=={}


@pytest.mark.parametrize('identifier',list(SPECS))
def test_adapter_relations_only_target_registered_libraries(identifier):
    assert all(target in SPECS for target in reference_targets(identifier).values())
    if SPECS[identifier]['kind'] in ('part_material','tool_material','holder','machine','device','fixture'):
        assert not reference_targets(identifier)


def test_preview_requires_explicit_exact_corrections_and_preserves_group_rows(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    decisions=reference_data(store,source);before=store.manifest(source)
    outputs,blocked=normalization_plan(source,store.standards,'2512','2406')
    assert not blocked['can_import'] and len(blocked['references']['errors'])==2
    assert all(c['type']=='reference' for c in blocked['conflicts'])
    assert blocked['reference_resolutions']==[]
    assert blocked['references']['errors'][0]['candidates'][0]['key']=='TMC0_000055'
    assert 'TMC0_00001' not in {c['key'] for c in blocked['references']['errors'][0]['candidates']}
    ready,report=normalization_plan(source,store.standards,'2512','2406',reference_resolutions=decisions)
    assert report['can_import'] and not report['references']['errors']
    groups=NativeTable(ready[SPECS['tool-metric']['path']]).groups()
    assert [r['values']['MATREF'] for r in groups['KEY1']]==['TMC0_000055','TMC0_00007']
    assert groups['STD'][0]['values']['MATREF']=='TMC0_00007'
    assert [d['affected_rows'] for d in report['reference_resolutions']]==[1,1]
    assert store.manifest(source)==before and store.releases()==[]
    assert report['result_hashes']!=blocked['result_hashes']


def test_prepared_import_does_not_invent_missing_active_libraries_from_installation(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    reference_data(store,source)
    (source/SPECS['tool_material']['path']).unlink()  # Own fixture only.
    (source/SPECS['tool_material']['legacy_path']).unlink()
    outputs,report=normalization_plan(source,store.standards,'2512','2406')
    assert not report['can_import'] and SPECS['tool_material']['path'] not in outputs
    assert report['references']['errors'][0]['candidate_count']==0
    # Whole-share upgrade really copies the target standards; its available
    # choices therefore differ honestly from prepared-folder import.
    plan,full=Planner(store).build(request)
    assert full['references']['errors'][0]['candidate_count']==1


@pytest.mark.parametrize('replacement', ['TMC0_00001','TMC0_000055 ', '', 'TMC0_00055', 'bad|key'])
def test_invalid_inactive_or_guessed_target_is_rejected_without_writes(upgrade_pilot,replacement):
    client,store,request,source=upgrade_pilot
    decisions=reference_data(store,source)
    decisions['tool-metric']['KEY1']['MATREF']['TMC0_00055']=replacement
    before=store.manifest(source)
    with pytest.raises(DomainError):
        normalization_plan(source,store.standards,'2512','2406',reference_resolutions=decisions)
    assert store.manifest(source)==before and store.releases()==[]


def test_standard_warnings_are_visible_but_cannot_be_remapped_as_customer_data(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    decisions=reference_data(store,source)
    stock=table(['LIBRF','MATREF','DESCR'], [('STD','TMC0_00001','Unresolved example')])
    for root in store.standards.values():write(root,SPECS['tool-metric']['path'],stock)
    decisions['tool-metric']['STD']={'MATREF':{'TMC0_00001':'TMC0_000055'}}
    with pytest.raises(DomainError,match='unresolved customer'):
        normalization_plan(source,store.standards,'2512','2406',reference_resolutions=decisions)
    del decisions['tool-metric']['STD']
    _,report=normalization_plan(source,store.standards,'2512','2406',reference_resolutions=decisions)
    assert report['can_import'] and report['references']['warning_rows']==1


@pytest.mark.parametrize('data', [[],{'unknown':{}},{'tool-metric':{'KEY':{'DESCR':{'old':'new'}}}},
    {'part_material':{'KEY':{'PARTMAT':{'old':'new'}}}}, {'tool-metric':{'KEY':{'MATREF':{'old':3}}}},
    {'tool-metric':{'KEY':{'MATREF':['old','new']}}}])
def test_invalid_mapping_shapes_and_non_reference_fields_are_rejected(data):
    with pytest.raises(DomainError):choices(data)


def test_decision_limit_is_enforced():
    with pytest.raises(DomainError,match='1,000'):
        choices({'tool-metric':{str(i):{'MATREF':{'old':'new'}} for i in range(1001)}})


def test_omitting_a_valid_standard_reference_target_is_error_not_stock_warning(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    reference_data(store,source)
    write(source,SPECS['tool-metric']['path'],table(['LIBRF','MATREF','DESCR'], [('STD','TMC0_00007','Stock')]))
    (source/SPECS['tool_material']['path']).unlink()
    (source/SPECS['tool_material']['legacy_path']).unlink()
    _,report=normalization_plan(source,store.standards,'2512','2406')
    assert not report['can_import'] and len(report['references']['errors'])==1
    error=report['references']['errors'][0]
    assert error['standard_object'] and error['key']=='STD'
    with pytest.raises(DomainError,match='unresolved customer'):
        normalization_plan(source,store.standards,'2512','2406',reference_resolutions={
            'tool-metric':{'STD':{'MATREF':{'TMC0_00007':'TMC0_000055'}}}})


def test_prepared_gui_api_preview_import_and_stale_mapping_hash(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    decisions=reference_data(store,source);before=store.manifest(source)
    body={'source':'source','nx_version':'2512','name':'Reviewed refs','normalize_libraries':True,
          'legacy_version':'2406','reference_resolutions':decisions}
    first=client.post('/api/v1/import-preview',headers=HEADERS,json=body)
    assert first.status_code==200,first.text
    report=first.json();assert report['can_import']
    changed=copy.deepcopy(body);changed['reference_resolutions']['tool-metric']['KEY1']['MATREF']['TMC0_00055']='TMC0_00007'
    stale=client.post('/api/v1/releases',headers=HEADERS,json={**changed,'preview_hash':report['preview_hash']})
    assert stale.status_code==409 and store.releases()==[]
    response=client.post('/api/v1/releases',headers=HEADERS,json={**body,'preview_hash':report['preview_hash']})
    assert response.status_code==201,response.text
    release=response.json();folder=store.release_folder(release['id'])
    assert {path:hashlib.sha256((folder/path).read_bytes()).hexdigest()
            for path in report['result_hashes']}==report['result_hashes']
    assert NativeTable.read(folder/SPECS['tool-metric']['path']).groups()['KEY1'][0]['values']['MATREF']=='TMC0_000055'
    assert release['catalog']['normalization']['reference_resolutions']==report['reference_resolutions']
    assert store.manifest(source)==before and store.active()['generation']==0
    assert not (folder/SPECS['tool_material']['legacy_path']).exists()


def test_api_roles_shapes_and_disabled_normalization_cannot_ignore_choices(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    decisions=reference_data(store,source)
    body={'source':'source','nx_version':'2512','name':'No bypass','reference_resolutions':decisions}
    assert client.post('/api/v1/releases',headers=HEADERS,json=body).status_code==400
    assert client.post('/api/v1/import-preview',json=body).status_code==401
    assert client.post('/api/v1/import-preview',headers=ERP_HEADERS,json=body).status_code in (401,403)
    assert client.post('/api/v1/import-preview',headers=HEADERS,json={**body,'reference_resolutions':{'tool-metric':[]}}).status_code==422
    assert client.get('/references.js').status_code==200
    assert store.releases()==[]


def test_actual_upgrade_binds_applied_decisions_and_does_not_modify_source(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    decisions=reference_data(store,source);before=store.manifest(source)
    _,blocked=preview(client,request)
    assert not blocked['can_build'] and sum(c['category']=='references' for c in blocked['conflicts'])==2
    request['reference_resolutions']=decisions
    job,report=preview(client,request)
    assert report['can_build'] and len(report['libraries']['reference_resolutions'])==2
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='completed',result
    release=store.get_release(result['release_id'])
    groups=NativeTable.read(Path(release['folder'])/SPECS['tool-metric']['path']).groups()
    assert groups['KEY2'][0]['values']['MATREF']=='TMC0_000055'
    assert not reference_report(Path(release['folder']),store.standards,'2512')['errors']
    assert store.manifest(source)==before and store.active()['generation']==0


def test_target_reference_definition_is_bound_even_if_not_itself_modified(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    reference_data(store,source)
    request['reference_resolutions']={'tool-metric':{'KEY1':{'MATREF':{'TMC0_00055':'TMC0_00007'}},
                                                   'KEY2':{'MATREF':{'TMC0_00055':'TMC0_00007'}}}}
    job,report=preview(client,request);assert report['can_build']
    write(store.standards['2512'],SPECS['tool_material']['path'],table(['LIBRF','MATNAM','MATDESC'],
        [('TMC0_00007','Changed semantic definition','Different')]))
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='failed' and not Path(request['target_path']).exists()
    assert store.releases()==[]


def test_managed_upgrade_keeps_owned_corrected_objects_editable_without_catalog_rewrite(upgrade_pilot):
    from test_libraries import resource_body
    client,store,request,source=upgrade_pilot
    decisions=reference_data(store,source)
    field_spec=SPECS['feeds_speeds']
    for root in store.standards.values():
        write(root,field_spec['path'],table(['LIBRF','TOOLMAT','DESCR'],[('STDF','TMC0_00007','Stock')]))
    write(source,field_spec['path'],table(['LIBRF','TOOLMAT','DESCR'],[('FEED_CUSTOM','TMC0_00055','Custom feeds')]))
    decisions['feeds_speeds']={'FEED_CUSTOM':{'TOOLMAT':{'TMC0_00055':'TMC0_000055'}}}
    old=store.import_release('source','2406','Old refs','fixture')
    catalog_body={'libraries':['feeds_speeds']};url='/api/v1/releases/'+old['id']
    proposal=client.post(url+'/catalog-preview',headers=HEADERS,json=catalog_body).json()
    assert client.post(url+'/catalog-import',headers=HEADERS,json={**catalog_body,'preview_hash':proposal['preview_hash']}).status_code==200
    original=store.resources()[0]
    request.update(source=None,source_path=None,source_release_id=old['id'],reference_resolutions=decisions)
    job,report=preview(client,request);assert report['can_build']
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='completed',result
    migrated=store.get_release(result['release_id']);catalog=migrated['catalog']
    assert original['id'] in catalog['exported_native'] and original['id'] not in catalog['migration_exclusions']
    assert catalog['reference_reviews'][original['id']]['catalog_changed'] is False
    assert next(r for r in store.resources() if r['id']==original['id'])==original
    # Draft daily export keeps the corrected NX values; approving the old CAM
    # values cannot roll those references back to a dangling key.
    daily=store.import_release('','','Daily with draft','fixture',parent=migrated['id'])
    payload=resource_body(original);payload['cam_status']='approved'
    saved=store.save_resource(payload,'fixture',original['id'],original['revision'])
    blocked=store.preview_export(daily['id'])
    assert not blocked['can_export']
    assert blocked['conflicts'][0]['resource_id']==original['id']
    assert original['id'] in blocked['reference_reviews']
    # An explicit catalog revision with the reviewed new reference works normally.
    payload=resource_body(saved);payload['cam']['records'][0]['values']['TOOLMAT']='TMC0_000055'
    payload['cam']['records'][0]['values']['DESCR']='Daily updated feeds'
    payload['cam']['nx_version']='2512'
    corrected=store.save_resource(payload,'fixture',saved['id'],saved['revision'])
    assert store.preview_export(daily['id'])['can_export']
    final=store.import_release('','','Reviewed daily','fixture',parent=daily['id'])
    values=NativeTable.read(Path(final['folder'])/field_spec['path']).groups()['FEED_CUSTOM'][0]['values']
    assert values['TOOLMAT']=='TMC0_000055' and values['DESCR']=='Daily updated feeds'
    assert next(r for r in store.resources() if r['id']==original['id'])==corrected
