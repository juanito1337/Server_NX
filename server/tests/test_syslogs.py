"""Immutable, uploaded NX evidence; all content is synthetic and stays in pytest storage."""
import base64
import hashlib
from pathlib import Path

import pytest

from nxserver.store import FUNCTIONAL_TESTS
from nxserver.syslogs import MAX_LOG_BYTES, decode_log, scan_bytes
from nxserver.safety import DomainError
from test_management import HEADERS, ERP_HEADERS, imported, pilot, pinned_review


def upload(client, raw, filename='test.syslog', headers=HEADERS):
    return client.post('/api/v1/logs', headers=headers,
                       json={'filename': filename, 'content_base64': base64.b64encode(raw).decode()})


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'cp1252'])
def test_uploaded_logs_are_independent_immutable_evidence_and_can_be_reviewed(pilot, encoding):
    client, store, source, configured = pilot
    release = imported(client)
    before = store.manifest(Path(release['folder']))
    configured_before = configured.read_bytes()
    raw = ('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\nPrüfung: ' + release['folder']).encode(encoding)
    response = upload(client, raw)
    assert response.status_code == 201, response.text
    log = response.json()
    assert log['sha256'] == hashlib.sha256(raw).hexdigest()
    assert log['size'] == len(raw)
    assert client.get('/api/v1/logs', headers=HEADERS).json()[0]['id'] == log['id']
    scan = client.post('/api/v1/logs/' + log['id'] + '/scan', headers=HEADERS,
                       json={'release_id': release['id']}).json()
    assert scan['contains_release_reference'] and scan['contains_cam_activity']
    assert not scan['blocking_errors'] and scan['expected_folder'] == release['folder']
    assert store.get_release(release['id'])['status'] == 'draft'
    review = {'tests': {key: True for key in FUNCTIONAL_TESTS},
              'evidence': 'Synthetic test-only human confirmation, NOT a real CAM approval.', 'syslog_source': log['id']}
    assert client.post('/api/v1/releases/' + release['id'] + '/review', headers=HEADERS, json=pinned_review(client,release,review)).status_code == 200
    assert client.post('/api/v1/active', headers=HEADERS,
                       json={'release_id': release['id'], 'expected_generation': 0}).status_code == 200
    assert store.manifest(Path(release['folder'])) == before
    assert configured.read_bytes() == configured_before
    assert not source.joinpath('evidence').exists()
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM syslogs').fetchone()[0] == 1
    assert any(entry['action'] == 'syslog.uploaded' for entry in store.audit_entries())


def test_logs_require_admin_and_valid_bounded_plain_text(pilot):
    client, store, *_ = pilot
    raw = b'UG_APP_MANUFACTURING\nSynthetic evidence'
    assert upload(client, raw, headers=ERP_HEADERS).status_code == 403
    assert upload(client, raw, headers={}).status_code == 401
    assert client.get('/api/v1/logs', headers=ERP_HEADERS).status_code == 403
    for filename in ('../outside.syslog', r'W:\SiemensShare\nx2406\nx.syslog', 'file.exe', 'file.log '):
        assert upload(client, raw, filename).status_code == 400
    for content in (b'\x00binary', b'\x01binary', b'\xff\xfe\x41', b''):
        assert upload(client, content).status_code in (400, 422)
    assert client.post('/api/v1/logs', headers=HEADERS, json={'filename': 'test.syslog', 'content_base64': '!!!'}).status_code == 400
    assert store.list_logs() == [{'id': 'test-log', 'filename': 'test.syslog', 'source_type': 'configured',
                                 'sha256': None, 'size': None, 'created_at': None}]


def test_wrong_candidate_errors_and_tampering_prevent_approval(pilot):
    client, store, source, _ = pilot
    release = imported(client)
    url = '/api/v1/releases/' + release['id'] + '/review'
    review = {'tests': {key: True for key in FUNCTIONAL_TESTS},
              'evidence': 'Synthetic functional test confirmation only.', 'syslog_source': ''}
    for raw in (('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + str(source)).encode(),
                ('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + release['folder'] + '\nUid in component and part file do not match').encode()):
        log = upload(client, raw).json()
        review['syslog_source'] = log['id']
        assert client.post(url, headers=HEADERS, json=pinned_review(client,release,review)).status_code == 409
    log = upload(client, ('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + release['folder']).encode()).json()
    review['syslog_source'] = log['id']
    assert client.post(url, headers=HEADERS, json=pinned_review(client,release,review)).status_code == 200
    path = store.root / 'evidence' / log['id'] / 'nx.syslog'
    path.write_bytes(path.read_bytes() + b'\nChanged outside API')
    assert client.post('/api/v1/logs/' + log['id'] + '/scan', headers=HEADERS).status_code == 409
    assert client.post('/api/v1/active', headers=HEADERS,
                       json={'release_id': release['id'], 'expected_generation': 0}).status_code == 409
    assert store.active()['release_id'] is None


def test_missing_logs_return_a_diagnostic_not_a_server_error(pilot):
    client, store, _, configured = pilot
    configured.unlink()  # Test-owned file only.
    assert client.post('/api/v1/logs/test-log/scan', headers=HEADERS).status_code == 404
    assert client.post('/api/v1/logs/unknown/scan', headers=HEADERS).status_code == 404


def test_limits_and_path_boundaries_never_dereference_references():
    with pytest.raises(DomainError, match='20 MB'):
        decode_log(b'x' * (MAX_LOG_BYTES + 1))
    assert not scan_bytes(b'UG_APP_MANUFACTURING\nC:/example/nx-other', 'fixture', 'C:/example/nx')['contains_release_reference']
    assert scan_bytes(b'UG_APP_MANUFACTURING\nC:/example/nx/MACH/file.prt', 'fixture', 'C:/example/nx')['contains_release_reference']
    result = scan_bytes(b'UG_APP_MANUFACTURING\nW:/SiemensShare/nx2406/file.prt', 'fixture', 'C:/offline/test')
    assert not result['contains_release_reference']
