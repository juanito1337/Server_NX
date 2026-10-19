"""Explicit row removal edits only a disposable native group, not NX files."""
import copy
import json
import os
from pathlib import Path

import pytest

from test_native_editor_browser import editor,field,cam
from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated native row editing.')


def remove(page,index):return page.locator('[data-native-remove="'+str(index)+'"]')


def load_creation(page,target):
    from playwright.sync_api import expect
    page.locator('#create-release').select_option(target['id']);page.locator('#create-inventory').click()
    expect(page.locator('#create-library option')).to_have_count(2)
    page.locator('#create-library').select_option('holder-metric');page.locator('#create-schema').click()
    expect(page.locator('#native-add-row')).to_be_enabled()


def test_remove_exact_filtered_row_preserves_all_other_values_and_only_saves_a_draft(editor):
    from playwright.sync_api import expect
    page,store,target,resource,count=editor;original=cam(page);before=store.audit_entries();manifest=store.manifest(Path(target['folder']))
    writes=[];page.on('request',lambda request:writes.append(request.url) if request.method not in ('GET','HEAD') else None)
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#native-field-block').select_option('B:1');page.locator('#native-field-query').fill('no_visible_columns')
    remove(page,count).click();expect(page.locator('#message')).to_contain_text('nur im Editor entfernt')
    changed=cam(page);expected=copy.deepcopy(original);del expected['records'][count]
    assert changed==expected and changed['records'][-1]['values']['SEQ']=='1'
    expect(page.locator('#camstatus')).to_have_value('draft');expect(page.locator('#geometry-status')).to_have_value('needs_preparation')
    assert writes==[] and store.audit_entries()==before and store.manifest(Path(target['folder']))==manifest
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
    saved=next(value for value in store.resources() if value['id']==resource['id'])
    assert saved['cam']==expected and saved['revision']==2 and saved['cam_status']=='draft'
    assert json.loads(store.history(resource['id'])[0]['payload'])['cam']==original
    assert store.manifest(Path(target['folder']))==manifest and store.active()['release_id'] is None


def test_declined_row_removal_keeps_every_value_and_existing_checks(editor):
    from playwright.sync_api import expect
    page,store,*_=editor;original=page.locator('#cam').input_value();before=store.audit_entries()
    page.on('dialog',lambda dialog:dialog.dismiss());remove(page,0).click()
    expect(page.locator('#cam')).to_have_value(original);expect(page.locator('#camstatus')).to_have_value('approved')
    expect(page.locator('#geometry-status')).to_have_value('validated');assert store.audit_entries()==before


@pytest.mark.parametrize('editor',[1],indirect=True)
def test_last_row_cannot_be_removed_even_by_a_saved_old_handler(editor):
    from playwright.sync_api import expect
    page,store,_,resource,_=editor;original=cam(page);before=store.audit_entries();page.on('dialog',lambda dialog:dialog.accept())
    remove(page,1).click();remove(page,1).click()
    expect(remove(page,0)).to_be_disabled();expect(page.locator('#native-row-policy')).to_contain_text('letzte Objektzeile')
    # Keep unused FORMAT definitions; do not silently alter the declared schema.
    assert cam(page)=={**original,'records':[original['records'][0]]}
    remove(page,0).evaluate('(button)=>{window.__lastRowRemove=button.onclick;}')
    page.evaluate('async()=>{await window.__lastRowRemove();}')
    assert len(cam(page)['records'])==1 and store.audit_entries()==before
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
    assert next(value for value in store.resources() if value['id']==resource['id'])['cam']['schemas']==original['schemas']


@pytest.mark.parametrize('mode',['invalid','reorder','aba','new_editor','logout','rerender'])
def test_detached_remove_handler_never_changes_a_new_raw_or_editor_context(editor,mode):
    from playwright.sync_api import expect
    page,store,*_=editor;original=page.locator('#cam').input_value();before=store.audit_entries()
    remove(page,0).evaluate('(button)=>{window.__oldRowRemove=button.onclick;}');page.on('dialog',lambda dialog:dialog.accept())
    if mode=='new_editor':page.locator('#editor-new').click()
    elif mode=='logout':page.locator('#disconnect').click()
    elif mode=='rerender':
        page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
        page.locator('#fields-refresh').click()
    else:
        changed=cam(page);changed['records'][0],changed['records'][1]=changed['records'][1],changed['records'][0]
        page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
        page.locator('#cam').fill('{' if mode=='invalid' else json.dumps(changed))
        if mode=='aba':page.locator('#cam').fill(original)
    expected=page.locator('#cam').input_value();page.evaluate('async()=>{await window.__oldRowRemove();}')
    expect(page.locator('#cam')).to_have_value(expected);assert store.audit_entries()==before
    if mode in ('new_editor','logout'):expect(page.locator('#native-row-policy')).to_be_hidden()


def test_double_invocation_of_old_remove_handler_cannot_remove_the_following_row(editor):
    page,store,*_=editor;original=cam(page);before=store.audit_entries();dialogs=[]
    page.on('dialog',lambda dialog:(dialogs.append(dialog.message),dialog.accept()))
    remove(page,0).evaluate('(button)=>{window.__onceOnlyRemove=button.onclick;}')
    page.evaluate('async()=>{await window.__onceOnlyRemove();await window.__onceOnlyRemove();}')
    assert cam(page)=={**original,'records':original['records'][1:]} and len(dialogs)==1 and store.audit_entries()==before


@pytest.mark.parametrize('lost_reply',[False,True])
def test_pending_save_blocks_row_structure_even_with_dom_bypass(editor,lost_reply):
    from playwright.sync_api import expect
    page,store,target,resource,*_=editor;load_creation(page,target);field(page,0,'F01').fill('Submitted first')
    path='/api/v1/resources/'+resource['id']
    if lost_reply:lose(page,path)
    else:hold(page,path);track(page,'save')
    page.locator('#save').click()
    if lost_reply:expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    else:page.wait_for_function('()=>window.__held')
    before=cam(page);expect(remove(page,0)).to_be_disabled()
    remove(page,0).evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    expect(page.locator('#native-add-row')).to_be_disabled()
    page.locator('#native-add-row').evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    assert cam(page)==before
    # Later value edits are still allowed, unlike destructive structure edits.
    field(page,0,'F01').fill('Later unsaved value')
    if lost_reply:page.locator('#save-receipt').click()
    else:release(page)
    expect(page.locator('#message')).to_contain_text('Schreibbeleg bestätigt');expect(remove(page,0)).to_be_enabled()
    assert cam(page)['records'][0]['values']['F01']=='Later unsaved value'
    assert next(value for value in store.resources() if value['id']==resource['id'])['cam']['records'][0]['values']['F01']=='Submitted first'


@pytest.mark.parametrize('lock',['standard','model'])
def test_standard_and_model_bound_groups_need_an_explicit_customer_or_detach_workflow(editor,lock):
    from playwright.sync_api import expect
    page,store,target,*_=editor;load_creation(page,target);before=store.audit_entries();original=page.locator('#cam').input_value()
    page.evaluate("""lock=>{
        if(lock==='standard')editing={...editing,origin:{editable:false}};
        NXNative.edit(lock==='model'?{...editing,model_package:{result_id:'synthetic-only',root_part:'part.prt'}}:editing);
    }""",lock)
    expect(remove(page,0)).to_be_disabled()
    expect(page.locator('#native-row-policy')).to_contain_text('Siemens-Standard' if lock=='standard' else 'Paketbindung')
    remove(page,0).evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    expect(page.locator('#native-add-row')).to_be_disabled()
    page.locator('#native-add-row').evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    expect(page.locator('#cam')).to_have_value(original);assert store.audit_entries()==before


def test_row_removal_in_a_retired_editor_does_not_reactivate_it(editor):
    from playwright.sync_api import expect
    page,store,*_=editor;before=store.audit_entries();page.locator('#camstatus').select_option('retired')
    page.on('dialog',lambda dialog:dialog.accept());remove(page,0).click()
    expect(page.locator('#camstatus')).to_have_value('retired');assert store.audit_entries()==before


def test_removing_last_wide_row_rebuilds_column_navigation_before_reading_shifted_indices(editor):
    from playwright.sync_api import expect
    page,store,_,_,count=editor;original=cam(page);before=store.audit_entries();page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#native-field-block').select_option('B:1');remove(page,count).click();remove(page,count).click()
    expect(remove(page,count-1)).to_be_enabled();remove(page,count-1).click()
    expect(page.locator('#message')).to_contain_text('nur im Editor entfernt')
    assert cam(page)=={**original,'records':original['records'][:count-1]} and store.audit_entries()==before
    # Remaining last wide row still has correct, usable column buttons.
    page.locator('[data-native-column-next="'+str(count-2)+'"]').click()
    expect(field(page,count-2,'F70')).to_have_value('A'+str(count-2)+'_F70')


def test_pending_attachment_upload_blocks_row_removal_until_receipt_is_resolved(editor):
    from playwright.sync_api import expect
    page,store,*_=editor;original=cam(page)
    page.locator('#assetfile').set_input_files({'name':'synthetic.prt','mimeType':'application/octet-stream','buffer':b'Synthetic bytes only; not NX geometry.'})
    lose(page,'/api/v1/assets');page.locator('#upload').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost');expect(remove(page,0)).to_be_disabled()
    remove(page,0).evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    assert cam(page)==original
    page.locator('#upload-receipt').click();expect(remove(page,0)).to_be_enabled()


@pytest.mark.parametrize('change',['raw_aba','new_editor'])
def test_removal_rechecks_editor_after_confirmation_before_changing_any_row(editor,change):
    from playwright.sync_api import expect
    page,store,*_=editor;before=store.audit_entries();original=page.locator('#cam').input_value()
    page.evaluate("""change=>{window.confirm=()=>{
        if(change==='raw_aba'){
            const raw=document.getElementById('cam'),original=raw.value;
            raw.value='{}';raw.dispatchEvent(new Event('input'));
            raw.value=original;raw.dispatchEvent(new Event('input'));
        }else document.getElementById('editor-new').click();
        return true;
    };}""",change)
    remove(page,0).click()
    expect(page.locator('#cam')).to_have_value(original if change=='raw_aba' else '{}')
    assert store.audit_entries()==before


@pytest.mark.parametrize('editor',[1],indirect=True)
def test_row_actions_fit_a_mobile_view_and_removing_last_page_keeps_navigation_valid(editor):
    from playwright.sync_api import expect
    page,store,*_=editor;before=store.audit_entries();page.set_viewport_size({'width':390,'height':844})
    page.on('dialog',lambda dialog:dialog.accept());remove(page,2).click();expect(remove(page,1)).to_be_enabled()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    expect(page.locator('#native-field-next')).to_be_disabled();assert len(cam(page)['records'])==2
    assert store.audit_entries()==before
