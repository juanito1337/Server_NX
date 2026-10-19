"""File-package row navigation and stale-control safety on disposable data."""
import copy
import os

import pytest

from nxserver.integrity import snapshot
from test_filepackages import packages, body
from test_filepackages_browser import package_ui

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated package editor rows.')


def open_package(page, store, value):
    from playwright.sync_api import expect
    item = store.save_resource(value, 'fixture')
    page.locator('#refresh').click(); expect(page.locator('#resources')).to_contain_text(item['name'])
    page.locator('#tab-manage').click()
    page.locator('#resources tr').filter(has_text=item['name']).get_by_role('button', name='Bearbeiten', exact=True).click()
    return item


@pytest.mark.parametrize('action', ['remove', 'path'])
def test_old_remove_button_cannot_remove_a_file_from_a_different_editor(package_ui, action):
    page, store, *_ = package_ui
    first = body(store, status='draft'); first['name'] = 'Original package editor'
    open_package(page, store, first)
    page.locator('#package-entries button').evaluate('(node)=>globalThis.__oldPackageRemove=node')
    page.locator('#package-entries input').evaluate('(node)=>globalThis.__oldPackagePath=node')
    newer = body(store, path='UGII/templates/OTHER.prt', filename='OTHER.prt', status='draft')
    newer['name'] = 'New package editor'
    open_package(page, store, newer)
    before = page.evaluate('NXFilePackages.fields()'); database = snapshot(store)['sha256']
    if action == 'remove': page.evaluate('async()=>await __oldPackageRemove.onclick()')
    else: page.evaluate('()=>{__oldPackagePath.value="UGII/templates/WRONG.prt";__oldPackagePath.oninput();}')
    assert page.evaluate('NXFilePackages.fields()') == before
    assert snapshot(store)['sha256'] == database


@pytest.mark.parametrize('change', ['filter', 'page', 'tab', 'logout'])
def test_old_row_controls_after_view_change_do_not_alter_package_fields(package_ui, change):
    from playwright.sync_api import expect
    page, store, *_ = package_ui; open_package(page, store, many(store, 25))
    page.locator('#package-entries button').first.evaluate('(node)=>globalThis.__oldPackageRemove=node')
    page.locator('#package-entries input').first.evaluate('(node)=>globalThis.__oldPackagePath=node')
    if change == 'filter': page.locator('#package-entry-query').fill('PART_024.prt')
    elif change == 'page': page.locator('#package-entry-next').click()
    elif change == 'tab': page.locator('#tab-manage').click()
    else: page.locator('#disconnect').click()
    before = page.evaluate('NXFilePackages.fields()'); database = snapshot(store)['sha256']
    page.evaluate('''async()=>{__oldPackagePath.value='UGII/templates/WRONG.prt';__oldPackagePath.oninput();await __oldPackageRemove.onclick();}''')
    assert page.evaluate('NXFilePackages.fields()') == before
    assert snapshot(store)['sha256'] == database


def many(store, count=45):
    value = {'kind': 'template', 'name': 'Many package entries', 'cam': {}, 'cam_status': 'draft', 'erp': None,
             'asset_ids': [], 'file_package': {'nx_version': '2512', 'entries': [], 'checks': {key: True for key in ('paths', 'dependencies', 'content', 'nx_test')}, 'asset_hashes': {}, 'evidence': 'Synthetic package flags, not an NX qualification.', 'provenance': None}}
    for index in range(count):
        name = f'PART_{index:03d}.prt'; asset = store.add_asset(name, b'Synthetic component; never open in NX', 'fixture')
        value['asset_ids'].append(asset['id'])
        value['file_package']['asset_hashes'][asset['id']] = asset['sha256']
        value['file_package']['entries'].append({'asset_id': asset['id'], 'path': 'UGII/templates/' + name})
    return value


def test_search_pages_and_filtered_removal_keep_full_entry_order_and_original_indices(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui
    resource = open_package(page, store, many(store)); database = snapshot(store)['sha256']
    before = page.evaluate('NXFilePackages.fields().file_package.entries')
    qualified = page.evaluate('NXFilePackages.fields()')
    expect(page.locator('#package-entries tr')).to_have_count(20)
    page.locator('#package-entry-next').click(); expect(page.locator('#package-entries tr')).to_have_count(20)
    page.locator('#package-entry-next').click(); expect(page.locator('#package-entries tr')).to_have_count(5)
    assert page.evaluate('NXFilePackages.fields().file_package.entries') == before
    assert page.evaluate('NXFilePackages.fields()') == qualified
    page.locator('#package-entry-query').fill('PART_043.prt')
    expect(page.locator('#package-entries tr')).to_have_count(1)
    page.locator('#package-entries input').fill('UGII/templates/Sub/PART_043.prt')
    expected = copy.deepcopy(before); expected[43]['path'] = 'UGII/templates/Sub/PART_043.prt'
    assert page.evaluate('NXFilePackages.fields().file_package.entries') == expected
    page.locator('#package-entries button').click(); expected.pop(43)
    assert page.evaluate('NXFilePackages.fields().file_package.entries') == expected
    page.locator('#package-entry-query').fill(''); expect(page.locator('#package-entries tr')).to_have_count(20)
    assert store.get_resource(resource['id'])['file_package']['entries'] == before
    assert snapshot(store)['sha256'] == database
    page.locator('#save').click(); expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved = store.get_resource(resource['id'])
    assert saved['revision'] == 2 and saved['file_package']['entries'] == expected
    assert len(saved['asset_ids']) == 45 and len(store.asset_inventory()['items']) == 45


def test_declined_removal_preserves_paths_hashes_and_checks(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui; open_package(page, store, many(store, 2))
    before = page.evaluate('NXFilePackages.fields()'); database = snapshot(store)['sha256']
    page.evaluate('window.confirm=()=>false')
    page.locator('#package-entries button').first.click()
    expect(page.locator('#package-entries tr')).to_have_count(2)
    assert page.evaluate('NXFilePackages.fields()') == before and snapshot(store)['sha256'] == database


def test_added_file_is_visible_after_an_existing_filter_and_on_the_last_page(package_ui):
    from playwright.sync_api import expect
    page, store, *_ = package_ui; value = many(store, 25)
    extra = value['file_package']['entries'].pop()
    del value['file_package']['asset_hashes'][extra['asset_id']]
    resource = open_package(page, store, value); database = snapshot(store)['sha256']
    page.locator('#package-entry-query').fill('no match'); expect(page.locator('#package-entries tr')).to_have_count(0)
    page.locator('#package-load-assets').click(); expect(page.locator('#package-asset option')).to_have_count(25)
    page.locator('#package-asset').select_option(extra['asset_id']); page.locator('#package-root').select_option('UGII/templates/')
    page.locator('#package-add').click()
    expect(page.locator('#package-entry-query')).to_have_value('')
    expect(page.locator('#package-entries tr')).to_have_count(5)
    expect(page.locator('#package-entries')).to_contain_text('PART_024.prt')
    assert page.evaluate('NXFilePackages.fields().file_package.entries') == value['file_package']['entries'] + [extra]
    assert len(store.get_resource(resource['id'])['file_package']['entries']) == 24
    assert snapshot(store)['sha256'] == database
