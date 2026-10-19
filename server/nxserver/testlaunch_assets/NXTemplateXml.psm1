#requires -Version 5.1
Set-StrictMode -Version Latest
function Read-NXPaletteDocument {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)
    Assert-NXOfflinePath -Path $Path
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "PAX-Datei fehlt: $Path" }
    if ((Get-Item -LiteralPath $Path -Force).Length -gt 20MB) { throw 'PAX exceeds the supported 20 MiB reader.' }
    $settings=New-Object System.Xml.XmlReaderSettings
    $settings.DtdProcessing=[System.Xml.DtdProcessing]::Prohibit
    $settings.XmlResolver=$null
    $settings.MaxCharactersInDocument=20MB
    $settings.MaxCharactersFromEntities=1024
    $reader=[System.Xml.XmlReader]::Create($Path,$settings)
    try {
        $document=New-Object System.Xml.XmlDocument
        $document.XmlResolver=$null
        $document.PreserveWhitespace=$false
        $document.Load($reader)
    }
    finally { $reader.Dispose() }
    if ($document.DocumentElement.LocalName -cne 'Palette') { throw 'Unexpected PAX root element; expected Palette.' }
    return $document
}
Export-ModuleMember -Function Read-NXPaletteDocument
