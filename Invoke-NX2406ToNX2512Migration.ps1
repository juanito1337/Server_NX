#requires -Version 5.1

<#
.SYNOPSIS
Analyzes and builds an offline NX2512 staging tree from an adapted NX2406 tree.

.DESCRIPTION
This script performs a three-way migration:

  1. Siemens/SIDT standard NX2406
  2. Adapted NX2406 working copy
  3. Siemens/SIDT standard NX2512

It classifies every file in the adapted NX2406 tree. Safe custom additions and
safe custom modifications are included in the NX2512 copy plan. Files where
both the customer and Siemens changed the same path are never overwritten
automatically and are written to the manual-review report.

The default mode is Analyze. Build without -Apply only creates a copy plan.
Build with -Apply creates a new staging directory. Existing staging directories
are never merged or overwritten.

The production directory W:\SiemensShare\nx2406 and all paths on drive W: are
blocked by this script.
#>

[CmdletBinding()]
param(
    [ValidateSet('Analyze', 'Build')]
    [string]$Mode = 'Analyze',

    [string]$CustomizedNX2406Path,

    [string]$StandardNX2406Path = 'C:\Siemens\NX2406',

    [string]$StandardNX2512Path = 'C:\Siemens\NX2512',

    [string]$SIDTNX2406Path,

    [string]$SIDTNX2512Path,

    [string]$StagingPath,

    [string]$ReportPath,

    [string]$TargetNX2512SharePath = 'W:\SiemensShare\nx2512',

    [ValidateSet('KeepNX2512', 'CreateCandidate')]
    [string]$StandardDbAction = 'KeepNX2512',

    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force

$script:HashCache = @{}
$script:TextExtensions = @{
    '.bat' = $true; '.cmd' = $true; '.cfg' = $true; '.cdf' = $true
    '.csv' = $true; '.dat' = $true; '.def' = $true; '.dpv' = $true
    '.env' = $true; '.htm' = $true; '.html' = $true; '.ini' = $true
    '.json' = $true; '.log' = $true; '.men' = $true; '.pax' = $true
    '.properties' = $true; '.ps1' = $true; '.py' = $true; '.rtf' = $true
    '.std' = $true; '.css' = $true; '.js' = $true; '.sql' = $true
    '.tcl' = $true; '.txt' = $true; '.xml' = $true; '.xsl' = $true
    '.vb' = $true; '.vbs' = $true; '.yaml' = $true; '.yml' = $true
}
$script:CompatibilityExtensions = @{
    '.dll' = $true; '.dpx' = $true; '.exe' = $true; '.jar' = $true
    '.prt' = $true; '.pyc' = $true; '.so' = $true
}
$script:ToolDatabaseRelativePath = 'MACH\resource\library\tool\metric\tool_database.dat'
$script:StandardDbRelativePath = 'MACH\auxiliary\mfgam\xbps\data\standard_db.xml'
$script:CustomDirsRelativePath = 'MACH\resource\usertools\ugii\menus\custom_dirs.dat'

function Get-NormalizedComparisonPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $fullPath = [System.IO.Path]::GetFullPath($Path)
    }
    catch {
        $fullPath = $Path
    }

    return $fullPath.TrimEnd([char[]]'\/').ToUpperInvariant()
}

function Get-NormalizedWindowsSharePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $normalized = $Path.Replace('/', '\').TrimEnd([char]92)
    if ($normalized.StartsWith('\\')) {
        $remainder = [System.Text.RegularExpressions.Regex]::Replace(
            $normalized.Substring(2),
            '\\+',
            '\'
        )
        return '\\' + $remainder
    }
    return [System.Text.RegularExpressions.Regex]::Replace($normalized, '\\+', '\')
}

function Assert-SafePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$BlockWDrive,
        [switch]$ReferenceOnly
    )

    $candidate = Get-NormalizedComparisonPath -Path $Path
    $protected = Get-NormalizedComparisonPath -Path 'W:\SiemensShare\nx2406'
    $protectedShareFragment = '\SIEMENSSHARE\NX2406'

    if ($candidate -eq $protected -or
        $candidate.StartsWith("$protected\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidate.EndsWith($protectedShareFragment, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidate.IndexOf("$protectedShareFragment\", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        throw "$Label points to the protected production tree: W:\SiemensShare\nx2406"
    }

    if ($BlockWDrive -and ($candidate -eq 'W:' -or $candidate.StartsWith('W:\'))) {
        throw "$Label must not be located on drive W:. Use an offline staging or report path."
    }
    Assert-NXOfflinePath -Path $Path -Label $Label -ReferenceOnly:$ReferenceOnly
}

function Get-ExistingDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-SafePath -Path $Path -Label $Label
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) {
        throw "$Label is not a directory: $Path"
    }
    return $item.FullName
}

function Get-PathPrefix {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.EndsWith('\')) {
        return $Path
    }
    return "$Path\"
}

function Get-RelativePathFromRoot {
    param(
        [Parameter(Mandatory = $true)][string]$FullName,
        [Parameter(Mandatory = $true)][string]$RootPrefix
    )

    return $FullName.Substring($RootPrefix.Length).Replace('/', '\')
}

function Test-IsPermittedNXShareExtensionPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace('/', '\')
    if ($normalized -eq 'UGII\ugii_env.dat' -or
        $normalized -eq 'UGII\ugii_env_ug_nx2406.dat') {
        return $true
    }

    foreach ($prefix in @(
        'UGII\startup\',
        'UGII\application\',
        'UGII\udo\',
        'UGII\menus\',
        'UGII\udf\',
        'UGII\materials\',
        'NXPLOT\config\pm_server\',
        'MACH\custom\',
        'MACH\resource\',
        'CMM_INSPECTION\custom\',
        'CMM_INSPECTION\resource\'
    )) {
        if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    return $false
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path, [switch]$Fresh)

    $cacheKey = (Get-NormalizedComparisonPath -Path $Path)
    if (-not $Fresh -and $script:HashCache.ContainsKey($cacheKey)) {
        return $script:HashCache[$cacheKey]
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $stream = $null
    try {
        $stream = New-Object System.IO.FileStream(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $hashBytes = $sha256.ComputeHash($stream)
        $hash = ([System.BitConverter]::ToString($hashBytes)).Replace('-', '').ToLowerInvariant()
        $script:HashCache[$cacheKey] = $hash
        return $hash
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
        $sha256.Dispose()
    }
}

function Test-IsTextFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $extension = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
    if (-not $script:TextExtensions.ContainsKey($extension)) {
        return $false
    }

    $stream = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $buffer = New-Object byte[] 4096
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -ge 2) {
            if (($buffer[0] -eq 0xff -and $buffer[1] -eq 0xfe) -or
                ($buffer[0] -eq 0xfe -and $buffer[1] -eq 0xff)) {
                return $true
            }
        }

        for ($index = 0; $index -lt $read; $index++) {
            if ($buffer[$index] -eq 0) {
                return $false
            }
        }
        return $true
    }
    finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Test-ContainsNX2406Reference {
    param([Parameter(Mandatory = $true)][string]$Path)

    $file = Get-Item -LiteralPath $Path -Force
    if ($file.Length -gt 16777216) {
        return $false
    }

    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        $asciiText = [System.Text.Encoding]::ASCII.GetString($bytes)
        if ($asciiText.IndexOf('nx2406', [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }

        if (($bytes.Length % 2) -eq 0) {
            $unicodeText = [System.Text.Encoding]::Unicode.GetString($bytes)
            if ($unicodeText.IndexOf('nx2406', [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $true
            }
        }
    }
    catch {
        return $false
    }

    return $false
}

function Test-IsSiemensExecutable {
    param([Parameter(Mandatory = $true)][string]$Path)

    $extension = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
    if ($extension -ne '.dll' -and $extension -ne '.exe') {
        return $false
    }

    try {
        $versionInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
        return (-not [string]::IsNullOrWhiteSpace($versionInfo.CompanyName)) -and
            ($versionInfo.CompanyName -match '(?i)Siemens|UGS')
    }
    catch {
        return $false
    }
}

function Get-ByteArraySha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha256.ComputeHash($Bytes)
        return ([System.BitConverter]::ToString($hashBytes)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-NormalizedOfficePackageMap {
    param([Parameter(Mandatory = $true)][string]$Path)

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
    $result = @{}
    try {
        foreach ($entry in $archive.Entries) {
            if ([string]::IsNullOrEmpty($entry.Name)) {
                continue
            }

            $entryName = $entry.FullName.Replace('\', '/')
            if ($entryName.StartsWith('[trash]/', [System.StringComparison]::OrdinalIgnoreCase)) {
                continue
            }

            $entryStream = $null
            $memoryStream = $null
            try {
                $entryStream = $entry.Open()
                $memoryStream = New-Object System.IO.MemoryStream
                $entryStream.CopyTo($memoryStream)
                $bytes = $memoryStream.ToArray()
            }
            finally {
                if ($null -ne $memoryStream) {
                    $memoryStream.Dispose()
                }
                if ($null -ne $entryStream) {
                    $entryStream.Dispose()
                }
            }

            if ($entryName.StartsWith('customXml/', [System.StringComparison]::OrdinalIgnoreCase)) {
                $customXmlText = [System.Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xfeff)
                if ($entryName -match '(?i)^customXml/itemProps\d+\.xml$' -or
                    $entryName.IndexOf('/_rels/', [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $customXmlText -match '(?i)schemas\.microsoft\.com/office/(2006/metadata|infopath/2007/PartnerControls)' -or
                    $customXmlText -match '(?i)schemas\.microsoft\.com/sharepoint/v3/contenttype') {
                    continue
                }
                try {
                    [xml]$customXml = $customXmlText
                    $bytes = [System.Text.Encoding]::UTF8.GetBytes($customXml.OuterXml)
                }
                catch {
                    # Unknown non-XML payload remains part of the content comparison.
                }
            }

            if ($entryName -eq '[Content_Types].xml' -or $entryName.EndsWith('.rels', [System.StringComparison]::OrdinalIgnoreCase)) {
                try {
                    $xmlText = [System.Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xfeff)
                    [xml]$xml = $xmlText
                    if ($entryName -eq '[Content_Types].xml') {
                        $nodes = @($xml.SelectNodes("//*[local-name()='Override']"))
                        foreach ($node in $nodes) {
                            if ($node.PartName -like '/customXml/*') {
                                [void]$node.ParentNode.RemoveChild($node)
                            }
                        }
                    }
                    else {
                        $nodes = @($xml.SelectNodes("//*[local-name()='Relationship']"))
                        foreach ($node in $nodes) {
                            if ([string]$node.Target -match '(?i)customXml') {
                                [void]$node.ParentNode.RemoveChild($node)
                            }
                        }
                    }
                    $nodeSignatures = @($xml.DocumentElement.ChildNodes |
                        Where-Object { $_.NodeType -eq [System.Xml.XmlNodeType]::Element } |
                        ForEach-Object {
                            $node = $_
                            $attributeSignatures = @($node.Attributes |
                                ForEach-Object { "$($_.Name)=$($_.Value)" } |
                                Sort-Object)
                            "$($node.LocalName)|$($attributeSignatures -join '|')"
                        } |
                        Sort-Object)
                    $bytes = [System.Text.Encoding]::UTF8.GetBytes($nodeSignatures -join "`n")
                }
                catch {
                    # If a relationship part cannot be normalized, compare its raw bytes.
                }
            }

            $result[$entryName] = Get-ByteArraySha256 -Bytes $bytes
        }
    }
    finally {
        $archive.Dispose()
    }

    return ,$result
}

function Test-OfficeBusinessContentEqual {
    param(
        [Parameter(Mandatory = $true)][string]$FirstPath,
        [Parameter(Mandatory = $true)][string]$SecondPath
    )

    $supportedExtensions = @('.docx', '.docm', '.xlsx', '.xlsm', '.pptx', '.pptm')
    $firstExtension = [System.IO.Path]::GetExtension($FirstPath).ToLowerInvariant()
    $secondExtension = [System.IO.Path]::GetExtension($SecondPath).ToLowerInvariant()
    if ($firstExtension -ne $secondExtension -or $supportedExtensions -notcontains $firstExtension) {
        return $false
    }

    try {
        $firstMap = Get-NormalizedOfficePackageMap -Path $FirstPath
        $secondMap = Get-NormalizedOfficePackageMap -Path $SecondPath
        if ($firstMap.Count -ne $secondMap.Count) {
            return $false
        }
        foreach ($entryName in $firstMap.Keys) {
            if (-not $secondMap.ContainsKey($entryName) -or $firstMap[$entryName] -ne $secondMap[$entryName]) {
                return $false
            }
        }
        return $true
    }
    catch {
        return $false
    }
}

function Get-NXLogicalDatabaseLines {
    param([Parameter(Mandatory)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Lines)
    $logical = New-Object 'System.Collections.Generic.List[object]'
    $pending = $null
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        $text = $Lines[$i].Trim()
        if ($text -match '^(?i)(FORMAT|DATA)\s|^#CLASS\s|^#END_DATA\b') {
            if ($null -ne $pending) { $logical.Add($pending); $pending = $null }
            $pending = [pscustomobject]@{ Text = $text; LineNumber = $i + 1 }
        }
        elseif ($text.Length -eq 0 -or $text.StartsWith('#')) { continue }
        elseif ($null -ne $pending -and $pending.Text -match '^(?i)(FORMAT|DATA)\s') {
            $pending.Text += ' ' + $text
        }
        else { throw "Line $($i + 1): continuation without FORMAT or DATA." }
    }
    if ($null -ne $pending) { $logical.Add($pending) }
    return $logical.ToArray()
}

function Read-NXToolDatabase {
    param([Parameter(Mandatory = $true)][string]$Path)

    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $reader = New-Object System.IO.StreamReader($Path, $strictUtf8, $true)
    try { $lines = @($reader.ReadToEnd() -split '\r\n|\n|\r') }
    finally { $reader.Dispose() }
    $schemas = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([System.StringComparer]::Ordinal)
    $records = New-Object 'System.Collections.Generic.List[object]'
    $recordsByKey = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([System.StringComparer]::Ordinal)
    $errors = New-Object 'System.Collections.Generic.List[string]'
    $className = $null
    $fields = $null

    foreach ($logicalLine in @(Get-NXLogicalDatabaseLines -Lines $lines)) {
        $lineIndex = $logicalLine.LineNumber - 1
        $trimmed = $logicalLine.Text
        if ($trimmed -match '^(?i)#CLASS\s+(.+)$') {
            $className = $Matches[1].Trim()
            $fields = $null
            continue
        }
        if ($trimmed -match '^(?i)FORMAT\s+(.+)$') {
            if ([string]::IsNullOrWhiteSpace($className)) {
                $errors.Add("Line $($lineIndex + 1): FORMAT without #CLASS")
                continue
            }
            $fields = @($Matches[1].Trim() -split '\s+')
            $schemas[$className] = $fields
            continue
        }
        if ($trimmed -notmatch '^(?i)DATA\s') {
            continue
        }
        if ([string]::IsNullOrWhiteSpace($className) -or $null -eq $fields) {
            $errors.Add("Line $($lineIndex + 1): DATA without FORMAT")
            continue
        }

        $dataPosition = $trimmed.IndexOf('DATA', [System.StringComparison]::OrdinalIgnoreCase)
        $values = @($trimmed.Substring($dataPosition + 4).Split([char]'|') | ForEach-Object { $_.Trim() })
        if ($values.Count -gt 0 -and $values[0] -eq '') {
            $values = @($values[1..($values.Count - 1)])
        }
        while ($values.Count -gt $fields.Count -and $values[$values.Count - 1] -eq '') {
            $values = @($values[0..($values.Count - 2)])
        }
        if ($values.Count -ne $fields.Count) {
            $errors.Add("Line $($lineIndex + 1): class $className has $($fields.Count) fields but $($values.Count) values")
            continue
        }

        $fieldValues = @{}
        for ($fieldIndex = 0; $fieldIndex -lt $fields.Count; $fieldIndex++) {
            $fieldValues[$fields[$fieldIndex]] = $values[$fieldIndex]
        }
        $libraryReference = [string]$fieldValues['LIBRF']
        if ([string]::IsNullOrWhiteSpace($libraryReference)) {
            $errors.Add("Line $($lineIndex + 1): empty LIBRF in class $className")
            continue
        }
        # Use an ordinal dictionary: NX library identifiers are case-sensitive.
        $plainKey = "$className`0$libraryReference"
        $key = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($plainKey))
        $record = [pscustomobject]@{
            Key              = $key
            ClassName        = $className
            LibraryReference = $libraryReference
            FieldValues      = $fieldValues
            LineNumber       = $lineIndex + 1
        }
        $records.Add($record)
        if (-not $recordsByKey.ContainsKey($key)) {
            $recordsByKey[$key] = New-Object 'System.Collections.Generic.List[object]'
        }
        $recordsByKey[$key].Add($record)
    }

    return [pscustomobject]@{
        Path         = $Path
        Lines        = $lines
        Schemas      = $schemas
        Records      = $records
        RecordsByKey = $recordsByKey
        Errors       = $errors
    }
}

function Test-NXToolRecordEqual {
    param(
        [Parameter(Mandatory = $true)]$First,
        [Parameter(Mandatory = $true)]$Second
    )

    if ($First.FieldValues.Count -ne $Second.FieldValues.Count) {
        return $false
    }
    foreach ($fieldName in $First.FieldValues.Keys) {
        if (-not $Second.FieldValues.ContainsKey($fieldName) -or
            [string]$First.FieldValues[$fieldName] -cne [string]$Second.FieldValues[$fieldName]) {
            return $false
        }
    }
    return $true
}

function Convert-NXToolDatabase {
    param(
        [Parameter(Mandatory = $true)][string]$StandardNX2406File,
        [Parameter(Mandatory = $true)][string]$CustomizedNX2406File,
        [Parameter(Mandatory = $true)][string]$StandardNX2512File,
        [Parameter(Mandatory = $true)][string]$OutputFile
    )

    $oldDatabase = Read-NXToolDatabase -Path $StandardNX2406File
    $customDatabase = Read-NXToolDatabase -Path $CustomizedNX2406File
    $newDatabase = Read-NXToolDatabase -Path $StandardNX2512File
    foreach ($database in @($oldDatabase, $customDatabase, $newDatabase)) {
        if ($database.Errors.Count -gt 0) {
            throw "Tool database validation failed for $($database.Path): $($database.Errors[0])"
        }
    }

    foreach ($key in $oldDatabase.RecordsByKey.Keys) {
        if (-not $customDatabase.RecordsByKey.ContainsKey($key)) {
            throw "Customized tool database removed a standard record: $key"
        }
        $oldRecords = $oldDatabase.RecordsByKey[$key]
        $customRecords = $customDatabase.RecordsByKey[$key]
        if ($oldRecords.Count -ne $customRecords.Count) {
            throw "Customized tool database changed the occurrence count of a standard record: $key"
        }
        for ($recordIndex = 0; $recordIndex -lt $oldRecords.Count; $recordIndex++) {
            if (-not (Test-NXToolRecordEqual -First $oldRecords[$recordIndex] -Second $customRecords[$recordIndex])) {
                throw "Customized tool database modified a standard record: $key"
            }
        }
    }

    $recordsToAdd = New-Object 'System.Collections.Generic.List[object]'
    $seenAddedKeys = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([System.StringComparer]::Ordinal)
    $duplicateCount = 0
    foreach ($record in $customDatabase.Records) {
        if ($oldDatabase.RecordsByKey.ContainsKey($record.Key)) {
            continue
        }
        if ($seenAddedKeys.ContainsKey($record.Key)) {
            if (-not (Test-NXToolRecordEqual -First $seenAddedKeys[$record.Key] -Second $record)) {
                throw "Customized tool database contains different records with the same key: $($record.Key)"
            }
            $duplicateCount++
            continue
        }
        if ($newDatabase.RecordsByKey.ContainsKey($record.Key)) {
            throw "NX2512 already contains a customer tool key: $($record.Key)"
        }
        if (-not $newDatabase.Schemas.ContainsKey($record.ClassName)) {
            throw "NX2512 has no target class for customer tool: $($record.ClassName)"
        }
        foreach ($sourceField in $record.FieldValues.Keys) {
            if ($newDatabase.Schemas[$record.ClassName] -cnotcontains $sourceField) {
                throw "NX2512 class $($record.ClassName) has no field $sourceField"
            }
        }
        $seenAddedKeys[$record.Key] = $record
        $recordsToAdd.Add($record)
    }

    $recordsByClass = @{}
    foreach ($record in $recordsToAdd) {
        if (-not $recordsByClass.ContainsKey($record.ClassName)) {
            $recordsByClass[$record.ClassName] = New-Object 'System.Collections.Generic.List[object]'
        }
        $recordsByClass[$record.ClassName].Add($record)
    }

    $outputLines = New-Object 'System.Collections.Generic.List[string]'
    $currentClass = $null
    $insertedCount = 0
    foreach ($line in $newDatabase.Lines) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^(?i)#CLASS\s+(.+)$') {
            $currentClass = $Matches[1].Trim()
        }
        if ($trimmed -eq '#END_DATA' -and $null -ne $currentClass -and $recordsByClass.ContainsKey($currentClass)) {
            $outputLines.Add('# NX2406 customer tool records migrated to the NX2512 schema')
            $targetFields = $newDatabase.Schemas[$currentClass]
            foreach ($record in $recordsByClass[$currentClass]) {
                $targetValues = foreach ($targetField in $targetFields) {
                    if ($record.FieldValues.ContainsKey($targetField)) {
                        [string]$record.FieldValues[$targetField]
                    }
                    else {
                        ''
                    }
                }
                $outputLines.Add('DATA | ' + ($targetValues -join ' | '))
                $insertedCount++
            }
        }
        $outputLines.Add($line)
    }
    if ($insertedCount -ne $recordsToAdd.Count) {
        throw "Only $insertedCount of $($recordsToAdd.Count) customer tool records could be inserted"
    }

    $outputParent = Split-Path -Parent $OutputFile
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    $utf8WithBom = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllLines($OutputFile, $outputLines, $utf8WithBom)

    $convertedDatabase = Read-NXToolDatabase -Path $OutputFile
    if ($convertedDatabase.Errors.Count -gt 0 -or
        $convertedDatabase.Records.Count -ne ($newDatabase.Records.Count + $recordsToAdd.Count)) {
        throw 'Converted NX2512 tool database failed its post-conversion validation.'
    }

    return [pscustomobject]@{
        Converter             = 'NXToolDatabaseRecordMerge'
        OutputFile            = $OutputFile
        CustomerRecordCount   = $recordsToAdd.Count
        ExactDuplicatesOmitted = $duplicateCount
        NX2512RecordCount     = $newDatabase.Records.Count
        ResultRecordCount     = $convertedDatabase.Records.Count
        RequiresNXValidation  = $true
    }
}

function Convert-NXStandardDbCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$StandardNX2406File,
        [Parameter(Mandatory = $true)][string]$CustomizedNX2406File,
        [Parameter(Mandatory = $true)][string]$StandardNX2512File,
        [Parameter(Mandatory = $true)][string]$OutputFile
    )

    $oldText = [System.IO.File]::ReadAllText($StandardNX2406File)
    $customText = [System.IO.File]::ReadAllText($CustomizedNX2406File)
    $expectedCustomText = $oldText
    foreach ($mapping in @(
        @{ From = '<OutputFormat>[part]_p.cli</OutputFormat>'; To = '<OutputFormat>[part].cli</OutputFormat>' },
        @{ From = '<OutputFormat>[part]_supports_ctof.cli</OutputFormat>'; To = '<OutputFormat>[part]_supports_cs.cli</OutputFormat>' },
        @{ From = '<OutputFormat>[part]_supports_0i.cli</OutputFormat>'; To = '<OutputFormat>[part]_supports_0m.cli</OutputFormat>' }
    )) {
        $expectedCustomText = $expectedCustomText.Replace($mapping.From, $mapping.To)
    }
    if ($expectedCustomText -ne $customText) {
        throw 'The NX2406 standard_db.xml contains changes outside the validated output-name rules.'
    }

    $newText = [System.IO.File]::ReadAllText($StandardNX2512File)
    $candidateText = $newText
    $replacementCount = 0
    $migrationMappings = @(
        @{ From = '[part]_solidparts_p.cli'; To = '[part]_solidparts.cli' },
        @{ From = '[part]_sheetparts_p.cli'; To = '[part]_sheetparts.cli' },
        @{ From = '[part]_supports_ctof.cli'; To = '[part]_supports_cs.cli' },
        @{ From = '[part]_supports_0i.cli'; To = '[part]_supports_0m.cli' }
    )
    foreach ($mapping in $migrationMappings) {
        $fromElement = '<name>' + $mapping.From + '</name>'
        $toElement = '<name>' + $mapping.To + '</name>'
        $count = [System.Text.RegularExpressions.Regex]::Matches(
            $candidateText,
            [System.Text.RegularExpressions.Regex]::Escape($fromElement)
        ).Count
        if ($count -ne 2) {
            throw "Expected two NX2512 output profiles named $($mapping.From), found $count"
        }
        $candidateText = $candidateText.Replace($fromElement, $toElement)
        $replacementCount += $count
    }

    [xml]$candidateXml = $candidateText
    if ([string]$candidateXml.Data.PpXmlVer -ne '2512.0') {
        throw 'Converted standard_db.xml does not identify itself as NX2512.'
    }

    $outputParent = Split-Path -Parent $OutputFile
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($OutputFile, $candidateText, $utf8WithoutBom)

    return [pscustomobject]@{
        Converter            = 'NXStandardDbOutputNameMigration'
        OutputFile           = $OutputFile
        ReplacementCount     = $replacementCount
        MigrationMappings    = $migrationMappings
        RequiresNXValidation = $true
    }
}

function Convert-NXTextPathCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$InputFile,
        [Parameter(Mandatory = $true)][string]$OutputFile,
        [Parameter(Mandatory = $true)][string]$OldPath,
        [Parameter(Mandatory = $true)][string]$NewPath
    )

    $bytes = [System.IO.File]::ReadAllBytes($InputFile)
    foreach ($value in $bytes) {
        if ($value -gt 127) {
            throw "Path converter only accepts ASCII files: $InputFile"
        }
    }
    $text = [System.Text.Encoding]::ASCII.GetString($bytes)
    $pattern = [System.Text.RegularExpressions.Regex]::Escape($OldPath.TrimEnd([char[]]'\/'))
    $matches = [System.Text.RegularExpressions.Regex]::Matches(
        $text,
        $pattern,
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if ($matches.Count -eq 0) {
        throw "Expected path reference was not found in $InputFile"
    }
    $convertedText = [System.Text.RegularExpressions.Regex]::Replace(
        $text,
        $pattern,
        $NewPath.TrimEnd([char[]]'\/').Replace('$', '$$'),
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )

    $outputParent = Split-Path -Parent $OutputFile
    New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    [System.IO.File]::WriteAllBytes($OutputFile, [System.Text.Encoding]::ASCII.GetBytes($convertedText))

    return [pscustomobject]@{
        Converter            = 'NXTextPathMigration'
        OutputFile           = $OutputFile
        OldPath              = $OldPath
        NewPath              = $NewPath
        ReplacementCount     = $matches.Count
        RequiresNXValidation = $true
    }
}

function Get-StockFilePath {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$NXInstallRoot,
        [Parameter(Mandatory = $true)][string]$SIDTRoot
    )

    $ugiiPrefix = 'UGII\'
    if ($RelativePath.StartsWith($ugiiPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $child = $RelativePath.Substring($ugiiPrefix.Length)
        $sidtCandidate = Join-Path -Path (Join-Path $SIDTRoot 'applications\nx\ugii') -ChildPath $child
        if ([System.IO.File]::Exists($sidtCandidate)) {
            return $sidtCandidate
        }
    }

    $nxPlotPrefix = 'NXPLOT\config\pm_server\'
    if ($RelativePath.StartsWith($nxPlotPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $child = $RelativePath.Substring($nxPlotPrefix.Length)
        $sidtCandidate = Join-Path -Path (Join-Path $SIDTRoot 'applications\nx\nxplot') -ChildPath $child
        if ([System.IO.File]::Exists($sidtCandidate)) {
            return $sidtCandidate
        }
    }

    $nxCandidate = Join-Path -Path $NXInstallRoot -ChildPath $RelativePath
    if ([System.IO.File]::Exists($nxCandidate)) {
        return $nxCandidate
    }

    return $null
}

function Add-CopyPlanFile {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    $Plan[$RelativePath] = [pscustomobject]@{
        RelativePath = $RelativePath
        SourcePath   = $SourcePath
        Reason       = $Reason
        Size         = (Get-Item -LiteralPath $SourcePath -Force).Length
        Sha256       = Get-FileSha256 -Path $SourcePath
    }
}

function Add-CopyPlanTree {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$TargetRoot,
        [Parameter(Mandatory = $true)][string]$Reason
    )

    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw "Required source directory is missing: $SourceRoot"
    }

    $prefix = Get-PathPrefix -Path (Get-Item -LiteralPath $SourceRoot -Force).FullName
    foreach ($file in @(Get-ChildItem -LiteralPath $SourceRoot -File -Recurse -Force)) {
        $child = Get-RelativePathFromRoot -FullName $file.FullName -RootPrefix $prefix
        $relativePath = Join-Path -Path $TargetRoot -ChildPath $child
        Add-CopyPlanFile -Plan $Plan -RelativePath $relativePath -SourcePath $file.FullName -Reason $Reason
    }
}

function Add-NX2512BaselineToPlan {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Plan,
        [Parameter(Mandatory = $true)][string]$NXRoot,
        [Parameter(Mandatory = $true)][string]$SIDTRoot
    )

    foreach ($relativePath in @(
        'UGII\10.ugf',
        'UGII\ugcolor.cdf',
        'UGII\ugsmd_def.std',
        'UGII\thd_english.dat',
        'UGII\thd_metric.dat'
    )) {
        $sourcePath = Join-Path -Path $NXRoot -ChildPath $relativePath
        if (-not [System.IO.File]::Exists($sourcePath)) {
            throw "Required NX2512 source file is missing: $sourcePath"
        }
        Add-CopyPlanFile -Plan $Plan -RelativePath $relativePath -SourcePath $sourcePath -Reason 'NX2512Baseline'
    }

    foreach ($mapping in @(
        @{ Source = 'UGII\udf'; Target = 'UGII\udf' },
        @{ Source = 'UGII\materials'; Target = 'UGII\materials' },
        @{ Source = 'NXPLOT\config\pm_server'; Target = 'NXPLOT\config\pm_server' },
        @{ Source = 'MACH\custom'; Target = 'MACH\custom' },
        @{ Source = 'MACH\resource'; Target = 'MACH\resource' },
        @{ Source = 'CMM_INSPECTION\resource'; Target = 'CMM_INSPECTION\resource' }
    )) {
        Add-CopyPlanTree -Plan $Plan `
            -SourceRoot (Join-Path $NXRoot $mapping.Source) `
            -TargetRoot $mapping.Target `
            -Reason 'NX2512Baseline'
    }

    Add-CopyPlanTree -Plan $Plan `
        -SourceRoot (Join-Path $SIDTRoot 'applications\nx\nxplot') `
        -TargetRoot 'NXPLOT\config\pm_server' `
        -Reason 'SIDTNX2512Overlay'

    Add-CopyPlanTree -Plan $Plan `
        -SourceRoot (Join-Path $SIDTRoot 'applications\nx\ugii') `
        -TargetRoot 'UGII' `
        -Reason 'SIDTNX2512Overlay'
}

function Copy-PlanFile {
    param(
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    $parent = Split-Path -Parent $DestinationPath
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
}

try {
    if ([string]::IsNullOrWhiteSpace($CustomizedNX2406Path)) {
        $CustomizedNX2406Path = Join-Path $PSScriptRoot 'nx2406'
    }
    if ([string]::IsNullOrWhiteSpace($SIDTNX2406Path)) {
        $SIDTNX2406Path = Join-Path $PSScriptRoot 'installer_extracted\NX2406_V1.11\app\nx2406'
    }
    if ([string]::IsNullOrWhiteSpace($SIDTNX2512Path)) {
        $SIDTNX2512Path = Join-Path $PSScriptRoot 'installer_extracted\NX2512_V1.14\app\nx2512'
    }
    if ([string]::IsNullOrWhiteSpace($ReportPath)) {
        $timeStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $ReportPath = Join-Path $PSScriptRoot "migration_reports\$timeStamp"
    }

    $customRoot = Get-ExistingDirectory -Path $CustomizedNX2406Path -Label 'CustomizedNX2406Path'
    $oldRoot = Get-ExistingDirectory -Path $StandardNX2406Path -Label 'StandardNX2406Path'
    $newRoot = Get-ExistingDirectory -Path $StandardNX2512Path -Label 'StandardNX2512Path'
    $sidtOldRoot = Get-ExistingDirectory -Path $SIDTNX2406Path -Label 'SIDTNX2406Path'
    $sidtNewRoot = Get-ExistingDirectory -Path $SIDTNX2512Path -Label 'SIDTNX2512Path'

    if ([string]::IsNullOrWhiteSpace($TargetNX2512SharePath)) {
        throw 'TargetNX2512SharePath must not be empty.'
    }
    $targetSharePath = Get-NormalizedWindowsSharePath -Path $TargetNX2512SharePath
    Assert-SafePath -Path $targetSharePath -Label 'TargetNX2512SharePath' -ReferenceOnly

    Assert-SafePath -Path $ReportPath -Label 'ReportPath' -BlockWDrive
    $reportFullPath = [System.IO.Path]::GetFullPath($ReportPath)
    foreach ($sourcePath in @($customRoot, $oldRoot, $newRoot, $sidtOldRoot, $sidtNewRoot)) {
        Assert-NXDisjointPaths -First $reportFullPath -Second $sourcePath
    }
    if (Test-Path -LiteralPath $reportFullPath) {
        throw "ReportPath already exists. Use a new report directory to preserve audit evidence: $reportFullPath"
    }

    if ($Mode -eq 'Build') {
        if ([string]::IsNullOrWhiteSpace($StagingPath)) {
            throw 'StagingPath is required in Build mode.'
        }
        Assert-SafePath -Path $StagingPath -Label 'StagingPath' -BlockWDrive
        $stagingFullPath = [System.IO.Path]::GetFullPath($StagingPath)

        foreach ($sourcePath in @($customRoot, $oldRoot, $newRoot, $sidtOldRoot, $sidtNewRoot, $reportFullPath)) {
            $sourceCompare = Get-NormalizedComparisonPath -Path $sourcePath
            $stagingCompare = Get-NormalizedComparisonPath -Path $stagingFullPath
            if ($stagingCompare -eq $sourceCompare -or
                $stagingCompare.StartsWith("$sourceCompare\", [System.StringComparison]::OrdinalIgnoreCase) -or
                $sourceCompare.StartsWith("$stagingCompare\", [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'StagingPath must not overlap any source or report directory.'
            }
        }

        if ($Apply -and (Test-Path -LiteralPath $stagingFullPath)) {
            throw "StagingPath already exists. A migration build requires a new directory: $stagingFullPath"
        }
    }
    elseif ($Apply) {
        throw '-Apply is only valid in Build mode.'
    }

    foreach ($sourcePath in @($customRoot, $oldRoot, $newRoot, $sidtOldRoot, $sidtNewRoot)) {
        Assert-NXTreeNoReparsePoint -Root $sourcePath
    }
    New-Item -ItemType Directory -Path $reportFullPath -ErrorAction Stop | Out-Null

    $copyPlan = @{}
    Add-NX2512BaselineToPlan -Plan $copyPlan -NXRoot $newRoot -SIDTRoot $sidtNewRoot
    # Optional legacy palette: malformed closing root before a second entry.
    # User chose the standard NX layout; omit it only from NEW builds.
    $optionalLegacyPalette = 'MACH\resource\dmg_templates.pax'
    $copyPlan.Remove($optionalLegacyPalette)

    # Build the exact file set created by the NX2406 after_install.bat.  The
    # customized input can also be a copy of a complete NX installation.  In
    # that case local program files outside the share scope must not be treated
    # as customer additions.
    $oldAfterInstallPlan = @{}
    Add-NX2512BaselineToPlan -Plan $oldAfterInstallPlan -NXRoot $oldRoot -SIDTRoot $sidtOldRoot

    $customPrefix = Get-PathPrefix -Path $customRoot
    $allCustomFiles = @(Get-ChildItem -LiteralPath $customRoot -File -Recurse -Force)
    $customFileList = New-Object 'System.Collections.Generic.List[object]'
    $ignoredInputRecords = New-Object 'System.Collections.Generic.List[object]'

    foreach ($candidateFile in $allCustomFiles) {
        $candidateRelativePath = Get-RelativePathFromRoot -FullName $candidateFile.FullName -RootPrefix $customPrefix
        if ($candidateRelativePath -eq $optionalLegacyPalette) {
            $ignoredInputRecords.Add([pscustomobject]@{
                RelativePath = $candidateRelativePath
                SourcePath   = $candidateFile.FullName
                Reason       = 'OptionalLegacyDMGPaletteOmittedByUserDecision'
                Size         = $candidateFile.Length
            })
            continue
        }
        if ($oldAfterInstallPlan.ContainsKey($candidateRelativePath)) {
            $customFileList.Add($candidateFile)
            continue
        }

        if (-not (Test-IsPermittedNXShareExtensionPath -RelativePath $candidateRelativePath)) {
            $ignoredInputRecords.Add([pscustomobject]@{
                RelativePath = $candidateRelativePath
                SourcePath   = $candidateFile.FullName
                Reason       = 'OutsideAfterInstallShareScope'
                Size         = $candidateFile.Length
            })
            continue
        }

        # A complete NX installation contains many stock files below UGII
        # menus/startup that after_install.bat never places on the share.  Keep
        # only a real modification or addition at such an extension path.
        $candidateStockPath = Get-StockFilePath `
            -RelativePath $candidateRelativePath `
            -NXInstallRoot $oldRoot `
            -SIDTRoot $sidtOldRoot
        if ($null -ne $candidateStockPath) {
            $candidateStockFile = Get-Item -LiteralPath $candidateStockPath -Force
            if ($candidateFile.Length -eq $candidateStockFile.Length -and
                (Get-FileSha256 -Path $candidateFile.FullName) -eq (Get-FileSha256 -Path $candidateStockPath)) {
                $ignoredInputRecords.Add([pscustomobject]@{
                    RelativePath = $candidateRelativePath
                    SourcePath   = $candidateFile.FullName
                    Reason       = 'InstalledStandardOutsideAfterInstallScope'
                    Size         = $candidateFile.Length
                })
                continue
            }
        }

        $customFileList.Add($candidateFile)
    }
    $customFiles = @($customFileList.ToArray())
    $records = New-Object 'System.Collections.Generic.List[object]'
    $versionReferenceRecords = New-Object 'System.Collections.Generic.List[object]'
    $conversionRecords = New-Object 'System.Collections.Generic.List[object]'
    $convertedFilesRoot = Join-Path $reportFullPath 'converted_files'

    for ($index = 0; $index -lt $customFiles.Count; $index++) {
        $customFile = $customFiles[$index]
        $relativePath = Get-RelativePathFromRoot -FullName $customFile.FullName -RootPrefix $customPrefix
        $oldBasePath = Get-StockFilePath -RelativePath $relativePath -NXInstallRoot $oldRoot -SIDTRoot $sidtOldRoot
        $newBasePath = Get-StockFilePath -RelativePath $relativePath -NXInstallRoot $newRoot -SIDTRoot $sidtNewRoot
        $isText = Test-IsTextFile -Path $customFile.FullName
        $extension = [System.IO.Path]::GetExtension($relativePath).ToLowerInvariant()
        $needsCompatibilityCheck = $script:CompatibilityExtensions.ContainsKey($extension)

        $classification = $null
        $recommendedAction = $null
        $customHash = $null
        $oldBaseHash = $null
        $newBaseHash = $null

        if ($null -eq $oldBasePath) {
            if ($null -eq $newBasePath) {
                if ($relativePath -eq 'UGII\ugii_env_ug_nx2406.dat') {
                    $unversionedOldFile = Join-Path $oldRoot 'UGII\ugii_env_ug.dat'
                    if ([System.IO.File]::Exists($unversionedOldFile) -and
                        $customFile.Length -eq (Get-Item -LiteralPath $unversionedOldFile -Force).Length -and
                        (Get-FileSha256 -Path $customFile.FullName) -eq (Get-FileSha256 -Path $unversionedOldFile)) {
                        $classification = 'NX2406StandardSnapshotSkipped'
                        $recommendedAction = 'SkipUnreferencedNX2406StandardSnapshot'
                        $customHash = Get-FileSha256 -Path $customFile.FullName
                        $oldBaseHash = Get-FileSha256 -Path $unversionedOldFile
                    }
                    else {
                        $classification = 'CustomAdded'
                        $recommendedAction = if ($needsCompatibilityCheck) { 'CopyCustomAndValidateCompatibility' } else { 'CopyCustom' }
                        Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $customFile.FullName -Reason $classification
                    }
                }
                elseif ($relativePath -eq $script:CustomDirsRelativePath) {
                    $convertedPath = Join-Path $convertedFilesRoot $relativePath
                    try {
                        $conversion = Convert-NXTextPathCandidate `
                            -InputFile $customFile.FullName `
                            -OutputFile $convertedPath `
                            -OldPath 'W:\SiemensShare\nx2406' `
                            -NewPath $targetSharePath
                        $conversionRecords.Add($conversion)
                        $classification = 'CustomConvertedPathReference'
                        $recommendedAction = 'UseConvertedCandidateAndValidatePath'
                        Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $convertedPath -Reason $classification
                    }
                    catch {
                        $conversionRecords.Add([pscustomobject]@{
                            Converter = 'NXTextPathMigration'
                            OutputFile = $convertedPath
                            Error = $_.Exception.Message
                            RequiresNXValidation = $true
                        })
                        $classification = 'CustomLegacyText'
                        $recommendedAction = 'ManualPathMigration'
                    }
                }
                else {
                    $classification = 'CustomAdded'
                    $recommendedAction = if ($needsCompatibilityCheck) { 'CopyCustomAndValidateCompatibility' } else { 'CopyCustom' }
                    Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $customFile.FullName -Reason $classification
                }
            }
            else {
                $classification = if ($isText) { 'CustomCollisionWithNewText' } else { 'CustomCollisionWithNewBinary' }
                $recommendedAction = if ($isText) { 'ManualMerge' } else { 'ManualCompatibilityReview' }
                Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason 'NX2512WinsPendingReview'
            }
        }
        else {
            $oldBaseFile = Get-Item -LiteralPath $oldBasePath -Force
            if ($customFile.Length -eq $oldBaseFile.Length) {
                $customHash = Get-FileSha256 -Path $customFile.FullName
                $oldBaseHash = Get-FileSha256 -Path $oldBasePath
            }

            $matchesOldBase = ($null -ne $customHash) -and ($customHash -eq $oldBaseHash)
            if ($matchesOldBase) {
                if ($null -eq $newBasePath) {
                    $classification = 'StandardRemovedInNX2512'
                    $recommendedAction = 'SkipRemovedStandardFile'
                }
                else {
                    $classification = 'StandardUseNX2512'
                    $recommendedAction = 'UseNX2512'
                    Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason $classification
                }
            }
            else {
                if ($null -eq $customHash) {
                    $customHash = Get-FileSha256 -Path $customFile.FullName
                }
                if ($null -eq $oldBaseHash) {
                    $oldBaseHash = Get-FileSha256 -Path $oldBasePath
                }

                if ($null -eq $newBasePath) {
                    if (Test-OfficeBusinessContentEqual -FirstPath $oldBasePath -SecondPath $customFile.FullName) {
                        $classification = 'OfficeMetadataOnlyRemovedInNX2512'
                        $recommendedAction = 'SkipRemovedStandardOfficeFile'
                    }
                    elseif ((Test-IsSiemensExecutable -Path $customFile.FullName) -and
                        (Test-IsSiemensExecutable -Path $oldBasePath)) {
                        $classification = 'SiemensBinaryRemovedInNX2512'
                        $recommendedAction = 'SkipRemovedSiemensBinary'
                    }
                    else {
                        $classification = if ($isText) { 'CustomLegacyText' } else { 'CustomLegacyBinary' }
                        $recommendedAction = if ($isText) { 'ManualMigrationReview' } else { 'ManualCompatibilityReview' }
                    }
                }
                else {
                    $newBaseHash = Get-FileSha256 -Path $newBasePath
                    if ($customHash -eq $newBaseHash) {
                        $classification = 'CustomAlreadyMatchesNX2512'
                        $recommendedAction = 'UseNX2512'
                        Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason $classification
                    }
                    elseif ($oldBaseHash -eq $newBaseHash) {
                        $classification = 'CustomModifiedSafe'
                        $recommendedAction = if ($needsCompatibilityCheck) { 'CopyCustomAndValidateCompatibility' } else { 'CopyCustom' }
                        Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $customFile.FullName -Reason $classification
                    }
                    elseif (Test-OfficeBusinessContentEqual -FirstPath $oldBasePath -SecondPath $customFile.FullName) {
                        $classification = 'OfficeMetadataOnlyUseNX2512'
                        $recommendedAction = 'UseNX2512'
                        Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason $classification
                    }
                    elseif ($relativePath -eq $script:ToolDatabaseRelativePath) {
                        $convertedPath = Join-Path $convertedFilesRoot $relativePath
                        try {
                            $conversion = Convert-NXToolDatabase `
                                -StandardNX2406File $oldBasePath `
                                -CustomizedNX2406File $customFile.FullName `
                                -StandardNX2512File $newBasePath `
                                -OutputFile $convertedPath
                            $conversionRecords.Add($conversion)
                            $classification = 'CustomConvertedToolDatabase'
                            $recommendedAction = 'UseConvertedCandidateAndValidateInNX2512'
                            Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $convertedPath -Reason $classification
                        }
                        catch {
                            $conversionRecords.Add([pscustomobject]@{
                                Converter = 'NXToolDatabaseRecordMerge'
                                OutputFile = $convertedPath
                                Error = $_.Exception.Message
                                RequiresNXValidation = $true
                            })
                            $classification = 'CustomConflictText'
                            $recommendedAction = 'ManualThreeWayMerge'
                            Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason 'NX2512WinsPendingReview'
                        }
                    }
                    elseif ($relativePath -eq $script:StandardDbRelativePath) {
                        if ($StandardDbAction -eq 'KeepNX2512') {
                            $classification = 'CustomStandardDbUseNX2512ByDecision'
                            $recommendedAction = 'UseNX2512OriginalByApprovedDecision'
                            Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason $classification
                        }
                        else {
                            $convertedPath = Join-Path $convertedFilesRoot $relativePath
                            try {
                                $conversion = Convert-NXStandardDbCandidate `
                                    -StandardNX2406File $oldBasePath `
                                    -CustomizedNX2406File $customFile.FullName `
                                    -StandardNX2512File $newBasePath `
                                    -OutputFile $convertedPath
                                $conversionRecords.Add($conversion)
                                $classification = 'CustomConvertedStandardDbCandidate'
                                $recommendedAction = 'ManualValidateConvertedXML'
                                Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $convertedPath -Reason $classification
                            }
                            catch {
                                $conversionRecords.Add([pscustomobject]@{
                                    Converter = 'NXStandardDbOutputNameMigration'
                                    OutputFile = $convertedPath
                                    Error = $_.Exception.Message
                                    RequiresNXValidation = $true
                                })
                                $classification = 'CustomConflictText'
                                $recommendedAction = 'ManualThreeWayMerge'
                                Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason 'NX2512WinsPendingReview'
                            }
                        }
                    }
                    elseif ((Test-IsSiemensExecutable -Path $customFile.FullName) -and
                        (Test-IsSiemensExecutable -Path $oldBasePath) -and
                        (Test-IsSiemensExecutable -Path $newBasePath)) {
                        $classification = 'SiemensBinaryUseNX2512'
                        $recommendedAction = 'UseNX2512'
                        Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason $classification
                    }
                    else {
                        $classification = if ($isText) { 'CustomConflictText' } else { 'CustomConflictBinary' }
                        $recommendedAction = if ($isText) { 'ManualThreeWayMerge' } else { 'ManualCompatibilityReview' }
                        Add-CopyPlanFile -Plan $copyPlan -RelativePath $relativePath -SourcePath $newBasePath -Reason 'NX2512WinsPendingReview'
                    }
                }
            }
        }

        $containsNX2406Reference = $false
        $containsNX2406InPath = $relativePath.IndexOf('nx2406', [System.StringComparison]::OrdinalIgnoreCase) -ge 0
        if (($classification -eq 'CustomAdded' -or $classification -eq 'CustomModifiedSafe') -and $isText) {
            $containsNX2406Reference = Test-ContainsNX2406Reference -Path $customFile.FullName
        }
        if (($classification -eq 'CustomAdded' -or $classification -eq 'CustomModifiedSafe') -and
            ($containsNX2406Reference -or $containsNX2406InPath)) {
            $referenceLocation = if ($containsNX2406Reference -and $containsNX2406InPath) {
                'PathAndContent'
            }
            elseif ($containsNX2406InPath) {
                'Path'
            }
            else {
                'Content'
            }
            $versionReferenceRecords.Add([pscustomobject]@{
                RelativePath     = $relativePath
                SourcePath       = $customFile.FullName
                ReferenceLocation = $referenceLocation
                RequiredWork     = 'Review and replace path references from nx2406 to nx2512 where semantically correct'
            })
        }

        $records.Add([pscustomobject]@{
            RelativePath             = $relativePath
            Classification           = $classification
            RecommendedAction        = $recommendedAction
            FileType                 = if ($isText) { 'Text' } else { 'Binary' }
            Extension                = $extension
            NeedsCompatibilityCheck  = $needsCompatibilityCheck
            ContainsNX2406Reference  = $containsNX2406Reference
            ContainsNX2406InPath     = $containsNX2406InPath
            CustomizedSize           = $customFile.Length
            CustomizedPath           = $customFile.FullName
            StandardNX2406Path        = $oldBasePath
            StandardNX2512Path        = $newBasePath
            CustomizedSha256         = $customHash
            StandardNX2406Sha256      = $oldBaseHash
            StandardNX2512Sha256      = $newBaseHash
        })

        if (($index % 100) -eq 0) {
            $percent = if ($customFiles.Count -eq 0) { 100 } else { [int](100 * ($index + 1) / $customFiles.Count) }
            Write-Progress -Activity 'Analyzing NX2406 customizations' -Status "$($index + 1) of $($customFiles.Count) files" -PercentComplete $percent
        }
    }
    Write-Progress -Activity 'Analyzing NX2406 customizations' -Completed

    $inventoryPath = Join-Path $reportFullPath 'migration_inventory.csv'
    $records | Sort-Object RelativePath | Export-Csv -LiteralPath $inventoryPath -Delimiter ';' -NoTypeInformation -Encoding UTF8

    $ignoredInputPath = Join-Path $reportFullPath 'ignored_outside_after_install_scope.csv'
    $ignoredInputRecords | Sort-Object RelativePath | Export-Csv -LiteralPath $ignoredInputPath -Delimiter ';' -NoTypeInformation -Encoding UTF8

    $manualReviewRecords = @($records | Where-Object {
        $_.RecommendedAction -like 'Manual*'
    })
    $manualReviewPath = Join-Path $reportFullPath 'manual_review.csv'
    $compatibilityReviewRecords = @($records | Where-Object { $_.RecommendedAction -like '*Validate*' })
    $compatibilityReviewRecords | Sort-Object RelativePath |
        Export-Csv -LiteralPath (Join-Path $reportFullPath 'compatibility_review.csv') -Delimiter ';' -NoTypeInformation -Encoding UTF8
    if ($manualReviewRecords.Count -eq 0) {
        $manualHeader = @($records[0].PSObject.Properties.Name |
            ForEach-Object { '"' + $_.Replace('"', '""') + '"' }) -join ';'
        $manualHeader | Set-Content -LiteralPath $manualReviewPath -Encoding UTF8
    }
    else {
        $manualReviewRecords | Sort-Object RelativePath | Export-Csv -LiteralPath $manualReviewPath -Delimiter ';' -NoTypeInformation -Encoding UTF8
    }

    $versionReferencesPath = Join-Path $reportFullPath 'nx2406_path_references.csv'
    $versionReferenceRecords | Sort-Object RelativePath | Export-Csv -LiteralPath $versionReferencesPath -Delimiter ';' -NoTypeInformation -Encoding UTF8

    $conversionReportPath = Join-Path $reportFullPath 'conversion_details.json'
    $conversionRecords.ToArray() | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $conversionReportPath -Encoding UTF8

    $copyPlanRecords = @($copyPlan.Values | Sort-Object RelativePath)
    $copyPlanPath = Join-Path $reportFullPath 'copy_plan.csv'
    $copyPlanRecords | Export-Csv -LiteralPath $copyPlanPath -Delimiter ';' -NoTypeInformation -Encoding UTF8

    $classificationSummary = @($records |
        Group-Object Classification |
        Sort-Object Name |
        ForEach-Object {
            [pscustomobject]@{ Classification = $_.Name; Count = $_.Count }
        })

    $copyPlanBytes = [long]0
    foreach ($planItem in $copyPlanRecords) {
        $copyPlanBytes += $planItem.Size
    }

    $summary = [ordered]@{
        GeneratedAt                 = (Get-Date).ToString('o')
        Mode                        = $Mode
        Apply                       = [bool]$Apply
        CustomizedNX2406Path        = $customRoot
        StandardNX2406Path          = $oldRoot
        StandardNX2512Path          = $newRoot
        TargetNX2512SharePath       = $targetSharePath
        StandardDbAction            = $StandardDbAction
        CustomizedInputFileCount    = $allCustomFiles.Count
        InScopeCustomizedFileCount  = $customFiles.Count
        IgnoredOutOfScopeFileCount  = $ignoredInputRecords.Count
        CustomizedFileCount         = $customFiles.Count
        PlannedNX2512FileCount       = $copyPlanRecords.Count
        PlannedNX2512Bytes           = $copyPlanBytes
        ManualReviewCount            = $manualReviewRecords.Count
        CompatibilityReviewCount     = $compatibilityReviewRecords.Count
        ReleaseStatus                = 'PendingNXFunctionalValidation'
        BuildCompleted               = $false
        NX2406PathReferenceCount     = $versionReferenceRecords.Count
        ConversionCount              = $conversionRecords.Count
        Classifications              = $classificationSummary
    }
    if ($Mode -eq 'Build') {
        $summary['StagingPath'] = $stagingFullPath
    }

    $summaryPath = Join-Path $reportFullPath 'migration_summary.json'
    $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8

    if ($Mode -eq 'Build' -and $Apply) {
        if ($manualReviewRecords.Count -gt 0) {
            throw 'Build blocked: resolve manual-review conflicts before creating a staging tree.'
        }
        New-Item -ItemType Directory -Path $stagingFullPath -Force | Out-Null
        for ($index = 0; $index -lt $copyPlanRecords.Count; $index++) {
            $planItem = $copyPlanRecords[$index]
            $destinationFile = Join-Path -Path $stagingFullPath -ChildPath $planItem.RelativePath
            if ((Get-FileSha256 -Path $planItem.SourcePath -Fresh) -cne $planItem.Sha256) {
                throw "Source changed after planning: $($planItem.RelativePath)"
            }
            Copy-PlanFile -SourcePath $planItem.SourcePath -DestinationPath $destinationFile
            if ((Get-FileSha256 -Path $destinationFile -Fresh) -cne $planItem.Sha256) {
                throw "Post-copy SHA-256 verification failed: $($planItem.RelativePath)"
            }

            if (($index % 50) -eq 0) {
                $percent = if ($copyPlanRecords.Count -eq 0) { 100 } else { [int](100 * ($index + 1) / $copyPlanRecords.Count) }
                Write-Progress -Activity 'Building NX2512 staging tree' -Status "$($index + 1) of $($copyPlanRecords.Count) files" -PercentComplete $percent
            }
        }
        Write-Progress -Activity 'Building NX2512 staging tree' -Completed

        foreach ($requiredDirectory in @('CMM_INSPECTION\custom', 'UGII')) {
            $directoryPath = Join-Path -Path $stagingFullPath -ChildPath $requiredDirectory
            if (-not (Test-Path -LiteralPath $directoryPath -PathType Container)) {
                New-Item -ItemType Directory -Path $directoryPath -Force | Out-Null
            }
        }

        $buildMarker = Join-Path $stagingFullPath '_NX2512_MIGRATION_STAGING.txt'
        @(
            'This is an offline NX2512 migration staging tree.',
            "Generated: $((Get-Date).ToString('o'))",
            "Report: $reportFullPath",
            "Manual review items: $($manualReviewRecords.Count)",
            'Do not deploy until all manual-review items have been resolved and NX2512 validation has passed.'
        ) | Set-Content -LiteralPath $buildMarker -Encoding UTF8
        $manifest = [ordered]@{
            SchemaVersion = 1
            NXVersion = '2512'
            Files = @($copyPlanRecords | Select-Object RelativePath, Size, Sha256)
        }
        $manifest | ConvertTo-Json -Depth 5 |
            Set-Content -LiteralPath (Join-Path $reportFullPath 'release_manifest.json') -Encoding UTF8
        $summary['BuildCompleted'] = $true
        $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
        [ordered]@{ PlannedFiles = $copyPlanRecords.Count; MissingFiles = 0; Sha256Mismatches = 0; ValidationPassed = $true } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $reportFullPath 'staging_validation.json') -Encoding UTF8
    }

    Write-Host ''
    Write-Host 'NX2406 -> NX2512 migration analysis completed.'
    Write-Host "  NX2406 input files:       $($allCustomFiles.Count)"
    Write-Host "  In-scope share files:     $($customFiles.Count)"
    Write-Host "  Ignored outside scope:    $($ignoredInputRecords.Count)"
    Write-Host "  Planned NX2512 files:    $($copyPlanRecords.Count)"
    Write-Host "  Planned size:            $copyPlanBytes bytes"
    Write-Host "  Manual review items:     $($manualReviewRecords.Count)"
    Write-Host "  nx2406 path references:  $($versionReferenceRecords.Count)"
    Write-Host "  Reports:                 $reportFullPath"

    foreach ($group in $classificationSummary) {
        Write-Host ("  {0,-32} {1,8}" -f $group.Classification, $group.Count)
    }

    if ($Mode -eq 'Build' -and -not $Apply) {
        Write-Host ''
        Write-Host 'Preview only. Add -Apply to create a new staging directory after reviewing the reports.'
    }
    elseif ($Mode -eq 'Build') {
        Write-Host "  Staging created:         $stagingFullPath"
    }

    exit 0
}
catch {
    Write-Error ("{0}`n{1}" -f $_.Exception.Message, $_.ScriptStackTrace)
    exit 1
}
