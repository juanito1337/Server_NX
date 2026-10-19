"""Release packages and real PowerShell preparation with synthetic data, never NX."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.safety import DomainError
from nxserver.store import Store, TOOL_DATABASE
from nxserver.testlaunch import ASSETS, windows_reference
import nxserver.store as store_module

ADMIN = 'isolated-launcher-admin-' + 'a' * 40
ERP = 'isolated-launcher-erp-' + 'b' * 40
HEADERS = {'Authorization': 'Bearer ' + ADMIN}


@pytest.fixture
def launcher(tmp_path):
    source = tmp_path / 'source'
    database = source / TOOL_DATABASE
    database.parent.mkdir(parents=True)
    database.write_text('#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |STD|Standard\n#END_DATA\n', encoding='utf-8')
    (source / '_NX2512_MIGRATION_STAGING.txt').write_text('synthetic marker', encoding='utf-8')
    store = Store(tmp_path / 'managed', {'test': source})
    before = store.manifest(source)
    release = store.import_release('test', '2512', 'Launcher fixture', 'test')
    assert before == store.manifest(source)
    return store, release


def package(store, release):
    with zipfile.ZipFile(store.test_launcher_package(release['id'])) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_package_is_automatic_self_contained_and_does_not_change_release(launcher):
    store, release = launcher
    before = store.manifest(Path(release['folder']))
    metadata = release['test_launcher']
    assert metadata and metadata['release_id'] == release['id']
    assert Path(metadata['starter']).is_file()
    assert not Path(metadata['folder']).is_relative_to(Path(release['folder']))
    files = package(store, release)
    assert set(files) == set(ASSETS) | {'Start-NX2512-Test.cmd', 'release_test.json', 'release_manifest.json', 'LIESMICH.txt'}
    config = json.loads(files['release_test.json'])
    expected = json.loads(files['release_manifest.json'])
    assert config['ReleaseId'] == release['id'] and config['NxVersion'] == '2512'
    assert config['ManifestSha256'] == hashlib.sha256(files['release_manifest.json']).hexdigest()
    assert expected['IncludeStagingMarker'] is True
    assert len(expected['Files']) == len(release['manifest'])
    assert any(row['RelativePath'] == '_NX2512_MIGRATION_STAGING.txt' for row in expected['Files'])
    for name, digest in config['ScriptHashes'].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest
    assert store.manifest(Path(release['folder'])) == before == release['manifest']
    assert store.active()['release_id'] is None and not release['review']
    assert store.prepare_test_launcher(release['id'], 'test') == metadata
    assert store.prepare_missing_test_launchers() == []


def test_download_authentication_and_package_integrity(launcher):
    store, release = launcher
    with TestClient(create_app(store, ADMIN, ERP)) as client:
        url = '/api/v1/releases/' + release['id'] + '/test-launcher/package'
        assert client.get(url).status_code == 401
        assert client.get(url, headers={'Authorization': 'Bearer ' + ERP}).status_code == 403
        response = client.get(url, headers=HEADERS)
        assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
        assert zipfile.is_zipfile(io.BytesIO(response.content))
        assert ADMIN.encode() not in response.content and ERP.encode() not in response.content
        path = store.test_launcher_package(release['id'])
        path.write_bytes(b'changed package fixture')
        assert client.get(url, headers=HEADERS).status_code == 409


def test_derived_release_gets_its_own_bound_package(launcher):
    store, release = launcher
    original = store.test_launcher_package(release['id']).read_bytes()
    child = store.import_release('', '', 'Derived fixture', 'test', parent=release['id'])
    assert child['test_launcher']['release_id'] == child['id']
    assert child['test_launcher']['test_root'] != release['test_launcher']['test_root']
    assert json.loads(package(store, child)['release_test.json'])['ReleaseId'] == child['id']
    assert store.test_launcher_package(release['id']).read_bytes() == original


def test_updated_templates_create_a_new_revision_and_preserve_previous_package(launcher, monkeypatch):
    store, release = launcher
    previous = release['test_launcher']
    original_path = store.test_launcher_package(release['id'])
    original_bytes = original_path.read_bytes()
    before = store.manifest(Path(release['folder']))
    original_generator = store_module.package_files
    def updated(*args):
        files, config = original_generator(*args)
        files['Start-NX2512ManagedTest.ps1'] += b'\n# synthetic template update\n'
        config['ScriptHashes']['Start-NX2512ManagedTest.ps1'] = hashlib.sha256(files['Start-NX2512ManagedTest.ps1']).hexdigest()
        files['release_test.json'] = json.dumps(config).encode('utf-8')
        return files, config
    monkeypatch.setattr(store_module, 'package_files', updated)
    monkeypatch.setattr(store_module, 'template_sha256', lambda: 'c' * 64)
    assert store.prepare_missing_test_launchers() == []
    current = store.get_release(release['id'])['test_launcher']
    assert current['package_revision'] != previous['package_revision']
    assert current['template_sha256'] == 'c' * 64
    assert store.test_launcher_package(release['id']) != original_path
    assert original_path.read_bytes() == original_bytes
    assert current['manifest_sha256'] == previous['manifest_sha256']
    assert store.manifest(Path(release['folder'])) == before
    assert store.active()['release_id'] is None
    event = next(row for row in store.audit_entries() if row['action'] == 'release.test_launcher.refreshed')
    payload = json.loads(event['payload'])
    assert payload['previous'] == previous and payload['current'] == current
    assert store.prepare_test_launcher(release['id'], 'test') == current
    assert store.prepare_missing_test_launchers() == []


def test_legacy_unversioned_package_is_retained_on_refresh(launcher):
    store, release = launcher
    previous = dict(release['test_launcher'])
    original_path = store.test_launcher_package(release['id'])
    legacy = original_path.parent.parent
    # Simulate an old server package with no revision/template metadata.
    for path in original_path.parent.iterdir():
        shutil.copyfile(path, legacy / path.name)
    previous.pop('package_revision')
    previous.pop('template_sha256')
    previous['folder'] = str(legacy)
    previous['starter'] = str(legacy / 'Start-NX2512-Test.cmd')
    with store.db() as db:
        db.execute('UPDATE test_launchers SET payload=? WHERE release_id=?', (json.dumps(previous), release['id']))
    assert store.test_launcher_package(release['id']) == legacy / 'NX2512_Test.zip'
    original_bytes = store.test_launcher_package(release['id']).read_bytes()
    # Existing identical artifacts must be verified and reused, never overwritten.
    assert store.prepare_missing_test_launchers() == []
    current = store.get_release(release['id'])['test_launcher']
    assert current['package_revision'] and current['template_sha256']
    assert (legacy / 'NX2512_Test.zip').read_bytes() == original_bytes


@pytest.mark.parametrize('revision', ['../elsewhere', '', 'G' * 64, 'a' * 63])
def test_invalid_stored_package_revision_is_rejected(launcher, revision):
    store, release = launcher
    metadata = dict(release['test_launcher'], package_revision=revision)
    with store.db() as db:
        db.execute('UPDATE test_launchers SET payload=? WHERE release_id=?', (json.dumps(metadata), release['id']))
    with pytest.raises(DomainError, match='revision'):
        store.test_launcher_package(release['id'])


def test_old_draft_is_backfilled_without_changing_its_manifest(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'fixture.txt').write_text('old release fixture', encoding='utf-8')
    store = Store(tmp_path / 'managed', {'test': source})
    # Model an old database release without producing/removing an artifact first.
    release = store.import_release('test', '2406', 'Old fixture', 'test')
    with store.db() as db:
        db.execute("UPDATE releases SET nx_version='2512' WHERE id=?", (release['id'],))
    manifest = store.get_release(release['id'])['manifest']
    assert store.prepare_missing_test_launchers() == []
    after = store.get_release(release['id'])
    assert after['test_launcher'] and after['manifest'] == manifest and after['status'] == 'draft'
    assert store.prepare_missing_test_launchers() == []


def test_changed_or_failed_release_cannot_be_backfilled(launcher):
    store, release = launcher
    (Path(release['folder']) / 'added.txt').write_text('tamper fixture', encoding='utf-8')
    with pytest.raises(DomainError, match='changed outside'):
        store.prepare_test_launcher(release['id'], 'test')
    with store.db() as db:
        db.execute("UPDATE releases SET status='failed' WHERE id=?", (release['id'],))
    with pytest.raises(DomainError, match='incomplete'):
        store.prepare_test_launcher(release['id'], 'test')
    assert store.prepare_missing_test_launchers() == []


def test_package_creation_failure_is_not_a_successful_import(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'fixture.txt').write_text('fixture', encoding='utf-8')
    store = Store(tmp_path / 'managed', {'test': source})
    def fail(*args):
        raise OSError('Synthetic test package write failure')
    monkeypatch.setattr(store, '_create_test_launcher', fail)
    with pytest.raises(OSError):
        store.import_release('test', '2512', 'Must fail', 'test')
    assert store.releases()[0]['status'] == 'failed'
    assert store.releases()[0]['test_launcher'] is None
    assert store.active()['release_id'] is None


def test_linux_host_mapping_uses_only_textual_windows_reference(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'fixture.txt').write_text('fixture', encoding='utf-8')
    root = tmp_path / 'managed'
    store = Store(root, {'test': source}, display_paths={str(root / 'r'): r'C:\NX_Migration\DockerReleases'})
    release = store.import_release('test', '2512', 'Mapped fixture', 'test')
    config = json.loads(package(store, release)['release_test.json'])
    assert config['StagingRoot'] == release['host_folder']
    assert release['test_launcher']['status'] == 'ready'
    if os.name != 'nt':
        assert not (Path.cwd() / config['StagingRoot']).exists()


@pytest.mark.parametrize('path', [r'W:\SiemensShare\nx2406', r'\\server\share', 'relative', '/data/r/test',
                                  r'C:\test\..\outside', r'C:\SiemensShare\nx2406', 'C:\\test\x00'])
def test_unsafe_windows_references_are_rejected_without_access(path):
    with pytest.raises(DomainError):
        windows_reference(path)


POWERSHELL = shutil.which('powershell.exe') if os.name == 'nt' else None


@pytest.fixture
def executable_launcher(launcher, tmp_path):
    """Compile a harmless NX stand-in: it only records its own environment and exits."""
    if not POWERSHELL:
        pytest.skip('Windows child process test, no real NX.')
    store, release = launcher
    extracted = tmp_path / 'package'
    extracted.mkdir()
    for name, content in package(store, release).items():
        (extracted / name).write_bytes(content)
    install = tmp_path / 'NX2512'
    (install / 'NXBIN').mkdir(parents=True)
    executable = install / 'NXBIN' / 'ugraf.exe'
    code = '''using System;
using System.IO;
using System.Reflection;
[assembly: AssemblyCompany("Siemens (synthetic environment recorder)")]
[assembly: AssemblyProduct("Designcenter NX")]
[assembly: AssemblyVersion("2512.8500.0.0")]
[assembly: AssemblyFileVersion("2512.8500.0.0")]
[assembly: AssemblyInformationalVersion("2512.8500")]
public class LauncherProbe {
    public static int Main(string[] args) {
        File.WriteAllLines(Environment.GetEnvironmentVariable("NX_TEST_PROBE_OUTPUT"), new string[] {
            String.Join(" ", args), Environment.CurrentDirectory,
            Environment.GetEnvironmentVariable("SPLM_SHR_DIR"),
            Environment.GetEnvironmentVariable("UGII_BASE_DIR"),
            Environment.GetEnvironmentVariable("UGII_CAM_RESOURCE_DIR"),
            Environment.GetEnvironmentVariable("UGII_CUSTOM_DIRECTORY_FILE")
        });
        string roleOutput=Environment.GetEnvironmentVariable("NX_TEST_ROLE_OUTPUT");
        if (!String.IsNullOrEmpty(roleOutput)) File.WriteAllText(roleOutput, Environment.GetEnvironmentVariable("UGII_DEFAULT_ROLE") ?? "");
        string pathOutput=Environment.GetEnvironmentVariable("NX_TEST_PATH_OUTPUT");
        if (!String.IsNullOrEmpty(pathOutput)) File.WriteAllText(pathOutput, Environment.GetEnvironmentVariable("PATH") ?? "");
        string templateOutput=Environment.GetEnvironmentVariable("NX_TEST_TEMPLATE_OUTPUT");
        if (!String.IsNullOrEmpty(templateOutput)) {
            string[] names={"UGII_CAM_TEMPLATE_PART_DIR","UGII_CAM_TEMPLATE_PART_METRIC_DIR","UGII_CAM_TEMPLATE_PART_ENGLISH_DIR","UGII_CAM_TEMPLATE_IMAGE_DIR","UGII_CAM_TEMPLATE_SET_DIR","UGII_CAM_CONFIG_DIR"};
            string[] values=new string[names.Length];
            for(int index=0; index<names.Length; index++) values[index]=names[index]+"="+(Environment.GetEnvironmentVariable(names[index]) ?? "");
            File.WriteAllLines(templateOutput,values);
        }
        int delay; Int32.TryParse(Environment.GetEnvironmentVariable("NX_TEST_PROBE_DELAY"), out delay);
        System.Threading.Thread.Sleep(delay);
        int exitCode; Int32.TryParse(Environment.GetEnvironmentVariable("NX_TEST_PROBE_EXIT"), out exitCode);
        return exitCode;
    }
}'''
    compilation = "Add-Type -TypeDefinition '" + code + "' -OutputAssembly '" + str(executable).replace("'", "''") + "' -OutputType ConsoleApplication"
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', compilation], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and executable.is_file(), result.stdout + result.stderr
    palette = install / 'MACH' / 'localization' / 'german' / 'ugs_manufacturing_templates_german.pax'
    palette.parent.mkdir(parents=True)
    palette.write_text('<Palette xmlns="urn:test"><PaletteEntry id="STD"/></Palette>', encoding='utf-8')
    runtime = tmp_path / 'runtime'
    output = tmp_path / 'child-environment.txt'
    env = dict(os.environ, SPLM_SHR_DIR=r'W:\SiemensShare\nx2406', NX_TEST_PROBE_OUTPUT=str(output))
    # No access to the value above: the starter must replace it with the local fixture path.
    prepare = [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(extracted / 'Invoke-NXReleaseTest.ps1'),
               '-PackagePath', str(extracted / 'release_test.json'), '-NXInstallRoot', str(install), '-TestRoot', str(runtime), '-CheckOnly']
    result = subprocess.run(prepare, capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not output.exists() and not (runtime / 'temp').exists()
    return store, release, extracted, install, runtime, output, env, prepare


@pytest.mark.parametrize('mode', ['running', 'zero_exit', 'error_exit', 'invalid_executable'])
@pytest.mark.parametrize('starter', ['runtime', 'package'])
def test_cmd_launches_the_child_with_safe_environment_and_reports_failures(executable_launcher, mode, starter):
    store, release, extracted, install, runtime, output, env, prepare = executable_launcher
    env['NX_TEST_PROBE_EXIT'] = '23' if mode == 'error_exit' else '0'
    env['NX_TEST_PROBE_DELAY'] = '3000' if mode == 'running' else '0'
    if mode == 'invalid_executable':
        (install / 'NXBIN' / 'ugraf.exe').write_bytes(b'not an executable')
    # cmd.exe has its own quoting rules, not Python's MSVCRT list2cmdline rules.
    directory = runtime if starter == 'runtime' else extracted
    command = '"' + os.environ['COMSPEC'] + '" /d /c call "' + str(directory / 'Start-NX2512-Test.cmd') + '"'
    if starter == 'package':
        command += ' -NXInstallRoot "' + str(install) + '" -TestRoot "' + str(runtime) + '"'
    result = subprocess.run(command, input='\n', capture_output=True, text=True,
                            encoding='oem', errors='replace', env=env, timeout=60)
    text = result.stdout + result.stderr
    log_path = runtime / 'temp' / 'nx-test-launcher.log'
    if mode == 'invalid_executable':
        assert result.returncode != 0 and 'version metadata' in text,text
        assert not output.exists() and not log_path.exists() and not (runtime/'temp').exists()
        assert store.manifest(Path(release['folder'])) == release['manifest']
        return
    assert log_path.is_file(), text
    log = log_path.read_text(encoding='utf-8-sig')
    assert 'Verifying SHA-256' in text and 'Verified ' in text
    assert ('[ERROR]' in log) == (mode == 'error_exit')
    assert (result.returncode != 0) == (mode == 'error_exit'), text
    args, working, shared, base, resources, dirs = output.read_text(encoding='utf-8-sig').splitlines()
    assert args == '-nx' and Path(working) == runtime
    assert Path(shared) == Path(release['folder']).parent
    assert Path(base) == install
    assert Path(resources) == Path(release['folder']) / 'MACH' / 'resource'
    assert Path(dirs) == runtime / 'custom_dirs.dat'
    assert 'NX process created (PID ' in text
    if mode == 'running':
        assert 'NX process is still running' in text
    elif mode == 'zero_exit':
        assert 'does not confirm that the NX window opened' in text
    elif mode == 'error_exit':
        assert 'exit code 23' in text and 'NX-Test abgebrochen' in text
    assert store.manifest(Path(release['folder'])) == release['manifest']


def test_uncontrolled_inherited_network_reference_still_blocks_before_start(executable_launcher):
    store, release, extracted, install, runtime, output, env, prepare = executable_launcher
    env['UGII_UNCONTROLLED_TEST_PATH'] = r'W:\SiemensShare\nx2406'
    result = subprocess.run(prepare[:-1], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode != 0 and 'UGII_UNCONTROLLED_TEST_PATH' in result.stderr
    assert not output.exists() and not (runtime / 'temp').exists()


@pytest.mark.skipif(not POWERSHELL, reason='Real Windows PowerShell test; never launches NX.')
def test_real_powershell_prepares_pinned_config_and_rejects_tampering(launcher, tmp_path):
    store, release = launcher
    extracted = tmp_path / 'package'
    extracted.mkdir()
    files = package(store, release)
    for name, content in files.items():
        (extracted / name).write_bytes(content)
    install = tmp_path / 'NX2512'
    (install / 'NXBIN').mkdir(parents=True)
    from installation_version_test_support import write_nx_image
    write_nx_image(install,'2512') # Metadata only, still never launched.
    palette = install / 'MACH' / 'localization' / 'german' / 'ugs_manufacturing_templates_german.pax'
    palette.parent.mkdir(parents=True)
    palette.write_text('<Palette xmlns="urn:test"><PaletteEntry id="STD"/></Palette>', encoding='utf-8')
    runtime = tmp_path / 'runtime'
    command = [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(extracted / 'Invoke-NXReleaseTest.ps1'), '-PackagePath', str(extracted / 'release_test.json'),
               '-NXInstallRoot', str(install), '-TestRoot', str(runtime), '-CheckOnly']
    def execute(args=None):
        return subprocess.run(args or command, capture_output=True, text=True, timeout=60)
    first = execute()
    assert first.returncode == 0, first.stdout + first.stderr
    assert 'NX not started' in first.stdout
    config_path = runtime / 'test_environment.json'
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    assert config['StagingRoot'] == release['folder']
    assert config['IncludeStagingMarker'] and len(config['Files']) == len(release['manifest'])
    assert not (runtime / 'temp').exists() and not (runtime / 'user').exists()
    before = config_path.read_bytes()
    assert execute().returncode == 0
    (Path(release['folder']) / TOOL_DATABASE).write_text('tamper fixture', encoding='utf-8')
    changed = execute()
    assert changed.returncode != 0 and 'Staging changed' in changed.stderr
    assert config_path.read_bytes() == before
    blocked = execute(command + ['-StagingRoot', r'W:\SiemensShare\nx2406'])
    assert blocked.returncode != 0 and 'protected production' in blocked.stderr
    (extracted / 'New-NX2512TestPalette.ps1').write_text('# altered fixture', encoding='utf-8')
    changed_package = execute()
    assert changed_package.returncode != 0 and 'Package script changed' in changed_package.stderr
