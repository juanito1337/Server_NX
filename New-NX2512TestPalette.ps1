[CmdletBinding()]
param(
    [string]$SystemPalette = 'C:\Siemens\NX2512\MACH\localization\german\ugs_manufacturing_templates_german.pax',
    [string]$CustomPalette = 'C:\NX_Migration\NX2512_AfterInstall\MACH\resource\usertools\templates\ugs_manufacturing_templates_german.pax',
    [string]$OutputPalette = 'C:\NX_Migration\NX2512-TestConfig\CAMTemplates\startup\ugs_manufacturing_templates_german.pax',
    [string]$StagingRoot = 'C:\NX_Migration\NX2512_AfterInstall'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force
foreach ($inputPath in @($SystemPalette, $CustomPalette, $StagingRoot)) {
    Assert-NXOfflinePath -Path $inputPath
}
Assert-NXOfflinePath -Path $OutputPalette -Label OutputPalette
Assert-NXDisjointPaths -First $OutputPalette -Second $SystemPalette
Assert-NXDisjointPaths -First $OutputPalette -Second $CustomPalette
if (Test-Path -LiteralPath $OutputPalette) { throw 'OutputPalette already exists; use a new output file.' }

function Get-FullPathWithSeparator {
    param([Parameter(Mandatory)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    return $fullPath + '\'
}

function Read-PaletteXml {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "PAX-Datei fehlt: $Path"
    }

    $document = New-Object System.Xml.XmlDocument
    $document.PreserveWhitespace = $false
    $document.Load($Path)

    if ($document.DocumentElement.LocalName -ne 'Palette') {
        throw "Unerwartetes Wurzelelement in '$Path': $($document.DocumentElement.Name)"
    }

    return $document
}

function Get-PaletteEntries {
    param([Parameter(Mandatory)][System.Xml.XmlDocument]$Document)

    $namespace = New-Object System.Xml.XmlNamespaceManager($Document.NameTable)
    $namespace.AddNamespace('p', $Document.DocumentElement.NamespaceURI)
    return @($Document.SelectNodes('/p:Palette/p:PaletteEntry', $namespace))
}

function Resolve-TestReference {
    param(
        [Parameter(Mandatory)][string]$Value,
        [Parameter(Mandatory)][hashtable]$Variables
    )

    $resolved = $Value
    foreach ($name in $Variables.Keys) {
        $resolved = $resolved.Replace(('${' + $name + '}'), $Variables[$name])
    }
    return $resolved
}

$outputFullPath = [System.IO.Path]::GetFullPath($OutputPalette)
$stagingPrefix = Get-FullPathWithSeparator -Path $StagingRoot
if (($outputFullPath + '\').StartsWith($stagingPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Die Test-PAX darf nicht in den validierten Staging-Baum geschrieben werden: $outputFullPath"
}
if ($outputFullPath.StartsWith('W:\SiemensShare\nx2406\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Das Produktivverzeichnis W:\SiemensShare\nx2406 darf nicht veraendert werden.'
}

$systemDocument = Read-PaletteXml -Path $SystemPalette
$customDocument = Read-PaletteXml -Path $CustomPalette

if ($systemDocument.DocumentElement.NamespaceURI -ne $customDocument.DocumentElement.NamespaceURI) {
    throw 'System- und Kunden-PAX verwenden unterschiedliche XML-Namespaces.'
}

$systemEntries = @(Get-PaletteEntries -Document $systemDocument)
$customEntries = @(Get-PaletteEntries -Document $customDocument)
if ($systemEntries.Count -eq 0) {
    throw 'Die NX2512-System-PAX enthaelt keine Vorlageneintraege.'
}
if ($customEntries.Count -eq 0) {
    throw 'Die migrierte Kunden-PAX enthaelt keine Vorlageneintraege.'
}

$ids = @{}
foreach ($entry in $systemEntries) {
    $id = [string]$entry.GetAttribute('id')
    if ([string]::IsNullOrWhiteSpace($id) -or $ids.ContainsKey($id)) {
        throw "Fehlende oder doppelte Eintrags-ID in der NX2512-System-PAX: '$id'"
    }
    $ids[$id] = $true
}

$testVariables = @{
    UGII_CAM_TEMPLATE_PART_METRIC_DIR = (Join-Path $StagingRoot 'MACH\resource\template_part\metric') + '\'
    UGII_CAM_TEMPLATE_PART_ENGLISH_DIR = (Join-Path $StagingRoot 'MACH\resource\template_part\english') + '\'
    UGII_CAM_CONFIG_DIR = (Join-Path $StagingRoot 'MACH\resource\configuration') + '\'
}

foreach ($entry in $customEntries) {
    $id = [string]$entry.GetAttribute('id')
    if ([string]::IsNullOrWhiteSpace($id) -or $ids.ContainsKey($id)) {
        throw "Fehlende oder doppelte Eintrags-ID beim Zusammenfuehren: '$id'"
    }

    $ids[$id] = $true
    foreach ($xpath in @('./*[local-name()="Presentation"]/*[local-name()="PreviewImage"]/@location', './*[local-name()="ObjectData"]/*[local-name()="Filename"]', './*[local-name()="ObjectData"]/*[local-name()="Configuration"]')) {
        $reference = $entry.SelectSingleNode($xpath)
        $referenceValue = if ($null -ne $reference -and $reference.NodeType -eq [System.Xml.XmlNodeType]::Attribute) {
            [string]$reference.Value
        }
        elseif ($null -ne $reference) {
            [string]$reference.InnerText
        }
        else {
            $null
        }

        if ([string]::IsNullOrWhiteSpace($referenceValue)) {
            throw "Eintrag '$id' besitzt nicht alle erforderlichen Datei-Verweise."
        }

        $resolvedPath = Resolve-TestReference -Value $referenceValue -Variables $testVariables
        if ($resolvedPath -match '\$\{[^}]+\}') {
            throw "Eintrag '$id' enthaelt eine unbekannte NX-Variable: $referenceValue"
        }
        if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
            throw "Referenzierte Datei fuer Eintrag '$id' fehlt: $resolvedPath"
        }
    }

    $importedEntry = $systemDocument.ImportNode($entry, $true)
    [void]$systemDocument.DocumentElement.AppendChild($importedEntry)
}

$outputDirectory = Split-Path -Parent $outputFullPath
[void][System.IO.Directory]::CreateDirectory($outputDirectory)

$settings = New-Object System.Xml.XmlWriterSettings
$settings.Encoding = New-Object System.Text.UTF8Encoding($false)
$settings.Indent = $true
$settings.IndentChars = '  '
$settings.NewLineChars = "`r`n"
$settings.NewLineHandling = [System.Xml.NewLineHandling]::Replace

$writer = [System.Xml.XmlWriter]::Create($outputFullPath, $settings)
try {
    $systemDocument.Save($writer)
}
finally {
    $writer.Dispose()
}

$validationDocument = Read-PaletteXml -Path $outputFullPath
$validationEntries = @(Get-PaletteEntries -Document $validationDocument)
$expectedCount = $systemEntries.Count + $customEntries.Count
if ($validationEntries.Count -ne $expectedCount) {
    throw "Die erzeugte PAX enthaelt $($validationEntries.Count) statt $expectedCount Eintraege."
}

$hash = (Get-FileHash -LiteralPath $outputFullPath -Algorithm SHA256).Hash
Write-Host "[OK] NX2512-Standardvorlagen: $($systemEntries.Count)"
Write-Host "[OK] Migrierte Kundenmaschinen: $($customEntries.Count)"
Write-Host "[OK] Zusammengefuehrte PAX: $outputFullPath"
Write-Host "[OK] SHA-256: $hash"
