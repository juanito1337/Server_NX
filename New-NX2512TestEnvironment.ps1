#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$StagingRoot = 'C:\NX_Migration\NX2512_AfterInstall',
    [string]$NXInstallRoot = 'C:\Siemens\NX2512',
    [Parameter(Mandatory)][string]$OutputRoot,
    [string]$ReleaseManifestPath
)
. (Join-Path $PSScriptRoot 'server\nxserver\testlaunch_assets\New-NX2512TestEnvironment.ps1') @PSBoundParameters
