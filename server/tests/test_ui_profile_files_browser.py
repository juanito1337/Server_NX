"""Actual UI actions for opaque profile data; synthetic bytes, never real NX."""
import json
import os

import pytest

from nxserver.filepackages import CHECKS
from test_filepackages import packages
from test_filepackages_browser import package_ui
from test_ui_profile_files import EXTENSIONS

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated Edge UI profile packages.')


def export_from_gui(page, store, parent):
    from playwright.sync_api import expect
    page.locator('#tab-manage').click()
    page.locator('#releases tr').filter(has_text=parent['id']).get_by_role(
        'button', name='Neuen Export ableiten', exact=True).click()
    page.locator('#export-preview').click()
    expect(page.locator('#export-create')).to_be_enabled()
    page.locator('#export-create').click()
    expect(page.locator('#message')).to_contain_text(
        'Neuer NX-Datenstand aus geprüfter Exportvorschau', timeout=15000)
    return next(item for item in store.releases() if item['parent_id'] == parent['id'])


def test_all_nine_profile_types_upload_bind_save_and_export_through_gui(package_ui):
    from playwright.sync_api import expect
    page, store, parent, source = package_ui
    before = store.manifest(store.release_folder(parent['id']))
    source_before = store.manifest(source)
    expected = {}
    page.locator('#new').click()
    page.locator('#kind').select_option('ui_standard')
    page.locator('#name').fill('Synthetic nine-format UI profile package')
    for index, extension in enumerate(EXTENSIONS):
        name = 'Profile_' + str(index) + extension
        content = b'\x00\x81Synthetic opaque UI definition; NOT NX qualification\r\n' + extension.encode()
        page.locator('#assetfile').set_input_files({
            'name': name, 'mimeType': 'application/octet-stream', 'buffer': content})
        page.locator('#upload').click()
        expect(page.locator('#message')).to_contain_text('Datei gespeichert')
        expect(page.locator('#attachments tr')).to_have_count(index + 1)
        identifier = json.loads(page.locator('#assetids').input_value())[-1]
        page.locator('#package-load-assets').click()
        expect(page.locator('#package-asset option')).to_have_count(index + 1)
        page.locator('#package-asset').select_option(identifier)
        page.locator('#package-root').select_option('UGII/menus/profiles/')
        page.locator('#package-add').click()
        path = 'UGII/menus/profiles/All/' + name
        page.locator('#package-entries input').nth(index).fill(path)
        expected[path] = content
    assert store.resources() == []
    # The upload whitelist is not permission to publish these types as roles.
    row = page.locator('#package-entries input').first
    correct = row.input_value()
    row.fill('UGII/menus/roles/' + correct.rsplit('/', 1)[-1])
    page.locator('#package-preflight').click()
    expect(page.locator('#package-preflight-state')).to_contain_text('technische Fehler')
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes') == {}
    assert store.resources() == []
    row.fill(correct)
    page.locator('#package-preflight').click()
    expect(page.locator('#package-preflight-state')).to_contain_text('Technische Paketprüfung bestanden')
    expect(page.locator('#package-preflight-files tr')).to_have_count(9)
    for row in page.locator('#package-preflight-files tr').all():
        expect(row).to_contain_text('nicht fachlich geprüft')
    page.locator('#package-bind').click()
    expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    for check in CHECKS:
        expect(page.locator('#package-' + check)).not_to_be_checked()
    page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    resource = store.resources()[0]
    assert resource['cam_status'] == 'draft' and len(resource['file_package']['entries']) == 9
    assert not any(resource['file_package']['checks'].values())
    # Saving deliberately clears the editor. Reopen the stored revision through
    # the management workspace before qualifying it; do not edit an empty form.
    page.locator('#tab-manage').click()
    page.locator('#resources tr').get_by_role('button', name='Bearbeiten', exact=True).click()
    expect(page.locator('#kind')).to_have_value('ui_standard')
    expect(page.locator('#package-entries input')).to_have_count(9)
    page.locator('#package-evidence').fill(
        'Synthetic four-check UI contract only; NOT real NX qualification or deployment.')
    for check in CHECKS:
        page.locator('#package-' + check).check()
    page.locator('#camstatus').select_option('approved')
    page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert store.resources()[0]['revision'] == 2
    exported = export_from_gui(page, store, parent)
    for path, content in expected.items():
        assert (store.release_folder(exported['id']) / path).read_bytes() == content
    assert store.manifest(store.release_folder(parent['id'])) == before
    assert store.manifest(source) == source_before and store.active()['release_id'] is None


def test_existing_nine_format_profile_package_adoption_and_retirement_through_gui(package_ui):
    from playwright.sync_api import expect
    page, store, _, source = package_ui
    (store.standards['2512'] / 'UGII/menus/profiles').mkdir(parents=True)
    expected = {}
    for index, extension in enumerate(EXTENSIONS):
        path = 'UGII/menus/profiles/All/Customer_' + str(index) + extension
        file = source / path
        file.parent.mkdir(parents=True, exist_ok=True)
        expected[path] = b'Synthetic existing customer UI definition\x00' + extension.encode()
        file.write_bytes(expected[path])
    source_before = store.manifest(source)
    parent = store.import_release('test', '2512', 'Synthetic customer profiles', 'fixture')
    before = store.manifest(store.release_folder(parent['id']))
    page.locator('#refresh').click()
    expect(page.locator('#releases')).to_contain_text(parent['id'])
    page.locator('#file-adopt-release').select_option(parent['id'])
    page.locator('#file-adopt-kind').select_option('ui_standard')
    page.locator('#file-adopt-query').fill('UGII/menus/profiles/')
    page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-files input')).to_have_count(9)
    for path in expected:
        page.locator('#file-adopt-files label').filter(has_text=path).locator('input').check()
    page.locator('#file-adopt-preview').click()
    expect(page.locator('#file-adopt-apply')).to_be_enabled()
    page.locator('#file-adopt-apply').click()
    expect(page.locator('#panel-prepare')).to_be_visible()
    resource = store.resources()[0]
    assert resource['cam_status'] == 'draft' and len(resource['asset_ids']) == 9
    assert not any(resource['file_package']['checks'].values())
    assert {item['path'] for item in resource['file_package']['entries']} == set(expected)
    page.locator('#package-bind').click()
    expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    page.locator('#package-evidence').fill('Synthetic qualification flags; no actual NX model or UI test.')
    for check in CHECKS:
        page.locator('#package-' + check).check()
    page.locator('#camstatus').select_option('approved')
    page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    first = export_from_gui(page, store, parent)
    page.locator('#resources tr').get_by_role('button', name='Bearbeiten', exact=True).click()
    page.locator('#camstatus').select_option('retired')
    page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    second = export_from_gui(page, store, first)
    for path, content in expected.items():
        assert not (store.release_folder(second['id']) / path).exists()
        assert (store.release_folder(first['id']) / path).read_bytes() == content
        assert (source / path).read_bytes() == content
    assert store.manifest(store.release_folder(parent['id'])) == before
    assert store.manifest(source) == source_before and store.active()['release_id'] is None
