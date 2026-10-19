[CmdletBinding()]
param(
    [string]$SystemPalette = 'C:\Siemens\NX2512\MACH\localization\german\ugs_manufacturing_templates_german.pax',
    [string]$CustomPalette = 'C:\NX_Migration\NX2512_AfterInstall\MACH\resource\usertools\templates\ugs_manufacturing_templates_german.pax',
    [string]$OutputPalette = 'C:\NX_Migration\NX2512-TestConfig\CAMTemplates\startup\ugs_manufacturing_templates_german.pax',
    [string]$StagingRoot = 'C:\NX_Migration\NX2512_AfterInstall'
)
. (Join-Path $PSScriptRoot 'server\nxserver\testlaunch_assets\New-NX2512TestPalette.ps1') @PSBoundParameters
