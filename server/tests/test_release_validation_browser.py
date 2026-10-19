"""Readable technical results from actual synthetic server validations only."""
import json
import os

import pytest

from nxserver.libraries import SPECS
from test_requests_browser import ui, hold, track
from test_filepackages_browser import package_ui
from test_filepackages import packages
from test_libraries import write
from test_references import table

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated technical-validation Edge tests.')


def validate(page, target):
    page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Prüfen', exact=True).click()
    from playwright.sync_api import expect
    expect(page.locator('#release-validation-box')).to_be_visible(timeout=15000)


def test_actual_validation_has_readable_library_summary_without_changing_editor_or_files(ui):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    before = store.manifest(store.release_folder(target['id']))
    page.locator('#tab-prepare').click();page.locator('#name').fill('Unsaved validation-view draft')
    page.locator('#editor-section details.technical > summary').click();page.locator('#cam').fill('{')
    page.locator('#tab-manage').click();validate(page, target)
    expect(page.locator('#release-validation-state')).to_contain_text('Technisch ohne blockierende Befunde')
    expect(page.locator('#release-validation-context')).to_contain_text(target['id'])
    expect(page.locator('#release-validation-items')).to_contain_text('tool-metric')
    expect(page.locator('#release-validation-items')).to_contain_text('1 FORMAT-Blöcke')
    raw = json.loads(page.locator('#details').text_content())
    assert raw == store.get_release(target['id'])['validation']
    page.locator('#release-validation-kind').select_option('error')
    expect(page.locator('#release-validation-items tr')).to_have_count(0)
    page.locator('#release-validation-kind').select_option('library')
    expect(page.locator('#release-validation-items tr')).to_have_count(1)
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved validation-view draft')
    expect(page.locator('#cam')).to_have_value('{')
    assert store.manifest(store.release_folder(target['id'])) == before and store.active()['release_id'] is None


def test_missing_relation_is_a_readable_blocker_and_markup_is_only_text(ui):
    from playwright.sync_api import expect
    page, store, _, _ = ui
    value = '<img src=x onerror="window.__validationInjected=true">'
    write(store.sources['test'], SPECS['tool-metric']['path'], table(['LIBRF','MATREF','DESCR'], [('CUSTOM',value,'Synthetic')], 'end_mill'))
    target = store.import_release('test', '2512', 'Blocked synthetic relation', 'fixture')
    before = store.manifest(store.release_folder(target['id']))
    page.locator('#refresh').click();expect(page.locator('#releases')).to_contain_text(target['id'])
    validate(page, target)
    expect(page.locator('#release-validation-state')).to_contain_text('blockierende Befunde')
    page.locator('#release-validation-kind').select_option('error')
    expect(page.locator('#release-validation-items')).to_contain_text('MATREF')
    expect(page.locator('#release-validation-items')).to_contain_text(value)
    assert page.locator('#release-validation-items img').count() == 0
    assert page.evaluate('window.__validationInjected===true') is False
    assert store.get_release(target['id'])['status'] == 'blocked'
    assert store.manifest(store.release_folder(target['id'])) == before and store.active()['release_id'] is None


def test_real_standard_warning_groups_are_paged_filtered_and_not_called_blockers(package_ui):
    from playwright.sync_api import expect
    page, store, _, source = package_ui
    contents = table(['LIBRF','MATREF','DESCR'], [('STD_'+str(i),'MISSING_'+str(i),'Synthetic stock warning') for i in range(75)], 'end_mill')
    write(source, SPECS['tool-metric']['path'], contents)
    write(store.standards['2512'], SPECS['tool-metric']['path'], contents)
    target = store.import_release('test', '2512', 'Synthetic standard warnings', 'fixture')
    page.locator('#refresh').click();expect(page.locator('#releases')).to_contain_text(target['id'])
    validate(page, target)
    expect(page.locator('#release-validation-state')).to_contain_text('Technisch ohne blockierende Befunde')
    expect(page.locator('#release-validation-state')).to_contain_text('75 Standard-Verweisgruppen / 75 betroffene Zeilen')
    page.locator('#release-validation-kind').select_option('warning')
    expect(page.locator('#release-validation-items tr')).to_have_count(50)
    page.locator('#release-validation-next').click();expect(page.locator('#release-validation-items tr')).to_have_count(25)
    page.locator('#release-validation-query').fill('MISSING_74')
    expect(page.locator('#release-validation-items tr')).to_have_count(1)
    expect(page.locator('#release-validation-page')).to_contain_text('1–1 von 1')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    assert store.get_release(target['id'])['validation']['references']['warning_rows'] == 75
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('defect', ['binding', 'shape'])
def test_unbound_or_incomplete_reply_does_not_publish_a_partial_summary_or_retry(ui, defect):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    suffix = '/api/v1/releases/' + target['id'] + '/validate'
    posts = []
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' and request.url.endswith(suffix) else None)
    def tamper(route):
        response = route.fetch();value = response.json()
        if defect == 'binding':value['file_count'] += 1
        else:value['libraries'] = None
        route.fulfill(response=response, json=value)
    page.route('**' + suffix, tamper)
    validate(page, target)
    expect(page.locator('#release-validation-state')).to_contain_text('Lesbare Übersicht nicht bestätigt')
    expect(page.locator('#release-validation-items tr')).to_have_count(0)
    assert len(posts) == 1 and store.get_release(target['id'])['validation'] is not None
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('change', ['report', 'details', 'tab', 'logout'])
def test_summary_is_invalidated_by_a_new_report_or_explicit_display_context(ui, change):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    validate(page, target)
    if change == 'report':
        store.validate_release(target['id'], 'fixture')
        page.locator('#refresh').click()
        expect(page.locator('#release-validation-state')).to_contain_text('nicht mehr aktuell')
        expect(page.locator('#release-validation-items tr')).to_have_count(0)
    else:
        if change == 'details':page.locator('#audit').click();expect(page.locator('#details')).to_contain_text('release.validated')
        elif change == 'tab':page.locator('#tab-prepare').click();page.locator('#tab-manage').click()
        else:page.locator('#disconnect').click()
        expect(page.locator('#release-validation-box')).to_be_hidden()
    assert store.active()['release_id'] is None


def test_late_validation_after_workspace_aba_does_not_reopen_readable_result(ui):
    from playwright.sync_api import expect
    page, _, target, _ = ui
    button = page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Prüfen', exact=True)
    button.evaluate('(node)=>node.id="isolated-readable-validation"')
    hold(page, '/api/v1/releases/' + target['id'] + '/validate');track(page, 'isolated-readable-validation')
    button.click();page.wait_for_function('()=>window.__held')
    page.locator('#tab-prepare').click();page.locator('#tab-manage').click()
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#details-box')).not_to_have_attribute('open', '')
    expect(page.locator('#release-validation-box')).to_be_hidden()


def test_stored_validation_can_be_read_without_a_new_post_or_audit_entry(ui):
    from playwright.sync_api import expect
    from nxserver.integrity import snapshot
    page, store, target, _ = ui
    store.validate_release(target['id'], 'fixture')
    page.locator('#refresh').click();expect(page.locator('#releases')).to_contain_text('validated')
    before = snapshot(store)['sha256'];posts = []
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Details', exact=True).click()
    expect(page.locator('#release-validation-state')).to_contain_text('Technisch ohne blockierende Befunde')
    expect(page.locator('#release-validation-items')).to_contain_text('tool-metric')
    assert not posts and snapshot(store)['sha256'] == before
