"""Opt-in REAL Docker build/start/restart, with disposable synthetic data only.

NX_DOCKER_TEST=1 python -m pytest tests/test_docker_integration.py -q -s
Requires local Docker Engine/Desktop and Compose. No production mount or data.
"""
import json
import os
import shutil
import socket
import subprocess
import urllib.request
import uuid
from pathlib import Path

import pytest

from docker_fixture_policy import validate_fixture_profile


@pytest.mark.skipif(os.environ.get('NX_DOCKER_TEST') != '1', reason='Opt-in actual local Docker Engine integration.')
def test_image_runtime_readonly_sources_and_persistence_after_recreation(tmp_path):
    docker = shutil.which('docker') or shutil.which('docker.exe')
    assert docker, 'Docker CLI is required for NX_DOCKER_TEST=1.'
    project_root = Path(__file__).resolve().parents[2]
    project_name = 'nx-docker-test-' + uuid.uuid4().hex[:12]
    # Never replace the normal nx-management:local image during an isolated test.
    image_name = project_name + ':isolated'
    source = tmp_path / 'source'
    database = source / 'MACH/resource/library/tool/metric/tool_database.dat'
    database.parent.mkdir(parents=True)
    original = b'#CLASS TEST\nFORMAT LIBRF DESCR\nDATA |STD|Standard\n#END_DATA\n'
    database.write_bytes(original)
    releases = tmp_path / 'test-releases'
    releases.mkdir()
    releases.chmod(0o777)  # Disposable fixture only; permit container UID 10001 on Linux.
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'data_dir': '/data', 'sources': {'fixture': '/inputs/fixture'}, 'logs': {}}))
    secret = tmp_path / 'token.txt'
    token = 'isolated-container-test-not-live-' + uuid.uuid4().hex
    secret.write_text(token)
    override = tmp_path / 'override.json'
    override.write_text(json.dumps({'services': {'nxserver': {
        'image': image_name,
        'build': {'context': str(project_root / 'server')},
        'volumes': [{'type': 'bind', 'source': str(settings), 'target': '/app/config/settings.json', 'read_only': True, 'bind': {'create_host_path': False}},
                    {'type': 'bind', 'source': str(source), 'target': '/inputs/fixture', 'read_only': True, 'bind': {'create_host_path': False}},
                    {'type': 'bind', 'source': str(releases), 'target': '/data/r', 'bind': {'create_host_path': False}}],
        'restart': 'no'}}, 'secrets': {'nx_admin_token': {'file': str(secret)}}}))
    # A unique project alone is insufficient: edited Compose files can name
    # another application's volume. Validate the resolved names before up/down.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    environment = {**os.environ, 'NX_WEB_PORT': str(port)}
    if environment.get('DOCKER_HOST'):
        assert environment['DOCKER_HOST'].startswith(('unix://', 'npipe:////./pipe/')), 'Use a local daemon, not DOCKER_HOST tcp/ssh.'
    def execute(arguments, timeout=600):
        return subprocess.run([docker] + arguments, env=environment, capture_output=True, text=True,
                              check=True, timeout=timeout).stdout.strip()
    context = execute(['context', 'show'])
    endpoint = execute(['context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}'])
    assert endpoint.startswith(('unix://', 'npipe:////./pipe/')), 'This test only supports a local daemon.'
    assert execute(['info', '--format', '{{.OSType}}']) == 'linux'
    prefix = ['compose', '-p', project_name, '--project-directory', str(tmp_path),
              '-f', str(project_root / 'compose.yaml'), '-f', str(override)]
    resolved = json.loads(execute(prefix + ['config', '--format', 'json']))
    approved = validate_fixture_profile(resolved, project_name, port, tmp_path, project_root / 'server')
    # Even a matching random name must never adopt an existing resource.
    # Fail before the try/finally cleanup if any owned target is already present.
    probes = [
        ['volume','ls','--filter','name='+project_name+'_management-data','--format','{{.Name}}'],
        ['network','ls','--filter','name='+project_name+'_default','--format','{{.Name}}'],
        ['ps','-a','--filter','label=com.docker.compose.project='+project_name,'--format','{{.ID}}'],
        ['image','ls','--filter','reference='+image_name,'--format','{{.ID}}'],
    ]
    for probe in probes:
        assert not execute(probe), 'Pre-existing test resources detected; no start or cleanup authorized.'
    # Pin the approved resolved profile. Later edits/interpolation of the source
    # YAML must not redirect either startup or the destructive test-only cleanup.
    frozen = tmp_path / 'verified-compose.json'
    with frozen.open('x', encoding='utf-8') as output:
        json.dump(approved, output)
    prefix = ['compose', '-p', project_name, '--project-directory', str(tmp_path), '-f', str(frozen)]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def api(path, data=None):
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/v1/{path}',
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(request, timeout=10) as response:
            return json.load(response)
    try:
        execute(prefix + ['up', '-d', '--build', '--wait', '--wait-timeout', '120'])
        assert api('resources') == []
        identifier = execute(prefix + ['ps', '-q', 'nxserver'])
        inspection = json.loads(execute(['inspect', identifier]))[0]
        assert inspection['Config']['User'] == '10001:10001'
        assert inspection['HostConfig']['ReadonlyRootfs']
        assert not next(v for v in inspection['Mounts'] if v['Destination'] == '/inputs/fixture')['RW']
        assert next(v for v in inspection['Mounts'] if v['Destination'] == '/data/r')['RW']
        assert all(v['Destination'] != '/var/run/docker.sock' for v in inspection['Mounts'])
        record = api('resources', {'kind': 'tool', 'name': 'Container fixture', 'cam_status': 'draft', 'asset_ids': []})
        release = api('releases', {'source': 'fixture', 'nx_version': '2512', 'name': 'Synthetic test release'})
        assert release['status'] == 'draft' and len(release['manifest']) == 1
        assert (releases / release['id'].replace('-', '')[:12] / 'nx' / database.relative_to(source)).read_bytes() == original
        assert token not in execute(prefix + ['logs', '--no-color'])
        # Recreate the container, retaining this test's named volume.
        execute(prefix + ['down'])
        execute(prefix + ['up', '-d', '--wait', '--wait-timeout', '120'])
        assert api('resources')[0]['id'] == record['id']
        assert api('releases/' + release['id'])['manifest'] == release['manifest']
        assert not api('active')['release_id']
        assert database.read_bytes() == original
    finally:
        # Use only the approved pinned profile and its exact owned volume name.
        # Never use prune, production names or the nx-management project here.
        execute(prefix + ['down', '--volumes'], timeout=120)
