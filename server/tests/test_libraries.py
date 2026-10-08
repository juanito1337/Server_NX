"""Native library merge/export contracts; all mutations use isolated fixtures."""
import base64
import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.libraries import (SPECS, OPTIONAL_PALETTE, NativeTable, cam_table,
                                merge_tables, native_cam, project_records, reference_report)
from nxserver.safety import DomainError
from nxserver.store import Store
from test_management import HEADERS, ADMIN, tool_payload


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def simple(key='STD', descr='Siemens', extra=False):
    return '#CLASS SAMPLE\nFORMAT LIBRF DESCR' + (' EXTRA' if extra else '') + '\nDATA |' + key + '|' + descr + ('|0' if extra else '') + '\n#END_DATA\n'


def resource_body(resource):
    return {key: value for key, value in resource.items() if key in
            ('kind', 'name', 'erp', 'cam', 'cam_status', 'asset_ids', 'geometry', 'origin')}


def test_multiline_multiblock_section_roundtrip():
    text = '#CLASS MILLING\nFORMAT LIBRF RTYPE\n DESCR\nDATA |A|1|Long\n holder\n#CLASS MILLING\nFORMAT LIBRF RTYPE SEQ DIAM\nDATA |A|2|1|10\nDATA |A|2|2|12\n#END_DATA\n'
    table = NativeTable(text)
    assert len(table.groups()['A']) == 3
    assert table.records[0]['values']['DESCR'] == 'Long holder'
    rendered = NativeTable(table.render(table.groups()))
    assert rendered.groups() == table.groups()
    assert rendered.blocks == table.blocks
    cam = native_cam('holder-metric', '2512', table, table.groups()['A'])
    assert cam_table(cam).groups() == table.groups()


def test_partial_schema_ids_survive_catalog_roundtrip():
    table = NativeTable('#CLASS X\nFORMAT LIBRF RTYPE DESCR\nDATA |A|1|first\n#END_DATA\n#CLASS X\nFORMAT LIBRF RTYPE SEQ DIA\nDATA |B|2|1|4\n#END_DATA\n')
    cam = native_cam('shank-metric', '2512', table, table.groups()['B'])
    parsed = cam_table(cam)
    assert parsed.records[0]['block_id'] == 'X:2'
    assert project_records(parsed.records, parsed, table) == table.groups()['B']


def test_projection_is_by_column_name_and_requires_new_column_value():
    old = NativeTable(simple('CUST', 'Customer'))
    new = NativeTable('#CLASS SAMPLE\nFORMAT DESCR EXTRA LIBRF\nDATA |Siemens|0|STD\n#END_DATA\n')
    with pytest.raises(DomainError, match='Explicit default'):
        project_records(old.records, old, new)
    record = project_records(old.records, old, new, {'SAMPLE:1': {'EXTRA': 'confirmed'}})[0]
    assert record['values'] == {'LIBRF': 'CUST', 'DESCR': 'Customer', 'EXTRA': 'confirmed'}
    removed = NativeTable('#CLASS SAMPLE\nFORMAT LIBRF RENAMED\nDATA |STD|test\n')
    with pytest.raises(DomainError, match='Removed/renamed'):
        project_records(old.records, old, removed)


def test_merge_keeps_new_siemens_data_and_requires_explicit_conflict_choice():
    old = NativeTable(simple())
    new = NativeTable(simple(descr='New Siemens'))
    groups, report = merge_tables(new, old, old)
    assert not report['conflicts']
    assert groups['STD'][0]['values']['DESCR'] == 'New Siemens'
    custom = NativeTable(simple(descr='Customer changed'))
    _, report = merge_tables(new, custom, old)
    assert len(report['conflicts']) == 1
    groups, report = merge_tables(new, custom, old, resolutions={'STD': 'customer'})
    assert not report['conflicts']
    assert groups['STD'][0]['values']['DESCR'] == 'Customer changed'
    groups, report = merge_tables(old, custom, old)
    assert not report['conflicts']
    assert report['counts']['customer_only_changes'] == 1
    assert groups['STD'][0]['values']['DESCR'] == 'Customer changed'


def test_unknown_legacy_database_is_not_silently_discarded(native_pilot):
    client, _, source = native_pilot
    write(source, 'MACH/resource/library/tool/henning_dierk/unknown_database.dat', simple())
    response = client.post('/api/v1/import-preview', headers=HEADERS,
                           json={'source': 'source', 'nx_version': '2512', 'name': 'Preview'})
    assert response.status_code == 200
    assert not response.json()['can_import']
    assert any(c['library'] == 'unmapped-legacy-database' for c in response.json()['conflicts'])
    body = {'source': 'source', 'nx_version': '2512', 'name': 'Preview',
            'defaults': {'tool-metric': {'SAMPLE:1': {'EXTRA': '0'}}},
            'legacy_omissions': ['MACH/resource/library/tool/henning_dierk/unknown_database.dat']}
    assert client.post('/api/v1/import-preview', headers=HEADERS, json=body).json()['can_import']
    body['legacy_omissions'] = [SPECS['tool-metric']['legacy_path']]
    assert client.post('/api/v1/import-preview', headers=HEADERS, json=body).status_code == 400


@pytest.mark.parametrize('identifier', list(SPECS))
def test_all_registered_native_libraries_can_read_create_and_export(tmp_path, identifier):
    spec = SPECS[identifier]
    source = tmp_path / 'source'
    path = write(source, spec['path'], simple())
    store = Store(tmp_path / 'managed', {'source': source})
    original = store.import_release('source', '2512', 'Fixture', 'test')
    table = NativeTable(simple('CUSTOM', 'Customer'))
    body = {'kind': spec['kind'], 'name': 'Customer', 'cam_status': 'approved',
            'cam': native_cam(identifier, '2512', table, table.records), 'asset_ids': [], 'erp': None,
            'geometry': tool_payload()['geometry']}
    with TestClient(create_app(store, ADMIN)) as client:
        data = client.get('/api/v1/releases/' + original['id'] + '/libraries/' + identifier, headers=HEADERS)
        assert data.status_code == 200, data.text
        response = client.post('/api/v1/resources', headers=HEADERS, json=body)
        assert response.status_code == 201, response.text
        derived = client.post('/api/v1/releases/' + original['id'] + '/derive', headers=HEADERS, json={'name': 'Export'})
        assert derived.status_code == 201, derived.text
        child = Path(derived.json()['folder'])
        assert set(NativeTable.read(child / spec['path']).groups()) == {'STD', 'CUSTOM'}
        assert path.read_text() == simple()
        assert NativeTable.read(Path(original['folder']) / spec['path']).groups().keys() == {'STD'}


@pytest.fixture
def native_pilot(tmp_path):
    source, old, new = (tmp_path / name for name in ('source', 'old', 'new'))
    spec = SPECS['tool-metric']
    write(old, spec['path'], simple())
    write(new, spec['path'], simple(extra=True))
    write(source, spec['path'], simple(extra=True) + simple('CUSTOM', 'Existing customer', True))
    # Repeating the class is legal when the same fields are used. Use one block
    # for the realistic standard + customer source in this fixture.
    write(source, spec['path'], simple(extra=True).replace('#END_DATA', 'DATA |CUSTOM|Existing customer|0\n#END_DATA'))
    write(source, spec['legacy_path'], simple().replace('#END_DATA', 'DATA |HD|Legacy customer\n#END_DATA'))
    write(source, OPTIONAL_PALETTE, '<Palette></Palette><PaletteEntry/>')
    write(source, 'MACH/resource/library/tool/henning_dierk/backup/tool_database.dat_bak', 'retained only in source')
    store = Store(tmp_path / 'managed', {'source': source}, standards={'2406': old, '2512': new})
    with TestClient(create_app(store, ADMIN)) as client:
        yield client, store, source


def normalized(client):
    body = {'source': 'source', 'nx_version': '2512', 'name': 'Normalized', 'normalize_libraries': True,
            'defaults': {'tool-metric': {'SAMPLE:1': {'EXTRA': '0'}}}}
    preview = client.post('/api/v1/import-preview', headers=HEADERS, json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()['can_import']
    result = client.post('/api/v1/releases', headers=HEADERS, json={**body, 'preview_hash': preview.json()['preview_hash']})
    assert result.status_code == 201, result.text
    return result.json()


def test_normalization_omits_legacy_copies_and_palette_without_touching_source(native_pilot):
    client, store, source = native_pilot
    before = store.manifest(source)
    release = normalized(client)
    root = Path(release['folder'])
    assert set(NativeTable.read(root / SPECS['tool-metric']['path']).groups()) == {'STD', 'CUSTOM', 'HD'}
    assert not (root / OPTIONAL_PALETTE).exists()
    assert not (root / SPECS['tool-metric']['legacy_path']).exists()
    assert store.manifest(source) == before
    assert store.resources() == []
    assert store.active()['release_id'] is None


def test_changed_preview_and_missing_new_column_block_import(native_pilot):
    client, store, source = native_pilot
    body = {'source': 'source', 'nx_version': '2512', 'name': 'Blocked', 'normalize_libraries': True}
    preview = client.post('/api/v1/import-preview', headers=HEADERS, json=body).json()
    assert not preview['can_import']
    assert client.post('/api/v1/releases', headers=HEADERS, json={**body, 'preview_hash': preview['preview_hash']}).status_code == 409
    assert not store.releases()
    body['defaults'] = {'tool-metric': {'SAMPLE:1': {'EXTRA': '0'}}}
    preview = client.post('/api/v1/import-preview', headers=HEADERS, json=body).json()
    write(source, SPECS['tool-metric']['legacy_path'], simple('CHANGED'))
    assert client.post('/api/v1/releases', headers=HEADERS, json={**body, 'preview_hash': preview['preview_hash']}).status_code == 409
    assert not store.releases()


def test_catalog_import_is_previewed_idempotent_and_draft_then_custom_update_exports(native_pilot):
    client, store, source = native_pilot
    release = normalized(client)
    url = '/api/v1/releases/' + release['id']
    body = {'libraries': ['tool-metric']}
    preview = client.post(url + '/catalog-preview', headers=HEADERS, json=body).json()
    assert preview['count'] == 2
    assert client.post(url + '/catalog-import', headers=HEADERS, json=body).status_code == 409
    imported = client.post(url + '/catalog-import', headers=HEADERS, json={**body, 'preview_hash': preview['preview_hash']})
    assert imported.status_code == 200, imported.text
    assert imported.json()['imported'] == 2
    assert all(r['cam_status'] == 'draft' for r in store.resources())
    again = client.post(url + '/catalog-preview', headers=HEADERS, json=body).json()
    assert again['count'] == 0 and not again['conflicts']
    resource = next(r for r in store.resources() if r['origin']['key'] == 'HD')
    update = resource_body(resource)
    update['cam']['records'][0]['values']['DESCR'] = 'Prepared in CAM'
    update.update(cam_status='approved', geometry=tool_payload()['geometry'], expected_revision=resource['revision'])
    response = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=update)
    assert response.status_code == 200, response.text
    child = client.post(url + '/derive', headers=HEADERS, json={'name': 'Custom updated'})
    assert child.status_code == 201, child.text
    table = NativeTable.read(Path(child.json()['folder']) / SPECS['tool-metric']['path'])
    assert table.groups()['HD'][0]['values']['DESCR'] == 'Prepared in CAM'
    assert NativeTable.read(Path(release['folder']) / SPECS['tool-metric']['path']).groups()['HD'][0]['values']['DESCR'] == 'Legacy customer'
    assert client.post(url + '/catalog-preview', headers=HEADERS, json=body).json()['conflicts']


def test_standard_catalog_object_cannot_overwrite_siemens_record(native_pilot):
    client, store, _ = native_pilot
    release = normalized(client)
    body = {'libraries': ['tool-metric'], 'include_standard': True}
    url = '/api/v1/releases/' + release['id']
    preview = client.post(url + '/catalog-preview', headers=HEADERS, json=body).json()
    client.post(url + '/catalog-import', headers=HEADERS, json={**body, 'preview_hash': preview['preview_hash']})
    resource = next(r for r in store.resources() if r['origin']['key'] == 'STD')
    assert not resource['origin']['editable']
    update = resource_body(resource)
    update['cam']['records'][0]['values']['DESCR'] = 'Not allowed'
    update.update(cam_status='approved', geometry=tool_payload()['geometry'], expected_revision=resource['revision'])
    assert client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=update).status_code == 200
    assert client.post(url + '/derive', headers=HEADERS, json={'name': 'Blocked'}).status_code == 409


def test_exact_standard_copy_cannot_be_adopted_then_overwritten(native_pilot):
    client, store, _ = native_pilot
    release = normalized(client)
    table = NativeTable.read(Path(release['folder']) / SPECS['tool-metric']['path'])
    body = {'kind': 'tool', 'name': 'Not a new tool', 'cam_status': 'approved', 'asset_ids': [],
            'cam': native_cam('tool-metric', '2512', table, table.groups()['STD']), 'geometry': tool_payload()['geometry']}
    assert client.post('/api/v1/resources', headers=HEADERS, json=body).status_code == 201
    response = client.post('/api/v1/releases/' + release['id'] + '/derive', headers=HEADERS, json={'name': 'Blocked adoption'})
    assert response.status_code == 409
    assert 'already exists' in response.text


def test_native_export_to_new_columns_requires_explicit_defaults(tmp_path):
    spec = SPECS['holder-metric']
    source = tmp_path / 'source'
    write(source, spec['path'], simple(extra=True))
    store = Store(tmp_path / 'managed', {'source': source})
    original = store.import_release('source', '2512', 'Fixture', 'test')
    old = NativeTable(simple('CUSTOM'))
    store.save_resource({'kind': 'holder', 'name': 'Old-schema holder', 'erp': None,
        'cam': native_cam('holder-metric', '2406', old, old.records), 'cam_status': 'approved', 'asset_ids': []}, 'test')
    with pytest.raises(DomainError, match='Explicit default'):
        store.import_release('', '', 'Blocked', 'test', parent=original['id'])
    child = store.import_release('', '', 'Mapped', 'test', parent=original['id'], defaults={'holder-metric': {'SAMPLE:1': {'EXTRA': '0'}}})
    assert NativeTable.read(Path(child['folder']) / spec['path']).groups()['CUSTOM'][0]['values']['EXTRA'] == '0'


def test_step_is_not_a_ready_cam_tool_and_prt_requires_hash_bound_evidence(native_pilot):
    client, _, _ = native_pilot
    body = tool_payload()
    body['geometry']['mode'] = 'step'
    assert client.post('/api/v1/resources', headers=HEADERS, json=body).status_code == 400
    asset = client.post('/api/v1/assets', headers=HEADERS, json={'filename': 'test.prt', 'content_base64': base64.b64encode(b'fixture-not-production').decode()}).json()
    body['asset_ids'] = [asset['id']]
    body['geometry']['mode'] = 'nx_cam'
    assert client.post('/api/v1/resources', headers=HEADERS, json=body).status_code == 400
    body['geometry']['asset_hashes'] = {asset['id']: asset['sha256']}
    assert client.post('/api/v1/resources', headers=HEADERS, json=body).status_code == 201


def test_missing_holder_reference_is_reported_and_known_siemens_defect_is_warning(tmp_path):
    root, standard = tmp_path / 'source', tmp_path / 'standard'
    text = '#CLASS SAMPLE\nFORMAT LIBRF HLDREF\nDATA |TOOL|MISSING\n#END_DATA\n'
    write(root, SPECS['tool-metric']['path'], text)
    assert reference_report(root)['errors'][0]['field'] == 'HLDREF'
    write(standard, SPECS['tool-metric']['path'], text)
    report = reference_report(root, {'2512': standard}, '2512')
    assert not report['errors'] and report['warnings']
