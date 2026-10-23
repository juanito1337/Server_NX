"""PATH isolation; forbidden paths are data, never the test process environment."""
import json
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from nxserver.safety import offline_path
from test_testlaunch import launcher, package, executable_launcher, POWERSHELL

ASSETS = Path(__file__).parents[1] / 'nxserver/testlaunch_assets'
WINDOWS = pytest.mark.skipif(os.name != 'nt' or not POWERSHELL, reason='Native PowerShell PATH test; no actual NX.')


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def run_ps(source, **parameters):
    executable = offline_path(POWERSHELL)
    arguments = ' '.join('-' + key + ' ' + ps_literal(value) for key, value in parameters.items())
    return subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command',
        '& {' + source + '} ' + arguments], capture_output=True, text=True, timeout=30)


INVOKE = r'''
param([string]$ModulePath,[string]$Preferred,[string]$Inherited)
$ErrorActionPreference='Stop'
Import-Module $ModulePath -Force
$before=$env:PATH
$result=Get-NXOfflineSearchPath -PreferredDirectories @((ConvertFrom-Json $Preferred)) -InheritedPath $Inherited
[pscustomobject]@{path=$result;parent_unchanged=($env:PATH -ceq $before)} | ConvertTo-Json -Compress
'''


def search(preferred, inherited):
    return run_ps(INVOKE, ModulePath=ASSETS / 'NXOfflineSafety.psm1',
        Preferred=json.dumps([str(path) for path in preferred]), Inherited=inherited)


@WINDOWS
@pytest.mark.parametrize('executable,search_path',[
    (r'W:\never-read\probe.exe',''),(r'w:relative.exe',''),
    (r'\\never-read\share\probe.exe',''),(r'\\?\W:\never-read\probe.exe',''),
    ('probe.exe',r'W:\never-read;\\never-read\share;relative;C:relative')])
def test_executable_resolution_rejects_protected_text_before_lookup(executable,search_path):
    source=r'''
    param([string]$ModulePath,[string]$Executable,[string]$Search)
    $ErrorActionPreference='Stop'
    Import-Module $ModulePath -Force
    function global:Test-Path { throw 'Unexpected filesystem lookup' }
    function global:Get-Item { throw 'Unexpected filesystem lookup' }
    function global:Get-ChildItem { throw 'Unexpected filesystem lookup' }
    try { Resolve-NXOfflineExecutable -FilePath $Executable -SearchPath $Search; exit 7 }
    catch { if ($_.Exception.Message -like '*Unexpected filesystem*') { throw }; Write-Output 'REJECTED_BEFORE_LOOKUP' }
    '''
    result=run_ps(source,ModulePath=ASSETS/'NXOfflineSafety.psm1',Executable=executable,Search=search_path)
    assert result.returncode==0 and 'REJECTED_BEFORE_LOOKUP' in result.stdout,result.stdout+result.stderr


@WINDOWS
def test_executable_resolution_returns_one_first_match_without_changing_parent_path(tmp_path):
    first=tmp_path/'first';second=tmp_path/'second';first.mkdir();second.mkdir()
    for folder in (first,second):(folder/'probe.exe').write_bytes(b'Inert; never executed by this resolution test.')
    source=r'''
    param([string]$ModulePath,[string]$Search)
    $ErrorActionPreference='Stop';Import-Module $ModulePath -Force
    $before=$env:PATH;$resolved=Resolve-NXOfflineExecutable -FilePath 'probe.exe' -SearchPath $Search
    [pscustomobject]@{executable=$resolved;is_string=($resolved -is [string]);parent_unchanged=($env:PATH -ceq $before)} | ConvertTo-Json -Compress
    '''
    result=run_ps(source,ModulePath=ASSETS/'NXOfflineSafety.psm1',Search=str(first)+';'+str(second))
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout);assert value=={'executable':str(first/'probe.exe'),'is_string':True,'parent_unchanged':True}


@WINDOWS
@pytest.mark.parametrize('content',[b'',bytes(range(256))*4096],ids=['empty','streaming'])
def test_native_hash_does_not_depend_on_host_filehash_cmdlet_and_releases_handle(tmp_path,content):
    path=tmp_path/'hash input $probe.bin';path.write_bytes(content)
    source=r'''
    param([string]$ModulePath,[string]$InputFile)
    $ErrorActionPreference='Stop';Import-Module $ModulePath -Force
    function global:Get-FileHash { throw 'Host hashing cmdlet must not be used' }
    $result=Get-NXFileHash -LiteralPath $InputFile -Algorithm SHA256
    $exclusive=[System.IO.File]::Open($InputFile,[System.IO.FileMode]::Open,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None)
    $exclusive.Dispose();$result | ConvertTo-Json -Compress
    '''
    result=run_ps(source,ModulePath=ASSETS/'NXOfflineSafety.psm1',InputFile=path)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['Hash']==hashlib.sha256(content).hexdigest().upper()
    assert path.read_bytes()==content


@WINDOWS
@pytest.mark.parametrize('value',[r'W:\never-read\hash.bin',r'w:relative.bin',r'\\never-read\share\hash.bin'])
def test_native_hash_rejects_protected_text_before_any_lookup(value):
    source=r'''
    param([string]$ModulePath,[string]$InputFile)
    $ErrorActionPreference='Stop';Import-Module $ModulePath -Force
    function global:Test-Path { throw 'Unexpected filesystem lookup' }
    function global:Get-Item { throw 'Unexpected filesystem lookup' }
    function global:Get-ChildItem { throw 'Unexpected filesystem lookup' }
    try { Get-NXFileHash -LiteralPath $InputFile; exit 7 }
    catch { if ($_.Exception.Message -like '*Unexpected filesystem*') { throw }; Write-Output 'REJECTED_BEFORE_LOOKUP' }
    '''
    result=run_ps(source,ModulePath=ASSETS/'NXOfflineSafety.psm1',InputFile=value)
    assert result.returncode==0 and 'REJECTED_BEFORE_LOOKUP' in result.stdout,result.stdout+result.stderr


@WINDOWS
@pytest.mark.parametrize('bad', [
    r'W:\SiemensShare\nx2406', r'w:/different-production-alias', r'\\forbidden.invalid\nx',
    r'\\?\C:\device', r'\\.\C:\device', r'\root-relative', 'relative', r'C:relative',
    '.', '..', '', r'%SystemRoot%\System32', r'C:\%UNKNOWN_NX_PATH%\bin',
    r'C:\SiemensShare\nx2406', '"W:\\SiemensShare\\nx2406"', '"C:\\Windows\\System32',
])
def test_unsafe_inherited_entries_are_data_only_and_never_enter_child_path(tmp_path, bad):
    result = search([tmp_path], bad)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value['path'] == str(tmp_path) and value['parent_unchanged']


@WINDOWS
def test_local_order_quotes_case_duplicates_missing_paths_and_files(tmp_path):
    first, second, third = [tmp_path / name for name in ('NXBIN', 'UGII', 'local tools')]
    for directory in (first, second, third):
        directory.mkdir()
    file = tmp_path / 'not-directory'
    file.write_bytes(b'Never read as PATH content')
    inherited = ';'.join([str(second).upper(), ' "' + str(third) + '" ', str(first) + '\\',
        str(tmp_path / 'absent'), str(file), '', '.', str(third)])
    result = search([first, second], inherited)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value['path'].split(';') == list(map(str, (first, second, third))) and value['parent_unchanged']


@WINDOWS
@pytest.mark.parametrize('bad', [r'W:\not-read', r'\\forbidden.invalid\nx', 'relative', r'C:relative'])
def test_unsafe_preferred_nx_directory_fails_closed_even_with_safe_host_fallback(tmp_path, bad):
    result = search([bad], str(tmp_path))
    assert result.returncode != 0 and 'absolute local paths' in result.stderr


@WINDOWS
def test_no_verified_local_directory_does_not_produce_empty_cwd_path():
    result = search([], ';.;relative;W:\\not-read')
    assert result.returncode != 0 and 'No verified local' in result.stderr


@WINDOWS
def test_guard_precedes_optional_metadata_lookup_and_required_guard_failure_is_not_hidden():
    source = r'''
param([string]$ModulePath)
$ErrorActionPreference='Stop'
$module=Import-Module $ModulePath -Force -PassThru
& $module {
    $script:checked=New-Object 'System.Collections.Generic.List[string]'
    $script:lookedUp=New-Object 'System.Collections.Generic.List[string]'
    function script:Assert-NXOfflinePath { param([string]$Path)
        [void]$script:checked.Add($Path)
        if ($Path.StartsWith('Z:',[StringComparison]::OrdinalIgnoreCase)) { throw 'Synthetic mapped-drive/junction rejection, no actual Z: query.' }
    }
    function script:Test-Path { param([string]$LiteralPath,[string]$PathType)
        [void]$script:lookedUp.Add($LiteralPath)
        if ($LiteralPath.StartsWith('Z:',[StringComparison]::OrdinalIgnoreCase)) { throw 'Guard was bypassed.' }
        return $true
    }
    $result=Get-NXOfflineSearchPath -PreferredDirectories @('C:\safe') -InheritedPath 'W:\never;\\forbidden.invalid\nx;Z:\synthetic-alias;C:\other'
    $requiredBlocked=$false
    try { Get-NXOfflineSearchPath -PreferredDirectories @('Z:\synthetic-alias') -InheritedPath 'C:\other' | Out-Null }
    catch { $requiredBlocked=$true }
    [pscustomobject]@{path=$result;checked=@($script:checked);looked_up=@($script:lookedUp);required_blocked=$requiredBlocked} | ConvertTo-Json -Compress
}
'''
    result = run_ps(source, ModulePath=ASSETS / 'NXOfflineSafety.psm1')
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value['path'] == r'C:\safe;C:\other' and value['required_blocked']
    assert not any(path.startswith(('W:', '\\\\', 'Z:')) for path in value['looked_up'])
    assert value['checked'].count(r'Z:\synthetic-alias') == 2


@WINDOWS
def test_protected_preferred_directory_is_not_hidden_by_safe_host_fallback(tmp_path):
    result = search([r'C:\SiemensShare\nx2406'], str(tmp_path))
    assert result.returncode != 0 and 'protected production directory' in result.stderr


@WINDOWS
@pytest.mark.parametrize('separator', [';', '"'])
def test_preferred_directory_cannot_introduce_extra_path_entries(tmp_path, separator):
    directory = tmp_path / ('safe' + separator + 'relative-entry')
    if separator == ';':
        directory.mkdir()  # A semicolon is a valid directory name, not a safe PATH entry.
    result = search([directory], str(tmp_path))
    assert result.returncode != 0 and 'without PATH separators or quotes' in result.stderr


@WINDOWS
@pytest.mark.parametrize('starter', ['runtime', 'package', 'project'])
def test_real_child_receives_normalized_local_path_only_without_changing_caller(executable_launcher, starter):
    store, release, extracted, install, runtime, output, env, prepare = executable_launcher
    (install / 'UGII').mkdir(exist_ok=True)
    path_output = output.parent / 'child-path.txt'
    env['NX_TEST_PATH_OUTPUT'] = str(path_output)
    before = os.environ.get('PATH')
    # Only safe local entries in the actual subprocess environment. Forbidden
    # strings are covered separately without exposing any loader to them.
    inherited = env['PATH']
    env['PATH'] = str(install / 'NXBIN') + ';;.;' + inherited + ';' + str(install / 'UGII')
    directory = runtime if starter == 'runtime' else extracted
    command = '"' + os.environ['COMSPEC'] + '" /d /c call "' + str(directory / 'Start-NX2512-Test.cmd') + '"'
    if starter == 'package':
        command += ' -NXInstallRoot "' + str(install) + '" -TestRoot "' + str(runtime) + '"'
    if starter == 'project':
        invocation = [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(Path(__file__).parents[2] / 'Start-NX2512ManagedTest.ps1'),
            '-ConfigPath', str(runtime / 'test_environment.json')]
    else:
        invocation = command
    result = subprocess.run(invocation, input='\n', capture_output=True, text=True,
        encoding='oem', errors='replace', env=env, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    actual = path_output.read_text(encoding='utf-8-sig').split(';')
    assert actual[:2] == [str(install / 'NXBIN'), str(install / 'UGII')]
    assert len(actual) == len({path.casefold() for path in actual}) and all(Path(path).is_absolute() for path in actual)
    assert not any(not path or path in ('.', '..') or path.startswith(('W:', '\\\\')) for path in actual)
    assert os.environ.get('PATH') == before
    assert store.manifest(Path(release['folder'])) == release['manifest'] and store.active()['release_id'] is None


def test_generated_package_contains_guarded_path_binding_and_matching_script_hashes(launcher):
    store, release = launcher
    files = package(store, release)
    config = json.loads(files['release_test.json'])
    import hashlib
    assert b'function Get-NXOfflineSearchPath' in files['NXOfflineSafety.psm1']
    assert b'PATH = $nxSearchPath' in files['Start-NX2512ManagedTest.ps1']
    assert b'PATH = "$install\\NXBIN;$install\\UGII;$env:PATH"' not in files['Start-NX2512ManagedTest.ps1']
    for name in ('NXOfflineSafety.psm1', 'Start-NX2512ManagedTest.ps1'):
        assert config['ScriptHashes'][name] == hashlib.sha256(files[name]).hexdigest()
