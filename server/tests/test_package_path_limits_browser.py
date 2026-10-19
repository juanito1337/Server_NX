"""Overlong paths are visible but not offered for adoption, no real NX."""
import os

import pytest

from nxserver.filepackages import CHECKS
from test_filepackages import packages, body
from test_filepackages_browser import package_ui
from test_package_path_limits import metadata_entry, relative_path

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated Edge package path boundaries.')


def test_overlong_metadata_path_is_explained_and_valid_selection_still_adopts(package_ui):
    from playwright.sync_api import expect
    page, store, release, source = package_ui
    bad = relative_path('UGII/templates/', 241, '.prt')
    metadata_entry(store, release, bad)
    before = store.manifest(store.release_folder(release['id']))
    source_before = store.manifest(source)
    expected_manifest = store.get_release(release['id'])['manifest']
    page.locator('#refresh').click()
    page.locator('#file-adopt-release').select_option(release['id'])
    page.locator('#file-adopt-kind').select_option('template')
    page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-files input')).to_have_count(2)
    row = page.locator('#file-adopt-files label').filter(has_text=bad)
    expect(row.locator('input')).to_be_disabled()
    expect(row).to_contain_text('1-240 characters')
    expect(page.locator('#file-adopt-apply')).to_be_disabled()
    assert store.asset_inventory()['total'] == 0 and store.resources() == []
    page.locator('#file-adopt-files label').filter(has_text='UGII/templates/CUSTOM.prt').locator('input').check()
    page.locator('#file-adopt-preview').click()
    expect(page.locator('#file-adopt-apply')).to_be_enabled()
    page.locator('#file-adopt-apply').click()
    expect(page.locator('#panel-prepare')).to_be_visible(timeout=15000)
    resource = store.resources()[0]
    assert resource['cam_status'] == 'draft' and len(resource['asset_ids']) == 1
    assert [entry['path'] for entry in resource['file_package']['entries']] == ['UGII/templates/CUSTOM.prt']
    assert store.get_release(release['id'])['manifest'] == expected_manifest
    assert store.manifest(store.release_folder(release['id'])) == before
    assert store.manifest(source) == source_before and store.active()['release_id'] is None


def test_editor_preflight_rejects_overlong_path_then_allows_explicit_correction(package_ui):
    from playwright.sync_api import expect
    page, store, release, source = package_ui
    good = 'UGII/templates/ITEM.prt'
    value = body(store, 'template', good, status='draft', filename='ITEM.prt')
    value['file_package'].update(checks={key: False for key in CHECKS}, asset_hashes={}, evidence='')
    resource = store.save_resource(value, 'fixture')
    before = store.manifest(store.release_folder(release['id']))
    page.locator('#refresh').click()
    page.locator('#resources tr').get_by_role('button', name='Bearbeiten', exact=True).click()
    audits = store.audit_entries()
    page.locator('#package-entries input').fill(relative_path('UGII/templates/', 241, '.prt'))
    page.locator('#package-preflight').click()
    expect(page.locator('#package-preflight-state')).to_contain_text('nicht bestätigt')
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes') == {}
    assert store.get_resource(resource['id']) == resource and store.audit_entries() == audits
    page.locator('#package-entries input').fill(good)
    page.locator('#package-preflight').click()
    expect(page.locator('#package-preflight-state')).to_contain_text('Technische Paketprüfung bestanden')
    page.locator('#package-bind').click()
    expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    for check in CHECKS:
        expect(page.locator('#package-' + check)).not_to_be_checked()
    page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved = store.get_resource(resource['id'])
    assert saved['revision'] == 2 and saved['cam_status'] == 'draft'
    assert saved['file_package']['entries'][0]['path'] == good
    assert store.manifest(store.release_folder(release['id'])) == before
    assert store.active()['release_id'] is None
