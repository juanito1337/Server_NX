"""Prepared-result preview contract and visible states; fake models, isolated Edge."""
import os

import pytest

from nxserver.results import Results
from nxserver.libraries import SPECS, NativeTable, native_cam
from test_requests_browser import ui, hold, track, release as release_reply
from test_result_jobs_browser import prepare
from test_results import bundle

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated prepared-result previews.')


@pytest.fixture
def prepared_ui(ui, request):
    from playwright.sync_api import expect
    page, store, *_ = ui
    target, tool, job, packet = prepare(page, store)
    if getattr(request, 'param', None) == 'missing_reference':
        def missing(value, _):
            value['objects'] = value['objects'][:1]
            value['objects'][0]['cam']['records'][0]['values']['HLDREF'] = 'MISSING_HOLDER'
        packet, _ = bundle(job, missing)
        page.locator('#result-file').set_input_files({'name': 'prepared-result.zip', 'mimeType': 'application/zip', 'buffer': packet})
    elif getattr(request, 'param', None) == 'reuse_catalog':
        _, manifest = bundle(job)
        store.save_resource({'kind': 'holder', 'name': 'Existing related holder', 'cam': manifest['objects'][1]['cam'],
            'cam_status': 'draft', 'asset_ids': [], 'erp': None}, 'fixture')
        page.locator('#refresh').click(); expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    elif getattr(request, 'param', None) == 'reuse_standard':
        standard = NativeTable.read(store.release_folder(target['id']) / SPECS['holder-metric']['path'])
        def reuse(value, _):
            value['objects'][0]['cam']['records'][0]['values']['HLDREF'] = 'STDH'
            value['objects'][1]['cam'] = native_cam('holder-metric', '2512', standard, standard.groups()['STDH'])
        packet, _ = bundle(job, reuse)
        page.locator('#result-file').set_input_files({'name': 'prepared-result.zip', 'mimeType': 'application/zip', 'buffer': packet})
    page.locator('#result-upload').click()
    expect(page.locator('#result-state')).to_contain_text('Paket SHA-geprüft gespeichert', timeout=20000)
    page.locator('#result-target').select_option(target['id'])
    result = Results(store).list(job['id'])[0]
    expected = {'resources': store.resources(), 'audit': store.audit_entries()}
    yield page, store, target, tool, job, result, expected
    assert store.resources() == expected['resources'] and store.audit_entries() == expected['audit']
    assert next(item for item in store.resources() if item['id'] == tool['id']) == tool
    assert Results(store).get(result['id'])['applied'] is None and store.active()['release_id'] is None


@pytest.mark.parametrize('field', ['apply_type', 'archive_sha256', 'resource_id', 'expected_revision', 'preview_hash',
    'automatic_cam_approval', 'geometry_checks_reset', 'cam_status_after_apply', 'files', 'actions', 'references',
    'catalog_revisions', 'schema_hashes', 'target_release_id', 'contradictory_conflict',
    'file_sha', 'file_path', 'file_duplicate', 'invalid_action', 'missing_parent', 'duplicate_action',
    'foreign_reuse_owner', 'catalog_revision_drift', 'catalog_duplicate', 'reference_approval',
    'warning_count', 'unreported_reference_error', 'schema_hash', 'wrong_archive_hash'])
def test_invalid_preview_cannot_enable_apply_or_leave_success_rows(prepared_ui, field):
    from playwright.sync_api import expect
    page, *_ = prepared_ui
    def malformed(route):
        response = route.fetch(); value = response.json()
        if field == 'apply_type': value['can_apply'] = 'false'
        elif field == 'resource_id': value[field] = '00000000-0000-0000-0000-000000000000'
        elif field == 'target_release_id': value[field] = '00000000-0000-0000-0000-000000000000'
        elif field == 'expected_revision': value[field] += 1
        elif field in ('automatic_cam_approval', 'geometry_checks_reset'): value[field] = not value[field]
        elif field == 'cam_status_after_apply': value[field] = 'approved'
        elif field == 'contradictory_conflict': value['conflicts'] = [{'reason': 'Synthetic unresolved conflict'}]
        elif field == 'file_sha': value['files'][0]['sha256'] = '0' * 64
        elif field == 'file_path': value['files'][0]['path'] = 'renamed.prt'
        elif field == 'file_duplicate': value['files'].append(value['files'][0])
        elif field == 'invalid_action': value['actions'][0]['mode'] = 'overwrite_standard'
        elif field == 'missing_parent': value['actions'] = value['actions'][1:]
        elif field == 'duplicate_action': value['actions'].append(value['actions'][0])
        elif field == 'foreign_reuse_owner':
            gauge = next(item for item in prepared_ui[1].resources() if item['kind'] == 'gauge')
            value['actions'][1].update(mode='reuse_catalog', resource_id=gauge['id'], revision=gauge['revision'])
        elif field == 'catalog_revision_drift': value['catalog_revisions'][0]['revision'] += 1
        elif field == 'catalog_duplicate': value['catalog_revisions'].append(value['catalog_revisions'][0])
        elif field == 'reference_approval': value['references']['automatic_geometry_validation'] = True
        elif field == 'warning_count': value['references']['warning_rows'] += 1
        elif field == 'unreported_reference_error': value['references']['errors'] = [{'reason': 'Undeclared reference conflict'}]
        elif field == 'schema_hash': value['schema_hashes']['tool-metric'] = 'not-a-hash'
        elif field == 'wrong_archive_hash': value['archive_sha256'] = '0' * 64
        else: value.pop(field)
        route.fulfill(response=response, json=value)
    page.route('**/geometry-results/*/preview', malformed)
    track(page, 'result-preview'); page.locator('#result-preview').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-state')).to_contain_text('Keine vollständige aktuelle Ergebnisvorschau')
    expect(page.locator('#result-actions tr')).to_have_count(0)
    expect(page.locator('#message')).to_contain_text('Ergebnisvorschauvertrag')


def test_selection_change_removes_previous_actions_and_approval(prepared_ui):
    from playwright.sync_api import expect
    page, *_ = prepared_ui
    page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_enabled()
    expect(page.locator('#result-actions tr')).to_have_count(2)
    page.locator('#result-target').select_option('')
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-actions tr')).to_have_count(0)
    expect(page.locator('#result-state')).to_contain_text('Auswahl geändert')
    expect(page.locator('#result-files tr')).to_have_count(0)
    expect(page.locator('#details')).to_contain_text('not_verified')


@pytest.mark.parametrize('old_error', [False, True])
def test_target_selection_aba_discards_late_preview_and_error(prepared_ui, old_error):
    from playwright.sync_api import expect
    page, _, target, *_ = prepared_ui
    hold(page, '/preview', old_error); track(page, 'result-preview')
    page.locator('#result-preview').click(); page.wait_for_function('()=>window.__held')
    page.locator('#result-target').select_option(''); page.locator('#result-target').select_option(target['id'])
    release_reply(page)
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-actions tr')).to_have_count(0)
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    expect(page.locator('#result-state')).to_contain_text('Auswahl geändert')


def test_preview_failure_clears_old_success_and_shows_retry_state(prepared_ui):
    from playwright.sync_api import expect
    page, *_ = prepared_ui
    page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_enabled()
    page.route('**/geometry-results/*/preview', lambda route: route.fulfill(status=503, json={'detail': 'Synthetic preview unavailable'}))
    page.locator('#result-preview').click()
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-actions tr')).to_have_count(0)
    expect(page.locator('#result-state')).to_contain_text('Keine vollständige aktuelle Ergebnisvorschau')
    expect(page.locator('#details')).to_contain_text('not_verified')
    page.unroute('**/geometry-results/*/preview')
    page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_enabled()


def test_complete_preview_shows_original_component_paths_sizes_and_hashes(prepared_ui):
    from playwright.sync_api import expect
    page, _, _, _, _, result, _ = prepared_ui
    page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_enabled()
    expect(page.locator('#result-files tr')).to_have_count(2)
    for item in result['manifest']['files']:
        row = page.locator('#result-files tr').filter(has_text=item['path'])
        expect(row).to_contain_text(str(item['size'])); expect(row).to_contain_text(item['sha256'])
    expect(page.locator('#result-file-state')).to_contain_text('2 PRT-Komponenten')
    expect(page.locator('#result-state')).to_contain_text('ungeprüft')


@pytest.mark.parametrize('prepared_ui', ['missing_reference'], indirect=True)
def test_valid_conflicted_preview_still_shows_files_and_precise_reference(prepared_ui):
    from playwright.sync_api import expect
    page, *_ = prepared_ui
    page.locator('#result-preview').click()
    expect(page.locator('#result-state')).to_contain_text('Konflikte')
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-actions')).to_contain_text('Missing library reference')
    expect(page.locator('#result-files tr')).to_have_count(2)
    expect(page.locator('#result-file-state')).to_contain_text('1 Konflikte')
    expect(page.locator('#message')).not_to_contain_text('Ergebnisvorschauvertrag')


@pytest.mark.parametrize('prepared_ui,expected_action', [('reuse_catalog', 'Bestehende Stammdaten unverändert nutzen'),
    ('reuse_standard', 'Standard unverändert nutzen')], indirect=['prepared_ui'])
def test_legitimate_reuse_is_visible_and_does_not_modify_existing_objects(prepared_ui, expected_action):
    from playwright.sync_api import expect
    page, *_ = prepared_ui
    page.locator('#result-preview').click(); expect(page.locator('#result-apply')).to_be_enabled()
    expect(page.locator('#result-actions')).to_contain_text(expected_action)
    expect(page.locator('#result-files tr')).to_have_count(2)


@pytest.mark.parametrize('when', ['after_preview', 'during_preview'])
def test_catalog_refresh_invalidates_old_preview_without_altering_input(prepared_ui, when):
    from playwright.sync_api import expect
    page, store, _, _, _, result, expected = prepared_ui
    if when == 'during_preview': hold(page, '/preview'); track(page, 'result-preview')
    page.locator('#result-preview').click()
    if when == 'during_preview': page.wait_for_function('()=>window.__held')
    else: expect(page.locator('#result-apply')).to_be_enabled()
    # Update a separate, synthetic gauge, not the pinned STEP tool. Refresh must
    # invalidate ownership/revision evidence even when the tool itself is unchanged.
    gauge = next(item for item in store.resources() if item['kind'] == 'gauge')
    body = {key: value for key, value in gauge.items() if key not in ('id', 'revision', 'updated_at')}
    body['name'] = 'New synthetic catalog revision'
    newer = store.save_resource(body, 'fixture', gauge['id'], gauge['revision'])
    expected.update(resources=store.resources(), audit=store.audit_entries())
    page.locator('#refresh').click(); expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    if when == 'during_preview': release_reply(page)
    expect(page.locator('#result-apply')).to_be_disabled()
    expect(page.locator('#result-actions tr')).to_have_count(0)
    expect(page.locator('#result-files tr')).to_have_count(0)
    expect(page.locator('#result-state')).to_contain_text('Stammdatenbestand oder Zielstand geändert')
    expect(page.locator('#result-select')).to_have_value(result['id'])
    expect(page.locator('#result-key')).to_have_value('CUSTOM')
    assert next(item for item in store.resources() if item['id'] == newer['id']) == newer
