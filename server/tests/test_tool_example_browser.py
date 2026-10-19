"""Isolated native tool-example editor handoff; never real NX/share data."""
import os
import json

import pytest

from nxserver.integrity import snapshot
from nxserver.libraries import SPECS, NativeTable, native_cam
from test_filepackages_browser import package_ui
from test_filepackages import packages
from test_libraries import write
from test_requests_browser import hold, track, release as release_reply
from test_saves_browser import lose
from test_management import tool_payload

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated tool-example Edge tests.')


def example(page, target):
    return page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='NX-Werkzeugschema', exact=True)


def prepare_editor(page):
    page.locator('#tab-prepare').click()
    page.locator('#name').fill('Unsubmitted existing editor')
    page.locator('#tab-manage').click()
    return page.evaluate('editorSignature()')


@pytest.mark.parametrize('failure', ['version', 'value'])
def test_invalid_example_never_replaces_existing_editor(package_ui, failure):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    previous = prepare_editor(page);before = snapshot(store)['sha256']

    def corrupt(route):
        response = route.fetch();data = response.json()
        if failure == 'version': data['items'][0]['cam']['nx_version'] = '2406'
        else: data['items'][0]['cam']['records'][0]['values']['DESCR'] = 42
        route.fulfill(response=response, json=data)

    page.route('**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?offset=0&limit=1', corrupt)
    example(page, target).click()
    expect(page.locator('#message')).to_have_class('error')
    assert page.evaluate('editorSignature()') == previous
    expect(page.locator('#panel-manage')).to_be_visible()
    assert snapshot(store)['sha256'] == before


def test_pending_write_rejects_example_before_reading_or_resetting(package_ui):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    previous = prepare_editor(page);before = snapshot(store)['sha256'];reads = []
    page.on('request', lambda request: reads.append(request.url) if '/libraries/tool-metric?' in request.url else None)
    page.evaluate('()=>{NXSaves.blocksSave=()=>true;}')
    example(page, target).click()
    expect(page.locator('#message')).to_contain_text('Speichervorgang/Upload zuerst klären')
    assert page.evaluate('editorSignature()') == previous and not reads
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('failure', ['schema', 'envelope', 'group', 'missing', 'limit', 'manifest', 'spec', 'filtered'])
def test_structurally_inconsistent_examples_are_not_handed_to_editor(package_ui, failure):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    previous = prepare_editor(page);before = snapshot(store)['sha256']

    def corrupt(route):
        response = route.fetch();data = response.json()
        if failure == 'schema': data['schemas'][0]['schema_hash'] = '0' * 64
        elif failure == 'envelope': data['items'][0]['cam']['extra'] = 'not native CAM'
        elif failure == 'group': data['items'][0]['cam']['records'][0]['values']['LIBRF'] = 'OTHER'
        elif failure == 'missing': del data['items'][0]['cam']['records'][0]['values']['DIA']
        elif failure == 'limit': data['limit'] = 100
        elif failure == 'manifest': data['manifest_sha256'] = None
        elif failure == 'spec': data['id'] = 'holder-metric'
        else: data['filtered'] = 0;data['items'] = []
        route.fulfill(response=response, json=data)

    page.route('**/api/v1/releases/' + target['id'] + '/libraries/tool-metric?offset=0&limit=1', corrupt)
    example(page, target).click()
    expect(page.locator('#message')).to_have_class('error')
    assert page.evaluate('editorSignature()') == previous
    expect(page.locator('#panel-manage')).to_be_visible()
    assert snapshot(store)['sha256'] == before


def start_held_example(page, target, error=False):
    control = example(page, target);control.evaluate('(node)=>node.id="held-tool-example"')
    hold(page, '/libraries/tool-metric?offset=0&limit=1', error);track(page, 'held-tool-example')
    control.click();page.wait_for_function('()=>window.__held')


@pytest.mark.parametrize('change', ['editor_aba', 'workspace_aba', 'source_folder', 'source_version', 'source_building', 'source_removed'])
@pytest.mark.parametrize('old_error', [False, True])
def test_intervening_intent_or_source_change_discards_late_example(package_ui, change, old_error):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    previous = prepare_editor(page);before = snapshot(store)['sha256']
    start_held_example(page, target, old_error)
    if change == 'editor_aba':
        page.locator('#name').evaluate("""node=>{const previous=node.value;node.value='Intervening typing';
            node.dispatchEvent(new Event('input',{bubbles:true}));node.value=previous;
            node.dispatchEvent(new Event('input',{bubbles:true}));} """)
    elif change == 'workspace_aba':
        page.locator('#tab-update').click();page.locator('#tab-manage').click()
    else:
        page.evaluate("""([id,change])=>{releases=releases.flatMap(source=>{
            if(source.id!==id)return [source];if(change==='source_removed')return [];
            return [{...source,...(change==='source_folder'?{folder:source.folder+'-synthetic-metadata'}:
                change==='source_version'?{nx_version:'2406'}:{status:'building'})}];});} """, [target['id'], change])
    page.evaluate('message("Newer interface state remains")')
    release_reply(page)
    assert page.evaluate('editorSignature()') == previous
    expect(page.locator('#panel-manage')).to_be_visible()
    expect(page.locator('#message')).to_have_text('Newer interface state remains')
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('blocked', ['save', 'upload'])
def test_write_started_while_example_loads_prevents_late_replacement(package_ui, blocked):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    previous = prepare_editor(page);before = snapshot(store)['sha256']
    start_held_example(page, target)
    page.evaluate("""value=>{if(value==='save')NXSaves.blocksSave=()=>true;else NXAssets.isUploading=()=>true;} """, blocked)
    release_reply(page)
    expect(page.locator('#message')).to_contain_text('Speichervorgang/Upload zuerst klären')
    assert page.evaluate('editorSignature()') == previous
    expect(page.locator('#panel-manage')).to_be_visible()
    assert snapshot(store)['sha256'] == before


def test_declining_explicit_replacement_preserves_entire_editor(package_ui):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    previous = prepare_editor(page);before = snapshot(store)['sha256'];prompts = []
    page.evaluate('()=>{window.__examplePrompts=[];window.confirm=text=>{window.__examplePrompts.push(text);return false;};}')
    control = example(page, target);control.evaluate('(node)=>node.id="declined-tool-example"')
    track(page, 'declined-tool-example');control.click();page.wait_for_function('()=>window.__oldActionFinished')
    prompts = page.evaluate('window.__examplePrompts')
    expect(page.locator('#name')).to_have_value('Unsubmitted existing editor')
    assert page.evaluate('editorSignature()') == previous and len(prompts) == 1
    assert 'NEUEN ungespeicherten Entwurf' in prompts[0]
    assert snapshot(store)['sha256'] == before


def test_invalid_raw_draft_can_be_replaced_after_explicit_confirmation(package_ui):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    page.locator('#tab-prepare').click();page.locator('#editor-section details.technical').evaluate('(node)=>node.open=true')
    page.locator('#cam').fill('{unfinished draft');page.locator('#tab-manage').click()
    before = snapshot(store)['sha256']
    example(page, target).click()
    expect(page.locator('#message')).to_contain_text('Vollständige native Werkzeuggruppe')
    expect(page.locator('#panel-prepare')).to_be_visible()
    assert json.loads(page.locator('#cam').input_value())['library_id'] == 'tool-metric'
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('status', ['building', 'failed'])
def test_incomplete_source_has_no_ui_or_method_bypass(package_ui, status):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    previous = prepare_editor(page);before = snapshot(store)['sha256'];reads = []
    page.on('request', lambda request: reads.append(request.url) if '/libraries/tool-metric?' in request.url else None)
    page.evaluate("""([id,status])=>{releases=releases.map(source=>source.id===id?{...source,status}:source);renderReleases();} """, [target['id'], status])
    control = example(page, target);expect(control).to_be_disabled()
    control.evaluate('(node)=>node.onclick()')
    expect(page.locator('#message')).to_contain_text('Vollständigen aktuellen NX-Datenstand')
    assert page.evaluate('editorSignature()') == previous and not reads
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('empty', [False, True])
def test_real_native_group_is_copied_whole_without_approval_or_server_write(package_ui, empty):
    from playwright.sync_api import expect
    page, store, _, source = package_ui
    text = '#CLASS MILL\nFORMAT LIBRF NAME RTYPE SEQ NEW_COLUMN __proto__\n'
    if not empty:
        text += ''.join(f'DATA |SOURCE|Label {index}|{index+1}|00{index}|🔧 value {index}|literal\n' for index in range(1002))
        text += '#CLASS MILL\nFORMAT NAME SEQ GRAPHICS\nDATA |SOURCE|010|existing_reference.prt\n'
    text += '#END_DATA\n'
    write(source, SPECS['tool-metric']['path'], text)
    target = store.import_release('test', '2512', 'Synthetic example native group', 'fixture')
    existing = None
    if not empty:
        asset = store.add_asset('prior_editor_note.pdf', b'Isolated attachment, not actual CAD.', 'fixture')
        value = tool_payload('PRIOR_EDITOR_TOOL');value['asset_ids'] = [asset['id']]
        value['erp'] = dict(company_id='fixture-company', system_id='fixture-tool', number='T001',
            description='Existing synthetic commercial link', unit='PCS', source_revision='1')
        existing = store.save_resource(value, 'fixture')
    page.locator('#refresh').click();expect(example(page, target)).to_have_count(1)
    if existing:
        page.locator('#resources tr').filter(has_text=existing['name']).get_by_role('button', name='Bearbeiten', exact=True).click()
    previous = prepare_editor(page);before = snapshot(store)['sha256']
    manifest = store.manifest(store.release_folder(target['id']))
    example(page, target).click()
    if empty:
        expect(page.locator('#message')).to_contain_text('enthält kein Beispiel')
        assert page.evaluate('editorSignature()') == previous
    else:
        expect(page.locator('#message')).to_contain_text('Vollständige native Werkzeuggruppe')
        # Preserve prototype-shaped DATA names across Playwright's own object
        # transport by reading the same JSON text the save path would send.
        value = json.loads(page.evaluate('JSON.stringify(editorBody())'));cam = value['cam'];expected = native_cam('tool-metric', '2512', NativeTable(text), NativeTable(text).records)
        assert cam == json.loads(page.locator('#cam').input_value())
        new_key = cam['records'][0]['values']['LIBRF']
        assert new_key.startswith('CUSTOM_') and len(new_key) == 39
        for row in expected['records']:
            schema = next(block for block in expected['schemas'] if block['id'] == row['block_id'])
            row['values']['LIBRF' if 'LIBRF' in schema['fields'] else 'NAME'] = new_key
        assert {key: value for key, value in cam.items() if key != 'records'} == {key: value for key, value in expected.items() if key != 'records'}
        assert len(cam['records']) == len(expected['records']) == 1003
        for index, (actual, original) in enumerate(zip(cam['records'], expected['records'])):
            assert actual == original, json.dumps(dict(row=index, differences={field: [actual['values'].get(field), original['values'].get(field)]
                for field in set(actual['values']) | set(original['values']) if actual['values'].get(field) != original['values'].get(field)}), ensure_ascii=True)
        assert value['kind'] == 'tool' and value['cam_status'] == 'draft' and value['erp'] is None and value['origin'] is None
        assert value['asset_ids'] == [] and value['file_bindings'] == [] and value['model_package'] is None
        assert value['geometry']['status'] == 'needs_preparation' and value['geometry']['checks'] == dict.fromkeys(value['geometry']['checks'], False)
        assert value['geometry']['asset_hashes'] == {} and value['geometry']['evidence'] == ''
        expect(page.locator('#panel-prepare')).to_be_visible()
    assert snapshot(store)['sha256'] == before
    assert store.manifest(store.release_folder(target['id'])) == manifest and store.active()['release_id'] is None


def test_actual_lost_save_receipt_is_not_discarded_by_tool_example(package_ui):
    from playwright.sync_api import expect
    page, store, target, _ = package_ui
    prepare_editor(page);page.locator('#tab-prepare').click();page.locator('#kind').select_option('gauge')
    lose(page, '/api/v1/resources');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    receipt = page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")')
    previous = page.evaluate('editorSignature()');before = snapshot(store)['sha256'];reads = []
    page.on('request', lambda request: reads.append(request.url) if '/libraries/tool-metric?' in request.url else None)
    page.locator('#tab-manage').click();example(page, target).click()
    expect(page.locator('#message')).to_contain_text('Speichervorgang/Upload zuerst klären')
    assert page.evaluate('editorSignature()') == previous and not reads
    assert page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")') == receipt and receipt
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('nx_version', ['2406', '2512'])
def test_example_is_saved_as_independent_full_native_draft(package_ui, nx_version):
    from playwright.sync_api import expect
    page, store, _, source = package_ui
    text = ('#CLASS COPY\nFORMAT LIBRF RTYPE SEQ __proto__ EXTRA\n'
        'DATA |SOURCE|1|001|literal root|new root\nDATA |SOURCE|2|010|literal child|new child\n'
        '#CLASS COPY\nFORMAT NAME DESCR\nDATA |SOURCE|Separate FORMAT block\n#END_DATA\n')
    write(source, SPECS['tool-metric']['path'], text)
    target = store.import_release('test', nx_version, 'Synthetic savable example', 'fixture')
    page.locator('#refresh').click();expect(example(page, target)).to_have_count(1)
    manifest = store.manifest(store.release_folder(target['id']))
    example(page, target).click()
    expect(page.locator('#message')).to_contain_text('Vollständige native Werkzeuggruppe')
    expected = json.loads(page.locator('#cam').input_value())
    page.locator('#name').fill('Independent complete tool example');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert len(store.resources()) == 1
    saved = store.resources()[0]
    assert saved['name'] == 'Independent complete tool example' and saved['revision'] == 1
    assert saved['cam'] == expected and len(saved['cam']['records']) == 3
    assert saved['cam']['nx_version'] == saved['geometry']['nx_version'] == nx_version
    assert saved['cam_status'] == 'draft' and saved['origin'] is None and saved['erp'] is None and saved['asset_ids'] == []
    assert saved['geometry']['status'] == 'needs_preparation'
    assert saved['cam']['records'][0]['values']['__proto__'] == 'literal root'
    assert store.manifest(store.release_folder(target['id'])) == manifest and store.active()['release_id'] is None
