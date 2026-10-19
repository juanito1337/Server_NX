"""Audit navigation uses only disposable databases; no NX or real account."""
import json

import pytest

from nxserver import auditview
from nxserver.safety import DomainError
from nxserver.store import Store
from test_history import rows
from test_management import HEADERS, ERP_HEADERS, pilot


def seed(store, count):
    with store.db() as db:
        for index in range(count):
            store.audit(db, 'CAM Ä' if index % 2 else 'ERP B', 'fixture.changed',
                        {'index': index, 'text': '<script>never execute</script>', 'future_column': ['text', 1.0]})


def pin(value):
    return {'sequence': value['head']['sequence'], 'digest': value['head']['hash']}


def test_empty_and_all_pages_beyond_the_legacy_200_limit_are_read_only(tmp_path, monkeypatch):
    store = Store(tmp_path / 'managed')
    assert auditview.summary(store)['head'] == {'sequence': 0, 'hash': '0' * 64}
    seed(store, 227)
    before = rows(store)
    monkeypatch.setattr(store, 'db', lambda: pytest.fail('Audit browsing must not use a writable connection.'))
    first = auditview.summary(store, limit=50)
    values = first['items'][:]
    for offset in (50, 100, 150, 200):
        value = auditview.summary(store, offset=offset, limit=50, **pin(first))
        values.extend(value['items'])
    assert [item['sequence'] for item in values] == list(range(227, 0, -1))
    assert all('payload' not in item for item in values)
    assert first['total'] == first['filtered'] == 227
    selected = values[-1]
    result = auditview.entry(store, 1, selected['hash'], **pin(first))
    assert json.loads(result['payload'])['index'] == 0
    assert result['entry_hash_verified'] is True
    assert result['audit_chain_verified'] is result['files_verified'] is result['signed'] is False
    monkeypatch.undo()
    assert rows(store) == before


def test_exact_substring_filters_are_parameterized_and_never_search_private_payloads(tmp_path):
    store = Store(tmp_path / 'managed'); seed(store, 7)
    assert auditview.summary(store, query='CAM Ä')['filtered'] == 3
    assert auditview.summary(store, query='CAM ä')['filtered'] == 0
    assert auditview.summary(store, query='fixture.changed')['filtered'] == 7
    assert auditview.summary(store, query='future_column')['filtered'] == 0
    assert auditview.summary(store, query="' OR 1=1 --")['filtered'] == 0


@pytest.mark.parametrize('mode', ['append', 'truncate', 'replace'])
def test_changed_head_rejects_older_page_and_detail_without_repair(tmp_path, mode):
    store = Store(tmp_path / 'managed'); seed(store, 25)
    first = auditview.summary(store)
    if mode == 'append': seed(store, 1)
    else:
        with store.db() as db:
            if mode == 'truncate': db.execute('DELETE FROM audit WHERE sequence=25')
            else: db.execute('UPDATE audit SET hash=? WHERE sequence=25', ('a' * 64,))
    before = rows(store)
    for operation in (lambda: auditview.summary(store, offset=20, **pin(first)),
                      lambda: auditview.entry(store, 25, first['items'][0]['hash'], **pin(first))):
        with pytest.raises(DomainError) as error: operation()
        assert error.value.status == 409
    assert rows(store) == before


@pytest.mark.parametrize('field', ['payload', 'actor', 'action', 'created_at', 'previous_hash'])
def test_detail_detects_selected_entry_tampering_without_history_rewrite(tmp_path, field):
    store = Store(tmp_path / 'managed'); seed(store, 3)
    first = auditview.summary(store); original = first['items'][1]
    with store.db() as db:
        db.execute('UPDATE audit SET ' + field + '=? WHERE sequence=2', ('b' * 64 if field == 'previous_hash' else 'changed',))
    before = rows(store)
    with pytest.raises(DomainError) as error:
        auditview.entry(store, 2, original['hash'], **pin(first))
    assert error.value.status == 409
    assert rows(store) == before


def test_large_payload_has_metadata_but_bounded_details_and_keeps_exact_raw_text(tmp_path, monkeypatch):
    store = Store(tmp_path / 'managed'); seed(store, 1)
    first = auditview.summary(store); selected = first['items'][0]
    raw = store.audit_entries()[0]['payload']
    assert auditview.entry(store, 1, selected['hash'], **pin(first))['payload'] == raw
    monkeypatch.setattr(auditview, 'MAX_PAYLOAD', 2)
    assert auditview.summary(store)['items'][0]['payload_bytes'] == len(raw.encode('utf-8'))
    with pytest.raises(DomainError, match='bounded detail reader'):
        auditview.entry(store, 1, selected['hash'], **pin(first))


def test_read_snapshot_does_not_mix_concurrent_append(tmp_path, monkeypatch):
    store = Store(tmp_path / 'managed'); seed(store, 2)
    old = auditview._head
    def concurrent(db, sequence, digest):
        head = old(db, sequence, digest)
        seed(store, 1)
        return head
    monkeypatch.setattr(auditview, '_head', concurrent)
    result = auditview.summary(store)
    assert result['head']['sequence'] == result['total'] == 2
    assert [item['sequence'] for item in result['items']] == [2, 1]
    assert len(store.audit_entries()) == 3


@pytest.mark.parametrize('kwargs', [{'offset': -1}, {'offset': True}, {'limit': 0}, {'limit': 51},
                                  {'limit': True}, {'query': 'x' * 201}, {'offset': 20},
                                  {'sequence': 0}, {'digest': '0' * 64}])
def test_invalid_navigation_is_rejected(tmp_path, kwargs):
    store = Store(tmp_path / 'managed')
    with pytest.raises(DomainError): auditview.summary(store, **kwargs)


def test_api_auth_query_contract_and_legacy_endpoint_unchanged(pilot):
    client, store, *_ = pilot; seed(store, 23)
    before = rows(store)
    for route in ('audit-summary', 'audit-entries/1?expected_hash=' + '0' * 64 + '&head_sequence=0&head_hash=' + '0' * 64):
        assert client.get('/api/v1/' + route).status_code == 401
        assert client.get('/api/v1/' + route, headers=ERP_HEADERS).status_code == 403
    first = client.get('/api/v1/audit-summary', headers=HEADERS).json()
    params = {'head_sequence': first['head']['sequence'], 'head_hash': first['head']['hash'], 'offset': 20}
    assert len(client.get('/api/v1/audit-summary', params=params, headers=HEADERS).json()['items']) == 3
    value = first['items'][0]
    params.pop('offset'); params['expected_hash'] = value['hash']
    detail = client.get('/api/v1/audit-entries/' + str(value['sequence']), params=params, headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()['entry_hash_verified'] is True
    assert client.get('/api/v1/audit', headers=HEADERS).json() == store.audit_entries()
    assert client.get('/api/v1/audit-summary?limit=51', headers=HEADERS).status_code == 422
    assert client.get('/auditview.js').status_code == 200
    assert rows(store) == before
