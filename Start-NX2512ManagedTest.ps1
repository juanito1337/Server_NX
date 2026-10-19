#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory)][string]$ConfigPath, [switch]$CheckOnly)
. (Join-Path $PSScriptRoot 'server\nxserver\testlaunch_assets\Start-NX2512ManagedTest.ps1') @PSBoundParameters
