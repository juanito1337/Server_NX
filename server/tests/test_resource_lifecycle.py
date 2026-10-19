"""Real create/edit/retire exports, always in new disposable folders; no production data."""
from pathlib import Path

import pytest

from nxserver.libraries import NativeTable, SPECS, native_cam
from nxserver.safety import DomainError
from nxserver.store import Store, TOOL_DATABASE
from test_management import HEADERS, imported, pilot, tool_payload
from test_libraries import native_pilot, normalized, resource_body, simple, write


def test_legacy_tool_can_update_retire_and_reactivate_only_its_owned_record(pilot):
    client, store, source, _ = pilot
    original = imported(client)
    resource = client.post('/api/v1/resources', headers=HEADERS, json=tool_payload()).json()
    first = client.post('/api/v1/releases/' + original['id'] + '/derive', headers=HEADERS, json={'name': 'First'}).json()
    first_before = store.manifest(Path(first['folder']))
    payload = {**tool_payload(), 'expected_revision': resource['revision']}
    payload['cam']['values']['DIA'] = '14'
    update = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=payload)
    assert update.status_code == 200, update.text
    second = client.post('/api/v1/releases/' + first['id'] + '/derive', headers=HEADERS, json={'name': 'Changed'})
    assert second.status_code == 201, second.text
    second = second.json()
    records = NativeTable.read(Path(second['folder']) / TOOL_DATABASE).groups()
    assert records['CUSTOM_1'][0]['values']['DIA'] == '14'
    payload.update(cam_status='retired', expected_revision=update.json()['revision'])
    retired = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=payload).json()
    third = client.post('/api/v1/releases/' + second['id'] + '/derive', headers=HEADERS, json={'name': 'Retired'})
    assert third.status_code == 201, third.text
    third = third.json()
    assert set(NativeTable.read(Path(third['folder']) / TOOL_DATABASE).groups()) == {'STD_1'}
    assert not third['catalog']['exported_tools']
    assert third['catalog']['export_actions'][0]['action'] == 'retired'
    payload.update(cam_status='approved', expected_revision=retired['revision'])
    assert client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=payload).status_code == 200
    last = client.post('/api/v1/releases/' + third['id'] + '/derive', headers=HEADERS, json={'name': 'Reactivated'})
    assert last.status_code == 201, last.text
    assert set(NativeTable.read(Path(last.json()['folder']) / TOOL_DATABASE).groups()) == {'STD_1', 'CUSTOM_1'}
    assert store.manifest(Path(first['folder'])) == first_before
    assert NativeTable.read(source / TOOL_DATABASE).groups().keys() == {'STD_1'}


@pytest.mark.parametrize('identifier', list(SPECS))
def test_every_native_library_supports_owned_retirement_without_changing_history(tmp_path, identifier):
    spec = SPECS[identifier]
    source = tmp_path / 'source'
    write(source, spec['path'], simple())
    store = Store(tmp_path / 'managed', {'source': source})
    original = store.import_release('source', '2512', 'Original', 'fixture')
    table = NativeTable(simple('CUSTOM'))
    payload = {'kind': spec['kind'], 'name': 'Customer', 'cam_status': 'approved', 'asset_ids': [], 'erp': None,
               'cam': native_cam(identifier, '2512', table, table.records), 'geometry': tool_payload()['geometry']}
    resource = store.save_resource(payload, 'fixture')
    first = store.import_release('', '', 'Export', 'fixture', parent=original['id'])
    before = store.manifest(Path(first['folder']))
    store.save_resource({**payload, 'cam_status': 'retired'}, 'fixture', resource['id'], resource['revision'])
    second = store.import_release('', '', 'Retired', 'fixture', parent=first['id'])
    assert NativeTable.read(Path(second['folder']) / spec['path']).groups().keys() == {'STD'}
    assert not second['catalog']['exported_native']
    assert store.manifest(Path(first['folder'])) == before
    assert NativeTable.read(source / spec['path']).groups().keys() == {'STD'}


def test_imported_customer_retirement_preserves_siemens_standard_records(native_pilot):
    client, store, source = native_pilot
    release = normalized(client)
    url = '/api/v1/releases/' + release['id']
    body = {'libraries': ['tool-metric'], 'include_standard': True}
    preview = client.post(url + '/catalog-preview', headers=HEADERS, json=body).json()
    assert client.post(url + '/catalog-import', headers=HEADERS, json={**body, 'preview_hash': preview['preview_hash']}).status_code == 200
    for resource in store.resources():
        payload = {**resource_body(resource), 'cam_status': 'retired', 'expected_revision': resource['revision']}
        assert client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=payload).status_code == 200
    result = client.post(url + '/derive', headers=HEADERS, json={'name': 'Only Siemens retained'})
    assert result.status_code == 201, result.text
    assert NativeTable.read(Path(result.json()['folder']) / SPECS['tool-metric']['path']).groups().keys() == {'STD'}
    assert NativeTable.read(Path(release['folder']) / SPECS['tool-metric']['path']).groups().keys() == {'STD', 'CUSTOM', 'HD'}


def test_retiring_a_referenced_holder_blocks_export_instead_of_breaking_tools(tmp_path):
    source = tmp_path / 'source'
    write(source, SPECS['holder-metric']['path'], simple())
    write(source, SPECS['tool-metric']['path'], '#CLASS SAMPLE\nFORMAT LIBRF HLDREF\nDATA |TOOL|CUSTOM\n#END_DATA\n')
    store = Store(tmp_path / 'managed', {'source': source})
    base = store.import_release('source', '2512', 'Original', 'fixture')
    table = NativeTable(simple('CUSTOM'))
    payload = {'kind': 'holder', 'name': 'Holder', 'cam_status': 'approved', 'asset_ids': [], 'erp': None,
               'cam': native_cam('holder-metric', '2512', table, table.records)}
    resource = store.save_resource(payload, 'fixture')
    first = store.import_release('', '', 'Holder added', 'fixture', parent=base['id'])
    store.save_resource({**payload, 'cam_status': 'retired'}, 'fixture', resource['id'], 1)
    with pytest.raises(DomainError, match='reference'):
        store.import_release('', '', 'Blocked removal', 'fixture', parent=first['id'])
    assert NativeTable.read(Path(first['folder']) / SPECS['holder-metric']['path']).groups().keys() == {'STD', 'CUSTOM'}
