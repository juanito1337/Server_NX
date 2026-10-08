Set-StrictMode -Version Latest

function Assert-NXOfflinePath {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path, [string]$Label = 'Path', [switch]$ReferenceOnly)

    $full = [System.IO.Path]::GetFullPath($Path).Replace('/', '\').TrimEnd('\')
    if ($full -match '(?i)(^W:\\SIEMENSSHARE\\NX2406($|\\)|\\SIEMENSSHARE\\NX2406($|\\))') {
        throw "$Label points to the protected production directory."
    }
    # A deployment path stored in text is not a filesystem operation.
    if ($ReferenceOnly) { return }
    if ($full -match '^(?i)(W:|\\\\)' -or $full -notmatch '^[A-Za-z]:\\') {
        throw "$Label must be a local offline path, not W: or a UNC share."
    }
    $drive = New-Object System.IO.DriveInfo($full.Substring(0, 3))
    if ($drive.DriveType -eq [System.IO.DriveType]::Network) {
        throw "$Label must not use a mapped network drive (including aliases of production)."
    }
    $current = $full
    while (-not [string]::IsNullOrEmpty($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label contains a junction or symbolic link: $current"
            }
        }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-NXDisjointPaths {
    param([Parameter(Mandatory)][string]$First, [Parameter(Mandatory)][string]$Second)
    $a = [System.IO.Path]::GetFullPath($First).TrimEnd('\', '/')
    $b = [System.IO.Path]::GetFullPath($Second).TrimEnd('\', '/')
    if ($a.Equals($b, [System.StringComparison]::OrdinalIgnoreCase) -or
        $a.StartsWith($b + '\', [System.StringComparison]::OrdinalIgnoreCase) -or
        $b.StartsWith($a + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Paths must not overlap: '$First' and '$Second'."
    }
}

function Assert-NXTreeNoReparsePoint {
    param([Parameter(Mandatory)][string]$Root)
    Assert-NXOfflinePath -Path $Root
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($Root)
    while ($pending.Count -gt 0) {
        foreach ($item in @(Get-ChildItem -LiteralPath $pending.Pop() -Force -ErrorAction Stop)) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Source trees must not contain junctions or symbolic links: $($item.FullName)"
            }
            if ($item.PSIsContainer) { $pending.Push($item.FullName) }
        }
    }
}

function Invoke-NXExternalProcess {
    param([Parameter(Mandatory)][string]$FilePath, [string[]]$Arguments = @(), [string]$WorkingDirectory = (Get-Location).Path)
    $executable = (Get-Command $FilePath -CommandType Application -ErrorAction Stop).Source
    Assert-NXOfflinePath -Path $executable
    Assert-NXOfflinePath -Path $WorkingDirectory
    $quoted = foreach ($argument in $Arguments) {
        $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
        $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
        '"' + $escaped + '"'
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $executable
    $process.StartInfo.Arguments = $quoted -join ' '
    $process.StartInfo.WorkingDirectory = $WorkingDirectory
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        if ($stdout.Result) { Write-Host $stdout.Result }
        if ($stderr.Result) { Write-Host $stderr.Result }
        return $process.ExitCode
    }
    finally { $process.Dispose() }
}

Export-ModuleMember -Function Assert-NXOfflinePath, Assert-NXDisjointPaths, Assert-NXTreeNoReparsePoint, Invoke-NXExternalProcess
