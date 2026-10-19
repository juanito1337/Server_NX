"""Generate self-contained, release-bound Windows test packages on any server OS."""
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PureWindowsPath

from .safety import DomainError

ASSETS = ('NXOfflineSafety.psm1', 'NXUIProfile.psm1', 'NXTemplateXml.psm1', 'New-NX2512TestPalette.ps1',
          'New-NX2512TestEnvironment.ps1', 'Start-NX2512ManagedTest.ps1', 'Invoke-NXReleaseTest.ps1')
STARTER = (
    '@echo off\r\nsetlocal\r\n'
    'echo NX2512-Test: Pruefung kann mehrere Minuten dauern. Fenster bitte offen lassen.\r\n'
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Invoke-NXReleaseTest.ps1" '
    '-PackagePath "%~dp0release_test.json" %*\r\n'
    'set "NX_TEST_EXIT=%ERRORLEVEL%"\r\n'
    'if not "%NX_TEST_EXIT%"=="0" (echo NX-Test abgebrochen. Fehler oben pruefen.)\r\n'
    'pause\r\nexit /b %NX_TEST_EXIT%\r\n').encode('ascii')


def template_sha256():
    """Fingerprint executable templates to refresh packages without rewriting old artifacts."""
    directory = Path(__file__).parent / 'testlaunch_assets'
    files = {name: (directory / name).read_bytes() for name in ASSETS}
    files['Start-NX2512-Test.cmd'] = STARTER
    return content_revision(files)


def content_revision(files):
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode('utf-8')).hexdigest()


def windows_reference(value):
    if value is None:
        return None
    path = PureWindowsPath(value)
    if (not path.is_absolute() or not re.fullmatch(r'(?i)[a-vx-z]:', path.drive) or
            any(part in ('.', '..') for part in path.parts) or
            any(ord(c) < 32 for c in str(path)) or 'siemensshare' in str(path).lower()):
        raise DomainError('The test launcher needs a local Windows path, never W: or a network share.')
    return str(path)


def package_files(release_id, manifest, windows_folder, ui_profile=None):
    from .launchprofiles import validate_profile
    ui_profile=validate_profile(ui_profile,manifest)
    windows_folder = windows_reference(windows_folder)
    directory = Path(__file__).parent / 'testlaunch_assets'
    files = {name: (directory / name).read_bytes() for name in ASSETS}
    files['Start-NX2512-Test.cmd'] = STARTER
    expected = {'SchemaVersion': 1, 'IncludeStagingMarker': True,
                'Files': [{'RelativePath': row['path'].replace('/', '\\'),
                           'Size': row['size'], 'Sha256': row['sha256']} for row in manifest]}
    files['release_manifest.json'] = json.dumps(expected, ensure_ascii=True, indent=2).encode('utf-8')
    config = {'SchemaVersion': 1, 'ReleaseId': release_id, 'NxVersion': '2512',
              'StagingRoot': windows_folder, 'NXInstallRoot': r'C:\Siemens\NX2512',
              'TestRoot': str(PureWindowsPath(r'C:\NX_Migration\TestRuns') / release_id.replace('-', '')[:12]),
              'ManifestSha256': hashlib.sha256(files['release_manifest.json']).hexdigest(),
              'ScriptHashes': {name: hashlib.sha256(data).hexdigest()
                               for name, data in files.items() if name != 'release_manifest.json'}}
    if ui_profile is not None:
        role_hash=hashlib.sha256(json.dumps(ui_profile,sort_keys=True).encode('utf-8')).hexdigest()
        config['UIProfile']=ui_profile
        config['TestRoot']+='-ui-'+role_hash[:12]
    files['release_test.json'] = json.dumps(config, ensure_ascii=True, indent=2).encode('utf-8')
    files['LIESMICH.txt'] = (f'''NX2512-Teststarter - Datenstand {release_id}

1. Gesamtes ZIP in einen neuen lokalen Ordner entpacken, NICHT in den NX-Datenordner.
2. Alle bisherigen NX-Test-/Produktivsitzungen schliessen. Lizenz muss verfuegbar sein.
3. Start-NX2512-Test.cmd doppelklicken. Der Starter benutzt exakt den unten genannten
   Datenstand, prueft alle Dateien per SHA-256 und erstellt eine separate Testumgebung.
   Keine Server-Schluessel im Paket. Kein automatischer NX-Start durch den Server.

NX-Installation: {config['NXInstallRoot']} (lokale Windows-Installation)
NX-Datenordner: {windows_folder or 'Noch nicht zugeordnet: lokale Windows-Kopie mit identischen Pruefsummen angeben.'}
Separate Testumgebung: {config['TestRoot']}
Ausdruecklich gewaehlte UI-Rolle: {ui_profile['path'] if ui_profile else 'Keine; unveraenderter isolierter Teststandard.'}
NX-Systemprotokolle: {config['TestRoot']}\\temp
Startprotokoll: {config['TestRoot']}\\temp\\nx-test-launcher.log
Die Pruefung zeigt Fortschritt; bei vielen Dateien dauert sie mehrere Minuten.
Erst danach wird der NX-Prozess gestartet und seine Prozessnummer angezeigt.

Nur die erste Vorbereitung schreibt Testkonfigurationen in die separate Testumgebung;
nie in den NX-Datenordner. Weitere Starts pruefen die vorhandene Konfiguration erneut.
Ein anders gebundener oder beschaedigter Testordner wird nicht ueberschrieben.

Ohne NX-Start pruefen (erste Ausfuehrung legt nur die Testkonfiguration an):
Start-NX2512-Test.cmd -CheckOnly
Andere lokale NX2512-Installation: -NXInstallRoot "C:\\anderer-pfad\\NX2512"
Anderer neuer Testordner: -TestRoot "C:\\NX_Migration\\anderer-testordner"
Ohne Windows-Zuordnung fragt der Starter nach dem lokalen Datenpfad.
Im Docker-Betrieb ist die Ordnerzuordnung ein Hostpfad, kein Containerpfad.
Eine abweichende lokale Kopie kann getestet werden, ist aber kein Freigabebeleg fuer
einen anderen Serverpfad. Fuer Server-Abnahme muss das Protokoll den verwalteten
Kandidaten/konfigurierten Hostpfad benennen.

Pruefen: Maschinen unter Neu, Werkzeuge/Geometrie, Spannmittel, Posts/NC-Ausgabe,
Simulation/Kollisionen und Pruefmittel. Mit Testteilen arbeiten, niemals produktive
Dateien speichern. Ein fehlerfreier Start ist noch keine fachliche Freigabe.
W:, UNC-/Netzpfade und Junctions sind in diesem lokalen Pilot gesperrt.
Eine gewaehlte MTX-Rolle setzt UGII_DEFAULT_ROLE nur im NX-Kindprozess.
Bestehende persoenliche Profile werden nicht geloescht oder ueberschrieben.
Das ist ein lokaler UI-Test, keine erzwungene Firmenprofilverteilung.
''').encode('utf-8')
    return files, config


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in sorted(files.items()):
            package.writestr(name, data)
    return output.getvalue()
