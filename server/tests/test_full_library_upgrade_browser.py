"""Registered-library GUI lifecycle, synthetic values and optional installed schemas."""
import hashlib
import json
import zipfile
import os

import pytest

from nxserver.libraries import NativeTable, SPECS, reference_report
from test_upgrade_recovery_browser import wizard
from full_upgrade_test_support import all_schema_data, EXTRA, prepare, verify

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated all-library upgrade GUI; no actual NX.')


def test_whole_upgrade_all_libraries_catalog_daily_edit_export_and_starter(wizard, all_schema_data):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    defaults, expected = prepare(store, source, all_schema_data)
    roots = (source, *store.standards.values(), *store.sidts.values())
    before = {str(root): store.manifest(root) for root in roots}
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-review')).to_be_visible(timeout=60000)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert not target.exists() and store.releases() == []
    page.locator('#import-defaults').fill(json.dumps(defaults))
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=60000)
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt', timeout=60000)
    assert len(store.releases()) == 1
    migrated = store.releases()[0]
    assert migrated['test_launcher'] and not migrated['review']
    counts = verify(target, expected)
    migrated_manifest = store.manifest(target)
    page.locator('#tab-manage').click()
    page.locator('#library-release').select_option(migrated['id'])
    page.locator('#library-inventory').click()
    expect(page.locator('#catalog-libraries input')).to_have_count(len(SPECS))
    for checkbox in page.locator('#catalog-libraries input').all():
        checkbox.check()
    page.locator('#include-standard').uncheck()
    page.locator('#catalog-preview').click()
    expect(page.locator('#catalog-import')).to_be_enabled(timeout=60000)
    report = json.loads(page.locator('#details').inner_text())
    count = counts['custom'] + counts['legacy']
    assert report['count'] == count and not report['conflicts']
    page.locator('#catalog-import').click()
    expect(page.locator('#message')).to_contain_text('Stammdaten übernommen', timeout=60000)
    assert len(store.resources()) == count and all(resource['cam_status'] == 'draft' for resource in store.resources())
    holder = next(resource for resource in store.resources() if resource['cam'].get('library_id') == 'holder-metric'
        and resource['origin']['key'] == expected['holder-metric']['custom_key'])
    page.locator('#resource-query').fill(holder['id'])
    expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button', name='Bearbeiten', exact=True).click()
    page.locator('#native-field-query').fill(EXTRA)
    field = page.locator('[data-native-row="0"] [data-native-field="' + EXTRA + '"]')
    expect(field).to_have_value('EXPLICIT_REVIEWED_2512')
    field.fill('EXPLICIT_DAILY_EDIT_2512')
    page.locator('#camstatus').select_option('approved')  # Synthetic contract, not an NX qualification.
    page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert store.get_resource(holder['id'])['revision'] == 2
    page.locator('#tab-manage').click()
    row = page.locator('#releases tr').filter(has_text=migrated['id'])
    row.get_by_role('button', name='Neuen Export ableiten', exact=True).click()
    page.locator('#export-preview').click()
    expect(page.locator('#export-create')).to_be_enabled(timeout=60000)
    page.locator('#export-create').click()
    expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau', timeout=60000)
    child = next(release for release in store.releases() if release['parent_id'] == migrated['id'])
    folder = store.release_folder(child['id'])
    groups = NativeTable.read(folder / SPECS['holder-metric']['path']).groups()
    assert groups[expected['holder-metric']['custom_key']][0]['values'][EXTRA] == 'EXPLICIT_DAILY_EDIT_2512'
    assert groups[expected['holder-metric']['stock_key']][0]['values'][EXTRA] == 'TARGET_STOCK_2512'
    assert not reference_report(folder, store.standards, '2512')['errors']
    for identifier, value in expected.items():
        if identifier != 'holder-metric':
            assert (folder / SPECS[identifier]['path']).read_bytes() == (target / SPECS[identifier]['path']).read_bytes()
    download_row = page.locator('#releases tr').filter(has_text=child['id'])
    with page.expect_download() as downloaded:
        download_row.get_by_role('button', name='Test-Starter herunterladen', exact=True).click()
    with zipfile.ZipFile(downloaded.value.path()) as archive:
        config = json.loads(archive.read('release_test.json'))
        assert config['ReleaseId'] == child['id']
        assert config['ManifestSha256'] == hashlib.sha256(archive.read('release_manifest.json')).hexdigest()
        for name, sha in config['ScriptHashes'].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == sha
        assert b'PATH = $nxSearchPath' in archive.read('Start-NX2512ManagedTest.ps1')
    assert store.manifest(target) == migrated_manifest and store.active()['release_id'] is None
    for root in roots:
        assert store.manifest(root) == before[str(root)]
    print(json.dumps({'schema_mode': all_schema_data['mode'], 'libraries': len(SPECS), 'custom_and_hd_drafts': count, 'daily_revision': 2,
        'historical_migration_unchanged': True, 'starter_downloaded': True, 'nx_executed': False}))
