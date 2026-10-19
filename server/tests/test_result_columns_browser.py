"""New target columns through the real GUI; synthetic DAT/STEP/PRT, no NX."""
import os

import pytest

from nxserver.libraries import SPECS
from nxserver.results import Results
from test_libraries import write
from test_results import table
from test_requests_browser import ui, hold, track, release as release_reply
from test_result_jobs_browser import prepare

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated result column GUI.')


@pytest.fixture
def columns_ui(ui, request):
    from playwright.sync_api import expect
    page, store, *_ = ui
    _, tool, job, packet = prepare(page, store)
    fields = ['NEW_' + str(index) for index in range(12)] if getattr(request, 'param', None) == 'pages' else ['NEW_A', 'NEW_B']
    source = store.sources['test']
    write(source, SPECS['tool-metric']['path'], table(['LIBRF', 'DESCR', 'HLDREF', *fields], [('STD', 'Stock', '', *['0'] * len(fields))]))
    write(source, SPECS['holder-metric']['path'], table(['LIBRF', 'DESCR', 'NEW_HOLDER'], [('STDH', 'Stock', '')]))
    target = store.import_release('test', '2512', 'New target columns', 'fixture')
    page.locator('#refresh').click(); expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    page.locator('#geometry-jobs').get_by_role('button', name='Ergebnispaket', exact=True).click()
    page.locator('#result-file').set_input_files({'name': 'prepared-result.zip', 'mimeType': 'application/zip', 'buffer': packet})
    page.locator('#result-upload').click(); expect(page.locator('#result-state')).to_contain_text('Paket SHA-geprüft gespeichert', timeout=20000)
    page.locator('#result-target').select_option(target['id']); page.locator('#result-preview').click()
    expect(page.locator('#result-columns-box')).to_be_visible()
    yield page, store, target, tool, job, Results(store).list(job['id'])[0]
    assert store.active()['release_id'] is None


def field(page, name): return page.locator('#result-columns fieldset[data-field="' + name + '"]')


def confirm_all(page):
    field(page, 'NEW_A').locator('[data-column-value]').fill('17.5')
    for name in ('NEW_A', 'NEW_B', 'NEW_HOLDER'): field(page, name).locator('[data-column-confirm]').check()


def test_new_values_and_empty_confirmation_create_only_drafts(columns_ui):
    from playwright.sync_api import expect
    page, store, target, tool, job, result = columns_ui
    before = store.manifest(store.release_folder(target['id']))
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-columns fieldset')).to_have_count(3)
    assert not field(page, 'NEW_B').locator('[data-column-confirm]').is_checked()
    confirm_all(page); page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_enabled()
    page.locator('#result-apply').click()
    expect(page.locator('#message')).to_contain_text('neue/aktualisierte Entwürfe gespeichert', timeout=20000)
    parent = next(value for value in store.resources() if value['id'] == tool['id'])
    assert parent['cam']['records'][0]['values']['NEW_A'] == '17.5' and parent['cam']['records'][0]['values']['NEW_B'] == ''
    assert parent['cam_status'] == 'draft' and parent['revision'] == 2
    assert store.manifest(store.release_folder(target['id'])) == before
    assert Results(store).get(result['id'])['applied']['column_values']['tool-metric']['CUSTOM']['SAMPLE:1']['NEW_B'] == ''


def test_typed_unconfirmed_values_and_edited_accepted_values_require_new_preview(columns_ui):
    from playwright.sync_api import expect
    page, store, _, tool, *_ = columns_ui
    field(page, 'NEW_A').locator('[data-column-value]').fill('17.5')
    page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_disabled()
    expect(field(page, 'NEW_A').locator('[data-column-value]')).to_have_value('17.5')
    confirm_all(page); page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_enabled()
    field(page, 'NEW_A').locator('[data-column-value]').fill('18')
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-state')).to_contain_text('Spaltenentscheidungen geändert')
    assert next(value for value in store.resources() if value['id'] == tool['id']) == tool


@pytest.mark.parametrize('columns_ui', ['pages'], indirect=True)
def test_column_pagination_retains_explicit_values_and_target_change_clears_them(columns_ui):
    from playwright.sync_api import expect
    page, _, target, *_ = columns_ui
    expect(page.locator('#result-columns fieldset')).to_have_count(10)
    expect(page.locator('#result-columns-page')).to_contain_text('von 13')
    first = page.locator('#result-columns fieldset').first
    first.locator('[data-column-value]').fill('retained'); first.locator('[data-column-confirm]').check()
    page.locator('#result-columns-next').click(); expect(page.locator('#result-columns fieldset')).to_have_count(3)
    page.locator('#result-columns-prev').click()
    expect(page.locator('#result-columns fieldset').first.locator('[data-column-value]')).to_have_value('retained')
    assert page.locator('#result-columns fieldset').first.locator('[data-column-confirm]').is_checked()
    page.locator('#result-target').select_option(''); page.locator('#result-target').select_option(target['id'])
    expect(page.locator('#result-columns-box')).to_be_hidden()
    page.locator('#result-preview').click(); expect(page.locator('#result-columns-box')).to_be_visible()
    assert not page.locator('#result-columns fieldset').first.locator('[data-column-confirm]').is_checked()


@pytest.mark.parametrize('old_error', [False, True])
def test_changed_column_decisions_discard_late_preview_and_error(columns_ui, old_error):
    from playwright.sync_api import expect
    page, *_ = columns_ui; confirm_all(page)
    hold(page, '/preview', old_error); track(page, 'result-preview')
    page.locator('#result-preview').click(); page.wait_for_function('()=>window.__held')
    field(page, 'NEW_A').locator('[data-column-value]').fill('18')
    release_reply(page)
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    expect(field(page, 'NEW_A').locator('[data-column-value]')).to_have_value('18')


def test_explicit_discard_removes_values_without_changing_packet(columns_ui):
    from playwright.sync_api import expect
    page, store, _, _, _, result = columns_ui; confirm_all(page)
    original = Results(store).archive(result['id']).read_bytes()
    page.locator('#result-columns-clear').click()
    assert not field(page, 'NEW_A').locator('[data-column-confirm]').is_checked()
    expect(field(page, 'NEW_A').locator('[data-column-value]')).to_have_value('')
    assert Results(store).archive(result['id']).read_bytes() == original


@pytest.mark.parametrize('defect', ['contract', 'missing_values', 'changed_value', 'confirmation', 'duplicate'])
def test_inconsistent_column_reply_never_qualifies_an_application(columns_ui, defect):
    from playwright.sync_api import expect
    page, store, _, tool, *_ = columns_ui; confirm_all(page)
    def tamper(route):
        response = route.fetch(); value = response.json()
        if defect == 'contract': value['column_decisions_contract'] = True
        elif defect == 'missing_values': value.pop('column_values')
        elif defect == 'changed_value': value['column_decisions'][0]['value'] = 'unreviewed'
        elif defect == 'confirmation': value['column_decisions'][0]['confirmed'] = False
        else: value['column_decisions'].append(value['column_decisions'][0])
        route.fulfill(response=response, json=value)
    page.route('**/geometry-results/*/preview', tamper)
    track(page, 'result-preview'); page.locator('#result-preview').click(); page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#message')).to_contain_text('Ergebnisvorschauvertrag')
    assert next(item for item in store.resources() if item['id'] == tool['id']) == tool
