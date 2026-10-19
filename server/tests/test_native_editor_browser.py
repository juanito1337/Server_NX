"""Native group editing/navigation with complete synthetic records, never NX proof."""
import json
import os
from pathlib import Path

import pytest

from nxserver.libraries import NativeTable,SPECS,native_cam
from test_libraries import write
from test_management import tool_payload
from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated native editor workflows.')


@pytest.fixture
def editor(ui,request):
    from playwright.sync_api import expect
    page,store,*_=ui;count=getattr(request,'param',12)
    fields=['LIBRF','NAME','SEQ']+['F'+str(index).zfill(2) for index in range(1,71)]+['__proto__']
    raw='#CLASS A\nFORMAT '+' '.join(fields)+'\n'
    for index in range(count):raw+='DATA |'+'|'.join(['CUSTOM_GROUP','Description name '+str(index),str(index)]+['A'+str(index)+'_'+field for field in fields[3:]])+'\n'
    raw+='#CLASS B\nFORMAT NAME SEQ DESCR\nDATA |CUSTOM_GROUP|0|Body B zero\nDATA |CUSTOM_GROUP|1|Body B one\n#END_DATA\n'
    table=NativeTable(raw);cam=native_cam('holder-metric','2512',table,table.records)
    write(store.sources['test'],SPECS['holder-metric']['path'],raw.replace('CUSTOM_GROUP','STD_GROUP'))
    target=store.import_release('test','2512','Native editor target','fixture')
    resource=store.save_resource({'kind':'holder','name':'Native editor group','cam':cam,'cam_status':'approved','asset_ids':[],'erp':None,'geometry':tool_payload()['geometry']},'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    yield page,store,target,resource,count


def field(page,row,name):return page.locator('[data-native-row="'+str(row)+'"] [data-native-field="'+name+'"]')
def cam(page):return json.loads(page.locator('#cam').input_value())


def test_row_and_column_pages_preserve_full_records_and_make_only_a_draft_on_save(editor):
    from playwright.sync_api import expect
    page,store,target,resource,count=editor;before=store.audit_entries();manifest=store.manifest(Path(target['folder']))
    expect(page.locator('#native-fields > fieldset')).to_have_count(10);expect(page.locator('#native-fields input')).to_have_count(500)
    field(page,0,'F01').fill('First-page edit');page.locator('[data-native-column-next="0"]').click();field(page,0,'F70').fill('Hidden-column edit')
    page.locator('#native-field-next').click();field(page,count,'DESCR').fill('Second-block edit')
    page.locator('#native-field-prev').click();expect(field(page,0,'F70')).to_have_value('Hidden-column edit')
    page.locator('[data-native-column-prev="0"]').click();expect(field(page,0,'F01')).to_have_value('First-page edit')
    expected=cam(page);assert len(expected['records'])==count+2 and expected['records'][count]['values']['DESCR']=='Second-block edit'
    expect(page.locator('#camstatus')).to_have_value('draft');expect(page.locator('#geometry-status')).to_have_value('needs_preparation')
    assert store.audit_entries()==before and store.manifest(Path(target['folder']))==manifest
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=next(item for item in store.resources() if item['id']==resource['id'])
    assert saved['cam']==expected and saved['revision']==2 and saved['cam_status']=='draft'
    assert json.loads(store.history(resource['id'])[0]['payload'])['cam']==resource['cam']
    assert store.active()['release_id'] is None and store.manifest(Path(target['folder']))==manifest


def test_view_filters_and_navigation_do_not_change_body_or_geometry(editor):
    from playwright.sync_api import expect
    page,store,_,_,count=editor;signature=page.evaluate('editorSignature()');before=store.audit_entries()
    page.locator('#native-field-block').select_option('B:1');expect(page.locator('#native-fields > fieldset')).to_have_count(2)
    page.locator('#native-field-query').fill('does_not_exist');expect(page.locator('#native-fields input')).to_have_count(0)
    page.locator('#native-field-query').fill('DESC');expect(page.locator('#native-fields input')).to_have_count(2)
    page.locator('#native-field-row').fill('1');page.locator('#native-field-jump').click();expect(page.locator('#native-field-block')).to_have_value('')
    page.locator('#native-field-query').fill('__proto__');expect(field(page,0,'__proto__')).to_have_value('A0___proto__')
    assert page.evaluate('editorSignature()')==signature and store.audit_entries()==before
    expect(page.locator('#camstatus')).to_have_value('approved');expect(page.locator('#geometry-status')).to_have_value('validated')


@pytest.mark.parametrize('mode',['invalid','reorder','aba'])
def test_old_field_mask_never_writes_into_changed_raw_cam(editor,mode):
    from playwright.sync_api import expect
    page,store,*_=editor;original=page.locator('#cam').input_value();before=store.audit_entries()
    field(page,0,'F01').evaluate('(input)=>{window.__oldNativeInput=input;}')
    changed=cam(page);changed['records'][0],changed['records'][1]=changed['records'][1],changed['records'][0]
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
    page.locator('#cam').fill('{' if mode=='invalid' else json.dumps(changed))
    if mode=='aba':page.locator('#cam').fill(original)
    expected=page.locator('#cam').input_value();expect(field(page,0,'F01')).to_be_disabled()
    page.evaluate('async()=>{window.__oldNativeInput.value="Must not overwrite";await window.__oldNativeInput.oninput();}')
    expect(page.locator('#cam')).to_have_value(expected);assert not page.evaluate('NXClientBoot.status().failed') and store.audit_entries()==before
    if mode!='invalid':
        page.locator('#fields-refresh').click();expect(field(page,0,'F01')).to_be_editable();field(page,0,'F01').fill('Explicit current row edit')
        after=cam(page);assert after['records'][0]['values']['F01']=='Explicit current row edit'
        assert after['records'][1]['values']['F01']==('A0_F01' if mode=='reorder' else 'A1_F01')


def test_detached_input_from_previous_editor_cannot_change_new_editor(editor):
    from playwright.sync_api import expect
    page,store,*_=editor;before=store.audit_entries()
    field(page,0,'F01').evaluate('(input)=>{window.__detachedNative=input;}')
    page.locator('#editor-new').click();page.locator('#name').fill('New unsubmitted editor')
    page.evaluate('async()=>{window.__detachedNative.value="Old event";await window.__detachedNative.oninput();}')
    expect(page.locator('#cam')).to_have_value('{}');expect(page.locator('#name')).to_have_value('New unsubmitted editor')
    assert store.audit_entries()==before


def test_explicit_group_key_change_includes_hidden_rows_and_only_key_columns(editor):
    from playwright.sync_api import expect
    page,store,_,_,count=editor;before=store.audit_entries();original=cam(page);page.on('dialog',lambda dialog:dialog.accept())
    expect(field(page,0,'LIBRF')).not_to_be_editable();expect(field(page,0,'NAME')).to_be_editable()
    page.locator('#native-field-block').select_option('B:1');page.locator('#native-key-new').fill('NEW_GROUP');page.locator('#native-key-change').click()
    changed=cam(page)
    for index,record in enumerate(changed['records']):
        key='LIBRF' if index<count else 'NAME';assert record['values'][key]=='NEW_GROUP'
        assert {name:value for name,value in record['values'].items() if name!=key}=={name:value for name,value in original['records'][index]['values'].items() if name!=key}
    assert len(changed['records'])==count+2 and store.audit_entries()==before
    expect(page.locator('#camstatus')).to_have_value('draft')


def test_declined_group_key_change_keeps_all_data_and_approval(editor):
    from playwright.sync_api import expect
    page,store,*_=editor;original=page.locator('#cam').input_value();before=store.audit_entries();page.on('dialog',lambda dialog:dialog.dismiss())
    page.locator('#native-key-new').fill('NOT_CONFIRMED');page.locator('#native-key-change').click()
    expect(page.locator('#cam')).to_have_value(original);expect(page.locator('#camstatus')).to_have_value('approved');assert store.audit_entries()==before


def test_new_editor_rejects_wrong_catalog_kind_before_allocating_save_receipt(editor):
    from playwright.sync_api import expect
    page,store,_,resource,*_=editor;before=store.audit_entries();page.locator('#editor-new').click()
    page.locator('#name').fill('Wrong kind must not be saved');page.locator('#kind').select_option('machine')
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}');page.locator('#cam').fill(json.dumps(resource['cam']))
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Datensatztyp und native Bibliothek passen nicht zusammen')
    assert store.audit_entries()==before and page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")') is None


def test_exported_key_becomes_locked_on_refresh_and_dom_bypass_cannot_write(editor):
    from playwright.sync_api import expect
    page,store,target,resource,count=editor;store.import_release('','','Owned export','fixture',parent=target['id'])
    page.locator('#refresh').click();expect(page.locator('#native-key-change')).to_be_disabled();original=page.locator('#cam').input_value();before=store.audit_entries()
    field(page,0,'LIBRF').evaluate('async input=>{input.readOnly=false;input.value="Protected key bypass";await input.oninput();}')
    expect(page.locator('#cam')).to_have_value(original);expect(field(page,0,'LIBRF')).to_have_value('CUSTOM_GROUP');assert store.audit_entries()==before


def test_append_row_uses_loaded_schema_and_exposes_complete_new_row(editor):
    from playwright.sync_api import expect
    page,store,target,_,count=editor;before=store.audit_entries()
    page.locator('#create-release').select_option(target['id']);page.locator('#create-inventory').click()
    expect(page.locator('#create-library option')).to_have_count(2);page.locator('#create-library').select_option('holder-metric');page.locator('#create-schema').click()
    expect(page.locator('#native-add-row')).to_be_enabled();page.locator('#native-block').select_option('B:1')
    page.locator('#native-field-query').fill('F70');page.locator('#native-add-row').click();expect(page.locator('#message')).to_contain_text('Neue Zeile')
    expect(page.locator('#native-field-query')).to_have_value('');expect(field(page,count+2,'NAME')).to_have_value('CUSTOM_GROUP')
    result=cam(page);assert len(result['records'])==count+3 and result['records'][-1]['values']=={'NAME':'CUSTOM_GROUP','SEQ':'','DESCR':''}
    assert store.audit_entries()==before


def test_later_edits_survive_lost_save_recovery_and_editor_remains_pinned(editor):
    from playwright.sync_api import expect
    page,store,_,resource,count=editor;field(page,0,'F01').fill('Submitted');lose(page,'/api/v1/resources/'+resource['id'])
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#native-field-next').click();field(page,count,'DESCR').fill('Later unsaved body');expect(page.locator('#native-key-change')).to_be_disabled()
    page.locator('#save-receipt').click();expect(page.locator('#message')).to_contain_text('Schreibbeleg bestätigt')
    expect(field(page,count,'DESCR')).to_have_value('Later unsaved body');expect(field(page,count,'DESCR')).to_be_editable()
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
    saved=next(item for item in store.resources() if item['id']==resource['id']);assert saved['revision']==3 and saved['cam']['records'][count]['values']['DESCR']=='Later unsaved body'


def test_new_resource_save_rebinds_existing_inputs_without_losing_focus_or_later_edits(editor):
    from playwright.sync_api import expect
    page,store,_,resource,count=editor;page.on('dialog',lambda dialog:dialog.accept());page.locator('#editor-new').click()
    page.locator('#kind').select_option('holder');page.locator('#name').fill('New native editor, later edits preserved')
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
    page.locator('#cam').fill(json.dumps(resource['cam']));page.locator('#fields-refresh').click()
    page.locator('#native-key-new').fill('NEW_NATIVE_GROUP');page.locator('#native-key-change').click()
    hold(page,'/api/v1/resources');track(page,'save');page.locator('#save').click();page.wait_for_function('()=>window.__held')
    page.locator('#native-field-next').click();field(page,count,'DESCR').fill('Later data before commit response')
    page.evaluate('window.__nativeFocused=document.activeElement');release(page)
    expect(page.locator('#message')).to_contain_text('Schreibbeleg bestätigt');expect(field(page,count,'DESCR')).to_be_editable()
    assert page.evaluate('window.__nativeFocused===document.activeElement&&window.__nativeFocused.isConnected')
    field(page,count,'DESCR').fill('Typed after resource ID assignment');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
    saved=next(item for item in store.resources() if item['name']=='New native editor, later edits preserved')
    assert saved['revision']==2 and saved['cam']['records'][count]['values']['DESCR']=='Typed after resource ID assignment'


@pytest.mark.parametrize('editor',[998],indirect=True)
def test_one_thousand_row_editor_bounds_dom_without_truncating_data(editor):
    from playwright.sync_api import expect
    page,store,_,_,count=editor;before=store.audit_entries();expect(page.locator('#native-fields input')).to_have_count(500)
    page.locator('#native-field-row').fill('1000');page.locator('#native-field-jump').click();expect(field(page,999,'DESCR')).to_have_value('Body B one')
    field(page,999,'DESCR').fill('Last original row');page.locator('#native-field-row').fill('1');page.locator('#native-field-jump').click()
    assert len(cam(page)['records'])==1000 and cam(page)['records'][999]['values']['DESCR']=='Last original row'
    expect(page.locator('#native-fields input')).to_have_count(500);assert store.audit_entries()==before
