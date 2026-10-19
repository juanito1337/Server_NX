"""Schema/source-bound catalog preview in disposable Edge/server fixtures."""
import json
import os

import pytest

from nxserver.libraries import SPECS,validate_catalog_cam
from test_catalog_selection_browser import selection_ui,read,select
from test_catalog_selection import database
from test_filepackages_browser import package_ui
from test_filepackages import packages
from test_libraries import write
from test_requests_browser import hold,track,release as release_reply

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge catalog contract tests.')


def preview(page):
    from playwright.sync_api import expect
    page.locator('#catalog-preview').click();expect(page.locator('#catalog-import')).to_be_enabled()


def test_whole_library_gui_displays_full_group_and_future_columns_without_writes(selection_ui):
    from playwright.sync_api import expect
    page,store,target,previous,source=selection_ui
    text=database(['STD','A','B']).replace('SEQ DESCR','SEQ DESCR FUTURE_2512 __proto__').replace('Root STD','Root STD|rootfuture|literal').replace('Child STD','Child STD|childfuture|literal').replace('Root A','Root A|rootfuture|literal').replace('Child A','Child A|childfuture|literal').replace('Root B','Root B|rootfuture|literal').replace('Child B','Child B|childfuture|literal')
    write(source,SPECS['tool-metric']['path'],text)
    newer=store.import_release('test','2512','Synthetic future format','fixture');before=store.manifest(store.release_folder(newer['id']));audit=store.audit_entries()
    page.locator('#new').click();page.locator('#name').fill('Unsaved title survives schema viewing')
    page.locator('#tab-manage').click();page.locator('#refresh').click();expect(page.locator('#library-release option[value="'+newer['id']+'"]')).to_have_count(1)
    page.locator('#library-release').select_option(newer['id']);page.locator('#library-inventory').click();expect(page.locator('#catalog-libraries input')).to_have_count(2)
    page.locator('#catalog-scope').select_option('libraries');page.locator('#catalog-libraries input[value="holder-metric"]').uncheck();preview(page)
    expect(page.locator('#catalog-objects > tr')).to_have_count(3)  # STD now differs, but no overwrite/freigabe.
    expect(page.locator('#catalog-source-state')).to_contain_text('auch bei ganzen Bibliotheken')
    row=page.locator('#catalog-objects > tr').filter(has_text='tool-metric / A')
    expect(row).to_contain_text('2 Zeilen · 1 FORMAT-Blöcke');row.locator('summary').click()
    expect(row.locator('.catalog-formats')).to_contain_text('Nur Quelle: FUTURE_2512, __proto__')
    row.get_by_role('button',name='Metadaten ansehen').click();details=json.loads(page.locator('#details').inner_text())
    assert details['record_count']==2 and details['format_blocks'][0]['source_only_fields']==['FUTURE_2512','__proto__']
    assert details['nx_files_changed'] is details['automatic_save'] is details['automatic_approval'] is False
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved title survives schema viewing')
    assert store.manifest(store.release_folder(newer['id']))==before and store.audit_entries()==audit and not store.resources()


@pytest.mark.parametrize('malformed',['missing_gate','source_hash','catalog_hash','wrong_total','wrong_kind','bad_last_block','partial_columns','wrong_selection','out_of_scope'])
def test_entire_preview_contract_is_validated_before_rendering_or_enabling_import(selection_ui,malformed):
    from playwright.sync_api import expect
    page,store,*_=selection_ui;read(page,'tool-metric');select(page,'tool-metric','A');select(page,'tool-metric','B');audit=store.audit_entries()
    def broken(route):
        response=route.fetch();value=response.json()
        if malformed=='missing_gate':value.pop('commit_contract_version')
        elif malformed=='source_hash':value['source_inputs']['tool-metric']='wrong'
        elif malformed=='catalog_hash':value['catalog_state_sha256']='wrong'
        elif malformed=='wrong_total':value['count']+=1
        elif malformed=='wrong_kind':value['objects'][-1]['kind']='machine'
        elif malformed=='bad_last_block':value['objects'][-1]['format_blocks'][0]['record_count']+=1
        elif malformed=='partial_columns':value['objects'][-1]['format_blocks'][0]['fields']=[]
        elif malformed=='wrong_selection':value['selection']['tool-metric']=['A']
        else:value['objects'][-1]['key']='not-selected'
        route.fulfill(response=response,json=value)
    pattern='**/api/v1/releases/*/catalog-preview';page.route(pattern,broken)
    page.locator('#catalog-preview').click();expect(page.locator('#message')).to_have_class('error')
    expect(page.locator('#catalog-objects > tr')).to_have_count(0);expect(page.locator('#catalog-import')).to_be_disabled();expect(page.locator('#catalog-source-state')).to_have_text('')
    assert store.audit_entries()==audit and not store.resources()
    page.unroute(pattern,broken);preview(page);expect(page.locator('#catalog-objects > tr')).to_have_count(2)


@pytest.mark.parametrize('old_error',[False,True])
def test_old_whole_library_preview_cannot_replace_selected_group_contract(selection_ui,old_error):
    from playwright.sync_api import expect
    page,store,*_=selection_ui;read(page,'tool-metric');select(page,'tool-metric','A');audit=store.audit_entries()
    page.locator('#catalog-scope').select_option('libraries');page.locator('#catalog-libraries input[value="holder-metric"]').uncheck()
    hold(page,'/catalog-preview',old_error);track(page,'catalog-preview');page.locator('#catalog-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#catalog-scope').select_option('objects');preview(page)
    expect(page.locator('#catalog-objects > tr')).to_have_count(1);expect(page.locator('#catalog-state')).to_contain_text('Nur ausdrücklich ausgewählte Objekte')
    release_reply(page);expect(page.locator('#catalog-objects > tr')).to_have_count(1);expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.audit_entries()==audit and not store.resources()


def test_whole_library_adoption_imports_complete_rows_then_repeats_without_duplicate_owners(selection_ui):
    from playwright.sync_api import expect
    page,store,target,*_=selection_ui;audit=store.audit_entries();before=store.manifest(store.release_folder(target['id']))
    page.locator('#catalog-scope').select_option('libraries');page.locator('#catalog-libraries input[value="holder-metric"]').uncheck();preview(page)
    expect(page.locator('#catalog-objects > tr')).to_have_count(3);page.on('dialog',lambda dialog:dialog.accept());page.locator('#catalog-import').click()
    expect(page.locator('#message')).to_contain_text('Stammdaten übernommen: 3',timeout=15000)
    assert len(store.resources())==3
    for resource in store.resources():validate_catalog_cam(resource);assert len(resource['cam']['records'])==2 and resource['cam_status']=='draft'
    current_audit=store.audit_entries();preview_count=3
    page.locator('#catalog-preview').click();expect(page.locator('#catalog-state')).to_contain_text('3 bereits verwaltet')
    expect(page.locator('#catalog-import')).to_be_disabled();expect(page.locator('#catalog-objects > tr')).to_have_count(preview_count)
    assert store.audit_entries()==current_audit and store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None
