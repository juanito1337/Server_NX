# Testpipeline und lokale NX-Qualifikation

W:\ ist Produktionsumgebung und für sämtliche Tests tabu: kein Lesen, Schreiben,
Auflisten, Auflösen oder Testzugriff. Alle Beispiele verwenden neue lokale Ordner.
Ein erfolgreicher Softwaretest ist keine CAM- oder Produktivfreigabe.

## Befehle

Die beiden reinen PowerShell-Dockerprofilprüfer haben ein begrenztes Zeitbudget
von 60 Sekunden je Prozess, einschließlich Windows-Start und Modulimport. Die
zuvor verwendeten 20 Sekunden wurden auf GitHub-Windows-Runnern überschritten.
Alle funktionalen Prüfungen bleiben erhalten; dies ist kein Performanznachweis
und ändert das getrennte 15-Minuten-Budget der echten NX-Fälle nicht.

Aus `server/` mit der separaten Python-Umgebung des Repositorys:

```powershell
..\.venv\Scripts\python.exe tools/run_ci_tests.py --suite core
..\.venv\Scripts\python.exe tools/run_ci_tests.py --suite browser
..\.venv\Scripts\python.exe tools/run_local_pipeline.py --stage server
..\.venv\Scripts\python.exe tools/run_local_pipeline.py --stage schemas
..\.venv\Scripts\python.exe tools/run_local_pipeline.py --stage fixture-layout
..\.venv\Scripts\python.exe tools/run_local_pipeline.py --stage prepare
..\.venv\Scripts\python.exe tools/start_development_server.py
```

Die vollständige Core-Suite benötigt Node.js 24 und Git. Liegen diese Laufzeiten
nicht im lokalen PATH, können `--node-executable C:/lokaler-pfad/node.exe` und
`--git-executable "C:/Program Files/Git/cmd/git.exe"` angegeben werden. Der Starter
prüft beide Versionen, protokolliert sie und ergänzt nur seinen eigenen Prozess-PATH.
Eine fehlende oder unpassende Laufzeit erzeugt einen Infrastrukturfehler mit
JUnit-/JSON-Bericht, statt JavaScript- oder Repositorytests grün zu überspringen.

Unter Windows prüft der Starter vor Core- und Browserläufen zusätzlich Windows
PowerShell 5.1 und eine kleine C#-Kompilierung. Ein eigener Modulcache gehört zum
jeweiligen Lauf; fremde Modulpfade und geerbte Cache-Dateien werden nicht übernommen.
Version, Startdauer und gehashter Prüfbeleg stehen im Laufzeitnachweis. Auch dieser
Prozess und seine Kinder gehören zu einem eigenen Windows Job Object. Fehlender
Compiler, Zeitüberschreitung oder ein verbliebener Kindprozess sind Infrastrukturfehler.
Die Cache-Datei wird nicht als GitHub-Artefakt hochgeladen; Skript und Prüfprotokoll
bleiben als gebundene Nachweise erhalten. Dieser Vorlauf startet NX nicht.

`server` startet eigene HTTP-Server auf localhost, prüft Entwurf/Anhang/NX2406-Paket,
Ergebnis-ZIP, Übernahme als zwei verbundene Entwürfe, Ausschluss dieser Entwürfe
beim Export, Neustart und Backup/Wiederherstellung in eine zweite Instanz.
Ressourcen, Revisionen, Historien, Anhänge, Ergebnisbindung und Exportdateien
werden nach beiden Neustarts verglichen. STEP- und PRT-Textproben sind ausdrücklich
keine CAD-Modelle; die sechs fachlichen Prüfungen bleiben zurückgesetzt.
Testserver werden anschließend beendet.
Eine dauerhaft betriebene Entwicklungsinstanz gehört nicht zu diesem Lauf.
Der letzte Befehl startet die getrennte Entwicklungsinstanz auf localhost:8765.
Der Sitzungsschlüssel wird nur in ihrer interaktiven Konsole gezeigt. Eingaben
und Exporte bleiben im eigenen Entwicklungsordner; keine Beispielkonfiguration
einer anderen Maschine wird übernommen.

`schemas` liest nur die 41 ausdrücklich bekannten Bibliothekspfade unter
`C:/Siemens/NX2406`, prüft FORMAT-Definitionen und stabile Dateihashes. Keine
ursprünglichen DATA-Zeilen gelangen in den Bericht. Fehlende Bibliotheken bleiben
als Fehler sichtbar. Der Lauf startet NX nicht und verändert die Installation nicht.

`fixture-layout` prüft zusätzlich die zwei ausdrücklich bekannten lokalen
Chuck-/Vise-KRX-Beispiele und ihre deklarierten Hauptmodell-/Bilddateien. Dateien
werden nur gelesen und gehasht. PRT-Komponenten, Einheiten und Geometrie werden
dadurch nicht qualifiziert; die zwei fehlenden fixture_automation-DATs bleiben
als eigener Befund der Schema-Prüfung sichtbar.

## NX2406-Spannmittelpakete

Für NX2406 unterstützt die Verwaltung zusätzlich den beobachteten nativen Baum
`MACH/resource/library/fixture/<Kategorie>/FixtureConf.krx` mit einem expliziten
`RootItemCollection`-Inhalt. `Folder`, `/graphics/...prt` und `Icon` werden ausschließlich
innerhalb dieses lokalen Paketlayouts aufgelöst. Hauptmodell und Bild müssen zum
Paket gehören. W:-/UNC-Pfade, Traversierung, Variablen und unbekannte XML-Erweiterungen
werden abgewiesen. Keine Datei oder binäre PRT-Referenz wird automatisch umbenannt.

Ein neues KRX-Spannmittel darf ohne erfundenen DAT-Datensatz als Entwurf angelegt
oder aus einer geprüften Kundenkopie übernommen werden. In der HMI ist der zusätzliche
Paketpfad nur bei NX2406 sichtbar. Ein Versionswechsel verwirft die Hashbindung und
die vier manuellen Bestätigungen. Bereits als Siemens-/SIDT-Standard erkannte
unveränderte Dateien werden nicht als Kundenbesitz übernommen.

Exportplanung prüft deklarierte KRX-Dateiverweise gegen den geplanten neuen Bestand.
Das Stilllegen eigener Modelle darf kein beibehaltenes KRX ins Leere zeigen lassen.
Unmittelbar vor dem SQL-Commit werden Beschreibungen und ihre Ziele nochmals anhand
von Größe und SHA geprüft; Binärdateien werden dabei gestreamt. Historische Stände
bleiben erhalten. Descriptoren sind auf 1 MiB, die Sammlung auf 1.000 Descriptoren,
128 MiB und 10.000 deklarierte Verweise begrenzt; Teilprüfungen geben keinen Export frei.
Auch die technische Releasevalidierung und die anschließende Freigabe sperren
fehlende KRX-Verweise. Ein erfolgreicher statischer Verweischeck bestätigt keine NX-Ausführung.

Ein getesteter synthetischer 2406→2512-Updateablauf erhält Dateiinhalt und Besitz,
übernimmt aber keine NX-Qualifikation. Der Legacy-Pfad wird nicht still als
NX2512-Dateipaket freigegeben. Eine tatsächliche Registrierung, vollständige PRT-
Baugruppenauflösung und Erprobung in NX bleiben fachlich zu bestätigen.

`prepare` bindet ein Profil an die SID des Entwicklungskontos. Die Laufordner
enthalten `summary.json`, `junit.xml` und die Quellenbindung einschließlich
uncommitteter Änderungen. Daten und Modelle bleiben git-ignoriert.

## Echte NX-Ausführung

Vorbedingung: eigenes Windows-Testkonto mit administrativ bestätigter Sperre des
Dateizugriffs auf die Produktivfreigabe, auch über alternative UNC-Adressen.
Ein fehlendes W:-Mapping ist kein Nachweis. Kein Passwort im Repository speichern.
Die Vorlage `server/tools/nxlab/isolation.example.json` ist absichtlich nicht
lauffähig. Eine Kopie als lokale Policy ausfüllen; Testkonto und Entwicklungskonto
müssen unterschiedliche SIDs besitzen. Die Policy ist eine administrative
Bestätigung, keine technisch aus W:-Zugriffen abgeleitete Messung.

Unter dem isolierten Testkonto:

```powershell
..\.venv\Scripts\python.exe tools/run_local_pipeline.py --stage nx --profile C:/lokaler-pfad/profile.json --isolation-policy C:/lokaler-pfad/isolation.json
```

Qualifikationsziel ist NX 2406.5001.0.0. Abweichende Builds bleiben gesperrt.
Die Laufzeit benötigt Modeling-/STEP-Lizenzen; ein Lizenzserver darf ausschließlich
über `port@host` konfiguriert sein. Produktive NX-/Python-/Bibliothekssuchpfade
werden nicht übernommen. NX- und Server-Python sind getrennt.

Die Testjournale erzeugen Fräser, Bohrer und Drehwerkzeug als vereinfachte
Referenzbaugruppen, exportieren STEP und messen nach einem frischen Start. Danach
werden Serverauftrag, reales Importjournal, Diagnoserückgabe und erneutes Öffnen
geprüft. Die unabhängig gespeicherten Sollwerte verwenden 0,01 mm, 0,1 Grad und
0,1 Prozent Volumentoleranz. PRT-Hashes binden Artefakte, ersetzen aber keine
geometrischen Messungen. Konstruktionselemente allein beweisen keine korrekte
Schneidrichtung. Nicht übertragene Montage-/Richtungsmarker bleiben ungemessen.
Für jeden Körper sind Volumen und räumliche Grenzen zusätzlich gemeinsam an
explizite Sollwerte gebunden, unabhängig von STEP-Körpernamen und Reihenfolge.
Dadurch bleibt eine verschobene Schneide sichtbar, auch wenn Gesamtvolumen und
äußere Gesamtabmessungen gleich bleiben. Eine physische Gegenprobe verschiebt
die Fräserschneide um 2 mm innerhalb der unveränderten äußeren Halterabmessungen.

Jeder automatische Werkzeug- und Gegenfall besitzt ein gemeinsames Ausführungsbudget
von 15 Minuten. Ein separater Server-Python-Worker umfasst alle Modell-/Importstarts,
HTTP-Anfragen und Mess-/Dateiprüfungen des Falls; einzelne Phasen erhalten dadurch
kein neues 15-Minuten-Budget. Sein äußeres Windows Job Object schließt auch die
verschachtelten NX-Jobs und den Testserver ein. Nach Ablauf werden nur diese eigenen
Prozesse beendet; die Beendigung wird vor dem Fehlerbericht überprüft. Diese notwendige
Aufräumprüfung kann über die Ausführungsfrist hinaus dauern. Eine CAM-Übergabe wird
erst vom Controller nach bestätigtem Prozessende innerhalb des Budgets veröffentlicht.
Unvollständige, abgebrochene oder verspätete Worker-Belege geben keine Übergabe frei.
Aufgaben- und Ergebnisbytes sind per SHA-256 an Fall, Quellen und Isolationspolicy
gebunden. Die spätere begleitete CAM-Abnahme hat kein automatisches Zeitlimit.

Jeder NX-Start verwendet außerdem ein eigenes Windows Job Object: vor Ausführung zugeordnet,
Kindprozesse eingeschlossen, keine Prozessnamensuche. Bei Timeout wird nur dieser
Job beendet und dessen Ende geprüft. Die Benutzer-NX-Sitzung wird nicht beendet.

Vor einem erzwungenen Ende werden die Mitglieder ausschließlich aus dem eigenen Job
ermittelt und ihre Handles durch `IsProcessInJob` geprüft. Nach dem Beenden wartet der
Starter auf diese konkreten Prozesshandles. Eine leere Job-Zählung allein bestätigt
keine abgeschlossene Freigabe von Dateihandles. Ändert sich die Mitgliedschaft während
dieser Prüfung, bleibt der Abschluss ungeklärt und der Lauf schlägt fehl. Es werden
keine systemweiten Prozesslisten oder fremden NX-Sitzungen verwaltet.
Die Wartebedingung folgt den dokumentierten Verträgen von
[TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)
und [IsProcessInJob](https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob).

Die NX-Stufe erzeugt außerdem einen lokalen Server-Zielstand mit den 14
installierten Werkzeug-/Halter-/Schaft-/Baugruppen-/Bezugspunktbibliotheken.
Der vorhandene native Schreiber entfernt ausschließlich DATA-Datensätze;
FORMATs und native Kopfzeilen bleiben erhalten. Der Server importiert diese
leeren Testbibliotheken als NX2406-Zielrelease. Originaldateien und vorhandene
Siemens-Objekte werden nicht verändert oder in das Testziel übernommen.

Jeder positive Fall erhält `cam-handoff.json`: Auftrag, Eingabe, Zielrelease,
Collector-Archiv, aktuelle Quellen sowie die gespeicherten und erneut geöffneten
Modellartefakte sind durch IDs und SHA-256 gebunden. Diese Datei wird erst nach
dem bestätigten Ende des zugehörigen Testservers veröffentlicht. Die numerischen
Messungen und die konkreten Artefakthashes bleiben getrennte Nachweise.

**Aktuell noch kein qualifizierter Echt-NX-Lauf.** Die Journalimplementierung muss
unter dem isolierten Konto gegen die installierten NXOpen-APIs praktisch bestätigt
werden. API-/Messwert-Doubles sind kein Ersatz für diesen Nachweis.

## Fachliche Abnahme und offene Ausbaustufen

Ein automatischer Lauf endet nach erfolgreicher Geometrieprüfung mit
`awaiting_cam`, Exitcode 2 und einem JUnit-Fehler für die noch offene Abnahme.
Kein fehlender Nachweis wird in einen grünen Skip verwandelt.

Anschließend sind pro Werkzeug native NX-CAM-Zuordnung, lokale TEST-Bibliothek,
Collector, Serverübernahme als Entwurf, kontrollierter Export und erneutes Laden
in einer frischen CAM-Sitzung erforderlich. Alle sechs vorhandenen Prüfungen
bleiben ausdrücklich nötig: Einheiten, Orientierung, Montagepunkt, Schneid-,
Nichtschneid- und Kollisionsgeometrie. Kollisions- und Freiraum-Gegenprobe gehören
zur begleiteten Abnahme. Dafür besteht noch kein vollständiger automatisierter
Rundlauf und kein Freigabeverifier; fehlende Schritte bleiben offen.

Die lokale HMI kann anhand der Übergabe wieder aufgenommen werden:

```powershell
..\.venv\Scripts\python.exe tools/resume_cam_review.py --handoff C:/NX_Migration/ToolJobs/lab-0123456789ab/cam-handoff.json --check-only
..\.venv\Scripts\python.exe tools/resume_cam_review.py --handoff C:/NX_Migration/ToolJobs/lab-0123456789ab/cam-handoff.json
```

Den Beispielordner durch den tatsächlich erzeugten Fall ersetzen. Der erste
Befehl prüft Quellen, Modellbytes, gespeicherten Importbericht, Zielmanifest und
Collector-Hilfsdateien. Der zweite hält einen eigenen Server auf einem neu
zugewiesenen localhost-Port für die manuelle HMI-Arbeit offen; URL und Schlüssel
erscheinen nur in der interaktiven Konsole. Ursprüngliche Quellaliase und fremde
Einstellungen werden nicht übernommen. Der Starter beendet ausschließlich diesen
eigenen Server und startet NX nicht. Unveränderte Referenz-/Importmodelle erhalten;
CAM-Aufbereitung und geschlossenen Bibliotheksexport in separaten lokalen Kopien
ausführen. Für NX bleiben das isolierte Testkonto und ausdrücklich lokale
Startpfade Voraussetzung.

Auch ein intakter Review-Kontext endet mit `awaiting_cam`, Exitcode 2 und einem
JUnit-Fehler für die offene fachliche Abnahme. Eine erfolgreiche HMI-Wiederaufnahme
bestätigt weder native CAM-Zuordnung noch Export/Wiederladen in einer frischen
CAM-Sitzung. Geänderte Quellen verlangen einen neuen qualifizierten Geometriefall.

Die numerischen Gegenproben erkennen falsche Einheiten/Achsen/Maße, fehlende Körper
und Komponenten sowie ungültige Messwerte. Physische NX-Gegenmodelle einschließlich
fehlender PRT-Komponente und flacher Baugruppe sind zusätzlich praktisch zu
qualifizieren. Versionsmigration 2406 → 2512 benötigt eine echte 2512-Installation.

## Abdeckung und Entwicklungssteuerung

Sicherungsmetadaten und Archivmanifeste werden als eindeutige JSON-Objekte gelesen.
Doppelte Felder, nichtendliche Zahlen, ungültige Unicode-Werte und falsche
Dokumenttypen sperren den Beleg. Ein defekter Einzelbeleg erscheint in der Liste
als ungültig; andere intakte Sicherungen bleiben auswählbar. Ein Bericht wird aus
denselben begrenzt gelesenen, stabilen und SHA-geprüften Bytes dekodiert. Ein
Dateiaustausch zwischen Hashprüfung und Rückgabe darf keinen Bericht freigeben.
Die Gegenproben liegen in `test_backup_metadata.py` und
`test_backup_metadata_browser.py`. Sie verwenden ausschließlich synthetische
lokale Bestände; unbekannte endliche Zusatzfelder bleiben kompatibel lesbar.

Vor einer Softwareabnahme werden die zwei Core- und vier Browser-Laufordner
zusammen geprüft. Jeder Ordner muss `plan.json`, `summary.json`, `junit.xml` und
`events.jsonl` enthalten. Beispiel aus `server/` (die sechs Platzhalter durch
die tatsächlich erzeugten lokalen Ordner ersetzen):

```powershell
..\.venv\Scripts\python.exe tools/audit_test_evidence.py --run-dir C:/Test/core-0 --run-dir C:/Test/core-1 --run-dir C:/Test/browser-0 --run-dir C:/Test/browser-1 --run-dir C:/Test/browser-2 --run-dir C:/Test/browser-3 --output C:/Test/neue-auswertung
```

Die Auswertung verlangt denselben Quellenhash, sämtliche Gruppen, eindeutige
Test-IDs, die vollständige ursprüngliche Collection und Übereinstimmung von
JUnit, Ereignisprotokoll und Zählern. Fehlende Aufrufe, fehlender Teardown,
Abbruch, Collection-only, veränderte Quellen und doppelte Ersatzläufe sperren
das Ergebnis. Plattform- und Opt-in-Skips bleiben mit ihren Gründen sichtbar.
Ältere Core-Berichte ohne Node-/Git-Vorprüfung gelten hierbei als unvollständig.
Ein bestandener Software-Audit bestätigt keine NX- oder CAM-Abnahme.

| Bereich | Bestehende Testfamilien / Ausbau |
|---|---|
| Alle 41 nativen Bibliotheken, Maschinen, Werkzeuge, Spannmittel, Schnittwerte | `test_libraries`, `test_library_schemas`, `test_resource_lifecycle`, HMI-Erstellungstests |
| Anlegen, Ändern, Stilllegen, erneuter Export | Ressourcen-Lifecycle: neue Exporte statt Überschreiben historischer Stände; referenzierte Halter sperren Löschung |
| Versionswechsel und Verzeichnisaufbau | `test_full_library_upgrade`, `test_format_upgrade_matrix`, Upgrade-/Dateiauswahl-/Konflikttests und entsprechende Browserabläufe |
| Revisionen, Verweise, Anhänge und Exporte | Referenz-/Paket-/Export-/Integritätstests einschließlich manipulierten Eingaben und konkurrierenden Änderungen |
| Serverbetrieb und Wiederherstellung | HTTP-, Backup-, Recovery-, Request-Replay- und Absturztests; zusätzlicher echter lokaler Serverlauf |
| NX-Pakete und Ergebnisübernahme | Bestehende Worker-/Collector-Verträge plus neue 2406-/2512-Versionstests; Echt-NX-Nachweis separat |
| HMI | Vollständige isolierte Edge-Suite, ergänzt um 2406-Auswahl und verlorene Antworten |
| Container | Isolierte GitHub-Containerprüfung manuell, wöchentlich und für Tags |
| NX2406-KRX-Spannmittel | Versionsgebundene Pakete, deklarierter Verweisgraph, finaler Byte-Commitgate, Übernahme/Export/Stilllegung und HMI; echte NX-Abnahme offen |
| Produktive SMB-Anbindung | Noch nicht abgenommen: erfordert gesonderte Testfreigabe und Berechtigungs-/Ausfalltests; W:\ bleibt ausgeschlossen |

## Release-Checkliste für den Werkzeugablauf

- Den zu prüfenden Commit und alle lokalen Änderungen festhalten. Software- und
  NX-Berichte müssen genau diesen Quellen- und Eingabestand binden. Nach Änderungen
  an Journal, Geometrie, Collector oder Export den lokalen NX-Nachweis erneuern.
- Vollständige aktuelle Core-/HMI-Berichte einschließlich Gruppenaudit sowie den
  vorgesehenen Docker-Lauf prüfen. Fehler, fehlende Gruppen und Infrastrukturprobleme
  verhindern die Softwareabnahme; alle echten Skips begründen.
- Für echte NX-Läufe getrenntes Testkonto und administrative Produktionssperre
  bestätigen. Den tatsächlich gemessenen Build, Lizenzbezug, STEP-Übersetzer und
  benötigte APIs dokumentieren. Ohne diese Voraussetzungen lautet das Ergebnis `blocked`.
- Fräser, Bohrer und Drehwerkzeug seriell erzeugen, als STEP importieren, speichern
  und in neuen NX-Sitzungen öffnen. Körper, Komponenten, Einzelvolumen, Lage,
  Gesamtabmessungen und Einheiten gegen die unabhängigen Sollwerte prüfen.
  Alle sechs physischen Gegenmodelle müssen den vorgesehenen Fehler zeigen.
- Begleitet mit nativen NX-CAM-Funktionen Schneid-/Nichtschneid- und
  Kollisionsgeometrie, Montagepunkt, Achsen und Einheiten bestätigen. Einen
  erwarteten Kollisionsfall und einen Freiraumfall dokumentieren. Personenname,
  Zeit und gebundene Modellartefakte gehören zum Nachweis.
- Aus der lokalen Testbibliothek sammeln, über den Server als Entwurf übernehmen,
  fachliche Prüfungen ausdrücklich bestätigen und kontrolliert exportieren.
  In einer neuen CAM-Sitzung aus genau diesem Export erneut laden; native
  Werkzeugwerte, LIBRF/Modellverweise und tatsächlich geladene Geometrie vergleichen.
  Die automatische Geometrieprüfung allein lässt diesen Punkt offen.
- Neustart, Backup und Wiederherstellung einschließlich Revisionen, Historien,
  Anhängen und Prüfbelegen nachweisen. Einen Versionswechsel nur für tatsächlich
  installierte und vollständig geprüfte NX-Versionen qualifizieren. Eine reale
  Migration nach 2512 bleibt bis zur entsprechenden Installation offen.

GitHub führt vollständige Core- und Browser-Suiten aus, einschließlich Windows mit
Python 3.10 und 3.12. Der wöchentliche Lauf beginnt montags 03:00 UTC. Reale lokale
NX-Läufe werden niemals durch GitHub oder Pull Requests gestartet.
Der CI-Starter schreibt zusätzlich fortlaufend `events.jsonl`. Sein `plan.json`
bindet Anwendungs-, Test-, Tool-, Workflow- und Abhängigkeitsdateien per SHA-256;
eine Änderung oder nicht mehr lesbare Quelle vor Laufende entwertet den Lauf.

Releasegates: vollständige aktuelle Softwarebaseline; bei NX-/Collector-/Export-
Änderungen zusätzlich Quellenbindung desselben Stands und alle drei Werkzeugfälle
mit begleiteter CAM-Abnahme. Fehlende, fehlerhafte oder unvollständige Berichte
sperren eine Produktivbehauptung. Produktive Netzfreigabe und echte 2512-Migration
bleiben gesonderte Abnahmen. Keine automatische Aktivierung oder Bereitstellung.

Priorität haben nachgewiesene Geometriefehler und Datenverlust, danach unterbrochene
Abläufe, Bedienbarkeit und Laufzeit. Die Anzahl vollständiger reproduzierbarer
Werkzeugabläufe ist die Erfolgsgröße, nicht die bloße Anzahl von Testfällen.
