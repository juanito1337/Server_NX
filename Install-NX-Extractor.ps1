#requires -Version 5.1
<# Installs a pinned PORTABLE archive reader, not Siemens software. No system install. #>
[CmdletBinding()]
param([string]$Destination = 'C:\NX_Migration\ServerTools\innounp-2.71.1')
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force
Assert-NXOfflinePath -Path $Destination
if (Test-Path -LiteralPath $Destination) { throw 'Tool destination already exists; choose a new directory.' }
$url = 'https://raw.githubusercontent.com/jrathlev/InnoUnpacker-Windows-GUI/refs/heads/master/innounp-2/bin/innounp-2.zip'
# Published by the Scoop Main package manifest; a changed upstream is rejected.
$expected = 'f2f037fdbc63de31248efae9ccb294398d160dc0cbad5f36d14c2f159e17bbf5'
New-Item -ItemType Directory -Path $Destination | Out-Null
$archive = Join-Path $Destination 'tool-download.zip'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected) {
    throw 'Download SHA-256 differs from pinned digest. Retain evidence; do not execute it.'
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    foreach ($entry in $zip.Entries) {
        $name = $entry.FullName.Replace('\', '/')
        if ($name.StartsWith('/') -or $name -match '(^|/)\.\.?(?:/|$)|[:*?"<>|\x00-\x1f]' -or
            (($entry.ExternalAttributes -shr 16) -band 61440) -eq 40960) { throw 'Unsafe tool ZIP entry.' }
        $target = Join-Path $Destination $name
        Assert-NXOfflinePath -Path $target
        if ($name.EndsWith('/')) { New-Item -ItemType Directory -Path $target -Force | Out-Null; continue }
        $parent = Split-Path $target -Parent
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $false)
    }
}
finally { $zip.Dispose() }
$tool = Join-Path $Destination 'innounp.exe'
if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw 'Pinned package has no expected tool.' }
$metadata = [ordered]@{ version = '2.71.1'; url = $url; archive_sha256 = $expected;
    path = $tool; sha256 = (Get-FileHash -LiteralPath $tool -Algorithm SHA256).Hash.ToLowerInvariant() }
$metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'tool.json') -Encoding UTF8
$metadata | ConvertTo-Json
Write-Host 'Portable tool downloaded and verified. No installer or extracted Siemens code executed.'
