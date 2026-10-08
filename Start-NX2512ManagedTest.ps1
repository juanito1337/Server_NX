#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath, [switch]$CheckOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force
Assert-NXOfflinePath -Path $ConfigPath
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
if ($config.SchemaVersion -ne 1) { throw 'Unknown test configuration schema.' }
$stage = [string]$config.StagingRoot
$install = [string]$config.NXInstallRoot
$test = [string]$config.TestRoot
foreach ($path in @($stage, $install, $test)) { Assert-NXOfflinePath -Path $path }
Assert-NXDisjointPaths -First $test -Second $stage
Assert-NXDisjointPaths -First $test -Second $install
Assert-NXTreeNoReparsePoint -Root $stage
Assert-NXTreeNoReparsePoint -Root $test
foreach ($entry in $config.Files) {
    if ($entry.RelativePath -match '(^[\\/]|(^|[\\/])\.\.([\\/]|$)|:)' -or $entry.Sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Unsafe manifest entry.' }
    $path = Join-Path $stage $entry.RelativePath
    Assert-NXOfflinePath -Path $path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path -Force).Length -ne $entry.Size -or
        (Get-FileHash -LiteralPath $path).Hash -ne $entry.Sha256) { throw "Staging changed: $($entry.RelativePath)" }
}
$markerPath = Join-Path $stage '_NX2512_MIGRATION_STAGING.txt'
$actualFiles = @(Get-ChildItem -LiteralPath $stage -File -Recurse -Force | Where-Object { $_.FullName -ne $markerPath })
if ($actualFiles.Count -ne $config.Files.Count) { throw 'Additional or missing staging files detected.' }
foreach ($entry in $config.ConfigurationFiles) {
    Assert-NXOfflinePath -Path $entry.Path
    if ((Get-FileHash -LiteralPath $entry.Path).Hash -ne $entry.Sha256) { throw "Test configuration changed: $($entry.Path)" }
}
Write-Host '[OK] Complete staging and test configuration SHA-256 checks passed.'
if ($CheckOnly) { Write-Host '[OK] NX not started; no runtime files created.'; return }
foreach ($name in @('temp', 'user', 'defaults')) { New-Item -ItemType Directory -Path (Join-Path $test $name) -Force | Out-Null }
$envValues = @{
    SPLM_APPL_DIR = (Split-Path $install -Parent); SPLM_SHR_DIR = (Split-Path $stage -Parent); SPLM_TMP_DIR = "$test\temp"
    NX_INST_DIR = 'NX2512'; NX_VERSION_DIR = 'NX2512'; NX_SHR_VERSION_DIR = 'NX2512'; NX_PLATFORM = 'win64'
    UGII_BASE_DIR = $install; UGII_ROOT_DIR = "$install\UGII"; UGII_ENV_FILE = ''; UGII_SITE_DIR = "$stage\UGII"
    UGII_USER_DIR = "$test\user"; NX_DEFAULT_SETTINGS_DIR = "$test\defaults"; UGII_LOCAL_USER_DEFAULTS = "$test\defaults\nx2512_test_user.dpv"
    UGII_CUSTOM_DIRECTORY_FILE = "$test\custom_dirs.dat"; UGII_TMP_DIR = "$test\temp"; UGII_UGSOLIDS_TMP = "$test\temp"
    UGII_KEEP_SYSTEM_LOG = 'yes'; UGII_LANG = 'german'; UGII_SURFACE_FINISH = 'ON'; UGII_WELD_SYMBOL = 'ON'
    UGII_10_FILE = "$stage\UGII\10.ugf"; UGII_ENGLISH_THREADS = "$stage\UGII\thd_english.dat"; UGII_METRIC_THREADS = "$stage\UGII\thd_metric.dat"
    UGII_UDF_DEFINITION_FILE = "$stage\UGII\udf\dbc_udf_ascii.def"; UGII_UDF_DATABASE_FILE = "$stage\UGII\udf\udf_database.txt"
    UGII_UDF_LIBRARY_DIR = "$stage\UGII\udf\resource\metric"; UGII_UDF_EVENT_FILE = "$stage\UGII\udf\dbc_udf_ascii.tcl"
    UGII_PHYS_MATERIAL_LIB_DIR = "$stage\UGII\materials\german\"; UGII_PHYS_MATERIAL_LIB_DATA_DIR = "$stage\UGII\materials\"
    UGII_PHYS_MATERIAL_LIB_PATH = ".;$stage\UGII\materials\"; UGII_CAM_BASE_DIR = "$install\MACH\"
    UGII_CAM_CUSTOM_DIR = "$stage\MACH\custom\"; UGII_CAM_RESOURCE_DIR = "$stage\MACH\resource\"
    UGII_CAM_FILE_SEARCH_PATH = ".;$install\MACH\;$stage\MACH\"; UGII_CAM_LIBRARY_DIR = "$stage\MACH\resource\library\"
    UGII_CAM_LIBRARY_TOOL_DIR = "$stage\MACH\resource\library\tool\"; UGII_ENABLE_CUTTER_SPIN = '1'; UGII_CAM_CM_TM_REFINE = 'y'
    UGII_INSPECTION_BASE_DIR = "$install\CMM_INSPECTION\"; UGII_INSPECTION_RESOURCE_DIR = "$stage\CMM_INSPECTION\resource\"
    UGII_UGSMD_STDS_LIBRARY = "$stage\UGII\"; UGII_SDI_CONFIG = "$stage\NXPLOT\config"; UGII_SDI_SERVER_CFG_DIR = "$stage\NXPLOT\config\pm_server"
    CX_DLL_SYSDIR = "$stage\MACH\resource\usertools\"; DMG_NX_TOOLS = "$stage\MACH\resource\usertools\"
    PATH = "$install\NXBIN;$install\UGII;$env:PATH"
}
$saved = @{}
try {
    foreach ($name in $envValues.Keys) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process'); [Environment]::SetEnvironmentVariable($name, $envValues[$name], 'Process') }
    Start-Process -FilePath (Join-Path $install 'NXBIN\ugraf.exe') -ArgumentList '-nx' -WorkingDirectory $test
    Write-Host "NX2512 test started. System logs: $test\temp"
}
finally { foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') } }
