# Migration des NX-Netzwerkordners von NX2406 nach NX2512

Den kompakten Funktions- und Abnahmestand der Serveranwendung beschreibt
[ABNAHMESTAND.txt](ABNAHMESTAND.txt). Der erste praktische Testablauf steht in
[TEST_CHECKLISTE.txt](TEST_CHECKLISTE.txt); das vollstaendige Entwicklungsprotokoll
in [DEVELOPMENT_PROGRESS.txt](DEVELOPMENT_PROGRESS.txt). Automatisierte Tests und
vorhandene Funktionen sind dabei getrennt von realer NX-/Produktivabnahme zu lesen.

Die GitHub-Testpipeline und lokale Testbefehle sind in [CI_TESTS.txt](CI_TESTS.txt)
beschrieben. Sie prueft Kernfunktionen unter Linux/Windows und die Oberflaeche
in isolierten Edge-Sitzungen; sie fuehrt keine Produktivbereitstellung aus.
Der Workflow wird erst mit einem entsprechenden Commit/Push auf GitHub aktiv.

Ergaenzung vom 16.10.2026: Die Dateipaketuebernahme verwendet dieselbe explizite
SIDT-Auswahl wie der Upgrade-Wizard, auch ohne statisch konfigurierten SIDT-Pfad.
Unveraenderte Standarddateien werden dadurch nicht als Kundenbesitz importiert.
Ausgewaehlte Extraktionsberichte werden einheitlich begrenzt und SHA-gebunden
gelesen; defekte Eingaben sperren statt auf andere Ordner zurueckzufallen.
Etappen113/114 sind im lokalen Pilot bereitgestellt. Nach dem Neustart Browser
neu laden und den neuesten Sitzungsschluessel aus dem neuen Serverfenster verwenden.
Dies ist keine reale NX-/Produktivabnahme; die breite Gesamtbaseline bleibt historisch.

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

Fuer den ersten praktischen GUI-/NX-Test siehe [TEST_CHECKLISTE.txt](TEST_CHECKLISTE.txt).
Sie trennt den vollstaendigen NX2406-Versionswechsel vom Import eines bereits
migrierten NX2512-Ordners und beschreibt die erwarteten Ergebnisse ohne Produktivfreigabe.

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

Die GUI prueft ihre lokalen JavaScript-Module vor dem Login. „Verbinden“ ist
bis zum erfolgreichen Start gesperrt; ein eigener Verbindungsstatus zeigt Laden,
Bereitschaft und laufende Anmeldung. Fehlende Skripte oder ein unerwarteter
Oberflaechenfehler sperren die Sitzung mit einer festen Meldung, ohne Ausnahme-
texte, Schluessel oder CAM-Rohdaten in eine Diagnose zu kopieren. Statische
Hinweise bleiben auch ohne JavaScript sichtbar. Diese Startpruefung fuehrt
keinen privaten API-Aufruf aus und ersetzt keine NX-/Datei-/Serverintegritaetspruefung.
Bei Fehlern Browser neu laden und Serverdateien pruefen; dabei koennen noch
ungespeicherte Eingaben verloren gehen. Angenommene Serverauftraege bleiben
erhalten und werden durch Abmeldung/Neuladen nicht gestoppt oder neu gestartet.
Ein abgelehnter/unvollstaendiger Login gibt keinen Workspace frei und kann
ausdruecklich erneut versucht werden. Abmeldung verwirft auch spaete Loginantworten;
eine alte Antwort entsperrt keinen neueren Anmeldeversuch.
Der Arbeitsbereich wird erst nach dem geprueften initialen Workspace-Read
angezeigt. Wird dieser Read in derselben Sitzung ueberholt, wird der Login
explizit beendet statt dauerhaft im Zustand „Verbinde“ zu bleiben.

Die lokale Konfiguration steht in `server/settings.example.json`.
Quellen und vorkonfigurierte Systemprotokolle werden dort als feste Aliase hinterlegt;
neue Protokolle koennen als separate Belege hochgeladen werden. API-Aufrufe
duerfen keine beliebigen Protokoll-Dateipfade vorgeben. Der Server bindet ausschliesslich
an `127.0.0.1`; er ist noch kein eingerichteter Windows-/Netzwerkdienst.

### Drei getrennte Arbeitsbereiche (12.10.2026)

Die GUI startet nach der Verbindung standardmaessig in **Stammdaten verwalten**:
Bestand nach Typ, CAM-Status und Bezeichnung/ERP-Nummer filtern, Revisionen lesen,
NX-Bibliotheken inventarisieren und Kundenobjekte als Entwuerfe uebernehmen.
Auch neue Exporte, Test-Starter, Pruefung und Freigabe bleiben hier erreichbar;
taegliche Stammdatenpflege erfordert keinen NX-Versionswechsel.

Im Stammdatenbestand kann unter **Exportbelege mit NX-Datenstand vergleichen**
ein konkreter Ordnerstand gewaehlt werden. Die zusaetzliche Spalte zeigt dessen
registrierte DAT-/Legacy-Werkzeug-/Dateipaketrevisionen gegen die angezeigte
Katalogrevision. Nach aktueller/aelterer Revision, Teilbeleg/Klaerungsbedarf,
Erfassungsquelle oder fehlendem eigenen Beleg filtern. Suche und Seitenauswahl
gelten weiterhin fuer den ganzen Workspace-Bestand und schreiben nichts.
Das ist ein Vergleich aus demselben SQLite-Workspace-Lesestand, kein neuer
Dateiscan, technischer Gleichheitsnachweis, Geometriecheck oder NX-Start.
Ein fehlender eigener Exportbeleg beweist nicht, dass ein Standard-/gleichnamiges
Objekt im Ordner fehlt. Ein reiner Katalogsnapshot ist kein Exportbeleg.
DAT und Dateipaket koennen getrennte Revisionen haben; fehlende Teilbelege,
gemischte Revisionen, Auslassungen oder verlorene Paketqualifizierung bleiben
Klaerungsbedarf. Bei Stilllegung kann ein Paketbeleg null Dateien enthalten;
dies wird ausdruecklich als "kein Paketinhalt registriert" angezeigt, nicht als
vorhandenes Modell. Die Auswahl schaltet weder den aktiven Ordner noch SMB um.
Eine neu gespeicherte Revision erscheint erst nach einem separaten geprueften
Export in einem neuen Ordnerstand; CAM-/fachliche Freigaben bleiben separat.

**Stammdaten anlegen & aufbereiten** enthaelt den separaten Editor, Dateianhaenge,
CAM-/3D-Pruefung und STEP-/Tool-Assistant-Auftraege. Die Schemaauswahl hat einen
eigenen NX-Datenstand und eine eigene Bibliothek; sie beeinflusst die reine
Bestandsansicht nicht. Spalten werden weiterhin aus den nativen FORMAT-Bloecken
des gewaehlten Datenstands geladen. Neue Entwuerfe ersetzen Editorwerte nur nach
ausdruecklichem Klick; das Durchsuchen oder Wechseln der Reiter speichert nichts.
„Bearbeiten“ im Bestand oeffnet denselben Editor fuer eine neue Revision.

**Versionsupdate** buendelt den seltenen Versionswechsel/Erstimport: vorbereiteten
Quellordner und neuen Zielordner auswaehlen, Standardbibliotheken zusammenfuehren
und neue Spalten/Konflikte klaeren. Nach dem Import fuehrt die GUI zu den neuen
NX-Datenstaenden und ihren Test-Startern. Die gesamte EXE-Extraktion und
Dreiwege-Versionsmigration waren zunaechst separat. Der neue Upgrade-Wizard
unterstuetzt jetzt den vollstaendigen Ordnervergleich/-Build von NX2406 nach
NX2512 als Hintergrundauftrag. Auch die statische EXE-Extraktion ist als eigener
Auftrag eingebunden; ihr Ergebnis wird separat als SIDT-Eingabe ausgewaehlt.

Reiter sind mit Tastatur bedienbar und erhalten ungespeicherte Editorwerte sowie
die Importauswahl waehrend der laufenden Sitzung. Sie koennen ueber `#manage`,
`#prepare` und `#update` direkt angesprungen werden. Der Administrationsschluessel
wird weiterhin nicht im Browser gespeichert. Die Trennung aendert weder APIs
noch vorhandene Stammdaten, Ordner-Versionen oder ihre Pruefsummen.

Verzoegerte lesende Detailantworten fuer Aenderungsprotokoll, API-Vertrag und
NX-Datenstand werden nach einem Reiterwechsel oder bewusstem Schliessen des
Detailbereichs verworfen. Das gilt auch nach Wechsel-zurueck und fuer alte
Fehlermeldungen. Ein neuer ausdruecklicher Detailaufruf bleibt moeglich.

### Lesbare technische Datenstandpruefung

Nach **Pruefen** steht unter „Details / letztes Pruefergebnis“ zusaetzlich zum
Original-JSON eine lesbare Uebersicht. Die vorhandene Serverpruefung bleibt
unveraendert: sie speichert ihren technischen Befund. **Details** kann einen
bereits gespeicherten Bericht dagegen ohne neuen Pruef-POST oder Auditeintrag
lesen. Die Anzeige selbst prueft keine Dateien erneut und schreibt nichts.

Dateien, gefundene bekannte DAT-Bibliotheken, blockierende Befunde,
Standard-Verweishinweise und bestehende Freigabebindungen werden getrennt
angezeigt. Warnungsgruppen und betroffene DATA-Zeilen sind unterschiedliche
Zaehler. Suche/Filter gelten fuer alle Eintraege, die Anzeige hat50Zeilen pro
Seite. Bekannte Bibliotheken sind keine Vollinventur aller Fertigungsdateien.

Die Uebersicht muss zum aktuellen gespeicherten Pruefbericht passen.
Auch zueinander passende Metadaten brauchen die richtigen Text-/Zahltypen,
nichtleere FORMAT-/Befundtexte und gueltige ISO-Pruefzeitpunkte mit Zeitzone.
Listen anstelle von Kennungs-/Pruefsummentext oder unmoegliche Kalendertage
werden nicht still umgewandelt. Der Leser korrigiert keine Originaldaten.
Geaenderte Metadaten entwerten die alte Anzeige; unvollstaendige Antworten
geben keine teilweise gueltig wirkende Uebersicht frei. Rohdaten bleiben
lesbar und der Servervorgang wird nicht automatisch wiederholt. Das ist kein
NX-/Geometrie-/Maschinennachweis, keine neue fachliche Freigabe und keine
Aktivierung. Die sechs ausdruecklichen Abnahmepruefungen bleiben erforderlich.

### Parallele Anfragen und Erhalt von Editorwerten (13.10.2026)

Die Oberflaeche verwirft ueberholte Antworten innerhalb derselben Sitzung,
nicht nur nach Logout. Gemeinsame Anfragegates pruefen zuletzt gestartete
Anfrage, Verbindungs-/Dialogstand und passende Auswahl. Auch das Wechseln einer
Auswahl weg und wieder zurueck macht alte Antworten ungueltig. Ein ueberholter
Fehler ersetzt keine neuere erfolgreiche Anzeige; echte aktuelle Fehler bleiben
sichtbar. Das ist keine Abbruch-/Rollbackfunktion fuer bereits abgesendete Writes.

- **Aktualisieren:** Stammdaten, Datenstaende und Aktivzeiger erst nach dem
  gesamten Metadatenabruf gemeinsam anzeigen; aeltere Abrufe nicht zurueckspielen.
  Sichtbarer Lade-/Fehlerstand und `aria-busy`. Seit Etappe 13 liefert ein
  einzelner Workspace-Read diese Metadaten aus einer SQLite-Lesetransaktion.
  Das ist kein atomarer Dateisnapshot. Einzelne Modulfehler bei
  Upgrade-/Protokoll-/Integritaets-/Backup-/STEP-Listen werden getrennt gemeldet;
  sie verbergen nicht den erfolgreich geladenen Kernbestand. Editorwerte bleiben.
- **Pruefen/Test-Starter:** die laufenden direkten Aktionen desselben
  NX-Datenstands sperren weitere solche Aufrufe, auch nach Listenrefresh.
  Andere Datenstaende bleiben bedienbar. Alte Antworten oeffnen nach Reiter-
  oder Detailswechsel keinen ueberholten Detailbereich und ersetzen keine
  neuere Meldung. Ein beantworteter Servervorgang mit anschliessendem
  Bestandsladefehler wird getrennt gemeldet, nicht automatisch wiederholt.
  Der Browsergate ist keine serveruebergreifende Idempotenz oder Stornierung.
- **Stammdatenzeilen:** Bearbeiten, Historie und ERP-Pruefung gelten nur fuer
  eine noch aktuelle Zeile im sichtbaren Verwaltungsreiter. Abgeloeste Zeilen
  nach Filter-/Seitenwechsel, neuem Workspace-Stand oder Abmeldung duerfen
  keine neueren Editorwerte ersetzen und keine alte Navigation ausloesen.
  Bewusst angeklickte aktuelle Datensaetze bleiben normal bedienbar.
- **Speichern:** pro Sitzung ein eigener Busy-Besitzer. Nachtraegliche Eingaben
  (auch noch unvollstaendiges JSON oder eine neue Dateiauswahl) nicht durch die
  alte Speicherantwort loeschen. Fuer denselben Editor wird die bestaetigte
  Ressourcen-ID/Revision uebernommen, neue Eingaben bleiben UNGESPEICHERT. Damit
  arbeitet der naechste Save mit der richtigen Revision statt einen zweiten
  Datensatz anzulegen. Typ ist waehrend Save gesperrt; Typwechsel eines bereits
  gespeicherten Objekts weiterhin nicht erlaubt. Echte parallele ERP-/Admin-
  Aenderungen bleiben ein Revisionskonflikt, kein heimlicher Merge/Rebase.
- **Neue Verbindung/Logout:** alter Editor, Abnahme, Ordnerdialog und gespeicherte
  Vorschauen werden geleert. Alte Callback-/finally-Bloecke entsperren keinen
  gerade laufenden Save der neuen Sitzung. Der Sitzungsschluessel bleibt nur
  im Browser-Speicher; Verbindung/Logout ist kein Abbruch einer Serveroperation.
- **Bibliotheken/Schemata:** separate Gates fuer Bestand, Suchobjekte, Entwurfs-
  bibliotheken/-schemata, Geometriehashes und Import-/Katalogvorschauen. Neue
  Auswahl oder Entscheidung sperrt alte Uebernahmen. Bibliotheksdurchsuchen
  setzt keinen Editor zurueck. Katalog-/Ordnerimport und STEP-Auftragsanlage
  haben zusaetzliche clientseitige Doppelklicksperren; Server-Pins bleiben Pflicht.
- **STEP/Ergebnisse:** bei unveraenderten ID/Revision/Anhaengen bleibt ein gerade
  gewaehlter Diagnose-/Ergebniskontext samt ungespeicherter LIBRF erhalten.
  Revision-/Ressourcenwechsel setzt ihn zurueck. Alte Anhangs-/Auftrags-/STEP-
  Antworten wirken nicht auf ein anderes Werkzeug. Upload-Datei vor HTTP gegen
  Kontextwechsel pruefen; eine waehrend Upload neu gewaehlte Datei nicht loeschen.
- **Ordnerdialog/Beobachter:** Schliessen (auch Escape), anderer Zweck oder
  Reconnect invalidiert alte Ordnerantworten. Upgrade-/Backup-/Integritaets-
  Beobachter haben eigene Anfragetickets; alte Polls beeinflussen weder neuen
  Bericht noch dessen Timer. Ein temporaerer Abfragefehler startet keinen neuen
  Serverauftrag. Backup-Busy bleibt auch beim Listenrefresh waehrend noch
  unbeantworteter Jobannahme aktiv. Allgemeine Historie-/API-/Detail-Reads
  verwenden ebenfalls eine gemeinsame spaeteste Anfrage.

Tests halten Antworten isolierter Testserver kontrolliert zurueck und warten
auch auf das Ende des alten Eventhandlers, nicht nur auf das Lesen von JSON.
Die JavaScript-Gates werden zusaetzlich direkt mit einem lokalen JS-Runtime
geprueft. Keine echten NX-/Produktivdaten fuer diese GUI-Vertragspruefungen.

### Konsistenter Workspace und dynamische Datenbankschluessel (13.10.2026)

Admin-GET `/api/v1/workspace` liefert `resources`, `releases` (ohne grosse
Dateimanifeste), `active` und `snapshot` gemeinsam. Ressourcen, Release-/Starter-
Metadaten, Aktivzeiger und Auditkopf kommen aus EINER expliziten SQLite-
Lesetransaktion. Ein paralleler API-/ERP-/anderer DB-Prozess kann danach
commiten; der gelesene Stand mischt nicht alte und neue Zeilen. Dateipfade werden
nur gegen die bisherigen Schutzregeln aufgeloest, kein Source-/NX-/Dateiscan.
Bestehende Ressourcen-/Release-/Aktiv-APIs bleiben vorhanden; komplette
Workspace-Daten verlangen die Adminrolle, ERP darf nicht Freigaben einsehen.

`snapshot` enthaelt `schema_version=1`, UTC-Erfassungszeit, erfassten Auditkopf,
deterministischen `state_sha256` ueber Kopf/sichtbare Daten und
`consistency=sqlite_read_transaction`. `files_verified=false`,
`audit_verified=false` und `signed=false`: keine Datei-/Audit-/NX-Qualifizierung
oder neue Freigabe aus diesem Hash ableiten. Unverifizierte historische
Metadaten bleiben unbeglaubigt. Ordner-, Versions- und Schema-Pins werden bei
schreibenden Aktionen weiterhin separat neu geprueft.

Seit 15.10.2026 traegt die erfolgreiche Admin-Antwort zusaetzlich
`X-NX-Workspace-Contract: 1`, `X-NX-Workspace-Bytes` und
`X-NX-Workspace-SHA256`. Der Server berechnet diese ueber exakt die von ihm
serialisierten UTF-8-JSON-Bytes. Die GUI liest den Datenstrom inkrementell,
prueft Bytezahl/SHA vor dem JSON-Einlesen und verwirft ueberholte Sitzungen/
Abfragen. Fehlende Header, unerwartete MIME-Typen, falsche Laenge, defektes
UTF-8/JSON oder andere Bytes fuehren zu keiner Bestandsuebernahme. Eine
Proxy-Schicht darf diese Anwendungsheader nicht entfernen. Der bisherige
JSON-Inhalt bleibt fuer API-Clients unveraendert; neue GUI und Server zusammen
ausrollen, den Server zuerst aktualisieren. Kein stiller ungepruefter Fallback.

Dieser Uebertragungsnachweis ist vom `state_sha256` zu unterscheiden: Der
bleibt der serverseitige Metadatenpin fuer Bereichskontexte und wird nicht
durch einen neuen Erfassungszeitpunkt ersetzt. Python-Zahlen wie `1.0` oder
freie CAM-Dezimalwerte lassen sich in JavaScript anders schreiben. Deshalb
den Empfang nicht durch erneutes Serialisieren des JS-Objekts hashen.
Die Pruefsumme ist weder Signatur noch unabhaengiger Nachweis ueber SQLite,
Dateien, Auditkette, Transportvertrauen oder NX-Tauglichkeit; obige False-
Kennzeichen bleiben verbindlich. Keine automatische Freigabe daraus ableiten.

Vor Veroeffentlichung prueft die GUI zudem den gesamten Metadatenvertrag:
eindeutige Ressourcen-/Release-IDs, Typen/Revisionen/Statusfelder, vorhandene
optionale Paket-/Starterstrukturen, Auditkopf und konsistenten lokalen
Aktivzeiger. Bei Fehler bleiben die letzte bestaetigte Ansicht und
ungespeicherte Editorwerte erhalten, ohne Teile der neuen Antwort zu mischen.
Die Fehleranzeige kennzeichnet, dass dies kein frisch bestaetigter Bestand ist.
Ein erster fehlgeschlagener Workspace-Read gibt auch keinen Login frei.
Einzelne Bereichssynchronisationen (einschliesslich Abnahmeansicht) werden
getrennt gemeldet: Ihr Fehler verhindert nicht die Aktualisierung des gueltigen
Kernbestands und der uebrigen Bereiche. Serverseitige Revisions-/Datei-/
Commitpruefungen und dauerhafte Schreibbelege bleiben unveraendert erforderlich.

Die GUI benutzt ausschliesslich diesen unterstuetzten Snapshotvertrag. Ein
alter Server ohne Endpoint oder ein unpassender Vertrag meldet einen Fehler,
statt still wieder drei zeitlich verschiedene GETs zu mischen. Vorhandene gute
Anzeige bleibt bei spaeterem Fehler erhalten. Der zentrale Status zeigt den
erfassten Metadaten-/Auditstand; Detail- und Joblisten behalten eigene Gates.
Bei Servercode-Aktualisierung neu starten und neuen Sitzungsschluessel verwenden.

Gemeinsame `NXData`-Helfer behandeln dynamische Spalten/Objekt-/Referenzschluessel
als eigene JSON-Eigenschaften, nicht als JavaScript-Prototypen. Auch
`__proto__`, `constructor`, `prototype`, `toString` bleiben literale Daten und
kommen durch JSON-/DAT-Export. Sie gelten nicht als reservierte CAM-Kennungen.
Neue verschachtelte Maps haben keinen Prototyp; vorhandene Objekte werden
erhalten, unpassende Arrays/Skalare/Nullzweige NICHT heimlich ersetzt.

Native Feldeditoren, Importkonflikte, explizite Update-/Exportspaltenwerte,
Schemaentscheidungen und Referenzkorrekturen nutzen diese Regeln. Fehler beim
Ausfuellen sperren alte Vorschauen und werden sichtbar gemeldet. Werte werden
weiterhin nur ausdruecklich bestaetigt; kein automatischer Standard-Override,
Spaltenwert, Einheitenwechsel oder CAM-Freigabe. Vertrags-/GUI-Tests verwenden
synthetische native Tabellen und einen explizit synthetischen Geometriebeleg.

### Vorlagen, UI-Standards und Maschinen-/Spannmitteldateipakete

Der Katalog hat jetzt zusaetzlich `template` (NX-Vorlagenpaket) und
`ui_standard` (NX-Oberflaechenstandard): insgesamt 23 Typen, weiterhin 41 native
DAT-Bibliotheksadapter. Dateipakete sind keine geratenen DAT-Datensaetze.
Sie verwenden dieselben revisionsgebundenen Stammdaten, unveraenderlichen
Dateianhaenge, Audit-Belege und Sicherungen; keine separate unversionierte Ablage.

Neu seit 15.10.2026: **Paketdateien technisch vorpruefen** im Dateipaketeditor
liest ausschliesslich die ausgewaehlten Paketdateien. Sichtbar werden alle
Pfad-/Basename-/Doppelzuordnungsfehler, widerspruechliche Verzeichnisschreibung
und Datei-/Ordnerkollisionen, fehlende/veraenderte Bytes und ungueltiges XML.
Solche Strukturkollisionen werden jetzt auch beim Speichern eines Entwurfs
abgewiesen, nicht erst spaeter beim Export. Historische Revisionen bleiben
unveraendert; Pfade oder interne binaere Referenzen werden nie umgeschrieben.

Die Vorpruefung allein aendert keine Hashbindung, fachliche Bestaetigung,
Revision, Herkunft, Dateibesitz oder NX-Datei. **Dateihashes an Beleg binden**
verlangt eine fehlerfreie frische technische Vorpruefung und aktuelle identische
Anhangsmetadaten; erst dann werden die SHA im Editor eingetragen. Wie bisher
werden bei neuer Hashbindung die vier fachlichen Bestaetigungen zurueckgesetzt.
Diese, der NX-Pruefbeleg und das ausdrueckliche Speichern bleiben separat.
Geometrie, binaere Abhaengigkeiten, PAX-Suchpfadaufloesung, Dateibesitz und
NX-Versionskompatibilitaet werden durch diese technische Stufe nicht freigegeben.

Der neue Admin-Endpunkt `POST /api/v1/file-packages/preflight` nimmt `kind`,
`nx_version`, `asset_ids` und `entries` entgegen, keine Qualifizierungsflags.
Er liefert einen an den exakten Antrag gebundenen, rekonstruierbaren SHA-Bericht
mit Dateibefunden und Fehlerliste. Keine Signatur oder atomare Dateisnapshot-
Zusage. Pro Datei maximal20MiB, insgesamt128MiB ausgewaehlte Dateibytes;
das Gesamtlimit wird vor den Dateireads geprueft. Bounded Handle-/Groessen-/SHA-
Pruefung statt ungebremstem Anhangsleser. Auch fehlerhafte Dateien werden nur
einmal je Anhang gelesen; wiederholte Zuordnungen bleiben Fehler. Nicht im
Paket verwendete Editor-Anhangskennungen werden nur gezaehlt, ihre Dateien
und Metadaten nicht gelesen oder als verifiziert behauptet. Die ERP-Rolle
erhaelt keinen Zugriff auf diesen Admin-Endpunkt.

Pfad-, Versions-, Anhangs-, Datensatz- oder Sitzungswechsel entwerten alte
Pruefantworten einschliesslich der eigenen Detailansicht, auch nach Ruecksetzen
auf dieselben Werte. Andere Editoreingaben und historische Daten werden nicht
durch verzoegerte Antworten ersetzt.

Bei der **Uebernahme bestehender Kunden-Dateipakete** werden seit 15.10.2026
auch gemeinsame Verzeichnisnamen der Auswahl vor Beginn der Anhangskopien geprueft. Eine
widerspruechliche Schreibung oder Datei-/Ordnerkollision blockiert bereits die
Vorschau. Es werden nur die exakten kanonischen Pfade aus dem Release-Manifest
akzeptiert, keine Gross-/Kleinschreibungs-Aliase. Ein blockierender Baseline-
Elternpfad wird als Konflikt gemeldet, ohne einen Unterpfad darunter zu sondieren.
Fehlende/verlorene Quellen und nicht lesbare Baseline-Pfade geben keinen Besitz.

Quell-Paketdateien werden erst nach der bekannten20MiB-Grenze mit stabilem
Handle/Groesse/Manifest-SHA gelesen. Baselines sind Vergleichsdaten und werden
nicht als Anhaenge kopiert: leere Platzhalter und groessere Siemens-Dateien
bleiben vergleichbar. Ihr SHA wird mit dem gemeinsamen Fingerprint-Leser in
1MiB-Bloecken gebildet, inklusive Status-/Groessenvergleich vor/nach dem Read
und dessen1TiB-Sicherheitsgrenze. Die20MiB-Anhangsgrenze wird nicht auf die
Baseline uebertragen. Diese Pruefungen gelten auch im letzten SQL-Besitzgate;
spaete Fehler behalten den bisherigen Phasenkontext im Fehlerbeleg. Kein
stiller Neustart und keine automatische Loeschung vorhandener Diagnoseanhaenge.
Ein blockierter Baseline-Vergleich ist im negativen Bericht explizit als
`read_error` mit leerem Vergleichspfad markiert, nicht als bestaetigte Abwesenheit.

Zulaessige relative Standardpfade:

| Kategorie | Dateipaket-Verzeichnisse |
| --- | --- |
| Vorlagen | `MACH/resource/template_part/`, `MACH/resource/template_set/`, `UGII/templates/` |
| UI-Standards | `UGII/menus/roles/`, `UGII/menus/profiles/` |
| Maschinen | `MACH/resource/library/machine/installed_machines/` |
| Spannmittel | `MACH/resource/library/fixture_automation/graphics/` |

Basenames bleiben unveraendert; Unterordner sind moeglich. Unterstuetzt sind
PRT, PAX, MTX, JPG/JPEG/PNG, OPT, XML, DPV, MEN, TBR und RIBBON. XML-artige
Dateien werden auf Wohlgeformtheit, alle Anhaenge auf Groesse/SHA geprueft.
Das ist weder eine geometrische noch eine semantische NX-Pruefung. Menue-/
UI-Dateien koennen auf Befehle verweisen: nur vertrauenswuerdige, fachlich
gepruefte Inhalte verwenden. Der Server fuehrt keine Paketinhalte aus.
Ausfuehrbare Programme/Journals, TCL-Posts, beliebige Konfigurationspfade,
vollstaendige Maschinen-/Post-/Simulationskonvertierung bleiben ausserhalb
dieses ersten Dateipaketadapters. Maximal 100 Dateien/Anhaenge je Datensatz,
1 Byte bis 20 MiB je Datei. Die Bestandsauswahl ist jetzt ueber den gesamten
Bestand durchsuchbar, mit 100 Dateien pro Seite und erhaltener Mehrseitenauswahl.

Fuer bestehende Kundendateien im Verwaltungsreiter „Vorhandene
Kunden-Dateipakete uebernehmen“ verwenden: verwalteten Datenstand und Kategorie
waehlen, Dateien laden/markieren und Vorschau pruefen. Die passende lokale
Siemens-Installationsbasis ist erforderlich; vorhandene SIDT-Overlays werden
beruecksichtigt. Unveraenderte Siemens-/SIDT-Dateien und bereits beanspruchte
Pfade sind ausgeschlossen. Auswahl, Manifest, Standarddateihashes und ggf.
Revision des bestehenden Entwurfs werden an die Uebernahmevorschau gebunden.
Die Uebernahme erzeugt ausschliesslich einen Entwurf und unveraenderliche
Dateikopien als Anhaenge; Quelle und Releases bleiben unveraendert.

Unmittelbar vor dem Entwurfs-/Ergebnis-/Auditcommit werden diese Bindungen
innerhalb derselben SQL-Schreibtransaktion erneut geprueft: Quellrelease,
Manifest und Katalog, aktueller Dateibesitz, vollstaendiger Zielentwurf sowie
Quellbytes und effektive Siemens-/SIDT-Baseline inklusive ihrer Wurzelpfade.
Ein neuer SIDT-Overlay oder eine fehlende Baseline wird nicht als neue
Kundenanpassung geraten. Die GUI akzeptiert nur den vollstaendigen finalen
Pruefvertrag. Eine zwischenzeitliche Aenderung verhindert die Uebernahme;
bereits kopierte, auditierte Anhaenge bleiben als Teilfehlernachweis erhalten,
ohne Dateibesitz, Entwurfsrevision oder erfolgreiches Auftragsergebnis.
Dies ist eine abschliessende Momentaufnahme, keine dauerhafte Dateisystemsperre
und keine automatische NX-Qualifizierung.

Maschinen/Spannmittel brauchen zuvor einen passenden gespeicherten nativen
Kunden-DAT-Entwurf. Weder ein Dateiname noch eine PRT erfinden einen Maschinen-
oder Spannmittel-Datensatz. Bestehende Siemens-Katalogobjekte duerfen keine
Dateieigentuemer werden. Vorhandene explizite Graphics-/Modellpaketbindungen
muessen zuerst bewusst geprueft/geloest werden, damit nicht zwei widersprechende
Pfadmechanismen nebeneinander existieren.

Neue Pakete koennen auch im Aufbereitungsreiter angelegt werden: Typ waehlen,
Dateien hochladen/zuordnen, „Zugeordnete Dateien laden“, Standardpfad auswaehlen
und Paketzuordnungen ergaenzen. Vor Freigabe sind vier ausdrueckliche Pruefungen
erforderlich (Pfade/Version, Abhaengigkeiten/Suchpfade, fachlicher Inhalt und Test
in genau dieser NX-Version), mindestens 20 Zeichen Beleg und die exakten
Dateihashes. Aenderungen an Version, Zuordnungen oder technischen Daten loesen
die GUI-Pruefbindung. Alte Antworten duerfen neue Pruefungen nicht vortaeuschen.

Anhangsaenderungen wirken jetzt bereits beim Eingeben der technischen ID-Liste,
nicht erst beim Verlassen des Feldes: Hashbindungen und Pruefcheckboxen werden
zurueckgesetzt, alte Metadaten-/Hashantworten verworfen und die Paket-Dateiauswahl
geleert. Das gilt auch fuer Entfernen/Wiederzuordnen und neue Uploads. Bereits
definierte Paketzeilen/Zielpfade bleiben erhalten; fehlende Anhaenge werden je
Zeile angezeigt und muessen ausdruecklich wieder zugeordnet oder aus dem Paket
entfernt werden. Datei, alte Ressourcenrevision und Elternrelease werden dadurch
nicht geloescht/geschrieben. Wiederzuordnen stellt keine alte Freigabe wieder her.
„Dateihashes an Beleg binden“ verlangt ein aktiviertes Paket und alle Paketdateien
in der aktuellen Anhangsliste. Unvollstaendige/ungueltige technische JSON-Eingaben
bleiben erhalten und verursachen keine Hashbindung oder erratene Zuordnung.

Die Zuordnungsliste im Dateipaketeditor hat eine lokale Suche nach Zielpfad,
geladenem Dateinamen oder Anhang-ID und Seiten mit je20Zeilen. Alle anderen
Zuordnungen bleiben vollstaendig im Formular; Suche/Blaettern aendern weder
Pfade noch Hashbindungen oder Pruefcheckboxen. Eine neu hinzugefuegte Datei
wird auch nach einer Suche sichtbar auf der letzten Seite angezeigt.
Paketpfade werden nicht automatisch umbenannt oder korrigiert.

„Zuordnung entfernen“ verlangt eine Bestaetigung und betrifft genau diese
ungespeicherte Paketzeile, nicht die Datei, die separate Anhangsliste oder
historische Revisionen. Die Entfernung wird erst mit einem separaten Save
zur neuen Stammdatenrevision. Veraltete Zeilenfelder/-aktionen aus anderen
Editoren, Suchseiten oder verborgenen Arbeitsbereichen duerfen weder aktuelle
Paketzuordnungen noch neue Pruefbindungen aendern. Nach einer bewusst geaenderten
Zuordnung gelten wie bisher die technische Neuvorpruefung und neue Bestaetigungen.

Beim Typwechsel eines neuen Entwurfs zu einem Datentyp ohne Dateipaket-
Unterstuetzung bleibt ein vorbereitetes Paket jetzt sichtbar. Zuordnungen
werden nicht still geloescht oder auf neue Pfade umgedeutet. Die Paketfelder
sind schreibgeschuetzt; nur „Dateipaket fuer diesen Datensatz verwalten“ kann
ausdruecklich deaktiviert werden. Alternativ zum passenden Typ zurueckwechseln.
Solange das nicht passende Paket aktiv bleibt, wird Speichern bereits vor
Anlage eines Schreibbelegs mit einem klaren Hinweis angehalten.

Deaktivieren entfernt keine Datei oder Anhangszuordnung. Vorbereitete
Paketzeilen bleiben nur im aktuellen Editor; beim Speichern ohne aktives
Paket werden sie nicht uebernommen. Rueckwechseln zum passenden Typ behaelt
Pfade/Zuordnungen, stellt aber keine alten Hash-/Pruefbindungen wieder her.
Gespeicherte Datensatztypen und serverseitige Exportregeln bleiben geschuetzt.

„Neuen Export ableiten“ zeigt Paketaktionen und alle geplanten Entfernungen.
Nur freigegebene Pakete werden in einen NEUEN Ordner kopiert; bestehende
unverwaltete/Standarddateien werden auch bei gleichen Bytes nicht beansprucht.
Eigene bereits exportierte oder nachweislich uebernommene Dateien koennen in
der neuen Kopie aktualisiert werden, nur wenn ihre bisherigen Hashes passen.
Stilllegung/entfallene Zuordnungen entfernen nur Dateien aus dieser neuen Kopie;
Elternrelease, Anhaenge und alte Revisionen bleiben erhalten. Eine bestehende
Paketbindung nicht durch Abschalten verstecken, sondern ausdruecklich stilllegen.

Beim verwalteten NX2406-zu-NX2512-Upgrade bleibt nur bytegleich nachgewiesener
Paketbesitz erhalten. Kunden-`UGII/templates` gehoert jetzt ebenfalls zum
geprueften Erweiterungsumfang; unveraenderte Installationsdateien werden nicht
als Kundenabweichungen kopiert. Explizit beim Update ersetzte/ausgelassene
Paketdateien werden gegen automatischen Rueckexport gesperrt. Dafuer den
Upgrade mit einer ausdruecklichen Kundendatei-Entscheidung erneut durchfuehren.
Die Stammdatenrevision und alte Pruefbelege werden NICHT automatisch geaendert:
Paket auf die neue Version als Entwurf umstellen, Checks/Hashes loesen, erneut
in NX pruefen und erst danach freigeben. Besitz ist keine Versionsqualifizierung.

API (Adminrolle): `GET /api/v1/releases/{id}/file-packages?kind=...`,
`POST .../file-packages/preview`, `POST .../file-packages/adopt`; neue/veraenderte
`file_package`-Daten werden ueber die bestehenden Ressourcenendpunkte versioniert.
ERP darf diese Administratoraktionen nicht ausfuehren; Vorlagen/UI-Standards
sind keine ERP-Upsert-Artikel. Dateipaketmetadata und Anhaenge sind Teil der
Integritaetspruefung und vollstaendigen Sicherung/isolierten Wiederherstellung.

Noch keine zentral erzwungene Firmenrolle oder Benutzerprofilverteilung:
der Teststarter hat weiterhin ein isoliertes Profil; eine verwaltete MTX-Rolle
kann jetzt ausdruecklich ausgewaehlt werden (siehe unten). Ohne Auswahl bleibt
der bisherige isolierte Standard erhalten. Vorhandene Benutzerprofile werden
nicht geloescht oder ueberschrieben.
Echte NX-/STEP-/Assistant- und Maschinenqualifizierung erfolgt spaeter mit
dem Benutzer; die automatisierten Nachweise verwenden synthetische Dateien.

Seit15.10.2026 verwenden Protokollscan, Dateipaketpruefung/-uebernahme und
Anhangsdownload einen gemeinsamen begrenzten Byte-Leser. Er liest maximal
die konfigurierte Grenze plus ein Pruefbyte und vergleicht Dateistatus vor/nach
dem Lesen sowie den Status des tatsaechlich geoeffneten Handles
([Python `os.fstat`](https://docs.python.org/3.10/library/os.html#os.fstat)).
Kurze Reads, Wachstum, Dateiwechsel und Leseverlust liefern keinen erfolgreichen
Teilinhalt. Registrierte Anhaenge und uebernommene Dateien brauchen weiterhin
exakt die gespeicherte Groesse/SHA; Originalbytes werden nicht umgeschrieben.

XML-Dateipakete (`.pax`, `.xml`, `.mtx`, `.dpv`) erhalten jetzt auch beim
Speichern und in der Bestandsuebernahme die20MiB-/50000Knoten-/Tiefe64-Grenzen
und DTD-Sperre. Die allgemeine XML-Syntaxpruefung erfindet keine neuen
NX-Felder oder Vorlagenschemata. Fehler werden als Konflikt angezeigt;
alte Anhaenge/Revisionen bleiben erhalten. Korrigierte Datei neu hochladen,
sichtbar zuordnen und Paketzuordnung/Pruefungen ausdruecklich erneuern.
Bricht die Dateipaketplanung ab, kennzeichnet der PAX-Teilbericht auch die
Abhaengigkeitsanalyse als unvollstaendig; der Hauptkonflikt wird nicht doppelt
gezaehlt. Keine automatische NX-Freigabe oder atomarer Dateisystem-Snapshot.

### NX-Teststart mit Firmenrolle (15.10.2026)

Im Reiter **Stammdaten verwalten** den Bereich **NX-Teststart mit Firmenrolle**
verwenden. Einen vollstaendigen NX2512-Datenstand, freigegebenen UI-Standard und
eine MTX-Datei unter `UGII/menus/roles/` waehlen. Die exakt freigegebene Revision
muss zuvor in diesen Datenstand exportiert worden sein; gleiche Bytes oder
beim Versionsupdate erhaltener Dateibesitz allein sind keine Qualifizierung.
Dann **Startprofil-Vorschau pruefen**, **Geprueftes Testpaket erstellen** und
**Erstelltes Testpaket herunterladen**. Keine persoenlichen Profile importieren.

Die Vorschau bindet Release/Manifest/Katalog, die komplette UI-Ressourcenrevision
und den vorherigen Testpaketstand. Vor dem SQL-/Auditcommit werden Metadaten
und Releasebytes erneut geprueft. Alte Pakete bleiben unveraendert vorhanden;
ein identischer bestaetigter Retry erzeugt keine zweite Auditbuchung. Historische
Rollenauswahl bleibt bei einem Skriptupdate und isoliertem Restore erhalten,
ohne eine spaetere geaenderte Stammdatenrevision automatisch zu uebernehmen.
Ohne Rollenauswahl kann ausdruecklich wieder ein Standard-Testpaket erstellt
werden; das historische Rollenpaket wird dadurch nicht geloescht.

Im entpackten Paket `Start-NX2512-Test.cmd` verwenden. `-CheckOnly` prueft
Rollendatei, Manifest und Konfiguration, ohne NX zu starten. Das Paket hat einen
eigenen rollenabhaengigen Testordner; ein bereits anders gebundener Testordner
wird auch bei explizitem `-TestRoot` nicht still umgebunden. Nur der NX-
Kindprozess bekommt `UGII_DEFAULT_ROLE` mit dem geprueften lokalen MTX-Pfad.
Keine Registry-/Systemvariablen-, persoenlichen Profil- oder Produktionswrites.

Der grundsaetzliche Rollenmechanismus wird in der
[Siemens-Administrationsunterlage, Kapitel9, Seite14](https://media.plm.automation.siemens.com/ru_ru/nx/book/chapter-9.pdf)
beschrieben. Diese aeltere Unterlage ist kein NX2512-Qualifizierungsnachweis;
die neue Implementierung wurde mit synthetischer MTX und einem harmlosen
Windows-Umgebungsrekorder getestet. Der echte NX2512-/Firmenbenutzertest folgt
spaeter. Das ist noch keine zentrale Firmenrollen-Durchsetzung oder ein
Verteilclient; lokale NX-Anpassungen werden nicht geloescht oder verhindert.

Admin-API: `POST /api/v1/releases/{id}/test-launcher/ui-profile-preview`
mit `resource_id`/`path` (beide `null` fuer isolierten Standard), dann
`POST .../test-launcher/ui-profile` mit demselben Inhalt und `preview_hash`.
Das bestehende Paketdownload liefert den aktuell ausgewaehlten Paketstand.
ERP darf weder Vorschau noch Auswahl ausfuehren. Die Auswahl schreibt nur
Testpaketmetadaten/Audit, keine Ressourcenrevision oder NX-Freigabe.

### Deklarierte PAX-Abhaengigkeiten im Export (15.10.2026)

Die Exportvorschau prueft jetzt `Filename`, `Configuration` und
`PreviewImage/@location` innerhalb der Eintraege ausdruecklich exportierter
Dateipaket-PAX. Der Bereich **Deklarierte PAX-Dateiverweise im geplanten Export**
zeigt jeweils50 Verweise, Originaltext, vorgesehenes Ziel und Datei-SHA.
Ein fehlender/unbekannter Verweis erscheint als Konflikt mit PAX-Pfad und
Stammdatensatz; **Geprueften Export als neuen Ordner erstellen** bleibt gesperrt.
Keine Datei wird durch die Vorschau kopiert, geaendert oder freigegeben.

Relative Dateiverweise werden in diesem Adapter neben der deklarierenden PAX
aufgeloest; sichere Unter-/Elternordner innerhalb des Wurzelordners sind moeglich.
Bekannte CAM-Variablen zeigen auf die geplanten `MACH/resource`-Bereiche.
`UGII_BASE_DIR`, `UGII_ROOT_DIR` und `UGII_CAM_BASE_DIR` brauchen die konfigurierte
Siemens-Referenzinstallation der exakten NX-Version. Ihre referenzierten Dateien
werden mit Wurzelpfad, Groesse und SHA an die Vorschau gebunden, nicht kopiert.
Absolute lokale/Netzpfade, Teamcenter-`@DB`-Verweise, unbekannte Suchvariablen
(auch nicht ausdruecklich konfigurierte `UGII_TEMPLATE_DIR`) und Wurzeltraversal
werden nicht geraten oder umgeschrieben. Originale Deklarationen bleiben erhalten.

Der virtuelle Zielbestand beruecksichtigt vorhandene Eltern-Dateien, alle
geplanten Paket-/Grafikdateien, neue native DAT-Ausgaben und geplante Entfernungen.
Ein Modell, das im neuen Stand entfernt wird, ist deshalb kein gueltiges Ziel
einer weiter exportierten PAX. Andere freigegebene Pakete koennen eine referenzierte
Datei bereitstellen; ein zurueckgestellter Entwurf tut das nicht.
Unveraenderte, unverwaltete Siemens-PAX werden durch diesen Adapter nicht neu
interpretiert oder als Kundeneigentum beansprucht.

Vor der finalen SQL-Registrierung werden die referenzierten Bytes im NEUEN
Kandidaten und gebundene Installationsdateien nochmals geprueft. Drift verhindert
den Commit; eine bereits entstandene neue Teilkopie bleibt als Diagnose erhalten,
Quelle, historischer Elternstand und Aktivzeiger bleiben unveraendert.
Der XML-Leser verbietet DTDs und begrenzt Dateigroesse, Knotenzahl, Tiefe und
Referenzanzahl; bei Leserfehlern wird der Vergleich nicht als vollstaendig behauptet.
Der DTD-Hook folgt dem
[Python-TreeBuilder-Vertrag](https://docs.python.org/3/library/xml.etree.elementtree.html#xml.etree.ElementTree.TreeBuilder).

Das ist eine technische Pruefung der unterstuetzten textuellen Deklarationen,
keine vollstaendige NX-PAX-Schema-/Suchpfad-/UI-Pruefung, kein Nachweis aller
Binaer-/Baugruppenabhaengigkeiten und kein automatischer NX-Test. Die vier
menschlichen Paketchecks, versionsbezogenen Belege und reale Abnahme bleiben
erforderlich. Eine syntaktisch/technisch passende PAX muss spaeter in genau
dieser NX-Version getestet werden. Alte Software-Vorschauen neu erstellen.

### CAM-Vorlagenpfade und Testpakete aktualisieren (15.10.2026)

In der Datenstandliste gibt es jetzt **Test-Starter aktualisieren** auch fuer
vollstaendige, technisch blockierte NX2512-Kandidaten. Danach den aktuellen
Paketstand neu herunterladen und vollstaendig in einen neuen lokalen Ordner
entpacken. Alte Paketrevisionen bleiben unveraendert vorhanden. Eine gesperrte
fachliche Abnahme wird durch das Aktualisieren des Starters nicht freigegeben.
Vorhandene Rollenbindung bleibt bei der Skriptaktualisierung erhalten.

Neue Teststarter filtern auch den Programmsuchpfad (`PATH`) des NX-Kindprozesses:
zuerst die lokalen NXBIN-/UGII-Verzeichnisse, danach vorhandene, absolut lokale
Verzeichnisse des bisherigen Suchpfads. Leere/relative Eintraege, W:, UNC-/
Geraetepfade, nicht aufgeloeste Variablen und vom Offline-Pfadschutz abgewiesene
Verzeichnisse werden nicht uebernommen. Doppelte Schreibweisen werden ohne
Aenderung der Reihenfolge zusammengefasst. Unsichere explizite NX-Verzeichnisse
brechen den Start ab; insbesondere darf der Installationspfad kein Semikolon
als PATH-Trennzeichen enthalten. Keine automatische Pfadkuerzung oder Umbenennung.
Die globale Windows-/Benutzer-/Elternprozess-Umgebung bleibt unveraendert.

Fuer bereits heruntergeladene Pakete: zuerst im Server bewusst **Test-Starter
aktualisieren**, das neue ZIP vollstaendig in einen neuen lokalen Ordner
entpacken und dessen `Start-NX2512-Test.cmd` verwenden. Alte Archive und deren
entpackte Starter werden nicht nachtraeglich umgeschrieben. Das ist eine
Suchpfadkorrektur, keine vollstaendige DLL-/NX-Sandbox oder reale NX-Abnahme.
Eine gewaehlt gebundene MTX-Rolle und die bekannten CAM-Vorlagenpfade werden
weiterhin separat gesetzt; firmenweite UI-Durchsetzung bleibt ein eigener Schritt.

Der NX-Kindprozess bekommt die CAM-Vorlagen-/Konfigurationspfade ausdruecklich
aus dem getesteten Staging-Ordner: `UGII_CAM_TEMPLATE_PART_DIR`, die metrische
und englische Variante, `UGII_CAM_TEMPLATE_IMAGE_DIR`, `UGII_CAM_TEMPLATE_SET_DIR`
und `UGII_CAM_CONFIG_DIR`. So koennen geerbte lokale NX2406-Pfade diese bekannten
Bereiche nicht still umlenken. Die Pfadbeziehungen wurden zusaetzlich in der
lokalen NX2512-Datei `UGII/ugii_env_ug.dat` bestaetigt; das ersetzt keinen echten
NX-Start-/Maschinen-/Vorlagennachweis. Nur Kindprozesswerte, keine System-/
Registryvariablen oder Originaldaten werden geaendert.

Der vorhandene Adapter fuer die deutsche CAM-Maschinenpalette prueft jetzt
ALLE passenden PreviewImage-, Filename- und Configuration-Verweise statt
nur des ersten Knotens. Mehrere gueltige Verweise bleiben erhalten; jeder
einzelne muss auf eine existierende Datei innerhalb des Staging-Ordners zeigen.
Bekannte CAM-Variablen werden nur fuer die Pruefung aufgeloest; XML-Inhalte
werden nicht umbenannt/umgeschrieben. Unbekannte Variablen, fehlende Dateien,
Verweise ausserhalb des Snapshots oder aus dem Arbeitsverzeichnis geratene
relative Pfade sperren diesen spezifischen Maschinenpalettenadapter.
Das ist keine allgemeine Interpretation jeder Siemens-PAX-Variante.

System-/Kunden-/erzeugte CAM-PAX verwenden denselben begrenzten XML-Leser:
20MiB Datei-/Zeichenlimit, DTD-Verarbeitung verboten, externer Resolver null.
Keine Entity-/Schema-/Netzressourcenauflösung. Diese Einstellungen folgen
[Microsofts DTD-Vertrag](https://learn.microsoft.com/en-us/dotnet/api/system.xml.xmlreadersettings.dtdprocessing?view=netframework-4.8.1)
und dem [Resolver-Vertrag](https://learn.microsoft.com/en-us/dotnet/api/system.xml.xmlreadersettings.xmlresolver?view=netframework-4.8.1).
`-CheckOnly` prueft jetzt auch die kontrollierten Umgebungsbindungen,
unbekannte geerbte Netzverweise und den lokalen Executable-Pfad, ohne NX zu
starten oder Laufzeitprofile anzulegen. Ein CheckOnly-Erfolg ist keine
semantische/geometrische Qualifizierung.

Offen bleibt die allgemeine Erreichbarkeit neuer CAD-/Zeichnungs-PAX unter
`UGII/templates` und die zentrale Verteilung aller Anwendungsvorlagen.
Der Starter setzt `UGII_TEMPLATE_DIR` NICHT pauschal auf einen eventuell
unvollstaendigen Kundenordner; damit wuerden moeglicherweise installierte
Siemens-Vorlagen ausgeblendet. Die
[aeltere Siemens-Administrationsunterlage, PDFseite16](https://media.plm.automation.siemens.com/ru_ru/nx/book/chapter-9.pdf)
beschreibt diesen Mechanismus, ist aber kein Beleg fuer alle Anwendungen in
NX2512. Hierfuer sind ein vollstaendiger Vorlagen-/Abhaengigkeitsadapter und
der spaetere echte NX-Test erforderlich, keine erratene globale Umstellung.

### Geplante DAT-Werte vor dem Export vergleichen (15.10.2026)

Unter **Stammdaten verwalten** einen Elternstand waehlen und **Neuen Export
ableiten** oeffnen. Zuerst eine vollstaendige exportierbare Vorschau pruefen;
neue Spaltenwerte ausdruecklich bestaetigen und Konflikte loesen. Dann
**Geplante DAT-Werte vor dem Export vergleichen** aufklappen und die
geplanten DAT-Objekte laden. Bibliothek, Aenderungsart und Suche filtern
alle betroffenen Objektgruppen; jeweils bis zu50 Treffer werden angezeigt.
Die Suche beruecksichtigt komplette Werte beider Seiten, nicht nur Kurztexte.

**Vorher-/Nachher-Werte** waehlt eine vollstaendige Objektgruppe. Die Seite
**Vorher** zeigt Werte im unveraenderten Elternordner, **Nachher** die tatsaechlich
geplanten Zielwerte. Jeweils20 Zeilen mit allen FORMAT-Feldern, Block-ID und
Originalposition innerhalb dieser Seite laden; weitere Seiten bleiben an
dieselbe Vorschau und denselben Gruppenhash gebunden. Auch mehrteilige Gruppen,
neue Spalten, leere Werte und literale JSON-Namen wie `__proto__` bleiben Daten.
Auf einer nicht vorhandenen Seite wird ausdruecklich kein Objekt angezeigt.

Gruppen werden nach Bibliothek und exaktem LIBRF-/NAME-Schluessel verglichen,
nicht anhand von Bezeichnungen. Die Klassifikation nutzt die bestehenden
reihenfolgenunabhaengigen nativen Gruppenhashes: neu, geaenderte Werte,
entfernt oder unveraendert. Es wird KEINE RTYPE-/SEQ-/Zeilenpaarung geraten.
Die Zeilenanzeige behaelt die jeweilige Seitenreihenfolge. Dateihashes werden
separat angezeigt; geaenderte Byteformatierung ist nicht automatisch eine
Wertaenderung. Paketdateien, Grafiken, geplante Dateientfernungen und bekannte
Verweispruefungen stehen weiterhin in der Hauptvorschau. Der DAT-Vergleich
behauptet nicht, alle XML-/PRT-/Konfigurationsaenderungen abzudecken.

Jeder Abruf erstellt denselben Exportplan erneut und prueft seinen Vorschauhash.
Geaenderte Quelle, Stammdaten oder explizite Spaltenwerte sperren alte Seiten.
Filter-/Seitenauswahl, ABA-Wechsel, neue Vorschau, Schliessen und Abmeldung
verwerfen spaete Antworten. Alle Feld-/FORMAT-/Hashvertraege einer Antwort
werden geprueft, bevor eine Teilanzeige entstehen kann. Kein Ressourcen-/
Release-/NX-Dateiwrite, keine automatische Freigabe oder Aktivierung.

Die Auswertung ist auf256MiB UTF-8-Tabellentext,500000 Zeilen,5000000 Zellen
und4MiB pro Antwort begrenzt. Bei Ueberschreitung oder Exportkonflikten wird
kein unvollstaendiger Vergleich als vollstaendig ausgegeben. Die Hauptvorschau
und die bestehenden Exportregeln bleiben massgeblich; dieser Leser ist kein
Auditnachweis, kein geometrischer NX-Test und keine automatische Konvertierung.

Admin-API: `POST /api/v1/releases/{id}/derive-preview/objects` mit `defaults`,
`preview_hash`, optional `library_id`, `change`, `query`, `offset`, `limit`;
`POST .../derive-preview/rows` mit denselben Planbindungen und exaktem
`library_id`, `key`, `side`, `group_sha256`, `offset`, `limit`. Fehlende Seiten
verwenden einen `null`-Gruppenhash. ERP hat keinen Zugriff. Bei laufender
Sicherung gilt weiterhin die bestehende POST-Pause, obwohl diese Abrufe
keine Nutzdaten schreiben. Beim Softwareupdate alte Vorschauen neu pruefen.

### Vollstaendigen Dateiplan und private Anhaenge pruefen (15.10.2026)

Nach einer exportierbaren Hauptvorschau **Alle geplanten Dateien und private
Anhaenge pruefen** aufklappen und **Dateiplan laden** waehlen. Die Ordneransicht
zeigt alle geerbten und geplanten Dateien, nicht nur DAT-Objektaktionen:
relativer Pfad, Dateityp, neu/geaendert/entfernt/unveraendert, Groesse und
SHA-256 vor und nach dem Export. Filter, Volltextsuche und Seiten mit50
Treffern sind an dieselbe Exportvorschau gebunden. Die Gesamtsummen und der
geplante Manifesthash beziehen sich immer auf den gesamten Ordner.

Die zweite Ansicht zeigt gespeicherte Metadaten der aktuellen Anhaenge,
die nicht kopiert werden: zum Beispiel STEP-Quellen neben ausdruecklich
zugeordneten PRT-Dateien, Entwuerfe, Stilllegungen oder reine ERP-Stammdaten.
Sie prueft NICHT erneut die Dateibytes privater Anhaenge und ist kein
Integritaetsnachweis. Ein nicht kopierter Anhang bedeutet nicht, dass eine
frueher exportierte/geerbte Datei entfernt wird. Gemeinsame Anhaenge koennen
pro Ressource erscheinen. Nicht zugeordnete globale Uploads gehoeren nicht
zu dieser Ansicht; sie bleiben in der separaten Anhangsverwaltung.

Stammdatenbezug heisst nur aktuelle geplante Mitwirkung, nicht historische
Eigentuemerschaft. Maximal zehn Bezüge pro Datei werden angezeigt, weitere
ausdruecklich gezaehlt; die Suche erfasst auch nicht angezeigte Bezugsnamen.
Die Ansicht erstellt keinen neuen Ordner, aendert keinen Katalog und erteilt
keine NX-Freigabe. Der spaetere Export bleibt eine separate bestaetigte Aktion.
Neu erzeugte DAT-Dateien werden inklusive UTF-8-BOM gehasht, passend zu den
tatsaechlichen Exportbytes. Keine automatische Pfad-/Grossschreibungsreparatur:
geerbte Grafikpfade mit nur abweichender Schreibweise und Kollisionen in
gemeinsamen Unterordnerpraefixen blockieren den Export,
damit Windows und Linux nicht unterschiedliche Ergebnisse erzeugen.

Admin-API: `POST /api/v1/releases/{id}/derive-preview/files`, mit `defaults`,
`preview_hash`, optional `view` (`files`/`private`), `change`, `query`, `offset`,
`limit`. Keine ERP-Berechtigung. Maximal100000 Pfade/Anhangszuordnungen,
100 Ergebnisse pro API-Seite,4MiB pro Antwort und200 Suchzeichen; bei
Ueberschreitung oder veraendertem Plan keine vollstaendige Teilantwort.
Zusatzmetadaten werden in einer SQLite-Read-only-Lesetransaktion gelesen;
der wiederverwendete Exportplaner nutzt weiterhin bestehende Store-Lesewege.
Kein atomarer Dateisystemsnapshot. Eine waehrend des Blaetterns geaenderte
Metadatenbindung verlangt eine neue Hauptvorschau; verspaetete Antworten nach
Filter-/Editor-/Sitzungswechsel werden verworfen. Beim Update Browser neu laden.

### Aeltere gespeicherte Pruefbefunde lesen

Bei aelteren Datenstaenden mit einem Bericht vor dem heutigen Freigabe-
Bindungsvertrag erscheint „Historische Befunde“. Diese Aktion liest nur den
bereits im Workspace gespeicherten Bericht, ohne weitere API-Anfrage,
Dateizugriff, neue Pruefung oder Auditzeile. Damalige Befunde sind mit Suche
und50erSeiten lesbar; das vollstaendige Original-JSON bleibt darunter erhalten.
Geaenderte Berichte/Datenstaende entwerten die Ansicht beim Aktualisieren.

Das ist ausdruecklich KEIN heutiger Integritaets-, Referenz-, NX- oder
Freigabenachweis. Auch null damalige Fehler machen den Datenstand nicht
heute geprueft. Alte Meldungen koennen durch fruehere Pruefregeln entstanden
sein. Den aktuellen Versionswechsel/Import in einen neuen Zielordner oder
eine bewusst gestartete neue technische Pruefung verwenden; alte Dateien,
Fehlerberichte und Sperren nicht passend umschreiben oder manuell loeschen.
Der strenge Leser aktueller Pruefberichte bleibt unveraendert und verwendet
diese Ansicht nicht als automatischen Fallback bei unvollstaendigen Antworten.

### Grosse Bestaende durchsuchen (13.10.2026)

Im Stammdatenkatalog bleiben alle Ressourcen im konsistenten Workspace-Snapshot;
die Tabelle zeigt jeweils bis zu 100 Treffer. Die Suche umfasst Bezeichnung,
gespeicherte ERP-Snapshotnummer, ID sowie native NX-Bibliothekskennungen,
Klassen und alle Text-Spaltennamen/-werte der gespeicherten DAT-Zeilen,
einschliesslich zusaetzlicher neuer Spalten. Typ-/Status-/Bibliotheksfilter
beziehen sich auf den GESAMTEN Snapshot, nicht nur auf die sichtbare Seite.
Filterwechsel beginnen auf Seite eins; Refresh laesst
die gueltige Seite und ungespeicherte Editorwerte bestehen. Bei schrumpfenden
Treffern wird auf eine vorhandene Seite begrenzt. Keine Import-/Saveaktion durch
Blattern; der Workspace wird nicht auf die sichtbaren 100 Datensaetze reduziert.

Die zusaetzliche Kennungsspalte zeigt die gespeicherte Bibliotheks-ID,
LIBRF bzw. NAME, Zeilenumfang und Klassen. Metrische, englische und
CMM-Bibliotheken sind getrennt filterbar. Aeltere Werkzeugdaten ohne
Bibliotheks-ID und andere freie Metadaten haben eigene Filteroptionen;
es wird keine Bibliothek anhand einer Bezeichnung erraten. Die Anzeige ist
keine Schema-/Referenz-/Datei- oder NX-Qualifizierung. Die Suche oeffnet keine
Dateien/Anhaenge und wertet keine freien CAM-JSON-Zahlen oder aktuelle ERP-
Liveartikel aus. Historische ERP-Snapshots bleiben entsprechend bezeichnet.

Auch native NX-Bibliotheksobjekte haben Suche und Vor-/Zurueckblaettern. Eine
Seite enthaelt hoechstens 100 vollstaendige Objekte, nie abgeschnittene FORMAT-/
RTYPE-/SEQ-Gruppen. Die gelesenen DAT-Bytes muessen dem gespeicherten Manifest
entsprechen; veränderte/unverzeichnete Dateien werden nicht als verwalteter Stand
angezeigt. Der SHA-Nachweis gilt nur fuer die ausgewaehlte DAT-Datei, nicht fuer
den gesamten Ordner oder eine NX-Qualifizierung. Pfadschutz steht vor Existenz-
und Inhaltszugriff, auch fuer die aelteren Werkzeug-Leseendpunkte.

„NX-Werkzeugschema“ laedt eine ganze native Werkzeuggruppe als UNGESPEICHERTES
Beispiel statt einer einzelnen Altformatzeile. Vor jeder Editoruebergabe wird
die komplette Antwort wie eine native Objektseite geprueft: passende Bibliothek/
NX-Version, Zaehler, manifestgebundener Dateihash, FORMAT-Folge/-Hashes und alle
Gruppenzeilen mit allen Textspalten. Erst nach ausdruecklicher Bestaetigung wird
der bisherige Editor ersetzt. Ablehnen, leere/fehlerhafte Antwort oder ein
unvollstaendiger Datenstand lassen ihn unveraendert; laufende/ungeklaerte
Speichervorgaenge oder Uploads sperren den Vorgang vor der Abfrage und vor
der Uebergabe. Auch ein unfertiger Rohdaten-JSON-Entwurf kann bewusst ersetzt
werden; dafuer muss er nicht erst erfolgreich als Datensatz geparst werden.

Alle Sektionen und Spalten bleiben zusammen, nur die jeweiligen LIBRF-/NAME-
Kennungsspalten erhalten denselben neuen UUID-basierten CUSTOM-Schluessel.
NAME als normale Bezeichnung neben LIBRF bleibt unveraendert. Keine ERP-
Zuordnung, Anhaenge, Modellpaketbindung, Importrechte oder Freigaben aus dem
bisherigen Editor uebernehmen. Der neue Entwurf benoetigt Namen, eigenen Save,
fachliche Pruefung und spaeteren Export. Bestehende Modelldateien werden nicht
umbenannt; Masse, Referenzen, Graphics-Namen und Geometrie sind weiterhin
ausdruecklich zu pruefen. Eine spaete Antwort darf nachtraegliches Tippen,
Reiterwechsel oder geaenderten Quellkontext nicht ersetzen, auch nicht nach
Wechsel zurueck zum alten Zustand. Die alten Tool-APIs bleiben begrenzte Legacy-Leser,
mit Pfadschutz und Manifestbindung, nicht universelle mehrzeilige Schemaadapter.

Dateipaket-Inventar hat Vertrag 2:
`GET /api/v1/releases/{id}/file-packages?kind=template&query=...&offset=0&limit=100`.
`query` maximal 200 Zeichen; `offset` 0 bis 1000000, `limit` 1 bis 500. Sortierung
nach relativem Pfad (casefold), `total`, `filtered`, `offset`, `limit`, `files`,
Manifest- und Besitzhash plus `snapshot` aus EINER SQLite-Lesetransaktion.
Das ist eine Metadatenliste, KEIN Datei-/Siemens-/NX-Test:
`files_verified=false`, `baseline_compared=false`, `automatic_approval=false`.
Die spaetere Uebernahmevorschau liest und prueft ausgewählte Dateien tatsaechlich.

Bereits beanspruchte Paketdateien zeigen ihren aktuellen oder historischen
Dateibesitzer; aktuelle Reservierungen UND Besitz im gewaehlten Elternrelease
werden kontrolliert. Eine im Editor entfernte Zuordnung gibt die im alten Release
noch vorhandene Datei nicht automatisch fuer einen neuen Besitzer frei. Zuerst
einen geprueften neuen Export ableiten; alte Releasebytes bleiben historisch.
Leere/zu grosse oder fuer Pakete zu lange Namen werden sichtbar mit Grund gesperrt,
nicht still ausgeblendet oder allein deshalb der ganze Bestand unlesbar gemacht.

Dateiauswahl bleibt beim Suchen/Blattern erhalten, einschliesslich Dateien auf
nicht sichtbaren Seiten. Die Liste „ausgewaehlte Dateien“ erlaubt ausdrueckliches
Entfernen. 100 ist eine Gesamtgrenze ueber alle Seiten, keine Grenze pro Seite.
Ein Wechsel von Datenstand/Kategorie oder Abmeldung verwirft die Auswahl.
Ein Wechsel des vorhandenen nativen Entwurfs invalidiert die Vorschau, nicht
die Dateisuche. „Auswahl verwerfen und Bestand neu laden“ ist der ausdrueckliche
Reset bei geaendertem Manifest/Dateibesitz. Alte Antworten/Fehler anderer Such-
kontexte, auch Wechsel weg und zurueck, werden verworfen. Eine Uebernahmevorschau
darf keine inzwischen geaenderten Bytes anstelle der ausgewaehlten Metadaten
unbemerkt einsetzen. Auch danach nur Entwurf, keine automatische NX-Freigabe.

### Speichern mit dauerhaftem Serverbeleg (13.10.2026)

Stammdaten-Speichern hat jetzt einen Wiederanlaufvertrag fuer verlorene Antworten.
Eine unterbrochene Verbindung beweist nicht, dass der Server nichts gespeichert
hat. Die GUI legt deshalb VOR dem Senden eine eindeutige Anfragekennung an.
Datensatz, unveraenderliche Revision, Schreibbeleg und Audit werden gemeinsam in
EINER SQLite-Schreibtransaktion committed oder zusammen zurueckgerollt.

Optionaler HTTP-Header `Idempotency-Key`: kanonische UUID in Kleinbuchstaben.
Unterstuetzt bei Admin-`POST /api/v1/resources`, Admin-`PUT
/api/v1/resources/{id}` und `POST /api/v1/erp/resources/upsert`. Bestehende
Request-Bodies und Clients ohne Header bleiben kompatibel; ohne Kennung gibt es
keinen Wiederanlaufbeleg. Der Hash bindet die Aktion, Ziel-ID, erwartete Revision
und normalisierte Request-Daten. Gleiche Kennung und gleiche Daten liefern die
URSPRUENGLICHE Antwort, ohne neue Revision/Audit und ohne spaetere Aenderungen
zurueckzusetzen. Andere Aktion/Daten/Revision unter derselben Kennung: HTTP 409.
Ein alter Beleg ist kein Nachweis fuer den aktuellen neuesten Datensatzstand.

`GET /api/v1/write-requests/{key}` liefert Vertrag 1 mit `committed` oder
`cancelled`, Aktion, Datenhash, Ressourcen-ID/Revision und urspruenglicher Antwort.
HTTP 404 bedeutet nur: noch kein terminaler Beleg. Ein laufender Request kann
weiterhin speichern. `POST /api/v1/write-requests/{key}/cancel` setzt deshalb
eine transaktionale Sperre fuer diese Kennung, wenn sie noch nicht committed ist.
Spaetere Schreibversuche mit dieser Kennung werden abgewiesen. War der Versuch
schon gespeichert, kommt stattdessen sein Beleg zurueck: KEIN Undo und keine
Loeschung. Belegzugriff/Sperren sind an die authentifizierte Rolle (`admin`/`erp`)
gebunden; noch keine individuellen Benutzerkonten oder mandantengetrennten Rollen.

Bei unbestaetigtem Speichern zeigt der Aufbereitungsreiter drei ausdrueckliche
Aktionen: „Serverbeleg pruefen“, „Exakt denselben Speicherversuch wiederholen“ und
„Versuch serverseitig beenden“. Normales erneutes Speichern bleibt bis zur
Klaerung gesperrt, die anderen Module bleiben benutzbar. Kein automatischer Retry.
Spaeter eingegebene Editorwerte bleiben erhalten; nur im unveraenderten
Editor-Kontext wird die bestaetigte ID/Revision als neue Bearbeitungsbasis gesetzt.
Antworten einer alten Sitzung duerfen keine neue Sitzung/deren Eingaben ersetzen.

Im `sessionStorage` dieses Browsertabs liegt nur ein Hinweis mit Vertrag,
Anfragekennung, Methode, API-Pfad und Zeitpunkt. KEIN Administrationsschluessel,
Modell oder CAM-/ERP-Payload. Die urspruenglichen Request-Daten bleiben nur im
Arbeitsspeicher fuer einen bewussten identischen Retry. Nach Seitenneuladen sind
daher Belegpruefung und serverseitiges Beenden verfuegbar, nicht Wiederholung aus
einem erfundenen Payload. Schliessen des Tabs verliert den Browserhinweis, nicht
den dauerhaften Serverbeleg. Ein kaputter lokaler Hinweis wird sichtbar gesperrt;
sein bewusstes Verwerfen loescht keine Serverdaten. Bestand/Audit dann pruefen.
Eine eindeutig zurueckgewiesene Validierung (HTTP 400/422) sperrt die alte Kennung
vor Freigabe eines korrigierten Versuchs. Transport-, Server-, Konflikt- und
Berechtigungsfehler werden nicht als sicher erfolglos angenommen.

Die neue Tabelle `write_requests` gehoert zur Metadatenintegritaet und zu
Vollsicherung/Wiederherstellung. Antwortdaten werden gegen die referenzierte
unveraenderliche Revision geprueft; Audit bindet auch den Beleg. Alte Archive
ohne Tabelle behalten ihren urspruenglichen Snapshot-Hash und bekommen erst bei
isolierter Wiederherstellung eine neue leere Tabelle. Keine automatische
Belegloeschung/Retention und keine alte Historie umgeschrieben.

Dieser Schreibvertrag gilt fuer Stammdaten-Speichern und ERP-Upsert, NICHT fuer
Dateiuploads, Bestandsuebernahme oder STEP-/Backupauftraege. Uploads und
Upgrade-/Extraktionsannahmen sowie Hintergrunduebernahmen/-exporte haben eigene
Vertraege (siehe unten). Die jeweiligen
Vorschau-/Revisions-/Jobvertraege bleiben erforderlich. Ein Speicherbeleg aendert
keine NX-Ordnerdatei und erteilt keine automatische CAM-/NX-Freigabe. ERP ist
weiter ein synthetisch gepruefter Adapter, keine echte Business-Central-Verbindung.

### Zahlen im freien CAM-JSON-Editor (15.10.2026)

Vorhandene JSON-Zahlendarstellungen bleiben beim Oeffnen und Bearbeiten des
freien CAM-Objekts sowie im Speichersnapshot und HTTP-Request erhalten. So werden
etwa `1.0`, `-0.0`, `1e+20` oder Ganzzahlen oberhalb von JavaScripts sicherem
Ganzzahlbereich nicht unbemerkt zu anderen Datentypen, gerundeten Zahlen oder
`null`. Auch eine reine Namensaenderung darf diese CAM-Werte nicht veraendern.
Die gespeicherten Zahlen erscheinen nach einer Bestaetigung entsprechend in der
Detailanzeige. Schreibantworten und Schreibbelege werden mit Zahlennachweis
gelesen. Der gemeinsame JSON-API-Leser erhaelt diesen Nachweis auch fuer andere
Antworten, insbesondere die echten64Bit-Grenzen in der OpenAPI-Dokumentation;
sie werden nicht zugunsten einer Browserdarstellung kleiner gerechnet.
Es gibt keine neue Datenbankversion und keinen historischen Hashumbau.

Eine bewusste Wiederholung nutzt denselben eingefrorenen JSON-Inhalt und dieselbe
Anfragekennung, auch nach spaeteren Editor-Eingaben. Unterschiede zwischen zwei
grossen Ganzzahlen bleiben fuer den Editor-Aenderungsvergleich unterscheidbar;
eine spaetere Eingabe wird nicht wegen gleicher gerundeter JS-Zahlen verworfen.
Im Browserstorage liegt weiterhin nur der nichtgeheime Vorgangshinweis.

Der Server verwendet weiterhin Python-JSON-Ganzzahlen und endliche binaere
Gleitkommazahlen, keine beliebig genaue Dezimalrechnung. Die Zahlentextuebernahme
ist kein Einheitensystem und kein numerischer Berechnungs-/Geometriekonverter.
Ueber-/Unterlauf in nichtendliche bzw. auf Null fallende Gleitkomma-Eingaben,
doppelte Objektschluessel und unvollstaendiges JSON sperren den GUI-Schreibversuch
vor der Anfragekennung und dem Senden. Unbewiesene bereits gerundete JS-
Ganzzahlen werden nicht als exakte Eingabe ausgegeben. Pro Serialisierung gelten
64 MiB UTF8-Bytes, 2000000 besuchte Werte und maximal 512 Verschachtelungsebenen.
Native DAT-Spaltenwerte bleiben die vom NX-Schema verlangten Texte.

Zusaetzlich prueft der zentrale serverseitige CAM-Vertrag nichtendliche Zahlen
auch in verschachtelten freien Metadaten. `NaN`, `Infinity` oder ein zu Infinity
ueberlaufender Float werden bei HTTP-Schreibzugriffen und direkten Store-
Schreibpfaden abgewiesen, bevor Ressource, Historienrevision, Audit oder neuer
Schreibbeleg committed werden. Grosse echte Integer werden dafuer nicht in Float
umgerechnet; numerisch aussehende Texte bleiben Texte. Eine Integritaetspruefung
meldet einen entsprechend fehlerhaften Altbestand, repariert oder normalisiert
ihn aber nicht automatisch. Historische Payload-/Audit-/Belegbytes bleiben.

Das ist eine GUI-/Metadatenpruefung, keine neue fachliche CAM-Freigabe. Die
uebrigen Export-, Schema-, Bibliotheks- und Geometriepruefungen bleiben bestehen;
die reale NX-/Maschinenqualifikation ist dadurch nicht ersetzt.

### Dateiuploads mit dauerhafter Allokation und Commitbeleg (14.10.2026)

Auch ein Dateiupload kann auf dem Server erfolgreich sein, obwohl die Antwort
den Browser nicht erreicht. Die GUI verwendet deshalb bei `POST /api/v1/assets`
einen kanonischen UUID-`Idempotency-Key`. Dateiname, Groesse und SHA-256 der
dekodierten Bytes binden die Anfrage. Vor dem physischen Schreiben wird die neue
Anhangs-ID dauerhaft reserviert und auditiert. Datei-Metadaten, Commitbeleg und
Audit committen anschliessend in EINER SQLite-Transaktion. Die Dateibytes selbst
liegen ausserhalb dieser SQL-Transaktion; eine Reservierung ist kein Dateierfolg.

Gleiche Kennung und gleiche Bytes nach erfolgreichem Commit liefern nur die
urspruenglichen Metadaten: keine zweite Datei, kein neues Audit und kein
Ueberschreiben. Andere Bytes oder anderer Name: 409. Ein reservierter,
fehlgeschlagener oder gesperrter Versuch wird NICHT automatisch neu geschrieben.
Teilpfade bleiben diagnostisch erhalten. Clients ohne Header bleiben kompatibel,
haben aber keinen Wiederanlaufbeleg. Der Vertrag gilt fuer bounded Anhaenge von
1 Byte bis 20 MiB, nicht fuer Installer oder grosse Werkzeugarchive.

Admin-`GET /api/v1/asset-requests/{key}` liefert Vertrag 1 mit Zustand
`reserved`, `committed`, `failed` oder `cancelled`, Allokation, Hash und ggf.
urspruenglichem Ergebnis. Ein 404 beweist nicht, dass eine laufende Anfrage
gestoppt ist. `POST /api/v1/asset-requests/{key}/cancel` sperrt einen noch nicht
committed Versuch transaktional, auch vor dessen Reservierung. Ein schon
committed Anhang wird mit seinem Beleg zurueckgegeben, nicht rueckgaengig
gemacht. Sperren beendet keinen physischen Writer und loescht keine Datei.
Der Writerabschluss wird separat festgehalten; Sicherung/Wiederherstellung
starten nicht, solange der zugeordnete Prozess noch an der Datei schreiben
koennte. Neue Uploadreservierungen warten bei aktiver Sicherung/Wiederherstellung.

Im Aufbereitungsreiter bieten ungeklaerte Uploads ausdrueckliche Belegpruefung,
identischen Retry oder Commit-Sperre. Neue Uploads und Stammdaten-Speichern
bleiben bis zur Klaerung gesperrt; andere Module bleiben benutzbar. Nur im
Arbeitsspeicher bleibt der originale Uploadbody fuer einen bewussten Retry.
`sessionStorage` enthaelt ausschliesslich Vertrag, Anfragekennung und Zeitpunkt:
kein Schluessel, Dateiname, Modell oder CAM-/ERP-Payload. Nach Seitenneuladen
deshalb nur Belegpruefung/Sperre, kein rekonstruierter Dateiinhalt. Antworten
einer alten Sitzung ueberschreiben keinen neuen Editor.

Ein Commitbeleg ist zunaechst nur Metadatenbeweis. Vor einer GUI-Zuordnung wird
der Anhang separat gegen Groesse/SHA geprueft. Nur ein unveraenderter Editor-
Kontext bekommt seine ID automatisch angehaengt; spaetere Eingaben und eine
neu gewaehlte Datei bleiben erhalten. Nach Reload oder Editorwechsel bewusst
im Dateibestand zuordnen. Bei fehlender/manipulierter Datei keine Zuordnung:
ein bekannter Metadatencommit kann ausdruecklich nur lokal anerkannt werden,
ohne Datei-/CAM-Freigabe, Loeschung oder zweite Uploadkopie. Integritaetsproblem
separat klaeren. Auch eine normale Zuordnung muss anschliessend als neue
Stammdatenrevision gespeichert und ggf. geometrisch/fachlich geprueft werden.

Die neue Tabelle `asset_requests` ist auditgebunden und Bestandteil der
Integritaetspruefung und Vollsicherung/isolierten Wiederherstellung. Teilabsichten
werden als Diagnosewarnung erhalten, nicht als verifizierte Anhaenge. Alte
Archive ohne Tabelle behalten zuerst ihren originalen Snapshot-Hash; erst der
isolierte Restore ergaenzt eine leere Tabelle. Keine Historienumschreibung,
automatische Auftragswiederholung oder automatische Retention.

### STEP-Auftraege mit dauerhafter Paketabsicht und Commitbeleg (14.10.2026)

`POST /api/v1/geometry-jobs` unterstuetzt jetzt einen kanonischen UUID-
`Idempotency-Key`, von der GUI immer verwendet. Gebunden werden gespeicherte
Ressourcen-ID, erwartete Revision, STEP-Anhangs-ID und Assistant-Werkzeugtyp.
Vor dem physischen ZIP-Write wird eine auditierte Paketabsicht dauerhaft
gespeichert: neue Job-ID, originale Werkzeug-/STEP-/Worker-Pins, Paket-SHA/Groesse
und Prozess-/Lebenszeitbezug. Nach Write/Dateipruefung und erneutem Vergleich
des Ressourcenkopfs committen Job, Beleg und Audit gemeinsam in EINER
SQLite-Transaktion. Datei-Schreiben ist selbst nicht transaktional.

HTTP 201 bedeutet registrierter lokaler Importauftrag, KEIN gestarteter oder
erfolgreicher NX-Lauf. Gleiche Kennung/gleiche Daten nach Commit liefern den
urspruenglichen Auftrag ohne neue Datei und ohne alte/neue Ressourcenrevisionen
zurueckzusetzen. Ein spaeterer Diagnosebericht oder additive Collector-Pakete
bleiben erhalten; aktuellen Diagnosestatus ueber Job-GET/Bestand lesen. Andere
Revision, Anhang oder Werkzeugtyp unter derselben Kennung: 409. Reservierte,
fehlgeschlagene und gesperrte Teilpakete nie automatisch ueberschreiben oder
neu starten. Clients ohne Header bleiben kompatibel, ohne Wiederanlaufbeleg.

Admin-`GET /api/v1/geometry-requests/{key}` liefert Vertrag 1 mit
`reserved`/`committed`/`failed`/`cancelled`, Allokation und ggf. Originalergebnis.
Ein 404 beweist keinen gestoppten Request. Admin-`POST
/api/v1/geometry-requests/{key}/cancel` sperrt nur einen offenen Commit, auch
vor Reservierung. Bereits registrierte Jobs und Teilpakete werden nicht
geloescht; kein NX-Prozess wird beendet. Writerabschluss ist separat erfasst.
Backup wartet auch nach einer Sperre, solange der physische Paketwriter noch
laufen koennte. Neue Reservierungen pruefen die Backup-Schreibpause nochmals.

Die GUI legt VOR dem Request einen minimalen Tab-Hinweis an: nur Vertrag,
Kennung und Zeit. Keine Modelle, Werkzeug-/Anhangs-IDs, Dateinamen, Schluessel
oder CAM-Daten im `sessionStorage`. Originalbody nur RAM fuer bewusst exakten
Retry. Nach Reload daher nur Belegpruefung/Sperre. Ein ungeklaerter Auftrag
sperrt neue STEP-Auftraege, nicht die anderen Verwaltungsbereiche. Neue
Auswahl/Editorwerte und neue Sitzungen bleiben bei spaeten Antworten erhalten.

`GET /api/v1/geometry-jobs/{id}/package-metadata` prueft das originale ZIP und
seinen Eingang gegen Groesse/SHA, ohne NX auszufuehren. Erst danach bestaetigt
die GUI das Paket; der Beleg allein ist nur Metadatenbeweis. Fehlende/geaenderte
Pakete bleiben ein Integritaetsproblem. Ein bekannter Auftragscommit kann nur
lokal anerkannt werden, ohne Paket-/Geometrie-/CAM-Freigabe oder neue Kopie.
Download prueft weiterhin separat. Diagnosebericht-POST nutzt BEGIN IMMEDIATE:
auch zwei unabhaengige Store-Schreiber duerfen keinen anderen Bericht ersetzen.
Identische Berichte sind weiterhin wiederholbar, keine NX-Abnahme.

`geometry_requests` gehoert zu Audit, Integritaet und Vollbackup/isoliertem
Restore. Teilabsichten bleiben Diagnosewarnungen; historische Archive ohne
Tabelle behalten ihren originalen Snapshot-Hash und bekommen erst bei Restore
eine leere Tabelle. Alte Job-/Worker-/Belegdateien nicht neu erzeugen. Echte
STEP-/NX-/Assistant-Erprobung bleibt laut Benutzer fuer spaeter offen.

### Ordneruebernahme und taeglicher Export als Hintergrundauftrag

Die GUI wartet bei Kopiervorgaengen nicht mehr auf einen langen synchronen
Import-/Exportrequest. Vorbereitete Ordneruebernahme und taeglicher Stammdaten-
export verwenden die gemeinsame dauerhafte Auftragsverwaltung. Unter
„Stammdaten verwalten“ zeigt „Uebernahmen und Stammdatenauftraege“ Annahme,
Fortschritt, Fehler und Berichte; diese Jobs stehen NICHT im selten genutzten
Versionsupdate-Bestand. Alle Ordnerwriter bleiben gemeinsam serialisiert.
Ungeklaerte/laufende Kopien sperren weitere Writer auch in den anderen Reitern.

Neue Admin-API: `POST /api/v1/release-jobs/import` (bestehender ReleaseInput)
und `POST /api/v1/release-jobs/derive` (DeriveInput plus `parent_release_id`).
Optional kanonischer UUID-`Idempotency-Key`, von der GUI immer verwendet.
Antwort HTTP 202 ist ausschliesslich Annahme, NICHT Erfolg. Status/Bericht ueber
`GET /api/v1/migrations/{id}` bzw. `/report`; Originalannahme/Sperre ueber
`/api/v1/migration-requests/{key}` bzw. `/cancel`. Dessen Vertrag 1 ist um die
Modi `import`/`derive` erweitert. Keine zweite Kopie/kein Worker-Neustart bei
identischem Retry, auch nach fehlgeschlagenem oder unterbrochenem Auftrag.

HTTP-Annahme prueft Rollen, lokale freigegebene Bereiche, vorhandene Ziele,
Grundstruktur/Elternmetadaten und syntaktischen Vorschauhash. Aufwendige
Datei-/DAT-/Referenz-/Revisionspruefungen laufen im Worker VOR dem ersten
Release-/Zielwrite; keine Gigabyte-Manifestberechnung im Annahmerequest.
Ein angenommener Auftrag mit geaendertem/ungueltigem Vorschauhash kann daher
`failed` werden, ohne einen neuen NX-Ordner erstellt zu haben. Erfolg verlangt
weiterhin exakt passende, konfliktfreie/importierbare/exportierbare Vorschau.
Neue Hintergrundimporte verlangen `normalize_libraries=true` und geprueften
Vorschauhash: Henning-Dierk-Daten gehen in Standardbibliotheken, nicht erneut
als alte Datenbanken ins Ziel. Der alte synchrone `POST /releases` und
`POST /releases/{id}/derive` bleiben fuer bestehende Clients kompatibel, sind
aber NICHT automatisch idempotent oder als Hintergrundauftrag nachverfolgbar.

Fortschritt benennt Planung, Quellmanifest/Kopie, Normalisierung bzw. Export,
Verifikation und Registrierung/Testpaket. Jeder gestartete Kandidat ist schon
vor dem Kopieren an die Job-ID gebunden. Speicherfehler erhalten den fehlgeschlagenen
Release und Teilordner; keine automatische Bereinigung/Neuverwendung. Bericht
bindet Job-/Release-ID, Preview- und Manifesthash. Erfolg erstellt nur einen
neuen Entwurf mit vorhandenem versionspassendem Test-Starter; keine Freigabe
oder Aktivierung aus Annahme, Fortschritt, Hash oder erfolgreichem Kopieren.

Der Export prueft den ganzen Katalog-Revisionssatz nach Kopie UND noch einmal
innerhalb seiner finalen SQLite-Schreibtransaktion. Aendert ein anderer
Store/ERP-Schreiber zwischen Planung, Kopie oder Testpaket-Erstellung eine
Revision, wird der Kandidat nicht als fertiger Export committed. Parent und
Quellen bleiben erhalten, neue Vorschau ist erforderlich. Einzelne Anhangs- und
Dateipaketpruefungen/Standard-Besitzsperren bleiben zusaetzlich verbindlich.

Bei verlorener Antwort im Verwaltungsreiter Annahme pruefen, bewusst identisch
wiederholen oder NUR vor Annahme sperren. Nach Reload bleibt nur ein minimaler
Hinweis (Vertrag, Kennung, Modus, Zeitpunkt) in diesem Tab; keine Schluessel,
Ordnerpfade, Spalten-/Konfliktentscheidungen oder CAM-/ERP-Daten. Originalbody
nur im Speicher. Nach Reload daher nur Belegpruefung/Sperre. Ein fehlender Beleg
ist kein Stopnachweis. Bereits angenommene Worker nie per Sperre stoppen/loeschen.
Neue Editor-/Exportwerte und auch unvollstaendiges spaeteres JSON bleiben erhalten;
Antworten einer alten Sitzung duerfen sie nicht zuruecksetzen. Veraltete
Exportmasken werden nicht auf einen inzwischen gewaehlt anderen Parent angewandt.

Explizite Verweiszuordnungen bekommen eine stabile Berichts-/Anwendungsreihenfolge,
unabhaengig von der JSON-Schluesselreihenfolge vor/nach dauerhaftem Speichern.
Native DAT-Zeilen, FORMAT-/RTYPE-Gruppen und Komponenten werden dabei NICHT
umgeordnet. Dieselben Entscheidungen und Bytes ergeben denselben Vorschauhash.
Nach Servercode-Update gegebenenfalls eine neue Vorschau pruefen; alte gespeicherte
Berichte/Belege werden nicht still umgeschrieben oder neu beglaubigt.

Jobs, unveraenderliche Berichte und Annahmen sind in Integritaetspruefung und
Vollbackup/isoliertem Restore enthalten. Es entsteht keine neue parallele,
unversionierte Dateiablage. Bei nachgewiesenem Server-/Ownerverlust bleiben
Teilstaende/Belege erhalten und unterbrochen, ohne automatischen Neustart.
Katalog-/Dateipaketuebernahmen nutzen jetzt ebenfalls diesen Jobvertrag (siehe
unten). Dateiuploads nutzen den separaten Allokations-/Commitvertrag oben.
SIDT-Auswahl ist nicht von diesen Annahmebelegen umfasst;
Backupjobs haben den eigenen dauerhaften Annahmevertrag unten;
STEP-Auftraege haben den separaten Paket-/Commitvertrag oben;
noch kein allgemeiner Retry fuer alle Writes.

### Native Stammdaten und Kunden-Dateipakete dauerhaft uebernehmen

Die GUI uebernimmt native Katalogobjekte und vorhandene Kunden-Dateipakete jetzt
als Hintergrundauftrag, im selben Verwaltungsbestand wie Ordnerimporte und
Exporte. Neue Admin-API: `POST /api/v1/release-jobs/catalog` (CatalogInput plus
`release_id`) und `POST /api/v1/release-jobs/file_adopt` (FileAdoptionApply plus
`release_id`). HTTP 202 nur Annahme, nicht Erfolg oder CAM-Freigabe. Optionaler
UUID-Idempotency-Key, von der GUI immer verwendet; Modi `catalog`/`file_adopt`
im bestehenden Annahmevertrag. Status/Bericht und pre-acceptance Fence bleiben
`migrations/{id}` bzw. `migration-requests/{key}`. Kein neuer Tabellen-/Dateibaum.

Worker prueft dieselbe exakte Vorschau, Bibliotheksschemata, Datengruppen,
Standard-/Kundenbesitzer, Dateihashes, Baseline/SIDT und Zielrevision wie zuvor.
Nach Paketkopie erneut die Auswahl/Baseline/Besitzbindung pruefen. Katalogimport
vergleicht den kompletten Ressourcensatz nochmals innerhalb BEGIN IMMEDIATE:
ein unabhaengiger ERP-/anderer Store kann keine konkurrierenden Besitzer
zwischen Planung und Insert unbemerkt einschieben. Neue/uebernommene Ressourcen
bleiben Entwuerfe, auch Siemens-Standardobjekte keine editierbaren Kundenbesitzer.

`domain_result` bindet Job-ID, Modus und exakte Ressourcen-ID/Revision gemeinsam
mit Ressource(n), immutable Versionen und Audit in EINER SQL-Transaktion. Bei
Commitfehler rollt auch diese Ergebniszuordnung zurueck. Nach Commit bleibt sie
erhalten, selbst wenn Berichtschreiben/Workerabschluss spaeter fehlschlaegt oder
der Besitzerprozess endet. Status `failed`/`interrupted` bedeutet dann NICHT,
dass nichts uebernommen wurde: Details enthalten die committed Entwurfsrevisionen.
Gleicher Retry liefert nur die alte Annahme, keine weiteren Ressourcen/Anhaenge.
Aktuelle Versionen werden nicht durch einen alten Ergebnissnapshot ersetzt.

Jeder Paketanhang bekommt VOR dem physischen Write eine auditgebundene Absicht
`asset_allocations`: ID, Basename, erwartete Groesse/SHA, `reserved`. Der
unveraenderliche Dateischreibvorgang wird separat verifiziert; Assetzeile, Audit
und Zustand `stored` committen gemeinsam. Kein Erfolg aus blosser Reservierung.
Bei Schreib-/spaeterem Entwurfsfehler bleiben zugeordnete Diagnosen, reservierte
Teilpfade und bereits gespeicherte Anhaenge erhalten, nicht unsichtbar geloescht.
Unzugeordnete gespeicherte Dateien sind weiterhin in der Anhangsverwaltung
sichtbar/reviewbar; keine automatische Reparatur, Zuweisung oder NX-Freigabe.

Job-GET prueft Resultat/Allokationen in EINER SQLite-Lesetransaktion gegen
immutable Entwurfsrevisionen, deren Actor/Importprovenienz und Assetmetadaten.
Beschaedigte/fehlende Bindung ist 409, kein gruenes Ergebnis. Integritaetspruefung
meldet Fehler und reservierte Teilanhaenge als Diagnosewarnung. Das ist ein
Metadatenbeleg, kein neuer Dateiscan, keine Signatur oder NX-Qualifizierung.
Jobberichte, Ergebnisse und Anhangsabsichten gehen in Vollbackup/isolierten
Restore ein; auch fehlgeschlagene, noch nicht gespeicherte Reservierungen.

Im GUI bestehende Annahme pruefen, exakt wiederholen oder nur VOR Annahme
sperren. Nach Reload nur der minimale Tab-Hinweis (Vertrag/Kennung/Modus/Zeit)
ohne Token, Quelldateien, Ordnerpfade oder technische Daten. Neue Eingaben/
neue Sitzung werden nicht durch ein spaetes Ergebnis zurueckgesetzt. Ein
unveraenderter Dateipaket-Editor kann den committed Entwurf oeffnen; sonst
„Aktuellen Datensatz oeffnen“ im Auftrag ausdruecklich waehlen. Kein automatisches
Laden einer historischen Revision ueber neuere Stammdaten. Vier Paketpruefungen,
Geometrie-/CAM-Abnahme und anschliessender neuer Export bleiben erforderlich.

Die alten synchronen `/catalog-import`- und `/file-packages/adopt`-Endpunkte
bleiben fuer bestehende Clients vorhanden, ohne vorgetaeuschte Job-Recovery.
Neue Capabilityflags `atomic_adoption_result`/`durable_attachment_intents` und
alle sechs Verwaltungsmodi sind fuer diese GUI erforderlich; alte Server sichtbar
gesperrt statt still zur unbestaetigten synchronen Uebernahme zurueckzufallen.

### Einzelne Bibliotheksobjekte gezielt als Stammdaten uebernehmen (14.10.2026)

Im Reiter **Stammdaten verwalten** koennen nun einzelne Bibliotheksobjekte beim
Lesen angekreuzt werden. Auswahl bleibt beim Suchen/Blaettern und Bibliothekswechsel
im selben NX-Datenstand erhalten. Unter „Umfang der Stammdatenuebernahme“ bewusst
„Nur ausdruecklich ausgewaehlte Objekte“ waehlen. Ganze Bibliotheken bleiben als
separater, bisheriger Modus vorhanden; leere Objektauswahl ist niemals Vollimport.
Vorschau zeigt neue Entwuerfe, bereits verwaltete Objekte und Konflikte getrennt
und blaettert in jeweils 100 Eintraegen. Bereits verwaltete Stammdaten koennen
aus der Vorschau ausdruecklich im Editor geoeffnet werden.

Absicherung der Objektansicht (15.10.2026): Beim Laden verschwindet die alte
Objektseite. Erst nach Pruefung der gesamten Antwort werden neue Zeilen
auswaehlbar: Bibliothek/NX-Version, Seitenzaehler, FORMAT-Folge/-Hashes und
saemtliche Gruppenzeilen mit allen deklarierten Textspalten muessen passen.
Eine unvollstaendige Antwort zeigt keine teilweise bearbeitbare Seite.
Bereits gewaehlte Objekte bleiben bei einem voruebergehenden Lesefehler erhalten.
Die gezielte Vorschau/Uebernahme bleibt dann gesperrt, bis die betroffene
Bibliothek erfolgreich mit demselben Datei-SHA erneut geladen wurde. Filter
und Seite duerfen dabei anders sein: Der SHA bindet die ganze DAT-Datei.
Alternativ die betroffenen Objekte unter „Ausgewaehlte Bibliotheksobjekte
pruefen / entfernen“ bewusst aus der Auswahl entfernen. Andere ausgewaehlte
Bibliotheken bleiben erhalten; eine fehlgeschlagene unbeteiligte Bibliothek
sperrt deren Vorschau nicht. Ganze Bibliotheken bleiben ein separat gewaehlter
Modus mit eigener Servervorschau, kein automatischer Ersatz fuer eine Auswahl.
Bei anderem Datei-SHA wird die alte Auswahl verworfen statt still neu gebunden.
Datenstand-/Ordner-/Versionswechsel oder unvollstaendiger Datenstand leeren
die alte Ansicht/Auswahl. Alte Antworten und abgeloeste Bedienelemente koennen
keine neuere Auswahl oder Editorwerte ersetzen. Keine Datei wird durch diese
Pruefung geschrieben; eine fachliche NX-Abnahme wird damit nicht bescheinigt.

Neue Entwuerfe zeigen vor der Uebernahme die Gesamtzahl ihrer Zeilen und
FORMAT-Bloecke. **FORMAT-Spalten anzeigen** klappt eine lesende Tabelle mit
Originalspalten, Baselinespalten und Quellen-/Baseline-only-Feldern auf. Der
Vergleich ist nur fuer dieselbe FORMAT-ID, keine automatisch qualifizierte
semantische Zuordnung. Fehlt der entsprechende Baselineblock, wird das sichtbar
angegeben. **Metadaten ansehen** zeigt die genauen Arrays auch als JSON.
Unbekannte/zusaetzliche gueltige Spalten und alle RTYPE-/SEQ-Zeilen bleiben
unveraendert im Entwurf; Baselinespalten werden nicht erfunden, Quellenfelder
nicht still entfernt. Eine spaetere Schemaumstellung erfolgt separat mit
ausdruecklichen Entscheidungen. Dieses Anzeigen aendert auch keine ungespeicherten
Editorwerte oder Dateien.

Auch der Import prueft jetzt den gemeinsamen strukturellen CAM-Vertrag vor
der Ressourcenanlage. Eine Gruppe, die anschliessend nicht korrekt im Editor
gespeichert werden koennte, erscheint als Vorschaukonflikt. Es gibt keine
automatische Teiluebernahme oder Format-Reparatur. Die GUI prueft erst den
vollstaendigen Quell-/Katalog-/Gruppenvertrag, bevor sie eine Vorschauseite
anzeigt oder die Uebernahme freigibt. Defekte Antworten verlangen Neuabruf.

`CatalogInput` akzeptiert optional `selection: {library_id: [key, ...]}` fuer
`catalog-preview`, den kompatiblen synchronen `catalog-import` und den bestehenden
Backgroundjob `release-jobs/catalog`. Bibliotheken und Auswahl muessen genau
zusammenpassen; insgesamt maximal 500 ganze Objekte, keine leeren/doppelten
Schluessel, maximal 1024 Zeichen pro Schluessel. Alle FORMAT-/RTYPE-/SEQ-Zeilen
der ausgewaehlten Gruppe bleiben zusammen. Fehlende Schluessel sind Konflikte,
keine stillen Auslassungen. Es gibt keine automatische Uebernahme zusaetzlicher
Referenzobjekte, ERP-Artikel, Werkzeugmodelle oder CAM-/Geometriefreigaben.

Ausgewaehlte unveraenderte Siemens-Objekte erfordern ausdruecklich
`include_standard`; sie bekommen keine NX-Ueberschreibungsrechte. Eigene
Metadatenrevisionen sind davon zu unterscheiden: Ein geaenderter solcher
Stammdatensatz darf keinen Siemens-Datensatz im NX-Export ueberschreiben.
Bereits verwaltete identische Objekte werden wiedererkannt, abweichende Besitzer
nicht automatisch ersetzt. Nicht ausgewaehlte Konfliktobjekte werden nicht
versehentlich mitimportiert.

Vorschauhash bindet Auswahl, ganze Datengruppen, Katalogrevisionen/-inhalt,
Manifest und Siemens-Baseline. Vor Entwurfscommit nochmals SQL-Quellidentitaet/
Manifest, DAT-Bytes, Baseline einschliesslich Vorhandensein und aktuellen
Kataloginhalt pruefen. Das gilt jetzt auch fuer ganze Bibliotheken, nicht nur
fuer einzelne ausgewaehlte Objekte. Gleichbleibende Revisionsnummern reichen
bei geaenderten Metadaten nicht. Neue Ressourcen/immutable
Revisionen, Job-Domainresultat und Audit bleiben ein Commit; GET/Integritaet und
Restore pruefen auch die Zugehoerigkeit zur Originalauswahl. Neuer Jobbericht
enthaelt die Auswahl. Annahmereplay erzeugt keine zweite Uebernahme.

Capability `catalog_selection` Vertrag 1 und neuer `catalog_adoption`-Vertrag 1;
keine neue SQL-Tabelle. Ohne optionale Auswahl bleibt der Whole-Library-Modus
erhalten. Die Vorschauantwort ergaenzt Quell-/Katalogbindungen und FORMAT-
Metadaten fuer beide Modi; alte Vorschauen vor dem Update neu berechnen.
`selection: null` wird vor Jobhashbildung wie ein fehlendes Feld behandelt,
damit alte Annahmebelege wiedergefunden werden. Nach Reload/Abmelden ist die lokale
Auswahl leer; nur minimale bestehende Auftrags-Recoveryhinweise bleiben im Tab,
keine Objektlisten/technischen Daten oder Zugangsschluessel. Quellwechsel verwirft
die Auswahl, spaete Antworten nach Auswahlwechsel werden nicht angewendet.
Echte NX-/Maschinen-/Assistant-Nachweise bleiben separate spaetere Tests.

### Gepruefter Stammdatenexport

„Neuen Export ableiten“ oeffnet im Verwaltungsreiter eine eigene Exportmaske.
Bezeichnung eingeben und „Exportvorschau pruefen“ ausfuehren. Die Vorschau erstellt
keinen neuen NX-Ordner und aendert weder Stammdaten noch Freigaben. Sie zeigt
neue, aktualisierte, unveraenderte und stillgelegte Objekte sowie zurueckgestellte
Entwuerfe. Ein Entwurf ersetzt keine bereits im Elternordner enthaltenen Daten.
Reine Katalogdaten ohne NX-Adapter werden ausdruecklich als solche angezeigt.

Bestehende Katalogwerte koennen zuvor im Editor separat auf das Schema eines
neueren Datenstands umgestellt werden (siehe „Schemaumstellung im Editor“).
Das ersetzt keine fachliche Freigabe und erweitert keine Exportrechte.

Neue FORMAT-Spalten erhalten separate Eingabefelder. Jeden Wert bewusst
bestaetigen (auch ein Leerwert muss bestaetigt werden), danach die Vorschau erneut
pruefen. Werte gelten fuer die betroffenen Objekte desselben Bibliotheks-/
FORMAT-Blocks. Entfernte, umbenannte oder mehrdeutige Spalten werden nicht geraten.
Der neue Ordner kann erst aus einer exportierbaren Vorschau erstellt werden.
Der Server bindet die Vorschau an Eltern-Manifest, Stammdatenrevisionen,
Spaltenwerte und Anhaenge; spaetere Aenderungen sperren die Erstellung.

Die Spaltenfelder und Stammdatenlinks gehoeren zur aktuellen sichtbaren
Exportvorschau. Nach Schliessen, Elternwechsel (auch hin und zurueck), neuer
Vorschau, Katalogrevision oder Abmeldung koennen alte Handler keine neuen
Spaltenwerte oder Editorentwuerfe mehr ersetzen. Im anderen Arbeitsbereich
sind diese Aktionen nicht wirksam. Mehrere Spalten derselben gueltigen
Vorschau koennen weiterhin nacheinander bestaetigt werden; die erste Wahl
sperrt die Exportbereitschaft, nicht die restlichen Entscheidungsfelder.

Auch Dateiplanlinks und native Objektvergleichszeilen gelten nur fuer ihre
aktuelle Seite/Vorschau und geoeffnete Vergleichsansicht. Ein Filterwechsel
hin und zurueck reaktiviert keine alte Zeile. Innerhalb eines aktuellen
Objektindex bleiben dagegen verschiedene Objekte und Vorher-/Nachher-Seiten
auswaehlbar. Stammdatenlinks oeffnen die passende aktuelle Revision, speichern
aber nichts automatisch. `tests/test_export_decision_context_browser.py`
prueft diese Grenzen sowie die weiterhin erfolgreichen aktuellen Ablaeufe.

Bibliothek und Schluessel haben genau einen Katalogbesitzer, auch fuer Entwuerfe
und stillgelegte Datensaetze. Die Bestandsuebernahme erkennt bereits verwaltete
Objekte statt sie unter einer zweiten ID anzulegen. Abweichungen werden als
Konflikte angezeigt. Gespeicherte Typen sowie importierte/exportierte Schluessel
bleiben unveraenderlich. Eigene Objekte koennen in neuen Staenden aktualisiert,
stillgelegt und erneut freigegeben werden; Siemens-Standardobjekte und historische
Staende werden nicht ueberschrieben. Referenzen werden gegen die geplanten
Bibliotheken geprueft, damit z.B. ein noch benoetigter Halter nicht entfernt wird.

Die GUI nutzt `POST /api/v1/releases/{id}/derive-preview` und uebergibt dessen
`preview_hash` an `/derive`. Aeltere API-Clients koennen weiterhin ohne diesen
Parameter arbeiten; der Server fuehrt auch dann die komplette Vorpruefung aus,
aber es liegt keine vom Client bestaetigte Vorschau vor. Planungskonflikte werden
vor dem Anlegen eines Ordners zurueckgewiesen; echte Kopier-/Schreibfehler danach
behalten ihren fehlgeschlagenen Release-Nachweis. Export und Abnahme aktivieren
weiterhin nichts automatisch. Die gesamten STEP-/Maschinenpaket- und
Versionswechsel-Module sind damit noch nicht fertiggestellt.

### Integritaet, Audit und separate Vergleichsanker

„Integritaetspruefung“ fuehrt zum Verwaltungsreiter. Eine schreibfreie Pruefung
liest die gesamte Audit-Kette (nicht nur die letzten 200 Anzeigeeintraege),
SQLite-Struktur, Ressourcenrevisionen, Kennungen, ERP-Verknuepfungen, Eltern-/
Aktivzeiger, Abnahmebelege und die bekannten Dateinachweise. Die Checkbox
„Auch verwaltete Dateien ...“ prueft zusaetzlich Release-Manifeste, Anhaenge,
hochgeladene Syslogs, Test-Starter, STEP-Pakete und Migrations-/Extraktionsberichte.
Konfigurierte externe Quellen und produktive Laufwerke werden nicht gescannt.

Neue Aenderungsereignisse binden die betroffenen Verwaltungszeilen einschliesslich
ihrer Spalten mit SHA-256 an die Audit-Kette. Alte Eintraege werden **nicht**
umgeschrieben. Fehlt eine solche historische Bindung, erscheint „Nur teilweise
nachweisbar“ mit genauer Abdeckungsangabe. Neue SQLite-Verwaltungsspalten werden
nicht stillschweigend durch alte Hashes abgedeckt. Schreibvorgaenge nennen ihre
Spalten explizit; unbekannte zusaetzliche Verwaltungsspalten bleiben beim Update
erhalten. Diese interne SQLite-Pruefung ersetzt nicht die separate NX-DAT-
Schemaumstellung oder fachliche Maschinen-/Geometrieabnahme.

Pruefauftraege laufen im Hintergrund, mit Phase/Dateizaehler und erneuter Abfrage.
Ein laufender Reader wird nicht wegen einer Abfrage-Zeitueberschreitung neu
gestartet. Maximal ein solcher Auftrag gleichzeitig; die letzten 20 Ergebnisse
bleiben **nur in derselben Server-Sitzung** erhalten. Serverneustart entfernt
diesen RAM-Verlauf. Keine automatische Wiederaufnahme, Reparatur oder Freigabe.

Die Anzeige unterscheidet:

- **Nachweise konsistent:** die ausgewaehlten internen Nachweise passen; keine
  behauptete kryptographische Authentizitaet oder Garantie fuer spaetere Aenderungen.
- **Nur teilweise nachweisbar:** z.B. alte ungebundene Metadaten, ausgelassene
  Dateipruefung oder bekannte fehlgeschlagene/unmanifestierte Teilstaende.
- **Abweichungen gefunden:** Hash-, Revisions-, Referenz-, Zeiger- oder Dateifehler.
  Originale bleiben unveraendert; nicht als gesunden Vergleichsanker exportierbar.
- **Waehrend Pruefung geaendert:** Datenbank-Snapshot war nicht stabil. Das ist
  kein sicherer Korruptionsbeleg; nach tatsaechlichem Writerende erneut pruefen.

Das Ergebnis zeigt seinen Abschlusszeitpunkt. „Ergebnis ansehen“ laedt den alten
Pruefstand, nicht eine neue Dateipruefung. Datenbank wird in einer konsistenten
Lesetransaktion erfasst und nach der Dateipruefung erneut verglichen. Dateien
werden begrenzt/streamend gegen Manifest/SHA gelesen; es ist **kein atomarer
Dateisystem-Snapshot** und keine NX-Lade-/Kollisionspruefung.

Ein bewusst gepruefter Ausgangsstand kann als JSON-Vergleichsanker heruntergeladen
und separat zugriffsgeschuetzt aufbewahrt werden. Er enthaelt Auditposition und
Hash-Bindungen der Verwaltungszeilen, keine Dateiinhalte oder Zugangsschluessel.
Beim spaeteren Pruefen optional wieder hochladen (UTF-8, auch BOM, max. 8 MiB).
Neuer legitimer Auditstand erweitert/ersetzt die passenden Zeilenbindungen;
Loeschung, Ruecksetzung oder komplette Neuberechnung der alten Kette widerspricht
dem separat gepinnten Auditkopf. Alte Datensaetze lassen sich so ab einem bewusst
akzeptierten Ausgangsstand binden, ohne ihre fruehere Echtheit zu behaupten.

**Grenzen:** Der Anker ist nicht signiert und kein Datenbackup. Wer sowohl
Datenbank als auch separat aufbewahrten Anker kontrolliert, kann beide ersetzen.
Dienst-/Dateiberechtigungen, unabhaengige Sicherung und ggf. externe Signatur/
Archivierung bleiben noetig. Der Pilot hat noch keinen produktiven SMB-Dienst;
der unten beschriebene Snapshot-/Recovery-Ablauf bleibt lokal und unsigniert.
Archiv-/Fehlversuchsdateien ohne fertigen
Manifestnachweis werden gekennzeichnet, nicht geloescht oder als intakt bestaetigt.
Die Detailanzeige ist auf 500 Fehler und 500 Hinweise begrenzt; Gesamtzaehler
werden weiterhin fuer alle gefundenen Befunde gefuehrt.

### Vollstaendige verwaltete Sicherung und isolierte Wiederherstellung

Unter **Stammdaten verwalten** → **Sicherung und isolierte Wiederherstellung**
werden Datenbank und verwaltete Dateien gemeinsam gesichert. Enthalten sind
alle registrierten NX-Ordner, auch ausserhalb des privaten Verwaltungsordners,
Anhaenge, hochgeladene Protokolle, alle Test-Starter-Revisionen, STEP-Pakete,
private Extraktions-/Auftragsdateien und unvollstaendige Diagnosebestaende.
Noch nicht registrierte, fehlgeschlagene Build-Ausgaben werden ebenfalls aus
den administrativ freigegebenen Zielbereichen erfasst. Abgebrochene Dateien
erhalten Snapshot-Hashes, aber keinen nachtraeglichen CAM-/Manifestnachweis.

Nicht enthalten: Originalquellen/Siemens-Installationen, konfigurierte externe
Protokolldateien, Serverkonfiguration, Serverzugangsschluessel und aeltere
Sicherungsarchive. Diese Betriebsbestandteile separat sichern und nach einem
Ausfall wieder konfigurieren. Es werden keine Installer, Skripte oder NX-Prozesse
aus den gesicherten Dateien ausgefuehrt.

1. **Vollstaendige verwaltete Sicherung erstellen.** Aeltere Metadaten ohne
   Audit-Bindung ggf. erst bewusst als geprueften Ausgangsstand akzeptieren.
   Inkonsistente Audits/Metadaten oder veraenderte abgeschlossene Artefakte
   sperren den Abschluss. SQLite wird konsistent per Backup-API kopiert;
   Dateien werden inventarisiert, gegen die kopierten Metadaten geprueft und
   beim Archivieren erneut SHA-geprueft. Abschliessend Datenbank und gesamte
   Dateiliste erneut vergleichen. Kein atomarer Dateisystem-Snapshot behauptet.
2. **Archiv pruefen** und **Archiv / Pruefsummenbeleg herunterladen.** Ein
   ZIP64-Archiv speichert identische Dateien nur einmal, nach SHA adressiert.
   Die Datei-Zuordnungen und leeren Ordner bleiben im Manifest erhalten.
   Ganze Archiv-SHA, Manifest, Objekt-SHA/Groessen und SQLite/Audit werden geprueft.
   Grosse Browserdownloads werden ueber die Dateispeicher-API gestreamt;
   ohne diese API nur maximal 64 MiB, kein mehrgigabytegrosser RAM-Download.
   Der Browser berechnet SHA-256 fortlaufend aus den empfangenen Bytes und
   vergleicht SHA und exakte Groesse mit dem Beleg. Erst danach wird der
   Dateischreibstrom geschlossen bzw. der begrenzte Browserdownload gestartet.
   Verkuerzte, verlaengerte oder gleich grosse manipulierte Antworten, verlorene
   Sitzungen und Schreibfehler erzeugen keine Erfolgsmeldung. Solange der
   Dateidialog/Download laeuft, wird kein zweiter Dialog gestartet.
3. **Archiv und Wiederherstellungsziel pruefen.** Sicherung, freigegebenen
   Wiederherstellungsbereich und kurzen NEUEN Unterordnernamen waehlen.
   Standardbereich: `data_dir`-Elternordner / `Recovered`. Vorschau ist an
   Archiv/Beleg und Zielentscheidung gebunden; belegte Ziele, unportable Pfade,
   Windows-Pfadlaengen oder fehlender Speicher sperren die Wiederherstellung.
4. **Unsignierten Stand bewusst akzeptieren** und als neuen Ordner wiederherstellen.
   Kein bestehendes Verzeichnis wird ersetzt oder geloescht. Alte Auditzeilen,
   Ressourcenrevisionen und Nachweisdateien bleiben erhalten. Physische Release-
   Pfade werden im neuen Datenbestand umgebunden und durch EIN neues Restore-
   Audit mit aktuellem Zeilen-SHA dokumentiert; keine alten Audits umschreiben.
   Urspruengliche DB/Manifest/Beleg liegen zusaetzlich unter `retained/recovery`.
5. Der Aktivzeiger wird ausschliesslich im neuen Bestand geleert; zuvor
   freigegebene/gepruefte Releases werden dort Entwuerfe mit erhaltenen alten
   Nachweisen. Bereits gesperrte/fehlgeschlagene Releases bleiben so markiert.
   Neue Test-Starter zeigen auf die neuen Ordner. Extraktionsberichte bleiben
   bytegleich; private SIDT-Artefakte werden am neuen Ort gelesen, nie heimlich
   am alten Host. Historische Build-/Workerauftraege werden nicht wiederholt.
   Die NX-Dateibytes bleiben absichtlich erhalten: eingebettete absolute Pfade
   in DAT/PAX/XML/Modellen werden NICHT automatisch fachlich umgedeutet. Diese
   beim erneuten NX-Test pruefen und erforderliche Anpassungen als neuen
   versionierten Datenstand vorbereiten; Restore ist keine CAD-Konvertierung.
6. Erst nach bestandener Integritaetskontrolle entfernt der Worker seinen eigenen
   `RECOVERY_PENDING.json`-Startschutz. Bei einem Fehler bleiben Teilstand und
   Schutz erhalten; der Server verweigert dessen Start. Neues Ziel fuer einen
   neuen Versuch verwenden, keine automatische Reparatur/Loeschung.
   `settings.recovered.json` enthaelt nur den neuen Datenpfad und leere externe
   Konfiguration: passende NX-Installationen konfigurieren und erneut in NX
   testen/abnehmen. Der laufende Verwaltungsserver wird nicht umgeschaltet.

Snapshots liegen in `data_dir/b/<erste 12 Hexzeichen der Backup-UUID>`:
`NX_Backup.zip` und `receipt.json` gehoeren zusammen. Fuer einen frischen Server
beide unveraendert in dessen entsprechend benannten `data_dir/b`-Unterordner
kopieren (keine vorhandene Sicherung ersetzen). Die GUI erkennt den Beleg;
Pruefung/Restore greifen nur auf das Archiv zu, nicht auf Originalordner des
alten Servers. Die Quell-DB ist im Archiv enthalten. Dienstkonfiguration und
Zugangsschluessel sind absichtlich nicht im Snapshot.

Auftragsmetadaten/Berichte liegen separat unter `data_dir/b/jobs`; ein Neustart
bewahrt sie. Unterbrochen wird nur nach nachgewiesenem Tod/PID-Lebenszeitwechsel
des Besitzers. Ein lebender/unbekannter Prozess oder wirklich laufendes Future
verhindert einen neuen Auftrag. Beobachtungsfehler starten nichts neu. Waehrend
einer Operation sind Admin-/ERP-Schreibaktionen und neue POST-Vorschauen pausiert;
GET-Lesezugriffe bleiben moeglich. Vollstaendige Archive werden im Quell-Audit
nach dem Snapshot-Abschluss als `backup.created` dokumentiert.

Grenzen des aktuellen Formats: 100.000 Dateien, 256 GiB logische Dateidaten,
64 MiB Manifest; UTF-8/portable Pfade und unverschluesseltes ZIP_STORED/ZIP64.
Kein automatisches Retention-Loeschen, keine Live-Reparatur, produktive
Umschaltung, Terminplanung, Offsite-Uebertragung oder externe Signatur.
Eine Sicherung auf demselben Datentraeger schuetzt nicht vor dessen Ausfall.
Beleg separat zugriffsgeschuetzt aufbewahren: SHA ersetzt weder Signatur noch
Echtheitsnachweis, wenn ein Administrator Archiv UND Beleg austauschen kann.

Streaming-SHA nutzt eine lokal mitgelieferte, unveraenderte SHA-256-Ausgabe von
[jsSHA](https://github.com/Caligatio/jsSHA), Version 3.3.2, fest an Commit
`694747f7ba4d4493208ea7a259438eaab71917a1` und Bundle-SHA
`932fa0e677f4866f8a54c958acf20401372069c36ae067b5920f58c0feb5a464` gebunden.
Herkunft und BSD-3-/Apache-2.0-Lizenztexte liegen unter
`server/nxserver/web/vendor`; kein CDN oder externer Laufzeitabruf.
Der Upstream-Tag ist kein hier verifizierter Signaturnachweis. Der Browserdialog
bestimmt das Speicherziel: der Server kann dessen lokale Dateirechte/Dauerhaftigkeit
nicht pruefen. Abmelden verwirft spaete UI-Antworten und versucht laufende
Schreibstroeme abzubrechen; ein bereits begonnener/fertiger Datei-Commit wird
nicht rueckgaengig gemacht. Die Empfangspruefung ersetzt keine Signatur,
Offsite-Sicherung oder erneute fachliche NX-Abnahme.

Admin-API: `POST /api/v1/integrity` mit `include_files` und optional `anchor`,
`GET /api/v1/integrity` fuer Sitzungsauftraege und `GET /api/v1/integrity/{id}`
fuer Fortschritt/Ergebnis. Kein Pfad oder Reparaturauftrag wird angenommen.
ERP-Schluessel duerfen diese Diagnosen/Checkpoint-Metadaten nicht abrufen.

### Sicherungsauftraege nach verlorener Antwort wiederfinden (14.10.2026)

Alle vier Admin-Auftrags-POSTs (`/backups`, `/backups/{id}/verify`,
`/restore-preview`, `/restore`) unterstuetzen jetzt einen kanonischen UUID-
`Idempotency-Key`. Die GUI verwendet ihn immer. Modus, Eingangsarchiv und
normalisierte Entscheidungen einschliesslich Ziel/Preview/Unsigned-Zustimmung
sind gebunden. Annahme, originale Job-ID/Owner-Pins und Audit committen in
EINER SQLite-Transaktion VOR Journalpublication/Workerstart. HTTP 202 ist nur
Annahme, keine fertige Sicherung oder erfolgreich wiederhergestellter Bestand.

Gleiche Kennung/gleiche Daten liefern ausschliesslich die originale Annahme,
auch bei laufendem, fehlgeschlagenem oder unterbrochenem Auftrag. Kein zweiter
Worker/Archiv-/Restoreversuch; keine erneute Eingangsarchiv-Suche bei Replay.
Andere Aktion/Archiv/Ziel-/Vorschauentscheidung: 409. `source_backup_id` ist
der unveraenderliche Eingang; `backup_id` kann bei Modus `backup` spaeter die
NEU erzeugte Ergebnis-ID enthalten. Diese Rollen werden nicht verwechselt.
Aktuellen Status/Bericht ueber `backup-jobs/{id}` bzw. `/report` lesen.

Admin-`GET /api/v1/backup-requests/{key}` liefert Vertrag 1 mit `accepted`
oder `cancelled`, Originalantwort und ggf. nachgewiesener Fehlerbeobachtung.
404 ist kein Stopnachweis. `POST .../{key}/cancel` sperrt nur VOR Annahme;
angenommene Worker werden nie beendet/neu gestartet und Teilordner nie
geloescht. Waehrend einer aktiven Sicherung darf ein bestehender Beleg gelesen
oder identisch wiederholt werden, aber keine neue Metadaten-Fence den erfassten
DB-Stand veraendern. Eine noch fehlende Kennung daher erst nach Writerende sperren.
Andere CAM-/ERP-/Ordnerschreibaktionen bleiben pausiert. ERP hat keine Rechte
auf die Admin-Annahme-/Sicherungsaktionen. Headerlose Clients bleiben kompatibel,
ohne vorgetaeuschten Wiederanlaufvertrag.

Im Verwaltungsreiter bleiben ungeklärte Auftraege sichtbar: Annahme pruefen,
bewusst exakt wiederholen oder nur vor Annahme sperren. In `sessionStorage`
stehen ausschliesslich Vertrag/Kennung/Modus/Zeit, keine Tokens, Archiv-/Zielpfade
oder Entscheidungsdaten. Originalbody nur RAM. Nach Reload nur Beleg/Fence.
Neue Zielnamen/Auswahlen und neue Sitzung werden nicht durch alte Antworten
zurueckgesetzt. Eine alte Vorschau aktiviert nach Auswahlwechsel weg/zurueck
oder Reload keine Wiederherstellung; neue passende Vorschau bewusst pruefen.

Fehlgeschlagene Journalpublication/Executor-Submission hinterlassen einen
angenommenen, aber fehlgeschlagenen Auftrag statt ewiger Schreibpause. Ein
spaet auftauchender, zuvor nur enqueued Callback prueft den Startfence VOR
Dateiarbeit. Ein bewiesen terminaler Future oder beendeter/wiederverwendeter
Owner ergibt Diagnose ohne Neustart; lebender/unverifizierbarer Owner ohne
Journal bleibt dagegen ungeklärt. Auch spaeteren Ownerverlust erneut pruefen.
Kann das Fehlerjournal nicht geschrieben werden, bleibt die nachgewiesene
Beobachtung separat SQL-/auditgebunden erhalten. Kein Erfolg aus Timeout.

`backup_requests` gehoert zur Metadatenintegritaet und Vollsicherung/Restore.
Sicherungsarchive sichern bewusst NICHT sich selbst und ihre `b/jobs`-Journale.
Die enthaltenen Annahmen sind damit historische Belege, keine transportierten
Worker/Archive. Sie binden den urspruenglichen Verwaltungsroot. Nach isoliertem
Restore werden solche Operationen ohne Originalpfadzugriff als Historie gezeigt,
niemals als aktuelle Writer oder neu auszufuehrende Auftraege. Benoetigte
Archive/Belege separat kopieren wie oben beschrieben. Alte Archive ohne Tabelle
erst mit originalem Snapshot-Hash pruefen, dann leere Tabelle bei Restore
ergaenzen. Weiterhin unsigniert; keine automatische Aktivierung/NX-Abnahme.

### Neue Kundenentwuerfe aus vorhandenen Bibliotheksobjekten (14.10.2026)

Im Bibliotheksbestand unter **Stammdaten verwalten** kann jede aktive Gruppe mit
„Neuen Entwurf ableiten“ als Vorlage gewaehlt werden. Quelle bleibt ein
manifestgebundener Datensatz aus dem explizit gewaehlten NX-Datenstand, nicht
eine beliebige Datei oder ein auskommentiertes Beispiel. Im Vorbereitungsreiter
eine eigene neue Bibliotheksnummer/NAME (maximal 116 Zeichen) und Bezeichnung
eingeben, Kopiervorschau pruefen und erst danach die Editoruebernahme bestaetigen.

Die ganze FORMAT-/RTYPE-/SEQ-Gruppe wird kopiert, nicht nur ihre erste Zeile.
Die Kopiervorschau zeigt alle Feldwerte seitenweise als Vorlage/Neuentwurf
(25 Feldwerte je Seite, Originalzeile und FORMAT bleiben sichtbar). Die
vollstaendige bereits geladene Quellgruppe wird im Browser festgehalten;
Vorschau und Editorantwort muessen ihr bis auf die ausdruecklich neue Kennung
exakt entsprechen. Fehlende Sektionen, geaenderte Masse/Verweise, widerspruechliche
Zeilen-/Freigabeangaben oder geerbte Rechte/Anhaenge/Geometriebelege sperren die
Editoruebernahme. Auswahlwechsel und Fehler entfernen alte Vorschauwerte,
aber keine Daten aus dem aktuellen Editor. Originalwerte bleiben unveraendert.
Nur die jeweiligen Identitaetsspalten werden geaendert; Namen/Beschreibungen in
anderen Spalten, Masse, Verweise und Graphics-Namen bleiben Vorlagenwerte und
muessen fachlich geprueft werden. Quelle und andere Datensaetze unveraendert.
Es werden keine ERP-Verknuepfungen, Herkunfts-/Ueberschreibungsrechte, Anhaenge,
Modellpakete oder CAM-/Geometriefreigaben kopiert. Der neue Editor ist Entwurf
mit erneut erforderlicher Vorbereitung. Das ist weder automatische 3D-Aufbereitung
noch Versions-/Schema-Konvertierung.

Die Editoruebernahme ersetzt bestehende Editorwerte nur nach ausdruecklicher
Bestaetigung. Vorschau/Oeffnen tun das nicht. Eine spaetere Editor-/Vorlagen-/
Sitzungs-/Auswahl-Aenderung verwirft alte Antworten; unvollstaendiges neues
Editor-JSON bleibt erhalten. Laufende/ungeklaerte Speicherversuche und Uploads
muessen zuerst geklaert werden. Erst anschliessend legt der normale Speichervorgang
mit atomarer Identitaetspruefung den neuen Stammdatensatz an. Ein nach der
Vorschau neu hinzugekommener Besitzer kann nicht doppelt gespeichert werden.

Admin-API `POST /api/v1/releases/{id}/library-clone/preview` mit `library_id`,
`source_key`, manifestgebundenem `source_sha256`, `new_key`, `name`; `/apply`
zusaetzlich mit `preview_hash`. Beide nur Editorvorbereitung, ohne SQL-/Datei-/
Auditwrite. Vorlagen-SHA, ganze Gruppe, Zielkennung und Katalogzustand sind
vorschaugebunden. Belegte Standard-/Kundenkennungen und reservierte Entwurfs-/
Stilllegungskennungen sperren die Kopie. Vor Rueckgabe Source/Katalog erneut
pruefen. Capability `native_cloning`, Vertrag 1; keine neue SQL-Tabelle.
Normale Datei-/Referenz-/Geometrie-/NX-Exportpruefungen bleiben erforderlich.

### Vollstaendige native Gruppen im Stammdateneditor bearbeiten (14.10.2026)

Die Entwurfsanlage liest seit 15.10.2026 keine ersten100 Bibliotheksobjekte mehr,
um nur deren FORMATs zu erhalten. „Bibliotheken fuer Entwurf laden“ liest nur
die vorhandenen Standardpfade, Groessen und SHA aus einem SQLite-Lesestand des
verwalteten Manifests. Keine DAT-Datei, Baseline oder Bibliotheksreferenz wird
dabei eingelesen. „Schema laden“ prueft anschliessend nur die gewaehlte DAT-Datei
gegen ihre exakte Groesse/SHA und denselben Inventarstand; maximal32MiB.
Der Schema-Lesemodus prueft native Zeilen und Fortsetzungen, sammelt aber keine
Wertwoerterbuecher, Objektgruppen oder Rohdatensatz-Tokens im Speicher.
Die Antwort enthaelt nur Definitionen und Zeilenanzahl, keine Objektwerte.
Objektansicht, Vorlagenableitung und Bestandsuebernahme bleiben getrennte Wege.

Entwurfsinventar: `GET /api/v1/releases/{id}/library-schemas`.
Gewaehltes Schema: `GET /api/v1/releases/{id}/library-schemas/{library_id}` mit
dem zwingenden `inventory_sha256` aus dem Inventar. Beide nur fuer Administratoren,
schreibfrei, kein neues Datenmodell. Manifestmetadaten maximal32MiB/100000
Eintraege, Definitionen maximal1000 FORMATs/100000 Spalten, Antwort maximal4MiB.
Der Lesestand bleibt zwischen SQL-Groessenpruefung und Datenlesen fest; nach
der ausgewählten Bytepruefung wird die Metadatenbindung nochmals gelesen.
Keine Behauptung eines atomaren Dateisystem-Snapshots oder einer NX-Freigabe.

Die GUI prueft Bericht-/Definitionenhash, Bibliothek, Typ, Version, Quellordner,
Manifest-/Dateibelege und vollstaendige FORMAT-Folge vor der Freischaltung.
Bei alter Serverversion, ungueltiger Antwort, Quelle-/Auswahlwechsel oder
veraltetem Inventar bleibt die Entwurfsanlage gesperrt. Kein Rueckfall auf
ungepruefte alte Objektseiten. Eine neue Schemaabfrage verwirft nur deren alte
Bereitschaft; vorhandene CAM-Werte und fokussierte Eingabefelder bleiben stehen.
Erst der ausdrueckliche neue Schemaentwurf ersetzt den Editor. Er enthaelt alle
Spalten, aber keine geratenen Werkzeugwerte, ERP-Bezuege oder Freigaben.

Die Spaltenmaske zeigt hoechstens zehn Objektzeilen und je Zeile 50 Spalten.
Weitere Zeilen/Spalten bleiben im vollstaendigen CAM-Datensatz enthalten.
FORMAT-Blockfilter, Spaltennamensuche und Sprung zur aktuellen Gruppenzeile
veraendern nur die Anzeige, nicht die Werte, Geometriepruefungen oder Freigaben.
Die Zeilennummern beziehen sich weiterhin auf die ganze Gruppe, auch wenn nur
ein Block sichtbar ist. Speichern uebernimmt stets alle Zeilen und Spalten.

Einzelne Zeilen lassen sich seit 15.10.2026 direkt an der jeweiligen Zeile
mit „Zeile … aus Entwurf entfernen“ nach ausdruecklicher Bestaetigung entfernen.
Betroffen ist nur der ungespeicherte Editor, einschliesslich ausgeblendeter
Spalten dieser einen Zeile. Alle anderen Werte, die Gruppenkennung und auch
danach unbenutzte FORMAT-Definitionen bleiben erhalten. Keine automatische
Neunummerierung von SEQ/RTYPE oder Umschreibung von Verweisen/GRAPHICS.
Nach dem Entfernen zeigt die Maske die benachbarte aktuelle Gruppenzeile
ohne Block-/Spaltenfilter. Die letzte Objektzeile kann nicht entfernt werden;
zum Entfernen eines ganzen eigenen Objekts dessen Stilllegung verwenden.
Veraltete Masken, laufende/ungeklaerte Saves oder Uploads, Siemens-Standard-
importe und gebundene Modellpakete sperren Strukturaktionen auch bei einem
alten Buttonereignis; das gilt ebenfalls fuer „Zeile aus gewaehltem FORMAT
anhaengen“. Kundenentwurf ableiten bzw. Paketbindung ausdruecklich loesen.
Technische Pruefungen werden verworfen, `approved` wird `draft`, `retired`
bleibt stillgelegt. Erst bewusst speichern; historische Revisionen/Quellen
und Exportordner werden nicht geaendert. Eine neue Entwurfsrevision ersetzt
noch keine bereits exportierten Zeilen; erst fachliche Pruefung/Freigabe und
ein neuer Export uebernehmen die geaenderte gesamte Gruppe.

Jedes Eingabefeld ist an den aktuellen Editor und dessen vollstaendige
CAM-Rohdaten gebunden. Nach manueller Rohdatenaenderung bleibt die alte Maske
gesperrt, auch wenn spaeter wieder derselbe Text eingegeben wird. Mit
„Spaltenmaske aus CAM-Daten anzeigen“ die neuen Daten ausdruecklich laden.
Ungueltiges/unvollstaendiges JSON oder nicht passende Zeilen/FORMAT-Definitionen
bleiben als Rohtext erhalten; daraus wird keine teilweise Maske erzeugt.
Ein altes Feld darf weder umsortierte Zeilen noch einen neuen Editor aendern.

Technische Wert-/Zeilenaenderungen setzen den bisherigen Exportstatus
`approved` auf `draft` und verwerfen alte Geometrie-/Dateipaketpruefungen.
Ein bereits stillgelegter Datensatz wird dadurch nicht reaktiviert. Reine
Anzeigewechsel tun das nicht. Bibliothek und Datensatztyp muessen in der GUI
vor einem Speicherversuch zusammenpassen; neue Speicherversuche beginnen
weiterhin mit dauerhaften Schreibbelegen.

Bei mehrzeiligen Gruppen bleibt die Kennungsspalte in den Einzelzeilen lesend.
Eine noch ungebundene Gruppenkennung kann ausdruecklich in allen Zeilen
gleichzeitig geaendert werden, auch in ausgeblendeten Bloecken. Nur die
tatsaechliche Kennungsspalte `LIBRF`, alternativ `NAME`, wird geaendert:
eine daneben vorhandene `NAME`-Beschreibung bleibt erhalten. Bestaetigung,
nichtleere Kennung ohne Rand-Leerzeichen/Datentrennzeichen und maximal1024
Zeichen erforderlich. Importierte, exportierte und modellgebundene Kennungen
bleiben gesperrt; laufende/ungeklaerte Saves oder Uploads ebenfalls. Keine
automatischen Aenderungen von Verweisen, GRAPHICS, Anhaengen oder Dateinamen.

Neue Sektionen/Zeilen aus einem geladenen Schema uebernehmen die aktuelle
Gruppenkennung. Stimmen vorhandene FORMAT-Definitionen nicht mit dem geladenen
Schema ueberein, wird nichts angehaengt. Erfolg zeigt die neue letzte Zeile
ungefiltert an; alte Zeilen bleiben erhalten. Auch hierbei erst Werte
vervollstaendigen und separat als Entwurf speichern, keine NX-Freigabe.

Spaetere Eingaben bleiben bei bestaetigtem/verlorenem Speicherversuch erhalten.
Wird einem neuen Datensatz seine Server-ID zugeordnet, werden dieselben
aktuellen Eingabefelder ohne DOM-Neuaufbau/Fokusverlust neu gebunden. Das gilt
nur innerhalb derselben Editorsitzung mit unveraenderten CAM-Rohdaten;
andere/neue Editor-Kontexte werden nicht durch alte Antworten beschrieben.

### Gemeinsame CAM-Vertraege fuer GUI, API und interne Schreibpfade

Native CAM-Daten muessen auch als Entwurf oder stillgelegter Datensatz eine
vollstaendige Objektgruppe mit passenden FORMAT-/Klassen-/Felddefinitionen
bilden. Der Katalogtyp muss zur registrierten NX-Bibliothek passen. Diese
Pruefung gilt jetzt auch zentral im Store, nicht nur in der HTTP-Eingangspruefung.
Sie erfolgt vor Dateien-/Paketvalidierung und dem gemeinsamen SQL-Commit von
Ressource, neuer Revision, Schreibbeleg und Audit. ERP-Updates duerfen vorhandene
widerspruechliche CAM-Daten nicht in eine weitere Revision fortschreiben.
Leere CAM-Entwuerfe aus dem ERP bleiben erlaubt; kaufmaennische Updates aendern
keine technischen CAM-Werte oder Freigaben.

Maschinen und Spannmittel koennen alternativ reine Dateipakete ohne DAT-Zeile
verwalten (`cam: {}`). Deren explizite Freigabe ist jetzt auch ueber die GUI/
HTTP-API moeglich. Paket vorhanden bedeutet nicht Paket geprueft: vorgesehene
Standardpfade, originale Basenames, valide XML-Dateien soweit zutreffend,
zugeordnete unveraenderte Anhaenge, exakte Hashbindung, vier menschliche
Pruefungen und aussagekraeftiger NX-Beleg bleiben erforderlich. Beliebige
Rohdaten statt DAT-Gruppe oder fehlende/ungepruefte Pakete sind kein Ersatz.
Werkzeuge brauchen weiterhin technische CAM-Daten und Geometrienachweis.

Die Integritaetspruefung meldet widerspruechliche aktuelle Metadaten als
`catalog_cam_contract`; sie repariert oder qualifiziert nichts automatisch.
Historische Payloads, Revisionsantworten und Auditzeilen werden nicht neu
geschrieben. Ein direkt gespeicherter Altbeleg bleibt historisch lesbar und
kann aus dem Store unter seiner alten Kennung wiedergefunden werden; das
behauptet keine heutige Gueltigkeit/Freigabe. Alte Fehlzuordnungen ausdruecklich
in einer neuen Revision korrigieren. Backups/Restore akzeptieren keine aktuell
inkonsistenten Metadaten als fehlerfrei. Alle Aenderungen bleiben lokal;
kein Produktiv-/SMB- oder automatischer Aktivierungsauftrag.

### Stammdatenhistorie und lesender Revisionsvergleich (14.10.2026)

Unter **Stammdaten verwalten** am Datensatz **Historie** waehlen. Die Liste
zeigt jeweils 20 Revisionen mit Akteur, Zeitpunkt, Metadatenumfang und
Byte-SHA-256. Weitere Seiten behalten bereits geladene Revisionsauswahlen.
Vorher-/Nachher-Revision ausdruecklich waehlen und **Ausgewaehlte Revisionen
vergleichen** anklicken. Ein neuer Historienabruf waehlt die beiden neuesten
Revisionen vor, startet aber keinen Vergleich automatisch.

Der Vergleich zeigt Bezeichnung/Typ, CAM-Werte, ERP-Felder, Exportstatus,
Geometrie-/Pruefmetadaten, Anhaenge, Modell-/Dateipakete, Herkunft und weitere
Felder. Bereichsfilter und Suche gelten fuer alle Unterschiede; die Anzeige
ist auf 50 Unterschiede pro Seite begrenzt. Gesucht wird auch in vollstaendigen
Werten, wenn deren Anzeige auf 1200 Zeichen gekuerzt ist. Fehlend ist nicht
`null`; Zahlen und Wahrheitswerte werden nicht gleichgesetzt. Arrays werden
positionsbezogen verglichen, nicht automatisch ueber RTYPE/SEQ zugeordnet.

Ausgewaehlte Revisionsbytes und Folgeseiten sind per SHA gebunden. Ein neuer
Katalogstand, geaenderte historische Bytes oder unvollstaendige Historie
erfordern einen ausdruecklichen Neuabruf. Veraltete Antworten nach einer
anderen Auswahl, Dialogschluss oder Abmeldung werden verworfen. Unvollstaendige
oder zu tiefe/grosse Vergleiche werden nicht als vollstaendig angezeigt.

Dies ist ein reiner Metadatenvergleich: keine Ruecknahme alter Revisionen,
keine Editoruebernahme, keine Datei-/NX-Aenderung und keine neue Freigabe.
Die SQLite-Verbindung wird mit `mode=ro` und `query_only=ON` geoeffnet;
auch der Journalmodus wird dabei nicht geaendert. Grenzen je ausgewaehlter
Payload: 32 MiB, 64 Verschachtelungsebenen und eine Million JSON-Knoten.
Es werden keine CAD-Dateien oder gespeicherten Netzwerkpfade geoeffnet. Die
Bytebindung ersetzt weder Auditpruefung noch Dateiintegritaet oder fachliche
NX-Abnahme; diese bleiben eigene Pruefschritte. Der bisherige rohe
`GET /api/v1/resources/{id}/history`-Endpunkt bleibt erhalten.

### Bibliotheksverweise direkt im Stammdateneditor pruefen (14.10.2026)

Unter **Stammdaten anlegen & aufbereiten** → **Bibliotheksverweise im
Stammdateneditor pruefen / auswaehlen** einen verwalteten NX-Datenstand waehlen.
Die Vorschau zeigt bekannte Beziehungen je Zeile/Feld, vorhandene/fehlende
Schluessel und fehlende Zielbibliotheken. Leere Werte werden nicht automatisch
als fachlich falsch bewertet. Beschreibungsspalten werden nicht anhand ihres
Namens in Beziehungen umgedeutet: PARTMAT in Werkstoffdaten ist beispielsweise
keine Beziehung. Es gelten die registrierten Adapter der konkreten Bibliothek.

„Zielobjekt auswaehlen“ durchsucht Schluessel und Bezeichnungen der passenden
aktiven Zielbibliothek, 100 Optionen pro Seite. Nur manifestgebundene DAT-Bytes,
keine Installations-Fallbacks, unregistrierten Dateien oder auskommentierten
Beispiele. Auch bei einem einzigen Treffer wird nichts automatisch gewaehlt.
Exakt vorhandenen Schluessel ausdruecklich waehlen und nur die gewaehlte
Editorzeile uebernehmen. Andere Zeilen/Verweise, ERP-Felder, Anhaenge und NX-Dateien
bleiben unveraendert. CAM-Status wird Entwurf; bisherige Geometriechecks/Hashpins
werden ungueltig. Erst ausdrueckliches Speichern legt eine neue Revision an.

Neue Admin-API, Vertrag 1:
- `GET /api/v1/releases/{id}/reference-options` mit `library_id`, `field`,
  `query`, `offset`, `limit` (maximal 100; Suche maximal 200 Zeichen).
- `POST /api/v1/editor-references/preview` mit `target_release_id`, `kind`,
  `cam`, optional zusammen `resource_id`/`expected_revision`.
- `POST /api/v1/editor-references/apply` mit denselben Eingaben plus
  `preview_hash`, `row_index`, `field`, `value` (exakter aktiver Schluessel).

Die API aendert ausschliesslich zurueckgegebene Editorwerte, nicht Serverdaten,
Audits, Ordner oder Freigaben. Der Vorschauhash bindet Source-CAM, Ressource/
Revision, Zielmanifest und die verwendeten Referenzdateien. Vor Rueckgabe nochmals
Zieldatei/Manifest und Stammdatenrevision pruefen. Veraltete/unpassende Entscheidungen
werden abgewiesen, aehnliche Nummern sind keine automatisch bestaetigten Aliase.
Keine durable Schreibannahme erforderlich, weil diese Endpunkte nichts speichern.

Native mehrzeilige Gruppen und der alte Einzeilen-Werkzeugeditor bleiben in ihrer
jeweiligen Darstellung. Eine Auswahl aus NX2512 konvertiert ein NX2406-Schema
nicht automatisch; Schemaumstellung, Exportvorschau und echte NX-/CAM-Pruefung
bleiben separate Schritte. Die GUI verwirft spaete Antworten nach Editor-/Ziel-/
Auswahl-/Sitzungswechsel, auch nach Wechsel weg und zurueck. Kein Ruecksetzen
neuer Editorwerte oder automatisches Speichern. Capability `editor_references`;
keine neue SQL-Tabelle, kein Zugriff auf das Produktivlaufwerk.

### Katalogweite Schema-Arbeitsliste (15.10.2026)

Unter **Stammdaten verwalten → Stammdaten-Schemata fuer einen NX-Datenstand
pruefen** einen vollstaendigen Zieldatenstand waehlen und **Schema-Arbeitsliste
pruefen** anklicken. Die Uebersicht nutzt dieselben Projektionsregeln wie die
Einzelpruefung im Schemaeditor, ohne Entscheidungen vorwegzunehmen. Bibliothek,
Typ, CAM-Status, Befund und Volltextsuche grenzen die Arbeitsliste ein; jede
Seite zeigt maximal 50 Datensaetze. Die Zaehler beziehen sich auf den gewaehlten
Bibliotheks-/Typ-/Statusumfang vor Befund-/Suchfilter, nicht nur auf diese Seite.

| Befund | Bedeutung | Naechster Schritt |
|---|---|---|
| Bereits passend (`current`) | CAM-Darstellung entspricht der Projektion auf dieses Zielschema. | Keine Schemaaenderung vorgeschlagen; andere Pruefungen bleiben erforderlich. |
| Ohne neue Werte projizierbar (`ready`) | Werte koennen erhalten werden, etwa bei neuer Spaltenreihenfolge oder Versionskennung. | Im Einzel-Editor pruefen, erst danach als Entwurf speichern. |
| Entscheidungen erforderlich (`decisions_required`) | Neue Werte, Spaltenzuordnungen, Weglassen oder Ziel-FORMAT muessen bestaetigt werden. | Pro Zeile entscheiden; keine geratenen Defaults. |
| Blockiert (`blocked`) | Quelle/Ziel fehlt, ist geaendert, unlesbar oder ausserhalb der Adaptergrenzen. | Ursache klaeren; keine Editoruebernahme aus dieser Zeile. |
| Siemens-Standard (`read_only`) | Importiertes Standardobjekt, kein eigener Kundenentwurf. | Keine Umschreibung als Kundenanpassung. |
| Anderer Bearbeitungsweg (`not_applicable`) | Reine Metadaten/Dateipakete oder stillgelegter Datensatz. | Bestehenden Paket-/Stilllegungsablauf verwenden. |

**Im Schemaeditor pruefen** liest die exakte aktuelle Ressourcenrevision und
den Zieldatenstand erneut. Nach ausdruecklicher Bestaetigung wird nur der
Datensatz geoeffnet und das Zielschema ausgewaehlt. Spaetere Editorwerte, auch
Aendern-und-Zuruecksetzen, werden nicht durch eine verzoegerte Antwort ersetzt.
Schema-Vorschau, Entscheidungen, Uebernahme in den Editor und Speichern bleiben
getrennte Aktionen. Keine Sammelrevision, kein automatischer Save/Export, keine
Aenderung historischer Revisionen oder des ERP-Bezugs.

Zeilenaktionen gehoeren ausschliesslich zur aktuell angezeigten, geprueften
Arbeitslistenseite. Alte/ausgefilterte Zeilen oder Aktionen aus einem anderen
Reiter bzw. einer geschlossenen Arbeitsliste fragen nicht erneut nach einer
Editorersetzung und starten keinen Leseaufruf. Reiterwechsel und Schliessen
der Liste (Maus oder Tastatur, auch Schliessen-und-wieder-Oeffnen) verwerfen
eine bereits wartende Editoroeffnung. Eine neue bewusste Aktion ist danach
sofort moeglich; der spaete Abschluss der alten Anfrage sperrt/entsperrt keine
neuere. Das verwirft nur die Anzeigeabsicht, keinen serverseitigen Schreibauftrag.
Laufende/ungeklaerte Speicher- und Uploadvorgaenge bleiben auch waehrend der
Leseantwort geschuetzt. Das bewusste Oeffnen einer aktuellen Zeile behaelt die
bisherige Bestaetigung fuer das Ersetzen ungespeicherter Editorwerte.

Der Arbeitslistenbericht nutzt die gemeinsame Detail-Anzeigeabsicht. Die
gepruefte Liste kann im Hintergrund fertig werden, ohne nach Reiterwechsel,
bewusstem Schliessen der Details oder einer neueren Detailauswahl das globale
Detailsfenster wieder zu oeffnen oder dessen Inhalt zu ersetzen. Ein gemischtes
Clientpaket ohne den erforderlichen Reiterwechsel-Hook wird schon beim Laden
vor der Anmeldung gesperrt. Keine automatische Daten-/Schemauebernahme dadurch.

Erlaubte Zahlen in freien CAM-Metadaten (etwa `1.0`, `0.015`, Exponenten oder
grosse Ganzzahlen eines anderen Datensatzes) blockieren die Arbeitsliste nicht
mehr allein durch JavaScripts abweichende Zahlendarstellung. Beim Lesen bleiben
die urspruenglichen JSON-Zahlentexte fuer den Pruefsummenvergleich an ihre
Objekte gebunden. Die bestehenden serverseitigen Canonical-, Revisions- und
Audit-Pruefsummen werden nicht geaendert. Die Ressourcen-/Datenstand- und
Arbeitslistenantworten werden dabei auf 64 MiB begrenzt gelesen; unvollstaendige,
nicht passende oder veraltete Antworten bestaetigen keine Arbeitsliste.
Die Arbeitsliste selbst liest und prueft nur. Der freie CAM-JSON-Editor nutzt
den getrennt beschriebenen Zahlen-Schreibpfad; beliebig genaue Dezimalrechnung
ist damit nicht zugesichert. Native DAT-Spaltenwerte bleiben wie bisher Texte;
es erfolgt keine Einheitenkonvertierung.

Die Arbeitsliste ist an den sortierten vollstaendigen Katalog und die relevanten
Zielmetadaten gebunden. Folge- und Zurueckseiten behalten denselben Kontextpin.
Bei Drift zuerst Bestand aktualisieren und ausdruecklich eine neue Arbeitsliste
pruefen. Der Bericht hat einen rekonstruierbaren SHA, aber keine Signatur.
Ein Metadatensnapshot ist kein atomarer Dateisnapshot. Erfolgreich gelesene
Zielbibliotheken werden jeweils gemeinsam geladen und am Ende erneut byte-/SHA-
gebunden geprueft, nicht pro Stammdatensatz erneut geoeffnet. Der begrenzte Leser
prueft regulaeren geoeffneten Dateihandle, Groesse und Manifest-SHA; auch die
bisherige Einzel-Schemaanpassung nutzt ihn.

Explizite Grenzen: 10000 Katalogressourcen, 32 MiB gespeicherte Ressourcenpayloads
plus Zielmanifest/-katalog vor dem JSON-Parsen, 1000000 betrachtete Quellzellen,
128 MiB Zielbibliotheken insgesamt; Einzelbibliothek maximal 32 MiB,
Einzelobjekt weiterhin 1000 Zeilen/100000 Zellen. Bei Ueberschreitung kein still
gekuerzter Erfolgsbericht. Maximal 20 Konflikte pro Listenzeile sichtbar, mit
Gesamtzahl/Kuerzungsflag; im Einzel-Editor bleibt die ganze Gruppe erhalten.
Die Uebersicht prueft weder Geometrie, native Verweise noch Dateipaketqualifikation
und behauptet keinen realen NX-/Maschinennachweis.

Admin-API: `POST /api/v1/releases/{id}/catalog-schema-impact` mit
`library_id`, `kind`, `cam_status`, `state`, `query`, `offset`, `limit` und optional
`context_sha256` fuer Folgeseiten. `GET /api/v1/resources/{id}` liefert gezielt
die heutige gespeicherte Revision, nicht einen historischen Schreibbeleg.
Beide Endpunkte sind rein lesend und nur fuer den Admin zugaenglich; ERP-Rolle
hat hier keine neue Berechtigung. Keine Business-Central-Verbindung.

### Schemaumstellung im Editor

Im Reiter „Stammdaten anlegen & aufbereiten“ den vorhandenen Datensatz oeffnen.
Unter **„Bestehende CAM-Werte auf ein anderes NX-Schema umstellen“** einen
vollstaendigen verwalteten Zieldatenstand waehlen und die Schema-Vorschau pruefen.
Dies arbeitet mit den aktuellen, auch noch ungespeicherten CAM-Werten des Editors.
Die Bibliothek kommt aus dem Datensatz; kein Wechsel von Werkzeug zu Maschine
oder von metrischer zu englischer Bibliothek.

- Gleichnamige Spalten behalten ihren Wert unabhaengig von der Zielreihenfolge.
  Neue Spalten brauchen je Objektzeile einen bestaetigten Wert (auch leer).
  Neue Werte mit fuehrenden/nachgestellten Leerzeichen, Tabs oder geschuetzten
  Leerzeichen werden abgewiesen, nicht still gekuerzt. Leerwerte und Leerzeichen
  innerhalb eines Werts bleiben erlaubt; vorhandene Quellwerte bleiben erhalten.
- Umbenannte/entfallene Quellspalten brauchen eine explizite Zuordnung oder
  „Ausdruecklich weglassen“, auch wenn ihr Wert leer ist. Zwei Quellspalten
  duerfen nicht stillschweigend in eine Zielspalte zusammenfallen.
- FORMAT-Bloecke werden nach Klasse/Spalten und ggf. RTYPE erkannt. Bei
  Mehrdeutigkeit einen Zielblock waehlen. Klassenwechsel muessen extra bestaetigt
  werden. Das ist eine manuelle Entscheidung, kein automatisch qualifizierter
  NX-/Versionsadapter. Kennungsspalte LIBRF bzw. NAME bleibt unveraendert.
- Nach Entscheidungen Vorschau erneut pruefen. „Geprueftes Schema in Editor
  uebernehmen“ aendert nur CAM-Werte im Editor, setzt CAM-Status auf Entwurf und
  Geometriepruefung zurueck. Name, ERP-Verknuepfung und Anhaenge bleiben erhalten.
- Erst „Stammdaten speichern“ erzeugt eine neue Revision. Alte Revisionen und
  NX-Dateien bleiben unveraendert. Vor Export technische CAM-/Geometriepruefung
  erneut dokumentieren und bewusst freigeben; dann den normalen Export verwenden.

Mehrzeilige Objekte bleiben gruppiert. Der alte Einzeilen-Werkzeugeditor bleibt
in seiner Darstellung (`class_name`/`values`), insbesondere bei bereits
veroeffentlichten Objekten. Zugeordnete Modelle werden hier nicht konvertiert.
Die GUI zeigt zehn Zeilen pro Seite. Bestaetigte Entscheidungen und noch
unbestaetigte Eingaben bleiben beim Seitenwechsel erhalten. Bei einer neuen
Spalte kann man einen Wert ausdruecklich fuer alle noch unbestaetigten Zeilen
desselben Ziel-FORMAT-Blocks bestaetigen, auch auf anderen Seiten. Bestaetigte
Einzelwerte werden nicht ersetzt; unbestaetigte Eingaben im bestaetigten Umfang
werden ersetzt. Andere Zielbloecke und Kennungsspalten sind ausgenommen.
Nach geaenderter Block-/Spaltenzuordnung erst neu pruefen, bevor die Sammelaktion
erneut angeboten wird. Diese Funktion behandelt eine bestehende Objektgruppe,
nicht ungefragt mehrere Stammdatensaetze des Katalogs.

Geaenderte Entscheidungen verwerfen alte Vorschau-/Anwendungsantworten, auch
wenn ein Wert anschliessend wieder gleich lautet. Eine alte Antwort darf auch
keinen neueren Pruefvorgang entsperren.

Die zurueckgelieferten CAM-Werte muessen zudem exakt zur geprueften Vorschau
passen, einschliesslich Zeilenreihenfolge, Feldtypen und beliebiger Spaltennamen;
eine geaenderte oder anders strukturierte Antwort wird nicht in den Editor
uebernommen.
Bereits die Vorschau wird im Browser gegen die aktuellen Quellzeilen und
ausdruecklichen Entscheidungen rekonstruiert: erhaltene/umbenannte/entfallene
Werte, neue bestaetigte Spalten, Zaehler und resultierendes CAM muessen
zusammenpassen. Unvollstaendige oder widerspruechliche Antworten sperren die
Uebernahme. Eingaben bleiben zur Korrektur erhalten, eine zugehoerige alte
Detailvorschau gilt nicht mehr als geprueft. Sammelentscheidungen erst nach
erneut gueltiger Layoutvorschau. Dies ist eine Konsistenzpruefung, keine
Server-Signatur oder fachliche Qualifizierung des Zielschemas.

Laufende/ungeklaerte Speicherversuche
oder Uploads sperren die Schemauebernahme. Wird nach einem bestaetigten Save
nur die Ressourcenrevision aktualisiert und der unveraenderte CAM-Editor
behalten, bleiben Entscheidungen erhalten, aber die Vorschau muss neu geprueft
werden. Ein neuer Datensatz oder geaenderte CAM-Quelldaten verwerfen dagegen
die alten Zuordnungen. Keine Entscheidungen oder CAM-Daten werden dafuer im
Browserstorage abgelegt; beim Reload die Umstellung neu beginnen.

Die ausgewaehlte Bibliotheksdatei ist an ihr Release-Manifest gebunden; diese
Pruefung behauptet keine Integritaetspruefung aller sonstigen Share-Dateien.
Vorschau/Anwendung sind schreibfreie, Admin-exklusive APIs. SHA bindet Quell-CAM,
Zielbibliothek, Entscheidungen und ggf. Ressourcenrevision. Aenderungen machen
die Anwendung ungueltig. Grenzen: 1.000 Zeilen/Bloecke, je 100.000 Quell-, Ziel-
und projizierte Feldzellen sowie 32 MiB Bibliotheksdatei.

`POST /api/v1/schema/preview` erwartet `target_release_id`, `kind`, `cam`, optional
gemeinsam `resource_id`/`expected_revision` und `decisions`. Entscheidungen je
nullbasiertem Zeilenindex enthalten optional `target_block_id`,
`field_map` (Quelle -> Ziel), `discard_fields`, `values` fuer neue Zielspalten
und `accept_class_change`. `POST /api/v1/schema/apply` erwartet denselben Inhalt
plus `preview_hash` und liefert nur vorbereitete Editorwerte, keine neue Revision.

Siemens-Standardobjekte und bestehende fremde Kennungen behalten ihre bisherigen
Ueberschreibsperren. Die Umstellung umgeht diese nicht; sie ersetzt auch nicht
die Dreiwege-Migration eines ganzen Ordners. Echte fachliche NX-Abnahme, komplexe
Geometrie-/Maschinenpakete und automatisch qualifizierte Umbenennungsadapter
bleiben separate Arbeiten.

### Vollstaendiger Versionswechsel im Upgrade-Wizard

`server/settings.example.json` stellt die bereits extrahierten `sidts` fuer
2406/2512 bereit. Der Wizard prueft diese zusammen mit `standards`, dem alten
Quellordner und einem noch nicht vorhandenen Zielordner. Die Pruefung betrifft
Server-Eingaben, nicht die NX-/Lizenzinstallation des Browser-PCs. Als Quelle
kann auch ein intakter verwalteter Datenstand der passenden alten Version dienen.

Vorschauen sind auch an den aktuellen Bearbeitungskontext im Browser gebunden:
Eingaben oder Modus aendern und anschliessend auf dieselben Werte zuruecksetzen
reaktiviert keinen alten Bericht. Eine erneut gestartete Vorschau ersetzt die
fruehere Baubereitschaft erst nach ihrem eigenen Ergebnis; ein Build verbraucht
diese GUI-Freigabe. Der Server prueft Eingaben/Vorschauhash weiterhin selbst.
Versionswechsel verwerfen auch ausstehende Voraussetzungenberichte.
Angenommene Auftraege laufen trotz weiterer Eingaben/Navigieren weiter. Ihr
Ergebnis bleibt im Auftragsbestand erhalten, aber ein inzwischen geoeffneter
Editor oder anderer Arbeitsbereich wird nicht automatisch ersetzt/umgeschaltet.

Seit 15.10.2026 qualifiziert die GUI vor Baubereitschaft den vollstaendigen
Upgrade-Planvertrag1 (`scope=nx_folder_upgrade_plan`): exakte Auftragseingaben,
Voraussetzungen, Ergebnismanifest/Dateianzahl/Bytes, Bibliotheks- und
Verweisergebnisse, Konflikte, ausdrueckliche neue Spaltenwerte sowie alle
Sicherheitsflags. Der rekonstruierbare `preview_hash` bindet den gesamten
Bericht ohne sein eigenes Hashfeld; `result_manifest_sha256` bindet die
Dateiliste, `request_sha256` den Antrag, `input_sha256`/`input_count` die
intern erfassten Eingaben. Der gespeicherte Bericht ist zusaetzlich durch
den SHA256 im Auftragsbeleg gebunden. Die Bibliothekshashes verwenden weiterhin
sortiertes Unicode-JSON mit normalen Trennzeichen; der gespeicherte Bericht
verwendet kompaktes JSON. Der Browser bildet beide bestehenden Formate exakt
nach, auch bei numerisch aussehenden oder Unicode-Objektschluesseln.

Die bestaetigte Vorschau zeigt unter **Was aendert die Upgrade-Vorschau?**
jetzt Quelle/Ziel, geplante Dateianzahl/Bytes, Konflikte und noch unbestaetigte
Spaltenentscheidungen sowie eine lesbare Aktions-/Hinweisliste. Nach Art oder
Dateipfad/Bibliothek/Hinweis filtern und in Seiten zu je 50 Zeilen pruefen:
Kundenanpassungen, Zielstandards, ausgelassene Altdateien, Bibliotheksabgleich,
Kompatibilitaetshinweise und nicht aus der Kundenquelle uebernommene Pfade.
Bibliothekszaehler beziehen sich auf Objektgruppen; eine Datei kann mehrere
Hinweise haben. Neue reine Siemens-Zieldateien stehen weiterhin im vollstaendigen
technischen Bericht, nicht zwingend als eigene Kundenaktion in dieser Liste.
Ausdruecklich ausgelassene zusaetzliche Alt-Datenbanken werden nicht als
erfolgreich zusammengefuehrt dargestellt; Sicherungen/unklare Zuordnungen
erhalten ebenfalls keine erfundene Zusammenfuehrungsbestaetigung.
Suche und Blaettern sind rein lokal, starten keinen Auftrag und aendern keine
Mergeentscheidung. Bei Eingabeaenderung, neuer Vorschau, ungueltigem Bericht,
Buildstart oder Abmeldung wird die bestaetigte Liste entfernt. Die Anzeige
belegt einen gebundenen Plan, keine bereits ausgefuehrte Kopie oder CAM-Abnahme.

Fehlende/veraltete/widerspruechliche Plaene werden nicht als Konfliktentscheidungen
angezeigt und schalten keinen Build frei. Vorhandene historische Berichte bleiben
lesbar, benoetigen fuer neue Builds jedoch eine frische Vorschau. Berichtlesen ist
auf 64 MiB begrenzt, prueft einen regulaeren geoeffneten Dateihandle und den
gespeicherten SHA und lehnt doppelte JSON-Felder/nichtendliche Werte ab. Das ist
keine digitale Signatur und kein Schutz gegen einen kompromittierten Server oder
beliebige Dateischreiber; Berechtigungen und Betriebsabnahme bleiben erforderlich.
Auftragsstatus muss zur beobachteten Kennung/Aktion/Eingabe passen. Unklare
Belege erzeugen keinen Ersatzauftrag. Eine Erfolgsmeldung nach Build verlangt
zusaetzlich den zu genau diesem Auftrag/Previewhash gehoerenden Datenstand.
Eingabeaenderungen entwerten die bestaetigte Ansicht sofort; Spaltenentscheidungen
bleiben im gleichen Quell-/Ziel-/Versionskontext zum Weiterbearbeiten sichtbar.
Ein anderer Kontext entfernt die alten Entscheidungselemente.

Spaltenwerte, Bibliothekskonflikte, zusaetzliche Alt-Datenbanken und
Verweiszuordnungen gehoeren jeweils zu ihrer aktuellen sichtbaren Vorschau.
Ein noch aus einer alten Ansicht ausgeloestes Ereignis darf nach Quell-/
Zielwechsel, neuer Vorschau, Moduswechsel oder Abmeldung keine aktuellen
JSON-Entscheidungen aendern. Auch Hin-und-zurueck-Wechsel eines Pfads oder
des Arbeitsablaufs macht die alte Ansicht nicht wieder gueltig. Beim Import
eines vorbereiteten Ordners entwertet ausserdem das Abschalten der
Bibliothekszusammenfuehrung die dazugehoerigen Konfliktfelder.
Im anderen Reiter sind diese Aktionen nicht wirksam. Im selben gueltigen
Kontext bleiben mehrere ausdrueckliche Entscheidungen nacheinander moeglich;
gespeicherte Formulareingaben werden dadurch nicht automatisch geloescht.
Vor Erstellen eines Ordners bleibt eine neue gepruefte Vorschau erforderlich.
Dies betrifft den Versionswechsel und den vorbereiteten Ordnerimport, nicht
eine pauschale Zusicherung fuer alle dynamischen Controls der Anwendung.
Die54isolierten Faelle in `tests/test_upgrade_decision_context_browser.py`
pruefen diese Kontextwechsel und aktuelle positive Imports. Zusammen mit
den Bibliotheks-/FORMAT-Gesamtablaeufen bleiben Spaltenbestaetigungen,
Verweis-Bulkentscheidungen und Undo eigenstaendige ausdrueckliche Schritte.

Der freigegebene Adapter erkennt die 13 aktiven XCOPY-Zuordnungen in beiden
SIDT-after_install-Dateien und bildet genau diesen Netzwerkumfang ab. Kein BAT,
Siemens-Setup, DLL oder NX-Prozess wird ausgefuehrt. Unbekannte Kopierlayouts werden nicht
geraten. Die Quellarbeitskopie darf ein kompletter Installationsbaum sein;
unveraenderte Programmdateien ausserhalb des Netzwerkumfangs werden ausgelassen.
Lokales `standard_db.xml` bleibt am Siemens-Original. Optionale DMG-Palette und
zusammengefuehrte Henning-Dierk-Datenbanken entfallen nur im neuen Ziel.

„Vollstaendige Upgrade-Vorschau starten“ erzeugt einen nachverfolgbaren Auftrag
und einen SHA-gebundenen Bericht, noch keinen NX-Ordner. Der Vergleich nutzt alte
Standards, Kundenabweichungen und neue Standards. Unveraenderte alte Standarddaten
setzen keine neuen Siemens-Werte zurueck, auch nicht bei gleichen FORMAT-Spalten.
Neue Spaltenwerte und Dateikonflikte muessen explizit entschieden werden.
Referenzen und Windows-Pfadlaengen werden ebenfalls vor dem Build geprueft.
Gemeinsame Verzeichnispraefixe mit widerspruechlicher Gross-/Kleinschreibung
oder Datei-/Ordnerkollisionen blockieren schon die Vorschau. Die ausdrueckliche
Dateientscheidung `standard` gilt auch fuer ansonsten konfliktfrei uebernehmbare
Kundenanpassungen; ohne neue Standarddatei bedeutet dies bewusstes Weglassen
nur im neuen Ziel. Die Quelldatei bleibt erhalten.
Fehlende Kundenverweise werden im Wizard mit ausdruecklich bestaetigten
Zuordnungen behandelt (siehe „Verweiszuordnungen“ unten); keine automatischen
Nummern-Aliase oder Aktivierung auskommentierter Standardbeispiele.

„Geprueftes Upgrade als neuen Ordner erstellen“ akzeptiert nur die passende,
baubare Vorschau. Eingaben werden vor/nach dem Kopieren gegen SHA geprueft;
keine Quelle oder vorhandener Zielordner wird ueberschrieben. Erfolg registriert
einen neuen Entwurf inklusive NX2512-Testpaket, keine automatische Aktivierung.
Bei verwalteten Quellen werden nachweisbare Besitzer-Hashes auf die neuen
Bibliotheksschemata projiziert. Bestaetigte Defaults bleiben fuer taegliche
Folgeexporte erhalten. Bewusst durch den Zielstandard ersetzte Kundengruppen
werden nicht automatisch wieder zurueckgeschrieben.
Ausdruecklich korrigierte Verweise werden in den Besitzer-Hashes und im
Upgrade-Nachweis beruecksichtigt. Der aktuelle Stammdaten-/ERP-Katalog wird
dabei nicht veraendert. Bei taeglichen Exporten zeigt die GUI die Korrekturen
mit einem Link zum Stammdateneditor; alte ungueltige Katalogverweise blockieren
den Rueckexport statt die Korrektur zu ueberschreiben.

Auftraege laufen nach Schliessen des Browsers weiter. Status kann erneut abgefragt
werden. Ein Serverabbruch hinterlaesst Teilstaende; ein nachweislich beendeter/
wiederverwendeter Besitzerprozess wird als unterbrochen erkannt. Live oder nicht
pruefbare Prozesse werden nicht aufgrund einer Abfragezeitueberschreitung neu
gestartet. Fuer einen neuen Versuch einen neuen Zielordner und neue Vorschau
verwenden, keine pauschale Loeschung der alten Nachweise.

Administrator-API: `GET /api/v1/migrations/options`, `POST /migrations/preview`,
`POST /migrations/build` (mit `preview_id`/`preview_hash`),
`GET /migrations`, `GET /migrations/{id}` und `/migrations/{id}/report`.
Alle Kurzpfade liegen unter `/api/v1`. ERP darf diese Auftraege nicht steuern.
Der bisherige Import vorbereiteter Ordner bleibt als separater Modus verfuegbar.
Weitere Versionsadapter und kompletter Paket-/Betriebsnachweis bleiben offen;
dieser Wizard ersetzt keine fachliche NX-Abnahme.

### Update-/Extraktionsauftrag nach verlorener Antwort wiederfinden

Auch `POST /api/v1/migrations/preview`, `/migrations/build` und
`/migrations/extract` unterstuetzen jetzt optional den kanonischen UUID-Header
`Idempotency-Key`. Annahmebeleg, neuer Auftrag und Audit werden in EINER
SQLite-Schreibtransaktion gespeichert. Der Hash bindet Modus, normalisierte
Eingaben und bei Build auch Vorschau-ID/-Hash. Gleiche Kennung mit anderen
Eingaben/anderer Vorschau ist HTTP 409, keine zweite Auftragsannahme.
Andere neue Auftraege bleiben waehrend eines laufenden Writers gesperrt.

Identischer Retry liefert die URSPRUENGLICHE angenommene Auftragsantwort
(`status=queued`, HTTP 202), auch wenn dieser Auftrag inzwischen fertig,
fehlgeschlagen oder unterbrochen ist. Das ist kein aktueller Status und kein
Build-Erfolg: anschliessend `GET /api/v1/migrations/{id}` abfragen. Es wird
KEIN Worker erneut eingereiht, kein vorhandener Zielordner angefasst und kein
Installer/Quellordner fuer einen bereits angenommenen Retry erneut gelesen.
Fehlschlag beim Einreihen eines neuen Workers wird im bestehenden Auftrag als
`failed` festgehalten; der Annahmebeleg bleibt erhalten, kein automatischer Retry.

Admin-`GET /api/v1/migration-requests/{key}` liefert Vertrag 1 mit `accepted`
oder `cancelled`, Aktion/Request-Hash, Job-ID und Originalantwort. Kein Beleg
(404) beweist NICHT, dass ein laufender Request nicht noch angenommen wird.
Admin-`POST /api/v1/migration-requests/{key}/cancel` serialisiert mit der
Annahme: noch keine Annahme -> Kennung dauerhaft sperren; schon angenommen ->
Originalbeleg zurueck, Auftrag weiter beobachten. Kein Worker-Abbruch, Undo,
Loeschen eines Teilordners, Neustart oder NX-/Produktivaktivierung. ERP ist von
beiden Routen ausgeschlossen. Kennungen sind serverseitig rollenbezogen,
keine individuellen Benutzer-/Mandantenkonten. Backup-Schreibpausen bleiben.

Im Wizard zeigt eine ungeklärte Antwort „Auftragsannahme pruefen“, „Exakt
denselben Startversuch wiederholen“ und „Versuch vor Annahme sperren“.
Bis zur Klaerung keine neue Upgrade-/Extraktionsannahme oder SIDT-Auswahl.
Kein automatischer Retry nach Verbindungsfehler. Nach sicherer 400/422-
Validierungsablehnung wird die alte Kennung vor korrigiertem Start gesperrt.
Konflikt-, Berechtigungs-, Server- und Transportfehler bleiben ungeklärt.

Dasselbe Kontextprinzip gilt fuer taegliche Exportvorschauen: geaenderte
Spaltenwerte oder ein inzwischen aktualisierter Katalog sperren auch noch
ausstehende alte Antworten. Ein abgeschlossener Export schliesst keine
zwischenzeitlich neu bearbeitete Exportauswahl, selbst bei gleicher spaeterer
Bezeichnung/JSON-Werten. Bereits angenommene Auftraege werden dadurch weder
geloescht noch erneut ausgefuehrt. Die Auswahlwerte bleiben zum bewussten
Erstellen einer neuen Vorschau erhalten; keine automatische CAM-/NX-Freigabe.

Nur Vertrag/Kennung/Methode/API-Pfad/Zeitpunkt bleiben im sessionStorage
dieses Tabs; keine Schluessel, Quell-/Zielordner, Konflikt-/Spaltenentscheidungen
oder CAM-Daten. Originaleingaben fuer bewussten identischen Retry nur im
Arbeitsspeicher. Nach Seitenneuladen nur Belegpruefung/Sperre, nicht Retry mit
geratenen Eingaben. Spaete Antworten alter Sitzungen bleiben wirkungslos.
Ein bereits erstellter Build wird nach Belegpruefung mit seiner originalen
Job-/Release-ID angezeigt, nicht in einen weiteren Ordner kopiert.

Eine Vorschauantwort fuer inzwischen veraenderte oder unvollstaendige JSON-
Eingaben setzt keine alten Entscheidungen ein und schaltet keinen Build frei.
Nach Reload/Reconnect fuer neue Eingaben die Auftragsberichte ansehen und eine
passende neue Vorschau erstellen. Die Annahme/Wiederaufnahme ersetzt keine
SHA-/Vorschaupruefung vor dem Build und keine fachliche NX-Abnahme danach.
Bei tatsaechlichem Server-/Workerabbruch bleibt bisherige Besitzer-/Child-
Lebenszeitpruefung massgeblich; alter Annahmebeleg loest keinen Neustart aus.

`migration_requests` wird gegen die unveraenderten Ursprungsfelder des Jobs
geprueft, audit-row-gebunden und mit der Vollsicherung wiederhergestellt. Alte
Archive ohne diese Tabelle behalten den originalen Snapshot-Hash; eine neue
leere Tabelle entsteht erst bei isoliertem Restore. Keine alte Auditgeschichte
umgeschrieben. Derselbe Annahmevertrag umfasst jetzt auch Hintergrundimporte
vorbereiteter Ordner und taegliche Exporte (siehe oben), nicht deren alten
synchronen Endpoints. SIDT-Auswahl und andere Jobs behalten ihre eigenen
Vorschau-/Generations-/Revisionspruefungen.

### Statische EXE-Extraktion im Upgrade-Wizard

Die Windows-Verwaltung kann die beiden vorhandenen Pakete NX2406 V1.11
(Inno-Format 6.3.0) und NX2512 V1.14 (6.7.0) als Archive lesen. Es wird nur der
separat freigegebene Entpacker gestartet, **niemals die Setup-EXE, entpackte BAT,
DLL oder install_script.iss**. Andere Formate/Versionslayouts werden abgewiesen.
Die [Dokumentation des innounp-Autors](https://raw.githubusercontent.com/jrathlev/InnoUnpacker-Windows-GUI/refs/heads/master/innounp-2/docs/innounp.htm)
beschreibt Archivliste, Integritaetspruefung und Entpackoptionen.

Einmalige Einrichtung auf einem Windows-Worker:

1. `Install-NX-Extractor.ps1` laedt das portable Paket 2.71.1 nach
   `C:\NX_Migration\ServerTools\innounp-2.71.1`. Keine Systeminstallation und kein
   Siemens-Setup. ZIP-SHA ist fest gebunden; vorhandene Ziele werden abgewiesen.
   Lizenz/Quellhinweise bleiben im Tool-Verzeichnis, Binaerdateien nicht in Git.
2. In den Server-Settings `extractor` mit `kind`, lokalem `path` und Tool-`sha256`
   sowie `installers` mit NX-Version, lokalem EXE-`path` und `sha256` konfigurieren.
   `settings.example.json` ist fuer die hier vorliegenden Dateien vorbereitet.
   Nach Datei-/Konfigurationsaenderungen neu starten; keine automatischen Updates.
3. Im Reiter „Versionsupdate“ das freigegebene Paket waehlen und „EXE statisch
   entpacken“ anklicken. Pfade/Pruefsummen koennen nicht per Auftrag ausgetauscht
   werden. Grenzen: EXE 64 MiB, Liste 20.000 Eintraege, Daten 2 GiB,
   Entpacker-Ausgabe 4 MiB, je Entpacker-Aufruf 300 Sekunden.
4. Fortschritt im Auftrag beobachten. Das Original wird nur gelesen; eine
   gepruefte private Kopie, Liste, extrahierte Dateien und Manifest/Bericht bleiben
   unter `data_dir/upgrades/<id>`. Bereits vorhandenes SIDT-Staging bleibt die
   Eingabe, bis ausdruecklich „Als SIDT-Eingabe waehlen“ geklickt wird.
5. Beide Versions-Eingaben und Voraussetzungen pruefen, danach eine neue
   Upgrade-Vorschau erstellen. Auswahl ist generationengebunden und waehrend
   eines aktiven Upgrade-/Extraktionswriters gesperrt. Keine Laufwerksumschaltung.

Ausgewaehlte Dateien werden gegen ihr vollstaendiges Manifest geprueft; nach
externer Aenderung gibt es einen Fehler statt stiller Rueckkehr zum alten Staging.
Fehlversuche behalten Teilnachweise. Ein noch lebender/nicht pruefbarer
Entpacker verhindert vorzeitige Freigabe des Writers nach Besitzerverlust.
Der Kindprozess erbt keine API-/Provider-Schluessel. Zugriff auf Tool, Settings
und privaten Datenordner muss auf Dienst/Admin beschraenkt werden; SHA ist
Integritaetsbindung, keine Ersatz-Authentifizierung gegen einen lokalen Admin.

Die bisherige lokale Extraktion hat `applications/nx/nxplot/temp_models.user`
ausgelassen. Beide Originalarchive enthalten diese leere Datei; der neue Reader
bewahrt sie. Alle zuvor extrahierten SIDT-Anwendungsdateien bleiben bytegleich.

Neue Administrator-API: `POST /api/v1/migrations/extract` mit `version` und
`POST /api/v1/migrations/{id}/select-input` mit `expected_generation`.
Auch Extraktionsjobs nutzen vorhandene Status-/Berichtsendpunkte.
Linux-Docker hat keinen nativen Windows-Reader und fuehrt weder Wine noch Setup
aus. Dort weiter freigegebenes SIDT-Staging read-only einbinden; ein verteilter
Windows-Extraktionsworker ist noch nicht implementiert. Die installierten
NX-Standarddaten beider Versionen werden weiterhin zusaetzlich gebraucht.

Isolierter Realtest (Windows, keine echte Verwaltungsdatenbank/NX-Ausfuehrung):

```powershell
$env:NX_EXTRACTOR_TEST = '1'
$env:NX_BROWSER_TEST = '1'
Push-Location .\server
try { .\.venv\Scripts\python.exe -m pytest tests/test_extraction.py tests/test_extraction_browser.py -q }
finally { Pop-Location }
```

Die Variablen schalten ausschliesslich optionale Tests frei. Der Browser-Test
verwendet temporaere Einstellungen, eigenen Port/Schluessel/SQLite und Edge
headless; er ersetzt keinen produktiven NX- oder Container-Nachweis.

### Dateianhaenge im Editor

Seit 15.10.2026 verwenden normale Test-Starter, Rollen-Testpakete, Dateianhaenge
und STEP-Importpakete denselben begrenzten Browserempfang. Der gemeinsame Bereich
„Dateidownload“ zeigt Fortschritt, Fehler und einen ausdruecklichen Abbruch.
Ein zweiter solcher Download wird bis Abschluss/Abbruch nicht gestartet.
Groesse und SHA-256 werden beim Empfang geprueft; kein unbeschraenktes
`response.blob()` oder kompletter ArrayBuffer vor der Pruefung. Die Obergrenze
betraegt 64 MiB. Dies ist ein Browserlimit, keine neue serverseitige Datei-
oder Archivgroessenregel. Grosse Sicherungs- und Collectorarchive behalten
ihren separaten Streaming-/Dateispeicherablauf.

Gespeicherte Groessen bleiben verbindlich. Bei Starterbelegen ohne gespeichertes
Groessenfeld wird ein strikt geprueftes HTTP-Content-Length nur als begrenzter
Transporthinweis verwendet; die gespeicherte SHA bleibt zwingend. Ohne sicheren
Groessenbeleg wird nichts empfangen/veroeffentlicht. Beide Starterwege lesen
gezielt vor und nach dem Empfang den aktuellen Release-/Paketstand und das
vollstaendige Quellmanifest. Die kompakte Bestandsuebersicht muss dafuer keine
Manifeste mitladen. Geaenderte Paketrevisions-/SHA-/Quellbindungen oder ein neues
Manifest sperren die Uebergabe; keine automatische Neuerstellung/Aktualisierung.

Abmeldung, Abbruch und relevante Editor-/Dateizuordnungs-/Rollen-/STEP-Auswahl-
wechsel verwerfen den betroffenen Empfang. Ein spaeter Abschluss eines alten
Downloads kann weder eine Datei veroeffentlichen noch den Status eines neuen
Downloads ersetzen. Serverdateien werden dabei nicht geloescht oder geaendert.
„Pruefsumme und Groesse bestaetigt“ bedeutet nur gepruefte Uebergabe an den
Browser, nicht bestaetigte Speicherung auf dem Datentraeger oder NX-Freigabe.
Ein Browserdialog kann danach weiterhin abgebrochen werden.

„Stammdaten anlegen & aufbereiten“ zeigt die zugeordneten Dateien mit Dateiname,
Groesse und Integritaetsstatus. Download prueft die gespeicherten Bytes auf dem
Server und nochmals im Browser gegen SHA-256. Die Suche nach bereits hochgeladenen
Dateien erlaubt Wiederverwendung ohne erneuten Upload. Die Bestandsliste liest nur
Metadaten; die Integritaet wird beim Zuordnen/Herunterladen geprueft. Ihre Zaehler
beziehen sich auf aktuelle Stammdaten, nicht auf alte Revisionen oder Releases.

Anhangsanzeige, Zuordnung, Paketdateiauswahl und Hashbindung verwenden dieselbe
Metadatenpruefung: angefragte Datei-ID, sicherer Original-Basename, SHA-256 und
Groesse muessen passen. Beim Zuordnen muss die verifizierte Datei ausserdem
exakt der ausgewaehlten Bestandszeile entsprechen. Bereits verifizierte
unveraenderliche Metadaten werden begrenzt im Arbeitsspeicher dieser Sitzung
festgehalten; keine Modelle oder CAM-Daten im Browserstorage. Ungueltige
Bestandsseiten leeren alte zuordenbare Zeilen statt sie als aktuell stehenzulassen.

Eine spaete Dateizuordnung oder Uploadantwort darf zwischenzeitlich geaenderte
Anhangseingaben nicht ueberholen, auch nach Aenderung und Rueckkehr zum alten Wert.
Ein bereits gespeicherter Upload bleibt dabei erhalten und kann spaeter bewusst
aus dem Dateibestand zugeordnet werden. Es wird kein zweiter Upload erzeugt.

Zuordnen und „Zuordnung entfernen“ sind zunaechst ungespeicherte Editoraenderungen.
Erst „Stammdaten speichern“ erzeugt eine neue Revision. Es gibt keine Dateiloeschung:
Originale, alte Revisionen und historische Exporte bleiben erhalten. CAM-
Dateireferenzen werden nicht automatisch geraten oder umgeschrieben. Aenderungen
an Anhaengen/CAM-Parametern setzen die Geometriebestaetigungen zurueck; fuer
Werkzeugfreigaben muss der Beleg erneut geprueft und gebunden werden.
Auch ein ausdrueckliches erneutes Binden der Pruefsummen verwirft vorherige
Hashbindungen und fachliche Haken. Erst nach erfolgreicher Pruefung aller
Dateien die jeweiligen Geometrie-/Paketchecks erneut bestaetigen. Ein Fehler
laesst keine alte oder teilweise neue Bindung als aktuellen Beleg stehen.
Fehlende/manipulierte Anhaenge bleiben unter der angefragten ID sichtbar und
koennen nur abgeloest werden; keine andere Datei wird ersatzweise angeboten.

Administrator-Inventar: `GET /api/v1/assets?query=...&offset=0&limit=100`.
Maximal 20 MB pro Datei, maximal 100 eindeutige Anhang-IDs pro Stammdatensatz.
Auch Entwuerfe koennen keine fehlenden/manipulierten Dateien neu zuordnen.
ERP behaelt den bestehenden authentifizierten Binaerdownload, aber keinen
Zugriff auf das globale Dateiinventar oder die CAM-Aufbereitung.

### PRT-Zielpfade grafisch vorbereiten

Unter „Stammdaten anlegen & aufbereiten“ einen vollstaendigen nativen
CAM-Datensatz (oder ein altes metrisches Einzelwerkzeug) oeffnen und die PRTs
als Anhaenge hochladen/zuordnen. Im Bereich **„PRT-Dateien im NX-Graphics-Ordner
zuordnen“** die Modelldateien laden. Die Bibliothek bestimmt den Standardordner;
Werkzeug-, Spannmittel- und CMM-Pfade werden nicht gegeneinander ausgetauscht.
Bibliotheken ohne Graphics-Adapter bleiben gesperrt. Maschinen-/Vorlagenpakete
verwenden ihren eigenen Dateipaketeditor.

Gewuenschte PRT-Dateien ankreuzen und optionale Unterordner eintragen. Dateinamen
bleiben unveraendert. **„Pfadzuordnung pruefen“** verifiziert alle Anhaenge und
verwendet dieselben Standardpfadregeln wie der Export. Danach bewusst nur in
den Editor uebernehmen; erst normales Speichern erzeugt eine Revision. Eine
vorherige Exportfreigabe wird zum Entwurf, Geometriechecks werden zurueckgesetzt.
Der anschliessende normale Export prueft Zielkollisionen und Besitz erneut.

Bei expliziter Auswahl werden nur die ausgewaehlten PRTs kopiert; andere PRTs,
STEP-Eingaenge und Dokumente bleiben private Anhaenge. Mindestens ein PRT muss
ausgewaehlt sein. Die alte leere Pfadliste `[]` bedeutet weiterhin **Standardmodus
fuer alle Anhaenge**, nicht „nichts exportieren“. Zurueckschalten erfordert eine
eigene Bestaetigung. Unvollstaendige Rohdaten werden nie still ersetzt.

Gebundene Tool-Ergebnispakete sind schreibgeschuetzt; die bestehende Aktion
„Paketbindung ausdruecklich loesen“ bleibt erforderlich. Diese Zuordnung prueft
keine internen PRT-Komponenten-/GRAPHICS-Verweise, konvertiert kein STEP-Modell
und ersetzt keine NX-/Montage-/Kollisionspruefung. Bei Unterordnern muessen die
bereits gespeicherten Komponentenreferenzen fachlich dazu passen.

Admin-exklusiv: `POST /api/v1/graphics-bindings/preview` mit `kind`, vollstaendigem
`cam`, `asset_ids`, `file_bindings` (`asset_id`/`path`) und optional gemeinsam
`resource_id`/`expected_revision`. Bis zu 100 Anhaenge; Revisionskontext,
Originalnamen und Bytes werden geprueft. Der SHA-gebundene Bericht hat den
begrenzten Umfang `graphics_paths_only`: keine Zielkollisionen, Paketprovenienz,
NX-Ausfuehrung, Datei-/Ressourcenschreibvorgaenge oder automatische Freigabe.
Es gibt keinen automatischen Apply-/Export-Aufruf aus diesem Dialog.

### Bereits umgesetzt

- GUI und API fuer 23 Stammdatentypen: Werkzeuge, Halter, Schaefte, Baugruppen,
  Konturen, Bezugspunkte, Mehrfachwerkzeuge, Spannmittel, Maschinen/Geraete,
  Werkstoffe, Schnitt-/Bearbeitungsdaten, Kraftparameter, CMM-Ressourcen,
  Vorlagen und UI-Standards;
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
vollstaendiges Kinematik-/Post-/Simulationspaket. Fuer Maschinen/Spannmittel gibt
es jetzt einen expliziten Dateipaketadapter an den oben beschriebenen Pfaden,
keine automatische Kinematik-/Post-/Simulationskonvertierung. Geraete brauchen
weiter einen eigenen Paketadapter. Feature-/Normtabellen, physikalische Material-
XMLs und Bearbeitungswissen bleiben Dateibestaende, kein beliebiger DAT-Editor.
Vorlagen/UI-Standards werden als gepruefte Dateipakete statt DAT-Zeilen verwaltet.

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

Unter **Stammdaten verwalten** gibt es **ERP-Zuordnung pruefen (nur lesend)**.
Die Uebersicht unterscheidet bereits zugeordnete technische Daten, reine ERP-
Entwuerfe, CAM-/sonstige Entwuerfe ohne ERP, Firmenstandards, Stilllegungen und
Zuordnungskonflikte. Status/Typ/Firma und Volltext filtern den gesamten
gespeicherten Stand; jeweils50 Treffer pro Seite. Folgeseiten sind an den
gespeicherten Kataloghash gebunden. Bei Aenderungen ausdruecklich neu laden.

Identitaet in dieser API ist das exakte Paar `company_id` + `system_id`.
Artikelnummern und Namen werden nicht zum automatischen Zusammenfuehren
verwendet. Gleiche Nummern bei verschiedenen Kennungen sind ein Hinweis,
keine neue Zuordnung. Widersprueche zwischen Index/Payload, mehrfach beanspruchte
Identitaeten oder unvollstaendige Revisionskoepfe sind Konflikte. Bei kaputten
ERP-Feldern bleiben gueltig lesbare Payload-/Indexkennungen sichtbar und nach
Firma suchbar; keine automatische Reparatur oder Feldnormalisierung.

Die exakte Abfrage zeigt den heute gespeicherten Metadatenstand. Sie ist kein
historischer Schreibbeleg: ein alter idempotenter Upsert kann weiter seine
Originalantwort liefern, waehrend die Abfrage die aktuelle Revision zeigt.
Der gesamte Ueberblick erfordert CAM-Adminrechte; eine exakte Einzelabfrage
ist auch fuer den ERP-API-Schluessel erlaubt. Unbekanntes Paar liefert
`found:false`, widerspruechlicher Stand409. Kein Aendern oder Auswaehlen eines
anderen Datensatzes ueber Nummernaehe. ERP-Upsert verweigert jetzt geaenderte
Identitaet gegen den bestehenden Index oder einen widerspruechlichen Kopf,
bevor eine neue Revision/Receipt/Audit entstehen. Historische Replayantworten
werden nicht nachtraeglich umgeschrieben oder als heutiger Stand ausgegeben.

Leser verwenden SQLite `mode=ro`, `query_only=ON` und eine Lesetransaktion;
keine Aenderung des Journalmodus oder von Dateien. Grenzen:50000 Ressourcen,
32MiB je Payload und512MiB Gesamtlesebudget einschliesslich aktueller
Versionskoepfe. Es erfolgt kein Business-Central-Livezugriff und keine
Audit-/Datei-/NXqualifikation. Technische Daten vorhanden bedeutet nicht fuer
NX geprueft. Eine weitere explizite Aktion kann passende gespeicherte
Stammdaten im Editor oeffnen; sie fragt vor Ersetzen ungespeicherter Eingaben.
Am 15.10.2026 bestaetigtes Ziel: Ein ERP-Artikel darf mehreren eigenstaendigen
NX/CAM-Varianten zugeordnet sein. Gemeinsame ERP-Stammdaten und getrennte
technische Varianten mit eigenen Revisionen/Freigaben sind vorzusehen.
Hierfuer gibt es jetzt ein separat aktivierbares Datenmodell mit API und
expliziter Bestandsmigration (siehe unten). Der lokale Bestand bleibt bis zur
bewussten Umstellung im bisherigen 1:1-Modell. `cardinality_policy: not_configured`
bezieht sich auf die bisherige lesende Uebersicht, nicht auf den neuen API-Vertrag.
Keine bestehenden Datensaetze werden automatisch zusammengefuehrt, verknuepft,
verschoben oder geloescht. Die Entscheidung autorisiert keine ERP-Liveanbindung.

### Gemeinsame ERP-Artikel und eigenstaendige CAM-Varianten (API und GUI, 15.10.2026)

Das optionale Modell2 trennt ERP-Artikel von technischen Ressourcen. Die exakte
Artikelidentitaet bleibt `company_id` + `system_id`; Artikelnummern sind weiterhin
keine Identitaet. Ein Artikel kann beliebig vielen vorhandenen CAM-Ressourcen
zugeordnet werden; eine Ressource hat hoechstens einen aktuellen Artikelbezug.
Ihre unveraenderte Ressourcen-UUID bezeichnet die Variante. Es werden keine
Geschaefts-Variantenkennungen erfunden oder vorhandene Datensaetze zusammengelegt.

Artikel haben eigene kommerzielle Revisionen. Zuordnungen haben ebenfalls eine
separate Historie, einschliesslich Wechsel und ausdruecklichem Loesen. Weder
eine Artikelaktualisierung noch eine Zuordnung schreibt CAM-Payloads, technische
Revisionen, Anhaenge, NX-Dateien oder Freigaben um. Technische Datensaetze werden
weiterhin ausschliesslich ueber die bestehenden Admin-Ressourcenfunktionen
angelegt/bearbeitet und separat exportiert. Firmenvorlagen/UI-Standards sind
keine ERP-Artikelvarianten. Der ERP-Schluessel darf Artikel schreiben, aber keine
technischen Varianten zuordnen, Geometrie anlegen oder CAM-Freigaben erteilen.

Ein normaler Serverstart legt die sechs neuen Tabellen NICHT an. Zuerst lesend
`GET /api/v1/erp/model/migration-preview` pruefen; der Admin muss die separate
Migration ausdruecklich mit dem exakten `preview_hash` anfordern. Sie prueft
Katalog/Revisionskoepfe/Audit-Metadaten, sperrt konkurrierende DB-Writes, erstellt
und verifiziert eine reine SQLite-Sicherung unter
`backups/erp-model/<Anfragekennung>/management.sqlite3` und uebernimmt pro bisher
gueltiger ERP-Zuordnung einen Artikel und einen Bezug zur gleichen Ressource.
Historische Ressourcen, Releases, Payloads und alte Schreibbelege bleiben
unveraendert. Schemaanlage, neue Daten, Beleg und Audit werden gemeinsam
committet; Fehler rollen auch die neue Schemaanlage zurueck. Die Sicherung bleibt
zur Diagnose erhalten. Kein ueberschreibender Restore und keine Datei-/Offsite-
Sicherung durch diesen Schritt; vor Betriebsumstellung ist eine regulaere
vollstaendige Sicherung weiterhin erforderlich.

Die Umstellung ist kein transparenter Ersatz des alten ERP-Upsert-Vertrags:
danach sind neue alte 1:1-Upserts und die alte Kennungsuebersicht gesperrt.
Exakte Wiederholungen bereits vorhandener alter Schreibbelege bleiben lesbar.
Neue Clients muessen bewusst die Artikel-/Varianten-API verwenden. Das fruehere
`erp` im Ressourcen-Payload bleibt ein historischer Snapshot, kein aktuell
projizierter ERP-Artikel. `GET .../erp/variants/{resource_id}` unterscheidet diesen
Snapshot ausdruecklich vom heute zugeordneten Artikel. Die GUI unter
**Stammdaten verwalten → ERP-Artikel und CAM-Varianten verwalten (1:n)**
unterstuetzt jetzt die ausdrueckliche Umstellung und den Wiederanlauf.
Es gibt keine Business-Central-Liveverbindung. Der lokale Bestand wird durch
das Softwareupdate, Oeffnen der Oberflaeche oder Lesen NICHT umgestellt.

Bedienung im neuen Bereich:

1. Modellstatus lesen. Im alten Modell die schreibfreie Umstellungsvorschau
   pruefen, Konflikte beseitigen, die Folgen fuer alte ERP-Clients und das
   Sicherungskonzept bewusst bestaetigen. Erst der separate Umstellungsbutton
   mit Bestaetigungsdialog startet die gesicherte Transaktion. Dies ist keine
   NX2406-/NX2512-Versionsmigration und keine Aktivierung eines NX-Ordners.
2. Nach der Umstellung Artikel suchen/laden; optional exakte Firma und System-ID
   gemeinsam angeben. Artikelnummern sind kein Zuordnungsschluessel. Je50 Treffer
   und Snapshot-gebundene Folgeseiten. Artikel entweder bewusst als Ziel waehlen
   oder dessen gemeinsame ERP-Felder ins separate lokale Formular laden.
   Neuanlage legt nur einen Verwaltungsartikel an, keinen Business-Central-
   Artikel oder technischen CAM-Datensatz. Identitaet beim Bearbeiten gesperrt;
   neue Identitaet braucht ausdrueckliche Neuanlage. Quellrevision nicht geraten.
3. Eine vorhandene technische CAM-Ressource ausdruecklich waehlen und deren
   heutigen Artikelbezug lesen. Erst danach dem gewaehlten gespeicherten Artikel
   zuordnen, auf einen anderen Artikel umstellen oder den Bezug bewusst loesen.
   Firmenvorlagen und UI-Standards sind nicht waehlbar. Technische Ressourcen
   weiterhin im getrennten Aufbereitungsreiter anlegen; kein automatischer
   3D-/STEP-/CAM-Aufbau durch einen neuen ERP-Artikel.
4. Zugeordnete Varianten und die separaten Artikel-/Zuordnungshistorien lesen.
   Historien haben20 Revisionen je Seite. CAM-Revision und Zuordnungsrevision
   werden getrennt gezeigt. Lesen veraendert weder den technischen Editor noch
   ein ungespeichertes ERP-Formular. Bewusstes Formular-Ersetzen fragt nach.

Die alte ERP-Pruefansicht wird erst nach bestaetigtem Modellstatus fuer das
neue Modell gesperrt. Hauptkatalog und technischer Rohdateneditor kennzeichnen
ihre bisherigen ERP-Felder als gespeicherte Snapshots; im1:n-Modell sind sie
historisch. Aktuelle gemeinsame ERP-Felder im neuen Artikelbereich lesen,
nicht aus einer alten Ressourcenrevision ableiten. Bestandsaktualisierung
beachtet auch den Verwaltungs-/Audit-Metadatenhash: ERP-Aenderungen koennen
Ansichten entwerten, obwohl keine technische CAM-Revision geaendert wurde.

Im Stammdatenbestand fuehrt „ERP-Bezug pruefen“ direkt zur lesenden Pruefung
der heutigen Zuordnung einer technischen CAM-Ressource. Bei aktivem1:n-Modell
zeigt die neue Artikelkarte alle sechs gemeinsamen ERP-Felder sowie Artikel-,
Zuordnungs- und CAM-Revision getrennt. Auch ohne Zuordnung wird nichts geraten.
Firmenvorlagen/UI-Standards erhalten diese Variantenaktion nicht. Im alten oder
noch ungeklaerten Modell oeffnet der Einstieg nur einen Hinweis auf Status/
bisherigen ERP-Pruefbereich; er stellt weder Modell noch Zuordnungen um.

Die Pruefung laesst CAM-Editor, ungespeichertes ERP-Formular und einen bereits
gewaehlten anderen Zielartikel unveraendert. „Historie dieses Artikels lesen“
zeigt die separate kaufmaennische Historie. Erst „Zugeordneten Artikel als Ziel
waehlen“ aendert bewusst die lokale Auswahl fuer Variantenuebersicht und eine
moegliche spaetere Zuordnung; es wird noch nichts gespeichert. Dafuer wird der
Artikel ueber seine exakte Firma/System-ID nochmals mit demselben Snapshotpin
gelesen, einschliesslich Variantenzahl. Danach koennen dessen weitere Varianten
geladen werden. Nummernaehnlichkeit wird nicht als Zuordnung verwendet.

Alte/ungueltige oder waehrend Auswahl-/Bestands-/Sitzungswechsel eingetroffene
Antworten ersetzen keine neue Karte oder Auswahl. Geaenderte Artikelbelege
erfordern eine neue Bezugspruefung. Offene/ungeklaerte ERP-Writes lassen
Lesen weiter zu, sperren aber neue Zieluebernahme; der Wiederanlauf bleibt
unveraendert. Die Karte ist ein gelesener Verwaltungsserverstand, keine live
ueberwachte Business-Central-Anzeige und keine technische CAM-Freigabe.

ERP-Feldgrenzen in der GUI zaehlen wie die API Unicode-Codepunkte statt
UTF-16-Einheiten; unvollstaendige Surrogatzeichen werden abgewiesen. Gueltige
ueber die API importierte Texte werden nicht allein wegen mehrteiliger
Unicode-Codierung zurueckgewiesen. Die HTML-Eingabegrenze laesst deren
Darstellung zu; die Speicherpruefung behaelt die eigentlichen Feldgrenzen bei.

Jeder GUI-Write wird mit demselben Originalbeleg gegengeprueft, bevor die
Oberflaeche ihn bestaetigt. Bei verlorener/ungueltiger Antwort blockiert der
Wiederanlaufdialog weitere ERP-Writes: Beleg lesen, ausdruecklich exakt dieselbe
Anfrage wiederholen oder offenen Commit sperren. Kein automatischer Retry oder
Abbruch aus fehlendem Beleg. Im sessionStorage bleiben nur Kennung, Aktion,
Pfad/UUID, Zeitpunkt, erwartete Revision und SHA-256, keine ERP-Feldwerte,
Modelle oder Zugangsschluessel. Nach Reload/Logout nur Belegpruefung/Sperre;
urspruengliche Felder fuer Retry liegen ausschliesslich in der Sitzung im RAM.
Spaetere Eingaben/andere Zielauswahl werden durch alte Antworten nicht ersetzt.
Ein unlesbarer lokaler Hinweis kann nur bewusst verworfen werden, ohne einen
Serverwrite zurueckzunehmen. Bei nicht verfuegbarem Browserspeicher wird vor
dem Senden gestoppt. Eine Sperre gilt fuer die Rolle/Kennung im angesprochenen
Verwaltungsserver, nicht als Abbruch eines externen Workers oder einer Maschine.

Alle drei Writes brauchen eine kanonische UUID als `Idempotency-Key`.
Artikel-Upsert verlangt die aktuelle Artikelrevision, `0` nur bei Neuanlage.
Zuordnung verlangt die aktuelle Zuordnungsrevision (`0` nur ohne bisherigen
Bezug), Ressourcenrevision und Artikelrevision. Loesen verwendet `article_id:null`
ohne Artikelrevision. Historische Belege liefern die damalige Antwort, nicht
den heute aktuellen Stand. Rollengetrennte Belegabfrage, exakter Retry und eine
Commit-Sperre sind vorhanden. Ein fehlender Beleg beweist keinen Abbruch.
Die Sperre ist schon vor der Modellumstellung moeglich und nimmt einen bereits
committeten Artikel/Bezug nicht zurueck. Keine automatische Wiederholung.

| Neue API | Rechte / Bedeutung |
|---|---|
| `GET /api/v1/erp/model` | Admin/ERP: aktueller Modellstatus |
| `GET /api/v1/erp/model/migration-preview` | Admin: gebundene, schreibfreie Umstellungsvorschau |
| `POST /api/v1/erp/model/migrate` | Admin: ausdrueckliche Transaktion mit DB-Sicherung |
| `POST /api/v1/erp/articles/upsert` | Admin/ERP: gemeinsame kaufmaennische Felder, keine CAM-Anlage |
| `GET /api/v1/erp/articles` | Admin/ERP: Suche, exaktes Firmen-/Systempaar, Seiten |
| `GET /api/v1/erp/articles/{id}/variants` | Admin/ERP: aktueller Artikel und technische Zuordnungsmetadaten |
| `GET /api/v1/erp/variants/{resource_id}` | Admin/ERP: heutiger Bezug, Artikel und getrennter alter Snapshot |
| `PUT /api/v1/erp/variants/{resource_id}` | Nur Admin: Bezug setzen/wechseln/loesen |
| `GET /api/v1/erp/articles/{id}/history` | Admin/ERP: unabhaengige kommerzielle Revisionshistorie |
| `GET /api/v1/erp/variants/{resource_id}/history` | Admin/ERP: unveraenderte Bezugshistorie |
| `GET /api/v1/erp/requests/{key}` | Eigene Rolle: dauerhafter Originalbeleg oder Commit-Sperre |
| `POST /api/v1/erp/requests/{key}/cancel` | Eigene Rolle: offenen Commit sperren, keinen bestehenden Write rueckgaengig machen |

Neue Leser verwenden SQLite `mode=ro`/`query_only`/Lesetransaktion. Maximal100000
Zeilen pro neuer Tabelle,32MiB je gelesener Zeile und512MiB Lesebudget fuer den
neuen Datenstand plus aktuelle Ressourcen; maximal100 Ergebnisse pro Seite und
4MiB Antwort. Seiten/History koennen an `snapshot_sha256` gebunden werden.
Keine Teilansicht als vollstaendig, keine automatische Reparatur inkonsistenter
Koepfe/Indizes/Belege. Audit-/Integritaetspruefung und vollstaendiger Backup/Restore
beruecksichtigen alle sechs optionalen Tabellen. Alte Archive ohne diese Tabellen
behalten ihren urspruenglichen Snapshot-Hash und werden nicht nachtraeglich migriert.

Alle fachlichen Endpunkte erwarten `Authorization: Bearer <Schluessel>`.
Der maschinenlesbare Vertrag ist unter `GET /api/v1/openapi.json` mit
Administrationsschluessel verfuegbar. Wichtige Endpunkte:

| Endpunkt | Zweck |
|---|---|
| `GET/POST /api/v1/resources` | Stammdaten lesen / anlegen |
| `PUT /api/v1/resources/{id}` | Revision mit `expected_revision` speichern |
| `GET /api/v1/resources/{id}/history` | Historie |
| `GET /api/v1/resources/{id}/history-summary` | Paginierte Revisionsmetadaten, ohne Payloads |
| `GET /api/v1/resources/{id}/history-diff` | Lesender, SHA-gebundener Revisionsvergleich mit Bereichsfilter/Suche |
| `POST /api/v1/integrity` | Schreibfreie Audit-/Metadaten-/Dateipruefung als Sitzungsauftrag |
| `GET /api/v1/integrity/{id}` | Fortschritt und zeitpunktbezogenes Ergebnis |
| `POST /api/v1/schema/preview` | Schreibfreier Vergleich vorhandener CAM-Werte mit einem Zielschema |
| `POST /api/v1/schema/apply` | Gepruefte Werte nur fuer Editor vorbereiten, nicht speichern |
| `POST /api/v1/erp/resources/upsert` | ERP-Daten anlegen / aktualisieren |
| `GET /api/v1/erp/catalog-preview` | Lesende ERP-Zuordnungs-/Konfliktuebersicht, nur Admin |
| `GET /api/v1/erp/resources/lookup` | Aktueller Stand fuer exakte company_id/system_id, Admin oder ERP |
| `POST /api/v1/assets` | Datei als Base64 ablegen, optionaler idempotenter Uploadbeleg |
| `GET /api/v1/asset-requests/{key}` | Uploadallokation / urspruenglichen Commitbeleg lesen |
| `POST /api/v1/asset-requests/{key}/cancel` | Noch offenen Uploadcommit sperren, keine Datei loeschen |
| `POST /api/v1/geometry-jobs` | Revisionsgebundenes lokales STEP-Importpaket, optionaler Commitbeleg |
| `GET /api/v1/geometry-requests/{key}` | Paketabsicht / urspruenglichen Jobcommit lesen |
| `POST /api/v1/geometry-requests/{key}/cancel` | Offenen STEP-Jobcommit sperren, kein NX-Prozessstop |
| `GET /api/v1/geometry-jobs/{id}/package-metadata` | ZIP/STEP-Pins pruefen, keine Geometriepruefung |
| `POST /api/v1/releases` | Konfigurierten Quellordner versionieren |
| `POST /api/v1/import-preview` | Henning-Dierk-/Standard-Merge vorab vergleichen |
| `GET/POST /api/v1/backups` | Verwaltete Snapshots/Auftraege lesen / Vollsicherung starten |
| `GET /api/v1/backups/{id}/receipt` | Separater Archiv-/Manifest-Pruefsummenbeleg |
| `GET /api/v1/backups/{id}/download` | Vollstaendig SHA-geprueftes ZIP64-Archiv herunterladen |
| `POST /api/v1/backups/{id}/verify` | Alle Objektbytes, Manifest und Datenbank pruefen |
| `POST /api/v1/backups/{id}/restore-preview` | Archiv und neues isoliertes Ziel pruefen |
| `POST /api/v1/backups/{id}/restore` | Bestaetigten Snapshot ohne Ueberschreiben wiederherstellen |
| `GET /api/v1/backup-jobs/{id}` | Persistierten Auftrag ohne automatischen Neustart beobachten |
| `GET /api/v1/backup-jobs/{id}/report` | SHA-gebundenen Abschluss-/Vorschaubericht lesen |
| `GET /api/v1/backup-requests/{key}` | Originale Annahme / nachgewiesene Fehlerbeobachtung lesen |
| `POST /api/v1/backup-requests/{key}/cancel` | Kennung nur vor Annahme sperren, kein Workerstop |
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
| `POST /api/v1/logs` | Protokoll unveraenderlich speichern, optionaler idempotenter Uploadbeleg |
| `GET /api/v1/log-requests/{key}` | Protokollallokation / urspruenglichen Commitbeleg lesen |
| `POST /api/v1/log-requests/{key}/cancel` | Offenen Protokollcommit sperren, keine Dateien loeschen |
| `GET /api/v1/logs/{id}/metadata` | Hochgeladenes Protokoll gegen SHA/Groesse verifizieren |

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

Unter „Stammdaten verwalten“ → „Abnehmen“ kann ein neues `.syslog`, `.log` oder
`.txt` vom Test-PC als Beleg hochgeladen werden (maximal 20 MB, UTF-8, UTF-16 mit
BOM oder Windows-1252). Es wird getrennt vom NX-Datenordner gespeichert; dessen
Dateien bleiben unveraendert. „Protokoll und Datenstand pruefen“ kontrolliert die
bekannten Ladefehler, CAM-Aktivitaet und den Bezug zum ausgewaehlten Kandidaten.
Zusaetzlich muss ein eindeutiger `NX_VERSION`-Header die Zielversionsfamilie belegen.
Der Server liest dabei keine im Protokoll erwaehnten Dateien. Hochgeladene Belege
sind per SHA-256 gebunden; spaetere Manipulation sperrt Scan und Aktivierung.
Die sechs fachlichen Bestaetigungen und der Abnahmebeleg bleiben erforderlich.
Ein fehlerfreies Protokoll beweist weder die korrekte Geometrie noch alleine die
NX-Zielversion ohne ausdruecklichen Versionsheader. Das Originalprotokoll auf dem Test-PC wird nicht geaendert.

### Revisionsgebundene NX-Abnahme (Vertrag 2, 13.10.2026)

Die GUI fordert vor der Freigabe eine aktuelle Protokollvorschau, alle sechs
fachlichen Bestätigungen und mindestens 20 aussagekraeftige Zeichen im Beleg.
Protokollwechsel/erneuter Scan setzt die Bestaetigungen zurueck; der Belegtext
bleibt beim Protokollwechsel erhalten. Keine automatische Freigabe durch einen
fehlerfreien Scan, eine Versionsnummer oder einen synthetischen Test.

- Unterstuetzter Versionsnachweis: eigene Zeile `NX_VERSION = 2512.8500`
  (auch Doppelpunkt/Gross-/Kleinschreibung). Die vierstellige Hauptversion muss
  passen. Mehrere Builds derselben Familie sind aufgelistet; unterschiedliche
  Familien, ungueltige Header oder fehlende Header sperren. Dateipfade, freie
  `NX 2512`-Erwaehnungen oder `UGII_VERSION` ersetzen keinen belegten Header.
  Grundlage ist der tatsaechliche Header im vorhandenen lokalen Siemens-Log,
  kein Versionsraten aus Verzeichnisnamen. Andere Logformate brauchen einen
  ausdruecklich geprueften Parseradapter, keine manuelle Versionsumgehung.
- Der vorhandene historische Log vom 23.09.2026 enthaelt ASCII-GS-Trenner (29).
  Der Decoder erlaubt ausschliesslich diesen zusaetzlichen Trenner und zeigt
  ihn diagnostisch als Leerzeichen an. Rohbytes und deren SHA bleiben gleich;
  NUL/sonstige Binaer-/Steuerdaten werden weiterhin abgewiesen. Der bekannte
  Header `2512.8500` wird erkannt; 25 bekannte Ladefehler sperren dieses alte
  Protokoll weiterhin. Es ist kein Nachweis fuer einen aktuellen Teststand.
- Bericht gibt gesamte Ladefehler-/Headerzahl an, zeigt maximal 200 Befunde
  je Liste und markiert Kuerzungen. Mehrdeutigkeit darf nicht in gekuerzten
  Befunden verschwinden. Pfadabgleich beidseitig begrenzt, niemals Lookup der
  im Log enthaltenen W:/UNC-/Altdateien. Ein laufend veraenderter Log wird
  abgewiesen; am besten eine gespeicherte Kopie hochladen.
- Scan ist schreibfrei. `POST /api/v1/logs/{alias}/scan` mit `release_id` liefert
  `sha256`, `manifest_sha256`, `version_check` und `can_review`. Freigabe-POST
  `releases/{id}/review` braucht neben sechs `tests`, `evidence`, `syslog_source`
  die Pins `expected_log_sha256` und `expected_manifest_sha256`. Fehlende/
  veraltete Pins oder unqualifizierende Belege: HTTP 409 vor Metadatenmutation.
  Der Server prueft Log und gesamten Kandidaten nochmals; eine schreibende
  NX-Sitzung zwischen Scan/Abnahme muss beendet oder der Log neu hochgeladen werden.
- Seit15.10.2026 werden Protokollbytes und das vollstaendige Kandidatenmanifest
  zusaetzlich direkt in der abschliessenden SQL-Transaktion vor Freigabe oder
  Aktivzeiger-UPDATE gelesen. Hochgeladene Logmetadaten stammen dabei aus genau
  derselben Transaktion. Spaete Dateiaenderung, neue/fehlende Datei, verlorener
  Lesezugriff oder veraenderter Log: HTTP409, keine neue Freigabe/Umschaltung.
  Vorher gespeicherte Diagnosen und historische Reviews bleiben erhalten;
  ein idempotenter Schreibbeleg endet ohne Erfolgsantwort. Historischer Retry
  eines bereits abgeschlossenen Vorgangs prueft/schaltet weiterhin nichts neu.
- Die GUI prueft den vollstaendigen Scanvertrag vor Anzeige einer qualifizierten
  Vorschau: Quelle/Kandidat/Zielversion/Pfad, Parserstand2, SHA-Pins, echte
  Booleans, gesamte Fehler-/Headerzahl, maximal200gezeigte Befunde und deren
  Kuerzungskennzeichen. Unvollstaendige/widerspruechliche Antworten sperren die
  Freigabe und zeigen keinen alten Scan als aktuelle Vorschau. Eine Auswahl
  A -> B -> A verwirft auch spaete Erfolg-/Fehlerantworten; Neupruefung bleibt
  moeglich. Sechs fachliche Bestaetigungen bleiben separat erforderlich.
- Der neue Client verlangt `release_transitions.final_evidence_byte_gate=true`
  und `filesystem_atomic_snapshot=false`. Ein aelterer/schwaecherer Server darf
  keine Freigabe/Aktivierung freischalten. Die SQL-Transaktion serialisiert
  Metadatenwriter, nicht externe Dateisystemwriter: dies ist eine frische
  Bytepruefung, kein atomarer Dateisystem-Snapshot oder Ersatz fuer Rechteentzug.
- Die allgemeine technische Ordnerpruefung verwendet fuer jedePAX einen
  begrenzten XML-Leser:20MiB jeDatei,50000Knoten,Tiefe64,keineDTD/Entities.
  Syntax-/Grenz-/Lesefehler sind sichtbare Abnahmesperren statt Serverfehler.
  Hier werden keine neuen NX-Vorlagenfelder oder Palette-Schemata erfunden;
  die strengere Deklarationspruefung verwalteter Export-PAX bleibt separat.
- Gespeicherte Abnahme enthaelt `contract_version=2`, Zielversion, Manifest-SHA,
  Parserstand und Logdiagnose. Revalidierung erhaelt aktuelle unveraenderte
  Freigaben. Veraenderte/fehlende Logs, falsche/alte Bindungen oder Kandidaten-
  fehler verlangen Neuabnahme. Alte Reviews bleiben als historische Belege
  erhalten; nur Status/aktuelle Pruefung werden mit neuem Audit fortgeschrieben.
- Historischer Aktivzeiger wird nicht automatisch geloescht/umgeschaltet.
  Alte Freigaben ohne aktuellen Vertrag qualifizieren aber nicht als nutzbarer
  Stand; GET zeigt `usable=false`. Explizite Validierung demotiert zu validated
  (oder blocked bei Dateifehlern), ohne alten Review zu verlieren. Aktivierung
  prueft aktuelle Abnahme und Generation erneut; eine veraltete Generation
  erzeugt weder Umschaltung noch eine zusaetzliche Validierungsmutation.
- GUI laesst Abnahme schliessen oder einen anderen Kandidaten waehlen.
  Verspaetete Scan-/Freigabeantworten veraendern den neu ausgewaehlten Dialog
  nicht. Logout leert die Abnahme und verwirft spaete Antworten. Bereits
  abgesendete Serveraktionen koennen trotzdem fertig werden: Navigation/
  Abmeldung ist kein Abbruch. Daten-/Abnahmehistorie und Aktivzeiger pruefen.

Die Anzeige eines nutzbaren Aktivzeigers bewertet gespeicherte Abnahmemetadaten,
nicht fortlaufend jeden NX-Dateibyte. Validieren/Aktivieren prueft die realen
Dateien. Externe Schreibrechte auf verwaltete Ordner/SQLite muessen im Betrieb
gesperrt werden; SHA/Logtext sind keine Signatur gegen einen lokalen Administrator.
Der echte aktuelle NX-/Assistant-/Maschinennachweis bleibt fuer die spaetere
Erprobung durch den Benutzer offen.

### Protokollupload nach verlorener Antwort klaeren

Protokolluploads aus der Abnahme benutzen jetzt einen kanonischen UUID-Header
`Idempotency-Key`. Annahme/allokierter privater Pfad liegen vor dem Schreiben
in `log_requests`. Log-Metadaten, urspruengliche Antwort und Audit werden nach
SHA-/Groessenpruefung gemeinsam committed. Derselbe Versuch mit denselben
Dateibytes/Dateinamen liefert den urspruenglichen Beleg, ohne zweite Datei oder
neuen Audit. Andere Bytes/Dateinamen unter derselben Kennung sind gesperrt.
Ohne Header bleibt die bisherige API nutzbar, aber ohne Wiederanlaufgarantie.

Bei verlorener Antwort erscheint **Unbestaetigten Protokollupload klaeren**:
Serverbeleg pruefen, denselben RAM-Payload bewusst wiederholen oder den noch
nicht committed Versuch sperren. 404 beweist keinen Abbruch. Reservierte/
fehlgeschlagene Teilpfade werden nie auf derselben Kennung ueberschrieben oder
geloescht. Eine Sperre macht einen offenen Commit unmoeglich, beendet aber
keinen Writer. Sicherungen warten bis zum nachgewiesenen physischen Writerende.
Ein bereits committed Protokoll bleibt auch nach „Sperren“ unveraendert erhalten.

Nur Vertrag/Kennung/Zeitpunkt liegen im sessionStorage dieses Browsertabs,
keine Schluessel, Dateinamen, Protokollbytes oder CAM-Daten. Nach Reload nur
Belegpruefung/Sperre, kein rekonstruierter Upload. Wiedergefundenes Protokoll
nicht automatisch einer neu geoeffneten Abnahme zuweisen: explizite Leerwahl
verhindert auch ein stilles Vorwaehlen des ersten neuen Logs. Upload im selben
unveraenderten Dialog waehlt seinen Beleg erst nach separater Dateiverifikation.
Neue Eingaben/Dateiauswahlen bleiben bei spaeterem Abschluss alter Uploads erhalten.

Der Commitbeleg ist keine Dateiverifikation oder NX-Abnahme. Bei spaeterer
Dateimanipulation/Verlust bleibt der Metadatenbeleg committed, aber dessen
Dateiverifikation/Scan scheitert. Optional nur diesen Metadatencommit anerkennen,
ohne Zuordnung/Freigabe; Integritaetsproblem separat klaeren. Log-/Kandidaten-
SHA, Versionsnachweis und sechs fachliche Bestaetigungen bleiben erforderlich.
Audit-/Integritaets-/Backup-/Restoreformat umfasst die neue Tabelle; alte
Archive ohne sie werden zuerst mit ihrem urspruenglichen Snapshot-SHA geprueft.
Beim Serverstart wird nur die fehlende Tabelle ergaenzt, keine alte Auditzeile
umgeschrieben. Der gesamte Metadatensnapshot-SHA aendert sich bei dieser
Schemaerweiterung; alte Datenzeilen und Auditkopf bleiben unveraendert.

### Freigabe und lokale Aktivierung nach verlorener Antwort

Die GUI verwendet fuer beide Aktionen einen `Idempotency-Key` und einen
dauerhaften `release_requests`-Beleg. Derselbe Versuch liefert die
urspruengliche Antwort, ohne erneute Freigabe, Validierung oder Umschaltung.
Andere Abnahmeinhalte, Kandidaten oder Aktivzeiger-Generationen unter derselben
Kennung werden abgewiesen. Ohne Header bleibt die Legacy-API verfuegbar,
jedoch ohne Wiederanlaufgarantie. ERP-Zugangsdaten berechtigen nicht dazu.

Bei unklarer Antwort erscheint **Freigabe oder lokale Aktivierung klaeren**.
Serverbeleg ausdruecklich pruefen, identischen RAM-Payload wiederholen oder
noch offenen finalen Commit sperren. 404 ist kein Abbruchnachweis. Reservierte,
fehlgeschlagene und gesperrte Versuche werden nicht neu ausgefuehrt. Eine
Sperre beendet keinen Worker: bereits zugelassene technische Validierung kann
Diagnosen/Validierungsstatus speichern. Nur die abschliessende Freigabe bzw.
Zeigerumschaltung wird atomar gegen die Sperre geprueft. Bereits abgeschlossene
Freigaben/Aktivierungen werden nicht zurueckgenommen. Backup/Restore wartet
auch nach einer Sperre auf den nachgewiesenen Abschluss des zugelassenen Writers.

Der historische Beleg ist an seine urspruengliche Antwort und Auditzeile
gebunden, nicht an den heutigen Kandidatenstatus oder Aktivzeiger. Recovery
laedt den heutigen Bestand separat; alte Antworten werden niemals als heutige
Freigabe/Zeiger uebernommen. Bei fehlgeschlagenem Bestandsabruf kann man nur
den bekannten historischen Commit anerkennen; anschliessend Bestand neu laden.
Wiederholungen aktivieren keinen ehemals aktiven Datenstand erneut.

Im Browsertab bleiben nur Vertrag, Kennung, Aktion und Zeitpunkt. Keine
Abnahmeinhalte, CAM-Pfade, Dateien oder Zugangsschluessel werden gespeichert.
Nach Reload/Reconnect gibt es keinen rekonstruierten Payload und keinen
automatischen Retry. Ungeklaerte/kaputte Hinweise sperren neue Freigaben und
Aktivierungen. Spaete Antworten duerfen keine neuen Abnahmeeingaben ersetzen.
Log-/Manifest-Pins, richtige NX-Version und sechs menschliche CAM-Pruefungen
bleiben unveraendert erforderlich; keine produktive SMB-Umschaltung.

Integritaet, Sicherung und Restore pruefen die neue Receipt-/Auditbindung.
Alte Archive ohne diese Tabelle behalten ihren urspruenglichen Snapshot-SHA;
die fehlende Tabelle wird erst nach der Archivpruefung ergaenzt. Restore
erhaelt historische Antworten, setzt aber keinen alten Aktivzeiger und
qualifiziert keine alte Freigabe automatisch neu.

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

Der durchgehende Bibliotheks-Integrationstest prueft zusaetzlich den gesamten
Weg von einer alten Testkopie zum neuen Ordner: neue Spalten explizit bestaetigen,
alle 41 Bibliotheken zusammenfuehren, Henning-Dierk-Gruppen integrieren,
Stammdaten als Entwuerfe uebernehmen, eine taegliche Revision speichern und
einen neuen Export samt automatisch erzeugtem Test-Starter herunterladen.
`tests/test_full_library_upgrade.py` prueft den API-/Buildweg;
`tests/test_full_library_upgrade_browser.py` den anschliessenden GUI-Ablauf.
Der Standardmodus erzeugt ausschliesslich synthetische Testdaten und kehrt
auch die Zielspaltenreihenfolge um. Ein zusaetzlicher Windows-Opt-in liest
nur die registrierten DAT-Schemata unter `C:\Siemens\NX2406`/`NX2512`,
prueft deren unveraenderte Hashes und verwendet weiterhin keine originalen
DATA-Objektwerte oder Modelle. Dazu im Verzeichnis `server`:

```powershell
$nxPreviousBrowserFlag = $env:NX_BROWSER_TEST
$nxPreviousStockSchemaFlag = $env:NX_STOCK_SCHEMA_TEST
try {
    $env:NX_BROWSER_TEST = '1'
    $env:NX_STOCK_SCHEMA_TEST = '1'
    & .\.venv\Scripts\python.exe -m pytest tests\test_full_library_upgrade.py tests\test_full_library_upgrade_browser.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Bibliotheks-Integrationstest fehlgeschlagen.' }
}
finally {
    if ($null -eq $nxPreviousBrowserFlag) {
        Remove-Item Env:NX_BROWSER_TEST -ErrorAction SilentlyContinue
    } else { $env:NX_BROWSER_TEST = $nxPreviousBrowserFlag }
    if ($null -eq $nxPreviousStockSchemaFlag) {
        Remove-Item Env:NX_STOCK_SCHEMA_TEST -ErrorAction SilentlyContinue
    } else { $env:NX_STOCK_SCHEMA_TEST = $nxPreviousStockSchemaFlag }
}
```

Der installierte Modus migriert je alter Bibliothek eine eindeutige FORMAT-
Projektion, nicht alle alten Klassen. Alle Zieldefinitionen bleiben erhalten.
Die zusaetzliche Testspalte ist synthetisch; echte neue Spalten brauchen
weiterhin fachlich bestaetigte Werte. Kein NX wird gestartet und ein im Test
gesetztes `approved` ist keine reale Geometrie- oder Produktionsfreigabe.
Ohne `NX_STOCK_SCHEMA_TEST=1` wird der installierte Modus ausgelassen.

Die erweiterte FORMAT-Matrix in `tests/test_format_upgrade_matrix.py` und
`tests/test_format_upgrade_matrix_browser.py` verwendet dagegen alle alten
FORMAT-Definitionen. Fuer diese beiden Module gelten dieselben Opt-ins wie
oben; im dortigen Aufruf koennen die beiden Dateinamen ersetzt werden.
Im installierten Schema-Test passen 227 von 235 alten Definitionen eindeutig;
16 davon benoetigen echte neue Zielspalten (`STYPE`, `DROT`, `FD`). Die GUI-
Pruefung fuellt und bestaetigt jede davon ueber die sichtbaren Eingabefelder.
Alle ausgegebenen Werte werden spaltenweise verglichen, nicht nur gezählt.

Acht weitere Definitionen haben anhand Klasse und Spalten mehrere Kandidaten.
Im absichtlich nicht ueber RTYPE unterscheidbaren Testaufbau bleiben ihre
Gruppen gesperrt. Erst eine ausdrueckliche Entscheidung, sie NICHT zu
uebernehmen, erlaubt den Build; dies wird nicht als Konvertierung verbucht.
Reale RTYPE-Werte koennen in einem konkreten Ordner eine andere, eindeutige
Situation ergeben. Diese Tests leiten aus Blockreihenfolgen oder leeren
Siemens-Beispielen keine fachliche Freigabe oder gueltige CAM-Werte ab.
Die Matrix bestaetigt 241 synthetische Zielstandard-, 227 Kunden- und 101
fruehere HD-Gruppen. Testwerte bleiben unqualifiziert, kein NX wird gestartet.

Optionaler echter GUI-Test mit bereits installiertem Microsoft Edge, frischem
Browserprofil und isolierten Testquellen (keine NX-Sitzung, keine Live-Daten):

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
$env:NX_BROWSER_TEST = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_browser.py -q
Remove-Item Env:NX_BROWSER_TEST
```

Fuer alle vorhandenen Browser-Testmodule (einschliesslich Reitertrennung,
Editor, Import/Export, Anhaengen, Sicherung und Wiederanlauf) statt nur des
einzelnen Einstiegstests folgenden Block im Verzeichnis `server` verwenden.
Er liest keine echten Installer und startet kein NX. Die vorherigen
Testschalter werden auch bei einem Fehler wiederhergestellt:

```powershell
$nxPreviousBrowserFlag = $env:NX_BROWSER_TEST
$nxPreviousExtractorFlag = $env:NX_EXTRACTOR_TEST
try {
    $env:NX_BROWSER_TEST = '1'
    Remove-Item Env:NX_EXTRACTOR_TEST -ErrorAction SilentlyContinue
    $nxBrowserTests = @(Get-ChildItem -LiteralPath .\tests -File |
        Where-Object { $_.Name -like '*browser*.py' -or $_.Name -eq 'test_web_layout.py' } |
        Sort-Object Name | Select-Object -ExpandProperty FullName)
    if ($nxBrowserTests.Count -eq 0) { throw 'Keine Browser-Testmodule gefunden.' }
    & .\.venv\Scripts\python.exe -m pytest -q @nxBrowserTests --tb=short
    if ($LASTEXITCODE -ne 0) { throw 'Browser-Prueflauf fehlgeschlagen; siehe Testergebnis.' }
}
finally {
    if ($null -eq $nxPreviousBrowserFlag) {
        Remove-Item Env:NX_BROWSER_TEST -ErrorAction SilentlyContinue
    } else { $env:NX_BROWSER_TEST = $nxPreviousBrowserFlag }
    if ($null -eq $nxPreviousExtractorFlag) {
        Remove-Item Env:NX_EXTRACTOR_TEST -ErrorAction SilentlyContinue
    } else { $env:NX_EXTRACTOR_TEST = $nxPreviousExtractorFlag }
}
```

Der optionale echte Extraktionstest wird in diesem isolierten Lauf bewusst
uebersprungen. Ein gruener Browser-Prueflauf ist kein Nachweis der CAM-Eignung,
der STEP-/Tool-Assistant-Verarbeitung oder eines Docker-/Produktivbetriebs.

Die gemeinsamen Race-/Dateipaket-Fixtures pruefen den lokalen Testserver
ueber eine proxyfreie, begrenzte Healthabfrage und einen lebenden Kindprozess.
Transiente Verbindungs-/HTTP-Startfehler werden hoechstens15Sekunden lang
erneut geprueft; eine beliebige HTTP-Antwort gilt nicht als Bereitschaft.
Die reinen Fehler-/Deadlinefaelle in `tests/test_browser_test_support.py`
brauchen weder Edge noch NX. Dateipaket-Exporte warten wie die vorhandenen
Hintergrundauftragstests bis15Sekunden auf den wirklichen Abschluss;
ein Annahmebeleg allein besteht den Test nicht. Laufzahlen und gesonderte
Wiederholungen stehen in `DEVELOPMENT_PROGRESS.txt`.

### Ablauf fuer Standardbibliotheken (ab 07.10.2026)

1. `server/settings.example.json`: `standards` verweist auf saubere lokale
   Siemens-Installationen (`2406`/`2512`). Quelle ist der bereits migrierte
   lokale Netzwerkordner. Dieser Ablauf betrifft den Modus „Bereits vorbereiteten
   NX-Ordner uebernehmen“. Fuer einen vollstaendigen Versionswechsel den oben
   beschriebenen Upgrade-Wizard verwenden; Kinematik/CAM-Abnahme bleibt separat.
2. `Start-NX-Verwaltung.cmd`, Schluessel eingeben. Quell- und neuen Zielordner
   auswaehlen (siehe unten), dann **Merge-Vorschau erstellen**.
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

### Verweiszuordnungen im Import und Upgrade

Die Pruefung nutzt Bibliotheksadapter, nicht allein den Spaltennamen. Beispielsweise
ist `PARTMAT` in `part_materials.dat` eine Beschreibung, in `feeds_speeds.dat`
aber ein Werkstoffverweis. Bezug ist die Attributbeschreibung der installierten
Siemens-ASCII-Dateien. CAM- und CMM-Bibliotheken sowie metrische/englische
Zielbibliotheken bleiben getrennt. Ein unveraendertes Siemens-Objekt gilt nur
dann als bekannter Warnfall, wenn auch der Verweis im Zielstandard fehlt;
eine beim Import ausgelassene, eigentlich gueltige Zieldefinition ist ein Fehler.

Bei einem fehlenden Kundenverweis zeigt die Vorschau die Zielbibliothek und
bis zu 20 aehnliche vorhandene Schluessel. Ein Schluessel muss eingegeben und
mit **Zuordnung bestaetigen** bewusst ausgewaehlt werden. Nummernaehnlichkeit
ist kein fachlicher Nachweis: etwa `TMC0_00055` und `TMC0_000055` werden NICHT
automatisch gleichgesetzt. Optional gilt die Bestaetigung fuer alle derzeit
betroffenen Kundenobjekte derselben Bibliothek/Spalte mit exakt diesem Altwert.
Andere Werte und unveraenderte Standardobjekte werden nicht umgedeutet.

Die Zuordnung aendert zunaechst nur die Entscheidungen im Browser. Danach eine
neue Vorschau berechnen. Diese zeigt die angewandten Zuordnungen mit Anzahl
betroffener Zeilen und erlaubt Ruecknahme. Alle uebrigen Verweise muessen weiterhin
aufloesbar sein. Erst ein anschliessender Import/Build schreibt in den NEUEN
Zielordner; Quellen, historische Releases und ERP-Katalog bleiben unveraendert.
Normalisierte DAT-Dateien werden binaer mit genau den geprueften UTF-8/BOM-
Bytes geschrieben und gegen `result_hashes` kontrolliert; Windows setzt dabei
keine Zeilenumbrueche um. Das gilt auch fuer unveraenderte Kommentare/FORMATs.

Das erweiterte JSON/API-Feld `reference_resolutions` hat dieses Format:

```json
{
  "tool-metric": {
    "CUSTOM_TOOL": {
      "MATREF": {"OLD_MATERIAL_KEY": "EXISTING_TARGET_KEY"}
    }
  }
}
```

Maximal 1.000 Einzelzuordnungen; keine Leerwerte, unbekannten Referenzspalten,
inaktiven/nicht vorhandenen Ziele oder nicht mehr passenden Altwerte. Vorhandene
Siemens-Standardobjekte bleiben geschuetzt. Die Entscheidungen, Zieldefinitionen,
Schemata und Datenbytes sind an die Vorschau gebunden; jede Aenderung verlangt
erneute Pruefung. Der schreibfreie Importcheck prueft den wirklich geplanten
Ordnerbestand: er leiht keine im Ordner fehlenden Datenbanken heimlich aus der
NX-Installation. Der vollstaendige Upgrade-Build beruecksichtigt dagegen die
tatsaechlich kopierten Zielstandardbibliotheken.

Eine fehlende Definition muss fachlich geklaert und ggf. ueber die Stammdatenpflege
angelegt werden. Auskommentierte `#DATA`-Beispiele sind keine aktiven Datensaetze;
sie werden nicht automatisch eingeschaltet. NX-Start allein bestaetigt weder
korrekte Schnittwerte noch vollstaendige Material-/Halterverknuepfungen.

#### Quell- und Zielordner auswaehlen (09.10.2026)

Im Importdialog sind beide vollstaendigen Serverpfade sichtbar und editierbar.
**Quellordner auswaehlen** oeffnet einen Ordnerdialog innerhalb des ausgewaehlten,
administrativ freigegebenen Quellbereichs. **Zielordner auswaehlen** waehlt den
uebergeordneten Ordner; der neue Unterordnername bleibt im Feld **Neuer Zielordner**
editierbar. Auch leere, schon vorhandene Zielordner werden abgelehnt. Es wird
nichts in bestehende Ordner hineinkopiert und keine Quelle ersetzt.

Die native Testkonfiguration bietet die bisherigen Quellordner und zusaetzlich
`C:\NX_Migration` zum Durchsuchen an. Ziele koennen im verwalteten Bereich
`C:\NX_Migration\ServerData\r` oder in einem neuen Unterordner von
`C:\NX_Migration` entstehen, beispielsweise `C:\NX_Migration\NX2512_Test_01`.
Verwaltungsmetadaten, bestehende Releases, Quellen und Siemens-Standards bleiben
geschuetzt. Die breite Testwurzel ist kein gueltiger NX-Quellordner: den konkreten
Unterordner mit dem Netzwerkordner-Aufbau auswaehlen.

Weitere Bereiche werden ueber `source_roots` bzw. `target_roots` in der
Serverkonfiguration freigegeben (Alias → absoluter lokaler Serverpfad). Der
reservierte Zielalias `managed` verweist immer auf `<data_dir>/r`. Die GUI erlaubt
keinen beliebigen Dateisystemzugriff ausserhalb dieser Bereiche. W:, UNC-/Netzpfade,
Traversal, Symlinks/Junctions, Quell-/Zielueberlappungen und Wiederverwendung alter
Releases werden abgefangen. Die Vorschau legt keinen Zielordner an und ist an
Quelle, Ziel und Bibliothekspruefsummen gebunden. Bei einer Aenderung ist eine neue
Vorschau erforderlich; die Zielpruefung erfolgt vor dem Import erneut.

Im Docker-Betrieb zeigt die Auswahl **Containerordner**, nicht das Dateisystem
des Browser-PCs. Weitere Quellen/Ziele benoetigen ausdrueckliche Mounts und eine
angepasste Serverkonfiguration; die bestehenden Compose-Testprofile werden nicht
um breitere Freigaben erweitert. Quellen bleiben read-only eingebunden. Bei der
Windows-Testzuordnung wird ein Ziel unter `/data/r` zusaetzlich als Hostpfad unter
`C:\NX_Migration\DockerReleases` angezeigt. Neue Zielpfade werden in SQLite,
Manifest und Audit verwaltet; ein frei waehlbarer Testordner ist dadurch noch
nicht gegen direkte Schreibzugriffe des Windows-Benutzers/Hostadministrators
geschuetzt und noch keine produktive SMB-Veroeffentlichung. Fuer Backups gehoeren
auch ausserhalb von `data_dir` gewaehlte Releaseordner zum Sicherungsumfang.

API-Erweiterungen: `GET /api/v1/sources` liefert `source_roots` und `target_roots`;
`GET /api/v1/folders?purpose=source|target&root=<Alias>&path=<absoluter-Pfad>`
listet ausschliesslich Ordner innerhalb einer Freigabe (Administrator erforderlich).
`POST /api/v1/import-preview` und `POST /api/v1/releases` akzeptieren optional
`source_path` sowie das zusammengehoerige Paar `target`/`target_path`.
Ohne diese Felder bleibt der bisherige Alias-/automatische-Zielpfad-API-Ablauf
kompatibel. Ableitungen bestehender Releases entstehen weiter in `managed`.

Diese Auswahl erweitert den Import eines bereits migrierten Ordners. Sie fuehrt
noch nicht die separate PowerShell-Drei-Wege-Migration NX2406 → NX2512 aus.

#### Automatisches Testpaket pro NX2512-Datenstand (09.10.2026)

Jeder erfolgreiche NX2512-Import und jeder neu abgeleitete Export erzeugt nun
automatisch einen eigenen Windows-Teststarter samt Paket. Die GUI bietet unter
**Datenstaende und Freigaben** den Button **Test-Starter herunterladen** an:

1. Gesamtes ZIP in einen **neuen lokalen Ordner ausserhalb des NX-Datenordners**
   entpacken; nicht nur die CMD herausziehen.
2. `LIESMICH.txt` lesen, bisherige NX-Sitzungen schliessen und
   `Start-NX2512-Test.cmd` starten. NX2512 muss lokal installiert sein, standardmaessig
   unter `C:\Siemens\NX2512`; eine passende NX-Lizenz ist weiterhin notwendig.
3. Das Paket bindet Release-ID, Zielpfad und die vollstaendige Dateiliste inklusive
   Staging-Marker/SHA-256. Die erste Ausfuehrung erzeugt eine separate Testumgebung
   unter `C:\NX_Migration\TestRuns\<Release-Kurz-ID>`. Maschinenpalette und
   `custom_dirs.dat` werden dort erzeugt; NX-Benutzerdateien und Systemprotokolle
   liegen ebenfalls dort, nie im verwalteten Datenordner. Fehlt eine Kundenpalette,
   bleibt die Siemens-Systempalette unveraendert. Fehlerhafte Kundenpaletten und
   geschuetzte/neue nicht aufloesbare Referenzen werden nicht still ignoriert.
4. Weitere Starts pruefen Release-Dateien und Testkonfiguration erneut; ein
   vorhandener anders gebundener Testordner wird nicht ersetzt. `-CheckOnly`
   startet NX nicht. Beim ersten Aufruf werden trotzdem die kleine Testkonfiguration
   und Bindungsdatei erstellt, danach sind Pruefaufrufe rein lesend.

Pakete liegen separat unter `<data_dir>/test-launchers/<Release-ID>/<Paketrevision>`; der Server
speichert Paket-/Manifest-SHA und Erstellungsaktion in SQLite/Audit. Keine
API-Schluessel und keine NX-Binaerdateien im ZIP. Der Server fuehrt weder NX noch
PowerShell aus. Paketfehler verhindern, dass ein neuer Import als fertig gemeldet
wird. Unvollstaendige/fehlgeschlagene Releases haben keinen nutzbaren Starter.
Beim Serverstart werden fehlende oder durch neue Startskripte veraltete Pakete fuer vorhandene intakte NX2512-Entwuerfe,
validierte und freigegebene Staende automatisch nachgezogen. Alte blockierte
Staende erhalten nichts ungefragt; fuer diese gibt es **Test-Starter vorbereiten**.
Ein Paket ist keine technische oder fachliche Freigabe und schaltet nichts um.

Startkorrektur (12.10.2026): Die vollstaendige Pruefung zeigt jetzt Dateizaehler;
bei grossen Datenstaenden dauert sie mehrere Minuten. Kontrollierte NX-Variablen
(auch ein geerbtes `SPLM_SHR_DIR`) werden ausschliesslich im Kindprozess durch
lokale Testpfade ersetzt. Nicht kontrollierte NX-Variablen mit Netzwerkreferenzen
bleiben gesperrt. Der Start erfolgt ohne Shell mit expliziter Kindprozessumgebung;
PID, fruehes Prozessende und Startfehler werden angezeigt und unter
`<TestRoot>/temp/nx-test-launcher.log` protokolliert. Ein Prozessstart bestaetigt
noch kein geoeffnetes NX-Fenster und keine fachliche Abnahme. CMD-Dateien halten
das Fenster offen und geben den PowerShell-Fehlercode weiter.
Korrigierte Pakete erhalten eine neue Revision mit Audit-Eintrag. Alte Pakete
werden aufbewahrt; der NX-Datenstand und seine Pruefsummen bleiben unveraendert.
Ein neu heruntergeladenes Paket kann dieselbe intakte, gebundene Testumgebung
verwenden. Ein schon erzeugter Starter im Testordner verweist jedoch weiterhin
auf sein urspruengliches Paket; zum Aktualisieren den Starter aus dem neuen
Paket verwenden (vorhandene Testkonfigurationen werden nicht ueberschrieben).

Im Linux-Container wird der konfigurierte Windows-Hostpfad als Text ins Paket
geschrieben; die Container-/Windows-Zuordnung bleibt explizit. Fehlt diese, wird
das Paket trotzdem erstellt, fragt beim Start aber nach einem lokalen Windows-Pfad
mit exakt denselben Dateien. Ein Test einer lokalen Kopie ist kein Freigabebeleg
fuer einen anderen Serverpfad; die bestehende Abnahme verlangt weiterhin die
tatsaechliche verwaltete Ordnerreferenz im NX-Protokoll. Netzlaufwerke/UNC/W: sind
in diesem lokalen Pilot weiterhin gesperrt. Andere NX-Versionen brauchen eigene
Umgebungsadapter; der neue Teststarter behauptet keine generische Versionsfreigabe.

Die bisherigen Projekt-Skripte `New-NX2512TestEnvironment.ps1`,
`New-NX2512TestPalette.ps1`, `Start-NX2512ManagedTest.ps1` und
`NXMigrationSafety.psm1` bleiben kompatible Einstiegspunkte. Ihre gemeinsame
Implementierung liegt jetzt unter `server/nxserver/testlaunch_assets` und wird
auch ins Docker-Image und die Testpakete aufgenommen. Keine duplizierten
PowerShell-Implementierungen. Fuer vorhandene manuelle Starter muss daher das
Projekt mitsamt diesem Unterordner erhalten bleiben; neue Pakete sind selbststaendig.

Administrator-API: `POST /api/v1/releases/{id}/test-launcher` bereitet einen
vorhandenen Stand idempotent vor; `GET /api/v1/releases/{id}/test-launcher/package`
liefert das bereits erzeugte, SHA-gepruefte ZIP. Releases enthalten die Metadaten
in `test_launcher`. ERP-Token duerfen weder Pakete erstellen noch herunterladen.

### STEP-Vorbereitung mit dem NX CAM 3D Tool Assistant (07.10.2026)

Seit16.10.2026 begrenzen neu erzeugte STEP-Journale die Diagnose auf10000
Komponenten und500Zeichen jeKomponenten-ID. Bei Ueberschreitung bleiben
Dateien und eine begrenzte Teilinventur als hochladbarer Fehlerbericht
erhalten; es gibt keine automatische CAM-Freigabe. Die Grenze wird vor
weiteren Prototypabfragen geprueft. Vorhandene Jobpakete und ihre Hashes
bleiben unveraendert: fuer diese Korrektur einen neuen Auftrag anlegen,
nicht den Programmcode in einem alten ZIP austauschen. NX/STEP selbst
wurde dafuer nicht ausgefuehrt; reale Erprobung bleibt separat erforderlich.

Neu erzeugte Journale begrenzen ausserdem geladene Teile auf5000 und Koerper
je Teil auf10000. Sie beenden die Inventur vor dem Zugriff auf das naechste
abgewiesene NX-Objekt. Eine unvollstaendige Koerperliste wird nicht als
vollstaendiges Teil ausgegeben. Die gesamte `report.json` bleibt einschliesslich
UTF-8-Namen und Fehlermeldungen innerhalb der1MiB-Uploadgrenze von GUI und API.
Die Datei verwendet kompaktes JSON; bei Platzmangel bleiben ausdruecklich als
`failed` gekennzeichnete Metadatenpraefixe erhalten. Sehr lange Meldungen oder
zu viele Meldungen werden kenntlich begrenzt. PRT-/STEP-Dateien selbst werden
dabei weder gekuerzt noch veraendert. Diese Diagnose ist keine CAM-Freigabe.
Auch diese Korrektur gilt nur fuer neu erzeugte Job-/Collector-Pakete; bereits
gespeicherte Pakete und deren Hashes bleiben unveraendert. Der Collector nutzt
dieselbe Koerperanzahlgrenze und bricht bei deren Ueberschreitung ohne
Ergebnisarchiv ab, statt eine unvollstaendige Geometrieinventur auszugeben.

Seit15.10.2026 haben neue STEP- und Collector-Pakete einen gemeinsamen
Startschutz. Auftrags-/Collector-JSON und alle Paketprogramme werden begrenzt
gelesen und anhand ihrer gespeicherten Hashes geprueft, bevor Hilfscode laeuft.
Alle Hilfsquellen werden vor der ersten Ausfuehrung kompiliert. Der Start
verwendet genau diese Quellbytes in einem eigenen kurzlebigen Namensraum;
fremde `nx_safety`-/`stepfiles`-/Collector-Importcaches, `sys.path` und
benachbarte `.pyc`-Dateien ersetzen keine Paketdateien.

Die Dateinamen bleiben unveraendert: drei STEP-Workerdateien, fuenf Collector-
Dateien. Gemeinsamer Startcode wird direkt in die Journalquellen eingebettet,
kein zusaetzlicher Installationsschritt und keine Hilfsdatei aus einem anderen
Auftrag erforderlich. STEP-Eingang und bereits vorbereitete PRT-Dateien werden
mit expliziter20MiB-Grenze und frischer Dateibindung gelesen; Programmdateien
sind auf1MiB begrenzt. Job-JSON erlaubt32MiB einschliesslich CAM-Snapshot,
damit nicht nur einfache Einzeilenwerkzeuge unterstuetzt werden.

Historische gespeicherte Pakete und ihre Hashes bleiben unveraendert. Fuer
neuen Startschutz einen neuen Auftrag bzw. ein neues Collector-Paket erzeugen,
nicht Programme in einem alten ZIP austauschen. SHA ist keine Signatur gegen
einen Administrator, der Programm und Manifest zusammen aendern kann. Echte
NX-/STEP-/Assistant-Abnahme erfolgt wie vereinbart spaeter; die Standalone-
Tests laden lediglich synthetische Paketquellen und rufen kein NX auf.

Der aktuelle STEP-Worker erstellt Datei-SHA und Groesse seiner eigenen
Ausgaben jetzt in maximal1MiB grossen Lesebloecken, ohne die gesamte PRT-Datei
in den Speicher zu laden. Dateihandle, Pfadstatus und tatsaechliche Bytezahl
werden dabei vor/nach dem Lesen abgeglichen. Auch leere Translatorprotokolle
koennen als leere Diagnoseartefakte aufgefuehrt werden; das ist kein zulaessiger
leerer CAM-Modellanhaenger.

Bei Lesefehler, Dateiwechsel oder fehlgeschlagener Verzeichnisinventur bleibt
der Auftrag `failed` und erhaelt im weiterhin beschreibbaren lokalen
Ausgabeordner einen Diagnosebericht. Nur erfolgreich gepruefte Dateien werden
mit Hash aufgefuehrt; bei mehr als10000Ausgabedateien wird ausdruecklich eine
fehlgeschlagene Teilinventur gemeldet. Erzeugte Teile und alte Eingaben werden
nicht geloescht. Der Fehlerbericht kann in der GUI hochgeladen werden, ohne
Dateien in NX-Bibliotheken zu uebernehmen oder CAM-/Kollisionsfreigaben zu setzen.
Dies ist weiterhin kein atomarer Dateisystem-Snapshot oder echter NX-Nachweis.

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
   Neue Auftraege speichern dieses ZIP unveraenderlich; spaetere Serverupdates oder
   Diagnosen aendern weder Journal noch Eingangskopie im alten Paket. SHA-256 und
   Paketgroesse werden bei Download geprueft. Fehlende/manipulierte Pakete werden
   nicht unbemerkt neu erzeugt. Altauftraege ohne gespeichertes ZIP koennen nur mit
   den exakt urspruenglichen Worker-Hashes rekonstruiert werden; andernfalls ist ein
   neuer Auftrag noetig.
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
   Verschachtelte Teil-/Koerper-/Komponenten-/Dateidaten werden strikt geprueft,
   ebenso relative Ausgabepfade, doppelte Eintraege, Referenzen, Zaehler und Status.
   Kein Berichtpfad wird geoeffnet. Ein `needs_selection` ohne geeignetes gemeldetes
   Inventar wird abgewiesen. Die GUI zeigt Diagnose-Teile und weist auf veraltete
   Stammdatenrevisionen hin. Auch ein formal gueltiger Bericht ist kein verifiziertes
   PRT-/Baugruppenpaket und keine automatische CAM-Freigabe.
7. Strukturprobleme beheben und den separat gelieferten Siemens-Assistant ueber
   **Ctrl+U / NX Open / NXCAM3DToolAssistant.dll** starten (nicht Journal Play).
   Montage-/Koerperauswahl und Werkzeugparameter fachlich festlegen. Erst in eine
   lokale Testbibliothek exportieren, LIBRF/DAT/PRT inklusive aller Komponenten
   pruefen; danach gepruefte Daten in der Verwaltung pflegen. Automatischer
   Fuer den gebundenen Rueckimport kompletter lokaler DAT-/PRT-Ergebnisse den
   folgenden Ergebnis-Collector verwenden. Die Assistant-Koerper-/Montageauswahl
   bleibt interaktiv; die sechs Geometriepruefungen bleiben erforderlich.

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

### Ergebnisupload und Werkzeug-Rueckimport als Verwaltungsauftraege (14.10.2026)

Ergebnispaket hochladen und gepruefte Ergebnisse uebernehmen nutzen jetzt die
gemeinsame Auftragsverwaltung im Reiter **Stammdaten verwalten**. Neue Admin-API:
`POST /api/v1/release-jobs/result_upload?geometry_job_id={id}` mit
`Content-Type: application/zip` (1 Byte bis 64 MiB, exakter Content-Length),
und `POST /api/v1/release-jobs/result_adopt` mit `result_id`,
`target_release_id`, `preview_hash`. UUID-Idempotency-Key von der GUI immer
verwendet. HTTP 202 nur Annahme. Fortschritt/Bericht ueber `migrations/{id}`
bzw. `/report`; Originalannahme/Fence ueber `migration-requests/{key}`.
Bestehender Vertrag 1 um result_upload/result_adopt erweitert, keine neue Tabelle.

Uploadbytes werden fuer den gestarteten Worker nur im RAM gehalten. Request-
und Annahmedaten enthalten Geometriejob, Bytegroesse und SHA, keine Modelle.
Worker prueft ZIP/Inventar/Source-Bindungen vor Archivwrite. Auditierte
`result_allocation` reserviert ID/SHA/Groesse VOR FS-Write. Danach Archivzeile,
stored-Absicht, domain_result und Audit gemeinsam im SQL-Commit. Teilwrite/
Auditfehler behalten reserved-Diagnose und ggf. Teilarchiv, keine automatische
Loeschung/zweite Datei. Identische intakte bereits gespeicherte Bytes koennen
ohne neue Datei als Operationsergebnis gebunden werden. Gleiches Requestkey
liefert nie neuen Worker, auch nach Fehler oder verlorenem Reply.

Die Adoption reserviert alle neuen Anhaenge vor Dateikopie. Stored-Assets
bleiben bei spaeterem Fehler zugeordnet diagnostisch erhalten. Parent und
zugehoerige Resource(n), immutable Revisionen, Ergebnisanwendung und Job-Domain-
resultat committen zusammen. Ganzen Katalog-Revisionssatz nach Kopie und in
BEGIN IMMEDIATE erneut pruefen, Ziel-/Archiv-/Referenzvorschau nach Copy ebenso.
Fremder Owner/ERP-/Zieldrift sperrt Entwurfscommit. Kein Parent-/Siemens-/NX-
Ordnerueberschreiben, keine automatische Geometriefreigabe. Schon anderweitig
committed Anwendungen bewusst ansehen, nicht per neuem Job weitere Kopien bauen.

Job-GET bindet Archiv-/Anwendungsergebnis und Ressource/Version/Assetmetadata
in einer Lesetransaktion. Das ist Metadatenbeweis, kein neuer Dateiscan oder
NX-Lauf. Berichtfehler NACH Domaincommit behaelt das Ergebnis; failed bedeutet
nicht automatisch nichts gespeichert. Integritaet und Vollbackup/isolierter
Restore enthalten Domainresultate, Archiv-/Anhangsabsichten und Teilstaende.
Collector-Paketmetadaten werden gegen unabhaengige Writer transaktional merged,
alte Collector-/Workerdateien bleiben unveraendert. Weiter kein Assistantstart.

Bei verlorener Antwort im Verwaltungsauftragsbereich Annahme pruefen, exakt
wiederholen oder nur VOR Annahme sperren. Das Tabticket enthaelt nur Vertrag,
Kennung, Modus, Zeit. ZIP-ArrayBuffer nur RAM, keine Modelle, Identitaeten,
Zielentscheidungen oder Tokens im sessionStorage. Nach Reload nur Beleg/Fence,
kein neues ZIP aus erfundenen Bytes. Neue Editor-/Ergebnisauswahl bleibt erhalten;
der alte Upload wird nach Reload/Auswahlwechsel nicht automatisch ausgewaehlt.
Vorschau-Auswahl-Nonce verhindert alte Antwort nach Wechsel weg/zurueck.

Die alten synchronen prepared-result-/apply-/Collector-Endpunkte bleiben fuer
bestehende Clients vorhanden, nicht als Hintergrund-Recovery ausgegeben. Sechs
taegliche Verwaltungsmodi und atomic_adoption_result/durable_attachment_intents
sind fuer diese GUI erforderlich. Echte NX-/Assistant-Abnahme laut Benutzer
spaeter. Paketbytes/PRT-Endung/Inventar allein qualifizieren kein Werkzeug.

### Laufende Ausfuehrungspruefung fuer Verwaltungsauftraege (14.10.2026)

Die Auftragsliste und `GET /api/v1/migrations/{id}` pruefen laufend den lokalen
Future, die gespeicherte Owner-Lebenszeit und den bekannten Unterprozess.
Ein unerwartet fehlgeschlagener/abgebrochener Task oder ein Task ohne terminale
Veroeffentlichung bleibt nicht dauerhaft als laufend angezeigt. Nach nachgewiesenem
Ownerverlust ohne bekannten aktiven Unterprozess wird der Auftrag unterbrochen.
Die Beobachtung wird genau einmal mit Jobstatus im selben SQL-Commit auditiert.
Ergebniszuordnungen, Entwurfsrevisionen und Teilanhaenge bleiben erhalten;
`failed` bedeutet nicht, dass zuvor keine Daten gespeichert wurden.

Solange Future oder Unterprozess noch aktiv/ungeklaert sind, werden neue
Verwaltungsauftraege, SIDT-Eingabewechsel und Backups gesperrt. Bei einem zu frueh
gespeicherten terminalen Status zeigt die API einen laufenden Beobachtungsstatus
mit `execution.persisted_status`. Diese Liveprojektion wird nicht in die DB
zurueckgeschrieben. GUI-Liste und Statusverfolgung zeigen aktive/ungeklaerte
Ausfuehrung und Unterprozesse ausdruecklich. Fehlende alte Owner-Metadaten und
Zugriffsfehler sind Ungewissheit, kein Abbruchnachweis. Ein Timeout beweist nie
das Ende, und es gibt keinen automatischen Neustart/Prozessabbruch.

Die Workspace-Capability `migration_execution` und das response-only Feld
`execution` verwenden Vertrag 1. Der urspruengliche Annahmebeleg bleibt
unveraendert und startet bei Replay keinen neuen Worker. Progress-Updates lesen
den frischen SQL-Job im selben BEGIN IMMEDIATE wie ihr Update, damit parallele
Ergebnis-/Anhangszuordnungen nicht durch alte Fortschrittsdaten verloren gehen.

Wiederhergestellte Auftraege sind historische Belege: Sie verwenden weder den
Future noch Prozess-IDs der Originalverwaltung. Die Integritaetspruefung bleibt
bei aktiven Writern vorlaeufig; Archiv-Metadatapruefungen haben keinen Livebezug
zur Originalverwaltung. Keine neue Tabelle, kein Aendern alter Archivhashes,
kein realer NX-/STEP-/Assistant- oder Docker-Laufnachweis. Modelltests bleiben
wie vom Benutzer gewuenscht auf spaeter verschoben.

### Vorbereitete NX-Ergebnisse und PRT-Komponentenpakete

Der zusaetzliche Ergebnis-Collector ist vom STEP-Importjournal/Diagnosebericht
getrennt. Er liest einen bereits fachlich vorbereiteten und gespeicherten
lokalen NX2512-Bibliotheksexport; er startet weder den Siemens-Assistant noch
exportiert/speichert/benennt er Teile um. Primaere lokale Grundlage: Siemens-
Assistant-Anleitung S.17-20, NX2512-pythonStubs (`BasePart.IsModified`,
`ComponentAssembly`, `FullPath`) und `dbc_tool_ascii.tcl` Zeile 634: die
Standardbibliothek erwartet die Werkzeuggrafik als `<LIBRF>.prt`.

1. Gespeicherten STEP-Auftrag unter **Ergebnispaket** waehlen. Exakte LIBRF des
   lokalen Assistant-Exports und passende Werkzeug-/Komplettwerkzeugbibliothek
   angeben. **Gebundenen NX-Collector herunterladen**. Das Archiv enthaelt
   nur Collector/Helfer, gebundene Auftragsdaten und Anleitung, keinen API-Key.
   Helfer und Archiv sind SHA-gebunden; wiederholter gleicher Download ist
   deterministisch. Bis zu 20 unveraenderliche Collector-Snapshots pro Auftrag.
   Der Browser bindet den Empfang an Auftrag, Stammdatenrevision, Kennung,
   Bibliothek und Sitzung. Eine Eingabeaenderung, auch mit anschliessender
   Rueckkehr zum alten Wert, oder Abmeldung verwirft den alten Download und
   bricht dessen Empfang ab. Ein bereits serverseitig gespeicherter Snapshot
   bleibt dabei erhalten; es wird kein neuer STEP-Auftrag gestartet.
   Groesse und SHA des empfangenen Datenstroms werden vor dem Speichern
   geprueft (maximal 64 MiB). Fehlende/ungueltige Belege, verkuerzte oder
   veraenderte Bytes werden nicht als Datei veroeffentlicht. Aktuelle Fehler
   bleiben sichtbar und erlauben einen ausdruecklichen erneuten Download;
   verspaetete Fehler einer verworfenen Auswahl ersetzen keine neuere Anzeige.
   Diese Empfangspruefung ist keine Signatur oder fachliche NX-Abnahme.
2. In einen frischen, kurzen Unterordner unter `C:\NX_Migration\ToolJobs`
   entpacken, NICHT das alte STEP-Importpaket/dessen Helfer ueberschreiben.
   Den lokalen Testexport unter `return-library` im Standardlayout bereitstellen:
   `MACH/resource/library/tool/metric|english/*_database.dat` und
   `MACH/resource/library/tool/graphics/<LIBRF>.prt` plus alle Komponenten.
   Unterordner/Dateinamen unveraendert lassen; kein `graphics/managed`-Zusatz.
3. In isolierter NX2512-Sitzung die dortige gespeicherte `<LIBRF>.prt` oeffnen,
   Komponenten voll laden und alle Teile vorher speichern. Fremde offene Teile,
   Referenzen ausserhalb dieser lokalen Bibliothek oder geaenderte ungespeicherte
   Teile sperren den Collector. Produktiv-/Netzwerkzugriff vorher sperren;
   eingebettete absolute Verweise koennen nicht durch den Server aus den
   binaeren PRTs bewiesen/umgeschrieben werden. Portable Referenzen selbst pruefen.
4. **Journal Play: nx_result_collect.py.** Er liest PRT-Bytes und minimale
   native DAT-Gruppen des Hauptobjekts samt enthaltenen Tool-/Halter-/Schaft-/
   Bezugspunkt-Abhaengigkeiten und erfasst die geladene Komponentenstruktur.
   Ein vorhandenes `prepared-result.zip` wird nicht ersetzt. Nur deklarierte
   lokale PRTs gelangen ins ZIP, keine DLL/Skripte oder produktiven Dateipfade.
   Neu erzeugte Collector-Pakete pruefen das vollstaendige, kompakte UTF-8-
   Manifest bereits VOR Erstellung des Ergebnis-ZIPs auf die 2-MiB-Grenze.
   Bei Ueberschreitung bricht der Collector mit einer konkreten Meldung ab;
   er erstellt kein vom Server unbrauchbar abgewiesenes Archiv und kuerzt
   keine Koerper-/Objektliste, um eine vollstaendige Inventur vorzutäuschen.
   Die gespeicherten PRT-/DAT-Dateien bleiben erhalten. Bereits geladene alte
   Collector-Pakete und deren Hashes werden nicht nachtraeglich umgeschrieben;
   fuer die Korrektur einen neuen Collector-Snapshot erzeugen.
5. ZIP unter dem gleichen gespeicherten Auftrag hochladen. Gebunden an Job,
   Ressourcenrevision/-SHA, Eingangs-STEP, Version/Werkzeugtyp und originale
   Worker-SHAs. Wiederholter gleicher Upload ist idempotent; Speicherveraenderung
   wird auch bei Wiederholungen nicht ignoriert. Andere/veraltete Auftraege oder
   ERP-Aenderungen sperren neue Anwendung. Neues Paket ist externe UNBEGLAUBIGTE
   Inventaraussage; SHA beweist Bytekonsistenz, keine korrekte Werkzeuggeometrie.
6. Verwalteten Zielstand waehlen und **Uebernahmevorschau pruefen**. Native
   FORMATs nach Namen gegen manifestgebundene Zielbibliotheken projizieren;
   unbekannte/neue fehlende Spalten, unaufgeloeste Verweise, bereits belegte
   Bibliotheks-/Grafiknamen und fremde Besitzer sperren. Keine Standard-/Katalog-
   ueberschreibung, keine Mehrdeutigkeits-/Einheitenannahmen. Gleiche verwandte
   bestehende Datensaetze werden unveraendert wiederverwendet, keine Dubletten.
   Der Browser prueft vor der Freischaltung den gesamten vorhandenen
   Vorschauvertrag: Paket-/Ziel-/Ressourcenkennung, Revision, Archiv-/Dateihashes,
   Katalogrevisionen, Schemapins, Objektaktionen, Referenzen und Entwurfsstatus.
   Alle PRT-Komponenten sind mit unveraendertem relativem Pfad, Groesse und
   SHA sichtbar. Auch eine gueltige konfliktbehaftete Vorschau bleibt lesbar,
   kann aber nicht uebernommen werden. Referenzwarnungen bleiben getrennt von
   blockierenden Fehlern; das vollstaendige Ergebnis steht in den Details.
   Auswahl- und Bestandsaenderungen entwerten die Vorschau einschliesslich
   alter Tabellen/Detailfreigabe. Nach Ladefehlern bleibt das Paket erhalten;
   Vorschau ausdruecklich erneut pruefen. Keine automatische Wiederholung.
   Fuehrt das Zielschema neue Spalten ein, zeigt die Vorschau diese getrennt
   nach Bibliothek, Objektkennung und Ziel-FORMAT. Je Feld Wert eingeben und
   ausdruecklich bestaetigen, auch bei einem Leerwert. Die angezeigte Zeilenzahl
   nennt den Geltungsbereich innerhalb dieses Objekts/FORMATs; andere Objekte
   oder vorhandene Spalten werden nicht damit geaendert. Zehn Entscheidungen
   je Seite, Eingaben bleiben beim Blaettern erhalten. Danach erneut Vorschau
   pruefen und erst dann als Entwuerfe uebernehmen. Ein Ziel-/Paketwechsel
   entfernt die alten Entscheidungen; bewusstes Verwerfen ist separat moeglich.
   DAT-Trennzeichen, Zeilenumbrueche und umgebende Leerzeichen sind fuer diese
   neuen Werte unzulaessig, damit keine stille Textnormalisierung stattfindet.
   Entfernte/umbenannte/mehrdeutige FORMATs bleiben Konflikte; keine erratene
   Zuordnung und kein Ersetzen fachlicher Geometriepruefungen.
   API: optionales `column_values` in Preview/Apply und `result_adopt`, Aufbau
   Bibliothek -> Objektkennung -> Ziel-FORMAT-ID -> neue Spalte -> Textwert.
   Nichtleere Entscheidungen werden in Vorschauhash, dauerhaftem Auftrag und
   Anwendungsbeleg gebunden und mit gesichert/wiederhergestellt. Fehlendes oder
   leeres `column_values` behaelt alte Anfrage-/Vorschauvertraege unveraendert.
   Vor dem Entwurfscommit erfolgt eine weitere Pruefung innerhalb derselben
   SQL-Schreibtransaktion: unveraenderte Ergebnis-/Auftragsmetadaten, gepruefte
   Zielordnerbindung, erneute schreibfreie Planung aus genau diesem SQL-Stand,
   frische STEP-/Paketpruefung, vollstaendiges Zielmanifest und Byte-/Metadaten-
   abgleich aller kopierten PRT-Anhaenge. Ein Fehler speichert keine Entwuerfe
   oder erfolgreiche Anwendung; bereits kopierte Anhaenge und der fehlgeschlagene
   Auftrag bleiben zur Diagnose erhalten. Ein identischer Auftragsbeleg startet
   keinen zweiten Writer. Historischer erfolgreicher Apply-Retry ist keine
   erneute Qualifizierung. SQL sperrt externe Dateischreiber nicht: dies ist
   kein atomarer Dateisystemsnapshot; Schreibrechte muessen im Betrieb begrenzt
   bleiben. Die neue Abschlusspruefung ersetzt keine fachliche NX-Abnahme.
7. **Als NEUE Entwuerfe uebernehmen.** Hauptobjekt bekommt eine neue Revision;
   neue zugehoerige Bibliotheksobjekte werden Entwuerfe. ERP, Name, Importprovenienz
   und vorhandene Quellenanhaenge des Hauptobjekts bleiben erhalten. Alle sechs
   Geometriechecks, Beleg und Hash-Bestaetigung werden zurueckgesetzt. Kein
   NX-Ordner/Release/Aktivzeiger wird dabei angelegt oder veraendert.
8. PRT-Anhaenge behalten ihre Basenames und expliziten relativen Zielpfade im
   vorgesehenen Siemens-Graphics-Ordner. Roh-STEP/Dokumente bleiben bei diesen
   expliziten Zuordnungen privat. Paketbindung ist im Editor sichtbar; normales
   Speichern erhaelt sie. Zum Aendern/Entfernen von Paketkomponenten erst bewusst
   **Paketbindung loesen**, Zielpfade/Anhangs-IDs pruefen und neu speichern.
   Dateiinhalte und alte Versionen werden nicht geloescht. Gebundene LIBRF/
   Bibliothek bleiben unveraenderlich, solange die Paketprovenienz besteht.
9. Modelle/Parameter fachlich pruefen, alle sechs Geometriechecks und Anhangs-
   SHAs bestaetigen, zugehoerige Objekte ebenfalls freigeben und erst dann einen
   neuen geprueften NX-Export erstellen. Geschlossene Dateizuordnung und
   unveraenderliches Ursprungspaket werden dabei erneut geprueft. Normale
   Zielabnahme/Syslog-Pruefung bleibt separat; niemals automatische CAM-Freigabe.

Grenzen: vorbereitetes ZIP 64 MiB, Manifest 2 MiB, bis zu 80 PRT-Dateien mit
jeweils 20 MiB / insgesamt 256 MiB, 20 native Objekte und 100.000 Feldzellen.
PRT-Dateiliste, Hierarchie, Erreichbarkeit/keine Zyklen, Originalnamen, Groessen
und SHA werden geprueft. Das Manifest muss ein JSON-Objekt ohne doppelte Felder
sein; falsche/zu tief verschachtelte Strukturen werden als Eingabefehler abgewiesen.
Ein fehlendes oder veraendertes gespeichertes Ursprungsarchiv sperrt auch
Wiederholungen und Export. Die Collector-Snapshotgrenze wird vor Dateianlage
geprueft. Root `<LIBRF>.prt` und jeder enthaltene Schneidwerkzeug-
Datensatz brauchen ihren passenden Grafiknamen. Maschinen-/Spannmittelpakete
sind hier noch nicht als Tool-Assistant-Ergebnis freigegeben; keine heuristische
CAD-Klassifikation oder Library-Nummern-Neuvergabe.

Neu: Admin-POST `geometry-jobs/{id}/collector-package` (Bibliothek/LIBRF),
`geometry-jobs/{id}/prepared-result` (binaerer `application/zip`-Body),
GET `geometry-jobs/{id}/prepared-results`, GET `geometry-results/{id}`,
POST `geometry-results/{id}/preview` (`target_release_id`) und `/apply`
(zusaetzlich `preview_hash`). ERP darf diese CAM-Aktionen nicht ausfuehren.
Metadaten liegen in `geometry_results`, Archive/Collector-Snapshots im privaten
Ordner `geometry-results`. Integritaetspruefung und Vollbackup schliessen sie ein.
Alte Snapshots ohne diese neuere Tabelle behalten ihren urspruenglichen
Source-State-Hash und erhalten erst im neuen Restorebestand eine leere Tabelle.

API-, Collector-Vertrags- und GUI-Tests verwenden synthetische PRTs/NX-Doubles.
Der reale NX2512-/Assistant-/Portabilitaetsnachweis fuer den Collector bleibt
auf Wunsch des Benutzers fuer seine spaetere Gesamterprobung offen.

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
   Fuer **alle** Aktionen validiert `NXDockerSafety.psm1` zuerst das vollstaendig
   aufgeloeste feste Testprofil. Abweichende Images/Startbefehle/Lifecycle-Hooks,
   Root-/Privileg-/Hostnamespaceoptionen, zusaetzliche Umgebungswerte oder
   Einbindungen und fremde Volume-/Netznamen werden vor Host- oder Docker-
   Aenderungen abgewiesen. Der Starter gibt keine komplette aufgeloeste
   Konfiguration mit moeglichen geheimen Umgebungswerten aus. Er ist kein
   allgemeiner Launcher fuer beliebige geaenderte Compose-Dateien.
   Nach erfolgreicher Pruefung schreibt er genau diesen Plan als neue UTF-8-
   JSON-Datei in ein geprueftes lokales Tempverzeichnis. Ein offener Dateihandle
   erlaubt Compose das Lesen, sperrt aber Aendern/Loeschen waehrend der Aktion.
   Alle weiteren Compose-Aufrufe verwenden nur diesen Plan, nicht erneut die
   Original-YAML-Dateien. Literale Dollarzeichen werden fuer das erneute
   Einlesen maskiert. Nach Erfolg oder Fehler wird nur die eigene temporaere
   Plandatei entfernt; Verwaltungsdaten/Volumes werden dabei nicht geloescht.
   Der temporaere Plan darf nicht in Quellen, Projekt oder Ausgabe liegen.
   Das pinnt die Konfiguration, nicht die Inhalte der eingebundenen Dateien,
   einen Image-Digest oder die Docker-Engine selbst. Ein abgebrochener Prozess
   kann eine kleine Rest-Plandatei im Tempverzeichnis hinterlassen; es gibt
   kein pauschales Aufraeumen fremder Dateien.
   Zusaetzlich wird die einmal gepruefte lokale Engine-Pipe fuer alle weiteren
   Abfragen/Aktionen ausdruecklich als `docker --host <gepruefte-Pipe>` angegeben.
   Ein spaeterer Wechsel des Standardkontexts oder des Endpunkts unter demselben
   Kontextnamen darf den Vorgang nicht zu einer anderen Verbindung umlenken.
   Der Starter aendert dafuer weder Docker-Kontexte noch die Benutzerumgebung.
   Ein gesetztes entferntes `DOCKER_HOST` wird weiterhin vor der ersten
   Kontextabfrage abgewiesen. Die Bindung ist keine Identitaets-/Verfuegbarkeits-
   zusicherung fuer einen hinter derselben Pipe neu gestarteten Daemon.
   [Docker-CLI-Optionen und Prioritaeten](https://docs.docker.com/reference/cli/docker/),
   [Docker-Kontexte](https://docs.docker.com/engine/manage-resources/contexts/).
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

Das Windows-Testprofil setzt den Benutzer jetzt auch explizit in Compose.
Das aufgeloeste JSON muss exakt ein bekanntes lokales Volume und Projektnetz
verwenden; ein nur gleich benannter logischer Volume-Eintrag darf kein fremdes
physisches Volume referenzieren. Compose kann einen expliziten Volume-Namen
unabhaengig vom Projekt verwenden, und Startbefehle koennen Imagevorgaben
ueberschreiben; deshalb prueft der Starter diese Einstellungen ausdruecklich.
[Volume-Namen](https://docs.docker.com/reference/compose-file/volumes/),
[Dienst-/Startoptionen](https://docs.docker.com/reference/compose-file/services/).

Der Build-Kontext erlaubt nur Programm-/Startskriptdateien sowie explizite
Vendor-Lizenztexte und deren Provenienzdatei. Andere JSON-/Textdateien,
Schluessel, Datenbanken, NX-Modelle und Archive im Anwendungspaket sind nicht
pauschal mit freigegeben. Geheimnisse trotzdem niemals in Quellcode eintragen;
die Dateityp-Allowlist ist kein Inhalts-Geheimnisscanner. Buildordner und
Anwendungspaket sowie vorhandene Ausgabebaeume werden vor Start auf
Junctions/Links geprueft. Der reale Docker-Test verwendet jetzt zusaetzlich zum
eigenen Projekt/Volume einen eigenen Image-Tag; kein Austausch von
`nx-management:local` durch einen isolierten Test.

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

#### Explizites Windows-Docker-Upgradeprofil NX2406 -> NX2512

Der bisherige Standardstart (`-Profile Prepared`, auch `Start-NX-Docker.cmd`)
uebernimmt einen bereits vorbereiteten NX2512-Ordner. Er ist kein Ersatz fuer
den vollstaendigen Dreiwege-Versionswechsel und bindet keine SIDTs ein.
Fuer den Versionswechsel gibt es jetzt ein getrenntes Profil mit
`compose.windows-upgrade.yaml` und `docker/settings.windows-upgrade.json`.
Es wird nur mit `compose.yaml` kombiniert, **nicht** zusaetzlich mit dem
vorbereiteten Windows-Profil.

1. `docker/upgrade-inputs.example.json` nach `docker/upgrade-inputs.local.json`
   kopieren und die drei Eingabepfade bewusst auf vorhandene lokale Ordner setzen.
   Die Beispielpfade sind Platzhalter; keine leeren Ersatzordner anlegen.
   `source_nx_version` muss die Zeichenfolge `"2406"` bleiben und bestaetigt die
   fachlich gewaehlte alte Testkopie, keine automatisch erkannte Dateiversion.
   Einen schon migrierten NX2512-Ordner nicht als alte Quelle eintragen.
   `sidt_2406` und `sidt_2512` zeigen auf die zugehoerigen statisch extrahierten
   SIDT-Wurzeln mit `applications/nx/after_install.bat`,
   `applications/nx/ugii` und `applications/nx/nxplot`.
   Die Eingabedatei darf nur die vier dokumentierten Felder enthalten (max.16KiB).
   Lokale `upgrade-inputs.*.json` werden ausser dem bereinigten Beispiel ignoriert.
2. Nur konkrete lokale C:-Ordner verwenden, keine Laufwerks-/Systemwurzel,
   W:/UNC, Links/Junctions oder ueberlappenden alten/SIDT-Quellen. Der Starter
   setzt die ausgewaehlten Pfade nur fuer seinen Compose-Abfrageprozess;
   globale Benutzerumgebung und Containerumgebung bekommen diese Werte nicht.
   Geerbte `NX_UPGRADE_*`-Werte ersetzen nicht die Eingabedatei. Quellen werden
   niemals durch Bind-Mounts oder diesen Starter automatisch angelegt.
3. In PowerShell im Projektordner zuerst das Profil pruefen, danach starten:

   ```powershell
   .\Start-NX-Docker.ps1 -Profile Upgrade -UpgradeInputsFile .\docker\upgrade-inputs.local.json -Action Config
   .\Start-NX-Docker.ps1 -Profile Upgrade -UpgradeInputsFile .\docker\upgrade-inputs.local.json -Action Start
   # Fuer Status und Stop dieselbe bewusste Profilwahl verwenden:
   .\Start-NX-Docker.ps1 -Profile Upgrade -UpgradeInputsFile .\docker\upgrade-inputs.local.json -Action Status
   .\Start-NX-Docker.ps1 -Profile Upgrade -UpgradeInputsFile .\docker\upgrade-inputs.local.json -Action Stop
   ```

   `Config` prueft Eingabevertrag und aufgeloestes Mount-/Laufzeitprofil, nicht
   die Existenz oder fachliche Vollstaendigkeit der Lesedaten. `Start` verlangt
   vorhandene Ordner, die minimale alte NX-Netzwerkstruktur, `NXBIN/ugraf.exe`
   beider Installationen und die genannten SIDT-Dateien/Overlayordner. Alle
   Lese-/vorhandenen Ausgabebaeume werden auf Links/Junctions geprueft.
   Dies ist keine NX-Ausfuehrungs-/Lizenz-/CAM-Abnahme. Die vollstaendige
   Baseline-/after_install-Inhaltspruefung folgt im Upgrade-Wizard.

| Im Container | Hostquelle / Zweck | Zugriff |
| --- | --- | --- |
| `/inputs/nx2406-local` | ausdruecklich gewaehlte alte NX2406-Testkopie | nur lesend |
| `/standards/2406`, `/standards/2512` | `C:\Siemens\NX2406`, `C:\Siemens\NX2512` | nur lesend |
| `/sidts/2406`, `/sidts/2512` | die beiden ausgewaehlten SIDT-Stagingordner | nur lesend |
| `/data/r` | neue Ordner unter `C:\NX_Migration\DockerReleases` | schreibbar |
| `/data` | bestehendes lokales Verwaltungsvolume | schreibbar |

Die zusaetzliche Settingsdatei ist ebenfalls nur lesend eingebunden.
Es gibt keinen pauschalen Host-/SMB-/Docker-Socket-Mount. Im GUI den Ablauf
"Vollstaendiger Versionswechsel", Quelle `nx2406-local`, die konkrete Quelle
`/inputs/nx2406-local` und einen **neuen** Zielunterordner von `/data/r` waehlen.
Danach Voraussetzungen und Vorschau pruefen; erst dann den neuen Datenstand
erstellen. NX auf dem Test-PC verwendet dessen Windows-`host_folder`.
Installer/after_install werden im Container nicht ausgefuehrt. NX/Lizenz und
Tool Assistant auf einem Windows-Client bleiben eigenstaendige Voraussetzungen.

Beide Betriebsprofile verwenden denselben Projektnamen, Port und dasselbe
Verwaltungsvolume. Sie sind Alternativen, keine parallel zu startenden Dienste.
Vor einem Profilwechsel laufende/ungeklaerte Auftraege abschliessen bzw. ihre
Belege klaeren und den vorhandenen Dienst kontrolliert stoppen. Ein Profilwechsel
ist weder ein Datenbankumzug noch eine produktive SMB-Umschaltung. Vorhandene
Verwaltungsdaten/Releaseordner nicht loeschen oder mit der nativen Pilot-DB ersetzen.
Das neue Profil mountet keine alten Testprotokolle; bei Bedarf ueber die vorhandene
GUI gezielt hochladen. Ein realer Compose-/Engine-/Mount-/Persistenznachweis bleibt
offen, solange die Werkzeuge nicht verfuegbar sind. Die synthetischen Tests
verwenden nur eigene Tempordner und fuehren keine Siemens-Dateien aus.

Bedienung des bisherigen vorbereiteten Profils in PowerShell im Projektordner:

```powershell
.\Start-NX-Docker.ps1 -Action Status
.\Start-NX-Docker.ps1 -Action Config
.\Start-NX-Docker.ps1 -Action Stop
# Nach Codeaenderungen wieder aufbauen/starten:
.\Start-NX-Docker.ps1 -Action Start
# Logs, ohne Schluessel auszugeben:
docker compose -p nx-management -f compose.yaml -f compose.windows.yaml logs --tail 100 nxserver
```

`Stop` stoppt nur den Dienst `nxserver`, ohne `down`, Volume- oder Netzloeschung.
`Status` und `Config` sowie der Stop-Gate legen keine Hostordner/Secrets an und
benoetigen keine aktuell verfuegbaren NX-Lesequellen. Geaenderte Profile koennen
auch diese Aktionen sperren; erst das erwartete Profil wiederherstellen.
Auch diese Aktionen verwenden nach der Policypruefung eine eigene kurzlebige
Plandatei, nicht die Original-YAML-Dateien. Die CLI-Ersatztests pruefen den
gleichzeitigen Lesezugriff, abgewiesene Schreib-/Loeschversuche, UTF-8 und
literale Dollarzeichen sowie Aufraeumen nach Erfolg und fehlgeschlagenen
Compose-Aktionen. Der Starttest verschiebt nur in seiner Skriptkopie die festen
Hostkonstanten in eigene leere Temp-Fixtures; echte C:-Quellen, Docker und NX
werden dabei nicht verwendet.
Die PowerShell-Policytests und der harmlose CLI-Ersatz testen diesen Ablauf,
ersetzen aber keinen echten Engine-/Containerlauf. Docker ist auf diesem PC
weiterhin nicht verfuegbar; es wurde nicht automatisch installiert.

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
Vor dem ersten `up` wird zusaetzlich die aufgeloeste Compose-Konfiguration
gegen ein reines Testprofil geprueft: Volume-/Netznamen muessen genau zum
zufaelligen Projekt gehoeren, Image/Nonroot/Readonly/Port/Secret/Einbindungen
duerfen nicht auf andere Dienste oder Daten zeigen. Quellbindungen erzeugen
keine fehlenden Hostordner automatisch. Der zugelassene aufgeloeste Plan wird
als neues `verified-compose.json` nur im temporaeren Testordner festgeschrieben;
Start, Neustart und testinternes Aufraeumen verwenden ausschliesslich diesen
Plan, nicht spaeter veraenderte Original-YAML-/Override-Dateien. Bei abgelehntem
Profil findet weder Start noch `down --volumes` statt. Dieses testinterne
Aufraeumen ist keine Anleitung fuer den normalen Verwaltungsbestand.
Vor dem Start wird ausserdem schreibfrei geprueft, dass Volume, Netz, Container
und Image unter dem zufaelligen Testnamen noch nicht existieren. Bei einem
Namenskonflikt wird nichts uebernommen und auch kein Cleanup ausgefuehrt.
Die Pure-/CLI-Ersatztests pruefen diesen Gate ohne Engine oder echte Loeschung;
erst der ausdrueckliche Integrationstest liefert einen Container-Nachweis.

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
  Bestaetigt am 15.10.2026: Ein ERP-Artikel kann mehrere NX/CAM-Varianten
  haben, etwa unterschiedliche Werkzeugaufbauten oder Bibliothekszuordnungen.
  Die Varianten behalten eigene technische Revisionen und Freigaben;
  gemeinsame ERP-Felder werden nicht zur automatischen Zusammenfuehrung genutzt.
  Optionales Datenmodell/API mit ausdruecklicher DB-gesicherter Bestandsmigration
  sowie gefuehrte GUI-Umschaltung/Wiederanlauf sind vorhanden. Reale ERP-Feldpolitik,
  Liveanbindung und gemeinsamer betrieblicher Abnahmetest bleiben offen.
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
Betriebsvorbereitung. Der Server kann vorbereitete Ordner importieren und den
NX2406-zu-NX2512-Dreiwege-Build inklusive statischer Windows-EXE-Extraktion ueber
den GUI-Wizard steuern. Weitere Versionsadapter, produktives SMB, Client,
Profil-Durchsetzung, ERP und KI-Gateway
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

Das vom Benutzer ausgewaehlte zentrale Repository ist seit dem 09.10.2026
[juanito1337/Server_NX](https://github.com/juanito1337/Server_NX).
Das lokale Remote `origin` verwendet
`https://github.com/juanito1337/Server_NX.git`; der Hauptbranch ist `main`.
Der Remote-Bestand wurde vor dem ersten Push geprueft und war leer. Kein
bestehender Remote-Commit wird ueberschrieben und es wird kein Force-Push benutzt.
Fuer HTTPS-Anmeldungen steht unter Windows der installierte Git Credential
Manager zur Verfuegung. Zugangsschluessel gehoeren weder in die Remote-URL
noch in das Repository oder den Chat.

```bash
git remote -v
git status --short --branch
git push
# Bestehende Aenderungen nicht durch einen ungeprueften Merge ueberlagern:
git pull --ff-only
```

Weitere Arbeiten koennen ueber `feature/...`-Branches mit geprueften Merges
nach `main` erfolgen. Ein Push aktualisiert ausschliesslich den Quellcode;
er startet keinen Server und aktiviert keinen NX-Datenstand. Der aktuelle
Stand ist ein Pilot, keine Produktivfreigabe.

### Gesamtes Aenderungsprotokoll lesen

Unter „Aenderungsprotokoll“ wird die gespeicherte Historie mit Zeitpunkt,
Akteur und Aktion in Seiten zu 20 Eintraegen angezeigt. Auch Eintraege vor den
letzten 200 sind erreichbar. Die Suche prueft Teiltexte in diesen Metadaten
und der Eintragsnummer, unter Beachtung der Gross-/Kleinschreibung; CAM-/ERP-
Nutzdaten werden weder fuer die Suche geladen noch automatisch angezeigt.
Nach einer Sucheingabe „Uebersicht neu laden“ waehlen. Aendert sich der
Auditkopf zwischen Seiten oder vor einem Detailabruf, ebenfalls neu laden.

„Eintrag lesen“ zeigt die unveraenderten gespeicherten Nutzdaten bis 8 MiB.
Der Server und Browser pruefen den ausgewaehlten Einzelhash. Dies ist keine
vollstaendige Ketten-/Dateipruefung, Signatur oder Echtheitsgarantie; dafuer
stehen die separate Integritaetspruefung und extern aufbewahrte Vergleichsanker
zur Verfuegung. Diese Ansicht schreibt keine Auditzeile und aendert keine
Stammdaten oder NX-Dateien. Reiterwechsel/Schliessen/Abmeldung verwerfen die
Ansicht und ausstehende Antworten, nicht die gespeicherte Historie.

Die bisherige Schnittstelle `GET /api/v1/audit` mit den letzten 200 Eintraegen
bleibt unveraendert und ist ausdruecklich als JSON-Rohansicht bezeichnet.
Neue Admin-Lesewege: `GET /api/v1/audit-summary` und
`GET /api/v1/audit-entries/{sequence}`. Aeltere Seiten und Details benoetigen
den gemeinsam gelesenen Auditkopf; kein Schreib- oder ERP-Livezugriff.

### Dateikonflikte beim Versionswechsel entscheiden

Die bestaetigte Upgrade-Vorschau zeigt Dateikonflikte mit den drei
SHA-256-Fingerprints von Altstandard, Kundenanpassung und Zielstandard.
„Nicht vorhanden“ ist ein fehlender entsprechender Standard, keine leere
Datei. Die Ansicht unterscheidet beidseitige Aenderungen, einen entfallenen
Zielstandard und eine neu hinzugekommene Siemens-Datei am selben Zielpfad.
Dies ist ein Vergleich der Vorschau-Metadaten, kein Text-/Geometrievergleich
und kein Nachweis, dass eine alte Datei unter NX2512 verwendbar ist.

Pfad-/Grundsuche, Konfliktartfilter und Seiten zu 50 Eintraegen arbeiten
lokal und starten keinen Auftrag. Ausdrueckliche standard/customer-Wahlen
bleiben beim Blaettern erhalten. „Kundenanpassung“ uebernimmt deren Bytes,
„Siemensstand“ waehlt die Zieldatei; fehlt sie im Zielstandard, wird die alte
Anpassung im neuen Ordner weggelassen. Originalquellen bleiben erhalten.
Die Auswahl schreibt nur ins Formular. Anschliessend Vorschau erneut
starten und erst danach den neuen Ordner ausdruecklich erstellen.

Ein Quell-/Zielwechsel oder eine neue Vorschau entwertet alte Steuerelemente.
Ungueltige erweiterte JSON-Dateientscheidungen werden nicht still ersetzt;
die Auswahlfelder sind bis zur ausdruecklichen Korrektur schreibgeschuetzt.
Bibliotheksobjektkonflikte stehen weiterhin in ihrer eigenen Liste darunter.

Je logischem Dateipfad ist genau eine Entscheidung erlaubt. Zwei Angaben mit
abweichender Gross-/Kleinschreibung werden auch bei gleichem Wert abgewiesen;
es gilt nicht der letzte JSON-Schluessel. Der Server prueft dies ohne
Quellzugriff vor Annahme eines neuen Vorschau-/Buildauftrags. Bereits
gespeicherte Annahmebelege bleiben unveraendert und starten keinen Worker neu.
Eine einzelne abweichende Schreibweise bleibt zulaessig: Das Auswahlfeld zeigt
deren Wert an und aendert/entfernt den vorhandenen Schluessel, statt unbemerkt
einen zweiten anzulegen. Ungueltiger Rohtext bleibt erhalten. Die lokale
Browserpruefung dient nur der Bedienung; die vollstaendige Unicode-casefold-
Identitaet prueft weiterhin der Server. Nach einer API-Ablehnung kann der
Benutzer korrigieren und erneut ausdruecklich starten, ohne haengenden Versuch.

### Datenumfang und Schemastand (16.10.2026)

Die Anwendung kennt 23 Stammdatentypen. 20 davon verwenden zusammen 41 native
NX-Bibliotheksadapter; Vorlagen und UI-Standards verwenden versionierte
Dateipakete. Allgemeine Pruefmittel (`gauge`) bleiben dagegen Katalogdaten,
auch bei `approved`. Sie erzeugen keine neue NX-Datenbank oder NX-Datei.
CMM-Taster (`inspection_tool`) haben eigene native Bibliotheken. Maschinen
und Spannmittel koennen ihre nativen Daten mit geprueften Dateipaketen ergaenzen;
Metadaten allein erzeugen kein funktionsfaehiges Maschinen-/Simulationsmodell.

Lesender Abgleich der installierten NX2512-Standard-DAT-Dateien: alle 41
Bibliotheken mit 241 FORMAT-Definitionen. Vorhandene Objektgruppen decken 152
Formate ab; 89 Definitionen haben keine Beispielzeilen. Ein zusaetzlicher
isolierter GUI-Prueffall hat aus allen 241 Definitionen je einen Entwurf
angelegt: nur echte Spaltennamen/Klassen, synthetische Kennungen, andere Werte
leer, keine Freigabe oder Veroeffentlichung. Auch Formate ohne Beispiele lassen
sich damit im Editor anlegen. Dies beweist keine fachlich gueltigen Schnittwerte,
Werkzeugmasse, Koerperrollen oder NX-Laufzeitkompatibilitaet leerer Datensaetze.
CAD-Modelle und Siemens-Programme wurden nicht geoeffnet/ausgefuehrt; Original-
DAT-Dateien blieben bytegleich. Die reale CAM-/STEP-Abnahme bleibt gesondert.

### NX-Oberflaechenprofile als Dateipakete

UI-Standards koennen neben Rollen unter `UGII/menus/roles/` auch zugehoerige
Profil-Dateien unter `UGII/menus/profiles/` verwalten. Fuer diesen Profilbaum
sind zusaetzlich `.ddb`, `.rtb`, `.grb`, `.gly`, `.csb`, `.abr`, `.stb`, `.dtx`
und `.crb` zugelassen. Diese neun Typen sind nicht fuer Rollen-, Vorlagen-,
Maschinen- oder Spannmittelverzeichnisse freigeschaltet. Die Uploadauswahl,
Bestandsanzeige und serverseitige Paketpruefung beruecksichtigen dieselben Typen.

Die Dateiinhalte bleiben unveraendert; keine automatische Interpretation,
Umbenennung oder Konvertierung. Technische Pruefung und Hashbindung bestaetigen
Bytes/Pfade, nicht fachliche Gueltigkeit, Abhaengigkeiten oder NX-Kompatibilitaet.
Vier ausdrueckliche Paketpruefungen samt Nachweis bleiben fuer `approved`
erforderlich. Vorhandene Dateien duerfen nur nach Vergleich mit der passenden,
vorhandenen Siemens-Baseline als Kundenanpassung uebernommen werden. Unveraenderte
Standarddateien erhalten keinen Kundenbesitz. Uebernahme erzeugt nur einen Entwurf.

Es gelten weiterhin maximal 100 Eintraege pro Paket, 20 MiB pro Anhang und
128 MiB je technischer Paketpruefung. Groessere Profilbestaende in mehrere
bewusst zusammengestellte Pakete aufteilen. Ein gepruefter Export erzeugt einen
neuen NX-Ordner, ohne Quelle/historische Datenstaende zu veraendern. Stilllegung
entfernt nur die verwalteten Dateien aus einem neu abgeleiteten Datenstand.
Ausfuehrbare Uploadtypen bleiben gesperrt; der Server fuehrt Profile nicht aus.

Fuer jeden relativen Dateipaketpfad gilt die bereits bestehende Grenze von
1 bis 240 Zeichen. Bestandsanzeige, Uebernahme und technische Vorpruefung wenden
dieselbe Grenze an: Zu lange Bestandsdateien bleiben sichtbar, sind aber mit
Begruendung nicht auswaehlbar. Auch direkte neue Uebernahmeauftraege werden vor
Anhangskopie bzw. Auftragsannahme abgewiesen. Alte Annahmebelege bleiben als
historische Belege lesbar; Wiederholung startet keinen neuen Worker.
Keine automatische Pfadkuerzung oder Umbenennung. Die Grenze bezieht sich auf
Paketpfade, nicht auf alle Dateien eines vollstaendigen Versionsupdates.
Ein gueltiger relativer Pfad beweist noch nicht, dass der gesamte absolute
Pfad auf jedem Windows-System verfuegbar ist; Dateisystem-/Betriebssystemgrenzen
und reale NX-Kompatibilitaet werden damit nicht aufgehoben.

Diese Dateiverwaltung ist noch keine erzwungene einheitliche NX-Oberflaeche auf
allen Arbeitsplaetzen. Clientverteilung, Profil-/Rollenauswahl und reale
NX-Laufzeitpruefung sind separat zu konfigurieren bzw. abzunehmen. Eine MTX-Rolle
wird im Teststarter weiterhin nur ueber dessen eigene qualifizierte Rollenbindung
gewaehlt; beliebige Profil-Dateien ersetzen diese Bindung nicht.

### Installationsversion im Upgrade-Wizard pruefen

"Voraussetzungen pruefen" liest jetzt die PE-Versionsressource von
`NXBIN/ugraf.exe` in beiden konfigurierten Siemens-Installationsordnern.
Ein vorhandener Dateiname allein reicht nicht: NX2406- und NX2512-Metadaten
muessen zur jeweiligen Zuordnung passen. Die Anzeige nennt die tatsaechlichen
Versionskennungen einschliesslich Buildstand. Fehlende, widerspruechliche oder
unlesbare Metadaten sperren neue Upgradeauftraege bereits vor ihrer Annahme.

Der Leser arbeitet plattformunabhaengig ohne NX-Start und ohne Siemens-DLLs.
Er benoetigt die vollstaendigen, lokal zugaenglichen Installationsreferenzen
beider Versionen und die statisch extrahierten SIDT-Daten. Das gilt auch fuer
spaeter explizit lokal eingebundene, schreibgeschuetzte Container-Eingaben;
diese Implementierung ist kein Nachweis eines tatsaechlich betriebenen Containers.
Die zu pruefende EXE wird nur als begrenzte Binaerdatei gelesen (maximal16MiB),
`after_install.bat` nur als begrenzte Textdatei (maximal1MiB), niemals ausgefuehrt.

Dateihashes fuer Versionsmetadaten und SIDT-Kopierprofil sind an den Upgradeplan
gebunden. Aenderungen waehrend der Planung oder zwischen Vorschau und Build
erfordern eine neue Vorschau. Alte Berichte bleiben lesbar, berechtigen ohne
diesen Versionsnachweis aber nicht zu einem neuen Build. Historische
Annahmebelege werden nicht als neuer Auftrag wiederholt.

Dies ist ausschliesslich eine statische Metadatenpruefung, keine digitale
Signatur-/Echtheitspruefung, Installationsvollstaendigkeits-, Lizenz- oder
NX-Laufzeitpruefung. Den neuen Ordner anschliessend weiterhin mit NX2512 auf
einem geeigneten Test-PC fachlich pruefen; keine automatische Freigabe.

Formatreferenzen: Microsoft beschreibt den
[PE-Ressourcenbaum](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format#the-rsrc-section),
[VS_VERSIONINFO](https://learn.microsoft.com/en-us/windows/win32/menurc/vs-versioninfo)
und die [Version-Strings](https://learn.microsoft.com/en-us/windows/win32/menurc/string-str).
Der konkrete Implementierungs-/Teststand steht in `ABNAHMESTAND.txt`, nicht in
einer davon abgeleiteten Behauptung der Hersteller-/Laufzeitfreigabe.

### Versionspruefung auf dem NX-Test-PC

Neue Teststarter pruefen jetzt auch die tatsaechlichen lokalen Versionsdaten
von `NXBIN/ugraf.exe`. Ein Ordner namens `NX2512` allein reicht nicht.
Die Windows-Dateiversionsmetadaten muessen NX2512 entsprechen; fehlende oder
widerspruechliche Angaben sperren die Vorbereitung vor dem Anlegen der
Testumgebung. Auch der direkte Projekt-Starter und erneute Starts einer
vorbereiteten Testumgebung pruefen die Installation.

Der Starter liest nur Dateimetadaten, fuehrt dazu keine NX-/Siemens-DLL aus
und verlangt auf dem Test-PC kein Python. Die Begrenzung fuer die zu lesende
Versionsdatei liegt bei16MiB. Waehrend des Lesens wird ein eigener Read-only-
Dateihandle ohne Schreib-/Loeschfreigabe gehalten. Vor und nach der uebrigen
Vorpruefung werden Dateihash und Versionsdaten verglichen; ein Wechsel auch
auf einen anderen NX2512-Build in diesem Zeitraum erfordert eine neue Pruefung.
Das ist keine vollstaendige Dateisystem-/DLL-Sandbox oder Echtheitspruefung.

Die Konsole nennt den gelesenen Buildstand und grenzt den Metadatencheck von
Signatur-, Lizenz- und fachlicher NX-Laufzeitfreigabe ab. `-CheckOnly` startet
NX nicht. Bei gueltigen Eingaben kann die erste Vorbereitung wie bisher ihre
separaten Testkonfigurationen erstellen; Laufzeitverzeichnisse entstehen erst
bei einem ausdruecklichen Start. Bereits fehlerhafte Versionsdaten erzeugen
keine neue Testumgebung.

Fuer vorhandene Serverpakete: In der GUI den Datenstand waehlen,
"Test-Starter aktualisieren", danach erneut herunterladen und das ganze ZIP
in einen neuen lokalen Paketordner entpacken. Alte ZIPs bleiben unveraendert;
keine Einzelskripte oder Pruefsummen darin manuell austauschen. Die neuen
Vorlagen werden vom Server bei der Paketerzeugung gelesen, deshalb ist fuer
diese Aenderung kein Serverneustart und kein neuer Sitzungsschluessel noetig.
Eine bisherige Testumgebung darf nicht ohne ihre bestehende Release-/
Pfad-/Konfigurationsbindung ueberschrieben werden.

Microsoft dokumentiert den verwendeten statischen Zugriff in
[FileVersionInfo.GetVersionInfo](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.fileversioninfo.getversioninfo?view=netframework-4.8.1).
Die Angabe eines passenden Herstellernamens im Ressourcenblock ist keine
digitale Signatur und beweist fuer sich allein nicht die Herkunft einer EXE.

### SIDT-Version passend zur Installationsreferenz

Der Upgrade-Wizard prueft neben der NX-EXE-Version jetzt die ausdrueckliche
SIDT-Deklaration in `start_apps/env/nx_inst_dir.cmd`. Das Kopierlayout aus
`after_install.bat` allein identifiziert die Version nicht. Die Anzeige nennt
fuer beide Seiten getrennt den installierten NX-Build und die SIDT-Deklaration.
Vertauschte Ordner, fehlende Deklarationen und widerspruechliche Angaben
sperren neue Vorschauen bereits vor ihrer Auftragsannahme. Auch eine neue
statische Extraktion muss zur angeforderten Version passen.

Der Server liest diese CMD-Datei nur als begrenzte Textdatei (maximal64KiB).
Er akzeptiert genau eine unterstuetzte literale Zuweisung von `NX_INST_DIR`,
wertet keine Shell-/Variablenausdruecke aus und fuehrt weder CMD noch Setup aus.
Aeltere Versionsnummern in Kommentaren oder fremden Verknuepfungsskripten
werden nicht als Versionsnachweis verwendet. Das ist eine Deklarations-/
Konsistenzpruefung, keine Signatur-, Herkunfts-, Lizenz- oder Laufzeitfreigabe.

Bei einer Sperre den vollstaendigen passenden Originalextrakt waehlen;
die Deklarationsdatei nicht bearbeiten, um eine Pruefung zu umgehen.
Bereits korrekt konfigurierte, vollstaendige SIDT-Ordner muessen nicht erneut
extrahiert werden. Die Datei wird zusammen mit den anderen Eingaben per SHA-256
an den Plan gebunden. Aenderungen nach der Vorschau erfordern eine neue Vorschau.
Alte Berichte bleiben lesbar, ohne diesen Nachweis aber keine neue Buildannahme.
Die Deklarationsdatei ist nur Eingabemetadatum und wird nicht zusaetzlich in
den neuen NX-Netzwerkordner kopiert; dessen Standardaufbau bleibt erhalten.
