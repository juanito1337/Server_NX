"""Read-only plans and ownership, using synthetic local data only."""
import copy
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.libraries import NativeTable, SPECS, legacy_cam_table, native_cam
from nxserver.safety import DomainError
from nxserver.store import Store, TOOL_DATABASE, canonical, now
from test_management import ADMIN, HEADERS, ERP_HEADERS, imported, pilot, tool_payload
from test_libraries import simple, write


def preview(client, release, defaults=None):
    response = client.post('/api/v1/releases/' + release['id'] + '/derive-preview', headers=HEADERS,
                           json={'defaults': defaults or {}})
    assert response.status_code == 200, response.text
    return response.json()


def derive(client, release, report, **values):
    return client.post('/api/v1/releases/' + release['id'] + '/derive', headers=HEADERS,
        json={'name': 'Reviewed export', 'defaults': report['defaults'], 'preview_hash': report['preview_hash'], **values})


def test_preview_is_read_only_and_actual_output_matches_reviewed_hash(pilot):
    client, store, source, _ = pilot
    original = imported(client)
    resource = client.post('/api/v1/resources', headers=HEADERS, json=tool_payload()).json()
    before, audit = store.manifest(Path(original['folder'])), store.audit_entries()
    folders = list((store.root / 'r').iterdir())
    report = preview(client, original)
    assert report['can_export'] and report['automatic_approval'] is False
    assert report['actions'][0]['action'] == 'created'
    assert report['catalog_revisions'] == [{'id': resource['id'], 'revision': 1}]
    assert store.manifest(Path(original['folder'])) == before
    assert store.audit_entries() == audit and list((store.root / 'r').iterdir()) == folders
    assert store.active()['generation'] == 0
    child = derive(client, original, report)
    assert child.status_code == 201, child.text
    child = child.json()
    assert child['catalog']['export_preview']['preview_hash'] == report['preview_hash']
    for output in report['outputs']:
        assert hashlib.sha256((Path(child['folder']) / output['path']).read_bytes()).hexdigest() == output['sha256']
    again = preview(client, child)
    assert again['actions'][0]['action'] == 'unchanged'
    assert NativeTable.read(source / TOOL_DATABASE).groups().keys() == {'STD_1'}


def test_catalog_revision_change_invalidates_preview_without_creating_folder(pilot):
    client, store, *_ = pilot
    original = imported(client)
    resource = client.post('/api/v1/resources', headers=HEADERS, json=tool_payload()).json()
    report = preview(client, original)
    updated = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS,
        json={**tool_payload(), 'name': 'Changed after preview', 'expected_revision': 1})
    assert updated.status_code == 200
    assert derive(client, original, report).status_code == 409
    assert len(store.releases()) == 1 and len(list((store.root / 'r').iterdir())) == 1
    assert derive(client, original, preview(client, original)).status_code == 201


def test_draft_is_explicitly_deferred_and_preserves_previous_export(pilot):
    client, store, *_ = pilot
    original = imported(client)
    payload = tool_payload()
    resource = client.post('/api/v1/resources', headers=HEADERS, json=payload).json()
    first = derive(client, original, preview(client, original)).json()
    payload['cam']['values']['DIA'] = '99'
    response = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS,
        json={**payload, 'cam_status': 'draft', 'expected_revision': 1})
    assert response.status_code == 200
    report = preview(client, first)
    assert report['can_export'] and report['actions'][0]['action'] == 'deferred'
    child = derive(client, first, report).json()
    assert NativeTable.read(Path(child['folder']) / TOOL_DATABASE).groups()['CUSTOM_1'][0]['values']['DIA'] == '12'


def test_new_columns_are_structured_explicit_and_bound_to_export(tmp_path):
    source = tmp_path / 'source'
    identifier = 'holder-metric'
    write(source, SPECS[identifier]['path'], simple(extra=True))
    store = Store(tmp_path / 'managed', {'source': source})
    original = store.import_release('source', '2512', 'Original', 'fixture')
    old = NativeTable(simple('CUSTOM', 'Old-schema holder'))
    resource = store.save_resource({'kind': 'holder', 'name': 'Holder', 'erp': None, 'asset_ids': [],
        'cam_status': 'approved', 'cam': native_cam(identifier, '2406', old, old.records)}, 'fixture')
    with TestClient(create_app(store, ADMIN)) as client:
        report = preview(client, original)
        assert not report['can_export']
        assert report['new_columns'] == [{'library_id': identifier, 'block_id': 'SAMPLE:1', 'field': 'EXTRA',
            'confirmed': False, 'value': None, 'resource_ids': [resource['id']]}]
        defaults = {identifier: {'SAMPLE:1': {'EXTRA': ''}}}  # Explicit empty value, never guessed.
        ready = preview(client, original, defaults)
        assert ready['can_export'] and ready['new_columns'][0]['confirmed']
        assert derive(client, original, ready, defaults={identifier: {'SAMPLE:1': {'EXTRA': 'unreviewed'}}}).status_code == 409
        assert len(store.releases()) == 1
        response = derive(client, original, ready)
        assert response.status_code == 201, response.text
        values = NativeTable.read(Path(response.json()['folder']) / SPECS[identifier]['path']).groups()['CUSTOM'][0]['values']
        assert values == {'LIBRF': 'CUSTOM', 'DESCR': 'Old-schema holder', 'EXTRA': ''}
        for fields in ({'UNKNOWN': '0'}, {'EXTRA': 3}, {'EXTRA': 'unsafe|value'}):
            assert client.post('/api/v1/releases/' + original['id'] + '/derive-preview', headers=HEADERS,
                json={'defaults': {identifier: {'SAMPLE:1': fields}}}).status_code == 400


def test_duplicate_identity_is_blocked_across_old_and_native_editors(pilot):
    client, store, *_ = pilot
    original = imported(client)
    payload = tool_payload()
    resource = client.post('/api/v1/resources', headers=HEADERS, json=payload).json()
    duplicate = client.post('/api/v1/resources', headers=HEADERS, json=payload)
    assert duplicate.status_code == 409 and len(store.resources()) == 1
    table = legacy_cam_table(payload['cam'])
    native = {**payload, 'cam': native_cam('tool-metric', '2512', table, table.records)}
    assert client.post('/api/v1/resources', headers=HEADERS, json=native).status_code == 409
    assert client.put('/api/v1/resources/' + resource['id'], headers=HEADERS,
        json={**payload, 'kind': 'fixture', 'cam_status': 'draft', 'expected_revision': 1}).status_code == 409
    first = derive(client, original, preview(client, original)).json()
    changed = tool_payload('RENAMED_AFTER_EXPORT')
    assert client.put('/api/v1/resources/' + resource['id'], headers=HEADERS,
        json={**changed, 'expected_revision': 1}).status_code == 409
    assert len(store.history(resource['id'])) == 1
    # Pre-existing duplicate data (from older app versions) must also fail closed.
    stale = {**copy.deepcopy(native), 'cam_status': 'retired',
             'origin': {'library_id': 'tool-metric', 'key': 'CUSTOM_1', 'editable': True,
                        'records_hash': first['catalog']['exported_tools'][resource['id']]['records_hash']}}
    with store.db() as db:
        db.execute('INSERT INTO resources VALUES(?,?,?,?,?)', (str(uuid.uuid4()), 1, canonical(stale), None, now()))
    report = preview(client, first)
    assert not report['can_export'] and 'Multiple catalog owners' in report['conflicts'][0]['reason']
    before = store.manifest(Path(first['folder']))
    assert derive(client, first, report).status_code == 409
    assert store.manifest(Path(first['folder'])) == before


def test_catalog_import_recognizes_existing_legacy_owner_and_rejects_stale_preview(pilot):
    client, store, *_ = pilot
    original = imported(client)
    resource = client.post('/api/v1/resources', headers=HEADERS, json=tool_payload()).json()
    first = derive(client, original, preview(client, original)).json()
    url = '/api/v1/releases/' + first['id']
    body = {'libraries': ['tool-metric'], 'include_standard': True}
    report = client.post(url + '/catalog-preview', headers=HEADERS, json=body).json()
    assert report['can_import'] and report['count'] == 1
    assert report['already_managed'][0]['resource_id'] == resource['id']
    assert not any(obj['key'] == 'CUSTOM_1' for obj in report['objects'])
    assert client.put('/api/v1/resources/' + resource['id'], headers=HEADERS,
        json={**tool_payload(), 'name': 'New label', 'expected_revision': 1}).status_code == 200
    assert client.post(url + '/catalog-import', headers=HEADERS,
        json={**body, 'preview_hash': report['preview_hash']}).status_code == 409
    assert len(store.resources()) == 1


def test_virtual_reference_checks_include_both_old_and_native_tool_exports(pilot):
    client, store, *_ = pilot
    original = imported(client)
    old = tool_payload('OLD_EDITOR')
    table = legacy_cam_table(tool_payload('NATIVE_EDITOR')['cam'])
    new = {**tool_payload('NATIVE_EDITOR'), 'cam': native_cam('tool-metric', '2512', table, table.records)}
    assert client.post('/api/v1/resources', headers=HEADERS, json=old).status_code == 201
    assert client.post('/api/v1/resources', headers=HEADERS, json=new).status_code == 201
    report = preview(client, original)
    assert report['can_export'] and len(report['actions']) == 2
    child = derive(client, original, report).json()
    assert NativeTable.read(Path(child['folder']) / TOOL_DATABASE).groups().keys() == {'STD_1', 'OLD_EDITOR', 'NATIVE_EDITOR'}


def test_preview_requires_admin_and_complete_parent(pilot):
    client, store, *_ = pilot
    original = imported(client)
    url = '/api/v1/releases/' + original['id'] + '/derive-preview'
    assert client.post(url, json={}).status_code == 401
    assert client.post(url, headers=ERP_HEADERS, json={}).status_code == 403
    assert client.post(url, headers=HEADERS, json={'unexpected': True}).status_code == 422
    path = Path(original['folder']) / TOOL_DATABASE
    path.write_bytes(path.read_bytes() + b'\n# Changed outside API\n')
    assert client.post(url, headers=HEADERS, json={}).status_code == 409


def test_real_failure_after_preflight_retains_failed_release(pilot, monkeypatch):
    client, store, *_ = pilot
    original = imported(client)
    client.post('/api/v1/resources', headers=HEADERS, json=tool_payload())
    report = preview(client, original)
    def failed_write(*args):
        raise OSError('Synthetic failure after snapshot; no real disk problem.')
    monkeypatch.setattr(store, '_apply_export_plan', failed_write)
    with pytest.raises(OSError):
        store.import_release('', '', 'Failed fixture', 'fixture', parent=original['id'], preview_hash=report['preview_hash'])
    assert store.releases()[0]['status'] == 'failed'
    assert store.get_release(original['id'])['status'] == 'draft'


def test_asset_collisions_and_tampering_block_preview_without_publishing(pilot):
    client, store, *_ = pilot
    original = imported(client)
    first = store.add_asset('picture.png', b'Synthetic picture version 1', 'fixture')
    second = store.add_asset('picture.png', b'Synthetic picture version 2', 'fixture')
    for key, asset in (('ONE', first), ('TWO', second)):
        payload = {**tool_payload(key), 'asset_ids': [asset['id']]}
        assert client.post('/api/v1/resources', headers=HEADERS, json=payload).status_code == 201
    before, audit = store.manifest(Path(original['folder'])), store.audit_entries()
    report = preview(client, original)
    assert not report['can_export'] and 'conflicting planned contents' in report['conflicts'][0]['reason']
    assert derive(client, original, report).status_code == 409
    assert len(store.releases()) == 1 and store.audit_entries() == audit
    assert store.manifest(Path(original['folder'])) == before
    path = store.asset(second['id'])[1]
    path.write_bytes(b'Tampered test-owned attachment')
    report = preview(client, original)
    assert not report['can_export'] and 'changed outside the API' in report['conflicts'][0]['reason']


def test_unreadable_asset_produces_a_conflict_and_not_an_internal_error(pilot):
    client, store, *_ = pilot
    original = imported(client)
    asset = store.add_asset('picture.png', b'Synthetic attachment', 'fixture')
    store.save_resource({**tool_payload(), 'asset_ids': [asset['id']]}, 'fixture')
    path = store.asset(asset['id'])[1]
    path.unlink()  # Test-owned file only, simulating unavailable storage.
    report = preview(client, original)
    assert not report['can_export'] and 'missing' in report['conflicts'][0]['reason']
    assert len(store.releases()) == 1


def test_reference_preview_blocks_dangling_retirement_but_allows_retiring_dependents(tmp_path):
    source = tmp_path / 'source'
    write(source, SPECS['holder-metric']['path'], simple())
    tool_schema = '#CLASS SAMPLE\nFORMAT LIBRF HLDREF\nDATA |STD_TOOL|\n#END_DATA\n'
    write(source, TOOL_DATABASE, tool_schema)
    store = Store(tmp_path / 'managed', {'source': source})
    original = store.import_release('source', '2512', 'Original', 'fixture')
    holder_table = NativeTable(simple('CUSTOM_HOLDER'))
    tool_table = NativeTable(tool_schema.replace('|STD_TOOL|', '|CUSTOM_TOOL|CUSTOM_HOLDER'))
    holder_body = {'kind': 'holder', 'name': 'Holder', 'cam_status': 'approved', 'asset_ids': [], 'erp': None,
        'cam': native_cam('holder-metric', '2512', holder_table, holder_table.records)}
    tool_body = {**tool_payload(), 'cam': native_cam('tool-metric', '2512', tool_table, tool_table.records)}
    holder = store.save_resource(holder_body, 'fixture')
    tool = store.save_resource(tool_body, 'fixture')
    report = store.preview_export(original['id'])
    assert report['can_export'] and not report['references']['errors']
    first = store.import_release('', '', 'Linked objects', 'fixture', parent=original['id'], preview_hash=report['preview_hash'])
    before = store.manifest(Path(first['folder']))
    store.save_resource({**holder_body, 'cam_status': 'retired'}, 'fixture', holder['id'], 1)
    report = store.preview_export(first['id'])
    assert not report['can_export'] and report['references']['errors'][0]['value'] == 'CUSTOM_HOLDER'
    assert len(store.releases()) == 2 and store.manifest(Path(first['folder'])) == before
    store.save_resource({**tool_body, 'cam_status': 'retired'}, 'fixture', tool['id'], 1)
    report = store.preview_export(first['id'])
    assert report['can_export']
    child = store.import_release('', '', 'Both retired', 'fixture', parent=first['id'], preview_hash=report['preview_hash'])
    assert NativeTable.read(Path(child['folder']) / TOOL_DATABASE).groups().keys() == {'STD_TOOL'}
    assert NativeTable.read(Path(child['folder']) / SPECS['holder-metric']['path']).groups().keys() == {'STD'}


def test_transient_library_change_during_read_cannot_escape_manifest_binding(pilot, monkeypatch):
    client, store, *_ = pilot
    original = imported(client)
    client.post('/api/v1/resources', headers=HEADERS, json=tool_payload())
    path = Path(original['folder']) / TOOL_DATABASE
    before = path.read_bytes()
    real_read = NativeTable.read.__func__
    def transient_read(cls, value):
        if Path(value) != path:
            return real_read(cls, value)
        path.write_bytes(before.replace(b'10', b'99'))
        try:
            return real_read(cls, value)
        finally:
            path.write_bytes(before)
    monkeypatch.setattr(NativeTable, 'read', classmethod(transient_read))
    report = preview(client, original)
    assert not report['can_export'] and 'changed while planning' in report['conflicts'][0]['reason']
    assert path.read_bytes() == before and len(store.releases()) == 1
