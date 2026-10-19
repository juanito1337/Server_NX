"""Folder selection uses disposable local fixtures, never real NX folders."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.folders import scoped_path
from nxserver.safety import DomainError
from nxserver.store import Store, TOOL_DATABASE

ADMIN = 'isolated-folder-admin-' + 'a' * 40
ERP = 'isolated-folder-erp-' + 'b' * 40
HEADERS = {'Authorization': 'Bearer ' + ADMIN}
DATABASE = '#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |STD|Standard\n#END_DATA\n'


@pytest.fixture
def folders(tmp_path):
    source = tmp_path / 'inputs' / 'nested-share'
    old, new, output = (tmp_path / name for name in ('old', 'new', 'output'))
    output.mkdir()
    for path in (source, old, new):
        database = path / TOOL_DATABASE
        database.parent.mkdir(parents=True)
        database.write_text(DATABASE, encoding='utf-8')
    store = Store(tmp_path / 'managed', {'preset': source}, standards={'2406': old, '2512': new},
                  source_roots={'inputs': source.parent, 'workspace': tmp_path},
                  target_roots={'output': output, 'workspace': tmp_path})
    with TestClient(create_app(store, ADMIN, ERP)) as client:
        yield client, store, source, output


def body(source, output):
    return {'source': 'inputs', 'source_path': str(source), 'target': 'output',
            'target_path': str(output / 'nx2512-test'), 'nx_version': '2512',
            'name': 'Selected folder fixture', 'normalize_libraries': True}


def test_browse_is_authenticated_and_scoped(folders):
    client, store, source, output = folders
    url = '/api/v1/folders'
    params = {'purpose': 'source', 'root': 'inputs'}
    assert client.get(url, params=params).status_code == 401
    assert client.get(url, params=params, headers={'Authorization': 'Bearer ' + ERP}).status_code == 403
    response = client.get(url, params=params, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()['children'] == [{'name': source.name, 'path': str(source)}]
    assert response.json()['parent'] is None
    assert client.get(url, params={**params, 'path': str(source)}, headers=HEADERS).json()['parent'] == str(source.parent)
    assert client.get(url, params={**params, 'path': str(output)}, headers=HEADERS).status_code == 400
    assert client.get(url, params={**params, 'root': 'unknown'}, headers=HEADERS).status_code == 400
    managed = client.get(url, params={'purpose': 'target', 'root': 'managed'}, headers=HEADERS).json()
    assert managed['can_select'] and not (store.root / 'r').exists()
    roots = client.get('/api/v1/sources', headers=HEADERS).json()
    assert roots['source_roots']['inputs'] == str(source.parent)
    assert roots['target_roots']['output'] == str(output)


def test_selected_source_and_destination_are_versioned_and_survive_restart(folders):
    client, store, source, output = folders
    original = store.manifest(source)
    request = body(source, output)
    preview = client.post('/api/v1/import-preview', headers=HEADERS, json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()['destination'] == request['target_path']
    assert not Path(request['target_path']).exists()
    assert not store.releases()
    result = client.post('/api/v1/releases', headers=HEADERS,
                         json={**request, 'preview_hash': preview.json()['preview_hash']})
    assert result.status_code == 201, result.text
    release = result.json()
    assert release['folder'] == request['target_path']
    assert release['source'] == str(source) and release['status'] == 'draft'
    assert store.manifest(source) == original and store.active()['release_id'] is None
    restarted = Store(store.root, {'preset': source})
    assert restarted.get_release(release['id'])['folder'] == request['target_path']
    assert restarted.get_release(release['id'])['manifest'] == release['manifest']
    with pytest.raises(DomainError):
        store.folders.source('workspace', request['target_path'])
    derived = store.import_release('', '', 'Derived fixture', 'admin', parent=release['id'])
    assert Path(derived['folder']).is_relative_to(store.root / 'r')
    assert store.get_release(release['id'])['manifest'] == release['manifest']
    assert any(json.loads(entry['payload']).get('folder') == request['target_path']
               for entry in store.audit_entries() if entry['action'] == 'release.started')


def test_changed_destination_or_source_requires_new_preview(folders):
    client, store, source, output = folders
    request = body(source, output)
    preview = client.post('/api/v1/import-preview', headers=HEADERS, json=request).json()
    result = client.post('/api/v1/releases', headers=HEADERS,
        json={**request, 'target_path': str(output / 'different'), 'preview_hash': preview['preview_hash']})
    assert result.status_code == 409
    assert not store.releases() and not list(output.iterdir())
    second = source.parent / 'identical-copy'
    (second / TOOL_DATABASE).parent.mkdir(parents=True)
    (second / TOOL_DATABASE).write_text(DATABASE, encoding='utf-8')
    result = client.post('/api/v1/releases', headers=HEADERS,
        json={**request, 'source_path': str(second), 'preview_hash': preview['preview_hash']})
    assert result.status_code == 409
    assert not store.releases() and not list(output.iterdir())


def test_existing_target_is_never_reused_even_when_empty(folders):
    client, store, source, output = folders
    request = body(source, output)
    Path(request['target_path']).mkdir()
    for endpoint in ('import-preview', 'releases'):
        result = client.post('/api/v1/' + endpoint, headers=HEADERS, json=request)
        assert result.status_code == 409
        assert 'already exists' in result.json()['detail']
    assert not store.releases() and not list(Path(request['target_path']).iterdir())


def test_target_created_after_preview_blocks_before_release_is_registered(folders):
    client, store, source, output = folders
    request = body(source, output)
    preview = client.post('/api/v1/import-preview', headers=HEADERS, json=request).json()
    Path(request['target_path']).mkdir()
    sentinel = Path(request['target_path']) / 'keep.txt'
    sentinel.write_text('Do not overwrite', encoding='utf-8')
    result = client.post('/api/v1/releases', headers=HEADERS,
        json={**request, 'preview_hash': preview['preview_hash']})
    assert result.status_code == 409 and not store.releases()
    assert sentinel.read_text(encoding='utf-8') == 'Do not overwrite'


def test_unrelated_source_folder_does_not_produce_a_valid_import_preview(folders):
    client, store, source, output = folders
    unrelated = source.parent / 'not-an-nx-share'
    unrelated.mkdir()
    result = client.post('/api/v1/import-preview', headers=HEADERS,
        json={**body(source, output), 'source_path': str(unrelated)})
    assert result.status_code == 400 and 'MACH/resource' in result.json()['detail']
    assert not store.releases() and not list(output.iterdir())


@pytest.mark.parametrize('choice', ['source', 'source-child', 'source-parent', 'metadata',
                                   'metadata-child', 'standard', 'outside', 'target-root'])
def test_invalid_or_overlapping_targets_are_blocked_without_writes(folders, choice):
    client, store, source, output = folders
    paths = {'source': source, 'source-child': source / 'new', 'source-parent': source.parent,
             'metadata': store.root, 'metadata-child': store.root / 'assets' / 'new',
             'standard': store.standards['2512'] / 'new', 'outside': output.parent.parent / 'outside',
             'target-root': output.parent}
    request = {**body(source, output), 'target': 'workspace', 'target_path': str(paths[choice])}
    before = store.manifest(source)
    assert client.post('/api/v1/import-preview', headers=HEADERS, json=request).status_code in (400, 409)
    assert not store.releases() and store.manifest(source) == before


def test_browser_hides_management_and_releases_but_can_list_parent_roots(folders):
    client, store, source, output = folders
    release = store.import_release('inputs', '2512', 'Fixture', 'admin', source_path=str(source),
                                   target='output', target_path=str(output / 'managed-release'))
    for purpose in ('source', 'target'):
        params = {'purpose': purpose, 'root': 'workspace'}
        result = client.get('/api/v1/folders', headers=HEADERS, params=params)
        assert result.status_code == 200
        assert store.root.name not in [entry['name'] for entry in result.json()['children']]
        assert client.get('/api/v1/folders', headers=HEADERS,
            params={**params, 'path': release['folder']}).status_code == 400
    assert client.get('/api/v1/folders', headers=HEADERS,
        params={'purpose': 'target', 'root': 'workspace', 'path': str(source)}).status_code == 400


@pytest.mark.parametrize('value', [r'W:\SiemensShare\nx2406', r'\\server\share', '/mnt/w/private',
                                  'relative', 'ROOT/../outside', 'ROOT/bad:stream', 'ROOT/CON',
                                  'ROOT/trailing.', 'ROOT/trailing ', 'ROOT/control\x00'])
def test_invalid_paths_are_rejected_before_any_filesystem_lookup(tmp_path, monkeypatch, value):
    root = tmp_path / 'allowed'
    value = value.replace('ROOT', str(root))
    def no_lookup(path):
        raise AssertionError('Invalid user input must be rejected before filesystem access.')
    monkeypatch.setattr(Path, 'lstat', no_lookup)
    with pytest.raises(DomainError):
        scoped_path(root, value)


def test_outside_path_is_rejected_before_any_filesystem_lookup(tmp_path, monkeypatch):
    root = tmp_path / 'allowed'
    def no_lookup(path):
        raise AssertionError('Out-of-scope filesystem lookup.')
    monkeypatch.setattr(Path, 'lstat', no_lookup)
    with pytest.raises(DomainError, match='outside'):
        scoped_path(root, str(tmp_path / 'allowed-other' / 'private'))


def test_linked_folder_cannot_be_browsed_or_selected(folders, tmp_path):
    client, store, source, output = folders
    link = source.parent / 'linked'
    try:
        link.symlink_to(output, target_is_directory=True)
    except OSError:
        pytest.skip('Creating test symlinks requires OS privileges.')
    params = {'purpose': 'source', 'root': 'inputs'}
    entries = client.get('/api/v1/folders', headers=HEADERS, params=params).json()['children']
    assert 'linked' not in [entry['name'] for entry in entries]
    assert client.get('/api/v1/folders', headers=HEADERS,
        params={**params, 'path': str(link)}).status_code == 400
    with pytest.raises(DomainError):
        store.folders.destination('output', str(output / 'missing' / '..' / 'new'), source)
