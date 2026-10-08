#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$StagingRoot = 'C:\NX_Migration\NX2512_AfterInstall',
    [string]$NXInstallRoot = 'C:\Siemens\NX2512',
    [Parameter(Mandatory)][string]$OutputRoot,
    [string]$ReleaseManifestPath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force
foreach ($path in @($StagingRoot, $NXInstallRoot, $OutputRoot)) { Assert-NXOfflinePath -Path $path }
Assert-NXDisjointPaths -First $OutputRoot -Second $StagingRoot
Assert-NXDisjointPaths -First $OutputRoot -Second $NXInstallRoot
if (Test-Path -LiteralPath $OutputRoot) { throw 'OutputRoot must be new; existing test configurations are not overwritten.' }
Assert-NXTreeNoReparsePoint -Root $StagingRoot
$stage = (Get-Item -LiteralPath $StagingRoot).FullName.TrimEnd('\')
$install = (Get-Item -LiteralPath $NXInstallRoot).FullName.TrimEnd('\')
$exe = Join-Path $install 'NXBIN\ugraf.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'NX2512 executable not found.' }

$markerPath = Join-Path $stage '_NX2512_MIGRATION_STAGING.txt'
$files = @(Get-ChildItem -LiteralPath $stage -Recurse -File -Force | Where-Object { $_.FullName -ne $markerPath })
$manifest = @($files | ForEach-Object {
    [pscustomobject]@{ RelativePath = $_.FullName.Substring($stage.Length + 1); Size = $_.Length; Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
if (-not [string]::IsNullOrWhiteSpace($ReleaseManifestPath)) {
    Assert-NXOfflinePath -Path $ReleaseManifestPath
    $expected = Get-Content -LiteralPath $ReleaseManifestPath -Raw | ConvertFrom-Json
    if ($expected.SchemaVersion -ne 1 -or $expected.Files.Count -ne $manifest.Count) { throw 'Release manifest file count/schema mismatch.' }
    $byPath = @{}
    foreach ($entry in $manifest) { $byPath[$entry.RelativePath] = $entry }
    foreach ($entry in $expected.Files) {
        if (-not $byPath.ContainsKey($entry.RelativePath) -or $byPath[$entry.RelativePath].Size -ne $entry.Size -or $byPath[$entry.RelativePath].Sha256 -ne $entry.Sha256) {
            throw "Release manifest mismatch: $($entry.RelativePath)"
        }
    }
}
New-Item -ItemType Directory -Path $OutputRoot -ErrorAction Stop | Out-Null
$output = (Get-Item -LiteralPath $OutputRoot).FullName
$palette = Join-Path $output 'CAMTemplates\startup\ugs_manufacturing_templates_german.pax'
$systemPalette = Join-Path $install 'MACH\localization\german\ugs_manufacturing_templates_german.pax'
$customPalette = Join-Path $stage 'MACH\resource\usertools\templates\ugs_manufacturing_templates_german.pax'
& (Join-Path $PSScriptRoot 'New-NX2512TestPalette.ps1') -SystemPalette $systemPalette -CustomPalette $customPalette -OutputPalette $palette -StagingRoot $stage
$customDirs = Join-Path $output 'custom_dirs.dat'
$directoryLines = @((Join-Path $stage 'MACH\resource\usertools'), (Join-Path $stage 'MACH\resource\usertools\PostConfiguratorLibrarySourceTool'), (Join-Path $output 'CAMTemplates'))
[System.IO.File]::WriteAllLines($customDirs, $directoryLines, (New-Object System.Text.UTF8Encoding($false)))
$config = [ordered]@{
    SchemaVersion = 1; NXInstallRoot = $install; StagingRoot = $stage; TestRoot = $output
    Files = $manifest
    ConfigurationFiles = @(
        [pscustomobject]@{ Path = $customDirs; Sha256 = (Get-FileHash -LiteralPath $customDirs).Hash },
        [pscustomobject]@{ Path = $palette; Sha256 = (Get-FileHash -LiteralPath $palette).Hash }
    )
}
$configPath = Join-Path $output 'test_environment.json'
$config | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $configPath -Encoding UTF8
$starter = Join-Path $output 'Start-NX2512-Test.cmd'
@('@echo off', ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'Start-NX2512ManagedTest.ps1') + '" -ConfigPath "' + $configPath + '" %*'), 'pause') |
    Set-Content -LiteralPath $starter -Encoding ASCII
Write-Host "Reproducible test environment created: $starter"
Write-Host 'This is not a functional approval. Keep the machine UID errors blocked until resolved in NX.'
