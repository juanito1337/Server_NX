"""Revision-bound offline STEP preparation jobs, not automatic CAM approvals."""
import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from .safety import DomainError
from .stepfiles import TOOL_TYPES, preflight
from .store import canonical, now

WORKERS = {'nx_step_import.py': 'nxworker.py', 'nx_safety.py': 'safety.py', 'stepfiles.py': 'stepfiles.py'}


def worker_sources():
    return {name: (Path(__file__).parent / source).read_bytes() for name, source in WORKERS.items()}


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
   Roh-STEP nicht als fertige Kollisionsgeometrie publizieren. Kein automatischer
   Rueckimport von CAM-Daten/Komponentenpaketen in dieser ersten Ausbaustufe.
   Sechs bestehende NX-Pruefungen und SHA-Bindung bleiben fuer Freigabe erforderlich.

Status des Journals: gegen lokale NX2512 API-Definitionen entwickelt, noch kein
Echt-NX-Nachweis fuer diesen Auftrag. Modell- und Kollisionspruefung erforderlich.
'''


class GeometryJobs:
    def __init__(self, store):
        self.store = store

    def preview(self, asset_id):
        metadata, path = self.store.asset(asset_id)
        if path.suffix.lower() not in {'.step', '.stp', '.p21'}:
            raise DomainError('Select a STEP/STP/P21 attachment.')
        return {'asset': metadata, **preflight(path.read_bytes())}

    def create(self, resource_id, expected_revision, asset_id, tool_type, actor):
        if tool_type not in TOOL_TYPES:
            raise DomainError('Unsupported Assistant tool type.')
        preview = self.preview(asset_id)
        with self.store.lock, self.store.db() as db:
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
            job = {'contract_version': 1, 'id': str(uuid.uuid4()), 'resource_id': resource_id,
                   'resource_revision': row['revision'], 'resource_hash': hashlib.sha256(row['payload'].encode()).hexdigest(),
                   'resource_name': resource['name'], 'cam_snapshot': resource.get('cam', {}),
                   'nx_version': '2512', 'tool_type': tool_type, 'input': preview['asset'],
                   'preflight': preview, 'status': 'awaiting_nx', 'created_at': now(),
                   'worker_hashes': {name: hashlib.sha256(data).hexdigest() for name, data in worker_sources().items()}}
            job['input'] = {**job['input'], 'protocol': preview['protocol']}
            db.execute('INSERT INTO geometry_jobs(id,resource_id,payload,report) VALUES(?,?,?,NULL)',
                       (job['id'], resource_id, canonical(job)))
            self.store.audit(db, actor, 'geometry.job.created', {'id': job['id'], 'resource_id': resource_id,
                                                               'revision': row['revision'], 'input': job['input']})
        return job

    def get(self, job_id):
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
        _, path = self.store.asset(job['input']['id'])
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != job['input']['sha256']:
            raise DomainError('Pinned input changed.', 409)
        workers = worker_sources()
        if {name: hashlib.sha256(data).hexdigest() for name, data in workers.items()} != job['worker_hashes']:
            raise DomainError('Worker software changed; create a new job instead of altering the old package.', 409)
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
        expected = {'contract_version': 1, 'job_id': job_id, 'resource_id': job['resource_id'],
                    'resource_revision': job['resource_revision'], 'input_sha256': job['input']['sha256'],
                    'nx_version': '2512', 'tool_type': job['tool_type'], 'automatic_cam_approval': False}
        if any(type(report.get(k)) is not type(v) or report.get(k) != v for k, v in expected.items()):
            raise DomainError('Report does not match the pinned job or attempts automatic approval.')
        if report.get('status') not in {'needs_selection', 'needs_structure', 'failed'}:
            raise DomainError('A STEP import report cannot claim a validated CAM tool.')
        for key in ('parts', 'components', 'files', 'errors', 'warnings'):
            if not isinstance(report.get(key), list):
                raise DomainError('Invalid diagnostic report lists.')
        self.store.asset(job['input']['id'])  # Detect changed attachment, too.
        with self.store.lock, self.store.db() as db:
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
