"""Real GUI schema-to-draft smoke for all adapters, using synthetic tables only."""
import os

import pytest

from nxserver.libraries import SPECS
from test_libraries import write
from test_requests_browser import ui

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated all-library creation smoke.')


def test_every_native_adapter_can_create_a_draft_through_its_loaded_schema(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui; source = store.sources['test']
    for spec in SPECS.values():
        write(source, spec['path'], '#CLASS SAMPLE\nFORMAT LIBRF DESCR FUTURE_NX_COLUMN\nDATA |STD|Synthetic stock|Future stock text\n#END_DATA\n')
    release = store.import_release('test', '2512', 'All native adapters fixture', 'fixture')
    before = store.manifest(source)
    page.locator('#refresh').click(); expect(page.locator('#summary')).to_contain_text('2 Ordner-Versionen')
    page.locator('#tab-prepare').click(); page.locator('#create-release').select_option(release['id'])
    page.locator('#create-inventory').click(); expect(page.locator('#create-library option')).to_have_count(len(SPECS))
    for index, (identifier, spec) in enumerate(SPECS.items()):
        page.locator('#create-library').select_option(identifier); page.locator('#create-schema').click()
        expect(page.locator('#native-new')).to_be_enabled(); page.locator('#native-new').click()
        expect(page.locator('#kind')).to_have_value(spec['kind'])
        name = 'Synthetic GUI adapter ' + identifier
        page.locator('#name').fill(name)
        expect(page.locator('#native-fields input')).to_have_count(3)
        for position, value in enumerate(('CUSTOM_' + str(index), 'Customer description', 'Future value ' + str(index))):
            page.locator('#native-fields input').nth(position).fill(value)
        page.locator('#save').click(); expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
        saved = next(item for item in store.resources() if item['name'] == name)
        assert saved['kind'] == spec['kind'] and saved['cam']['library_id'] == identifier
        assert saved['cam_status'] == 'draft' and saved['revision'] == 1
        assert saved['cam']['records'][0]['values']['FUTURE_NX_COLUMN'] == 'Future value ' + str(index)
    assert len(store.resources()) == len(SPECS) + 1
    assert store.manifest(source) == before and store.active()['release_id'] is None


def test_catalog_only_gauge_policy_matches_saved_status_and_the_real_export_preview(ui):
    from playwright.sync_api import expect
    from test_preview_context_browser import open_export
    page, store, parent, *_ = ui
    before = store.manifest(store.release_folder(parent['id']))
    page.locator('#tab-manage').click()
    page.locator('#new').click()  # The visible manage action opens the prepare workspace.
    policy = page.locator('#native-export-policy')
    expect(policy).to_be_visible()
    expect(policy).to_contain_text('Prüfmittel bleiben Katalogdaten ohne native NX-Ausgabe')
    expect(policy).to_contain_text('auch bei „Für Export geprüft“')
    expect(policy).to_contain_text('CMM-Taster')
    page.locator('#kind').select_option('gauge')
    page.locator('#name').fill('Synthetic catalog-only gauge')
    page.locator('#camstatus').select_option('approved')
    page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    resource = next(item for item in store.resources() if item['name'] == 'Synthetic catalog-only gauge')
    assert resource['kind'] == 'gauge' and resource['cam_status'] == 'approved' and resource['cam'] == {}
    page.locator('#tab-manage').click()
    open_export(page, parent)
    page.locator('#export-preview').click()
    row = page.locator('#export-actions tr').filter(has_text='Synthetic catalog-only gauge')
    expect(row).to_contain_text('Nur Stammdaten')
    expect(row).to_contain_text('kein NX-Exportadapter zugeordnet')
    assert store.manifest(store.release_folder(parent['id'])) == before
    assert len(store.releases()) == 1 and store.active()['release_id'] is None
