#requires -Version 5.1
[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status', 'Config')][string]$Action = 'Start',
      [ValidateSet('Prepared', 'Upgrade')][string]$Profile = 'Prepared',
      [string]$UpgradeInputsFile)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'NXDockerSafety.psm1') -Force

$composePrefix = @('compose', '-p', 'nx-management', '--project-directory', $PSScriptRoot,
    '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '-f', (Join-Path $PSScriptRoot 'compose.windows.yaml'))
$validatedComposeFile = $null
$validatedComposePath = $null
$validatedComposePrefix = $null
$composeEnvironment = @{}
$dockerConnectionArguments = @()
$dockerExecutable = $null
$dockerSearchPath = $null

function Get-DockerOutput {
    param([string[]]$Arguments)
    if (-not $dockerExecutable) { throw 'Kein geprueftes lokales Docker-Programm vorhanden.' }
    $executable = $dockerExecutable
    $dockerArguments = $dockerConnectionArguments + $Arguments
    $quoted = foreach ($argument in $dockerArguments) {
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
    $process.StartInfo.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    foreach ($name in $composeEnvironment.Keys) { $process.StartInfo.EnvironmentVariables[$name] = $composeEnvironment[$name] }
    $process.StartInfo.EnvironmentVariables['PATH'] = $dockerSearchPath
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
    if (-not $validatedComposeFile -or $dockerConnectionArguments.Count -ne 2) { throw 'Kein gepruefter lokaler Docker-Ausfuehrungsplan vorhanden.' }
    # Feed the same approved plan for every action. Original YAML/.env changes
    # after validation must not alter the configuration used by Compose.
    $code = Invoke-NXExternalProcess -FilePath $dockerExecutable -Arguments ($dockerConnectionArguments + $validatedComposePrefix + $Arguments) -WorkingDirectory $PSScriptRoot
    if ($code -ne 0) { throw "Docker Compose ist fehlgeschlagen (Exit $code)." }
}

function Read-UpgradeInputs {
    if ([string]::IsNullOrWhiteSpace($UpgradeInputsFile)) { throw 'Fuer -Profile Upgrade ist -UpgradeInputsFile erforderlich. Beispiel: docker\upgrade-inputs.example.json.' }
    Assert-NXOfflinePath -Path $UpgradeInputsFile -Label 'Lokale Docker-Upgrade-Eingabedatei'
    $item = Get-Item -LiteralPath $UpgradeInputsFile -Force -ErrorAction Stop
    if ($item -isnot [System.IO.FileInfo] -or $item.Length -lt 2 -or $item.Length -gt 16384) { throw 'Die Upgrade-Eingabedatei muss eine kleine regulaere JSON-Datei sein (max. 16 KiB).' }
    $stream = New-Object System.IO.FileStream($item.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 2 -or $stream.Length -gt 16384) { throw 'Ungueltige Upgrade-Eingabegroesse.' }
        $reader = New-Object System.IO.StreamReader($stream, (New-Object System.Text.UTF8Encoding($false, $true)), $true)
        try { $json = $reader.ReadToEnd() } finally { $reader.Dispose() }
        try { $configuration = $json | ConvertFrom-Json -ErrorAction Stop } catch { throw 'Die Upgrade-Eingabedatei enthaelt kein gueltiges JSON.' }
        return Get-NXWindowsUpgradeInputs -Configuration $configuration
    }
    finally { $stream.Dispose() }
}

try {
    Assert-NXOfflinePath -Path $PSScriptRoot -Label 'Projektordner'
    if ($env:DOCKER_HOST -and $env:DOCKER_HOST -notmatch '^npipe:////\./pipe/(docker_engine|dockerDesktopLinuxEngine)$') {
        throw 'DOCKER_HOST darf fuer diesen Starter nur auf eine lokale Docker-Desktop Pipe zeigen.'
    }
    $dockerSearchPath = Get-NXOfflineSearchPath -InheritedPath $env:PATH
    try { $dockerExecutable = Resolve-NXOfflineExecutable -FilePath 'docker.exe' -SearchPath $dockerSearchPath }
    catch { throw ('Docker Desktop mit Linux-Containern und Docker Compose wird benoetigt; kein geprueftes lokales Programm vorhanden. ' + $_.Exception.Message) }
    $contextName = Get-DockerOutput -Arguments @('context', 'show')
    $endpoint = Get-DockerOutput -Arguments @('context', 'inspect', $contextName, '--format', '{{.Endpoints.docker.Host}}')
    if ($endpoint -notmatch '^npipe:////\./pipe/(docker_engine|dockerDesktopLinuxEngine)$') {
        throw 'Dieser Windows-Starter akzeptiert nur eine lokale Docker-Desktop Pipe, keinen Remote-Kontext.'
    }
    # Pin the verified endpoint, not the mutable context name/default selection.
    # Explicit --host takes precedence over inherited context/host environment.
    $dockerConnectionArguments = @('--host', $endpoint)
    # Validate fixed local paths BEFORE Docker could mount them. No W:/UNC,
    # network-drive alias or junction is accepted by this starter.
    $sourcePaths = @('C:\NX_Migration\NX2512_Managed_20261006', 'C:\Siemens\NX2406',
                     'C:\Siemens\NX2512', 'C:\NX_Migration\TestRuntime\temp')
    $releasePath = 'C:\NX_Migration\DockerReleases'
    $permitted = @{
        '/app/config/settings.json' = (Join-Path $PSScriptRoot 'docker\settings.windows.json')
        '/inputs/nx2512-managed' = $sourcePaths[0]
        '/standards/2406' = $sourcePaths[1]
        '/standards/2512' = $sourcePaths[2]
        '/logs/nx-test' = $sourcePaths[3]
        '/data/r' = $releasePath
    }
    if ($Profile -eq 'Upgrade') {
        $inputs = Read-UpgradeInputs
        $sourcePaths = @($inputs.source, 'C:\Siemens\NX2406', 'C:\Siemens\NX2512', $inputs.sidt_2406, $inputs.sidt_2512)
        $permitted = @{
            '/app/config/settings.json' = (Join-Path $PSScriptRoot 'docker\settings.windows-upgrade.json')
            '/inputs/nx2406-local' = $sourcePaths[0]
            '/standards/2406' = $sourcePaths[1]
            '/standards/2512' = $sourcePaths[2]
            '/sidts/2406' = $sourcePaths[3]
            '/sidts/2512' = $sourcePaths[4]
            '/data/r' = $releasePath
        }
        foreach ($sourcePath in $sourcePaths) {
            Assert-NXOfflinePath -Path $sourcePath -Label 'Lokale Docker-Upgrade-Lesequelle'
            Assert-NXDisjointPaths -First $sourcePath -Second $releasePath
        }
        $composeEnvironment = @{
            'NX_UPGRADE_SOURCE' = $inputs.source.Replace('\', '/')
            'NX_UPGRADE_SIDT_2406' = $inputs.sidt_2406.Replace('\', '/')
            'NX_UPGRADE_SIDT_2512' = $inputs.sidt_2512.Replace('\', '/')
        }
        $composePrefix = @('compose', '-p', 'nx-management', '--project-directory', $PSScriptRoot,
            '-f', (Join-Path $PSScriptRoot 'compose.yaml'), '-f', (Join-Path $PSScriptRoot 'compose.windows-upgrade.yaml'))
    }
    elseif ($PSBoundParameters.ContainsKey('UpgradeInputsFile')) { throw '-UpgradeInputsFile ist nur zusammen mit -Profile Upgrade erlaubt.' }
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
    $webPort = 8765
    if ($env:NX_WEB_PORT) {
        if ($env:NX_WEB_PORT -notmatch '^[1-9][0-9]{0,4}$' -or [int]$env:NX_WEB_PORT -gt 65535) { throw 'NX_WEB_PORT muss eine lokale Portnummer zwischen 1 und 65535 sein.' }
        $webPort = [int]$env:NX_WEB_PORT
    }
    # Pure resolved-profile gate applies to ALL actions, before state changes.
    Assert-NXLocalDockerProfile -Configuration $resolved -ProjectRoot $PSScriptRoot -PermittedBindings $permitted -WebPort $webPort
    # Compose parses the frozen JSON as a Compose document again. Escape dollars
    # in the already-resolved values so this second parse cannot interpolate them.
    $validatedComposeJson = ($resolved | ConvertTo-Json -Depth 100 -Compress).Replace('$', '$$')
    $temporaryRoot = [System.IO.Path]::GetTempPath()
    Assert-NXOfflinePath -Path $temporaryRoot -Label 'Temporaerer Docker-Planordner'
    $validatedComposePath = Join-Path $temporaryRoot ('nx-compose-' + [Guid]::NewGuid().ToString('N') + '.json')
    Assert-NXOfflinePath -Path $validatedComposePath -Label 'Gepruefter Docker-Ausfuehrungsplan'
    foreach ($protectedRoot in (@($PSScriptRoot, $releasePath) + $sourcePaths)) {
        Assert-NXDisjointPaths -First $validatedComposePath -Second $protectedRoot
    }
    # CreateNew cannot replace another file. Keep this handle open: readers are
    # allowed, but no other process may change or delete the plan during actions.
    $validatedComposeFile = New-Object System.IO.FileStream($validatedComposePath, [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::Read)
    $planBytes = [System.Text.Encoding]::UTF8.GetBytes($validatedComposeJson)
    $validatedComposeFile.Write($planBytes, 0, $planBytes.Length)
    $validatedComposeFile.Flush()
    $validatedComposePrefix = @('compose', '-p', 'nx-management', '--project-directory', $PSScriptRoot,
        '-f', $validatedComposePath)
    if ($Action -eq 'Stop') { Invoke-Compose -Arguments @('stop', 'nxserver'); Write-Host 'Nur nxserver gestoppt; Container, Netz und Datenvolume bleiben erhalten.'; exit 0 }
    if ($Action -eq 'Status') { Invoke-Compose -Arguments @('ps', 'nxserver'); exit 0 }
    if ($Action -eq 'Config') { Invoke-Compose -Arguments @('config', '--quiet'); exit 0 }
    Assert-NXOfflinePath -Path $releasePath -Label 'Docker-Testausgabe'
    Assert-NXDisjointPaths -First $PSScriptRoot -Second $releasePath
    foreach ($sourcePath in $sourcePaths) {
        Assert-NXOfflinePath -Path $sourcePath -Label 'Docker-Lesequelle'
        Assert-NXDisjointPaths -First $releasePath -Second $sourcePath
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) { throw "Lokale Lesequelle fehlt: $sourcePath" }
    }
    if ($Profile -eq 'Upgrade') {
        $required = @(
            @($inputs.source, 'MACH\resource', 'Container'), @($inputs.source, 'UGII', 'Container'),
            @($sourcePaths[1], 'NXBIN\ugraf.exe', 'Leaf'), @($sourcePaths[2], 'NXBIN\ugraf.exe', 'Leaf'))
        foreach ($sidt in @($inputs.sidt_2406, $inputs.sidt_2512)) {
            $required += ,@($sidt, 'applications\nx\after_install.bat', 'Leaf')
            $required += ,@($sidt, 'applications\nx\ugii', 'Container')
            $required += ,@($sidt, 'applications\nx\nxplot', 'Container')
        }
        foreach ($entry in $required) {
            $path = Join-Path $entry[0] $entry[1]
            Assert-NXOfflinePath -Path $path -Label 'Docker-Upgrade-Eingabestruktur'
            if (-not (Test-Path -LiteralPath $path -PathType $entry[2])) { throw 'Unvollstaendige NX2406-/Siemens-/SIDT-Eingabe. Keine Quellen werden automatisch angelegt oder Installer ausgefuehrt.' }
        }
    }
    Assert-NXOfflinePath -Path (Join-Path $PSScriptRoot 'server') -Label 'Docker-Buildordner'
    Assert-NXOfflinePath -Path (Join-Path $PSScriptRoot 'server\Dockerfile') -Label 'Docker-Builddatei'
    Assert-NXTreeNoReparsePoint -Root (Join-Path $PSScriptRoot 'server\nxserver')
    # Require a local Linux daemon, never a remote engine.
    $daemonText = Get-DockerOutput -Arguments @('info', '--format', '{{.OSType}}')
    if ($daemonText -ne 'linux') { throw 'Docker Desktop muss laufen und auf Linux-Container eingestellt sein.' }
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
    if ($targets.Count -ne ($permitted.Count + 1) -or @($permitted.Keys | Where-Object { $_ -notin $targets }).Count -gt 0 -or '/data' -notin $targets) {
        throw 'Die erwarteten Docker-Einbindungen sind nicht vollstaendig vorhanden.'
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
    if (Test-Path -LiteralPath $releasePath) { Assert-NXTreeNoReparsePoint -Root $releasePath }
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
finally {
    if ($validatedComposeFile) {
        $validatedComposeFile.Dispose()
        [System.IO.File]::Delete($validatedComposePath)
    }
}
