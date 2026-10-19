"""Native starter version checks on own inert/compiled fixtures, never actual NX."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from nxserver.safety import offline_path
from test_testlaunch import launcher,package,POWERSHELL
from installation_version_test_support import write_nx_image
from test_starter_search_path import run_ps

ASSETS=Path(__file__).parents[1]/'nxserver/testlaunch_assets'
WINDOWS=pytest.mark.skipif(os.name!='nt' or not POWERSHELL,reason='Native client version metadata check; never actual NX.')


def test_preparation_guards_executable_before_first_child_path_lookup():
    source=(ASSETS/'New-NX2512TestEnvironment.ps1').read_text(encoding='utf-8')
    assignment=source.index("$exe = Join-Path $install 'NXBIN\\ugraf.exe'")
    guard=source.index('Assert-NXOfflinePath -Path $exe',assignment)
    lookup=source.index('Test-Path -LiteralPath $exe',assignment)
    assert assignment<guard<lookup


def prepare_inputs(tmp_path,store,release,version='2512'):
    extracted=tmp_path/'package';extracted.mkdir()
    for name,content in package(store,release).items():(extracted/name).write_bytes(content)
    install=tmp_path/'NX2512';write_nx_image(install,version)
    palette=install/'MACH/localization/german/ugs_manufacturing_templates_german.pax'
    palette.parent.mkdir(parents=True);palette.write_text('<Palette xmlns="urn:test"><PaletteEntry id="STD"/></Palette>',encoding='utf-8')
    runtime=tmp_path/'runtime'
    arguments=[str(offline_path(POWERSHELL)),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(extracted/'Invoke-NXReleaseTest.ps1'),'-PackagePath',str(extracted/'release_test.json'),
        '-NXInstallRoot',str(install),'-TestRoot',str(runtime),'-CheckOnly']
    return extracted,install,runtime,arguments


@WINDOWS
@pytest.mark.parametrize('entry',['package','project_prepare','runtime'])
def test_wrong_nx_in_correctly_named_folder_is_blocked_before_runtime_writes(launcher,tmp_path,entry):
    store,release=launcher;extracted,install,runtime,arguments=prepare_inputs(tmp_path,store,release,'2406')
    before=store.manifest(store.release_folder(release['id']))
    if entry=='project_prepare':
        arguments=[str(offline_path(POWERSHELL)),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
            str(Path(__file__).parents[2]/'New-NX2512TestEnvironment.ps1'),'-StagingRoot',release['folder'],
            '-NXInstallRoot',str(install),'-OutputRoot',str(runtime)]
    elif entry=='runtime':
        write_nx_image(install,'2512')
        prepared=subprocess.run(arguments,capture_output=True,text=True,timeout=60)
        assert prepared.returncode==0,prepared.stdout+prepared.stderr
        write_nx_image(install,'2406')
        arguments=[str(offline_path(POWERSHELL)),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
            str(extracted/'Start-NX2512ManagedTest.ps1'),'-ConfigPath',str(runtime/'test_environment.json'),'-CheckOnly']
    result=subprocess.run(arguments,capture_output=True,text=True,timeout=60)
    assert result.returncode!=0,'A folder name must not qualify a different NX version.'
    assert 'version metadata' in result.stdout+result.stderr
    assert not (runtime/'temp').exists() and not (runtime/'user').exists() and not (runtime/'defaults').exists()
    if entry!='runtime':assert not runtime.exists()
    assert store.manifest(store.release_folder(release['id']))==before


METADATA=r'''
param([string]$ModulePath,[string]$Installation)
$ErrorActionPreference='Stop'
Import-Module $ModulePath -Force
Get-NXInstallationVersionMetadata -InstallationRoot $Installation | ConvertTo-Json -Depth 5 -Compress
'''


@WINDOWS
@pytest.mark.parametrize('build',[7001,8500,8501])
def test_client_reports_static_version_and_hash_without_claiming_license_or_signature(tmp_path,build):
    import hashlib
    path=write_nx_image(tmp_path,build=build);before=path.read_bytes()
    result=run_ps(METADATA,ModulePath=ASSETS/'NXOfflineSafety.psm1',Installation=tmp_path)
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert value['DetectedVersion']=='2512' and value['ExpectedVersion']=='2512'
    assert value['ProductVersion']=='2512.'+str(build) and value['ProductVersionParts']==[2512,build,0,0]
    assert value['Sha256']==hashlib.sha256(before).hexdigest() and value['Size']==len(before)
    assert value['MetadataOnly'] and not value['SignatureVerified'] and not value['LicenseVerified'] and not value['NXExecutionVerified']
    assert value['ExePath']==str(path.absolute()) and path.read_bytes()==before


@WINDOWS
@pytest.mark.parametrize('fault',['wrong_version','company','product','filename','file_fixed','product_fixed','file_text','product_text','plain_text','empty','oversize'])
def test_unusable_client_version_metadata_is_not_accepted(tmp_path,fault):
    options={}
    if fault=='company':options['strings']={'CompanyName':'Unrelated company'}
    elif fault=='product':options['strings']={'ProductName':'Unrelated CAM'}
    elif fault=='filename':options['strings']={'OriginalFilename':'different.exe'}
    elif fault=='file_fixed':options['file_version']=(2406,8500,0,0)
    elif fault=='product_fixed':options['product_version']=(2406,8500,0,0)
    elif fault=='file_text':options['strings']={'FileVersion':'2512.8501'}
    elif fault=='product_text':options['strings']={'ProductVersion':'2512.8501'}
    path=write_nx_image(tmp_path,'2406' if fault=='wrong_version' else '2512',**options)
    if fault=='plain_text':path.write_bytes(b'Not an executable; never launched')
    elif fault=='empty':path.write_bytes(b'')
    elif fault=='oversize':
        with path.open('r+b') as stream:stream.truncate(16*1024*1024+1)
    before=path.stat()
    result=run_ps(METADATA,ModulePath=ASSETS/'NXOfflineSafety.psm1',Installation=tmp_path)
    assert result.returncode!=0 and 'version metadata' in result.stderr,result.stdout+result.stderr
    assert path.stat().st_size==before.st_size and path.stat().st_mtime_ns==before.st_mtime_ns


@WINDOWS
@pytest.mark.parametrize('bad',[r'W:\SiemensShare\nx2406',r'\\forbidden.invalid\nx',r'C:\SiemensShare\nx2406'])
def test_client_guard_rejects_protected_paths_before_metadata_lookup(bad):
    source=r'''
param([string]$ModulePath,[string]$Installation)
$ErrorActionPreference='Stop'
$module=Import-Module $ModulePath -Force -PassThru
& $module {
    param([string]$Installation)
    function script:Get-Item { throw 'A metadata lookup must not occur.' }
    Get-NXInstallationVersionMetadata -InstallationRoot $Installation
} $Installation
'''
    result=run_ps(source,ModulePath=ASSETS/'NXOfflineSafety.psm1',Installation=bad)
    assert result.returncode!=0 and 'A metadata lookup must not occur' not in result.stderr
    assert 'protected production directory' in result.stderr or 'local offline path' in result.stderr


@WINDOWS
def test_metadata_read_denies_concurrent_writer_and_releases_its_own_handle(tmp_path):
    path=write_nx_image(tmp_path);before=path.read_bytes()
    source=r'''
param([string]$ModulePath,[string]$Installation)
$ErrorActionPreference='Stop'
$module=Import-Module $ModulePath -Force -PassThru
& $module {
    param([string]$Installation)
    $script:allowed=[System.IO.Path]::GetFullPath($Installation)
    $script:calls=0;$script:writerBlocked=$false
    function script:Assert-NXOfflinePath {
        param([string]$Path)
        if (-not $Path.StartsWith($script:allowed,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected fixture path.' }
        $script:calls++
        if ($script:calls -eq 3) {
            $writer=$null
            try { $writer=[System.IO.File]::Open($Path,'Open','Write','ReadWrite') }
            catch [System.IO.IOException] { $script:writerBlocked=$true }
            finally { if ($null -ne $writer) { $writer.Dispose() } }
        }
    }
    $metadata=Get-NXInstallationVersionMetadata -InstallationRoot $Installation
    [pscustomobject]@{writer_blocked=$script:writerBlocked;metadata=$metadata} | ConvertTo-Json -Depth 5 -Compress
} $Installation
'''
    result=run_ps(source,ModulePath=ASSETS/'NXOfflineSafety.psm1',Installation=tmp_path)
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout);assert value['writer_blocked'] and value['metadata']['DetectedVersion']=='2512'
    assert path.read_bytes()==before
    path.write_bytes(before) # Would fail if the metadata reader leaked its deny-write handle.


@WINDOWS
@pytest.mark.parametrize('version,build',[('2406',8500),('2512',8501)])
def test_installation_drift_during_staging_checks_blocks_before_runtime_writes(tmp_path,version,build):
    from concurrent.futures import ThreadPoolExecutor
    from nxserver.store import Store,TOOL_DATABASE
    # Give the actual preflight a visible hashing phase, using small own files.
    # No instrumented script, PowerShell hook, modified release or NX process.
    source=tmp_path/'s';database=source/TOOL_DATABASE;database.parent.mkdir(parents=True)
    database.write_text('#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |STD|Synthetic\n#END_DATA\n',encoding='utf-8')
    folder=source/'MACH/custom/drift';folder.mkdir(parents=True)
    for index in range(512):(folder/(str(index)+'.txt')).write_text('Own hash preflight fixture '+str(index),encoding='utf-8')
    store=Store(tmp_path/'m',{'test':source});release=store.import_release('test','2512','Own drift fixture','fixture')
    extracted,install,runtime,arguments=prepare_inputs(tmp_path,store,release)
    prepared=subprocess.run(arguments,capture_output=True,text=True,timeout=60)
    assert prepared.returncode==0,prepared.stdout+prepared.stderr
    before={str(path.relative_to(runtime)):path.read_bytes() for path in runtime.rglob('*') if path.is_file()}
    process=subprocess.Popen([str(offline_path(POWERSHELL)),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
        '-File',str(extracted/'Start-NX2512ManagedTest.ps1'),'-ConfigPath',str(runtime/'test_environment.json'),'-CheckOnly'],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    with ThreadPoolExecutor(max_workers=1) as reader:
        try:
            first=reader.submit(process.stdout.readline).result(timeout=15)
            assert 'NX installation metadata: 2512.8500' in first,first
            assert process.poll() is None,'Own preflight must be live before the fixture mutation.'
            write_nx_image(install,version,build=build)
            stdout,stderr=process.communicate(timeout=60)
            assert process.returncode!=0 and 'version metadata' in stderr,first+stdout+stderr
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=10) # Only this own CheckOnly process.
            process.stdout.close();process.stderr.close()
    assert not (runtime/'temp').exists() and not (runtime/'user').exists() and not (runtime/'defaults').exists()
    assert before=={str(path.relative_to(runtime)):path.read_bytes() for path in runtime.rglob('*') if path.is_file()}


@WINDOWS
@pytest.mark.skipif(os.environ.get('NX_STOCK_SCHEMA_TEST')!='1',reason='Opt-in read-only installed NX metadata.')
def test_installed_client_version_is_read_without_starting_nx():
    from nxserver.installation_version import installation_version
    root=offline_path(Path('C:/Siemens/NX2512'));expected=installation_version(root)
    result=run_ps(METADATA,ModulePath=ASSETS/'NXOfflineSafety.psm1',Installation=root)
    assert result.returncode==0,result.stdout+result.stderr
    value=json.loads(result.stdout)
    assert value['ProductVersion']==expected['strings']['ProductVersion'] and value['Sha256']==expected['sha256']
    assert installation_version(root)==expected
