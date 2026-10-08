#requires -Version 5.1
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$project = Split-Path $PSScriptRoot -Parent
Import-Module (Join-Path $project 'NXMigrationSafety.psm1') -Force
function Assert-True([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function Assert-Blocked([scriptblock]$Action) { $blocked = $false; try { & $Action } catch { $blocked = $true }; Assert-True $blocked 'Unsafe input was not rejected.' }
foreach ($path in @('W:\SiemensShare\nx2406', 'W:\other', '\\server\share\nx2512')) {
    Assert-Blocked { Assert-NXOfflinePath -Path $path }
}
Assert-NXOfflinePath -Path 'W:\SiemensShare\nx2512' -ReferenceOnly
Assert-Blocked { Assert-NXDisjointPaths -First 'C:\source' -Second 'C:\source\reports' }
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $project 'Invoke-NX2406ToNX2512Migration.ps1'), [ref]$tokens, [ref]$errors)
Assert-True ($errors.Count -eq 0) 'Main script contains syntax errors.'
foreach ($function in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if (@('Get-NXLogicalDatabaseLines', 'Read-NXToolDatabase', 'Test-NXToolRecordEqual', 'Convert-NXToolDatabase') -contains $function.Name) { Invoke-Expression $function.Extent.Text }
}
$root = Join-Path ([System.IO.Path]::GetTempPath()) ('NXMigrationRegression_' + [guid]::NewGuid().ToString('N'))
Assert-NXOfflinePath -Path $root
New-Item -ItemType Directory -Path $root | Out-Null
$encoding = New-Object System.Text.UTF8Encoding($true)
$sample = Join-Path $root 'continuation.dat'
[System.IO.File]::WriteAllText($sample, "#CLASS tool`nFORMAT LIBRF`n DESCR`nDATA |A1|Alpha`n Beta`nDATA |a1|lower`n#END_DATA`n", $encoding)
$db = Read-NXToolDatabase -Path $sample
Assert-True ($db.Errors.Count -eq 0 -and $db.Records[0].FieldValues.DESCR -ceq 'Alpha Beta') 'Continuation parsing failed.'
Assert-True ($db.RecordsByKey.Count -eq 2) 'Case-sensitive library references collided.'
$first = [pscustomobject]@{ FieldValues = @{ LIBRF = 'A'; DESCR = 'Alpha' } }
$second = [pscustomobject]@{ FieldValues = @{ LIBRF = 'A'; DESCR = 'alpha' } }
Assert-True (-not (Test-NXToolRecordEqual $first $second)) 'Case-only changes were ignored.'

# Minimal, real three-way build; no production data or NX program is opened.
foreach ($name in @('old', 'new', 'custom')) {
    $base = Join-Path $root $name
    foreach ($relative in @('UGII\udf', 'UGII\materials', 'NXPLOT\config\pm_server', 'MACH\custom', 'MACH\resource\library\tool\metric', 'CMM_INSPECTION\resource')) {
        New-Item -ItemType Directory -Path (Join-Path $base $relative) -Force | Out-Null
    }
    foreach ($relative in @('10.ugf', 'ugcolor.cdf', 'ugsmd_def.std', 'thd_english.dat', 'thd_metric.dat')) {
        [System.IO.File]::WriteAllText((Join-Path $base "UGII\$relative"), 'stock fixture', $encoding)
    }
}
foreach ($name in @('sidt_old', 'sidt_new')) {
    foreach ($relative in @('applications\nx\nxplot', 'applications\nx\ugii')) {
        New-Item -ItemType Directory -Path (Join-Path (Join-Path $root $name) $relative) -Force | Out-Null
    }
}
$databaseRelative = 'MACH\resource\library\tool\metric\tool_database.dat'
$old = "#CLASS tool`nFORMAT LIBRF DESCR`nDATA |STD|Standard`n#END_DATA`n"
$custom = "#CLASS tool`nFORMAT LIBRF DESCR`nDATA |STD|Standard`nDATA |CUSTOM|Customer`n#END_DATA`n"
$new = "#CLASS tool`nFORMAT LIBRF DESCR EXTRA`nDATA |STD|Standard|0`n#END_DATA`n"
[System.IO.File]::WriteAllText((Join-Path (Join-Path $root 'old') $databaseRelative), $old, $encoding)
[System.IO.File]::WriteAllText((Join-Path (Join-Path $root 'custom') $databaseRelative), $custom, $encoding)
[System.IO.File]::WriteAllText((Join-Path (Join-Path $root 'custom') 'MACH\resource\dmg_templates.pax'), '<Palette></Palette><PaletteEntry/>', $encoding)
[System.IO.File]::WriteAllText((Join-Path (Join-Path $root 'new') $databaseRelative), $new, $encoding)
$migrationArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $project 'Invoke-NX2406ToNX2512Migration.ps1'),
    '-Mode', 'Build', '-Apply', '-CustomizedNX2406Path', (Join-Path $root 'custom'), '-StandardNX2406Path', (Join-Path $root 'old'),
    '-StandardNX2512Path', (Join-Path $root 'new'), '-SIDTNX2406Path', (Join-Path $root 'sidt_old'), '-SIDTNX2512Path', (Join-Path $root 'sidt_new'),
    '-StagingPath', (Join-Path $root 'stage'), '-ReportPath', (Join-Path $root 'reports'))
$migrationExit = Invoke-NXExternalProcess -FilePath 'powershell.exe' -Arguments $migrationArguments
Assert-True ($migrationExit -eq 0) 'Three-way fixture build failed.'
$summary = Get-Content -Raw (Join-Path $root 'reports\migration_summary.json') | ConvertFrom-Json
Assert-True $summary.BuildCompleted 'Build did not record successful completion.'
Assert-True ($summary.ReleaseStatus -eq 'PendingNXFunctionalValidation') 'Build falsely claims functional approval.'
$result = Read-NXToolDatabase -Path (Join-Path (Join-Path $root 'stage') $databaseRelative)
Assert-True ($result.Records.Count -eq 2 -and $result.Errors.Count -eq 0) 'Converted fixture database invalid.'
Assert-True (Test-Path (Join-Path $root 'reports\release_manifest.json')) 'Release SHA-256 manifest missing.'
Assert-True (-not (Test-Path (Join-Path $root 'stage\MACH\resource\dmg_templates.pax'))) 'Optional legacy palette was copied.'
Assert-True (Test-Path (Join-Path $root 'custom\MACH\resource\dmg_templates.pax')) 'Original optional palette was removed.'
$overlapArguments = @($migrationArguments)
$overlapArguments[[array]::IndexOf($overlapArguments, '-ReportPath') + 1] = Join-Path $root 'custom'
Assert-True ((Invoke-NXExternalProcess -FilePath 'powershell.exe' -Arguments $overlapArguments) -ne 0) 'Report/source overlap was accepted.'
foreach ($parameter in @('-LogPath', '-BackupPath')) {
    $mergeArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $project 'Merge-NXNetworkFolders.ps1'),
        '-SourcePath', (Join-Path $root 'old'), '-DestinationPath', (Join-Path $root 'new'), $parameter, 'W:\SiemensShare\nx2406\must_not_write.log')
    Assert-True ((Invoke-NXExternalProcess -FilePath 'powershell.exe' -Arguments $mergeArguments) -ne 0) "Unsafe $parameter accepted."
}
Write-Host "[OK] Migration regression tests passed. Isolated fixtures retained: $root"
