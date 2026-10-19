#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackagePath,
    [string]$StagingRoot,
    [string]$NXInstallRoot,
    [string]$TestRoot,
    [switch]$CheckOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXOfflineSafety.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'NXUIProfile.psm1') -Force
Assert-NXOfflinePath -Path $PackagePath
Assert-NXOfflinePath -Path $PSScriptRoot
$package = Get-Content -LiteralPath $PackagePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($package.SchemaVersion -ne 1 -or $package.NxVersion -ne '2512') { throw 'Unsupported NX test package.' }
$releaseId = [guid]::Parse($package.ReleaseId).ToString()
foreach ($entry in $package.ScriptHashes.PSObject.Properties) {
    if ($entry.Name -notmatch '^[A-Za-z0-9_-]+\.(ps1|psm1|cmd)$') { throw 'Unsafe package script name.' }
    $file = Join-Path $PSScriptRoot $entry.Name
    Assert-NXOfflinePath -Path $file
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.Value) { throw "Package script changed: $($entry.Name)" }
}
$manifestPath = Join-Path $PSScriptRoot 'release_manifest.json'
Assert-NXOfflinePath -Path $manifestPath
if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash -ne $package.ManifestSha256) { throw 'Package release manifest changed.' }
$uiProfile=$null
if ($null -ne $package.PSObject.Properties['UIProfile']) { $uiProfile=$package.UIProfile }
if ([string]::IsNullOrWhiteSpace($StagingRoot)) { $StagingRoot = [string]$package.StagingRoot }
if ([string]::IsNullOrWhiteSpace($StagingRoot)) { $StagingRoot = Read-Host 'Lokaler Windows-Pfad des importierten NX-Ordners' }
if ([string]::IsNullOrWhiteSpace($NXInstallRoot)) { $NXInstallRoot = [string]$package.NXInstallRoot }
if ([string]::IsNullOrWhiteSpace($TestRoot)) { $TestRoot = [string]$package.TestRoot }
foreach ($path in @($StagingRoot, $NXInstallRoot, $TestRoot)) { Assert-NXOfflinePath -Path $path }
$expectedFiles=(Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8|ConvertFrom-Json).Files
[void](Get-NXUIRolePath -UIProfile $uiProfile -StagingRoot $StagingRoot -Files @($expectedFiles))
Assert-NXDisjointPaths -First $TestRoot -Second $StagingRoot
Assert-NXDisjointPaths -First $TestRoot -Second $NXInstallRoot
Assert-NXDisjointPaths -First $TestRoot -Second $PSScriptRoot
Assert-NXDisjointPaths -First $PSScriptRoot -Second $StagingRoot
$versionName = (Split-Path $NXInstallRoot.TrimEnd('\', '/') -Leaf)
if ($versionName -ine 'NX2512') { throw 'Test installation must be an explicit NX2512 folder.' }
Assert-NXOfflinePath -Path (Join-Path $NXInstallRoot 'NXBIN\ugraf.exe')
if (-not (Test-Path -LiteralPath (Join-Path $NXInstallRoot 'NXBIN\ugraf.exe') -PathType Leaf)) { throw 'NX2512 is not installed at the configured local path.' }
[void](Get-NXInstallationVersionMetadata -InstallationRoot $NXInstallRoot)
$configPath = Join-Path $TestRoot 'test_environment.json'
$bindingPath = Join-Path $TestRoot 'server_release_binding.json'
$binding = [ordered]@{
    ReleaseId = $releaseId; ManifestSha256 = $package.ManifestSha256
    StagingRoot = [System.IO.Path]::GetFullPath($StagingRoot).TrimEnd('\')
    NXInstallRoot = [System.IO.Path]::GetFullPath($NXInstallRoot).TrimEnd('\')
    TestRoot = [System.IO.Path]::GetFullPath($TestRoot).TrimEnd('\')
}
if ($null -ne $uiProfile) { $binding['UIProfileJson']=$uiProfile|ConvertTo-Json -Depth 5 -Compress }
if (Test-Path -LiteralPath $TestRoot) {
    Assert-NXTreeNoReparsePoint -Root $TestRoot
    if (-not (Test-Path -LiteralPath $bindingPath -PathType Leaf) -or -not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Existing test directory is not bound to this server release; use a new directory.' }
    $stored = Get-Content -LiteralPath $bindingPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $requestedUI=''
    if ($null -ne $uiProfile) { $requestedUI=$binding.UIProfileJson }
    $storedUI=''
    if ($null -ne $stored.PSObject.Properties['UIProfileJson']) { $storedUI=[string]$stored.UIProfileJson }
    if ($storedUI -cne $requestedUI) { throw 'Existing test directory belongs to another UI-role selection. Use a new directory.' }
    foreach ($key in $binding.Keys) { if ($stored.$key -cne $binding[$key]) { throw 'Existing test directory belongs to another release/path. Nothing was overwritten.' } }
    if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash -ne $stored.ConfigSha256) { throw 'Test configuration changed outside the package.' }
}
else {
    & (Join-Path $PSScriptRoot 'New-NX2512TestEnvironment.ps1') -StagingRoot $StagingRoot -NXInstallRoot $NXInstallRoot -OutputRoot $TestRoot -ReleaseManifestPath $manifestPath -UIProfile $uiProfile
    $binding['ConfigSha256'] = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
    $binding | ConvertTo-Json | Set-Content -LiteralPath $bindingPath -Encoding UTF8
}
Write-Host "[INFO] Server release: $releaseId"
Write-Host "[INFO] Tested NX folder: $StagingRoot"
& (Join-Path $PSScriptRoot 'Start-NX2512ManagedTest.ps1') -ConfigPath $configPath -CheckOnly:$CheckOnly
