import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.dat import read_database
from nxserver.safety import DomainError, offline_path
from nxserver.store import FUNCTIONAL_TESTS, Store, TOOL_DATABASE

ADMIN = 'test-administrator-token-not-real-' + 'a' * 32
ERP = 'test-erp-token-not-real-' + 'b' * 32
HEADERS = {'Authorization': 'Bearer ' + ADMIN}
ERP_HEADERS = {'Authorization': 'Bearer ' + ERP}
DATABASE = '#CLASS end_mill\nFORMAT LIBRF DESCR DIA\nDATA |STD_1|Standard|10\n#END_DATA\n'


@pytest.fixture
def pilot(tmp_path):
    source = tmp_path / 'source'
    database = source / TOOL_DATABASE
    database.parent.mkdir(parents=True)
    database.write_text(DATABASE, encoding='utf-8')
    log = tmp_path / 'test.syslog'
    log.write_text('NX_VERSION = 2512.8500\nAPP_ENTERED_APPLICATION_ENVIRONMENT_CALLBACK_ID UG_APP_MANUFACTURING.\n' + str(source), encoding='utf-8')
    store = Store(tmp_path / 'managed', {'test': source}, {'test-log': log})
    with TestClient(create_app(store, ADMIN, ERP)) as client:
        yield client, store, source, log


def imported(client):
    response = client.post('/api/v1/releases', headers=HEADERS,
                           json={'source': 'test', 'nx_version': '2512', 'name': 'Test release'})
    assert response.status_code == 201, response.text
    return response.json()


def pinned_review(client, release, review):
    scan=client.post('/api/v1/logs/'+review['syslog_source']+'/scan',headers=HEADERS,json={'release_id':release['id']})
    assert scan.status_code==200,scan.text
    value=scan.json()
    return {**review,'expected_log_sha256':value['sha256'],'expected_manifest_sha256':value['manifest_sha256']}


def tool_payload(libref='CUSTOM_1'):
    return {'kind': 'tool', 'name': 'Customer end mill', 'cam_status': 'approved',
            'cam': {'class_name': 'end_mill', 'values': {'LIBRF': libref, 'DESCR': 'Custom', 'DIA': '12'}},
            'erp': None, 'asset_ids': [], 'geometry': {
                'mode': 'parametric_nx', 'status': 'validated', 'nx_version': '2512',
                'checks': {key: True for key in ('units', 'orientation', 'mount_point', 'cutting_geometry', 'non_cutting_geometry', 'collision_geometry')},
                'evidence': 'Isolated test fixture; no real production geometry approval.', 'asset_hashes': {}}}


def test_authentication_and_erp_scope(pilot):
    client, *_ = pilot
    assert client.get('/health').json()['production_connected'] is False
    assert client.get('/api/v1/resources').status_code == 401
    assert client.get('/api/v1/resources', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert client.post('/api/v1/resources', headers=ERP_HEADERS, json=tool_payload()).status_code == 403
    assert client.get('/api/v1/releases', headers=ERP_HEADERS).status_code == 403
    assert client.get('/api/v1/openapi.json', headers=HEADERS).json()['info']['version'] == '0.1.0'


def test_catalog_is_versioned_and_does_not_change_nx(pilot):
    client, store, source, _ = pilot
    before = (source / TOOL_DATABASE).read_bytes()
    resource = client.post('/api/v1/resources', headers=HEADERS, json=tool_payload()).json()
    assert (source / TOOL_DATABASE).read_bytes() == before
    update = {**tool_payload(), 'name': 'Changed', 'expected_revision': 1}
    response = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=update)
    assert response.status_code == 200
    assert response.json()['revision'] == 2
    assert client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=update).status_code == 409
    assert len(store.history(resource['id'])) == 2
    assert len(store.audit_entries()) == 2


def test_erp_idempotency_and_ownership(pilot):
    client, _, source, _ = pilot
    erp = {'kind': 'tool', 'company_id': 'company-1', 'system_id': 'bc-system-1', 'number': 'W0001',
           'description': 'ERP tool', 'unit': 'PCS', 'source_revision': 'etag-1'}
    created = client.post('/api/v1/erp/resources/upsert', headers=ERP_HEADERS, json=erp)
    assert created.status_code == 200
    resource = created.json()
    assert resource['cam_status'] == 'draft'
    assert client.post('/api/v1/erp/resources/upsert', headers=ERP_HEADERS, json=erp).json()['unchanged']
    body = {**tool_payload(), 'expected_revision': resource['revision'], 'erp': resource['erp']}
    updated = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=body).json()
    enriched = {**erp, 'source_revision': 'etag-2', 'description': 'New description', 'expected_revision': updated['revision']}
    result = client.post('/api/v1/erp/resources/upsert', headers=ERP_HEADERS, json=enriched).json()
    assert result['cam'] == tool_payload()['cam']
    assert result['cam_status'] == 'approved'
    assert len(client.get('/api/v1/resources', headers=HEADERS).json()) == 1
    assert client.post('/api/v1/erp/resources/upsert', headers=ERP_HEADERS, json=erp).status_code == 409
    assert (source / TOOL_DATABASE).read_text() == DATABASE


def test_derive_exports_new_tools_to_a_new_folder(pilot):
    client, store, source, _ = pilot
    original = imported(client)
    records_response = client.get('/api/v1/releases/' + original['id'] + '/tools', headers=HEADERS).json()
    assert records_response['total'] == 1
    assert records_response['items'][0]['values']['LIBRF'] == 'STD_1'
    assert client.post('/api/v1/resources', headers=HEADERS, json=tool_payload()).status_code == 201
    response = client.post('/api/v1/releases/' + original['id'] + '/derive', headers=HEADERS, json={'name': 'With tool'})
    assert response.status_code == 201, response.text
    child = response.json()
    _, _, records, _ = read_database(Path(child['folder']) / TOOL_DATABASE)
    assert len(records) == 2
    assert records[-1]['values']['LIBRF'] == 'CUSTOM_1'
    assert (source / TOOL_DATABASE).read_text() == DATABASE
    assert (Path(original['folder']) / TOOL_DATABASE).read_text() == DATABASE
    # Exported records are tracked: subsequent unchanged catalog exports are idempotent.
    third = client.post('/api/v1/releases/' + child['id'] + '/derive', headers=HEADERS, json={'name': 'Repeat'})
    assert third.status_code == 201, third.text
    assert len(read_database(Path(third.json()['folder']) / TOOL_DATABASE)[2]) == 2
    assert len(store.get_release(child['id'])['catalog']['exported_tools']) == 1


def test_schema_and_collision_fail_closed(pilot):
    client, store, *_ = pilot
    original = imported(client)
    assert client.post('/api/v1/resources', headers=HEADERS, json=tool_payload('STD_1')).status_code == 201
    response = client.post('/api/v1/releases/' + original['id'] + '/derive', headers=HEADERS, json={'name': 'Collision'})
    assert response.status_code == 400
    # A known planning conflict is rejected before creating a failed folder/job.
    # Actual failures after snapshotting still retain their failed release evidence.
    assert len(store.releases()) == 1 and store.releases()[0]['id'] == original['id']
    assert store.active()['release_id'] is None


def test_release_tampering_and_approval_gates(pilot):
    client, store, _, log = pilot
    release = imported(client)
    url = '/api/v1/releases/' + release['id']
    assert client.post('/api/v1/active', headers=HEADERS, json={'release_id': release['id'], 'expected_generation': 0}).status_code == 409
    review = {'tests': {key: True for key in FUNCTIONAL_TESTS}, 'evidence': 'Fixture tests passed by qualified CAM reviewer.', 'syslog_source': 'test-log'}
    log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n'+release['folder'] + '\n+++ Uid in component and part file do not match', encoding='utf-8')
    assert client.post(url + '/review', headers=HEADERS, json=pinned_review(client,release,review)).status_code == 409
    assert store.scan_log('test-log')['blocking_errors']
    log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + release['folder'], encoding='utf-8')
    assert client.post(url + '/review', headers=HEADERS, json=pinned_review(client,release,review)).status_code == 200
    activate = {'release_id': release['id'], 'expected_generation': 0}
    assert client.post('/api/v1/active', headers=HEADERS, json=activate).json()['generation'] == 1
    assert client.post('/api/v1/active', headers=HEADERS, json=activate).status_code == 409
    (Path(release['folder']) / TOOL_DATABASE).write_text(DATABASE + '# tamper\n', encoding='utf-8')
    assert not client.post(url + '/validate', headers=HEADERS).json()['integrity_passed']
    assert client.post('/api/v1/active', headers=HEADERS, json={'release_id': release['id'], 'expected_generation': 1}).status_code == 409


def test_wrong_log_and_partial_review_cannot_approve(pilot):
    client, _, _, log = pilot
    release = imported(client)
    url = '/api/v1/releases/' + release['id'] + '/review'
    review = {'tests': {key: True for key in FUNCTIONAL_TESTS}, 'evidence': 'Fixture tests passed by qualified CAM reviewer.', 'syslog_source': 'test-log'}
    log.write_text('NX_VERSION = 2512.8500\nNX startup without manufacturing test', encoding='utf-8')
    assert client.post(url, headers=HEADERS, json=pinned_review(client,release,review)).status_code == 409
    log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\nC:/other-release', encoding='utf-8')
    assert client.post(url, headers=HEADERS, json=pinned_review(client,release,review)).status_code == 409
    log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + release['source'], encoding='utf-8')
    assert client.post(url, headers=HEADERS, json=pinned_review(client,release,review)).status_code == 409
    log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + release['folder'], encoding='utf-8')
    review['tests']['simulation'] = False
    assert client.post(url, headers=HEADERS, json=pinned_review(client,release,review)).status_code == 409


def test_malformed_palette_blocks_validation(pilot):
    client, _, source, _ = pilot
    (source / 'bad.pax').write_text('<Palette></Palette><PaletteEntry/>', encoding='utf-8')
    release = imported(client)
    result = client.post('/api/v1/releases/' + release['id'] + '/validate', headers=HEADERS).json()
    assert not result['integrity_passed']
    assert any('Malformed PAX' in error for error in result['errors'])


def test_assets_are_attached_and_versioned(pilot):
    client, _, _, _ = pilot
    original = imported(client)
    encoded = base64.b64encode(b'test fixture, not a real NX part').decode()
    asset = client.post('/api/v1/assets', headers=HEADERS, json={'filename': 'model.prt', 'content_base64': encoded}).json()
    body = tool_payload()
    body['asset_ids'] = [asset['id']]
    body['geometry']['mode'] = 'nx_cam'
    body['geometry']['asset_hashes'] = {asset['id']: asset['sha256']}
    resource = client.post('/api/v1/resources', headers=HEADERS, json=body).json()
    child = client.post('/api/v1/releases/' + original['id'] + '/derive', headers=HEADERS, json={'name': 'With model'}).json()
    assert (Path(child['folder']) / 'MACH/resource/library/tool/graphics/model.prt').exists()
    assert not (Path(child['folder']) / 'MACH/resource/library/tool/graphics/managed').exists()
    assert client.get('/api/v1/assets/' + asset['id'], headers=HEADERS).content == base64.b64decode(encoded)
    assert client.post('/api/v1/assets', headers=HEADERS, json={'filename': '../model.prt', 'content_base64': encoded}).status_code == 400


@pytest.mark.parametrize('path', [r'W:\SiemensShare\nx2406', r'W:\anything', r'\\server\share\folder', '/mnt/w/SiemensShare/nx2406'])
def test_production_and_network_paths_rejected_without_access(path):
    with pytest.raises(DomainError):
        offline_path(path)


def test_sources_allowlist_and_overlap(pilot, tmp_path):
    client, _, source, _ = pilot
    assert client.post('/api/v1/releases', headers=HEADERS, json={'source': '../outside', 'nx_version': '2512', 'name': 'Bad'}).status_code == 400
    with pytest.raises(DomainError):
        Store(source / 'managed', {'test': source})


def test_continuations_and_case_sensitive_refs(tmp_path):
    path = tmp_path / 'tool.dat'
    path.write_text('#CLASS end_mill\nFORMAT LIBRF\n DESCR\nDATA |A1|Alpha\n Beta\nDATA |a1|lower\n#END_DATA\n', encoding='utf-8')
    _, schemas, records, keys = read_database(path)
    assert schemas['end_mill'] == ['LIBRF', 'DESCR']
    assert records[0]['values']['DESCR'] == 'Alpha Beta'
    assert len(keys) == 2


def test_api_rejects_cross_origin_and_unknown_fields(pilot):
    client, *_ = pilot
    assert client.get('/api/v1/resources', headers={**HEADERS, 'Origin': 'https://untrusted.example'}).status_code == 403
    assert client.post('/api/v1/resources', headers=HEADERS, json={**tool_payload(), 'arbitrary_path': 'W:\\'}).status_code == 422
