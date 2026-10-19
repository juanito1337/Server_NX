#requires -Version 5.1
# Shared implementation: used by the project wrapper and server test packages.
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath, [switch]$CheckOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXOfflineSafety.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'NXUIProfile.psm1') -Force
Assert-NXOfflinePath -Path $ConfigPath
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.SchemaVersion -ne 1) { throw 'Unknown test configuration schema.' }
$stage = [string]$config.StagingRoot
$install = [string]$config.NXInstallRoot
$test = [string]$config.TestRoot
foreach ($path in @($stage, $install, $test)) { Assert-NXOfflinePath -Path $path }
Assert-NXDisjointPaths -First $test -Second $stage
Assert-NXDisjointPaths -First $test -Second $install
$installationMetadata = Get-NXInstallationVersionMetadata -InstallationRoot $install
Write-Host "[INFO] NX installation metadata: $($installationMetadata.ProductVersion). Metadata only; no signature, license or runtime approval."
Write-Host '[INFO] Checking the local test folder (no NX start yet). Please keep this window open.'
Write-Host '[INFO] Checking the staging tree for junctions and symbolic links...'
Assert-NXTreeNoReparsePoint -Root $stage
Assert-NXTreeNoReparsePoint -Root $test
$checkedFiles = 0
Write-Host "[INFO] Verifying SHA-256 of $($config.Files.Count) files. This can take several minutes."
foreach ($entry in $config.Files) {
    if ($entry.RelativePath -match '(^[\\/]|(^|[\\/])\.\.([\\/]|$)|:)' -or $entry.Sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Unsafe manifest entry.' }
    $path = Join-Path $stage $entry.RelativePath
    Assert-NXOfflinePath -Path $path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path -Force).Length -ne $entry.Size -or
        (Get-FileHash -LiteralPath $path).Hash -ne $entry.Sha256) { throw "Staging changed: $($entry.RelativePath)" }
    $checkedFiles++
    if ($checkedFiles % 250 -eq 0 -or $checkedFiles -eq $config.Files.Count) {
        Write-Host "[INFO] Verified $checkedFiles / $($config.Files.Count) files."
    }
}
$markerPath = Join-Path $stage '_NX2512_MIGRATION_STAGING.txt'
$includeMarker = $null -ne $config.PSObject.Properties['IncludeStagingMarker'] -and $config.IncludeStagingMarker -eq $true
$actualFiles = @(Get-ChildItem -LiteralPath $stage -File -Recurse -Force | Where-Object { $includeMarker -or $_.FullName -ne $markerPath })
if ($actualFiles.Count -ne $config.Files.Count) { throw 'Additional or missing staging files detected.' }
foreach ($entry in $config.ConfigurationFiles) {
    Assert-NXOfflinePath -Path $entry.Path
    if (-not ([System.IO.Path]::GetFullPath($entry.Path)).StartsWith(([System.IO.Path]::GetFullPath($test).TrimEnd('\') + '\'), [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Configuration files must stay inside the separate test directory.' }
    if ((Get-FileHash -LiteralPath $entry.Path).Hash -ne $entry.Sha256) { throw "Test configuration changed: $($entry.Path)" }
}
Write-Host '[OK] Complete staging and test configuration SHA-256 checks passed.'
$uiProfile=$null
if ($null -ne $config.PSObject.Properties['UIProfile']) { $uiProfile=$config.UIProfile }
$uiRole=Get-NXUIRolePath -UIProfile $uiProfile -StagingRoot $stage -Files @($config.Files)
if ($uiRole) { Write-Host "[INFO] Explicit managed UI role: $uiRole" }
$nxSearchPath = Get-NXOfflineSearchPath -PreferredDirectories @("$install\NXBIN", "$install\UGII") -InheritedPath $env:PATH
$envValues = @{
    SPLM_APPL_DIR = (Split-Path $install -Parent); SPLM_SHR_DIR = (Split-Path $stage -Parent); SPLM_TMP_DIR = "$test\temp"
    NX_INST_DIR = 'NX2512'; NX_VERSION_DIR = 'NX2512'; NX_SHR_VERSION_DIR = 'NX2512'; NX_PLATFORM = 'win64'
    UGII_BASE_DIR = $install; UGII_ROOT_DIR = "$install\UGII"; UGII_ENV_FILE = ''; UGII_SITE_DIR = "$stage\UGII"
    UGII_USER_DIR = "$test\user"; NX_DEFAULT_SETTINGS_DIR = "$test\defaults"; UGII_LOCAL_USER_DEFAULTS = "$test\defaults\nx2512_test_user.dpv"
    UGII_VENDOR_DIR = ''; UGII_DEFAULT_ROLE = $uiRole; UGII_LOAD_OPTIONS = ''
    UGII_CUSTOM_DIRECTORY_FILE = "$test\custom_dirs.dat"; UGII_TMP_DIR = "$test\temp"; UGII_UGSOLIDS_TMP = "$test\temp"
    UGII_KEEP_SYSTEM_LOG = 'yes'; UGII_LANG = 'german'; UGII_SURFACE_FINISH = 'ON'; UGII_WELD_SYMBOL = 'ON'
    UGII_10_FILE = "$stage\UGII\10.ugf"; UGII_ENGLISH_THREADS = "$stage\UGII\thd_english.dat"; UGII_METRIC_THREADS = "$stage\UGII\thd_metric.dat"
    UGII_UDF_DEFINITION_FILE = "$stage\UGII\udf\dbc_udf_ascii.def"; UGII_UDF_DATABASE_FILE = "$stage\UGII\udf\udf_database.txt"
    UGII_UDF_LIBRARY_DIR = "$stage\UGII\udf\resource\metric"; UGII_UDF_EVENT_FILE = "$stage\UGII\udf\dbc_udf_ascii.tcl"
    UGII_PHYS_MATERIAL_LIB_DIR = "$stage\UGII\materials\german\"; UGII_PHYS_MATERIAL_LIB_DATA_DIR = "$stage\UGII\materials\"
    UGII_PHYS_MATERIAL_LIB_PATH = ".;$stage\UGII\materials\"; UGII_CAM_BASE_DIR = "$install\MACH\"
    UGII_CAM_CUSTOM_DIR = "$stage\MACH\custom\"; UGII_CAM_RESOURCE_DIR = "$stage\MACH\resource\"
    UGII_CAM_TEMPLATE_PART_DIR = "$stage\MACH\resource\template_part\"
    UGII_CAM_TEMPLATE_PART_METRIC_DIR = "$stage\MACH\resource\template_part\metric\"
    UGII_CAM_TEMPLATE_PART_ENGLISH_DIR = "$stage\MACH\resource\template_part\english\"
    UGII_CAM_TEMPLATE_IMAGE_DIR = "$stage\MACH\resource\template_part\images\"
    UGII_CAM_TEMPLATE_SET_DIR = "$stage\MACH\resource\template_set\"
    UGII_CAM_CONFIG_DIR = "$stage\MACH\resource\configuration\"
    UGII_CAM_FILE_SEARCH_PATH = ".;$install\MACH\;$stage\MACH\"; UGII_CAM_LIBRARY_DIR = "$stage\MACH\resource\library\"
    UGII_CAM_LIBRARY_TOOL_DIR = "$stage\MACH\resource\library\tool\"; UGII_ENABLE_CUTTER_SPIN = '1'; UGII_CAM_CM_TM_REFINE = 'y'
    UGII_INSPECTION_BASE_DIR = "$install\CMM_INSPECTION\"; UGII_INSPECTION_RESOURCE_DIR = "$stage\CMM_INSPECTION\resource\"
    UGII_UGSMD_STDS_LIBRARY = "$stage\UGII\"; UGII_SDI_CONFIG = "$stage\NXPLOT\config"; UGII_SDI_SERVER_CFG_DIR = "$stage\NXPLOT\config\pm_server"
    CX_DLL_SYSDIR = "$stage\MACH\resource\usertools\"; DMG_NX_TOOLS = "$stage\MACH\resource\usertools\"
    PATH = $nxSearchPath
}
foreach ($entry in @(Get-ChildItem Env: | Where-Object { $_.Name -match '^(UGII_|NX_|SPLM_|DMG_NX_TOOLS$|CX_DLL_SYSDIR$)' })) {
    # Controlled variables are replaced below in the CHILD process, never used as inherited paths.
    # Other NX variables must still not introduce a production/network reference.
    if (-not $envValues.ContainsKey($entry.Name) -and
        $entry.Value -match '(?i)(W:[\\/]|[\\/]SiemensShare[\\/]nx2406|\\\\[^\\])') {
        throw "Inherited NX environment contains a protected/network reference: $($entry.Name)"
    }
}
$exe = Join-Path $install 'NXBIN\ugraf.exe'
Assert-NXOfflinePath -Path $exe
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'NX2512 executable not found.' }
$latestInstallationMetadata = Get-NXInstallationVersionMetadata -InstallationRoot $install
if ($latestInstallationMetadata.Sha256 -cne $installationMetadata.Sha256) { throw 'NX2512 version metadata file changed during preflight; run a fresh check.' }
if ($CheckOnly) { Write-Host '[OK] NX not started; no runtime files created. Environment bindings and executable path checked.'; return }
foreach ($name in @('temp', 'user', 'defaults')) { New-Item -ItemType Directory -Path (Join-Path $test $name) -Force | Out-Null }
$logPath = Join-Path $test 'temp\nx-test-launcher.log'
Assert-NXOfflinePath -Path $logPath
function Write-LaunchMessage([string]$Message) {
    Write-Host $Message
    Add-Content -LiteralPath $logPath -Value ((Get-Date -Format 's') + ' ' + $Message) -Encoding UTF8
}
$process = New-Object System.Diagnostics.Process
try {
    $process.StartInfo.FileName = $exe
    $process.StartInfo.Arguments = '-nx'
    $process.StartInfo.WorkingDirectory = $test
    $process.StartInfo.UseShellExecute = $false
    foreach ($name in $envValues.Keys) { $process.StartInfo.EnvironmentVariables[$name] = $envValues[$name] }
    Write-LaunchMessage "[INFO] Starting NX2512: $exe"
    if (-not $process.Start()) { throw 'Windows did not create the NX process.' }
    Write-LaunchMessage "[OK] NX process created (PID $($process.Id)). System logs: $test\temp"
    if ($process.WaitForExit(2000)) {
        if ($process.ExitCode -ne 0) { throw "NX exited immediately with exit code $($process.ExitCode). Check the NX system log in $test\temp." }
        Write-LaunchMessage '[WARN] NX launcher exited with code 0. This does not confirm that the NX window opened.'
    }
    else { Write-LaunchMessage '[OK] NX process is still running. Check the NX window; startup is not a functional approval.' }
}
catch {
    Write-LaunchMessage ('[ERROR] ' + $_.Exception.Message)
    throw
}
finally { $process.Dispose() }
