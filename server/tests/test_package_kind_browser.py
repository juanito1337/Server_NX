"""New-draft kind switches must not strand or silently discard package data."""
import json
import os

import pytest

from nxserver.integrity import snapshot
from nxserver.libraries import KINDS
from test_filepackages import packages, body
from test_filepackages_browser import package_ui

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated package kind-switch tests.')


def prepared(page, store):
    from playwright.sync_api import expect
    value = body(store, status='draft')
    page.locator('#new').click(); page.locator('#kind').select_option('template')
    page.locator('#name').fill('Unsaved package kind switch')
    page.locator('#editor-section details.technical').evaluate('(node)=>node.open=true')
    page.locator('#assetids').fill(json.dumps(value['asset_ids'])); page.locator('#assetids').dispatch_event('change')
    page.locator('#package-load-assets').click(); expect(page.locator('#package-asset option')).to_have_count(1)
    page.locator('#package-root').select_option('UGII/templates/'); page.locator('#package-add').click()
    expect(page.locator('#package-entries tr')).to_have_count(1)
    return page.evaluate('NXFilePackages.fields().file_package.entries')


def test_unsupported_kind_keeps_package_visible_and_allows_explicit_deactivation(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui; entries = prepared(page, store)
    database = snapshot(store)['sha256']
    page.locator('#kind').select_option('gauge')
    expect(page.locator('#file-package-editor')).to_be_visible()
    expect(page.locator('#package-kind-state')).to_contain_text('unterstützt kein NX-Dateipaket')
    expect(page.locator('#package-enabled')).to_be_checked()
    expect(page.locator('#package-entries input')).to_have_value(entries[0]['path'])
    page.locator('#package-enabled').uncheck()
    assert page.evaluate('NXFilePackages.fields().file_package') is None
    assert snapshot(store)['sha256'] == database


def test_every_unsupported_kind_blocks_save_before_receipt_and_preserves_original_paths(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui; entries = prepared(page, store)
    database = snapshot(store)['sha256']; mutations = []
    page.on('request', lambda request: mutations.append(request.url) if request.method != 'GET' else None)
    for kind in sorted(set(KINDS) - {'template', 'ui_standard', 'machine', 'fixture'}):
        page.locator('#kind').select_option(kind)
        expect(page.locator('#file-package-editor')).to_be_visible()
        expect(page.locator('#package-entries input')).to_be_disabled()
        expect(page.locator('#package-add')).to_be_disabled()
        page.locator('#save').click(); expect(page.locator('#message')).to_contain_text('unterstützt kein NX-Dateipaket')
        assert page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")') is None
        page.locator('#kind').select_option('template')
        assert page.evaluate('NXFilePackages.fields().file_package.entries') == entries
    assert not mutations and snapshot(store)['sha256'] == database and not store.resources()


def test_explicit_deactivation_then_save_keeps_file_and_attachment_but_not_a_hidden_package(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui; prepared(page, store)
    ids = json.loads(page.locator('#assetids').input_value())
    page.locator('#kind').select_option('gauge'); page.locator('#package-enabled').uncheck()
    expect(page.locator('#package-kind-state')).to_contain_text('Paket ausdrücklich deaktiviert')
    page.locator('#save').click(); expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved = store.resources()[0]
    assert saved['kind'] == 'gauge' and saved['cam_status'] == 'draft' and saved['revision'] == 1
    assert saved['file_package'] is None and saved['asset_ids'] == ids
    assert len(store.asset_inventory()['items']) == 1 and store.asset(ids[0])[1].is_file()


def test_returning_to_supported_kind_preserves_entries_without_restoring_old_checks(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui; entries = prepared(page, store)
    page.locator('#package-bind').click(); expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    for key in ('paths', 'dependencies', 'content', 'nx_test'): page.locator('#package-' + key).check()
    database = snapshot(store)['sha256']
    page.locator('#kind').select_option('gauge'); page.locator('#kind').select_option('template')
    expect(page.locator('#package-kind-state')).to_be_hidden()
    expect(page.locator('#package-entries input')).to_be_enabled()
    result = page.evaluate('NXFilePackages.fields().file_package')
    assert result['entries'] == entries and result['asset_hashes'] == {} and not any(result['checks'].values())
    assert snapshot(store)['sha256'] == database


def test_empty_non_package_draft_stays_uncluttered_and_disabled_controls_cannot_write_retained_rows(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui
    page.locator('#new').click(); page.locator('#kind').select_option('gauge')
    expect(page.locator('#file-package-editor')).to_be_hidden()
    page.locator('#tab-manage').click()
    entries = prepared(page, store); database = snapshot(store)['sha256']
    page.locator('#kind').select_option('gauge')
    page.evaluate('''async()=>{const field=document.querySelector('#package-entries input');field.value='UGII/templates/WRONG.prt';field.oninput();await document.querySelector('#package-entries button').onclick();}''')
    page.locator('#kind').select_option('template')
    assert page.evaluate('NXFilePackages.fields().file_package.entries') == entries
    assert snapshot(store)['sha256'] == database
