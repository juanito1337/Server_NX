"""Revision-bound offline STEP preparation jobs, not automatic CAM approvals."""
import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from .safety import DomainError, offline_path
from .diagnostics import validate_report
from .stepfiles import TOOL_TYPES, preflight
from .store import canonical, now
from .nxbootstrap import journal_source

WORKERS = {'nx_step_import.py': 'nxworker.py', 'nx_safety.py': 'safety.py', 'stepfiles.py': 'stepfiles.py'}


def worker_sources():
    return {name: journal_source(source) if source=='nxworker.py' else (Path(__file__).parent/source).read_bytes()
            for name,source in WORKERS.items()}


def guidance(job):
    return f'''NX STEP-Vorbereitung - kein fertiges CAM-Werkzeug
Auftrag: {job['id']}
Stammdatensatz: {job['resource_id']} / Revision {job['resource_revision']}
Assistant-Werkzeugtyp: {TOOL_TYPES[job['tool_type']]}
Eingang: {job['input']['filename']} / SHA256 {job['input']['sha256']}

1. Gesamtes ZIP in einen NEUEN lokalen Ordner entpacken, z.B.
   C:\\NX_Migration\\ToolJobs\\{job['id'][:8]}.
   Keine Netzlaufwerke/UNC-Pfade, keine Siemens-Installation als Ziel.
2. Isolierte lokale NX2512-Sitzung (C:\\Siemens\\NX2512) in Modeling verwenden.
   Alle Teile schliessen; keine CAM-Sitzung, keine produktiven NX-Pfadvariablen.
   Tools > Automation > Journal > Play: nx_step_import.py aus diesem Paket.
   NICHT mit normalem Python starten. STEP/NX-Lizenz erforderlich.
3. Das Journal importiert nach output/tool.prt, erhaelt die Baugruppenstruktur
   und erzeugt output/report.json. Fehlversuche bleiben erhalten; fuer einen
   neuen Versuch einen neuen Ordner verwenden. NICHT bestehende output loeschen.
4. report.json in der Verwaltung zum selben Auftrag hochladen. Der Bericht
   ist Diagnose, KEINE Freigabe; er erzeugt keine Bibliotheksdatensaetze.
5. Bei needs_structure zuerst Geometrie/Baugruppe reparieren. Eine flache STEP
   wird nicht automatisch zur korrekten Werkzeugbaugruppe umgebaut. Ein einzelner
   vereinigter Koerper wird nicht durch geratenes Teilen zur Schneide.
6. Den separat bereitgestellten Siemens NX CAM 3D Tool Assistant starten:
   Ctrl+U > NX Open > app/NXCAM3DToolAssistant.dll (nicht Journal Play).
   Das Paket installiert/startet diese DLL nicht und enthaelt sie nicht.
   Anleitung V1.0, 09.06.2025, Seiten 10-20:
   - Kein Manufacturing Setup vor dem Assistant anlegen.
   - Nur Werkzeuge mit Spindelanschluss; oben angegebenen Werkzeugtyp auswaehlen.
   - Werkzeugmasse/typabhaengige Parameter pruefen, nicht aus Dateinamen raten.
   - Machine Side Connection Face und Mounting Centre Point waehlen.
   - Bei Drehen Insert Orientation Direction festlegen; bei Fraesen/Bohren
     kann der Assistant die Rotationsorientierung automatisch setzen.
   - Schneidkoerper waehlen; bei Fraesen/Bohren auch Halter-/Schaftkoerper.
     Mehrere Halter-/Schaftkoerper muessen mindestens aneinander anliegen.
   - Optional CUT-Farbe 42 / NOCUT-Farbe 87. Farbe ist kein Geometrienachweis.
   - Bei fehlgeschlagener Konturableitung kann ein 1-Stufen-Schaft entstehen:
     Construction Geometry behalten, vereinfachen, Schritte erneut ableiten.
     Einfache Huellkonturen und P9/Antastpunkt fachlich pruefen.
7. Assistant-Ergebnis zuerst nur in lokale TEST-Bibliothek exportieren. LIBRF,
   DAT-Parameter und alle PRT-Baugruppenkomponenten muessen zusammenpassen.
   Das Journal exportiert absichtlich weder DAT noch PRT in Netzbibliotheken.
8. Aufbereitetes Modell + CAM-Daten danach als Entwurf in der Verwaltung pflegen.
   Roh-STEP nicht als fertige Kollisionsgeometrie publizieren. Fuer gespeicherte
   Assistant-Ergebnisse gibt es in der GUI einen lokalen Collector und eine
   ausdrueckliche Paketvorschau/-uebernahme, keine automatische Veroeffentlichung.
   DAT-Daten und alle PRT-Komponenten zusammen pruefen. Die Uebernahme erzeugt
   nur Entwuerfe. Sechs NX-Pruefungen und SHA-Bindung bleiben fuer Freigabe noetig.

Status des Journals: gegen lokale NX2512 API-Definitionen entwickelt, noch kein
Echt-NX-Nachweis fuer diesen Auftrag. Modell- und Kollisionspruefung erforderlich.
Neue Pakete pruefen job.json und alle drei Workerdateien vor dem Laden der
Hilfsdateien. Es werden ausschliesslich gepruefte Quellbytes ausgefuehrt, keine
fremden Import-Caches oder benachbarten pyc-Dateien. Alte gespeicherte ZIPs
bleiben unveraendert; fuer den neuen Startschutz einen neuen Auftrag anlegen.
STEP-Lesen ist auf20MiB/Groesse/SHA gebunden. Ein Hash ist keine Signatur gegen
einen Administrator, der zugleich Manifest und Programme veraendern kann.
'''


class GeometryJobs:
    def __init__(self, store):
        self.store = store

    def preview(self, asset_id):
        return self._input(asset_id)[0]

    def _input(self, asset_id):
        metadata, content = self.store.asset_content(asset_id)
        if Path(metadata['filename']).suffix.lower() not in {'.step', '.stp', '.p21'}:
            raise DomainError('Select a STEP/STP/P21 attachment.')
        return {'asset': metadata, **preflight(content)}, content

    def create(self, resource_id, expected_revision, asset_id, tool_type, actor,request_key=None):
        # Legacy callers have no durable allocation for backup to observe.
        # Keep their whole physical write under the in-process store gate.
        if request_key is None:
            from .asset_requests import backup_active
            if backup_active(self.store):raise DomainError('Backup/recovery writer is active; STEP package allocation must wait.',409)
            with self.store.lock:
                return self._create(resource_id,expected_revision,asset_id,tool_type,actor)
        return self._create(resource_id,expected_revision,asset_id,tool_type,actor,request_key)

    def _create(self, resource_id, expected_revision, asset_id, tool_type, actor,request_key=None):
        from . import geometry_requests as receipts
        from .write_requests import key
        from .asset_requests import backup_active
        request_key=key(request_key)
        digest=receipts.request_hash(resource_id,expected_revision,asset_id,tool_type)
        if request_key is not None:
            with self.store.db() as db:
                db.execute('BEGIN');cached=receipts.replay(db,actor,request_key,digest)
            if cached is not None:return cached  # Original metadata, not a package/file verification.
        if backup_active(self.store):raise DomainError('Backup/recovery writer is active; STEP package allocation must wait.',409)
        if tool_type not in TOOL_TYPES:
            raise DomainError('Unsupported Assistant tool type.')
        preview, content = self._input(asset_id)
        with self.store.lock, self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cached=receipts.replay(db,actor,request_key,digest)
            if cached is not None:return cached
            if backup_active(self.store):raise DomainError('Backup/recovery writer is active; STEP package allocation must wait.',409)
            row = db.execute('SELECT * FROM resources WHERE id=?', (resource_id,)).fetchone()
            if not row:
                raise DomainError('Resource not found.', 404)
            if row['revision'] != expected_revision:
                raise DomainError('Resource changed; reload before creating a geometry job.', 409)
            resource = json.loads(row['payload'])
            if resource['kind'] not in {'tool', 'tool_assembly'}:
                raise DomainError('The Assistant workflow supports spindle-connected cutting tools, not arbitrary catalog objects.')
            if asset_id not in resource.get('asset_ids', []):
                raise DomainError('STEP attachment must belong to the saved resource.')
            if resource.get('cam_status') != 'draft':
                raise DomainError('Create geometry preparation jobs only for drafts; do not change an approved tool implicitly.')
            workers = worker_sources()
            job = {'contract_version': 1, 'id': str(uuid.uuid4()), 'resource_id': resource_id,
                   'resource_revision': row['revision'], 'resource_hash': hashlib.sha256(row['payload'].encode()).hexdigest(),
                   'resource_name': resource['name'], 'cam_snapshot': resource.get('cam', {}),
                   'nx_version': '2512', 'tool_type': tool_type, 'input': preview['asset'],
                   'preflight': preview, 'status': 'awaiting_nx', 'created_at': now(),
                   'worker_hashes': {name: hashlib.sha256(data).hexdigest() for name, data in workers.items()}}
            job['input'] = {**job['input'], 'protocol': preview['protocol']}
            package = self._archive(job, workers, content)
            job['package_sha256'] = hashlib.sha256(package).hexdigest()
            job['package_size'] = len(package)
            receipts.reserve(self.store,db,actor,request_key,digest,job)
        # Intent is durable BEFORE physical write. A fence prevents SQL commit,
        # not the actual filesystem writer; incomplete packages are retained.
        try:
            directory = offline_path(self.store.root / 'geometry-packages' / job['id'])
            directory.mkdir(parents=True, exist_ok=False)
            path=offline_path(directory / 'NX_STEP.zip');path.write_bytes(package)
            from .integrity import check_file
            check_file(path,job['package_sha256'],job['package_size'])
            metadata,_=self.store.asset(asset_id)
            if any(metadata[name]!=job['input'][name] for name in ('id','filename','sha256','size','created_at')):
                raise DomainError('STEP input changed during package creation; partial evidence retained.',409)
            with self.store.lock,self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT revision,payload FROM resources WHERE id=?',(resource_id,)).fetchone()
                if not current or current['revision']!=expected_revision or hashlib.sha256(current['payload'].encode()).hexdigest()!=job['resource_hash']:
                    raise DomainError('Resource changed during STEP package creation; partial evidence retained.',409)
                db.execute('INSERT INTO geometry_jobs(id,resource_id,payload,report) VALUES(?,?,?,NULL)',
                           (job['id'], resource_id, canonical(job)))
                binding=receipts.commit(db,actor,request_key,job,now())
                self.store.audit(db, actor, 'geometry.job.created', {'id': job['id'], 'resource_id': resource_id,
                        'revision': expected_revision, 'input': job['input'], 'package_sha256': job['package_sha256'],**binding})
        except Exception as error:
            receipts.fail(self.store,actor,request_key)
            if isinstance(error,OSError):raise DomainError('Cannot store the immutable STEP package; incomplete files retained.',409) from error
            raise
        return job

    def get(self, job_id):
        try:
            if str(uuid.UUID(job_id)) != job_id:
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise DomainError('Invalid geometry job identifier.')
        with self.store.db() as db:
            row = db.execute('SELECT * FROM geometry_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise DomainError('Geometry job not found.', 404)
        result = json.loads(row['payload'])
        result['report'] = json.loads(row['report']) if row['report'] else None
        if result['report']:
            result['status'] = result['report']['status']
        return result

    def list(self, resource_id=None):
        with self.store.db() as db:
            rows = db.execute('SELECT id FROM geometry_jobs WHERE resource_id=? ORDER BY rowid DESC', (resource_id,)) if resource_id else db.execute('SELECT id FROM geometry_jobs ORDER BY rowid DESC')
            ids = [row['id'] for row in rows]
        return [self.get(job_id) for job_id in ids]

    def package(self, job_id):
        job = self.get(job_id)
        _, content = self.store.asset_content(job['input']['id'])
        if hashlib.sha256(content).hexdigest() != job['input']['sha256']:
            raise DomainError('Pinned input changed.', 409)
        if 'package_sha256' in job:
            path = offline_path(self.store.root / 'geometry-packages' / job['id'] / 'NX_STEP.zip')
            try:
                if path.stat().st_size != job['package_size']:
                    raise DomainError('Pinned STEP package size changed.', 409)
                with path.open('rb') as stream:
                    package = stream.read(job['package_size'] + 1)
            except OSError as error:
                raise DomainError('Pinned STEP package is missing or unreadable; do not recreate it from current software.', 409) from error
            if len(package) != job['package_size'] or hashlib.sha256(package).hexdigest() != job['package_sha256']:
                raise DomainError('Pinned STEP package changed outside the API.', 409)
            return package
        # Legacy jobs have no stored artifact. Never pretend today's changed
        # workers are the historical ones; only their exact hashes can be used.
        workers = worker_sources()
        if {name: hashlib.sha256(data).hexdigest() for name, data in workers.items()} != job['worker_hashes']:
            raise DomainError('Worker software changed; create a new job instead of altering the old package.', 409)
        return self._archive({**job, 'status': 'awaiting_nx'}, workers, content)

    @staticmethod
    def _archive(job, workers, content):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('input.step', content)
            archive.writestr('job.json', canonical({k: v for k, v in job.items() if k != 'report'}).encode())
            archive.writestr('LIESMICH.txt', guidance(job).encode('utf-8-sig'))
            for name, data in workers.items():
                archive.writestr(name, data)
        return output.getvalue()

    def submit_report(self, job_id, report, actor):
        job = self.get(job_id)
        text = canonical(report)
        if len(text.encode()) > 1024 * 1024:
            raise DomainError('NX diagnostic report must be at most 1 MB.')
        report = validate_report(report, job)
        text = canonical(report)
        metadata, _ = self.store.asset(job['input']['id'])
        if metadata['sha256'] != job['input']['sha256']:
            raise DomainError('Diagnostic input does not match the pinned attachment.', 409)
        with self.store.lock, self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,payload FROM resources WHERE id=?', (job['resource_id'],)).fetchone()
            if not row or row['revision'] != job['resource_revision'] or hashlib.sha256(row['payload'].encode()).hexdigest() != job['resource_hash']:
                raise DomainError('Resource changed since job creation. Create a new job.', 409)
            old = db.execute('SELECT report FROM geometry_jobs WHERE id=?', (job_id,)).fetchone()['report']
            if old and old != text:
                raise DomainError('A diagnostic report is already bound to this job; create a new job for another run.', 409)
            if not old:
                db.execute('UPDATE geometry_jobs SET report=? WHERE id=?', (text, job_id))
                self.store.audit(db, actor, 'geometry.report.received', {'id': job_id, 'report_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'status': report['status'], 'trust': 'unverified_external_diagnostic', 'cam_approval_changed': False})
        return self.get(job_id)
