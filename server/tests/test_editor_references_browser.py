"""Editor reference lookup/races on own synthetic servers, no NX/CAD runs."""
import json
import os

import pytest

from nxserver.libraries import SPECS,NativeTable,native_cam
from nxserver.store import canonical
from test_filepackages import packages
from test_filepackages_browser import package_ui
from test_libraries import write
from test_references import table
from test_requests_browser import hold,track,release as release_reply

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated editor reference Edge tests.')


@pytest.fixture
def reference_ui(package_ui):
    from playwright.sync_api import expect
    page,store,previous,source=package_ui
    write(source,SPECS['tool_material']['path'],table(['LIBRF','DESCR'],[('MAT_OK','Known active material'),('__proto__','Reserved key')])+ '#DATA | INACTIVE | Comment only\n')
    write(source,SPECS['holder-metric']['path'],table(['LIBRF','DESCR'],[(f'HOLD_{index:04}',f'Holder {index}') for index in range(205)]))
    text=table(['LIBRF','MATREF','HLDREF','DESCR'],[('CUSTOM','MISSING','HOLD_0000','First section'),('CUSTOM','MAT_OK','HOLD_0001','Second section')])
    write(source,SPECS['tool-metric']['path'],text)
    target=store.import_release('test','2512','Reference editor target','fixture');parsed=NativeTable(text)
    resource=store.save_resource({'kind':'tool','name':'Reference tool','cam':native_cam('tool-metric','2406',parsed,parsed.records),
        'cam_status':'draft','asset_ids':[],'erp':None},'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#editor-reference-box summary').click();page.locator('#editor-ref-target').select_option(target['id'])
    yield page,store,target,resource


def inspect(page):
    from playwright.sync_api import expect
    page.locator('#editor-ref-preview').click();expect(page.locator('#editor-ref-rows tr')).to_have_count(4)


def choose(page,field='MATREF'):
    from playwright.sync_api import expect
    page.locator('#editor-ref-rows tr').filter(has_text='Zeile 1 · '+field).get_by_role('button',name='Zielobjekt auswählen').click()
    expect(page.locator('#editor-ref-options-state')).to_contain_text('aktive Objekte')


def test_reference_applies_exactly_one_editor_row_and_saves_only_on_explicit_save(reference_ui):
    from playwright.sync_api import expect
    page,store,target,resource=reference_ui;before=store.manifest(store.release_folder(target['id']));audits=store.audit_entries()
    page.locator('#camstatus').select_option('approved');inspect(page);choose(page)
    expect(page.locator('#editor-ref-key')).to_have_value('');expect(page.locator('#editor-ref-apply')).to_be_disabled()
    assert not page.locator('#editor-ref-key option[value="INACTIVE"]').count()
    page.locator('#editor-ref-key').select_option('__proto__');page.locator('#editor-ref-apply').click()
    expect(page.locator('#message')).to_contain_text('Verweis nur in diese Editorzeile übernommen')
    cam=json.loads(page.locator('#cam').input_value());assert cam['records'][0]['values']['MATREF']=='__proto__'
    assert cam['records'][1]['values']['MATREF']=='MAT_OK' and cam['nx_version']=='2406'
    expect(page.locator('#camstatus')).to_have_value('draft');expect(page.locator('#geometry-status')).to_have_value('needs_preparation')
    assert store.resources()[0]['revision']==1 and store.audit_entries()==audits
    assert page.evaluate('()=>!Object.hasOwn(Object.prototype,"MATREF")')
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Schreibbeleg bestätigt: '+resource['id']+' / Rev 2')
    assert store.resources()[0]['revision']==2 and store.resources()[0]['cam']['records'][0]['values']['MATREF']=='__proto__'
    assert store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None


def test_reference_options_page_and_search_do_not_autoselect_or_change_editor(reference_ui):
    from playwright.sync_api import expect
    page,store,target,resource=reference_ui;before=page.locator('#cam').input_value();inspect(page);choose(page,'HLDREF')
    expect(page.locator('#editor-ref-key option')).to_have_count(101)
    page.locator('#editor-ref-key').select_option('HOLD_0002');expect(page.locator('#editor-ref-apply')).to_be_enabled()
    page.locator('#editor-ref-options-next').click();expect(page.locator('#editor-ref-options-state')).to_contain_text('101–200')
    expect(page.locator('#editor-ref-key')).to_have_value('');expect(page.locator('#editor-ref-apply')).to_be_disabled()
    page.locator('#editor-ref-query').fill('Holder 204');page.locator('#editor-ref-search').click()
    expect(page.locator('#editor-ref-key option')).to_have_count(2)
    expect(page.locator('#editor-ref-key option[value="HOLD_0204"]')).to_have_count(1)
    expect(page.locator('#editor-ref-key')).to_have_value('');expect(page.locator('#cam')).to_have_value(before)
    assert store.resources()[0]['revision']==1


@pytest.mark.parametrize('old_error',[False,True])
def test_editor_change_even_back_to_same_values_discards_pending_preview(reference_ui,old_error):
    from playwright.sync_api import expect
    page,store,target,resource=reference_ui
    hold(page,'/api/v1/editor-references/preview',error=old_error);track(page,'editor-ref-preview')
    page.locator('#editor-ref-preview').click();page.wait_for_function('()=>window.__held')
    value=page.locator('#native-fields input').nth(3).input_value()
    page.locator('#native-fields input').nth(3).fill('Changed temporarily');page.locator('#native-fields input').nth(3).fill(value)
    release_reply(page);expect(page.locator('#editor-ref-rows tr')).to_have_count(0)
    expect(page.locator('#editor-ref-state')).to_contain_text('Verweise neu prüfen')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.resources()[0]['revision']==1


def test_old_search_cannot_unblock_or_replace_new_search(reference_ui):
    from playwright.sync_api import expect
    page,store,target,resource=reference_ui;inspect(page);choose(page,'HLDREF')
    suffix='/reference-options?library_id=tool-metric&field=HLDREF&query=Holder+1&offset=0&limit=100'
    hold(page,'/api/v1/releases/'+target['id']+suffix);track(page,'editor-ref-search')
    page.locator('#editor-ref-query').fill('Holder 1');page.locator('#editor-ref-search').click();page.wait_for_function('()=>window.__held')
    page.locator('#editor-ref-query').fill('Holder 204');page.locator('#editor-ref-search').click()
    expect(page.locator('#editor-ref-key option[value="HOLD_0204"]')).to_have_count(1)
    release_reply(page);expect(page.locator('#editor-ref-key option')).to_have_count(2)
    expect(page.locator('#editor-ref-key')).to_have_value('');expect(page.locator('#editor-ref-apply')).to_be_disabled()
    assert store.resources()[0]['revision']==1


def test_delayed_editor_apply_cannot_overwrite_reopened_same_resource(reference_ui):
    from playwright.sync_api import expect
    page,store,target,resource=reference_ui;inspect(page);choose(page)
    page.locator('#editor-ref-key').select_option('MAT_OK')
    hold(page,'/api/v1/editor-references/apply');track(page,'editor-ref-apply')
    page.locator('#editor-ref-apply').click();page.wait_for_function('()=>window.__held')
    page.locator('#tab-manage').click();page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#name').fill('New editor state must remain');release_reply(page)
    expect(page.locator('#name')).to_have_value('New editor state must remain')
    assert json.loads(page.locator('#cam').input_value())['records'][0]['values']['MATREF']=='MISSING'
    assert store.resources()[0]['revision']==1


def test_changed_target_snapshot_requires_new_preview_not_silent_candidates(reference_ui):
    from playwright.sync_api import expect
    page,store,target,resource=reference_ui;inspect(page)
    relative=SPECS['tool_material']['path'];write(store.release_folder(target['id']),relative,table(['LIBRF','DESCR'],[('NEW','New active key')]))
    # Only the owned fixture updates its manifest, simulating a new source pin.
    with store.db() as db:db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical(store.manifest(store.release_folder(target['id']))),target['id']))
    page.locator('#editor-ref-rows tr').filter(has_text='Zeile 1 · MATREF').get_by_role('button',name='Zielobjekt auswählen').click()
    expect(page.locator('#message')).to_contain_text('Zielbibliothek geändert')
    expect(page.locator('#editor-ref-apply')).to_be_disabled();expect(page.locator('#editor-ref-rows tr')).to_have_count(0)
    assert store.resources()[0]['revision']==1


def test_incomplete_cam_json_shows_error_and_keeps_editor_without_browser_exception(reference_ui):
    from playwright.sync_api import expect
    page,store,target,resource=reference_ui
    page.locator('#editor-section details.technical summary').first.click()
    page.locator('#cam').fill('{');page.locator('#editor-ref-preview').click()
    expect(page.locator('#message')).to_have_class('error');expect(page.locator('#message')).to_contain_text('JSON')
    expect(page.locator('#cam')).to_have_value('{');expect(page.locator('#editor-ref-rows tr')).to_have_count(0)
    assert store.resources()[0]['revision']==1
