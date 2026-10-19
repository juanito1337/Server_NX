"""Explicit new-column decisions for prepared results; synthetic NX libraries only."""
import copy
import uuid

import pytest

from nxserver.libraries import SPECS
from nxserver.results import Results
from nxserver.safety import DomainError
from nxserver.integrity import scan
from nxserver.upgrades import LIVE
from test_results import result_pilot, bundle, table, HEADERS
from test_libraries import write


@pytest.fixture
def column_pilot(result_pilot):
    client, store, old_target, tool, job = result_pilot
    source = store.sources['test']
    write(source, SPECS['tool-metric']['path'], table(['LIBRF', 'DESCR', 'HLDREF', 'NEW_A', 'NEW_B'], [('STD', 'Stock', '', '0', '')]))
    write(source, SPECS['holder-metric']['path'], table(['LIBRF', 'DESCR', 'NEW_HOLDER'], [('STDH', 'Stock', '')]))
    target = store.import_release('test', '2512', 'Synthetic new FORMAT columns', 'fixture')
    content, _ = bundle(job); result = Results(store).upload(job['id'], content, 'fixture')
    values = {'tool-metric': {'CUSTOM': {'SAMPLE:1': {'NEW_A': '17.5', 'NEW_B': ''}}},
              'holder-metric': {'HLD_CUSTOM': {'SAMPLE:1': {'NEW_HOLDER': 'manual'}}}}
    return client, store, target, tool, job, result, values, old_target


def test_preview_lists_every_new_column_without_guessing_values_or_writing(column_pilot):
    client, store, target, tool, job, result, _, _ = column_pilot
    before = store.audit_entries(); resources = store.resources()
    report = Results(store).preview(result['id'], target['id'])[0]
    assert not report['can_apply'] and report['column_decisions_contract'] == 1
    assert {(item['library'], item['key'], item['block_id'], item['field']) for item in report['column_decisions']} == {
        ('tool-metric', 'CUSTOM', 'SAMPLE:1', 'NEW_A'), ('tool-metric', 'CUSTOM', 'SAMPLE:1', 'NEW_B'),
        ('holder-metric', 'HLD_CUSTOM', 'SAMPLE:1', 'NEW_HOLDER')}
    assert all(item['confirmed'] is False and item['value'] is None and item['affected_rows'] == 1 for item in report['column_decisions'])
    assert store.resources() == resources and store.audit_entries() == before


def test_explicit_values_and_explicit_empty_survive_preview_apply_and_original_packet(column_pilot):
    client, store, target, tool, job, result, values, _ = column_pilot
    service = Results(store); original = service.archive(result['id']).read_bytes(); manifest = store.manifest(store.release_folder(target['id']))
    report = service.preview(result['id'], target['id'], column_values=values)[0]
    assert report['can_apply'], report['conflicts']
    assert report['column_values'] == values and all(item['confirmed'] for item in report['column_decisions'])
    applied = service.apply(result['id'], target['id'], report['preview_hash'], 'fixture', column_values=values)
    parent = next(item for item in store.resources() if item['id'] == tool['id'])
    assert parent['cam']['records'][0]['values'] == {'LIBRF': 'CUSTOM', 'DESCR': 'Prepared', 'HLDREF': 'HLD_CUSTOM', 'NEW_A': '17.5', 'NEW_B': ''}
    holder = next(item for item in store.resources() if item['kind'] == 'holder')
    assert holder['cam']['records'][0]['values']['NEW_HOLDER'] == 'manual'
    assert applied['column_values'] == values and parent['cam_status'] == holder['cam_status'] == 'draft'
    assert service.archive(result['id']).read_bytes() == original and store.manifest(store.release_folder(target['id'])) == manifest
    assert service.apply(result['id'], target['id'], report['preview_hash'], 'fixture', column_values=values) == applied
    assert scan(store)['error_count'] == 0 and store.active()['release_id'] is None


def test_changed_decision_never_reuses_reviewed_hash_or_applied_history(column_pilot):
    _, store, target, _, _, result, values, _ = column_pilot; service = Results(store)
    report = service.preview(result['id'], target['id'], column_values=values)[0]
    changed = copy.deepcopy(values); changed['tool-metric']['CUSTOM']['SAMPLE:1']['NEW_A'] = '18'
    with pytest.raises(DomainError): service.apply(result['id'], target['id'], report['preview_hash'], 'fixture', column_values=changed)
    service.apply(result['id'], target['id'], report['preview_hash'], 'fixture', column_values=values)
    with pytest.raises(DomainError): service.apply(result['id'], target['id'], report['preview_hash'], 'fixture', column_values=changed)


@pytest.mark.parametrize('field', ['existing_field', 'unknown_library', 'unknown_object', 'unknown_block', 'unknown_field', 'number', 'separator', 'newline', 'null', 'space'])
def test_only_explicit_new_fields_accept_text_decisions(column_pilot, field):
    _, store, target, _, _, result, values, _ = column_pilot; changed = copy.deepcopy(values)
    if field == 'existing_field': changed['tool-metric']['CUSTOM']['SAMPLE:1']['DESCR'] = 'Hidden overwrite'
    elif field == 'unknown_library': changed['missing'] = changed.pop('tool-metric')
    elif field == 'unknown_object': changed['tool-metric']['MISSING'] = changed['tool-metric'].pop('CUSTOM')
    elif field == 'unknown_block': changed['tool-metric']['CUSTOM']['MISSING:1'] = changed['tool-metric']['CUSTOM'].pop('SAMPLE:1')
    elif field == 'unknown_field': changed['tool-metric']['CUSTOM']['SAMPLE:1']['UNKNOWN'] = 'x'
    else: changed['tool-metric']['CUSTOM']['SAMPLE:1']['NEW_A'] = {'number': 17, 'separator': 'a|b', 'newline': 'a\nb', 'null': None, 'space': ' 17 '}[field]
    before = store.resources(); audit = store.audit_entries()
    with pytest.raises(DomainError): Results(store).preview(result['id'], target['id'], column_values=changed)
    assert store.resources() == before and store.audit_entries() == audit


def test_background_api_persists_decisions_and_exact_retry_never_duplicates(column_pilot):
    client, store, target, tool, job, result, values, _ = column_pilot
    body = {'target_release_id': target['id'], 'column_values': values}
    response = client.post('/api/v1/geometry-results/' + result['id'] + '/preview', headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    plan = response.json(); key = str(uuid.uuid4()); headers = {**HEADERS, 'Idempotency-Key': key}
    request = {**body, 'result_id': result['id'], 'preview_hash': plan['preview_hash']}
    accepted = client.post('/api/v1/release-jobs/result_adopt', headers=headers, json=request)
    assert accepted.status_code == 202, accepted.text
    LIVE[accepted.json()['id']].result(timeout=30)
    done = client.get('/api/v1/migrations/' + accepted.json()['id'], headers=HEADERS).json()
    assert done['status'] == 'completed', done
    assert done['request']['column_values'] == values
    before = store.resources(); audit = store.audit_entries()
    assert client.post('/api/v1/release-jobs/result_adopt', headers=headers, json=request).json() == accepted.json()
    assert store.resources() == before and store.audit_entries() == audit and scan(store)['error_count'] == 0


def test_old_requests_and_previews_do_not_gain_empty_decision_fields(column_pilot):
    client, store, _, _, _, result, _, old = column_pilot
    service = Results(store)
    original = service.preview(result['id'], old['id'])[0]
    assert service.preview(result['id'], old['id'], column_values={})[0] == original
    assert 'column_values' not in original and 'column_decisions' not in original
    key = str(uuid.uuid4()); headers = {**HEADERS, 'Idempotency-Key': key}
    body = {'result_id': result['id'], 'target_release_id': old['id'], 'preview_hash': original['preview_hash']}
    accepted = client.post('/api/v1/release-jobs/result_adopt', headers=headers, json=body)
    assert accepted.status_code == 202, accepted.text
    LIVE[accepted.json()['id']].result(timeout=30)
    assert 'column_values' not in accepted.json()['request']
    assert client.post('/api/v1/release-jobs/result_adopt', headers=headers, json={**body, 'column_values': {}}).json() == accepted.json()


def test_same_object_format_decision_covers_all_rows_without_touching_existing_cells(column_pilot):
    _, store, target, _, job, _, values, _ = column_pilot
    def two_rows(manifest, _):
        row = copy.deepcopy(manifest['objects'][0]['cam']['records'][0]); row['values']['DESCR'] = 'Second explicit original row'
        manifest['objects'][0]['cam']['records'].append(row)
    content, _ = bundle(job, two_rows); result = Results(store).upload(job['id'], content, 'fixture')
    report, projected, _ = Results(store).preview(result['id'], target['id'], column_values=values)
    assert report['can_apply'], report['conflicts']
    assert [item['affected_rows'] for item in report['column_decisions'] if item['library'] == 'tool-metric'] == [2, 2]
    rows = projected[('tool-metric', 'CUSTOM')]['records']
    assert [row['values']['DESCR'] for row in rows] == ['Prepared', 'Second explicit original row']
    assert all(row['values']['NEW_A'] == '17.5' and row['values']['NEW_B'] == '' for row in rows)


def test_column_restore(column_pilot):
    from nxserver.backups import Backups, FUTURES
    from nxserver.store import Store
    _, store, target, _, _, result, values, _ = column_pilot
    service = Results(store); plan = service.preview(result['id'], target['id'], column_values=values)[0]
    service.apply(result['id'], target['id'], plan['preview_hash'], 'fixture', column_values=values)
    manager = Backups(store)
    def complete(job):
        FUTURES[job['id']].result(timeout=30)
        current = manager.get_job(job['id'])
        assert current['status'] == 'completed', current['message']
        return manager.report(job['id'])
    receipt = complete(manager.create('backup', 'fixture'))
    # Keep synthetic pytest/recovery paths short enough for the application's
    # real Windows path gate; do not weaken that gate to make a fixture pass.
    preview = manager.create('preview', 'fixture', {'target': 'local', 'name': 'R'}, receipt['id'])
    restore_plan = complete(preview)
    assert restore_plan['can_restore'], restore_plan['errors']
    report = complete(manager.create('restore', 'fixture', {'target': 'local', 'name': 'R',
        'preview_id': preview['id'], 'preview_hash': restore_plan['preview_hash'], 'accept_unsigned': True}, receipt['id']))
    restored = Store(report['target_folder'])
    assert Results(restored).get(result['id'])['applied']['column_values'] == values
    assert restored.resources() == store.resources() and scan(restored)['error_count'] == 0
