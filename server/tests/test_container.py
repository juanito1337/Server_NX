"""Container startup contract; an actual engine test is opt-in and separate."""
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nxserver.config import startup, token_from_environment
from nxserver.safety import DomainError, offline_path
from nxserver.store import FUNCTIONAL_TESTS, Store, TOOL_DATABASE, digest


def settings(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'data_dir': str(tmp_path / 'data'), 'sources': {}}), encoding='utf-8')
    return path


def test_default_listener_retains_local_random_key(tmp_path):
    value, admin, erp, generated = startup(settings(tmp_path), '127.0.0.1', {})
    assert generated and len(admin) >= 32 and erp is None
    assert not (tmp_path / 'data').exists()  # Settings validation creates no store.


def test_container_listener_requires_explicit_persistent_secret(tmp_path):
    with pytest.raises(DomainError, match='configured admin token'):
        startup(settings(tmp_path), '0.0.0.0', {})
    assert not (tmp_path / 'data').exists()
    secret = tmp_path / 'token.txt'
    secret.write_text('z' * 64 + '\n', encoding='utf-8')
    _, admin, erp, generated = startup(settings(tmp_path), '0.0.0.0', {'NX_ADMIN_TOKEN_FILE': str(secret)})
    assert admin == 'z' * 64 and not generated and erp is None
    assert startup(settings(tmp_path), '0.0.0.0', {'NX_ADMIN_TOKEN_FILE': str(secret)})[1] == admin


@pytest.mark.parametrize('value', ['', 'too-short', 'a' * 32 + '\ninline', 'a' * 4097])
def test_invalid_inline_tokens_are_rejected(value):
    with pytest.raises(DomainError):
        token_from_environment('NX_ADMIN_TOKEN', {'NX_ADMIN_TOKEN': value})


def test_token_files_cannot_be_ambiguous_oversized_or_production_paths(tmp_path):
    path = tmp_path / 'secret.txt'
    path.write_text('a' * 32)
    with pytest.raises(DomainError, match='both'):
        token_from_environment('NX_ADMIN_TOKEN', {'NX_ADMIN_TOKEN': 'a' * 32, 'NX_ADMIN_TOKEN_FILE': str(path)})
    path.write_text('a' * 4097)
    with pytest.raises(DomainError, match='exceeds'):
        token_from_environment('NX_ADMIN_TOKEN', {'NX_ADMIN_TOKEN_FILE': str(path)})
    with pytest.raises(DomainError, match='Protected/network'):
        token_from_environment('NX_ADMIN_TOKEN', {'NX_ADMIN_TOKEN_FILE': r'W:\SiemensShare\nx2406\token.txt'})
    with pytest.raises(DomainError, match='distinct'):
        startup(settings(tmp_path), '0.0.0.0', {'NX_ADMIN_TOKEN': 'a' * 32, 'NX_ERP_TOKEN': 'a' * 32})


def test_separate_erp_secret_file(tmp_path):
    path = tmp_path / 'erp.txt'
    path.write_text('b' * 64)
    _, admin, erp, generated = startup(settings(tmp_path), '0.0.0.0', {
        'NX_ADMIN_TOKEN': 'a' * 64, 'NX_ERP_TOKEN_FILE': str(path)})
    assert admin != erp and erp == 'b' * 64 and not generated


@pytest.mark.parametrize('reference', [r'W:\SiemensShare\nx2406', r'\\server\share', r'D:\alias',
                                      r'C:\SiemensShare\nx2406', r'C:\test\..\prod', 'relative'])
def test_container_host_references_are_local_text_only(tmp_path, reference):
    root = tmp_path / 'data'
    with pytest.raises(DomainError, match='Display paths'):
        Store(root, display_paths={str(root / 'r'): reference})
    assert not root.exists()


def test_host_mapping_cannot_redirect_sources_or_metadata(tmp_path):
    root = tmp_path / 'data'
    with pytest.raises(DomainError, match='limited'):
        Store(root, display_paths={str(tmp_path / 'input'): r'C:\NX_Migration\DockerReleases'})


def test_windows_host_release_mapping_survives_restart_and_binds_exact_log(tmp_path):
    root, source = tmp_path / 'data', tmp_path / 'input'
    database = source / TOOL_DATABASE
    database.parent.mkdir(parents=True)
    database.write_text('#CLASS TEST\nFORMAT LIBRF DESCR\nDATA |STD|Standard\n#END_DATA\n', encoding='utf-8')
    log = tmp_path / 'test.syslog'
    log.write_text('UG_APP_MANUFACTURING\n', encoding='utf-8')
    mapping = {str(root / 'r'): r'C:\NX_Migration\DockerReleases'}
    store = Store(root, {'test': source}, {'test': log}, display_paths=mapping)
    release = store.import_release('test', '2512', 'Container test', 'admin')
    assert release['host_folder'] == r'C:\NX_Migration\DockerReleases' + '\\' + release['id'].replace('-', '')[:12] + r'\nx'
    assert Path(release['folder']).is_dir()
    assert (source / TOOL_DATABASE).read_text(encoding='utf-8').endswith('#END_DATA\n')
    for wrong in (str(source), release['host_folder'] + '_old', str(Path(release['host_folder']).parent)):
        log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + wrong, encoding='utf-8')
        with pytest.raises(DomainError, match='approval blocked'):
            store.review_release(release['id'], {key: True for key in FUNCTIONAL_TESTS},
                                 'Qualified fixture review only, not real NX.', 'test', 'admin',
                                 store.scan_log('test')['sha256'],digest(release['manifest']))
    log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n' + release['host_folder'].replace('\\', '/').upper() + '/MACH/resource/file.prt', encoding='utf-8')
    assert store.review_release(release['id'], {key: True for key in FUNCTIONAL_TESTS},
                                'Qualified fixture review only, not real NX.', 'test', 'admin',
                                store.scan_log('test')['sha256'],digest(release['manifest']))['status'] == 'approved'
    store.activate(release['id'], 0, 'admin')
    restarted = Store(root, {'test': source}, {'test': log}, display_paths=mapping)
    assert restarted.active()['host_folder'] == release['host_folder']
    assert restarted.active()['generation'] == 1
    assert restarted.get_release(release['id'])['manifest'] == release['manifest']


def test_linux_rejects_windows_settings_before_lookup(monkeypatch):
    monkeypatch.setattr('nxserver.safety.os', SimpleNamespace(name='posix'))
    with pytest.raises(DomainError, match='Windows filesystem paths'):
        offline_path(r'C:\Siemens\NX2512')


def test_ancestors_are_checked_before_following_intermediate_link(tmp_path, monkeypatch):
    linked = tmp_path / 'alias'
    target = linked / 'do-not-read' / 'file'
    seen = []
    def lstat(path):
        seen.append(path)
        return SimpleNamespace(st_mode=stat.S_IFLNK if path == linked else stat.S_IFDIR)
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(DomainError, match='Symbolic links'):
        offline_path(target)
    assert linked in seen and target not in seen and target.parent not in seen


def test_runtime_build_context_is_allowlisted_and_has_no_test_dependencies():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / 'requirements-runtime.txt').read_text()
    assert 'pytest' not in runtime and 'httpx' not in runtime and 'playwright' not in runtime
    dockerfile = (root / 'Dockerfile').read_text()
    assert 'USER 10001:10001' in dockerfile and '--only-binary=:all:' in dockerfile
    assert 'COPY nxserver ./nxserver' in dockerfile and 'COPY . ' not in dockerfile
    ignore = (root / '.dockerignore').read_text()
    assert '**\n' in ignore and '!nxserver/**/' in ignore and '!nxserver/**/*.py' in ignore
    assert '!nxserver/**\n' not in ignore and '!nxserver/**/*.json' not in ignore and '!nxserver/**/*.txt' not in ignore
    assert '!nxserver/web/vendor/LICENSE-*.txt' in ignore and '!nxserver/web/vendor/provenance.json' in ignore
    assert '!settings.example.json' not in ignore


@pytest.mark.skipif(not os.environ.get('NX_COMPOSE_BIN'), reason='Optional standalone Compose validator.')
def test_real_compose_resolves_windows_readonly_sources_and_persistent_data(tmp_path):
    project = Path(__file__).resolve().parents[2]
    for name in ('compose.yaml', 'compose.windows.yaml'):
        shutil.copyfile(project / name, tmp_path / name)
    (tmp_path / 'docker' / 'secrets').mkdir(parents=True)
    (tmp_path / 'docker' / 'secrets' / 'admin_token.txt').write_text('isolated-test-only-' + 'x' * 40)
    for name in ('settings.json', 'settings.windows.json'):
        shutil.copyfile(project / 'docker' / name, tmp_path / 'docker' / name)
    (tmp_path / 'server').mkdir()
    response = subprocess.run([os.environ['NX_COMPOSE_BIN'], '-f', str(tmp_path / 'compose.yaml'),
        '-f', str(tmp_path / 'compose.windows.yaml'), 'config', '--format', 'json'],
        cwd=tmp_path, capture_output=True, text=True, check=True)
    config = json.loads(response.stdout)
    service = config['services']['nxserver']
    assert service['ports'][0]['host_ip'] == '127.0.0.1'
    assert service['read_only'] and service['restart'] == 'unless-stopped'
    assert service['environment']['NX_ADMIN_TOKEN_FILE'] == '/run/secrets/nx_admin_token'
    mounts = {v['target']: v for v in service['volumes']}
    assert mounts['/data']['type'] == 'volume'
    assert mounts['/data/r']['type'] == 'bind' and not mounts['/data/r'].get('read_only', False)
    for target in ('/inputs/nx2512-managed', '/standards/2406', '/standards/2512', '/logs/nx-test'):
        assert mounts[target]['read_only'] and not mounts[target]['bind']['create_host_path']
    assert len(mounts) == 7
    assert all('siemensshare' not in v.get('source', '').lower() for v in mounts.values())
    assert 'NX_ADMIN_TOKEN' not in service['environment']
