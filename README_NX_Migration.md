# Migration des NX-Netzwerkordners von NX2406 nach NX2512

Das Hauptskript ist `Invoke-NX2406ToNX2512Migration.ps1`. Es erzeugt einen neuen NX2512-Staging-Baum aus drei Quellen:

1. dem unveraenderten Siemens-/SIDT-Standard von NX2406,
2. einer angepassten NX2406-Arbeitskopie,
3. dem unveraenderten Siemens-/SIDT-Standard von NX2512.

Dadurch werden nicht einfach zwei Ordner uebereinanderkopiert. Das Skript erkennt fuer jede Datei, ob sie Siemens-Standard, eine kundenspezifische Ergaenzung, eine sichere Kundenanpassung oder ein echter Versionskonflikt ist.

Die angepasste NX2406-Quelle darf auch eine Kopie eines vollstaendigen NX-Ordners sein. Vor dem Vergleich begrenzt das Skript sie auf den durch `after_install.bat` definierten Netzwerkumfang. Lokale Programmdateien wie `MACH\auxiliary`, `CMM_INSPECTION\application` oder `NXPLOT\bin` werden nicht als Share-Anpassungen uebernommen und separat protokolliert.

## Unveraenderliche Sicherheitsgrenze

`W:\SiemensShare\nx2406` ist das Produktivverzeichnis und wird nicht angefasst.

- Der bekannte Produktivpfad und alle Unterordner sind als Quelle und Ziel gesperrt.
- Alle Dateioperationen sind auf lokale Offline-Pfade beschraenkt; `W:` und UNC-Freigaben sind gesperrt. Ein spaeterer Share-Pfad darf ausschliesslich als Textreferenz konfiguriert werden.
- Junctions und symbolische Links in Quellen und Ausgabepfaden werden abgewiesen.
- Berichtsordner duerfen weder vorhandene Berichte noch Quellen ueberlagern.
- `Build -Apply` akzeptiert nur ein neues, noch nicht vorhandenes Staging-Verzeichnis.
- Es gibt kein Spiegeln, Loeschen oder Bereinigen eines vorhandenen Verzeichnisses.

Die aktuelle Analyse verwendete ausschliesslich `C:\Siemens\NX2406`, `C:\Siemens\NX2512`, die statisch extrahierten SIDT-Dateien und die lokale Arbeitskopie `nx2406`.

## Herkunft der Ordnerstruktur

Die Installationsdateien wurden statisch entpackt und nicht ausgefuehrt. Die vollstaendige NX-Dateistruktur steckt nicht in den EXE-Dateien. Das enthaltene `applications\nx\after_install.bat` kopiert die Standarddaten aus der separat installierten NX-Version unter `C:\Siemens` und legt diese Share-Bereiche an:

- `UGII` mit Einzeldateien, `udf`, `materials` und SIDT-Overlay,
- `NXPLOT\config\pm_server` mit SIDT-Overlay,
- `MACH\custom` und `MACH\resource`,
- `CMM_INSPECTION\custom` und `CMM_INSPECTION\resource`.

Die extrahierten Strukturen und die Installer-Analyse liegen unter `installer_extracted`.

## Automatische Entscheidungen

Das Skript arbeitet mit SHA-256-Inhaltsvergleichen und verwendet folgende Regeln:

- Unveraenderter NX2406-Standard wird durch die NX2512-Datei ersetzt.
- In NX2512 entfallene, unveraenderte Standarddateien werden ausgelassen.
- Kundenspezifische neue Dateien werden uebernommen.
- Kundenanpassungen werden uebernommen, wenn Siemens dieselbe Datei in NX2512 nicht geaendert hat.
- Von Siemens stammende DLL-/EXE-Wartungsunterschiede werden anhand der Hersteller-Metadaten erkannt; es wird die NX2512-Version verwendet.
- Wenn Kunde und Siemens dieselbe Datei geaendert haben, erfolgt keine blinde Ueberschreibung.
- Office-Dateien werden paketintern verglichen. Unterschiede nur in `customXml`-/Papierkorb-Metadaten gelten nicht als fachliche Dokumentaenderung.
- Vorkommen von `nx2406` in Pfaden und Textinhalten werden berichtet, aber niemals global ersetzt.
- Die absolute Referenz in `MACH\resource\usertools\ugii\menus\custom_dirs.dat` wird gezielt auf den konfigurierbaren NX2512-Share umgestellt.
- Die nicht referenzierte Datei `UGII\ugii_env_ug_nx2406.dat` ist bytegleich mit dem NX2406-Standard und wird als alte Standardkopie ausgelassen.
- Dateien ausserhalb des After-Install-Netzwerkumfangs werden in `ignored_outside_after_install_scope.csv` dokumentiert und nicht in den NX2512-Share kopiert.

## Semantische Konvertierung der Werkzeugdatenbank

Fuer `MACH\resource\library\tool\metric\tool_database.dat` wird kein Zeilen-Merge verwendet. Der Konverter liest `#CLASS`, `FORMAT` und `DATA`, vergleicht Werkzeugschluessel und uebertraegt die Kundenfelder in die Feldreihenfolge des NX2512-Schemas.

Die aktuelle Arbeitskopie enthaelt:

- 241 eindeutige neue Kundenwerkzeuge,
- keine geaenderten oder geloeschten Siemens-Standardwerkzeuge,
- keine Schluesselkollision mit NX2512,
- sieben exakt doppelte Kundenzeilen, die beim Konvertieren einmalig uebernommen werden.

Das Ergebnis enthaelt 1.528 NX2512-Standardwerkzeuge plus 241 Kundenwerkzeuge, insgesamt 1.769 Datensaetze.

## Entscheidung fuer die Standard-DB

`MACH\auxiliary\mfgam\xbps\data\standard_db.xml` gehoert nicht zu dem vom `after_install.bat` erzeugten Netzwerkordner. NX2512 verwendet deshalb weiterhin das unveraenderte lokale Original aus `C:\Siemens\NX2512`; die NX2406-Datei wird nicht in den Share uebernommen. Das entspricht der fachlichen Entscheidung, beim Original zu bleiben.

## Aktueller Analysezustand

Der korrigierte Build-Bericht liegt unter `C:\NX_Migration\Reports\NX2512_AfterInstall_Scoped`. Er basiert ausschliesslich auf dem vom `after_install.bat` definierten Netzwerkumfang:

- 31.349 Dateien in der gelieferten NX2406-Quelle,
- 11.604 Dateien innerhalb des Share-Umfangs,
- 19.745 ausgeschlossene lokale Installationsdateien,
- 12.761 Dateien im NX2512-Kopierplan,
- 3.387.472.966 Byte geplanter Inhalt,
- zwei erzeugte Konvertierungen,
- keine offenen manuellen Merge-Konflikte im damaligen Bericht; dies ist keine fachliche Freigabe,
- 15 verbleibende Vorkommen von `nx2406` in Pfad oder Inhalt.

Der validierte Testbaum liegt unter `C:\NX_Migration\NX2512_AfterInstall`. Der vollstaendige SHA-256-Abgleich ergab keine fehlenden, zusaetzlichen oder abweichenden Dateien.

Die wichtigsten Berichte sind:

- `migration_summary.json`: Zusammenfassung,
- `migration_inventory.csv`: Entscheidung fuer jede NX2406-Datei,
- `copy_plan.csv`: genaue Quelle und Zielrelativpfad jeder Staging-Datei,
- `manual_review.csv`: verbleibende manuelle Freigaben,
- `conversion_details.json`: Details der semantischen Konverter,
- `converted_files`: erzeugte NX2512-Konvertierungskandidaten,
- `ignored_outside_after_install_scope.csv`: nicht zum Netzwerkordner gehoerende Eingabedateien,
- `nx2406_path_references.csv`: gezielt zu pruefende Versionsreferenzen.

## Ausfuehrung

Nur Analyse und Berichte erzeugen:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\Invoke-NX2406ToNX2512Migration.ps1 `
  -Mode Analyze `
  -ReportPath .\migration_reports\analyse
```

Einen Build nur planen, ohne das Staging-Verzeichnis anzulegen:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\Invoke-NX2406ToNX2512Migration.ps1 `
  -Mode Build `
  -StagingPath D:\NX_Migration\NX2512_Staging `
  -ReportPath D:\NX_Migration\reports\build_preview
```

Nach Pruefung der Berichte einen neuen Offline-Staging-Baum erzeugen:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\Invoke-NX2406ToNX2512Migration.ps1 `
  -Mode Build `
  -StagingPath D:\NX_Migration\NX2512_Staging `
  -ReportPath D:\NX_Migration\reports\build `
  -Apply
```

Optionale Pfade koennen mit `-CustomizedNX2406Path`, `-StandardNX2406Path`, `-StandardNX2512Path`, `-SIDTNX2406Path` und `-SIDTNX2512Path` gesetzt werden. Der spaetere Share-Pfad in konvertierten Konfigurationsdateien wird mit `-TargetNX2512SharePath` gesetzt; Standard ist `W:\SiemensShare\nx2512`. Das Skript schreibt dabei nicht auf diesen Share.

## Abnahme vor einer Serverfreigabe

1. Die 15 Eintraege aus `nx2406_path_references.csv` einzeln bewerten. Vierzehn davon sind historische `.log`-Dateien; fachlich relevant ist vor allem der Dateiname `NX2406_CAM_Setup_DMC1150V_MPP.prt`.
2. NX2512 gegen den Offline-Staging-Baum starten.
3. Werkzeugbibliothek laden und Stichproben der Kundenwerkzeuge pruefen.
4. CAM-Ausgaben fuer Part, Solid Support und Non-Solid Support testen.
5. Kundenspezifische DLLs, Posts, Maschinenmodelle und `.prt`-Templates fachlich testen.
6. Erst nach erfolgreicher Abnahme einen separaten NX2512-Share bereitstellen. NX2406 bleibt als Rueckfallversion unveraendert.

`Merge-NXNetworkFolders.ps1` bleibt als allgemeines, niedrigstufiges Ordnerwerkzeug erhalten. Fuer diese Migration ist es nicht das Hauptskript, weil es keine Siemens-/Kundenunterschiede und keine semantischen Datenkonvertierungen erkennen kann.

## Verwaltungsanwendung: lokaler Pilot

Der Verwaltungskern liegt unter `server`. Er verwendet FastAPI und eine lokale
SQLite-Datenbank. NX bekommt weiterhin einen normalen Ordner, keine proprietaere
Datenbankverbindung. Die Verwaltung erzeugt neue Ordner-Versionen; vorhandene
Versionen werden ueber die API nicht veraendert. Ein SHA-256-Manifest erkennt
nachtraegliche Aenderungen ausserhalb der Anwendung. Betriebssystem-ACLs fuer
schreibgeschuetzte Releases sind fuer den spaeteren Serverbetrieb noch einzurichten.

Der Datenfluss ist:

```text
Business Central (spaeter)
          |
          v
ERP-Upsert-API -> versionierte Stammdaten + Dateianhaenge
                              |
                      technische CAM-Pruefung
                              |
                   neuer NX-Ordner / neuer Export
                              |
              Datei-Pruefung + fachliche NX-Abnahme
                              |
                   lokaler aktiver Versionszeiger
                              |
                    normaler Ordner fuer NX
```

### Start unter Windows

Python 3.10 oder neuer ist erforderlich. Fuer einen spaeteren Dauerbetrieb ist
ein dann unterstuetzter, aktueller Python-Versionsstand zu verwenden.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-NX-Verwaltung.ps1
```

Danach `Start-NX-Verwaltung.cmd` doppelklicken und
`http://127.0.0.1:8765` oeffnen. Den im Startfenster erzeugten Sitzungsschluessel
in die GUI eingeben. Er wird nicht im Browser gespeichert. Server stoppen:
Strg+C im Startfenster. Daten bleiben unter `C:\NX_Migration\ServerData` erhalten.
Bei einem Neustart aendert sich der automatisch erzeugte Sitzungsschluessel.

Die lokale Konfiguration steht in `server/settings.example.json`.
Quellen und Systemprotokolle werden dort als feste Aliase hinterlegt; API-Aufrufe
duerfen keine beliebigen Dateipfade vorgeben. Der Server bindet ausschliesslich
an `127.0.0.1`; er ist noch kein eingerichteter Windows-/Netzwerkdienst.

### Bereits umgesetzt

- GUI und API fuer 21 Stammdatentypen: Werkzeuge, Halter, Schaefte, Baugruppen,
  Konturen, Bezugspunkte, Mehrfachwerkzeuge, Spannmittel, Maschinen/Geraete,
  Werkstoffe, Schnitt-/Bearbeitungsdaten, Kraftparameter und CMM-Ressourcen;
- Revisionen, Historie und ein verkettetes Aenderungsprotokoll;
- 3D-/Dokumentanhaenge mit SHA-256 und Zuordnung zu Stammdaten;
- neue Ordner-Snapshots, Integritaetspruefung und dokumentierte Abnahme;
- getrennte API-Berechtigungen fuer CAM-Administration und ERP-Zulieferung;
- idempotenter ERP-Upsert mit Company-ID, System-ID und ERP-Quellrevision;
- schema-basierter Lese-/Import-/Exportadapter fuer 41 native Bibliotheken
  (einschliesslich metrischer/Zoll- und CMM-Varianten), nur in neue Ordnerstaende;
- kontrollierte Zusammenfuehrung ehemaliger Henning-Dierk-Datenbanken in die
  Standardbibliotheken, Vorschau mit Pruefsummen und Drei-Wege-Konflikten;
- Spaltenmasken aus den tatsaechlichen FORMAT-Bloecken statt festem NX2406-Schema;
- mehrzeilige Objekte behalten alle Sektionen, RTYPEs und SEQs zusammen;
- lokaler aktiver Versionszeiger mit Konfliktschutz; Rueckwechsel auf einen
  zuvor freigegebenen, unveraenderten Stand erfolgt ueber denselben Endpunkt.

Wichtig: Die Anlage im Stammdatenkatalog ist noch nicht gleichbedeutend mit
Sichtbarkeit in NX. Nur freigegebene, bibliotheksspezifische CAM-Daten werden
beim Ableiten exportiert. Alle FORMAT-Felder und Referenzen sind fachlich zu
pruefen. Die GUI liest die aktuelle Bibliothek und erzeugt passende Spaltenmasken.
Modelle gehen in die vorgesehene `graphics`-Bibliothek, nicht `graphics/managed`.
Referenzen und Dateinamen muessen dazu passen; abweichende Dateien gleichen
Namens werden nicht ueberschrieben. Ein Maschinen-DAT-Eintrag erzeugt kein
vollstaendiges Kinematik-/Post-/Simulationspaket. Fuer Maschinen/Geraete mit
Dateianhaengen fehlt noch der Paketadapter; vorhandene Pakete bleiben als
Ordnerdateien versioniert. Feature-/Normtabellen, physikalische Material-XMLs,
Bearbeitungswissen und Vorlagen bleiben Dateibestaende, kein beliebiger DAT-Editor.

Werkzeuge/Komplettwerkzeuge/CMM-Taster brauchen vor Export eine dokumentierte
3D-/CAM-Pruefung fuer die Zielversion: Einheiten, Achse, Montage, schneidende,
nichtschneidende und Kollisionsgeometrie. Allgemeine STEP-/Herstellerdaten
bleiben in Aufbereitung. Aufbereitete NX-PRT-Ergebnisse werden mit allen
Anhang-Pruefsummen an den Pruefbeleg gebunden. Ein parametrisches NX-Werkzeug
ohne externes Modell darf alternativ nach einem dokumentierten NX-Test
freigegeben werden. STEP-Vorpruefung und ein gefuehrter NX-Importauftrag sind
implementiert (siehe unten); es gibt keine vollautomatische STEP-zu-CAM-Konvertierung
und keine Geometriepruefung allein anhand einer Dateiendung.

Bereits in NX vorhandene Werkzeugschluessel werden nicht automatisch
ueberschrieben. Importierte Kundenobjekte koennen mit Herkunfts-/Versionsschutz
in neuen Staenden aktualisiert werden. Siemens-Standardobjekte bleiben nicht
ueberschreibbar. Der aeltere metrische Einzelwerkzeugadapter bleibt append-only;
fuer Updates wird der native Gruppenadapter verwendet.
Stilllegung im Katalog loescht keine NX-Datensaetze. Unveraenderte, bereits
exportierte Werkzeuge werden bei weiteren Ableitungen nicht nochmals eingefuegt.
Vorhandene Objekte lassen sich nach einer bestaetigten Stammdatenvorschau als
Entwuerfe uebernehmen; standardmaessig nur Kundenabweichungen. Wiederholungen
erzeugen keine Dubletten. Abweichende bereits verwaltete Objekte sind Konflikte,
keine automatischen Ueberschreibungen. Es entstehen keine automatischen ERP-Artikel.

Die automatische Erzeugung kompletter Maschinen-/Simulationspakete, vollautomatische
CAM-Geometrieaufbereitung, Business-Central-Synchronisation, Cloud Tool Manager und SINUMERIK
Integrate bleiben Erweiterungspunkte, keine implementierten Verbindungen.
Microsoft beschreibt individuelle Business-Central-Objekte ueber
[Custom API Pages in AL](https://learn.microsoft.com/en-us/dynamics365/business-central/dev-itpro/developer/devenv-develop-custom-api).
Ob dafuer neue ERP-Tabellen oder vorhandene Artikel-/Anlagenobjekte verwendet
werden, ist mit eurem Business-Central-Partner zu entscheiden.

### API-Vertrag und ERP-Test

Alle fachlichen Endpunkte erwarten `Authorization: Bearer <Schluessel>`.
Der maschinenlesbare Vertrag ist unter `GET /api/v1/openapi.json` mit
Administrationsschluessel verfuegbar. Wichtige Endpunkte:

| Endpunkt | Zweck |
|---|---|
| `GET/POST /api/v1/resources` | Stammdaten lesen / anlegen |
| `PUT /api/v1/resources/{id}` | Revision mit `expected_revision` speichern |
| `GET /api/v1/resources/{id}/history` | Historie |
| `POST /api/v1/erp/resources/upsert` | ERP-Daten anlegen / aktualisieren |
| `POST /api/v1/assets` | Datei als Base64 ablegen |
| `POST /api/v1/releases` | Konfigurierten Quellordner versionieren |
| `POST /api/v1/import-preview` | Henning-Dierk-/Standard-Merge vorab vergleichen |
| `GET /api/v1/releases/{id}/libraries` | Alle nativen Bibliotheken und Referenzpruefung |
| `GET /api/v1/releases/{id}/libraries/{library}` | FORMAT-Schemata und gruppierte Objekte lesen |
| `POST /api/v1/releases/{id}/catalog-preview` | Stammdatenimport vorab vergleichen |
| `POST /api/v1/releases/{id}/catalog-import` | Bestaetigte Vorschau als Entwuerfe uebernehmen |
| `POST /api/v1/releases/{id}/derive` | Neuen Stand mit nativen Bibliotheksdaten erzeugen |
| `GET /api/v1/releases/{id}/tools` | NX-Werkzeuge lesen, filtern und seitenweise anzeigen |
| `POST /api/v1/releases/{id}/validate` | Manifest, PAX-XML, alle Bibliotheksformate und bekannte Referenzen pruefen |
| `POST /api/v1/releases/{id}/review` | Sechs fachliche Checks und NX-Protokoll dokumentieren |
| `GET/POST /api/v1/active` | Lokalen Zeiger lesen / kontrolliert umschalten |
| `POST /api/v1/logs/{alias}/scan` | NX-Ladefehler erfassen |

Fuer einen manuellen ERP-Test koennen vor dem Start `NX_ADMIN_TOKEN` und
`NX_ERP_TOKEN` als zwei unterschiedliche, zufaellige Prozessvariablen mit
mindestens 32 Zeichen gesetzt werden. Der ERP-Schluessel darf Stammdaten lesen,
ERP-Upserts senden und Dateianhaenge lesen, aber weder CAM-Werte aendern noch
Releases freigeben/aktivieren. Keine echten Zugangsdaten in Konfigurationsdateien
oder Git ablegen. Eine produktive Anbindung braucht HTTPS, Benutzer-/Dienstrollen,
Secret-Verwaltung, Monitoring und einen gesicherten Dienstbetrieb; diese
Betriebseinrichtung ist nicht Bestandteil dieses lokalen Piloten.

Ein ERP-Upsert-Beispiel:

```json
{
  "kind": "tool",
  "company_id": "BC-Mandant-ID",
  "system_id": "BC-System-ID-des-Werkzeugs",
  "number": "W-001",
  "description": "Schaftfraeser 12 mm",
  "unit": "STK",
  "source_revision": "ERP-ETag-oder-Revision"
}
```

Ein identischer Aufruf erzeugt keine weitere Revision. Eine Aktualisierung
erfordert zusaetzlich `expected_revision` aus dem aktuellen Stammdatensatz.
ERP-Felder und CAM-Felder haben getrennte Eigentuemer; es gibt kein
Last-Writer-Wins zwischen ERP und CAM. ERP-Stammdaten ohne CAM-Anreicherung
bleiben Entwuerfe und werden nicht in NX exportiert.

### Reproduzierbare NX-Testumgebung

Ein neuer Build speichert `release_manifest.json` und kontrolliert jede Kopie
per SHA-256. `BuildCompleted` wird erst nach erfolgreichem Abschluss gesetzt.
`compatibility_review.csv` weist notwendige Funktionspruefungen getrennt von
Merge-Konflikten aus. Ein Build mit manuellen Merge-Konflikten wird blockiert.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\New-NX2512TestEnvironment.ps1 `
  -StagingRoot C:\NX_Migration\NX2512_Managed_20261006 `
  -OutputRoot C:\NX_Migration\NX2512_ManagedTest_20261006 `
  -ReleaseManifestPath C:\NX_Migration\Reports\NX2512_Managed_20261006\release_manifest.json
```

Diese Datei erzeugt die zusammengefuehrte Maschinenpalette, die zugehoerige
`custom_dirs.dat`, einen vollstaendigen Pruefsummenstand und einen neuen
`Start-NX2512-Test.cmd`. Der neue Starter nimmt die Konfiguration als Parameter
statt fest eingebauter Hashwerte. `-CheckOnly` kontrolliert den gesamten
Dateibestand, startet NX nicht und erzeugt keine Laufzeitdateien.
Der bisherige Testbaum und bisherige Starter werden nicht ersetzt.

### Bekannte Abnahmesperren

Das vorhandene Maschinentestprotokoll `Jan14e4f550.syslog` vom 23.09.2026 enthaelt
UID-Konflikte bei DMC1450V-X-/Y-Achse sowie nicht aufgeloeste Baugruppenreferenzen.
Die erwarteten UID-Bytefolgen finden sich in den vorhandenen OKUMA-Achsdateien,
nicht in den DMC-Achsdateien; dies ist ein Suchbefund, keine Freigabe zum
Austausch von Geometrie. Die betroffenen Dateien sind bytegleich aus NX2406
uebernommen. Es wird weder ein UID-Check deaktiviert noch automatisch eine
andere Maschinenkomponente zugeordnet. Korrekte Geometrie und Baugruppenbezug
muessen in einer lokalen NX-Arbeitskopie fachlich geklaert werden.

`MACH/resource/dmg_templates.pax` ist ausserdem bereits in der Quelle kein
wohlgeformtes XML: In Zeile 19 schliesst `</Palette>` die Palette; in Zeile 21
folgt ein weiterer Eintrag ausserhalb des Wurzelelements. Entscheidung des
Benutzers vom 07.10.2026: optional, Standardaufbau beibehalten. Daher lassen
Migrationsskript und Server diese Datei in NEUEN Staenden weg. Originaldateien
und historische Releases werden nicht geaendert. Andere fehlerhafte PAX-Dateien
bleiben Abnahmesperren; die Standardpalette wird nicht veraendert.
Ein NX-Protokoll mit Ladefehlern oder ohne CAM-Aktivitaet und Bezug zum
getesteten Datenstand kann keine Freigabe begruenden. Die GUI-Abnahme ist eine
dokumentierte Erklaerung eines qualifizierten Bedieners, keine automatische
Zertifizierung von Postprozessoren, Kinematik oder Kollisionsfreiheit.

### Regressionstests

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\Test-NXMigration.ps1
cd server
.\.venv\Scripts\python.exe -m pytest tests -q
```

Die Tests arbeiten mit isolierten kleinen Fixtures und pruefen unter anderem
Produktivpfadsperren, DAT-Fortsetzungszeilen, Gross-/Kleinschreibung,
Drei-Wege-Build, Revisionen, ERP-Idempotenz, Berechtigungen, Werkzeugexport,
Dateianhaenge, Manipulationserkennung und Freigabesperren. Sie ersetzen keine
fachliche Abnahme in NX. SQLite und Audit-Daten liegen lokal, nicht auf SMB;
Backups muessen Datenbank und Dateien konsistent gemeinsam sichern.

Optionaler echter GUI-Test mit bereits installiertem Microsoft Edge, frischem
Browserprofil und isolierten Testquellen (keine NX-Sitzung, keine Live-Daten):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
$env:NX_BROWSER_TEST = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_browser.py -q
Remove-Item Env:NX_BROWSER_TEST
```

### Ablauf fuer Standardbibliotheken (ab 07.10.2026)

1. `server/settings.example.json`: `standards` verweist auf saubere lokale
   Siemens-Installationen (`2406`/`2512`). Quelle ist der bereits migrierte
   lokale Netzwerkordner. Die Serverfunktion migriert dessen Bibliotheken,
   nicht eine komplette NX-Installation/Kinematik. Hauptmigration bleibt separat.
2. `Start-NX-Verwaltung.cmd`, Schluessel eingeben. **Merge-Vorschau erstellen**.
   Reine Kundenaenderungen gegen einen unveraenderten Siemens-Datensatz werden
   uebernommen. Aenderungen in beiden Quellen werden pro Bibliotheksnummer
   entschieden. Alte, unveraenderte Siemens-Daten rollen neue Standards nicht
   zurueck. Fehlende Datensaetze einer Teilbibliothek loeschen keine Standards.
3. Neue Spalten werden nach Namen zugeordnet, nicht nach Position. Notwendige
   Werte muessen ausdruecklich angegeben werden, beispielsweise
   `{"holder-metric":{"MILLING_DRILLING:2":{"STYPE":"fachlich gepruefter Wert"}}}`.
   Umbenannte/entfernte oder mehrdeutige Spalten brauchen einen Adapter oder
   eine ausdrueckliche Entscheidung, die Kundengruppe nicht zu uebernehmen.
   Eine unbekannte Alt-Datenbank wird nicht stillschweigend verworfen. Sie kann
   nach ausdruecklicher Entscheidung als zusaetzlicher/inaktiver Bestand nicht
   uebernommen werden (`legacy_omissions`); die Originaldatei bleibt erhalten.
4. **Geprueften Merge importieren** funktioniert nur bei konfliktfreier, noch
   aktueller Vorschau. Keine Henning-Dierk-DAT-Kopien im neuen Stand; bekannte
   Alt-Sicherungen werden ebenfalls nicht erneut als Datenbanken kopiert.
   Quellen bleiben erhalten. Ein reiner API-Snapshot ohne Normalisierung ist
   weiterhin moeglich, aber nicht der GUI-Standardweg.
5. Bibliotheken inventarisieren, Bibliothek/Schemata lesen; ausgewaehlte
   Bestaende ueber **Stammdatenvorschau** und **als Entwuerfe uebernehmen** importieren.
   ERP-Zuordnung ist dafuer nicht erforderlich und wird nicht aus Namen geraten.
6. Kundenobjekte bearbeiten oder aus dem echten Schema neu anlegen. Halter/
   Schaefte/Konturen mit allen Zeilen/Sektionen pflegen. Werkzeuge erst nach
   CAM-/3D-Aufbereitung freigeben. **Neuen Export ableiten** erstellt einen
   weiteren Ordnerstand; Parent und Quelle bleiben unveraendert.
7. Technisch pruefen, fachlich in NX testen und Protokoll zuordnen. Fehlende
   Kundenreferenzen blockieren. Bereits in Siemens-Beispieldaten vorhandene,
   unveraenderte Referenzprobleme werden separat als Warnungen ausgewiesen,
   nicht als neue Kundenfehler verschwiegen. Neue Versionen brauchen einen
   erneuten NX-/Geometrietest. Das Protokoll muss den tatsaechlichen verwalteten
   Kandidatenordner (`release.folder`) nennen, nicht nur den urspruenglichen
   Quell-/Parentordner. Vorhandene Test-Starter/Manifeste gehoeren jeweils zu
   ihrem bisherigen Stand und duerfen nicht fuer neue Ordner wiederverwendet
   werden. Aktivierung bleibt nur ein lokaler Zeiger.

### STEP-Vorbereitung mit dem NX CAM 3D Tool Assistant (07.10.2026)

Gepruefte Quelle: die vom Benutzer bereitgestellte `NX CAM 3D Tool Assistant.zip`,
Siemens-Anleitung `documentation/NX CAM 3D Tool Assistant V1.0.pdf`, alle 23 Seiten,
Dokumentstand 09.06.2025. Ausgepackte Anleitung und Dialogabbildungen liegen unter
`migration_reports/tool_assistant_source_20261007`; aus dem ZIP wurde keine EXE/DLL
gestartet. Es enthaelt kein STEP-Beispiel und keinen Anwendungsquellcode. Die
Anleitung dokumentiert einen interaktiven Dialog, keine Batch-/Server-API.
`PackageInfo.json` nennt zwar `app/Readme.txt`, diese Datei fehlt im ZIP.
Der tatsaechliche Einstieg ist `app/NXCAM3DToolAssistant.dll`; der abweichende
Dateiname im Text auf Seite 9 ist nicht als existierende Datei zu verwenden.

Die Anleitung nennt NX2306 oder neuer (signiert mit NX2412), ausschliesslich
Werkzeuge mit Spindelanschluss und eine nicht-flache Baugruppe ohne vorhandenes
Manufacturing Setup. Bei Fraesen/Bohren muessen Schneide und nichtschneidende
Geometrie getrennte Koerper sein. Montageflaeche und Montagezentrum sind immer
zu waehlen; die Rotationsorientierung kann bei Fraesen/Bohren automatisch erfolgen,
bei Drehen ist die Schneidenorientierung notwendig. Halter-/Schaftkoerper muessen
bei Mehrfachauswahl mindestens aneinander anliegen. Der Assistant erzeugt dann
CAM-Werkzeug, Ausrichtung am Absolut-CSYS, Parameter, LIBRF/Beschreibung sowie
Halter-/Schaftkonturen. Optional sind CUT-Farbe 42, NOCUT-Farbe 87, Werkzeug- und
Korrekturnummern sowie Tracking Point P9/Antastpunkt. Herstellerabmessungen sind
zu pruefen. Eine misslungene Konturableitung kann still auf einen 1-Stufen-Schaft
zurueckfallen: Konstruktion behalten, vereinfachen, Kontur erneut ableiten und
Kollisionshuelle pruefen. Nur eine PRT-Endung oder passende Farben beweisen nichts.

Wichtige Versionsabweichung: die Anleitung nennt `planar_mill.prt`. Die lokale
NX2512-Installation liefert `mill_planar.prt` und `turning.prt`; im bereitgestellten
BackEnd-DLL-Stringbestand ist `mill_planar` enthalten. Das ist ein statischer
Hinweis, kein Nachweis, dass der Assistant unter NX2512 erfolgreich gelaufen ist.
Keine Vorlagendatei wird umbenannt oder in der Siemens-Installation geaendert.

Implementierter erster Automatisierungsschritt in der GUI:

1. STEP/STP/P21 als Anhang hochladen und einem **Werkzeugentwurf** oder
   **Komplettwerkzeugentwurf** zuordnen; Stammdaten speichern.
2. Im Abschnitt **STEP-Aufbereitung mit Siemens Tool Assistant** den gespeicherten
   Entwurf, STEP-Anhang und einen der zehn Werkzeugtypen aus dem Siemens-Dialog
   waehlen. **STEP vorpruefen** kontrolliert das Textformat, AP203/AP214/AP242 und
   selbstenthaltene Daten. Text-Entity-Zaehler sind keine echten NX-Koerperzaehler.
   Externe Referenzen und pfadartige DATA-Namen werden im ersten Pilot bewusst
   abgelehnt; es findet keine Aufloesung externer Hersteller-/Netzpfade statt.
3. **NX-Importauftrag anlegen**, dann **Importpaket herunterladen**. Das ZIP enthaelt
   die unveraenderte Eingangskopie `input.step`, `job.json`, `LIESMICH.txt` und das
   Journal samt zwei stdlib-Helfern. Werkzeugrevision, STEP-SHA und Worker-SHAs
   sind gebunden. Keine API-Schluessel, keine Vendor-DLLs, kein automatischer NX-Start.
4. Gesamtes Paket in einen neuen, kurzen lokalen Unterordner von
   `C:\NX_Migration\ToolJobs` entpacken. `LIESMICH.txt` lesen. In einer leeren,
   isolierten **NX2512-Modellierungssitzung** das paketierte `nx_step_import.py`
   ueber **Tools / Automation / Journal / Play** abspielen. Das Journal verlangt
   `C:\Siemens\NX2512`, keine offenen Teile/CAM-Sitzung und keine erkennbaren
   produktiven/Netzpfadvariablen. Kein normales Python fuer NX-Journale benutzen.
5. Automatisiert: STEP-Importer passend zum Schema, neue PRT-Ausgabe in
   `output`, Baugruppenstruktur erhalten (`FlattenAssembly=False`), keine
   bewusste Neuskalierung/Koerpervereinfachung, lokaler Translator-Log, Inventar
   der geladenen Prototypen/Koerper und Komponenten. Kein geratenes Teilen
   einer vereinigten Schneide, keine automatisch gewaehlte Spindelachse.
   Ein flaches Modell, fehlende Koerpertrennung, nicht geladene Komponenten oder
   fehlende Volumenkoerper fuehren zu Diagnose statt CAM-Freigabe.
6. `output/report.json` ueber **Bericht zuordnen** / **Bericht uebernehmen** an den
   Auftrag binden. Dieser Bericht ist eine **ungepruefte externe Diagnose**,
   kein signierter geometrischer Nachweis. Er veraendert weder Stammdatenrevision
   noch CAM-Freigabe. Andere Eingangs-SHAs/veraltete Revisionen/Freigabeversuche
   werden abgewiesen. Ein vorhandener Bericht wird nicht durch einen anderen Lauf
   ersetzt; neue Versuche brauchen einen neuen Auftrag/entpackten Ordner.
7. Strukturprobleme beheben und den separat gelieferten Siemens-Assistant ueber
   **Ctrl+U / NX Open / NXCAM3DToolAssistant.dll** starten (nicht Journal Play).
   Montage-/Koerperauswahl und Werkzeugparameter fachlich festlegen. Erst in eine
   lokale Testbibliothek exportieren, LIBRF/DAT/PRT inklusive aller Komponenten
   pruefen; danach gepruefte Daten in der Verwaltung pflegen. Automatischer
   Rueckimport kompletter Assistant-Ergebnisse und Komponentenpakete ist noch
   kein implementierter Adapter. Die sechs Geometriepruefungen bleiben erforderlich.

Neu: Administrator-Endpunkte `GET /api/v1/assets/{id}/step-preview`,
`POST /api/v1/geometry-jobs` (`resource_id`, `expected_revision`, `asset_id`,
`tool_type`), `GET /api/v1/geometry-jobs?resource_id=...`,
`GET /api/v1/geometry-jobs/{id}/package` und
`POST /api/v1/geometry-jobs/{id}/report` (`{"report": ...}`). ERP-Token haben
keinen Zugriff auf diese Aufbereitung. Diagnosestatus: `awaiting_nx`,
`needs_structure`, `needs_selection`, `failed`; niemals `validated`.

Das Journal wurde gegen die lokal installierten **NX2512 pythonStubs** entwickelt.
API-/Paket-/Schutzregeln und GUI sind mit isolierten Fixtures getestet; die
NX-Vertrags-Tests verwenden ausdruecklich Testdoubles, keine echte CAD-Berechnung.
**Ein echter NX2512-STEP-/Assistant-Test und geometrischer Nachweis stehen noch
aus.** Dafuer wird ein repraesentatives Hersteller-STEP benoetigt. Ein laufender
Verwaltungsserver muss fuer die neuen Routen/Tabellen neu gestartet werden.

### Docker-Betrieb des Verwaltungsservers (08.10.2026)

Docker umfasst den Verwaltungsserver/API und die bestehende Browser-GUI, nicht
NX selbst. NX2512, Siemens Tool Assistant und NX-Journale bleiben auf dem
Windows-Arbeitsplatz mit NX-Lizenz. Die PowerShell-Hauptmigration bleibt ebenfalls
ein separates Windows-Werkzeug; ein Linux-Container fuehrt sie nicht automatisch aus.

Voraussetzung auf diesem PC ist **Docker Desktop mit Linux-Containern** und
aktuellem Docker Compose. Docker wurde bei der Umsetzung hier nicht gefunden;
Docker Desktop wird nicht durch ein Projektskript installiert oder eingerichtet.
Hinweise zum Windows-Setup: [Docker-Dokumentation](https://docs.docker.com/desktop/setup/install/windows-install/).

Empfohlener Windows-Teststart:

1. Docker Desktop installieren/aktivieren und auf Linux-Container einstellen.
   Einen eventuell laufenden nativen Verwaltungsserver auf Port 8765 vorher
   beenden oder `NX_WEB_PORT` auf einen anderen freien lokalen Port setzen.
2. **`Start-NX-Docker.cmd`** ausfuehren. Der Starter prueft den lokalen
   Docker-Kontext, die aufgeloesten Einbindungen und lokale Quellen inkl.
   Junctions/Links. Er legt nur bei Bedarf `C:\NX_Migration\DockerReleases`
   und `docker\secrets\admin_token.txt` an, baut das Image und startet im Hintergrund.
   Kein W:-Laufwerk/UNC, keine Produktivfreigabe, keine Remote-Docker-Engine.
3. `http://127.0.0.1:8765` im Browser oeffnen. Den persistenten lokalen
   Administrationsschluessel aus `docker\secrets\admin_token.txt` in die GUI
   eingeben. Die Datei hat eingeschraenkte Windows-Berechtigungen, steht auf
   der Git-Ignorierliste und wird weder ins Image kopiert noch ins Log geschrieben.
   Sie bleibt sensibel; nicht hochladen oder weitergeben. Compose bindet sie
   als Secret-Datei ein, nicht als Klartext-Umgebungswert.
   [Compose-Secrets](https://docs.docker.com/compose/how-tos/use-secrets/)
4. Das Startfenster darf geschlossen werden. Nach einem Neustart von Docker
   startet ein nicht ausdruecklich gestoppter Container wieder; Docker Desktop
   muss dazu laufen. Die Portfreigabe ist nur an `127.0.0.1` gebunden,
   kein offener LAN-Server und kein produktiver Mehrbenutzer-/TLS-Betrieb.
   [Portfreigabe](https://docs.docker.com/engine/network/port-publishing/)

Einbindungen des Windows-Profils:

| Windows / Speicher | Im Container | Zweck / Zugriff |
| --- | --- | --- |
| `C:\NX_Migration\NX2512_Managed_20261006` | `/inputs/nx2512-managed` | Lokaler migrierter Quellordner, nur lesend |
| `C:\Siemens\NX2406` | `/standards/2406` | Siemens-Vergleichsbasis, nur lesend |
| `C:\Siemens\NX2512` | `/standards/2512` | Siemens-Zielstandard, nur lesend |
| `C:\NX_Migration\TestRuntime\temp` | `/logs/nx-test` | NX-Testprotokolle, nur lesend |
| `C:\NX_Migration\DockerReleases` | `/data/r` | Neue lokale NX-Ordnerstaende, schreibbar |
| Docker-Volume `nx-management_management-data` | `/data` | SQLite, Audit, Katalog, Anhaenge, STEP-Auftraege |

Windows-Dateien werden ueber explizite Bind-Mounts eingebunden; das Image enthaelt
keine NX-Installation oder Netzwerkordner. Die Build-Kontext-Allowlist umfasst
nur Anwendung und Laufzeitabhaengigkeiten, nicht virtuelle Umgebung, Tests,
Installer, Archive, Modelle, bestehende Datenbanken oder Zugangsschluessel.
Der Prozess laeuft als UID/GID 10001, mit schreibgeschuetztem Image-Dateisystem,
ohne erweiterte Linux-Capabilities oder Docker-Socket. Nur `/data`, die Testausgabe
und `/tmp` sind zum Schreiben vorgesehen. Datei-/Schemafreigaben ersetzen
weiterhin keine NX-/Kollisionsabnahme.

Die API liefert fuer einen Stand `folder` (Containerpfad) und `host_folder`
(zugehoeriger Windows-Testpfad). In NX ist **`host_folder`** einzutragen.
Die Syslog-Abnahme akzeptiert den echten Windows-Pfad dieses Kandidaten, nicht
den Quellordner oder einen nur aehnlich benannten Ordner. Pfadabbildung ist reine
Metadatenkonfiguration, kein Zugriff auf C: durch den Linux-Container.

Docker startet mit einem **separaten neuen Verwaltungsbestand**. Der bisherige
Ordner `C:\NX_Migration\ServerData` und dessen Datenbank/alte Datenstaende bleiben
unveraendert. Vorhandene NX-Dateien werden wie bisher ueber Import/Merge-Vorschau
uebernommen. Eine bestehende native Verwaltungsdatenbank darf nicht blind als
Docker-Volume eingekopiert werden: gespeicherte absolute Windows-Pfade und
Abnahmebelege benoetigen einen eigenen geprueften Umzugsplan.

Bedienung in PowerShell im Projektordner:

```powershell
.\Start-NX-Docker.ps1 -Action Status
.\Start-NX-Docker.ps1 -Action Config
.\Start-NX-Docker.ps1 -Action Stop
# Nach Codeaenderungen wieder aufbauen/starten:
.\Start-NX-Docker.ps1 -Action Start
# Logs, ohne Schluessel auszugeben:
docker compose -p nx-management -f compose.yaml -f compose.windows.yaml logs --tail 100 nxserver
```

**Nicht `down --volumes`, `down -v` oder Volume-Pruning fuer den Verwaltungsbestand
verwenden:** Das wuerde persistente Daten loeschen. Normales Stoppen entfernt
keine Verwaltungsdaten. Vor Updates/Umzug Backups planen: Server stoppen und
Docker-Datenvolume, `DockerReleases` sowie Secret/Konfiguration konsistent sichern.
Ein Image allein ist kein Backup. [Docker-Volumes](https://docs.docker.com/engine/storage/volumes/)

`compose.yaml` allein ist das portable Linux-Basisprofil ohne Dateiquellen;
`compose.windows.yaml` ergaenzt die konkreten lokalen Windows-Testordner.
Fuer einen spaeteren Linux-Server sind eigene lokale Einbindungen/Konfiguration
noetig; die Windows-C:-Pfade sind dort nicht gueltig. Docker in WSL mit Linux-CLI
verwendet gegebenenfalls `/mnt/c/...`, nicht ungeprueft die Windows-Ueberlagerung.
Der vorhandene native `Start-NX-Verwaltung.cmd` bleibt fuer den bisherigen
Windows-Pilot erhalten. Keine automatische produktive SMB-Umschaltung.

Pruefungen: Python-Startvertrag, Secret-Dateien, Hostpfadabbildung, Schutzregeln,
Persistenz und echte HTTP-/GUI-Tests verwenden isolierte lokale Fixtures.
Compose-Konfiguration laesst sich ohne Engine mit dem offiziellen Compose-CLI
validieren; das ist **kein Image-Build-/Container-Laufzeitnachweis**.

Separater echter Docker-Integrationstest, sobald eine lokale Linux-Engine verfuegbar
ist (baut/startet mit eigenen synthetischen Quellen, prueft Nonroot/Readonly/
Persistenz nach Container-Neuanlage und loescht nur seinen neu erzeugten Testbestand):

```powershell
cd server
$env:NX_DOCKER_TEST = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_docker_integration.py -q -s
Remove-Item Env:NX_DOCKER_TEST
```

Der Test verwendet einen zufaelligen separaten Projektnamen `nx-docker-test-*`,
nicht `nx-management`, keine echten NX-Quellen und keine Produktivfreigabe.

## Vereinbartes Architekturziel und naechste Umsetzung

Die folgenden Punkte beschreiben das Zielbild, keine bereits vollstaendig
implementierte Serverplattform. Die erste Prioritaet ist der sichere Import
eines alten Netzwerkordners in einen neuen, separat erzeugten Versionsstand.

- **Upgrade-Wizard:** lokale Kopie des alten Ordners und neue vollstaendige
  Standardbasis auswaehlen. Fuer den Drei-Wege-Vergleich bleibt die passende
  unveraenderte alte Siemens-/SIDT-Referenz erhalten. Eine Installer-Extraktion
  allein enthaelt nicht alle Netzwerkdateien; sie braucht die zugehoerige
  NX-Installation oder eine daraus vorbereitete vollstaendige Vorlage.
  Ein spaeterer Windows-Client kann Version, Vollstaendigkeit und Voraussetzungen
  lokal pruefen. Der Browser auf einem anderen PC kann das nicht selbst.
- **Zentrale Verwaltung:** Katalog, Datensatzrevisionen, Modelle, Vorlagen,
  Herkunft, Pruefsummen und Freigaben werden ueber die Server-API verwaltet.
  Neue Spalten brauchen bewusste Zuordnung/bestaetigte Werte; ungeklärte
  Konflikte blockieren die Uebernahme. Henning-Dierk-Datenbanken werden in
  Standardbibliotheken integriert; die optionale DMG-Palette bleibt weg.
- **Unveraenderliche Releases:** Aenderungen erzeugen einen neuen Entwurf und
  nach Pruefung einen neuen Release. Quellen und veroeffentlichte Staende
  werden nicht direkt bearbeitet. SHA-256 erkennt Aenderungen, verhindert sie
  allein aber nicht; verbindliche Dateisystemrechte sind noch umzusetzen.
- **Separate SMB-Bereitstellung:** ein eigener Samba-Container kann den
  veroeffentlichten Release-Speicher nur lesend freigeben. Verwaltungsdatenbank,
  Secrets und Arbeitsbereiche sind nicht Bestandteil der Freigabe. Dauerhafte
  Daten liegen ausserhalb des Container-Images und werden konsistent gesichert.
  Fuer den Server ist eine Linux-VM mit Docker Engine vorgesehen; das aktuelle
  Windows-/Docker-Desktop-Profil bleibt ein lokaler Testaufbau.
- **Windows-Client und NXOpen-Erweiterung:** ein gemeinsamer Startweg bindet
  jede NX-Sitzung an einen konkreten freigegebenen Stand. Keine laufende Sitzung
  wird auf einen anderen Bibliotheksstand umgestellt. Pakete/Skripte werden
  versionsgebunden geprueft; kein beliebiger Remote-Code und kein verteilter
  globaler Administrationsschluessel. Ein spaeterer dedizierter NX-Worker bleibt
  ein separater, fuer Betriebssystem/Virtualisierung/Lizenz gepruefter Baustein.
- **Einheitlicher NX-Firmenstandard:** UI-Rollen, Menues/Ribbon/Tasten,
  Customer Defaults, Suchpfade, CAD-/Zeichnungs-/CAM-Vorlagen, UDFs,
  Reuse-Inhalte und freigegebene Skripte werden gemeinsam versioniert.
  Lokale Benutzeranpassungen koennen zentrale Rollen ueberlagern: Durchsetzung
  und erlaubte Ausnahmen sind zu pruefen. Bestehende Benutzerprofile werden
  nicht ungefragt geloescht oder ueberschrieben. Vorlagen bleiben lesend;
  daraus entstandene Projektdateien werden nicht automatisch nachtraeglich
  geaendert. NX-Version, aktive Anwendung und Lizenzen beeinflussen die UI.
- **Business Central:** kaufmaennische Felder bleiben ERP-verantwortlich;
  technische CAD/CAM-Daten und Freigaben gehoeren der NX-Verwaltung.
  Verknuepfte Entwuerfe werden erst nach technischer Vervollstaendigung
  veroeffentlicht. Konkrete ERP-Felder, Betriebsart und API-Anbindung sind
  noch abzustimmen; kein Teamcenter wird vorausgesetzt.
- **Spaeteres KI-Gateway:** zentrale Anbieter-/Datenfreigaben, Berechtigungen,
  Kostenlimits und nachvollziehbare Auftraege. Keine ungepruefte externe
  Uebertragung von Modellen, kein beliebiger KI-generierter NX-Code und keine
  automatische NC-/Bibliotheksfreigabe. Cloud Tool Manager und Sinumerik
  Integrate bleiben ebenfalls spaetere Adapter, keine aktuelle Anbindung.

Aktuell vorhanden sind das PowerShell-Migrationswerkzeug, der lokale
Verwaltungspilot mit Bibliotheksnormalisierung/Revisionen und die Docker-
Betriebsvorbereitung. Der Server importiert bisher einen bereits migrierten
Ordner; die vollstaendige Versionsmigration ist noch mit dem GUI-Wizard zu
verbinden. Produktives SMB, Client, Profil-Durchsetzung, ERP und KI-Gateway
sind noch nicht implementiert. `W:\SiemensShare\nx2406` bleibt gesperrt.

## Git-Repository: Quellcode, nicht NX-Nutzdaten

Das lokale Repository wird direkt in diesem Projektordner gefuehrt. Es umfasst
Skripte, Verwaltungsanwendung, Tests, Abhaengigkeiten, Docker-Testprofile und
Dokumentation. Das zentrale Remote sollte **privat** sein.

`.gitignore` schliesst insbesondere `nx2406`, `installer_extracted`,
`migration_reports`, Installer/Archive, CAD-Modelle, virtuelle Umgebungen,
lokale Verwaltungsdatenbanken, `server/settings*.json` (ausser dem Beispiel),
Secrets und `.env` aus. Die ausgeschlossenen Dateien bleiben lokal erhalten.
Eine neue Arbeitskopie enthaelt daher absichtlich keine NX-Installation oder
Kundendaten; diese Quellen muessen lokal separat bereitgestellt werden.
Die Docker-Testprofile enthalten lokale Beispielpfade, keine Zugangsschluessel.

**Git versioniert die Anwendung. Die Anwendung versioniert die NX-Ordner.**
Weder ein Code-Commit noch ein Push aktiviert automatisch einen NX-Datenstand
oder eine Produktivfreigabe. Vor einem Commit immer die vorgemerkten Dateien
pruefen; `.gitignore` ist kein Secret-Scanner und schuetzt keine Dateien,
die bereits frueher in Git aufgenommen wurden.

```bash
git status --short
git diff --cached --stat
git diff --cached --check
# Erst nach Pruefung und mit der eigenen Git-Autoridentitaet:
git commit -m "Initialer Stand der NX-Migration und Verwaltungsanwendung"
```

Fuer das erste Pushen fehlen die vom Benutzer ausgewaehlte Remote-URL und
gegebenenfalls die Anmeldung beim Anbieter. Es wird kein fremdes/neues Remote
ohne diese Auswahl angelegt oder ueberschrieben. Erst nach Pruefung eines
bestehenden Remote-Bestands `origin` setzen und `main` ohne Force-Push hochladen.
Weitere Arbeiten koennen ueber `feature/...`-Branches mit geprueften Merges
nach `main` erfolgen. Der aktuelle Stand ist ein Pilot, keine Produktivfreigabe.
