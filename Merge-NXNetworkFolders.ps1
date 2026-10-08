#requires -Version 5.1

<#
.SYNOPSIS
Merges an NX source tree into an existing NX network folder.

.DESCRIPTION
The script uses Robocopy to merge all subdirectories. Files that exist only in
the destination are never deleted. The default mode overwrites differing files
with the source version, but first backs up every file that will be replaced.

Without -Apply, the script only creates a preview and does not copy or replace
NX files.

The production folder W:\SiemensShare\nx2406 and all of its child paths are
explicitly blocked as both source and destination.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SourcePath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$DestinationPath,

    [ValidateSet('Overwrite', 'KeepExisting', 'SourceNewer')]
    [string]$ConflictAction = 'Overwrite',

    [string]$BackupPath,

    [string]$LogPath,

    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force

function Get-ResolvedDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) {
        throw "$Label is not a directory: $LiteralPath"
    }

    return $item.FullName
}

function Get-ComparisonPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return $Path.TrimEnd([char[]]'\/').ToUpperInvariant()
}

function Assert-NotProtectedProductionPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $protectedPath = 'W:\SiemensShare\nx2406'
    try {
        $candidatePath = [System.IO.Path]::GetFullPath($Path)
    }
    catch {
        $candidatePath = $Path
    }

    $candidateCompare = Get-ComparisonPath -Path $candidatePath
    $protectedCompare = Get-ComparisonPath -Path $protectedPath

    if ($candidateCompare -eq $protectedCompare -or
        $candidateCompare.StartsWith("$protectedCompare\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label is inside the protected production folder and must not be accessed by this script: $protectedPath"
    }
}

function Get-PathPrefix {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.EndsWith('\')) {
        return $Path
    }

    return "$Path\"
}

function Get-AbsoluteOutputPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path -Path (Get-Location).Path -ChildPath $Path))
}

function New-ParentDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $parent = Split-Path -Parent $Path
    if ([string]::IsNullOrWhiteSpace($parent)) {
        return
    }

    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function ConvertTo-NativeArgument {
    param([Parameter(Mandatory = $true)][string]$Argument)

    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    return '"' + $Argument.Replace('"', '\"') + '"'
}

try {
    Assert-NXOfflinePath -Path $SourcePath -Label SourcePath
    Assert-NXOfflinePath -Path $DestinationPath -Label DestinationPath
    Assert-NotProtectedProductionPath -Path $SourcePath -Label 'SourcePath'
    Assert-NotProtectedProductionPath -Path $DestinationPath -Label 'DestinationPath'

    $sourceRoot = Get-ResolvedDirectory -LiteralPath $SourcePath -Label 'SourcePath'
    $destinationRoot = Get-ResolvedDirectory -LiteralPath $DestinationPath -Label 'DestinationPath'

    $sourceCompare = Get-ComparisonPath -Path $sourceRoot
    $destinationCompare = Get-ComparisonPath -Path $destinationRoot

    if ($sourceCompare -eq $destinationCompare) {
        throw 'SourcePath and DestinationPath resolve to the same directory.'
    }

    if ($destinationCompare.StartsWith("$sourceCompare\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $sourceCompare.StartsWith("$destinationCompare\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'SourcePath and DestinationPath must not be nested inside each other.'
    }

    $timeStamp = Get-Date -Format 'yyyyMMdd_HHmmss'

    if ([string]::IsNullOrWhiteSpace($LogPath)) {
        $LogPath = Join-Path -Path $env:TEMP -ChildPath "NX-Migration\NX-Merge-$timeStamp.log"
    }
    $LogPath = Get-AbsoluteOutputPath -Path $LogPath

    if ([string]::IsNullOrWhiteSpace($BackupPath)) {
        $BackupPath = Join-Path -Path $destinationRoot -ChildPath "_NX_Migration_Backup\$timeStamp"
    }
    $BackupPath = Get-AbsoluteOutputPath -Path $BackupPath
    Assert-NXOfflinePath -Path $LogPath -Label LogPath
    Assert-NXOfflinePath -Path $BackupPath -Label BackupPath
    Assert-NXDisjointPaths -First $LogPath -Second $sourceRoot
    Assert-NXTreeNoReparsePoint -Root $sourceRoot
    Assert-NXTreeNoReparsePoint -Root $destinationRoot

    $backupCompare = Get-ComparisonPath -Path $BackupPath
    if ($backupCompare -eq $sourceCompare -or
        $backupCompare.StartsWith("$sourceCompare\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $sourceCompare.StartsWith("$backupCompare\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'BackupPath must not be the source directory, a child of it, or a parent of it.'
    }

    $sourcePrefix = Get-PathPrefix -Path $sourceRoot
    $sourceDirectories = @(Get-ChildItem -LiteralPath $sourceRoot -Directory -Recurse -Force)
    $sourceFiles = @(Get-ChildItem -LiteralPath $sourceRoot -File -Recurse -Force)

    $structureConflicts = New-Object 'System.Collections.Generic.List[string]'
    foreach ($sourceDirectory in $sourceDirectories) {
        $relativePath = $sourceDirectory.FullName.Substring($sourcePrefix.Length)
        $targetPath = Join-Path -Path $destinationRoot -ChildPath $relativePath
        if ([System.IO.File]::Exists($targetPath)) {
            $structureConflicts.Add("Source directory collides with destination file: $relativePath")
        }
    }

    $differentFiles = New-Object 'System.Collections.Generic.List[object]'
    $replacementFiles = New-Object 'System.Collections.Generic.List[object]'
    [long]$newFileCount = 0
    [long]$identicalFileCount = 0
    [long]$keptFileCount = 0

    for ($index = 0; $index -lt $sourceFiles.Count; $index++) {
        if (($index % 250) -eq 0) {
            $percent = if ($sourceFiles.Count -eq 0) { 100 } else { [int](100 * $index / $sourceFiles.Count) }
            Write-Progress -Activity 'NX merge preflight' -Status "$index of $($sourceFiles.Count) files" -PercentComplete $percent
        }

        $sourceFile = $sourceFiles[$index]
        $relativePath = $sourceFile.FullName.Substring($sourcePrefix.Length)
        $targetPath = Join-Path -Path $destinationRoot -ChildPath $relativePath

        if ([System.IO.Directory]::Exists($targetPath)) {
            $structureConflicts.Add("Source file collides with destination directory: $relativePath")
            continue
        }

        if (-not [System.IO.File]::Exists($targetPath)) {
            $newFileCount++
            continue
        }

        $targetFile = Get-Item -LiteralPath $targetPath -Force
        $timeDifferenceSeconds = ($sourceFile.LastWriteTimeUtc - $targetFile.LastWriteTimeUtc).TotalSeconds
        $sameForRobocopy = ($sourceFile.Length -eq $targetFile.Length) -and
            ([Math]::Abs($timeDifferenceSeconds) -le 2)

        if ($sameForRobocopy) {
            $identicalFileCount++
            continue
        }

        $action = switch ($ConflictAction) {
            'KeepExisting' {
                $keptFileCount++
                'KeepExisting'
            }
            'SourceNewer' {
                if ($timeDifferenceSeconds -ge -2) {
                    'BackupAndCopy'
                }
                else {
                    $keptFileCount++
                    'KeepNewerDestination'
                }
            }
            default {
                'BackupAndOverwrite'
            }
        }

        $record = [pscustomobject]@{
            RelativePath              = $relativePath
            Action                    = $action
            ExistingSize              = $targetFile.Length
            ExistingLastWriteTimeUtc  = $targetFile.LastWriteTimeUtc.ToString('o')
            SourceSize                = $sourceFile.Length
            SourceLastWriteTimeUtc    = $sourceFile.LastWriteTimeUtc.ToString('o')
            ExistingFullName          = $targetFile.FullName
        }

        $differentFiles.Add($record)
        if ($action -eq 'BackupAndCopy' -or $action -eq 'BackupAndOverwrite') {
            $replacementFiles.Add($record)
        }
    }
    Write-Progress -Activity 'NX merge preflight' -Completed

    if ($structureConflicts.Count -gt 0) {
        $sample = ($structureConflicts | Select-Object -First 20) -join [Environment]::NewLine
        throw "The merge was stopped because of $($structureConflicts.Count) structural conflict(s):`n$sample"
    }

    Write-Host ''
    Write-Host 'NX folder merge'
    Write-Host "  Mode:              $(if ($Apply) { 'APPLY' } else { 'PREVIEW ONLY' })"
    Write-Host "  Source:            $sourceRoot"
    Write-Host "  Destination:       $destinationRoot"
    Write-Host "  Conflict action:   $ConflictAction"
    Write-Host "  Source files:      $($sourceFiles.Count)"
    Write-Host "  New files:         $newFileCount"
    Write-Host "  Different files:   $($differentFiles.Count)"
    Write-Host "  Files to replace:  $($replacementFiles.Count)"
    Write-Host "  Existing files kept: $keptFileCount"
    Write-Host "  Matching files:    $identicalFileCount"

    New-ParentDirectory -Path $LogPath
    $manifestPath = [System.IO.Path]::ChangeExtension($LogPath, '.conflicts.csv')

    if ($differentFiles.Count -gt 0) {
        $differentFiles |
            Select-Object RelativePath, Action, ExistingSize, ExistingLastWriteTimeUtc, SourceSize, SourceLastWriteTimeUtc |
            Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding UTF8
        Write-Host "  Conflict manifest: $manifestPath"
    }

    if ($Apply -and $replacementFiles.Count -gt 0) {
        if (Test-Path -LiteralPath $BackupPath) {
            throw "BackupPath already exists. Choose an empty path: $BackupPath"
        }

        New-Item -ItemType Directory -Path $BackupPath -Force | Out-Null
        for ($index = 0; $index -lt $replacementFiles.Count; $index++) {
            $record = $replacementFiles[$index]
            $backupFile = Join-Path -Path $BackupPath -ChildPath $record.RelativePath
            New-ParentDirectory -Path $backupFile
            Copy-Item -LiteralPath $record.ExistingFullName -Destination $backupFile -Force

            if (($index % 50) -eq 0) {
                $percent = [int](100 * ($index + 1) / $replacementFiles.Count)
                Write-Progress -Activity 'Backing up files that will be replaced' -Status "$($index + 1) of $($replacementFiles.Count) files" -PercentComplete $percent
            }
        }
        Write-Progress -Activity 'Backing up files that will be replaced' -Completed

        $backupManifest = Join-Path -Path $BackupPath -ChildPath '_NX_conflicts.csv'
        $differentFiles |
            Select-Object RelativePath, Action, ExistingSize, ExistingLastWriteTimeUtc, SourceSize, SourceLastWriteTimeUtc |
            Export-Csv -LiteralPath $backupManifest -NoTypeInformation -Encoding UTF8
        Write-Host "  Backup:            $BackupPath"
    }

    $robocopyArguments = @(
        $sourceRoot,
        $destinationRoot,
        '*',
        '/E',
        '/COPY:DAT',
        '/DCOPY:DAT',
        '/R:2',
        '/W:2',
        '/FFT',
        '/XJ',
        '/XX',
        '/NP',
        '/TEE',
        '/NFL',
        '/NDL',
        "/UNILOG:$LogPath"
    )

    switch ($ConflictAction) {
        'KeepExisting' {
            $robocopyArguments += @('/XC', '/XN', '/XO')
        }
        'SourceNewer' {
            $robocopyArguments += '/XO'
        }
    }

    if (-not $Apply) {
        $robocopyArguments += '/L'
        Write-Host ''
        Write-Host 'Preview: no NX file will be changed. Add -Apply after checking the log.'
    }
    else {
        Write-Host ''
        Write-Host 'Starting merge. Destination-only files will be retained.'
    }

    $robocopyArgumentLine = ($robocopyArguments | ForEach-Object {
        ConvertTo-NativeArgument -Argument $_
    }) -join ' '

    $robocopyExecutable = Join-Path -Path $env:SystemRoot -ChildPath 'System32\robocopy.exe'
    $robocopyProcess = Start-Process -FilePath $robocopyExecutable `
        -ArgumentList $robocopyArgumentLine `
        -NoNewWindow `
        -Wait `
        -PassThru
    $robocopyExitCode = $robocopyProcess.ExitCode

    Write-Host ''
    Write-Host "Robocopy log: $LogPath"
    if ($robocopyExitCode -ge 8) {
        throw "Robocopy failed with exit code $robocopyExitCode. The destination may be partially updated; use the log and backup for recovery."
    }

    if ($Apply) {
        Write-Host "Merge completed successfully (Robocopy exit code $robocopyExitCode)."
    }
    else {
        Write-Host "Preview completed successfully (Robocopy exit code $robocopyExitCode)."
    }

    exit 0
}
catch {
    Write-Error $_
    exit 1
}
