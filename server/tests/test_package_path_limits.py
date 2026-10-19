"""One existing path-length contract before inventory selection/copy/acceptance."""
import os
import uuid
from concurrent.futures import Future

import pytest
from pydantic import ValidationError

from nxserver import filepackages
from nxserver.api import PackagePreflightEntry
from nxserver.filepackages import Entry, FilePackages, MAX_PATH_CHARS, path_for
from nxserver.safety import DomainError
from nxserver.store import canonical
from nxserver.upgrades import LIVE, Upgrades
from test_filepackages import packages
from test_management import HEADERS

BINDINGS = [
    ('template', 'UGII/templates/', '.prt'),
    ('ui_standard', 'UGII/menus/roles/', '.mtx'),
    ('ui_standard', 'UGII/menus/profiles/', '.grb'),
    ('machine', 'MACH/resource/library/machine/installed_machines/', '.xml'),
    ('fixture', 'MACH/resource/library/fixture_automation/graphics/', '.prt'),
]


def relative_path(root, length, suffix):
    prefix = root + '/'.join(letter * 40 for letter in 'ABC') + '/'
    name = 'ITEM' + suffix
    path = prefix + 'D' * (length - len(prefix) - len(name) - 1) + '/' + name
    assert len(path) == length and all(1 <= len(part) <= 120 for part in path.split('/'))
    return path


def metadata_entry(store, release, path):
    """Only own manifest metadata; no overlong Windows path is materialized."""
    with store.db() as connection:
        connection.execute('UPDATE releases SET manifest=? WHERE id=?', (
            canonical([*release['manifest'], {'path': path, 'size': 32, 'sha256': '0' * 64}]), release['id']))


def forbidden(*args, **kwargs):
    pytest.fail('Invalid package paths must not trigger source/baseline bytes or attachment allocation.')


@pytest.mark.parametrize('kind,root,suffix', BINDINGS)
@pytest.mark.parametrize('length', [239, 240, 241])
def test_entry_api_and_domain_use_the_same_existing_boundary(kind, root, suffix, length):
    assert MAX_PATH_CHARS == 240
    path = relative_path(root, length, suffix)
    entry = {'asset_id': str(uuid.uuid4()), 'path': path}
    if length <= MAX_PATH_CHARS:
        assert path_for(kind, path) == path
        assert Entry.model_validate(entry).path == PackagePreflightEntry.model_validate(entry).path == path
    else:
        with pytest.raises(DomainError, match='1-240 characters'):
            path_for(kind, path)
        for model in (Entry, PackagePreflightEntry):
            with pytest.raises(ValidationError):
                model.model_validate(entry)


@pytest.mark.parametrize('kind,root,suffix', BINDINGS)
@pytest.mark.parametrize('length', [240, 241])
def test_metadata_inventory_explains_length_without_file_or_asset_reads(packages, monkeypatch, kind, root, suffix, length):
    _, store, release, *_ = packages
    path = relative_path(root, length, suffix)
    metadata_entry(store, release, path)
    before = store.audit_entries()
    monkeypatch.setattr(store, 'asset', forbidden)
    monkeypatch.setattr(store, 'manifest', forbidden)
    monkeypatch.setattr(filepackages, 'file_content', forbidden)
    monkeypatch.setattr(filepackages, '_baseline_file', forbidden)
    report = FilePackages(store).inventory(release['id'], kind)
    assert report['total'] == 1 and report['files'][0]['path'] == path
    assert not report['snapshot']['files_verified'] and not report['snapshot']['baseline_compared']
    item = report['files'][0]
    assert item['selectable'] == (length <= MAX_PATH_CHARS)
    if length > MAX_PATH_CHARS:
        assert '1-240 characters' in item['unavailable_reason']
    else:
        assert item['unavailable_reason'] is None
    assert store.audit_entries() == before


@pytest.mark.parametrize('kind,root,suffix', BINDINGS)
def test_overlong_adoption_is_rejected_before_read_copy_or_job_acceptance(packages, monkeypatch, kind, root, suffix):
    client, store, release, *_ = packages
    path = relative_path(root, 241, suffix)
    metadata_entry(store, release, path)
    parent = store.save_resource({'kind': kind, 'name': 'Synthetic existing draft',
        'cam': {}, 'erp': None, 'cam_status': 'draft', 'asset_ids': []}, 'fixture') if kind in ('machine', 'fixture') else None
    parent_id = parent['id'] if parent else None
    before = store.audit_entries()
    monkeypatch.setattr(filepackages, 'file_content', forbidden)
    monkeypatch.setattr(filepackages, '_baseline_file', forbidden)
    monkeypatch.setattr(store, 'add_asset', forbidden)
    manager = FilePackages(store)
    with pytest.raises(DomainError, match='1-240 characters'):
        manager.adoption_preview(release['id'], kind, [path], parent_id)
    with pytest.raises(DomainError, match='1-240 characters'):
        manager.adopt(release['id'], kind, [path], 'Not adopted', parent_id, '0' * 64, 'fixture')
    request = {'kind': kind, 'paths': [path], 'resource_id': parent_id}
    adoption = {**request, 'name': 'Not adopted', 'preview_hash': '0' * 64}
    routes = [
        ('/api/v1/releases/' + release['id'] + '/file-packages/preview', request),
        ('/api/v1/releases/' + release['id'] + '/file-packages/adopt', adoption),
        ('/api/v1/release-jobs/file_adopt', {**adoption, 'release_id': release['id']}),
    ]
    for route, value in routes:
        response = client.post(route, headers={**HEADERS, 'Idempotency-Key': str(uuid.uuid4())}, json=value)
        assert response.status_code == 400 and '1-240 characters' in response.text, response.text
    with store.db() as connection:
        for table in ('assets', 'migration_jobs', 'migration_requests'):
            assert connection.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
    assert len(store.resources()) == (1 if parent else 0) and store.audit_entries() == before
    assert store.active()['release_id'] is None


@pytest.mark.skipif(os.name == 'nt', reason='Real 240-character relative paths exceed this Windows fixture root budget; lexical/API/metadata cases run on both OSs.')
@pytest.mark.parametrize('kind,root,suffix', BINDINGS)
def test_real_240_character_customer_file_adopts_without_changing_source(packages, kind, root, suffix):
    _, store, _, source, standard = packages
    path = relative_path(root, 240, suffix)
    file = source / path
    file.parent.mkdir(parents=True)
    content = b'<Synthetic/>' if suffix in ('.xml', '.mtx') else b'Synthetic bytes; NOT an NX model or qualification'
    file.write_bytes(content)
    (standard / root).mkdir(parents=True, exist_ok=True)
    release = store.import_release('test', '2512', 'Deep valid customer file', 'fixture')
    before = store.manifest(store.release_folder(release['id']))
    parent = store.save_resource({'kind': kind, 'name': 'Synthetic existing draft',
        'cam': {}, 'erp': None, 'cam_status': 'draft', 'asset_ids': []}, 'fixture') if kind in ('machine', 'fixture') else None
    parent_id = parent['id'] if parent else None
    service = FilePackages(store)
    report, _ = service.adoption_preview(release['id'], kind, [path], parent_id)
    assert report['can_adopt'], report['conflicts']
    resource = service.adopt(release['id'], kind, [path], 'Deep valid file', parent_id, report['preview_hash'], 'fixture')
    assert resource['cam_status'] == 'draft' and resource['file_package']['entries'][0]['path'] == path
    assert store.asset(resource['asset_ids'][0])[1].read_bytes() == content
    assert file.read_bytes() == content and store.manifest(store.release_folder(release['id'])) == before
    assert store.active()['release_id'] is None


def test_historical_overlong_acceptance_replays_without_new_validation_or_worker(packages, monkeypatch):
    _, store, release, *_ = packages
    manager = Upgrades(store)
    submitted = []
    def submit(callback, identifier):
        submitted.append(identifier)
        future = Future()
        monkeypatch.setitem(LIVE, identifier, future)
        return future
    monkeypatch.setattr(manager.pool, 'submit', submit)
    request = {'release_id': release['id'], 'kind': 'template',
        'paths': [relative_path('UGII/templates/', 241, '.prt')], 'resource_id': None,
        'name': 'Historical acceptance only', 'preview_hash': '0' * 64}
    key = str(uuid.uuid4())
    # Model an old receipt only. This never runs the callback or copies bytes.
    with monkeypatch.context() as historical:
        historical.setattr(filepackages, 'path_for', lambda kind, path: path)
        first = manager.create_adoption(request, 'file_adopt', 'fixture', request_key=key)
    LIVE[first['id']].set_result(None)
    manager.get(first['id'])
    before = store.audit_entries()
    monkeypatch.setattr(filepackages, 'path_for', forbidden)
    monkeypatch.setattr(store, 'get_release', forbidden)
    assert manager.create_adoption(request, 'file_adopt', 'fixture', request_key=key) == first
    assert submitted == [first['id']] and store.audit_entries() == before
    assert store.asset_inventory()['total'] == 0 and store.resources() == []
