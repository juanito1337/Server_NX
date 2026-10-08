#requires -Version 5.1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'NXMigrationSafety.psm1') -Force
Push-Location (Join-Path $PSScriptRoot 'server')
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        $installExit = Invoke-NXExternalProcess -FilePath 'py.exe' -Arguments @('-3', '-m', 'venv', '.venv')
        if ($installExit -ne 0) { throw 'Python 3.10 oder neuer muss installiert sein.' }
    }
    $installExit = Invoke-NXExternalProcess -FilePath '.\.venv\Scripts\python.exe' -Arguments @('-m', 'pip', 'install', '-r', 'requirements-lock.txt')
    if ($installExit -ne 0) { throw 'Installation der Python-Abhaengigkeiten fehlgeschlagen.' }
    Write-Host 'Installation abgeschlossen. Start: Start-NX-Verwaltung.cmd'
}
finally { Pop-Location }
