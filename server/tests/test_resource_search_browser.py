"""Read-only technical catalog search against disposable data, not real NX."""
import os

import pytest

from nxserver.integrity import snapshot
from test_requests_browser import ui

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated technical catalog search.')


def native(store, library, key, name, field='FUTURE_NX_COLUMN', text='Deep technical value'):
    from nxserver.libraries import SPECS
    payload = {'kind': SPECS[library]['kind'], 'name': name, 'cam_status': 'draft', 'erp': None,
               'asset_ids': [], 'geometry': {'mode': 'none', 'status': 'needs_preparation'},
               'cam': {'library_id': library, 'nx_version': '2512',
                       'schemas': [{'id': 'SAMPLE:1', 'class_name': 'SAMPLE', 'fields': ['LIBRF', field]}],
                       'records': [{'block_id': 'SAMPLE:1', 'class_name': 'SAMPLE',
                                    'values': {'LIBRF': key, field: text}}]}}
    return store.save_resource(payload, 'fixture')


def test_search_finds_native_identifier_and_future_columns_without_changing_editor_or_database(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui
    item = native(store, 'tool-metric', 'NX_ONLY_TECH_KEY_42', 'Unrelated visible name')
    before = snapshot(store)['sha256']
    page.locator('#refresh').click(); expect(page.locator('#summary')).to_contain_text('2 Stammdatensätze')
    page.locator('#tab-prepare').click(); page.locator('#name').fill('Unsaved editor text')
    page.locator('#tab-manage').click(); page.locator('#resource-query').fill('nx_only_tech_key_42')
    expect(page.locator('#resources tr')).to_have_count(1)
    expect(page.locator('#resources')).to_contain_text(item['name'])
    expect(page.locator('#resources')).to_contain_text('NX_ONLY_TECH_KEY_42')
    page.locator('#resource-query').fill('Deep technical value'); expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resource-query').fill('FUTURE_NX_COLUMN'); expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#tab-prepare').click(); expect(page.locator('#name')).to_have_value('Unsaved editor text')
    assert snapshot(store)['sha256'] == before


def test_search_includes_hidden_group_rows_and_refresh_does_not_reuse_old_revision_index(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui
    value = native(store, 'holder-metric', 'HOLDER_LONG_GROUP', 'Holder search fixture')
    body = {key: item for key, item in value.items() if key not in ('id', 'revision', 'updated_at')}
    body['cam']['records'] = [dict(body['cam']['records'][0], values={'LIBRF': 'HOLDER_LONG_GROUP', 'FUTURE_NX_COLUMN': f'GROUP_ROW_{index:03d}'}) for index in range(35)]
    value = store.save_resource(body, 'fixture', value['id'], value['revision'])
    page.locator('#refresh').click(); expect(page.locator('#summary')).to_contain_text('2 Stammdatensätze')
    page.locator('#resource-query').fill('GROUP_ROW_034'); expect(page.locator('#resources tr')).to_have_count(1)
    expect(page.locator('#resources')).to_contain_text('35 gespeicherte Zeile(n)')
    body['cam']['records'][-1]['values']['FUTURE_NX_COLUMN'] = 'NEW_REVISION_VALUE'
    store.save_resource(body, 'fixture', value['id'], value['revision'])
    before = snapshot(store)['sha256']
    page.locator('#refresh').click(); expect(page.locator('#resources tr')).to_have_count(0)
    page.locator('#resource-query').fill('NEW_REVISION_VALUE'); expect(page.locator('#resources tr')).to_have_count(1)
    assert snapshot(store)['sha256'] == before


def test_library_filter_and_pagination_reset_on_logout(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui; native(store, 'fixture-metric', 'FIXTURE_KEY', 'Fixture filter')
    page.locator('#refresh').click(); expect(page.locator('#summary')).to_contain_text('2 Stammdatensätze')
    page.locator('#resource-library').select_option('fixture-metric')
    expect(page.locator('#resources tr')).to_have_count(1)
    before = snapshot(store)['sha256']; page.locator('#disconnect').click()
    expect(page.locator('#resource-library')).to_have_value('')
    expect(page.locator('#resources tr')).to_have_count(0)
    assert snapshot(store)['sha256'] == before


def test_library_filter_separates_metric_english_cmm_and_non_native_records(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui
    for library in ('tool-metric', 'tool-english', 'cmm-inspection_tool-metric'):
        native(store, library, 'SHARED_IDENTIFIER', 'Name for ' + library)
    before = snapshot(store)['sha256']
    page.locator('#refresh').click(); expect(page.locator('#summary')).to_contain_text('4 Stammdatensätze')
    for library in ('tool-metric', 'tool-english', 'cmm-inspection_tool-metric'):
        page.locator('#resource-library').select_option(library)
        expect(page.locator('#resources tr')).to_have_count(1)
        expect(page.locator('#resources')).to_contain_text('Name for ' + library)
    page.locator('#resource-library').select_option('__no_native__')
    expect(page.locator('#resources tr')).to_have_count(1)
    expect(page.locator('#resources')).to_contain_text('Initial fixture')
    assert snapshot(store)['sha256'] == before
