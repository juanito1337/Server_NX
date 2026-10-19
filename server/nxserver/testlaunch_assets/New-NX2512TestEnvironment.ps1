#requires -Version 5.1
# Shared implementation: used by the project wrapper and server test packages.
[CmdletBinding()]
param(
    [string]$StagingRoot = 'C:\NX_Migration\NX2512_AfterInstall',
    [string]$NXInstallRoot = 'C:\Siemens\NX2512',
    [Parameter(Mandatory)][string]$OutputRoot,
    [string]$ReleaseManifestPath,
    [object]$UIProfile
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXOfflineSafety.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'NXUIProfile.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'NXTemplateXml.psm1') -Force
foreach ($path in @($StagingRoot, $NXInstallRoot, $OutputRoot)) { Assert-NXOfflinePath -Path $path }
Assert-NXDisjointPaths -First $OutputRoot -Second $StagingRoot
Assert-NXDisjointPaths -First $OutputRoot -Second $NXInstallRoot
if (Test-Path -LiteralPath $OutputRoot) { throw 'OutputRoot must be new; existing test configurations are not overwritten.' }
Write-Host '[INFO] Preparing a separate local test environment. Please keep this window open.'
Write-Host '[INFO] Checking the staging tree for junctions and symbolic links...'
Assert-NXTreeNoReparsePoint -Root $StagingRoot
$stage = (Get-Item -LiteralPath $StagingRoot).FullName.TrimEnd('\')
$install = (Get-Item -LiteralPath $NXInstallRoot).FullName.TrimEnd('\')
$exe = Join-Path $install 'NXBIN\ugraf.exe'
Assert-NXOfflinePath -Path $exe
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'NX2512 executable not found.' }
[void](Get-NXInstallationVersionMetadata -InstallationRoot $install)

$markerPath = Join-Path $stage '_NX2512_MIGRATION_STAGING.txt'
$expected = $null
$includeMarker = $false
if (-not [string]::IsNullOrWhiteSpace($ReleaseManifestPath)) {
    Assert-NXOfflinePath -Path $ReleaseManifestPath
    $expected = Get-Content -LiteralPath $ReleaseManifestPath -Raw | ConvertFrom-Json
    $includeMarker = $null -ne $expected.PSObject.Properties['IncludeStagingMarker'] -and $expected.IncludeStagingMarker -eq $true
}
$files = @(Get-ChildItem -LiteralPath $stage -Recurse -File -Force | Where-Object { $includeMarker -or $_.FullName -ne $markerPath })
$checkedFiles = 0
Write-Host "[INFO] Preparing SHA-256 manifest of $($files.Count) files. This can take several minutes."
$manifest = @($files | ForEach-Object {
    [pscustomobject]@{ RelativePath = $_.FullName.Substring($stage.Length + 1); Size = $_.Length; Sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    $checkedFiles++
    if ($checkedFiles % 250 -eq 0 -or $checkedFiles -eq $files.Count) { Write-Host "[INFO] Prepared $checkedFiles / $($files.Count) files." }
})
if ($null -ne $expected) {
    if ($expected.SchemaVersion -ne 1 -or $expected.Files.Count -ne $manifest.Count) { throw 'Release manifest file count/schema mismatch.' }
    $byPath = @{}
    foreach ($entry in $manifest) { $byPath[$entry.RelativePath] = $entry }
    foreach ($entry in $expected.Files) {
        if (-not $byPath.ContainsKey($entry.RelativePath) -or $byPath[$entry.RelativePath].Size -ne $entry.Size -or $byPath[$entry.RelativePath].Sha256 -ne $entry.Sha256) {
            throw "Release manifest mismatch: $($entry.RelativePath)"
        }
    }
}
[void](Get-NXUIRolePath -UIProfile $UIProfile -StagingRoot $stage -Files $manifest)
New-Item -ItemType Directory -Path $OutputRoot -ErrorAction Stop | Out-Null
$output = (Get-Item -LiteralPath $OutputRoot).FullName
$palette = Join-Path $output 'CAMTemplates\startup\ugs_manufacturing_templates_german.pax'
$systemPalette = Join-Path $install 'MACH\localization\german\ugs_manufacturing_templates_german.pax'
$customPalette = Join-Path $stage 'MACH\resource\usertools\templates\ugs_manufacturing_templates_german.pax'
if (Test-Path -LiteralPath $customPalette -PathType Leaf) {
    & (Join-Path $PSScriptRoot 'New-NX2512TestPalette.ps1') -SystemPalette $systemPalette -CustomPalette $customPalette -OutputPalette $palette -StagingRoot $stage
}
else {
    # A standard-only share has no customer machine palette. Do not invent one.
    Assert-NXOfflinePath -Path $systemPalette
    $document = Read-NXPaletteDocument -Path $systemPalette
    [void][System.IO.Directory]::CreateDirectory((Split-Path $palette -Parent))
    Copy-Item -LiteralPath $systemPalette -Destination $palette -ErrorAction Stop
    Write-Host '[INFO] No customer machine palette: using the unchanged Siemens palette.'
}
$customDirs = Join-Path $output 'custom_dirs.dat'
$directoryLines = @((Join-Path $stage 'MACH\resource\usertools'), (Join-Path $stage 'MACH\resource\usertools\PostConfiguratorLibrarySourceTool'), (Join-Path $output 'CAMTemplates'))
[System.IO.File]::WriteAllLines($customDirs, $directoryLines, (New-Object System.Text.UTF8Encoding($false)))
$config = [ordered]@{
    SchemaVersion = 1; NXInstallRoot = $install; StagingRoot = $stage; TestRoot = $output; IncludeStagingMarker = $includeMarker
    Files = $manifest
    ConfigurationFiles = @(
        [pscustomobject]@{ Path = $customDirs; Sha256 = (Get-FileHash -LiteralPath $customDirs).Hash },
        [pscustomobject]@{ Path = $palette; Sha256 = (Get-FileHash -LiteralPath $palette).Hash }
    )
}
$configPath = Join-Path $output 'test_environment.json'
if ($null -ne $UIProfile) { $config['UIProfile']=$UIProfile }
$config | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $configPath -Encoding UTF8
$starter = Join-Path $output 'Start-NX2512-Test.cmd'
@('@echo off', 'setlocal', 'echo NX2512-Test: Pruefung kann mehrere Minuten dauern. Fenster bitte offen lassen.',
    ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'Start-NX2512ManagedTest.ps1') + '" -ConfigPath "' + $configPath + '" %*'),
    'set "NX_TEST_EXIT=%ERRORLEVEL%"', 'if not "%NX_TEST_EXIT%"=="0" (echo NX-Test abgebrochen. Fehler oben pruefen.)',
    'pause', 'exit /b %NX_TEST_EXIT%') |
    Set-Content -LiteralPath $starter -Encoding ASCII
Write-Host "Reproducible test environment created: $starter"
Write-Host 'This is not a functional approval. Keep the machine UID errors blocked until resolved in NX.'
