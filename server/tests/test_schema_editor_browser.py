"""Paged schema decisions, explicit values and stale replies on synthetic data."""
import json
import os
from pathlib import Path

import pytest

from nxserver.libraries import NativeTable,SPECS,native_cam
from test_libraries import write
from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated schema editor workflow.')


@pytest.fixture
def schema(ui,request):
    from playwright.sync_api import expect
    page,store,*_=ui;config=getattr(request,'param',{})
    count=config.get('count',25);column=config.get('column','EXTRA')
    target='#CLASS A\nFORMAT DESCR LIBRF SEQ '+column+'\nDATA |Standard|STD_A|0|0\n#CLASS B\nFORMAT LIBRF SEQ DESCR '+column+'\nDATA |STD_B|0|Standard|0\n#END_DATA\n'
    write(store.sources['test'],SPECS['holder-metric']['path'],target)
    release_value=store.import_release('test','2512','Schema target','fixture')
    raw='#CLASS A\nFORMAT LIBRF SEQ DESCR\n'+''.join('DATA |CUSTOM_SCHEMA|'+str(index)+'|Source A '+str(index)+'\n' for index in range(count))
    raw+='#CLASS B\nFORMAT LIBRF SEQ DESCR\nDATA |CUSTOM_SCHEMA|0|Source B 0\nDATA |CUSTOM_SCHEMA|1|Source B 1\n#END_DATA\n'
    table=NativeTable(raw)
    resource=store.save_resource({'kind':'holder','name':'Synthetic schema group','cam_status':'draft','asset_ids':[],'erp':None,
        'cam':native_cam('holder-metric','2406',table,table.records)},'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#schema-adaptation summary').click();page.locator('#schema-target-release').select_option(release_value['id'])
    page.locator('#schema-preview').click();expect(page.locator('#schema-state')).to_contain_text('offene Entscheidung')
    yield page,store,release_value,resource,count,column


def row(page,index):return page.locator('[data-schema-row="'+str(index)+'"]')
def value(page,index,column='EXTRA'):return row(page,index).locator('[data-schema-field="'+column+'"]')
def check(page,index,column='EXTRA'):return row(page,index).locator('[data-schema-confirm="'+column+'"]')
def bulk(page,index,column='EXTRA'):return row(page,index).locator('[data-schema-bulk="'+column+'"]')


def complete(page,count,column='EXTRA'):
    from playwright.sync_api import expect
    page.on('dialog',lambda dialog:dialog.accept());value(page,0,column).fill('Common A');bulk(page,0,column).click()
    for _ in range(count//10):page.locator('#schema-next').click()
    value(page,count,column).fill('Common B');bulk(page,count,column).click()
    page.locator('#schema-preview').click();expect(page.locator('#schema-apply')).to_be_enabled()


def test_paging_bulk_preserves_explicit_exceptions_and_creates_only_a_draft_on_save(schema):
    from playwright.sync_api import expect
    page,store,target,resource,count,column=schema;page.on('dialog',lambda dialog:dialog.accept())
    before=store.audit_entries();manifest=store.manifest(Path(target['folder']))
    expect(page.locator('#schema-rows > fieldset')).to_have_count(10);expect(page.locator('#schema-page')).to_contain_text('1–10 von 27')
    value(page,0).fill('Only row zero');check(page,0).check();value(page,1).fill('Unconfirmed input survives paging')
    page.locator('#schema-next').click();value(page,10).fill('Specific row ten');check(page,10).check()
    page.locator('#schema-prev').click();expect(value(page,1)).to_have_value('Unconfirmed input survives paging');expect(check(page,1)).not_to_be_checked()
    value(page,2).fill('Common A');bulk(page,2).click();expect(bulk(page,0)).to_be_disabled()
    expect(value(page,0)).to_have_value('Only row zero');page.locator('#schema-next').click();expect(value(page,10)).to_have_value('Specific row ten')
    page.locator('#schema-next').click();expect(page.locator('#schema-rows > fieldset')).to_have_count(7)
    expect(check(page,count)).not_to_be_checked();value(page,count).fill('');bulk(page,count).click()
    page.locator('#schema-preview').click();expect(page.locator('#schema-apply')).to_be_enabled()
    page.locator('#schema-apply').click();expect(page.locator('#message')).to_contain_text('Schema in Editor übernommen')
    cam=json.loads(page.locator('#cam').input_value());assert cam['nx_version']=='2512' and len(cam['records'])==count+2
    assert [item['values'][column] for item in cam['records']]==['Only row zero']+['Common A']*9+['Specific row ten']+['Common A']*(count-11)+['','']
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1
    assert store.manifest(Path(target['folder']))==manifest and store.active()['release_id'] is None
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=next(item for item in store.resources() if item['id']==resource['id'])
    assert saved['revision']==2 and saved['cam_status']=='draft' and saved['cam']==cam
    assert json.loads(store.history(resource['id'])[0]['payload'])['cam']['nx_version']=='2406'
    assert store.manifest(Path(target['folder']))==manifest and store.active()['release_id'] is None


def test_structural_decision_blocks_bulk_until_new_layout_preview(schema):
    from playwright.sync_api import expect
    page,store,_,resource,*_=schema;before=store.audit_entries()
    row(page,0).locator('select').first.select_option('B:1');expect(bulk(page,1)).to_be_disabled()
    row(page,0).locator('select').first.select_option('A:1');expect(bulk(page,1)).to_be_disabled()
    page.locator('#schema-preview').click();expect(bulk(page,1)).to_be_enabled()
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1


def test_declined_bulk_confirmation_preserves_unconfirmed_input_only(schema):
    from playwright.sync_api import expect
    page,store,_,resource,*_=schema;before=store.audit_entries();original=page.locator('#cam').input_value()
    page.on('dialog',lambda dialog:dialog.dismiss());value(page,0).fill('Not confirmed for other rows');bulk(page,0).click()
    expect(check(page,0)).not_to_be_checked();expect(check(page,1)).not_to_be_checked();expect(value(page,1)).to_have_value('')
    page.locator('#schema-next').click();expect(check(page,10)).not_to_be_checked();page.locator('#schema-prev').click()
    expect(value(page,0)).to_have_value('Not confirmed for other rows');page.locator('#schema-preview').click()
    expect(page.locator('#schema-apply')).to_be_disabled();expect(page.locator('#cam')).to_have_value(original)
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1


@pytest.mark.parametrize('old_error',[False,True])
def test_decision_aba_does_not_rearm_old_schema_preview(schema,old_error):
    from playwright.sync_api import expect
    page,store,*_=schema;before=store.audit_entries();value(page,0).fill('Original');check(page,0).check()
    hold(page,'/schema/preview',old_error);track(page,'schema-preview');page.locator('#schema-preview').click();page.wait_for_function('()=>window.__held')
    value(page,0).fill('Different');value(page,0).fill('Original');release(page)
    expect(page.locator('#schema-apply')).to_be_disabled();expect(page.locator('#schema-preview')).to_be_enabled()
    expect(page.locator('#schema-state')).to_contain_text('Entscheidungen geändert');expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.audit_entries()==before


def test_apply_reply_after_value_aba_cannot_change_editor_or_reset_geometry(schema):
    from playwright.sync_api import expect
    page,store,_,resource,count,column=schema;complete(page,count,column);original=page.locator('#cam').input_value();before=store.audit_entries()
    hold(page,'/schema/apply');track(page,'schema-apply');page.locator('#schema-apply').click();page.wait_for_function('()=>window.__held')
    value(page,count).fill('Different');value(page,count).fill('Common B');release(page)
    expect(page.locator('#cam')).to_have_value(original);expect(page.locator('#schema-apply')).to_be_disabled()
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1


def test_old_preview_finally_cannot_unlock_a_new_preview_owner(schema):
    from playwright.sync_api import expect
    page,store,*_=schema;before=store.audit_entries()
    hold(page,'/schema/preview');track(page,'schema-preview');page.locator('#schema-preview').click();page.wait_for_function('()=>window.__held')
    page.evaluate('window.__releaseOldSchema=window.__releaseHeld');value(page,0).fill('New value');check(page,0).check()
    hold(page,'/schema/preview');page.locator('#schema-preview').click();page.wait_for_function('()=>window.__held')
    page.evaluate('window.__releaseOldSchema()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#schema-preview')).to_be_disabled();expect(page.locator('#schema-state')).to_contain_text('Prüfe Zielschema')
    page.evaluate('window.__releaseHeld()');expect(page.locator('#schema-preview')).to_be_enabled();expect(value(page,0)).to_have_value('New value')
    assert store.audit_entries()==before


def test_lost_save_receipt_blocks_schema_apply_without_erasing_decisions(schema):
    from playwright.sync_api import expect
    page,store,_,resource,count,column=schema;complete(page,count,column);original=page.locator('#cam').input_value()
    page.locator('#name').fill('Save not confirmed yet');lose(page,'/api/v1/resources/'+resource['id'])
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    expect(page.locator('#schema-apply')).to_be_disabled();expect(value(page,count)).to_have_value('Common B')
    expect(page.locator('#cam')).to_have_value(original);saved=next(item for item in store.resources() if item['id']==resource['id'])
    assert saved['revision']==2 and saved['cam']['nx_version']=='2406' and store.active()['release_id'] is None
    page.locator('#name').fill('Later unsubmitted name, preserve schema decisions')
    page.locator('#save-receipt').click();expect(page.locator('#message')).to_contain_text('Schreibbeleg bestätigt')
    expect(page.locator('#name')).to_have_value('Later unsubmitted name, preserve schema decisions')
    expect(value(page,count)).to_have_value('Common B');expect(page.locator('#schema-apply')).to_be_disabled()
    page.locator('#schema-preview').click();expect(page.locator('#schema-apply')).to_be_enabled()
    assert store.history(resource['id'])[-1]['revision']==2 and store.active()['release_id'] is None


def test_lost_upload_receipt_blocks_schema_apply_and_preserves_values(schema):
    from playwright.sync_api import expect
    page,store,_,resource,count,column=schema;complete(page,count,column);original=page.locator('#cam').input_value()
    page.locator('#assetfile').set_input_files({'name':'synthetic.pdf','mimeType':'application/pdf','buffer':b'Synthetic attachment, no real PDF or geometry qualification.'})
    lose(page,'/api/v1/assets');page.locator('#upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    expect(page.locator('#schema-apply')).to_be_disabled();expect(value(page,count)).to_have_value('Common B')
    expect(page.locator('#cam')).to_have_value(original);page.locator('#upload-receipt').click()
    expect(page.locator('#message')).to_contain_text('Datei gespeichert und SHA-256 geprüft')
    expect(value(page,count)).to_have_value('Common B');expect(page.locator('#schema-apply')).to_be_enabled()
    assert store.history(resource['id'])[0]['revision']==1 and store.active()['release_id'] is None


@pytest.mark.parametrize('schema',[{'count':998}],indirect=True)
def test_limit_of_one_thousand_rows_has_bounded_dom_and_cross_page_bulk(schema):
    from playwright.sync_api import expect
    page,store,_,resource,count,column=schema;before=store.audit_entries();page.on('dialog',lambda dialog:dialog.accept())
    expect(page.locator('#schema-rows > fieldset')).to_have_count(10);expect(page.locator('#schema-page')).to_contain_text('von 1000')
    value(page,0).fill('Explicit thousand-row value');bulk(page,0).click()
    page.evaluate('()=>{for(let index=0;index<99;index++)document.getElementById("schema-next").onclick();}')
    expect(page.locator('#schema-page')).to_contain_text('991–1000');expect(value(page,count-1)).to_have_value('Explicit thousand-row value')
    expect(check(page,count)).not_to_be_checked();bulk(page,count).click()
    page.locator('#schema-preview').click();expect(page.locator('#schema-apply')).to_be_enabled()
    page.locator('#schema-apply').click();expect(page.locator('#message')).to_contain_text('Schema in Editor übernommen')
    cam=json.loads(page.locator('#cam').input_value());assert cam['nx_version']=='2512' and len(cam['records'])==1000
    assert [item['values'][column] for item in cam['records']]==['Explicit thousand-row value']*count+['','']
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1


@pytest.mark.parametrize('schema',[{'count':13,'column':'__proto__'}],indirect=True)
def test_arbitrary_column_names_remain_own_data_in_bulk_decisions(schema):
    from playwright.sync_api import expect
    page,store,_,resource,count,column=schema;before=store.audit_entries();complete(page,count,column)
    page.locator('#schema-apply').click();expect(page.locator('#message')).to_contain_text('Schema in Editor übernommen')
    cam=json.loads(page.locator('#cam').input_value());assert all(column in item['values'] for item in cam['records'])
    assert [item['values'][column] for item in cam['records']]==['Common A']*count+['Common B']*2
    assert page.evaluate('Object.prototype["EXTRA"]===undefined') and store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1


@pytest.mark.parametrize('fault',['saved_flag','changed_cam','object_array_collision','null_result'])
def test_apply_contract_and_exact_preview_values_are_checked_before_editor_change(schema,fault):
    from playwright.sync_api import expect
    page,store,_,resource,count,column=schema;complete(page,count,column);original=page.locator('#cam').input_value();before=store.audit_entries()
    def corrupt(route):
        result=route.fetch().json()
        if fault=='null_result':route.fulfill(status=200,body='null',content_type='application/json');return
        if fault=='saved_flag':result['saved']=True
        elif fault=='changed_cam':result['cam']['records'][0]['values'][column]='Different than human preview'
        else:result['cam']['records'][0]['values']=[[key,value] for key,value in sorted(result['cam']['records'][0]['values'].items())]
        route.fulfill(json=result)
    page.route('**/api/v1/schema/apply',corrupt);page.locator('#schema-apply').click()
    expect(page.locator('#message')).to_contain_text('Kein unterstützter schreibfreier Schemaänderungsvertrag')
    expect(page.locator('#cam')).to_have_value(original);expect(page.locator('#schema-apply')).to_be_disabled()
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1
