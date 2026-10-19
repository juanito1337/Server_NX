# Shared offline safety module, exported through the project compatibility module.
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
    $ancestors = New-Object 'System.Collections.Generic.List[string]'
    while (-not [string]::IsNullOrEmpty($current)) {
        $ancestors.Add($current)
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
    # Inspect ancestors first; never follow a child lookup through a junction.
    for ($index = $ancestors.Count - 1; $index -ge 0; $index--) {
        $current = $ancestors[$index]
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label contains a junction or symbolic link: $current"
            }
        }
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

function Get-NXInstallationVersionMetadata {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$InstallationRoot)
    Assert-NXOfflinePath -Path $InstallationRoot
    $exe = Join-Path $InstallationRoot 'NXBIN\ugraf.exe'
    Assert-NXOfflinePath -Path $exe
    $stream = $null
    $hash = $null
    try {
        $before = Get-Item -LiteralPath $exe -Force -ErrorAction Stop
        if ($before.PSIsContainer -or $before.Length -lt 1 -or $before.Length -gt 16MB) {
            throw 'Expected an ordinary executable metadata file of at most 16 MiB.'
        }
        # Deny concurrent writers/deletion while Windows reads the version
        # resource. This is metadata inspection, never Assembly.Load or NX.
        $stream = [System.IO.File]::Open($exe, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        if ($stream.Length -ne $before.Length) { throw 'Installation changed before the metadata read.' }
        $hash = [System.Security.Cryptography.SHA256]::Create()
        $sha = [System.BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
        Assert-NXOfflinePath -Path $exe
        $info = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($exe)
        $fileParts = @($info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart, $info.FilePrivatePart)
        $productParts = @($info.ProductMajorPart, $info.ProductMinorPart, $info.ProductBuildPart, $info.ProductPrivatePart)
        $strings = @{
            CompanyName = [string]$info.CompanyName; ProductName = [string]$info.ProductName
            OriginalFilename = [string]$info.OriginalFilename
            FileVersion = [string]$info.FileVersion; ProductVersion = [string]$info.ProductVersion
        }
        foreach ($value in $strings.Values) {
            if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 512 -or $value -match '[\x00-\x1f]') {
                throw 'Missing or malformed version-resource strings.'
            }
        }
        if ($fileParts[0] -ne 2512 -or $productParts[0] -ne 2512 -or
            $strings.CompanyName -notmatch '(?i)siemens' -or
            $strings.ProductName -cnotin @('NX', 'Designcenter NX') -or
            $strings.OriginalFilename -notmatch '^(?i)ugraf(\.exe)?$') {
            throw 'Expected NX2512 product/file metadata, not a matching directory name.'
        }
        foreach ($name in @('FileVersion', 'ProductVersion')) {
            if ($strings[$name] -notmatch '^[0-9]+(\.[0-9]+){1,3}$') { throw 'Unsupported numeric version string.' }
            $parts = @($strings[$name].Split('.') | ForEach-Object { [int]$_ })
            while ($parts.Count -lt 4) { $parts += 0 }
            $fixed = $fileParts
            if ($name -eq 'ProductVersion') { $fixed = $productParts }
            if (($parts -join '.') -cne ($fixed -join '.')) { throw 'Textual and fixed version metadata disagree.' }
        }
        Assert-NXOfflinePath -Path $exe
        $after = Get-Item -LiteralPath $exe -Force -ErrorAction Stop
        if ($after.Length -ne $before.Length -or $after.LastWriteTimeUtc.Ticks -ne $before.LastWriteTimeUtc.Ticks -or
            $after.CreationTimeUtc.Ticks -ne $before.CreationTimeUtc.Ticks -or $stream.Length -ne $before.Length) {
            throw 'Installation changed during the metadata read.'
        }
        return [pscustomobject]@{
            ContractVersion = 1; Scope = 'nx_client_installation_version_metadata'
            ExpectedVersion = '2512'; DetectedVersion = [string]$productParts[0]
            ExePath = [System.IO.Path]::GetFullPath($exe); Size = [long]$before.Length; Sha256 = $sha
            FileVersion = $strings.FileVersion; ProductVersion = $strings.ProductVersion
            ProductName = $strings.ProductName; FileVersionParts = $fileParts; ProductVersionParts = $productParts
            MetadataOnly = $true; NXExecutionVerified = $false; LicenseVerified = $false; SignatureVerified = $false
        }
    }
    catch { throw ('NX2512 version metadata could not be verified: ' + $_.Exception.Message) }
    finally {
        if ($null -ne $hash) { $hash.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Get-NXOfflineSearchPath {
    [CmdletBinding()]
    param([AllowEmptyCollection()][string[]]$PreferredDirectories = @(), [AllowEmptyString()][string]$InheritedPath = '')
    $paths = New-Object 'System.Collections.Generic.List[string]'
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $addDirectory = {
        param([string]$Candidate)
        # Validate before any candidate-directory lookup, including aliases.
        Assert-NXOfflinePath -Path $Candidate
        if (-not (Test-Path -LiteralPath $Candidate -PathType Container)) { return }
        $directory = [System.IO.Path]::GetFullPath($Candidate)
        if ($directory.Length -gt 3) { $directory = $directory.TrimEnd('\') }
        if ($seen.Add($directory)) { [void]$paths.Add($directory) }
    }
    foreach ($candidate in $PreferredDirectories) {
        # A required NX directory must not silently hide an unsafe installation.
        if ($candidate -notmatch '^[A-VX-Z]:[\\/]' -or $candidate.Contains(';') -or $candidate.Contains('"')) {
            throw 'NX search directories must be single absolute local paths without PATH separators or quotes, never W: or UNC.'
        }
        & $addDirectory $candidate
    }
    foreach ($entry in @($InheritedPath -split ';')) {
        if ([string]::IsNullOrWhiteSpace($entry)) { continue }
        $candidate = $entry.Trim()
        if ($candidate.Length -ge 2 -and $candidate.StartsWith('"') -and $candidate.EndsWith('"')) {
            $candidate = $candidate.Substring(1, $candidate.Length - 2)
        }
        # Empty/relative/drive-relative/device/UNC/W: values are data only.
        # Do not expand inherited environment-variable references into paths.
        if ($candidate -notmatch '^[A-VX-Z]:[\\/]' -or $candidate.Contains('%') -or $candidate.Contains('"')) { continue }
        try { & $addDirectory $candidate }
        catch { continue }  # Optional host PATH entries may be unavailable/unsafe.
    }
    if ($paths.Count -eq 0) { throw 'No verified local NX process search directory is available.' }
    return ($paths -join ';')
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

Export-ModuleMember -Function Assert-NXOfflinePath, Assert-NXDisjointPaths, Assert-NXTreeNoReparsePoint, Invoke-NXExternalProcess, Get-NXOfflineSearchPath, Get-NXInstallationVersionMetadata
