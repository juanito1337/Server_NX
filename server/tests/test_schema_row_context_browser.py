"""Schema worklist row ownership on disposable Edge/server/CAM fixtures."""
import os

import pytest

from nxserver.integrity import snapshot
from test_requests_browser import ui,hold,track
from test_schema_editor_browser import schema
from test_schemaimpact_browser import show,read

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated schema worklist row context.')


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('change',['filtered','hidden','filter_aba','reread','collapsed','target_aba','logout'])
def test_old_worklist_action_cannot_replace_a_newer_unsaved_editor(schema,change):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema
    show(page,target);read(page)
    page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen').evaluate('(node)=>window.__oldSchemaRow=node')
    page.locator('#tab-prepare').click();page.locator('#name').fill('Keep newer unsaved schema draft')
    if change!='hidden':
        page.locator('#tab-manage').click()
        if change in ('filtered','filter_aba'):
            page.locator('#schema-impact-query').fill('no synthetic match');read(page)
            expect(page.locator('#schema-impact-items tr')).to_have_count(0)
            if change=='filter_aba':page.locator('#schema-impact-query').fill('');read(page)
        elif change=='reread':read(page)
        elif change=='collapsed':page.locator('#schema-impact-box > summary').click()
        elif change=='target_aba':
            page.locator('#schema-impact-release').select_option('');page.locator('#schema-impact-release').select_option(target['id']);read(page)
        elif change=='logout':page.locator('#disconnect').click()
    before=snapshot(store)['sha256'];name=page.locator('#name').input_value();draft=page.locator('#cam').input_value();workspace=page.evaluate('workspace')
    calls=[];dialogs=[]
    page.on('request',lambda request:calls.append(request.method) if '/api/v1/' in request.url else None)
    def accept(dialog):
        dialogs.append(dialog.message);dialog.accept()
    page.on('dialog',accept)
    page.evaluate('async()=>{await window.__oldSchemaRow.onclick();}')
    expect(page.locator('#name')).to_have_value(name)
    expect(page.locator('#cam')).to_have_value(draft)
    assert page.evaluate('workspace')==workspace
    assert not dialogs and not calls and snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('change',['tab_aba','close','close_aba','close_keyboard','close_keyboard_aba'])
@pytest.mark.parametrize('old_error',[False,True])
def test_late_open_respects_workspace_and_worklist_close_intent(schema,change,old_error):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema
    page.locator('#name').fill('Preserve draft after later navigation')
    show(page,target);read(page);page.on('dialog',lambda dialog:dialog.accept())
    button=page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen')
    button.evaluate('(node)=>node.id="schema-row-test-open"');track(page,'schema-row-test-open')
    hold(page,'/api/v1/resources/'+resource['id'],old_error);button.click();page.wait_for_function('()=>window.__held')
    if change=='tab_aba':page.locator('#tab-prepare').click();page.locator('#tab-manage').click()
    else:
        summary=page.locator('#schema-impact-box > summary')
        if 'keyboard' in change:summary.focus();summary.press('Enter')
        else:summary.click()
        if change.endswith('_aba'):
            if 'keyboard' in change:summary.press('Space')
            else:summary.click()
    page.evaluate('message("Keep newer navigation notice")')
    before=snapshot(store)['sha256'];draft=page.locator('#cam').input_value();workspace=page.evaluate('workspace');calls=[]
    page.on('request',lambda request:calls.append(request.method) if '/api/v1/' in request.url else None)
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#name')).to_have_value('Preserve draft after later navigation')
    expect(page.locator('#cam')).to_have_value(draft);expect(page.locator('#message')).to_have_text('Keep newer navigation notice')
    assert page.evaluate('workspace')==workspace and not calls and snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('change',['workspace','workspace_aba','details_closed','new_details'])
def test_late_worklist_report_does_not_reopen_or_replace_a_newer_shared_detail_view(schema,change):
    from playwright.sync_api import expect
    page,store,target,*_=schema;show(page,target)
    if change=='details_closed':
        page.locator('#details-box > summary').click()
        assert page.locator('#details-box').evaluate('(node)=>node.open')
    hold(page,'/catalog-schema-impact');track(page,'schema-impact-read')
    page.locator('#schema-impact-read').click();page.wait_for_function('()=>window.__held')
    if change in ('workspace','workspace_aba'):
        page.locator('#tab-prepare').click()
        if change=='workspace_aba':page.locator('#tab-manage').click()
    elif change=='details_closed':
        page.locator('#details-box > summary').click()
        assert not page.locator('#details-box').evaluate('(node)=>node.open')
    else:
        page.locator('#openapi').click();expect(page.locator('#details')).to_contain_text('"openapi"')
    raw=page.locator('#details').text_content();opened=page.locator('#details-box').evaluate('(node)=>node.open')
    before=snapshot(store)['sha256'];workspace=page.evaluate('workspace')
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#schema-impact-state')).to_contain_text('Schema-Arbeitsliste geprüft')
    assert page.locator('#details').text_content()==raw
    assert page.locator('#details-box').evaluate('(node)=>node.open')==opened
    assert page.evaluate('workspace')==workspace and snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
def test_abandoned_open_rearms_current_rows_without_old_finally_unlocking_a_new_request(schema):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema;show(page,target);read(page);page.on('dialog',lambda dialog:dialog.accept())
    button=page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen')
    button.evaluate('(node)=>node.id="schema-row-test-open"');track(page,'schema-row-test-open')
    hold(page,'/api/v1/resources/'+resource['id']);button.click();page.wait_for_function('()=>window.__held')
    page.evaluate('window.__releaseOldSchemaOpen=window.__releaseHeld')
    page.locator('#tab-prepare').click();page.locator('#tab-manage').click();expect(button).to_be_enabled()
    hold(page,'/api/v1/resources/'+resource['id']);button.click();page.wait_for_function('()=>window.__held')
    before=snapshot(store)['sha256']
    page.evaluate('window.__releaseOldSchemaOpen()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(button).to_be_disabled();expect(page.locator('#schema-impact-read')).to_be_disabled()
    expect(page.locator('#panel-manage')).to_be_visible()
    page.evaluate('window.__releaseHeld()');expect(page.locator('#panel-prepare')).to_be_visible()
    expect(page.locator('#name')).to_have_value(resource['name']);assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
def test_late_open_cannot_displace_a_real_pending_save(schema):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema;page.locator('#name').fill('Actually submitted newer schema draft')
    show(page,target);read(page);page.on('dialog',lambda dialog:dialog.accept())
    button=page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen')
    button.evaluate('(node)=>node.id="schema-row-test-open"');track(page,'schema-row-test-open')
    hold(page,'/api/v1/resources/'+resource['id']);button.click();page.wait_for_function('()=>window.__held')
    page.evaluate('window.__releaseSchemaOpen=window.__releaseHeld')
    hold(page,'/api/v1/resources/'+resource['id'])
    page.evaluate('()=>{window.__saveAttemptDone=false;void NXSaves.save().finally(()=>{window.__saveAttemptDone=true;});}')
    page.wait_for_function('()=>window.__held&&saveBusy')
    before=snapshot(store)['sha256'];draft=page.locator('#cam').input_value()
    page.evaluate('window.__releaseSchemaOpen()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#name')).to_have_value('Actually submitted newer schema draft')
    expect(page.locator('#cam')).to_have_value(draft);expect(page.locator('#panel-manage')).to_be_visible()
    assert page.evaluate('saveBusy') and snapshot(store)['sha256']==before
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__saveAttemptDone')
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert len(store.history(resource['id']))==2 and store.active()['release_id'] is None
