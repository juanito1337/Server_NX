"""Atomic native-object pages, using only synthetic files and isolated Edge."""
import os

import pytest

from nxserver.integrity import snapshot
from nxserver.libraries import SPECS, NativeTable, native_cam
from test_catalog_selection_browser import selection_ui, read, select
from test_filepackages_browser import package_ui
from test_filepackages import packages
from test_requests_browser import hold, track, release as release_reply
from test_libraries import write

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated native-page Edge tests.')


@pytest.mark.parametrize('failure', ['http', 'partial'])
def test_failed_reread_removes_old_or_partial_page_without_writing(selection_ui, failure):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'tool-metric')
    expect(page.locator('#native-objects > tr')).to_have_count(4)
    select(page, 'tool-metric', 'A')
    page.locator('#catalog-preview').click()
    expect(page.locator('#catalog-import')).to_be_enabled()
    pattern = '**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?*'

    def corrupt(route):
        if failure == 'http':
            route.fulfill(status=503, json={'detail': 'Synthetic page unavailable'})
        else:
            response = route.fetch()
            data = response.json()
            data['items'][1]['cam']['records'] = None
            route.fulfill(response=response, json=data)

    page.route(pattern, corrupt)
    page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('Bibliothek nicht aktualisiert')
    expect(page.locator('#native-objects > tr')).to_have_count(0)
    expect(page.locator('#catalog-import')).to_be_disabled()
    expect(page.locator('#catalog-select-page')).to_be_disabled()
    expect(page.locator('#catalog-selection-state')).to_contain_text('1 vollständige Objekte')
    expect(page.locator('#catalog-selection-state')).to_contain_text('erneut laden')
    page.locator('#catalog-preview').evaluate('(node)=>node.onclick()')
    expect(page.locator('#message')).to_contain_text('erneut laden')
    page.unroute(pattern, corrupt)
    page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('ausgewählte DAT-Datei SHA-geprüft')
    expect(page.get_by_role('checkbox', name='Objekt tool-metric / A auswählen', exact=True)).to_be_checked()
    page.locator('#catalog-preview').click()
    expect(page.locator('#catalog-import')).to_be_enabled()
    assert snapshot(store)['sha256'] == before and store.resources() == []


@pytest.mark.parametrize('failure', ['spec', 'count', 'schema', 'record', 'duplicate', 'version'])
def test_malformed_page_is_rejected_atomically(selection_ui, failure):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']

    def corrupt(route):
        response = route.fetch()
        data = response.json()
        if failure == 'spec': data['path'] = 'different_database.dat'
        elif failure == 'count': data['filtered'] += 1
        elif failure == 'schema': data['schemas'][0]['schema_hash'] = '0' * 64
        elif failure == 'record': data['items'][-1]['cam']['records'][-1]['values']['SEQ'] = 12
        elif failure == 'duplicate': data['items'][-1] = data['items'][0]
        else: data['items'][-1]['cam']['nx_version'] = '2406'
        route.fulfill(response=response, json=data)

    page.route('**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?*', corrupt)
    page.locator('#native-library').select_option('tool-metric')
    page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('Bibliothek nicht aktualisiert')
    expect(page.locator('#native-objects > tr')).to_have_count(0)
    for identifier in ('library-prev', 'library-next', 'catalog-select-page', 'catalog-import'):
        expect(page.locator('#' + identifier)).to_be_disabled()
    assert snapshot(store)['sha256'] == before


def test_only_unverified_selected_library_blocks_selection_and_can_be_removed(selection_ui):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'holder-metric'); select(page, 'holder-metric', 'KEY_0000')
    read(page, 'tool-metric', 'A'); select(page, 'tool-metric', 'A')
    pattern = '**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?*'
    page.route(pattern, lambda route: route.fulfill(status=503, json={'detail': 'Synthetic unavailable'}))
    page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('Bibliothek nicht aktualisiert')
    expect(page.locator('#catalog-preview')).to_be_disabled()
    read(page, 'holder-metric')
    expect(page.locator('#catalog-preview')).to_be_disabled()
    expect(page.locator('#catalog-selection-state')).to_contain_text('2 vollständige Objekte')
    page.get_by_text('Ausgewählte Bibliotheksobjekte prüfen / entfernen', exact=True).click()
    page.locator('#catalog-selection-list p').filter(has_text='tool-metric / A').get_by_role('button').click()
    expect(page.locator('#catalog-preview')).to_be_enabled()
    expect(page.locator('#catalog-selection-state')).to_contain_text('1 vollständige Objekte')
    page.locator('#catalog-preview').click()
    expect(page.locator('#catalog-import')).to_be_enabled()
    expect(page.locator('#catalog-objects > tr')).to_have_count(1)
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('old_error', [False, True])
def test_late_page_cannot_replace_newer_page_or_its_selection(selection_ui, old_error):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'tool-metric', 'Root A'); select(page, 'tool-metric', 'A')
    hold(page, '/tool-metric?query=Root+A&offset=0&limit=100', old_error)
    track(page, 'library-read'); page.locator('#library-read').click()
    page.wait_for_function('()=>window.__held')
    expect(page.locator('#native-objects > tr')).to_have_count(0)
    expect(page.locator('#catalog-preview')).to_be_disabled()
    page.locator('#library-query').fill('Root B'); page.locator('#library-read').click()
    expect(page.locator('#native-objects > tr')).to_have_count(1)
    select(page, 'tool-metric', 'B')
    page.locator('#catalog-preview').click(); expect(page.locator('#catalog-import')).to_be_enabled()
    release_reply(page)
    expect(page.locator('#native-objects > tr')).to_have_count(1)
    expect(page.get_by_role('checkbox', name='Objekt tool-metric / B auswählen', exact=True)).to_be_checked()
    expect(page.locator('#catalog-selection-state')).to_contain_text('2 vollständige Objekte')
    expect(page.locator('#catalog-import')).to_be_enabled()
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert snapshot(store)['sha256'] == before


def test_detached_row_and_old_selection_controls_cannot_act_after_aba(selection_ui):
    from playwright.sync_api import expect
    page, store, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'tool-metric', 'Root A'); select(page, 'tool-metric', 'A')
    page.evaluate("""()=>{const row=document.querySelector('#native-objects tr');
        window.__oldCheck=row.querySelector('input');window.__oldDetails=row.querySelectorAll('button')[0];
        window.__oldClone=row.querySelectorAll('button')[1];
        window.__oldRemove=document.querySelector('#catalog-selection-list button');} """)
    page.locator('#catalog-clear-selection').click()
    read(page, 'tool-metric', 'Root B'); select(page, 'tool-metric', 'B')
    read(page, 'tool-metric', 'Root A'); select(page, 'tool-metric', 'A')
    page.locator('#tab-prepare').click();page.locator('#name').fill('Unsaved protected editor')
    page.locator('#tab-manage').click()
    current_details = page.locator('#details').inner_text()
    page.evaluate("""async()=>{window.__oldCheck.checked=false;await window.__oldCheck.onchange();
        await window.__oldDetails.onclick();await window.__oldClone.onclick();await window.__oldRemove.onclick();} """)
    expect(page.locator('#catalog-selection-state')).to_contain_text('2 vollständige Objekte')
    expect(page.get_by_role('checkbox', name='Objekt tool-metric / A auswählen', exact=True)).to_be_checked()
    expect(page.locator('#clone-box')).not_to_be_visible()
    assert page.locator('#details').inner_text() == current_details
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved protected editor')
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('change', ['folder', 'nx_version', 'building', 'validated'])
def test_release_context_change_is_not_just_an_id_comparison(selection_ui, change):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'tool-metric', 'Root A'); select(page, 'tool-metric', 'A')
    page.evaluate("""([id,change])=>{const source=releases.find(value=>value.id===id);
        if(change==='folder')source.folder+='-synthetic-metadata-only';
        else if(change==='nx_version')source.nx_version='2406';else source.status=change;
        NXNative.syncReleaseSelections();} """, [target['id'], change])
    if change == 'validated':
        expect(page.locator('#native-objects > tr')).to_have_count(1)
        expect(page.locator('#catalog-selection-state')).to_contain_text('1 vollständige Objekte')
    else:
        expect(page.locator('#native-objects > tr')).to_have_count(0)
        expect(page.locator('#catalog-selection-state')).to_contain_text('0 vollständige Objekte')
        expect(page.locator('#native-library option')).to_have_count(0)
        expect(page.locator('#catalog-import')).to_be_disabled()
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('old_error', [False, True])
def test_reread_failure_invalidates_pending_preview_even_if_reply_arrives_later(selection_ui, old_error):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'tool-metric', 'Root A'); select(page, 'tool-metric', 'A')
    hold(page, '/api/v1/releases/' + target['id'] + '/catalog-preview', old_error)
    track(page, 'catalog-preview');page.locator('#catalog-preview').click()
    page.wait_for_function('()=>window.__held')
    page.route('**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?*',
        lambda route: route.fulfill(status=503, json={'detail': 'Synthetic unavailable'}))
    page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('Bibliothek nicht aktualisiert')
    release_reply(page)
    expect(page.locator('#catalog-objects > tr')).to_have_count(0)
    expect(page.locator('#catalog-preview')).to_be_disabled()
    expect(page.locator('#catalog-import')).to_be_disabled()
    expect(page.locator('#catalog-selection-state')).to_contain_text('1 vollständige Objekte')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert snapshot(store)['sha256'] == before


def test_changed_file_pin_discards_selection_instead_of_rebinding_it_silently(selection_ui):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'tool-metric', 'Root A'); select(page, 'tool-metric', 'A')

    def changed(route):
        response = route.fetch();data = response.json();data['input_sha256'] = '0' * 64
        route.fulfill(response=response, json=data)

    page.route('**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?*', changed)
    page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('ausgewählte DAT-Datei SHA-geprüft')
    expect(page.locator('#message')).to_contain_text('Alte Objektauswahl verworfen')
    expect(page.locator('#catalog-selection-state')).to_contain_text('0 vollständige Objekte')
    expect(page.get_by_role('checkbox', name='Objekt tool-metric / A auswählen', exact=True)).not_to_be_checked()
    expect(page.locator('#catalog-import')).to_be_disabled()
    assert snapshot(store)['sha256'] == before


def test_full_large_groups_new_columns_and_readonly_long_keys_are_not_truncated(selection_ui):
    from playwright.sync_api import expect
    import json
    page, store, _, _, source = selection_ui
    long_key = 'L' * 1025
    text = '#CLASS GROUP\nFORMAT LIBRF SEQ NEW_COLUMN __proto__\n' + ''.join(
        f'DATA |A|{index}|new value {index}|literal\n' for index in range(1201))
    text += '#CLASS GROUP\nFORMAT NAME TEXT\nDATA |A|🔧 extended FORMAT\n'
    text += 'DATA |' + long_key + '|Readable, but outside current selection contract\n#END_DATA\n'
    write(source, SPECS['tool-metric']['path'], text)
    target = store.import_release('test', '2512', 'Synthetic complete large groups', 'fixture')
    before = snapshot(store)['sha256']
    manifest = store.manifest(store.release_folder(target['id']))
    page.locator('#refresh').click()
    expect(page.locator('#library-release option[value="' + target['id'] + '"]')).to_have_count(1)
    page.locator('#library-release').select_option(target['id']);page.locator('#library-inventory').click()
    expect(page.locator('#catalog-libraries input')).to_have_count(2)
    read(page, 'tool-metric')
    expect(page.locator('#native-objects > tr')).to_have_count(2)
    first = page.locator('#native-objects > tr').first
    expect(first).to_contain_text('1202 Zeilen')
    first.get_by_role('button', name='Datensatz ansehen', exact=True).click()
    table = NativeTable(text)
    assert json.loads(page.locator('#details').inner_text()) == native_cam('tool-metric', '2512', table, table.groups()['A'])
    select(page, 'tool-metric', 'A')
    last = page.locator('#native-objects > tr').last
    expect(last.locator('input')).to_be_disabled()
    expect(last.get_by_role('button', name='Neuen Entwurf ableiten')).to_be_disabled()
    last.get_by_role('button', name='Datensatz ansehen', exact=True).click()
    assert json.loads(page.locator('#details').inner_text()) == native_cam('tool-metric', '2512', table, table.groups()[long_key])
    expect(page.locator('#catalog-selection-state')).to_contain_text('1 vollständige Objekte')
    assert snapshot(store)['sha256'] == before
    assert store.manifest(store.release_folder(target['id'])) == manifest


def test_failed_unselected_library_does_not_block_other_selection_or_whole_mode(selection_ui):
    from playwright.sync_api import expect
    page, store, target, *_ = selection_ui
    before = snapshot(store)['sha256']
    read(page, 'holder-metric'); select(page, 'holder-metric', 'KEY_0000')
    page.route('**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?*',
        lambda route: route.fulfill(status=503, json={'detail': 'Synthetic unavailable'}))
    page.locator('#native-library').select_option('tool-metric');page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('Bibliothek nicht aktualisiert')
    page.locator('#catalog-preview').click();expect(page.locator('#catalog-import')).to_be_enabled()
    expect(page.locator('#catalog-objects > tr')).to_have_count(1)
    page.locator('#catalog-scope').select_option('libraries')
    page.locator('#catalog-preview').click();expect(page.locator('#catalog-import')).to_be_enabled()
    expect(page.locator('#catalog-state')).to_contain_text('508 neue Objekte')
    assert snapshot(store)['sha256'] == before
