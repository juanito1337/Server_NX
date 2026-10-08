#requires -Version 5.1
[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status', 'Config')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force

$composePrefix = @('compose', '-p', 'nx-management', '--project-directory', $PSScriptRoot,
    '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '-f', (Join-Path $PSScriptRoot 'compose.windows.yaml'))

function Get-DockerOutput {
    param([string[]]$Arguments)
    $executable = (Get-Command docker.exe -CommandType Application -ErrorAction Stop).Source
    Assert-NXOfflinePath -Path $executable
    $quoted = foreach ($argument in $Arguments) {
        $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
        $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
        '"' + $escaped + '"'
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $executable
    $process.StartInfo.Arguments = $quoted -join ' '
    $process.StartInfo.WorkingDirectory = $PSScriptRoot
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Docker-Abfrage fehlgeschlagen: $($stderr.Result)" }
        return $stdout.Result.Trim()
    }
    finally { $process.Dispose() }
}

function Invoke-Compose {
    param([string[]]$Arguments)
    $composeArguments = $composePrefix + $Arguments
    $code = Invoke-NXExternalProcess -FilePath 'docker.exe' -Arguments $composeArguments -WorkingDirectory $PSScriptRoot
    if ($code -ne 0) { throw "Docker Compose ist fehlgeschlagen (Exit $code)." }
}

try {
    if (-not (Get-Command docker.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Docker Desktop mit Linux-Containern und Docker Compose wird benoetigt. Docker ist hier nicht installiert/verfuegbar; es wird nichts automatisch installiert.'
    }
    Assert-NXOfflinePath -Path $PSScriptRoot -Label 'Projektordner'
    if ($env:DOCKER_HOST -and $env:DOCKER_HOST -notmatch '^npipe:////\./pipe/(docker_engine|dockerDesktopLinuxEngine)$') {
        throw 'DOCKER_HOST darf fuer diesen Starter nur auf eine lokale Docker-Desktop Pipe zeigen.'
    }
    $contextName = Get-DockerOutput -Arguments @('context', 'show')
    $endpoint = Get-DockerOutput -Arguments @('context', 'inspect', $contextName, '--format', '{{.Endpoints.docker.Host}}')
    if ($endpoint -notmatch '^npipe:////\./pipe/(docker_engine|dockerDesktopLinuxEngine)$') {
        throw 'Dieser Windows-Starter akzeptiert nur eine lokale Docker-Desktop Pipe, keinen Remote-Kontext.'
    }
    if ($Action -eq 'Stop') { Invoke-Compose -Arguments @('down'); exit 0 }
    if ($Action -eq 'Status') { Invoke-Compose -Arguments @('ps'); exit 0 }
    if ($Action -eq 'Config') { Invoke-Compose -Arguments @('config', '--quiet'); exit 0 }

    # Validate fixed local paths BEFORE Docker could mount them. No W:/UNC,
    # network-drive alias or junction is accepted by this starter.
    $sourcePaths = @('C:\NX_Migration\NX2512_Managed_20261006', 'C:\Siemens\NX2406',
                     'C:\Siemens\NX2512', 'C:\NX_Migration\TestRuntime\temp')
    $releasePath = 'C:\NX_Migration\DockerReleases'
    Assert-NXOfflinePath -Path $PSScriptRoot -Label 'Projektordner'
    Assert-NXOfflinePath -Path $releasePath -Label 'Docker-Testausgabe'
    Assert-NXDisjointPaths -First $PSScriptRoot -Second $releasePath
    foreach ($sourcePath in $sourcePaths) {
        Assert-NXOfflinePath -Path $sourcePath -Label 'Docker-Lesequelle'
        Assert-NXDisjointPaths -First $releasePath -Second $sourcePath
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) { throw "Lokale Lesequelle fehlt: $sourcePath" }
    }
    # Require a local Linux daemon. A remote context would use the remote
    # server's paths, not this PC's C: folders.
    $daemonText = Get-DockerOutput -Arguments @('info', '--format', '{{.OSType}}')
    if ($daemonText -ne 'linux') {
        throw 'Docker Desktop muss laufen und auf Linux-Container eingestellt sein.'
    }
    # Validate the resolved Compose configuration, not only our default strings.
    # Edited YAML must not bypass production protection or mount extra targets.
    $resolved = Get-DockerOutput -Arguments ($composePrefix + @('config', '--format', 'json')) | ConvertFrom-Json
    if (@($resolved.services.PSObject.Properties).Count -ne 1 -or -not $resolved.services.nxserver) {
        throw 'Dieser lokale Starter akzeptiert nur den Dienst nxserver.'
    }
    if (@($resolved.secrets.PSObject.Properties).Count -ne 1 -or
        @($resolved.services.nxserver.secrets).Count -ne 1 -or
        $resolved.services.nxserver.secrets[0].source -ne 'nx_admin_token') {
        throw 'Dieser lokale Starter akzeptiert nur das konfigurierte Administrations-Secret.'
    }
    $permitted = @{
        '/app/config/settings.json' = (Join-Path $PSScriptRoot 'docker\settings.windows.json')
        '/inputs/nx2512-managed' = $sourcePaths[0]
        '/standards/2406' = $sourcePaths[1]
        '/standards/2512' = $sourcePaths[2]
        '/logs/nx-test' = $sourcePaths[3]
        '/data/r' = $releasePath
    }
    foreach ($mount in $resolved.services.nxserver.volumes) {
        if ($mount.type -eq 'volume' -and $mount.target -eq '/data') {
            if ($mount.source -ne 'management-data') { throw 'Unerwartetes Verwaltungsvolume.' }
            continue
        }
        if ($mount.type -ne 'bind' -or -not $permitted.ContainsKey($mount.target)) { throw 'Unerwartete Docker-Einbindung; Konfiguration pruefen.' }
        Assert-NXOfflinePath -Path $mount.source -Label 'Aufgeloeste Docker-Einbindung'
        $actual = [System.IO.Path]::GetFullPath($mount.source).TrimEnd('\', '/')
        $expected = [System.IO.Path]::GetFullPath($permitted[$mount.target]).TrimEnd('\', '/')
        if (-not $actual.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Unerwartete Lese-/Ausgabequelle fuer $($mount.target)." }
        if ($mount.target -ne '/data/r' -and $mount.read_only -ne $true) { throw "Lesequelle muss readonly sein: $($mount.target)" }
    }
    $targets = @($resolved.services.nxserver.volumes | ForEach-Object { $_.target })
    if ($targets.Count -ne 7 -or @($permitted.Keys | Where-Object { $_ -notin $targets }).Count -gt 0 -or '/data' -notin $targets) {
        throw 'Die erwarteten sieben Docker-Einbindungen sind nicht vollstaendig vorhanden.'
    }
    $managedVolume = $resolved.volumes.'management-data'
    if ($managedVolume.driver_opts -or $managedVolume.external -or ($managedVolume.driver -and $managedVolume.driver -ne 'local')) {
        throw 'Die Verwaltungsdaten muessen in einem lokalen, nicht externen Docker-Volume liegen.'
    }
    $secretFile = $resolved.secrets.nx_admin_token.file
    Assert-NXOfflinePath -Path $secretFile -Label 'Docker-Secret'
    if (-not ([System.IO.Path]::GetFullPath($secretFile)).Equals((Join-Path $PSScriptRoot 'docker\secrets\admin_token.txt'), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Unerwarteter Docker-Secret-Pfad.'
    }
    Write-Host 'Lokale Quellbaeume werden auf Links/Junctions geprueft. Dateien werden nicht geaendert.'
    foreach ($sourcePath in $sourcePaths) { Assert-NXTreeNoReparsePoint -Root $sourcePath }
    if (-not (Test-Path -LiteralPath $releasePath)) { [void][System.IO.Directory]::CreateDirectory($releasePath) }
    $secretDirectory = Join-Path $PSScriptRoot 'docker\secrets'
    $secretPath = Join-Path $secretDirectory 'admin_token.txt'
    Assert-NXOfflinePath -Path $secretPath -Label 'Lokaler Docker-Schluessel'
    if (-not (Test-Path -LiteralPath $secretPath)) {
        [void][System.IO.Directory]::CreateDirectory($secretDirectory)
        $bytes = New-Object byte[] 48
        $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $random.GetBytes($bytes) } finally { $random.Dispose() }
        $key = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        $stream = New-Object System.IO.FileStream($secretPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
        try { $data = [System.Text.Encoding]::ASCII.GetBytes($key); $stream.Write($data, 0, $data.Length) } finally { $stream.Dispose() }
    }
    # Restrict the local secret to its Windows owner, SYSTEM and Administrators.
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetOwner($owner)
    foreach ($sid in @($owner, (New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')),
                              (New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')))) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl, [System.Security.AccessControl.AccessControlType]::Allow)))
    }
    Set-Acl -LiteralPath $secretPath -AclObject $acl
    Invoke-Compose -Arguments @('config', '--quiet')
    Invoke-Compose -Arguments @('up', '-d', '--build', '--wait', '--wait-timeout', '120')
    Write-Host 'NX-Verwaltung laeuft unter http://127.0.0.1:8765 (ggf. NX_WEB_PORT).'
    Write-Host "Administrationsschluessel liegt lokal in $secretPath. Nicht weitergeben oder einchecken."
    Write-Host 'Das Startfenster kann geschlossen werden. Stoppen: Start-NX-Docker.ps1 -Action Stop (ohne Volume-Loeschung).'
}
catch { Write-Error $_; exit 1 }
