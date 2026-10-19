# Fixed local Windows test profile. This validator performs no filesystem/Docker IO.
Set-StrictMode -Version Latest

function Get-NXComposeKeys {
    param($Value)
    if ($Value -is [System.Collections.IDictionary]) { return @($Value.Keys) }
    if ($Value -is [System.Management.Automation.PSCustomObject]) { return @($Value.PSObject.Properties.Name) }
    throw 'Docker-Testprofil: Ein Konfigurationsobjekt wird erwartet.'
}

function Get-NXComposeValue {
    param($Value, [string]$Name)
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Collections.IDictionary]) {
        if ($Value.Contains($Name)) { return $Value[$Name] }
        return $null
    }
    $property = $Value.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $null
}

function Assert-NXComposeKeys {
    param($Value, [string[]]$Allowed, [string]$Label)
    foreach ($name in @(Get-NXComposeKeys $Value)) {
        if ($name -cnotin $Allowed) { throw "Docker-Testprofil: Unerwartete $Label-Einstellung; manuell pruefen." }
    }
}

function Assert-NXComposePath {
    param($Actual, [string]$Expected)
    if ($Actual -isnot [string] -or $Actual -notmatch '^[Cc]:[\\/]' -or $Actual -match '\.\.' -or $Actual -match '(?i)SiemensShare[\\/]nx2406') {
        throw 'Docker-Testprofil: Nicht freigegebener lokaler Pfad.'
    }
    $a = [System.IO.Path]::GetFullPath($Actual).TrimEnd('\', '/')
    $b = [System.IO.Path]::GetFullPath($Expected).TrimEnd('\', '/')
    if (-not $a.Equals($b, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Docker-Testprofil: Unerwartete lokale Quelle oder Ausgabe.' }
}

function Assert-NXLocalDockerProfile {
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Configuration, [Parameter(Mandatory)][string]$ProjectRoot,
          [Parameter(Mandatory)][System.Collections.IDictionary]$PermittedBindings,
          [Parameter(Mandatory)][ValidateRange(1,65535)][int]$WebPort)

    Assert-NXComposeKeys $Configuration @('name','services','volumes','networks','secrets') 'Projekt'
    if ((Get-NXComposeValue $Configuration 'name') -cne 'nx-management') { throw 'Docker-Testprofil: Falscher Projektname.' }
    $services = Get-NXComposeValue $Configuration 'services'
    if (@(Get-NXComposeKeys $services).Count -ne 1 -or 'nxserver' -cnotin @(Get-NXComposeKeys $services)) { throw 'Docker-Testprofil: Nur nxserver ist zugelassen.' }
    $service = Get-NXComposeValue $services 'nxserver'
    Assert-NXComposeKeys $service @('build','cap_drop','environment','image','init','logging','networks','ports','read_only','restart','secrets','security_opt','stop_grace_period','tmpfs','user','volumes') 'Dienst'
    foreach ($flag in @('read_only','init')) { if ((Get-NXComposeValue $service $flag) -isnot [bool]) { throw 'Docker-Testprofil: Laufzeitflags muessen JSON-Booleans sein.' } }
    if ((Get-NXComposeValue $service 'image') -cne 'nx-management:local' -or
        (Get-NXComposeValue $service 'user') -cne '10001:10001' -or
        (Get-NXComposeValue $service 'read_only') -ne $true -or
        (Get-NXComposeValue $service 'init') -ne $true -or
        (Get-NXComposeValue $service 'restart') -cne 'unless-stopped' -or
        (Get-NXComposeValue $service 'stop_grace_period') -cne '30s') { throw 'Docker-Testprofil: Laufzeit-/Benutzer-/Schreibschutzvertrag fehlt.' }
    $caps = @(Get-NXComposeValue $service 'cap_drop')
    $security = @(Get-NXComposeValue $service 'security_opt')
    if ($caps.Count -ne 1 -or $caps[0] -cne 'ALL' -or $security.Count -ne 1 -or $security[0] -notin @('no-new-privileges:true','no-new-privileges')) { throw 'Docker-Testprofil: Capabilities oder Privilegschutz geaendert.' }
    $temporary = @(Get-NXComposeValue $service 'tmpfs')
    if ($temporary.Count -ne 1 -or $temporary[0] -cne '/tmp:size=64m,mode=1777') { throw 'Docker-Testprofil: Unerwartetes temporaeres Dateisystem.' }
    $environment = Get-NXComposeValue $service 'environment'
    if (@(Get-NXComposeKeys $environment).Count -ne 1 -or (Get-NXComposeValue $environment 'NX_ADMIN_TOKEN_FILE') -cne '/run/secrets/nx_admin_token') { throw 'Docker-Testprofil: Nur das bekannte Datei-Secret ist als Umgebung zugelassen.' }
    $ports = @(Get-NXComposeValue $service 'ports')
    if ($ports.Count -ne 1 -or (Get-NXComposeValue $ports[0] 'host_ip') -cne '127.0.0.1' -or
        [string](Get-NXComposeValue $ports[0] 'published') -cne [string]$WebPort -or
        (Get-NXComposeValue $ports[0] 'target') -ne 8765 -or
        (Get-NXComposeValue $ports[0] 'protocol') -cne 'tcp' -or
        (Get-NXComposeValue $ports[0] 'mode') -notin @($null,'ingress')) { throw 'Docker-Testprofil: Genau ein lokaler TCP-Port wird erwartet.' }
    $build = Get-NXComposeValue $service 'build'
    Assert-NXComposeKeys $build @('context','dockerfile') 'Build'
    Assert-NXComposePath (Get-NXComposeValue $build 'context') (Join-Path $ProjectRoot 'server')
    if ((Get-NXComposeValue $build 'dockerfile') -cne 'Dockerfile') { throw 'Docker-Testprofil: Unerwartete Builddatei.' }
    $log = Get-NXComposeValue $service 'logging'
    Assert-NXComposeKeys $log @('driver','options') 'Log'
    $logOptions = Get-NXComposeValue $log 'options'
    Assert-NXComposeKeys $logOptions @('max-size','max-file') 'Logbegrenzung'
    if ((Get-NXComposeValue $log 'driver') -cne 'json-file' -or (Get-NXComposeValue $logOptions 'max-size') -cne '10m' -or (Get-NXComposeValue $logOptions 'max-file') -cne '3') { throw 'Docker-Testprofil: Begrenzte lokale Logs erforderlich.' }
    $networks = Get-NXComposeValue $service 'networks'
    if (@(Get-NXComposeKeys $networks).Count -ne 1 -or 'default' -cnotin @(Get-NXComposeKeys $networks)) { throw 'Docker-Testprofil: Nur das Projektnetz ist zugelassen.' }
    if ($null -ne (Get-NXComposeValue $networks 'default')) { throw 'Docker-Testprofil: Keine zusaetzlichen Netzoptionen.' }
    $projectNetworks = Get-NXComposeValue $Configuration 'networks'
    if (@(Get-NXComposeKeys $projectNetworks).Count -ne 1 -or 'default' -cnotin @(Get-NXComposeKeys $projectNetworks)) { throw 'Docker-Testprofil: Nur das lokale Standardnetz ist zugelassen.' }
    $network = Get-NXComposeValue $projectNetworks 'default'
    Assert-NXComposeKeys $network @('name','driver') 'Netz'
    if ((Get-NXComposeValue $network 'name') -cne 'nx-management_default' -or (Get-NXComposeValue $network 'driver') -notin @($null,'bridge')) { throw 'Docker-Testprofil: Kein fremdes/externes Netz.' }
    $volumes = Get-NXComposeValue $Configuration 'volumes'
    if (@(Get-NXComposeKeys $volumes).Count -ne 1 -or 'management-data' -cnotin @(Get-NXComposeKeys $volumes)) { throw 'Docker-Testprofil: Nur das Verwaltungsvolume ist zugelassen.' }
    $volume = Get-NXComposeValue $volumes 'management-data'
    Assert-NXComposeKeys $volume @('name','driver') 'Volume'
    if ((Get-NXComposeValue $volume 'name') -cne 'nx-management_management-data' -or (Get-NXComposeValue $volume 'driver') -notin @($null,'local')) { throw 'Docker-Testprofil: Kein fremdes/externes Verwaltungsvolume.' }
    $secrets = Get-NXComposeValue $Configuration 'secrets'
    if (@(Get-NXComposeKeys $secrets).Count -ne 1 -or 'nx_admin_token' -cnotin @(Get-NXComposeKeys $secrets)) { throw 'Docker-Testprofil: Nur das Administrations-Secret ist zugelassen.' }
    $secret = Get-NXComposeValue $secrets 'nx_admin_token'
    Assert-NXComposeKeys $secret @('file','name') 'Secret'
    Assert-NXComposePath (Get-NXComposeValue $secret 'file') (Join-Path $ProjectRoot 'docker\secrets\admin_token.txt')
    $serviceSecrets = @(Get-NXComposeValue $service 'secrets')
    if ($serviceSecrets.Count -ne 1 -or (Get-NXComposeValue $serviceSecrets[0] 'source') -cne 'nx_admin_token' -or
        (Get-NXComposeValue $serviceSecrets[0] 'target') -notin @($null,'nx_admin_token','/run/secrets/nx_admin_token')) { throw 'Docker-Testprofil: Unerwartete Secret-Einbindung.' }
    Assert-NXComposeKeys $serviceSecrets[0] @('source','target') 'Secret-Einbindung'
    $mounts = @(Get-NXComposeValue $service 'volumes');$seen = @{}
    if ($mounts.Count -ne $PermittedBindings.Count + 1) { throw 'Docker-Testprofil: Einbindungen unvollstaendig oder erweitert.' }
    foreach ($mount in $mounts) {
        Assert-NXComposeKeys $mount @('type','source','target','read_only','bind') 'Einbindung'
        $target = Get-NXComposeValue $mount 'target'
        $readonly = Get-NXComposeValue $mount 'read_only'
        if ($null -ne $readonly -and $readonly -isnot [bool]) { throw 'Docker-Testprofil: Mountflags muessen JSON-Booleans sein.' }
        if ($target -isnot [string] -or $seen.ContainsKey($target)) { throw 'Docker-Testprofil: Doppelte/ungueltige Einbindung.' }
        $seen[$target] = $true
        if ($target -ceq '/data') {
            if ((Get-NXComposeValue $mount 'type') -cne 'volume' -or (Get-NXComposeValue $mount 'source') -cne 'management-data' -or (Get-NXComposeValue $mount 'read_only') -eq $true -or $null -ne (Get-NXComposeValue $mount 'bind')) { throw 'Docker-Testprofil: Verwaltungsvolume muss unveraendert schreibbar sein.' }
            continue
        }
        if ((Get-NXComposeValue $mount 'type') -cne 'bind' -or $target -cnotin @($PermittedBindings.Keys)) { throw 'Docker-Testprofil: Unerwartete Einbindung.' }
        Assert-NXComposePath (Get-NXComposeValue $mount 'source') $PermittedBindings[$target]
        if ($target -ceq '/data/r') {
            if ((Get-NXComposeValue $mount 'read_only') -eq $true) { throw 'Docker-Testprofil: Die getrennte Testausgabe muss schreibbar sein.' }
        } elseif ((Get-NXComposeValue $mount 'read_only') -ne $true) { throw 'Docker-Testprofil: Lesequellen muessen schreibgeschuetzt sein.' }
        $bind = Get-NXComposeValue $mount 'bind'
        Assert-NXComposeKeys $bind @('create_host_path') 'Bindoption'
        if ((Get-NXComposeValue $bind 'create_host_path') -isnot [bool] -or (Get-NXComposeValue $bind 'create_host_path') -ne $false) { throw 'Docker-Testprofil: Keine automatischen Hostverzeichnisse.' }
    }
}

function Get-NXWindowsUpgradeInputs {
    [CmdletBinding()]
    param([Parameter(Mandatory)]$Configuration)
    $fields = @('source_nx_version', 'source', 'sidt_2406', 'sidt_2512')
    Assert-NXComposeKeys $Configuration $fields 'Upgrade-Eingabe'
    $sourceVersion = Get-NXComposeValue $Configuration 'source_nx_version'
    if (@(Get-NXComposeKeys $Configuration).Count -ne $fields.Count -or
        $sourceVersion -isnot [string] -or $sourceVersion -cne '2406') {
        throw 'Docker-Upgrade: Eine ausdruecklich als NX2406 bestaetigte lokale Testkopie und beide SIDTs sind erforderlich.'
    }
    $result = @{}
    foreach ($field in @('source', 'sidt_2406', 'sidt_2512')) {
        $value = Get-NXComposeValue $Configuration $field
        Assert-NXComposePath $value $value
        if ($value -match '[\x00-\x1f*?"<>|]' -or $value.Substring(2) -match ':') {
            throw 'Docker-Upgrade: Ungueltiger lokaler Eingabepfad.'
        }
        $path = [System.IO.Path]::GetFullPath($value).TrimEnd('\', '/')
        if ($path -match '^[Cc]:$' -or $path -in @('C:\Windows', 'C:\Users', 'C:\Program Files', 'C:\Program Files (x86)', 'C:\ProgramData', 'C:\Siemens', 'C:\NX_Migration')) {
            throw 'Docker-Upgrade: Einen konkreten NX-/SIDT-Ordner angeben, keine breite Laufwerks-/Systemwurzel.'
        }
        foreach ($previous in $result.Values) {
            if ($path.Equals($previous, [StringComparison]::OrdinalIgnoreCase) -or
                $path.StartsWith($previous + '\', [StringComparison]::OrdinalIgnoreCase) -or
                $previous.StartsWith($path + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Docker-Upgrade: Alte Testkopie und beide SIDT-Eingaben muessen getrennte Ordner sein.'
            }
        }
        $result[$field] = $path
    }
    return $result
}

Export-ModuleMember -Function Assert-NXLocalDockerProfile, Get-NXWindowsUpgradeInputs
