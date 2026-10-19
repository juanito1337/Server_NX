"""Whole-group templates and preserved editors on isolated fixture servers."""
import json
import os

import pytest

from nxserver.libraries import SPECS
from test_filepackages import packages
from test_filepackages_browser import package_ui
from test_libraries import write
from test_references import table
from test_requests_browser import hold,track,release as release_reply
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated native cloning Edge tests.')


@pytest.fixture
def clone_ui(package_ui,request):
    from playwright.sync_api import expect
    page,store,previous,source=package_ui
    config=getattr(request,'param',{});key_field=config.get('key_field','LIBRF');count=config.get('rows',2)
    rows=[('STD',str(index+1),str(index+1),'First template section' if index==0 else 'Second section' if index==1 else 'Section '+str(index+1)) for index in range(count)]
    text=table([key_field,'RTYPE','SEQ','DESCR'],[*rows,('TAKEN','1','1','Other object')])
    write(source,SPECS['holder-metric']['path'],text)
    target=store.import_release('test','2512','Whole group cloning','fixture')
    page.locator('#refresh').click();expect(page.locator('#library-release option[value="'+target['id']+'"]')).to_have_count(1)
    page.locator('#library-release').select_option(target['id']);page.locator('#library-inventory').click()
    expect(page.locator('#native-library option[value="holder-metric"]')).to_have_count(1)
    page.locator('#native-library').select_option('holder-metric');page.locator('#library-read').click()
    expect(page.locator('#native-objects tr')).to_have_count(2)
    yield page,store,target


def open_clone(page,key='NEW_CUSTOM',name='New copied customer'):
    from playwright.sync_api import expect
    page.locator('#native-objects tr').filter(has_text='STD').get_by_role('button',name='Neuen Entwurf ableiten').click()
    expect(page.locator('#clone-box')).to_be_visible();page.locator('#clone-key').fill(key);page.locator('#clone-name').fill(name)


def preview(page):
    from playwright.sync_api import expect
    page.locator('#clone-preview').click();expect(page.locator('#clone-apply')).to_be_enabled()


def test_new_complete_customer_group_prepares_editor_then_explicit_save(clone_ui):
    from playwright.sync_api import expect
    page,store,target=clone_ui;before=store.manifest(store.release_folder(target['id']));audits=store.audit_entries()
    page.locator('#tab-prepare').click();page.locator('#name').fill('Previous unsaved draft')
    page.locator('#tab-manage').click();open_clone(page);expect(page.locator('#name')).to_have_value('Previous unsaved draft')
    preview(page);expect(page.locator('#name')).to_have_value('Previous unsaved draft')
    assert store.resources()==[] and store.audit_entries()==audits
    page.locator('#clone-apply').click();expect(page.locator('#message')).to_contain_text('Neue Bibliotheksgruppe nur im Editor vorbereitet')
    expect(page.locator('#name')).to_have_value('New copied customer');expect(page.locator('#kind')).to_have_value('holder')
    cam=json.loads(page.locator('#cam').input_value());assert len(cam['records'])==2
    assert all(row['values']['LIBRF']=='NEW_CUSTOM' for row in cam['records'])
    assert cam['records'][0]['values']['DESCR']=='First template section'
    expect(page.locator('#camstatus')).to_have_value('draft');expect(page.locator('#assetids')).to_have_value('[]')
    assert store.resources()==[] and store.audit_entries()==audits
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('/ Rev 1')
    assert len(store.resources())==1 and store.resources()[0]['cam_status']=='draft' and store.resources()[0]['origin'] is None
    assert store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None


def test_colliding_key_is_visible_and_original_editor_is_not_changed(clone_ui):
    from playwright.sync_api import expect
    page,store,target=clone_ui;open_clone(page,key='TAKEN')
    page.locator('#clone-preview').click();expect(page.locator('#clone-state')).to_contain_text('already exists')
    expect(page.locator('#clone-apply')).to_be_disabled();assert store.resources()==[]
    page.locator('#clone-key').fill('STD');page.locator('#clone-preview').click()
    expect(page.locator('#message')).to_contain_text('different, new library identifier')
    expect(page.locator('#clone-apply')).to_be_disabled()


@pytest.mark.parametrize('old_error',[False,True])
def test_changed_clone_intent_even_away_and_back_discards_old_preview(clone_ui,old_error):
    from playwright.sync_api import expect
    page,store,target=clone_ui;open_clone(page)
    hold(page,'/api/v1/releases/'+target['id']+'/library-clone/preview',error=old_error);track(page,'clone-preview')
    page.locator('#clone-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#clone-key').fill('OTHER');page.locator('#clone-key').fill('NEW_CUSTOM');release_reply(page)
    expect(page.locator('#clone-apply')).to_be_disabled();expect(page.locator('#clone-state')).to_contain_text('erneut prüfen')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.resources()==[]


def test_delayed_apply_does_not_replace_later_editor_fields(clone_ui):
    from playwright.sync_api import expect
    page,store,target=clone_ui;open_clone(page);preview(page)
    hold(page,'/api/v1/releases/'+target['id']+'/library-clone/apply');track(page,'clone-apply')
    page.locator('#clone-apply').click();page.wait_for_function('()=>window.__held')
    page.locator('#name').fill('Later editor name');page.locator('#editor-section details.technical summary').first.click()
    page.locator('#cam').fill('{');release_reply(page)
    expect(page.locator('#name')).to_have_value('Later editor name');expect(page.locator('#cam')).to_have_value('{')
    assert store.resources()==[] and store.active()['release_id'] is None


def test_pending_original_save_is_not_abandoned_by_new_template(clone_ui):
    from playwright.sync_api import expect
    page,store,target=clone_ui;open_clone(page);preview(page);page.locator('#clone-apply').click()
    expect(page.locator('#message')).to_contain_text('Neue Bibliotheksgruppe nur im Editor vorbereitet')
    lose(page,'/api/v1/resources');page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#tab-manage').click();open_clone(page,key='SECOND');preview(page)
    page.locator('#clone-apply').click();expect(page.locator('#message')).to_contain_text('Speichervorgang/Upload zuerst klären')
    assert len(store.resources())==1


def test_disconnect_discards_clone_context_and_late_apply(clone_ui):
    from playwright.sync_api import expect
    page,store,target=clone_ui;open_clone(page);preview(page)
    hold(page,'/api/v1/releases/'+target['id']+'/library-clone/apply');track(page,'clone-apply')
    page.locator('#clone-apply').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();release_reply(page)
    expect(page.locator('#clone-box')).to_be_hidden();expect(page.locator('#name')).to_have_value('')
    assert store.resources()==[]
