"""Export controls belong to one visible report; isolated synthetic data only."""
import copy
import json
import os

import pytest

from nxserver.integrity import snapshot
from nxserver.libraries import NativeTable, SPECS, native_cam
from nxserver.store import canonical
from test_libraries import simple, write
from test_requests_browser import ui, payload
from test_preview_context_browser import open_export
from test_exportfiles_browser import files_ui, load as load_files
from test_exportview_browser import export_ui, objects as load_objects

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated daily-export context tests; no NX.')


@pytest.fixture
def columns_ui(ui):
    from playwright.sync_api import expect
    page, store, _, _ = ui
    identifier = 'holder-metric'
    text = '#CLASS SAMPLE\nFORMAT LIBRF DESCR FUTURE_A FUTURE_B\nDATA |STD|Synthetic stock|stock A|stock B\n#END_DATA\n'
    write(store.sources['test'], SPECS[identifier]['path'], text)
    first = store.import_release('test', '2512', 'Synthetic export parent A', 'fixture')
    other = store.import_release('test', '2512', 'Synthetic export parent B', 'fixture')
    old = NativeTable(simple('CUSTOM_CONTEXT', 'Synthetic old-schema holder'))
    resource = store.save_resource({'kind': 'holder', 'name': 'Synthetic export holder', 'cam_status': 'approved',
        'cam': native_cam(identifier, '2406', old, old.records), 'asset_ids': [], 'erp': None}, 'fixture')
    page.locator('#refresh').click()
    expect(page.locator('#resources')).to_contain_text(resource['name'])
    open_export(page, first)
    page.locator('#export-preview').click()
    expect(page.locator('#export-column-fields fieldset')).to_have_count(2)
    return page, store, first, other, resource


def capture_control(request, kind):
    from playwright.sync_api import expect
    if kind in ('column', 'column-input', 'conflict', 'review'):
        page, store, target, other, resource = request.getfixturevalue('columns_ui')
        if kind == 'conflict':
            table = NativeTable('#CLASS REMOVED_SYNTHETIC\nFORMAT LIBRF DESCR\nDATA |REMOVED_CONTEXT|Synthetic conflict\n#END_DATA\n')
            resource = store.save_resource({'kind': 'holder', 'name': 'Synthetic incompatible holder', 'cam_status': 'approved',
                'cam': native_cam('holder-metric', '2406', table, table.records), 'asset_ids': [], 'erp': None}, 'fixture')
        elif kind == 'review':
            # Deliberate metadata-only routing fixture, not a genuine migration
            # receipt or a Siemens/NX qualification.
            catalog = copy.deepcopy(target['catalog'])
            catalog['reference_reviews'] = {resource['id']: {'decisions': [
                {'field': 'MATREF', 'previous': 'SYNTHETIC_OLD', 'value': 'SYNTHETIC_NEW'}]}}
            with store.db() as db:
                db.execute('UPDATE releases SET catalog=? WHERE id=?', (canonical(catalog), target['id']))
        if kind != 'column':
            page.locator('#refresh').click()
            expect(page.locator('#resources')).to_contain_text(resource['name'])
            open_export(page, target)
            page.locator('#export-preview').click()
        selector = {'column': '#export-column-fields input[type="checkbox"]',
                    'column-input': '#export-column-fields input:not([type="checkbox"])',
                    'conflict': '#export-conflicts button', 'review': '#export-reference-reviews button'}[kind]
        expect(page.locator(selector)).to_have_count(2 if kind.startswith('column') else 1)
        page.locator(selector).first.evaluate('(node)=>globalThis.__oldExportControl=node')
        if kind.startswith('column'):
            page.evaluate('''()=>{const box=__oldExportControl.closest('fieldset');globalThis.__oldExportInput=box.querySelector('input:not([type="checkbox"])');globalThis.__oldExportCheck=box.querySelector('input[type="checkbox"]');__oldExportInput.value='STALE_EXPORT_VALUE';}''')
    elif kind == 'files':
        page, store, target, resource, *_ = request.getfixturevalue('files_ui')
        other = None
        page.locator('#export-files-change').select_option('changed')
        load_files(page, 2)
        page.locator('#export-files-items').get_by_role('button', name='Stammdaten öffnen').first.evaluate('(node)=>globalThis.__oldExportControl=node')
    else:
        page, store, target, resource = request.getfixturevalue('export_ui')
        other = None
        load_objects(page)
        page.locator('#export-native-objects').get_by_role('button', name='Vorher-/Nachher-Werte').first.evaluate('(node)=>globalThis.__oldExportControl=node')
    return page, store, target, other, resource


def invoke_old(page, kind):
    page.evaluate('''async(kind)=>{
        if(kind.startsWith('column')){__oldExportCheck.checked=true;if(kind==='column-input')await __oldExportControl.oninput();else await __oldExportControl.onchange();}
        else await __oldExportControl.onclick();
    }''', kind)


@pytest.mark.parametrize('kind', ['column', 'conflict', 'review', 'files', 'native'])
def test_closed_export_controls_do_not_change_later_inputs(request, kind):
    from playwright.sync_api import expect
    page, store, *_ = capture_control(request, kind)
    before_store = snapshot(store)['sha256']
    page.locator('#export-close').click()
    page.locator('#tab-prepare').click()
    page.locator('#name').fill('Later unsaved export-context draft')
    page.locator('#editor-section details.technical').evaluate('(node)=>{node.open=true;}')
    page.locator('#cam').fill('{')
    before_defaults = page.locator('#export-defaults').input_value()
    before_title = page.locator('#export-native-title').text_content()
    invoke_old(page, kind)
    expect(page.locator('#name')).to_have_value('Later unsaved export-context draft')
    expect(page.locator('#cam')).to_have_value('{')
    expect(page.locator('#export-defaults')).to_have_value(before_defaults)
    expect(page.locator('#export-native-title')).to_have_text(before_title)
    assert snapshot(store)['sha256'] == before_store and store.active()['release_id'] is None


def view_state(page):
    return page.evaluate('''()=>({name:document.getElementById('name').value,cam:document.getElementById('cam').value,
        defaults:document.getElementById('export-defaults').value,workspace,
        title:document.getElementById('export-native-title').textContent,
        side:document.getElementById('export-native-side').value,
        detail:document.getElementById('export-native-detail').hidden})''')


def unsaved_editor(page):
    page.locator('#tab-prepare').click()
    page.locator('#name').fill('Later untouched export draft')
    page.locator('#editor-section details.technical').evaluate('(node)=>{node.open=true;}')
    page.locator('#cam').fill('{')
    page.locator('#tab-manage').click()


@pytest.mark.parametrize('kind', ['column', 'column-input', 'conflict', 'review', 'files', 'native'])
@pytest.mark.parametrize('change', ['parent', 'parent_roundtrip', 'preview', 'tab', 'catalog', 'logout'])
def test_old_export_control_keeps_current_report_and_editor(request, kind, change):
    from playwright.sync_api import expect
    page, store, target, other, resource = capture_control(request, kind)
    unsaved_editor(page)
    if change in ('parent', 'parent_roundtrip'):
        other = other or next(item for item in store.releases() if item['id'] != target['id'])
        open_export(page, other)
        if change == 'parent_roundtrip':
            open_export(page, target)
    elif change == 'preview':
        page.locator('#export-preview').click()
        if kind.startswith('column'):
            selector = '#export-column-fields input[type="checkbox"]' if kind == 'column' else '#export-column-fields input:not([type="checkbox"])'
            page.wait_for_function('(selector)=>{const node=document.querySelector(selector);return node&&node!==__oldExportControl;}', arg=selector)
        elif kind in ('conflict', 'review'):
            selector = '#export-conflicts button' if kind == 'conflict' else '#export-reference-reviews button'
            page.wait_for_function('(selector)=>{const node=document.querySelector(selector);return node&&node!==__oldExportControl;}', arg=selector)
        elif kind == 'files':
            expect(page.locator('#export-create')).to_be_enabled()
            page.locator('#export-files-box').evaluate('(node)=>{node.open=true;}')
            page.locator('#export-files-change').select_option('changed')
            load_files(page, 2)
        else:
            expect(page.locator('#export-create')).to_be_enabled()
            page.locator('#export-native-box').evaluate('(node)=>{node.open=true;}')
            load_objects(page)
            page.locator('#export-native-side').select_option('before')
    elif change == 'catalog':
        body = payload(resource);body['name'] += ' newer revision'
        store.save_resource(body, 'fixture', resource['id'], resource['revision'])
        page.locator('#refresh').click()
        expect(page.locator('#resources')).to_contain_text(body['name'])
    elif change == 'logout':
        page.locator('#disconnect').click()
        expect(page.locator('#work')).to_be_hidden()
    else:
        page.locator('#tab-prepare').click()
    before_view, before_store = view_state(page), snapshot(store)['sha256']
    invoke_old(page, kind)
    assert view_state(page) == before_view
    assert snapshot(store)['sha256'] == before_store and store.active()['release_id'] is None


@pytest.mark.parametrize('kind', ['files', 'native'])
@pytest.mark.parametrize('change', ['filter_roundtrip', 'collapse'])
def test_old_comparison_row_ignores_new_filter_or_closed_details(request, kind, change):
    from playwright.sync_api import expect
    page, store, *_ = capture_control(request, kind)
    unsaved_editor(page)
    prefix = '#export-files-' if kind == 'files' else '#export-native-'
    # Editing the draft legitimately retires the earlier export preview.
    # Explicitly obtain a fresh index before testing filter/collapse retirement.
    page.locator('#export-preview').click()
    expect(page.locator('#export-create')).to_be_enabled()
    page.locator(prefix + 'box').evaluate('(node)=>{node.open=true;}')
    if kind == 'files':
        page.locator('#export-files-change').select_option('changed')
        load_files(page, 2)
        control = page.locator('#export-files-items').get_by_role('button', name='Stammdaten öffnen').first
    else:
        load_objects(page)
        control = page.locator('#export-native-objects').get_by_role('button', name='Vorher-/Nachher-Werte').first
        page.locator('#export-native-side').select_option('before')
    control.evaluate('(node)=>globalThis.__oldExportControl=node')
    assert control.evaluate('(node)=>node.isConnected')
    assert page.locator(prefix + 'box').evaluate('(node)=>node.open')
    if change == 'collapse':
        page.locator(prefix + 'box').evaluate('(node)=>{node.open=false;}')
        assert page.evaluate('globalThis.__oldExportControl.isConnected')
    else:
        page.locator(prefix + 'query').fill('no synthetic match')
        page.locator(prefix + 'query').fill('')
        if kind == 'files':
            load_files(page, 2)
        else:
            load_objects(page)
            page.locator('#export-native-side').select_option('before')
    before_view, before_store = view_state(page), snapshot(store)['sha256']
    invoke_old(page, kind)
    assert view_state(page) == before_view and snapshot(store)['sha256'] == before_store


@pytest.mark.parametrize('kind', ['conflict', 'review', 'files'])
def test_current_export_resource_link_still_opens_the_current_revision(request, kind):
    from playwright.sync_api import expect
    page, store, _, _, resource = capture_control(request, kind)
    before = snapshot(store)['sha256']
    page.evaluate('async()=>{await __oldExportControl.onclick();}')
    expect(page.locator('#panel-prepare')).to_be_visible()
    expect(page.locator('#name')).to_have_value(resource['name'])
    expect(page.locator('#editing')).to_contain_text('Revision ' + str(resource['revision']))
    assert snapshot(store)['sha256'] == before and store.active()['release_id'] is None


def test_two_current_column_decisions_still_export_exact_values(columns_ui):
    from playwright.sync_api import expect
    page, store, target, _, resource = columns_ui
    manifest = store.manifest(store.release_folder(target['id']))
    values = {'FUTURE_A': '__proto__ explicit test value', 'FUTURE_B': ''}
    for field, value in values.items():
        box = page.locator('#export-column-fields fieldset').filter(has=page.get_by_text('holder-metric · SAMPLE:1 · ' + field, exact=True))
        box.locator('input:not([type="checkbox"])').fill(value)
        box.get_by_role('checkbox').check()
    assert json.loads(page.locator('#export-defaults').input_value()) == {'holder-metric': {'SAMPLE:1': values}}
    expect(page.locator('#export-create')).to_be_disabled()
    page.locator('#export-preview').click()
    expect(page.locator('#export-create')).to_be_enabled()
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('#export-create').click()
    expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau', timeout=15000)
    child = next(item for item in store.releases() if item['parent_id'] == target['id'])
    groups = NativeTable.read(store.release_folder(child['id']) / SPECS['holder-metric']['path']).groups()
    assert all(groups['CUSTOM_CONTEXT'][0]['values'][field] == value for field, value in values.items())
    assert groups['STD'][0]['values']['FUTURE_A'] == 'stock A' and groups['STD'][0]['values']['FUTURE_B'] == 'stock B'
    assert store.manifest(store.release_folder(target['id'])) == manifest
    assert store.get_resource(resource['id'])['revision'] == 1 and child['test_launcher']
    assert store.active()['release_id'] is None


def test_current_native_index_allows_other_objects_after_selection_and_side_change(export_ui):
    from playwright.sync_api import expect
    page, store, target, resource = export_ui
    body = payload(resource);body['name'] = 'Second synthetic export group'
    for record in body['cam']['records']:
        record['values']['LIBRF'] = 'SECOND_CONTEXT'
    store.save_resource(body, 'fixture')
    page.locator('#refresh').click()
    expect(page.locator('#resources')).to_contain_text(body['name'])
    open_export(page, target);page.locator('#export-preview').click()
    expect(page.locator('#export-create')).to_be_enabled()
    page.locator('#export-native-box').evaluate('(node)=>{node.open=true;}')
    page.locator('#export-native-load').click()
    expect(page.locator('#export-native-objects tr')).to_have_count(2)
    before = snapshot(store)['sha256']
    rows = page.locator('#export-native-objects tr')
    rows.first.get_by_role('button', name='Vorher-/Nachher-Werte').click()
    page.locator('#export-native-side').select_option('before')
    rows.last.get_by_role('button', name='Vorher-/Nachher-Werte').click()
    expect(page.locator('#export-native-title')).to_contain_text('constructor')
    expect(page.locator('#export-native-side')).to_have_value('after')
    page.locator('#export-native-read').click()
    expect(page.locator('#export-native-rows > details')).to_have_count(20)
    assert snapshot(store)['sha256'] == before and store.active()['release_id'] is None
