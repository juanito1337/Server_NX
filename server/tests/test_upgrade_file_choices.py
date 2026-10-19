"""Unambiguous file decisions before any source lookup or worker acceptance."""
import copy
import uuid
from concurrent.futures import Future
from pathlib import Path

import pytest

from nxserver import migration, upgrades
from nxserver.api import MigrationInput
from nxserver.safety import DomainError
from nxserver.store import digest
from nxserver.upgrades import LIVE, Upgrades
from test_upgrades import upgrade_pilot, preview, finish
from test_management import HEADERS
from test_libraries import write


RELATIVE = 'MACH/custom/conflict.txt'


@pytest.mark.parametrize('values', [('standard', 'customer'), ('customer', 'standard'), ('customer', 'customer')])
@pytest.mark.parametrize('reverse', [False, True])
def test_duplicate_case_aliases_are_rejected_independently_of_insertion_order(values, reverse):
    pairs = list(zip([RELATIVE, RELATIVE.lower()], values))
    value = dict(reversed(pairs) if reverse else pairs)
    before = copy.deepcopy(value)
    assert digest(value) == digest(dict(reversed(list(value.items()))))
    with pytest.raises(DomainError, match='repeated case-insensitive paths'):
        migration.validated_file_choices(value)
    assert value == before


def test_unicode_casefold_aliases_follow_the_existing_planner_path_identity():
    with pytest.raises(DomainError, match='repeated case-insensitive paths'):
        migration.validated_file_choices({'MACH/custom/Straße.txt': 'customer', 'mach/custom/STRASSE.txt': 'standard'})


@pytest.mark.parametrize('value', [None, [], 'customer', {12: 'customer'}, {'relative.txt': 'invalid'},
    {'relative.txt': None}, {'../outside.txt': 'standard'}, {'': 'customer'},
    {'MACH/custom/CON.txt': 'customer'}, {r'W:\SiemensShare\nx2406': 'standard'}])
def test_invalid_choices_are_a_domain_error_without_any_filesystem_query(monkeypatch, value):
    monkeypatch.setattr(migration, 'offline_path', lambda *args: pytest.fail('Lexical choices must not inspect the filesystem.'))
    with pytest.raises(DomainError):
        migration.validated_file_choices(value)


def test_distinct_choices_keep_values_and_only_normalize_lookup_keys():
    value = {RELATIVE: 'customer', 'MACH/custom/Other.txt': 'standard'}
    before = copy.deepcopy(value)
    assert migration.validated_file_choices(value) == {RELATIVE.lower(): 'customer', 'mach/custom/other.txt': 'standard'}
    assert value == before and migration.validated_file_choices({}) == {}


def test_direct_planner_rejects_ambiguous_choices_before_prerequisites(monkeypatch):
    monkeypatch.setattr(migration, 'prerequisites', lambda *args: pytest.fail('Invalid choices must stop before input access.'))
    with pytest.raises(DomainError, match='case-insensitive'):
        migration.Planner(None).build({'file_resolutions': {RELATIVE: 'customer', RELATIVE.lower(): 'standard'}})


@pytest.mark.parametrize('mode', ['preview', 'build'])
@pytest.mark.parametrize('values', [('standard', 'customer'), ('customer', 'standard'), ('customer', 'customer')])
def test_api_rejects_ambiguous_choices_before_acceptance_or_source_access(upgrade_pilot, monkeypatch, mode, values):
    client, store, request, source = upgrade_pilot
    request['file_resolutions'] = dict(zip([RELATIVE, RELATIVE.lower()], values))
    if mode == 'build':
        request.update(preview_id=str(uuid.uuid4()), preview_hash='0' * 64)
    before = store.manifest(source)
    audits = store.audit_entries()
    monkeypatch.setattr(upgrades, 'request_source', lambda *args: pytest.fail('Rejected choices must not inspect source folders.'))
    response = client.post('/api/v1/migrations/' + mode, headers=HEADERS, json=request)
    assert response.status_code == 400 and 'case-insensitive' in response.text
    assert Upgrades(store).list() == [] and store.audit_entries() == audits
    assert store.manifest(source) == before and not Path(request['target_path']).exists()
    assert not store.releases() and store.active()['release_id'] is None


@pytest.mark.parametrize('choice', ['standard', 'customer'])
@pytest.mark.parametrize('casing', ['lower', 'upper'])
def test_single_case_variant_still_builds_exact_reviewed_bytes(upgrade_pilot, choice, casing):
    client, store, request, source = upgrade_pilot
    write(store.standards['2406'], RELATIVE, 'Old standard')
    write(store.standards['2512'], RELATIVE, 'New standard')
    write(source, RELATIVE, 'Customer customization')
    request['file_resolutions'] = {getattr(RELATIVE, casing)(): choice}
    before = store.manifest(source)
    checked, report = preview(client, request)
    assert report['can_build']
    result = finish(client, client.post('/api/v1/migrations/build', headers=HEADERS,
        json={**request, 'preview_id': checked['id'], 'preview_hash': report['preview_hash']}))
    assert result['status'] == 'completed'
    expected = 'New standard' if choice == 'standard' else 'Customer customization'
    assert (Path(request['target_path']) / RELATIVE).read_text() == expected
    assert store.manifest(source) == before and store.active()['release_id'] is None


def test_historical_acceptance_replay_precedes_new_choice_validation(upgrade_pilot, monkeypatch):
    _, store, request, source = upgrade_pilot
    request['file_resolutions'] = {RELATIVE: 'customer', RELATIVE.lower(): 'standard'}
    request = MigrationInput.model_validate(request).model_dump()
    manager = Upgrades(store)
    submitted = []

    def submit(callback, identifier):
        submitted.append(identifier)
        future = Future()
        monkeypatch.setitem(LIVE, identifier, future)
        return future

    monkeypatch.setattr(manager.pool, 'submit', submit)
    key = str(uuid.uuid4())
    # Model an old acceptance receipt in this disposable store, not a new
    # permitted migration. No callback runs and no source files are copied.
    with monkeypatch.context() as historical:
        historical.setattr(upgrades, 'validated_file_choices', lambda *args: {})
        first = manager.create(request, 'preview', 'fixture', request_key=key)
    LIVE[first['id']].set_result(None)
    manager.get(first['id'])  # Observe terminal test future before taking snapshots.
    audits = store.audit_entries()
    before = store.manifest(source)
    monkeypatch.setattr(upgrades, 'validated_file_choices', lambda *args: pytest.fail('Replay must return its original receipt.'))
    monkeypatch.setattr(upgrades, 'request_source', lambda *args: pytest.fail('Replay may not inspect input folders.'))
    assert manager.create(request, 'preview', 'fixture', request_key=key) == first
    assert submitted == [first['id']] and store.audit_entries() == audits
    assert store.manifest(source) == before and not Path(request['target_path']).exists()
