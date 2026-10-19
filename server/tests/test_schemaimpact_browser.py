"""Schema worklist to existing editor, disposable Edge/server/CAM fixtures only."""
import json
import os

import pytest

from nxserver.integrity import snapshot
from nxserver.libraries import NativeTable,native_cam,SPECS,digest
from test_schema_editor_browser import schema,complete
from test_requests_browser import ui,hold,track,release as release_reply,payload

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated catalog schema worklist.')


def show(page,target):
    page.locator('#tab-manage').click();page.locator('#schema-impact-box').evaluate('(element)=>{element.open=true;}')
    page.locator('#schema-impact-release').select_option(target['id'])


def read(page):
    from playwright.sync_api import expect
    page.locator('#schema-impact-read').click();expect(page.locator('#schema-impact-state')).to_contain_text('Schema-Arbeitsliste geprüft')


def test_worklist_opens_matching_editor_and_only_explicit_save_creates_revision(schema):
    from playwright.sync_api import expect
    page,store,target,resource,count,column=schema;accept=lambda dialog:dialog.accept();page.on('dialog',accept);before=snapshot(store)['sha256'];files=store.manifest(store.release_folder(target['id']))
    show(page,target);read(page);expect(page.locator('#schema-impact-counts')).to_contain_text('Entscheidungen erforderlich: 1')
    row=page.locator('#schema-impact-items tr').filter(has_text=resource['name']);expect(row).to_contain_text(str(count+2)+' neue Werte')
    assert snapshot(store)['sha256']==before
    row.get_by_role('button',name='Im Schemaeditor prüfen').click()
    expect(page.locator('#panel-prepare')).to_be_visible();expect(page.locator('#name')).to_have_value(resource['name'])
    expect(page.locator('#schema-target-release')).to_have_value(target['id']);expect(page.locator('#schema-adaptation')).to_have_attribute('open','')
    assert json.loads(page.locator('#cam').input_value())==resource['cam'] and snapshot(store)['sha256']==before
    page.locator('#schema-preview').click();expect(page.locator('#schema-state')).to_contain_text('offene Entscheidung')
    page.remove_listener('dialog',accept);complete(page,count,column);page.locator('#schema-apply').click()
    expect(page.locator('#message')).to_contain_text('Schema in Editor übernommen');assert snapshot(store)['sha256']==before
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=next(item for item in store.resources() if item['id']==resource['id']);assert saved['revision']==2 and saved['cam_status']=='draft' and saved['cam']['nx_version']=='2512'
    assert store.manifest(store.release_folder(target['id']))==files and store.active()['release_id'] is None
    show(page,target);expect(page.locator('#schema-impact-items tr')).to_have_count(0);read(page)
    expect(page.locator('#schema-impact-items tr').filter(has_text=resource['name'])).to_contain_text('Bereits passend')


def test_list_filter_does_not_overwrite_an_unsaved_editor_and_declined_open_is_noop(schema):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema;page.locator('#name').fill('Unsubmitted editor changes');before=snapshot(store)['sha256']
    show(page,target);page.locator('#schema-impact-filter').select_option('decisions_required');read(page)
    expect(page.locator('#schema-impact-items tr')).to_have_count(1)
    page.on('dialog',lambda dialog:dialog.dismiss());page.locator('#schema-impact-items button').click()
    expect(page.locator('#name')).to_have_value('Unsubmitted editor changes');expect(page.locator('#panel-manage')).to_be_visible()
    page.locator('#schema-impact-query').fill('not-a-match');expect(page.locator('#schema-impact-items tr')).to_have_count(0)
    read(page);expect(page.locator('#schema-impact-page')).to_contain_text('0–0 von 0')
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('fault',['contract','flags','context','catalog','target','report_hash','revision','resource_hash','scope_count','counts','rows','key'])
def test_bad_worklist_never_renders_editor_actions_or_retains_old_details(schema,fault):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema;show(page,target);read(page);before=snapshot(store)['sha256'];original=page.locator('#cam').input_value()
    def invalid(route):
        response=route.fetch();value=response.json();item=next(item for item in value['items'] if item['resource_id']==resource['id'])
        if fault=='contract':value.pop('contract_version')
        elif fault=='flags':value['automatic_save']=True
        elif fault=='context':value['context_sha256']='0'*64
        elif fault=='catalog':value['catalog_sha256']='0'*64
        elif fault=='target':value['target_sha256']='0'*64
        elif fault=='report_hash':value['report_sha256']='0'*64
        elif fault=='revision':item['revision']+=1
        elif fault=='resource_hash':item['resource_sha256']='0'*64
        elif fault=='scope_count':value['scoped_resources']+=1;value['counts']['blocked']+=1;value['filtered']+=1
        elif fault=='counts':value['counts']['decisions_required']-=1;value['counts']['blocked']+=1
        elif fault=='rows':item['rows']+=1
        else:item['key']='Wrong object'
        # Some coordinated mutations retain a valid self-hash. Metadata, actual
        # source records and page/count invariants still must agree.
        if fault in ('scope_count','counts','rows','key'):
            unsigned=dict(value);unsigned.pop('report_sha256');value['report_sha256']=digest(unsigned)
        route.fulfill(response=response,json=value)
    page.route('**/catalog-schema-impact',invalid);page.locator('#schema-impact-read').click()
    expect(page.locator('#schema-impact-state')).to_contain_text('Schema-Arbeitsliste nicht bestätigt')
    expect(page.locator('#schema-impact-items tr')).to_have_count(0);expect(page.locator('#schema-impact-next')).to_be_disabled()
    expect(page.locator('#details')).to_contain_text('not_verified');expect(page.locator('#cam')).to_have_value(original)
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('old_error',[False,True])
def test_worklist_selection_aba_and_refresh_discard_old_replies(schema,old_error):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema;show(page,target);before=snapshot(store)['sha256']
    hold(page,'/catalog-schema-impact',old_error);track(page,'schema-impact-read');page.locator('#schema-impact-read').click();page.wait_for_function('()=>window.__held')
    page.locator('#schema-impact-query').fill('Later');page.locator('#schema-impact-query').fill('')
    release_reply(page);expect(page.locator('#schema-impact-items tr')).to_have_count(0)
    expect(page.locator('#schema-impact-state')).to_contain_text('Auswahl geändert');read(page)
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('change',['resource','target','logout','editor','editor_aba'])
def test_late_editor_open_does_not_overwrite_a_new_context(schema,change):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema;page.on('dialog',lambda dialog:dialog.accept());page.locator('#name').fill('Unsubmitted later editor')
    show(page,target);read(page);hold(page,'/api/v1/resources/'+resource['id']);button=page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen')
    button.click();page.wait_for_function('()=>window.__held')
    if change=='logout':page.locator('#disconnect').click()
    elif change=='target':page.locator('#schema-impact-release').select_option('')
    elif change=='resource':
        changed=payload(resource);changed['name']='Another saved revision';store.save_resource(changed,'fixture',resource['id'],1);page.locator('#refresh').click()
        expect(page.locator('#resources')).to_contain_text(changed['name'])
    else:
        page.locator('#tab-prepare').click();page.locator('#name').fill('Edited while opening')
        if change=='editor_aba':page.locator('#name').fill('Unsubmitted later editor')
        page.locator('#tab-manage').click()
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__decoded')
    if change!='logout':expect(page.locator('#name')).to_have_value('Edited while opening' if change=='editor' else 'Unsubmitted later editor');expect(page.locator('#panel-manage')).to_be_visible()
    assert store.active()['release_id'] is None and len(store.history(resource['id']))==(2 if change=='resource' else 1)


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
def test_paging_and_unicode_search_cover_the_whole_catalog_and_pin_back_to_first_page(schema):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema
    for index in range(52):
        value=payload(resource);value['name']='Straße '+str(index)
        for record in value['cam']['records']:record['values']['LIBRF']='CUSTOM_'+str(index)
        store.save_resource(value,'fixture')
    page.locator('#refresh').click();show(page,target);page.locator('#schema-impact-query').fill('STRASSE');read(page)
    expect(page.locator('#schema-impact-items tr')).to_have_count(50);expect(page.locator('#schema-impact-page')).to_contain_text('1–50 von 52')
    page.locator('#schema-impact-next').click();expect(page.locator('#schema-impact-page')).to_contain_text('51–52 von 52')
    changed=payload(resource);changed['name']='Target independent source changed';store.save_resource(changed,'fixture',resource['id'],1)
    page.locator('#schema-impact-prev').click()
    expect(page.locator('#schema-impact-state')).to_contain_text('context changed')
    expect(page.locator('#schema-impact-items tr')).to_have_count(0)
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(changed['name'])
    read(page);expect(page.locator('#schema-impact-items tr')).to_have_count(50)
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('drift',['resource','same_revision','target'])
def test_fresh_editor_entry_rejects_server_drift_without_replacing_unsaved_values(schema,drift):
    from playwright.sync_api import expect
    from nxserver.store import canonical
    page,store,target,resource,*_=schema;page.on('dialog',lambda dialog:dialog.accept());page.locator('#name').fill('Keep this unsaved editor')
    show(page,target);read(page)
    if drift=='resource':
        changed=payload(resource);changed['name']='Newer current revision';store.save_resource(changed,'fixture',resource['id'],1)
    elif drift=='same_revision':
        changed=payload(resource);changed['name']='Different payload same revision'
        with store.db() as db:db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(changed),resource['id']))
    else:
        with store.db() as db:db.execute("UPDATE releases SET status='validated' WHERE id=?",(target['id'],))
    page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen').click()
    expect(page.locator('#schema-impact-state')).to_contain_text('Schemaeditor nicht vorbereitet')
    expect(page.locator('#name')).to_have_value('Keep this unsaved editor');expect(page.locator('#panel-manage')).to_be_visible()
    expect(page.locator('#schema-impact-items tr')).to_have_count(0)


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('flag',['read_only','automatic_save','target_library_bounded_reader'])
def test_weaker_server_contract_disables_the_new_workflow(schema,flag):
    from playwright.sync_api import expect
    page,store,target,*_=schema;show(page,target);before=snapshot(store)['sha256']
    page.evaluate('''async flag=>{const cap=await api('capabilities');cap.catalog_schema_impact[flag]=!cap.catalog_schema_impact[flag];NXSchemaImpact.configure(cap);NXSchemaImpact.sync();}''',flag)
    show(page,target)
    expect(page.locator('#schema-impact-read')).to_be_disabled();expect(page.locator('#schema-impact-items tr')).to_have_count(0)
    assert snapshot(store)['sha256']==before
