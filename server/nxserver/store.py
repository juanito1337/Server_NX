import hashlib
import copy
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
import zipfile
from contextlib import contextmanager,nullcontext
from datetime import datetime, timezone
from pathlib import Path

from .dat import read_database
from .folders import FolderAccess
from .libraries import (KINDS, SPECS, OPTIONAL_PALETTE, NativeTable, cam_table, digest,
                        geometry_check, inventory, legacy_cam_table, legacy_database, native_cam,
                        normalization_plan, project_records, projection_block, records_hash,
                        reference_report, safe_graphics_path, graphics_bindings, spec_for, catalog_selection,validate_catalog_cam)
from .safety import DomainError, disjoint, offline_path, tree_files, asset_filename
from .testlaunch import archive, content_revision, package_files, template_sha256
from .syslogs import MAX_LOG_BYTES, PARSER_VERSION, decode_log, scan_bytes
from .bounded_file import read as read_bounded_file
from .write_requests import SCHEMA as WRITE_REQUEST_SCHEMA,key as write_request_key,request_hash as write_hash,replay as write_replay,commit as write_commit
from .migration_requests import SCHEMA as MIGRATION_REQUEST_SCHEMA
from .asset_requests import SCHEMA as ASSET_REQUEST_SCHEMA
from .geometry_requests import SCHEMA as GEOMETRY_REQUEST_SCHEMA
from .backup_requests import SCHEMA as BACKUP_REQUEST_SCHEMA
from .log_requests import SCHEMA as LOG_REQUEST_SCHEMA
from .release_requests import SCHEMA as RELEASE_REQUEST_SCHEMA
from .migration_report import read as read_migration_report,decode as decode_migration_report

TOOL_DATABASE = 'MACH/resource/library/tool/metric/tool_database.dat'
FUNCTIONAL_TESTS = {'tools', 'machines', 'posts', 'simulation', 'fixtures', 'inspection'}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def resource_identity(resource):
    """One catalog owner per native library key, including reserved drafts/retirements."""
    origin, cam = resource.get('origin') or {}, resource.get('cam') or {}
    if origin:
        return origin['library_id'], origin['key']
    if cam.get('library_id'):
        table = cam_table(cam)
        return cam['library_id'], next(iter(table.groups()))
    key = cam.get('values', {}).get('LIBRF') if isinstance(cam.get('values'), dict) else None
    if resource['kind'] == 'tool' and isinstance(key, str) and key.strip():
        return 'tool-metric', key.strip()
    return None


class Store:
    def __init__(self, root, sources=None, logs=None, standards=None, display_paths=None,
                 source_roots=None, target_roots=None, sidts=None):
        self.root = offline_path(root)
        if offline_path(self.root/'RECOVERY_PENDING.json').exists():
            raise DomainError('Recovery is incomplete; this isolated directory must not be started.',409)
        self.sources = {key: offline_path(path) for key, path in (sources or {}).items()}
        self.logs = {key: offline_path(path) for key, path in (logs or {}).items()}
        self.standards = {key: offline_path(path) for key, path in (standards or {}).items()}
        self.sidts = {key: offline_path(path) for key, path in (sidts or {}).items()}
        self.installers = {}
        self.extractor = None
        self.display_paths = {}
        for internal, external in (display_paths or {}).items():
            # Host-side Windows paths are textual references, NEVER filesystem
            # targets in a Linux container. Only the administrator sets them.
            target = external.replace('\\', '/').rstrip('/')
            if not re.match(r'(?i)^c:/[^\x00-\x1f]+$', target) or re.search(
                    r'(?i)(^|/)siemensshare(/|$)|(?:^|/)\.\.?(/|$)', target):
                raise DomainError('Display paths must be absolute local C: references without traversal or production paths.')
            container_path = offline_path(internal)
            if container_path != self.root / 'r':
                raise DomainError('Display mapping is limited to the managed release directory.')
            self.display_paths[container_path] = target
        for source in list(self.sources.values()) + list(self.logs.values()) + list(self.standards.values()) + list(self.sidts.values()):
            disjoint(self.root, source)
        self.folders = FolderAccess(self, source_roots, target_roots)
        self.root.mkdir(parents=True, exist_ok=True)
        offline_path(self.root / 'management.sqlite3')
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    external_key TEXT UNIQUE, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resource_versions (
                    id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(id, revision));
                CREATE TABLE IF NOT EXISTS releases (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, nx_version TEXT NOT NULL,
                    source TEXT NOT NULL, parent_id TEXT, status TEXT NOT NULL,
                    manifest TEXT, validation TEXT, review TEXT, catalog TEXT,
                    created_at TEXT NOT NULL, folder TEXT);
                CREATE TABLE IF NOT EXISTS active (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    release_id TEXT, generation INTEGER NOT NULL);
                INSERT OR IGNORE INTO active(singleton,release_id,generation) VALUES(1, NULL, 0);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS geometry_jobs (
                    id TEXT PRIMARY KEY, resource_id TEXT NOT NULL,
                    payload TEXT NOT NULL, report TEXT);
                CREATE TABLE IF NOT EXISTS geometry_results (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, resource_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS migration_jobs (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sidt_inputs (
                    version TEXT PRIMARY KEY, job_id TEXT NOT NULL, generation INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS test_launchers (
                    release_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS syslogs (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                    action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
            ''')
            db.execute(WRITE_REQUEST_SCHEMA)
            db.execute(MIGRATION_REQUEST_SCHEMA)
            db.execute(ASSET_REQUEST_SCHEMA)
            db.execute(GEOMETRY_REQUEST_SCHEMA)
            db.execute(BACKUP_REQUEST_SCHEMA)
            db.execute(LOG_REQUEST_SCHEMA)
            db.execute(RELEASE_REQUEST_SCHEMA)
            if 'folder' not in {row['name'] for row in db.execute('PRAGMA table_info(releases)')}:
                db.execute('ALTER TABLE releases ADD COLUMN folder TEXT')
            for row in db.execute('SELECT id FROM releases WHERE folder IS NULL').fetchall():
                legacy = self.root / 'releases' / row['id'] / 'folder'
                db.execute('UPDATE releases SET folder=? WHERE id=?', (str(legacy), row['id']))
        with self.db() as db:
            self.recovery_origins=[];self.recovered_job_ids=set()
            for row in db.execute("SELECT payload FROM audit WHERE action='management.restored' ORDER BY sequence"):
                provenance=json.loads(row['payload'])
                self.recovered_job_ids.update(provenance.get('restored_migration_jobs',[]))
                for origin in provenance.get('original_management_roots',[]):
                    if origin not in self.recovery_origins:self.recovery_origins.append(origin)

    def private_artifact(self,value,relative):
        from .recovery import private_artifact
        return private_artifact(self,value,relative)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(offline_path(self.root / 'management.sqlite3'), timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            with connection:
                yield connection
        finally:
            connection.close()

    def audit(self, db, actor, action, payload):
        from .auditstate import committed_bindings
        bindings=committed_bindings(db,action,payload)
        if bindings:payload={**payload,'_state':{'schema':1,'bindings':bindings}}
        last = db.execute('SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1').fetchone()
        previous = last['hash'] if last else '0' * 64
        stamp, text = now(), canonical(payload)
        digest = hashlib.sha256(canonical([previous, actor, action, text, stamp]).encode()).hexdigest()
        db.execute('INSERT INTO audit(actor,action,payload,created_at,previous_hash,hash) VALUES(?,?,?,?,?,?)',
                   (actor, action, text, stamp, previous, digest))

    def save_resource(self, payload, actor, resource_id=None, expected=None, trusted_model_package=False, trusted_file_package=False,request_key=None,operation_job_id=None,file_adoption_guard=None):
        request_key=write_request_key(request_key);action='resource.update' if resource_id else 'resource.create'
        request_sha=write_hash(action,resource_id,expected,payload) if request_key else None
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cached=write_replay(db,actor,request_key,action,request_sha)
            if cached is not None:return cached
            from .erp_variants import legacy_key
            legacy_key(db,actor,request_key)
            old = db.execute('SELECT * FROM resources WHERE id=?', (resource_id,)).fetchone() if resource_id else None
            if resource_id and not old:
                raise DomainError('Resource not found.', 404)
            if old and expected != old['revision']:
                raise DomainError('Resource changed; reload before saving.', 409)
            resource_id = resource_id or str(uuid.uuid4())
            revision = old['revision'] + 1 if old else 1
            if old:
                old_payload = json.loads(old['payload'])
                if old_payload['kind'] != payload['kind']:
                    raise DomainError('Resource kind is immutable; create a new resource.', 409)
                if old_payload.get('erp') != payload.get('erp'):
                    raise DomainError('ERP-owned fields may only be changed through ERP upsert.', 409)
                if old_payload.get('origin') != payload.get('origin'):
                    raise DomainError('Import provenance may not be changed through resource editing.', 409)
            elif payload.get('origin'):
                raise DomainError('Import provenance is assigned only by native catalog import.')
            validate_catalog_cam(payload)
            from .filepackages import validate as validate_file_package
            if old and old_payload.get('file_package') and not payload.get('file_package'):
                raise DomainError('Keep file-package ownership; retire it explicitly instead of detaching it.')
            parsed_file_package=validate_file_package(self,payload,old_payload if old else None,trusted_file_package)
            if parsed_file_package is not None:payload={**payload,'file_package':parsed_file_package}
            package=payload.get('model_package')
            if package and not trusted_model_package and (not old or package!=old_payload.get('model_package')):
                raise DomainError('Model package provenance is assigned only by verified result import.')
            identity = resource_identity(payload)
            if old:
                tracked = resource_identity(old_payload) if old_payload.get('origin') else None
                for release in db.execute('SELECT catalog FROM releases WHERE catalog IS NOT NULL'):
                    catalog = json.loads(release['catalog'])
                    native = catalog.get('exported_native', {}).get(resource_id)
                    legacy = catalog.get('exported_tools', {}).get(resource_id)
                    if native:
                        tracked = native['library_id'], native['key']
                    elif legacy:
                        tracked = resource_identity(legacy)
                if tracked and (identity != tracked or
                        resource_identity({**payload, 'origin': None}) != tracked):
                    raise DomainError('Imported/exported native identifiers are immutable; create a new resource.', 409)
                if tracked and bool(old_payload.get('cam', {}).get('library_id')) != bool(payload.get('cam', {}).get('library_id')):
                    raise DomainError('Imported/exported CAM representation is immutable; edit its existing schema.', 409)
            if identity:
                for row in db.execute('SELECT id,payload FROM resources'):
                    if row['id'] != resource_id and resource_identity(json.loads(row['payload'])) == identity:
                        raise DomainError('NX library identifier is already managed by resource ' + row['id'] +
                                          ': ' + '/'.join(identity), 409)
            erp = payload.get('erp')
            if erp and not old:
                from .erp_variants import legacy_writer
                legacy_writer(db)
            asset_ids = payload.get('asset_ids', [])
            if len(asset_ids) != len(set(asset_ids)):
                raise DomainError('Duplicate asset identifiers are not allowed.')
            metadata = []
            for asset_id in payload.get('asset_ids', []):
                if not db.execute('SELECT id FROM assets WHERE id=?', (asset_id,)).fetchone():
                    raise DomainError('Unknown asset identifier.')
                metadata.append(self.asset(asset_id)[0])
            if payload.get('file_bindings'):
                library=payload['cam'].get('library_id') or ('tool-metric' if payload['kind']=='tool' else None)
                graphics_bindings(payload,metadata,spec_for(library))
            if package:
                if digest(payload.get('file_bindings',[]))!=package['bindings_hash'] or not set(package['asset_ids'])<=set(asset_ids):
                    raise DomainError('Package component bindings changed; explicitly detach package provenance before editing them.')
                if not identity or identity[0]!=package['library_id'] or identity[1]+'.prt'!=package['root_part']:
                    raise DomainError('Bound model library/key is immutable; detach or import a new prepared package before changing it.')
            if payload.get('cam_status') == 'approved':
                geometry_check(payload, metadata)
            if file_adoption_guard is not None:
                from .filepackages import verify_adoption_commit
                if not trusted_file_package:raise DomainError('File adoption commit context is internal only.')
                verify_adoption_commit(self,db,file_adoption_guard,payload,resource_id if old else None)
            external_key = canonical([erp['company_id'], erp['system_id']]) if erp else None
            text, stamp = canonical(payload), now()
            try:
                db.execute('INSERT INTO resources(id,revision,payload,external_key,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                           'revision=excluded.revision,payload=excluded.payload,external_key=excluded.external_key,updated_at=excluded.updated_at',
                           (resource_id, revision, text, external_key, stamp))
            except sqlite3.IntegrityError as error:
                raise DomainError('ERP object already linked to a resource.', 409) from error
            db.execute('INSERT INTO resource_versions(id,revision,payload,actor,created_at) VALUES(?,?,?,?,?)', (resource_id, revision, text, actor, stamp))
            result={'id': resource_id, 'revision': revision, 'updated_at': stamp, **payload}
            receipt=write_commit(db,actor,request_key,action,request_sha,result,stamp)
            if operation_job_id is not None:
                from .job_results import resource_result
                receipt.update(resource_result(db,operation_job_id,actor,'file_adopt',[{'id':resource_id,'revision':revision}]))
            self.audit(db, actor, 'resource.saved', {'id': resource_id, 'revision': revision, 'payload': payload,**receipt})
        return result

    def erp_upsert(self, data, actor,request_key=None):
        if not isinstance(data,dict):raise DomainError('ERP input must be a JSON object.')
        request_key=write_request_key(request_key);request_sha=write_hash('erp.upsert',None,data.get('expected_revision'),data) if request_key else None
        if data.get('kind') in ('template','ui_standard'):raise DomainError('Company UI/template packages are CAM-admin data, not ERP-owned resources.')
        external_key = canonical([data.get('company_id'), data.get('system_id')])
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            cached=write_replay(db,actor,request_key,'erp.upsert',request_sha)
            if cached is not None:return cached
            from .erp_variants import legacy_writer
            legacy_writer(db)
            from .erp_catalog import validate_fields,decode_resource
            if not isinstance(data.get('kind'),str) or data['kind'] not in KINDS:raise DomainError('Unsupported ERP catalog kind.')
            erp=validate_fields({name:data.get(name,'') for name in ('company_id','system_id','number','description','unit','source_revision')})
            old = db.execute('SELECT * FROM resources WHERE external_key=?', (external_key,)).fetchone()
            if old:
                payload = decode_resource(old['payload'])
                head=db.execute('SELECT payload,created_at FROM resource_versions WHERE id=? AND revision=?',(old['id'],old['revision'])).fetchone()
                history=tuple(db.execute('SELECT COUNT(*),MIN(revision),MAX(revision) FROM resource_versions WHERE id=?',(old['id'],)).fetchone())
                if type(old['revision']) is not int or old['revision']<1 or history!=(old['revision'],1,old['revision']) or not head or head['payload']!=old['payload'] or head['created_at']!=old['updated_at']:
                    raise DomainError('Current ERP resource differs from its complete history head; inspect before updating.',409)
                try:previous_erp=validate_fields(payload.get('erp'))
                except DomainError as error:raise DomainError('Current ERP identity is inconsistent; inspect the binding before updating.',409) from error
                if canonical([previous_erp['company_id'],previous_erp['system_id']])!=external_key:
                    raise DomainError('Current ERP lookup key differs from its commercial identity; no automatic reassignment.',409)
                if data['kind'] != payload.get('kind'):
                    raise DomainError('ERP cannot change the resource kind.', 409)
                validate_catalog_cam(payload)
                if payload['erp'] == erp:
                    result={'id': old['id'], 'revision': old['revision'], 'unchanged': True, **payload}
                    receipt=write_commit(db,actor,request_key,'erp.upsert',request_sha,result,now())
                    if receipt:self.audit(db,actor,'write.request.receipted',{'id':old['id'],**receipt})
                    return result
                if data.get('expected_revision') != old['revision']:
                    raise DomainError('ERP upsert requires current expected_revision.', 409)
                resource_id, revision = old['id'], old['revision'] + 1
                payload['erp'] = erp
                # CAM values and CAM approval are deliberately not ERP-owned.
            else:
                payload = {'kind': data['kind'], 'name': data['description'], 'erp': erp,
                           'cam': {}, 'cam_status': 'draft', 'asset_ids': []}
                resource_id, revision = str(uuid.uuid4()), 1
            text, stamp = canonical(payload), now()
            db.execute('INSERT INTO resources(id,revision,payload,external_key,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                       'revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at',
                       (resource_id, revision, text, external_key, stamp))
            db.execute('INSERT INTO resource_versions(id,revision,payload,actor,created_at) VALUES(?,?,?,?,?)', (resource_id, revision, text, actor, stamp))
            result={'id': resource_id, 'revision': revision, 'updated_at': stamp, **payload}
            receipt=write_commit(db,actor,request_key,'erp.upsert',request_sha,result,stamp)
            self.audit(db, actor, 'erp.upserted', {'id': resource_id, 'revision': revision, 'erp': erp,**receipt})
        return result

    def resources(self):
        with self.db() as db:
            return [self._resource_value(row) for row in db.execute('SELECT * FROM resources ORDER BY updated_at DESC')]

    def get_resource(self,resource_id):
        """Focused read of the current saved revision; no history replay or write."""
        with self.db() as db:row=db.execute('SELECT * FROM resources WHERE id=?',(resource_id,)).fetchone()
        if row is None:raise DomainError('Resource not found.',404)
        return self._resource_value(row)

    def _resource_value(self, row):
        try:payload = json.loads(row['payload'])
        except (ValueError,TypeError) as error:raise DomainError('Stored resource metadata is not valid JSON.',409) from error
        if not isinstance(payload,dict) or {'id','revision','updated_at'} & set(payload):
            raise DomainError('Stored resource metadata has an invalid shape or shadows its identity.',409)
        return {'id':row['id'],'revision':row['revision'],'updated_at':row['updated_at'],**payload}

    def history(self, resource_id):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM resource_versions WHERE id=? ORDER BY revision', (resource_id,))]

    def add_asset(self, filename, content, actor,operation_job_id=None,request_key=None):
        from .filepackages import UI_PROFILE_SUFFIXES
        request_key=write_request_key(request_key)
        if request_key is not None and operation_job_id is not None:raise DomainError('Upload and adoption allocation protocols may not be mixed.')
        asset_filename(filename)
        if Path(filename).suffix.lower() not in {'.prt', '.step', '.stp', '.jpg', '.jpeg', '.png', '.pdf',
                                                '.bmp', '.x_t', '.igs', '.iges', '.dxf', '.p21', '.xlsx', '.krx',
                                                '.pax','.mtx','.opt','.xml','.dpv','.men','.tbr','.ribbon'} | UI_PROFILE_SUFFIXES:
            raise DomainError('Unsupported asset extension.')
        if filename.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(?i)(COM|LPT)[1-9]', filename.split('.')[0]):
            raise DomainError('Reserved Windows asset filename.')
        if not content or len(content) > 20 * 1024 * 1024:
            raise DomainError('Asset must contain 1 byte to 20 MB.')
        asset_id = str(uuid.uuid4())
        from .job_results import reserve_asset,stored_asset
        allocation={'id':asset_id,'filename':filename,'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}
        if request_key is not None:
            from .asset_requests import reserve
            cached=reserve(self,actor,request_key,allocation)
            if cached is not None:return cached
        reserve_asset(self,operation_job_id,actor,allocation)
        directory = offline_path(self.root / 'assets' / asset_id)
        path = offline_path(directory / filename)
        try:
            directory.mkdir(parents=True, exist_ok=False)
            path.write_bytes(content)
        except OSError as error:
            from .asset_requests import fail
            fail(self,actor,request_key)
            raise DomainError('Cannot store attachment; incomplete evidence retained.', 409) from error
        try:
            digest = sha256(path)
            if digest!=allocation['sha256']:raise DomainError('Written attachment differs from prepared bytes; incomplete allocation retained.',409)
            with self.lock, self.db() as db:
                db.execute('BEGIN IMMEDIATE');stamp=now()
                db.execute('INSERT INTO assets(id,filename,sha256,size,created_at) VALUES(?,?,?,?,?)', (asset_id, filename, digest, len(content), stamp))
                binding=stored_asset(db,operation_job_id,actor,allocation)
                from .asset_requests import commit
                binding.update(commit(db,actor,request_key,allocation,stamp))
                self.audit(db, actor, 'asset.created', {'id': asset_id, 'filename': filename, 'sha256': digest,**binding})
        except Exception:
            from .asset_requests import fail
            fail(self,actor,request_key)
            raise
        return {'id': asset_id, 'filename': filename, 'sha256': digest, 'size': len(content)}

    def asset(self, asset_id):
        try:
            if str(uuid.UUID(asset_id)) != asset_id:
                raise ValueError()
        except (ValueError, AttributeError, TypeError):
            raise DomainError('Invalid asset identifier.')
        with self.db() as db:
            row = db.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
        if not row:
            raise DomainError('Asset not found.', 404)
        result = dict(row)
        path = offline_path(self.root / 'assets' / result['id'] / result['filename'])
        if not path.is_file():
            raise DomainError('Stored asset file is missing.', 409)
        try:
            if path.stat().st_size != result['size'] or sha256(path) != result['sha256']:
                raise DomainError('Asset has changed outside the API.', 409)
        except OSError as error:
            raise DomainError('Stored asset file cannot be read.', 409) from error
        return result, path

    def asset_content(self, asset_id):
        metadata, path = self.asset(asset_id)
        content=read_bounded_file(path,max_bytes=20*1024*1024,expected_size=metadata['size'],expected_sha256=metadata['sha256'],
            changed_message='Asset changed during download.',limit_message='Stored asset must contain 1 byte to 20 MB.')
        return metadata, content

    def asset_inventory(self, query='', offset=0, limit=100):
        """Metadata listing only; file integrity is checked on actual access."""
        with self.db() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM assets ORDER BY created_at DESC,id')]
            usage = {}
            for row in db.execute('SELECT payload FROM resources'):
                for asset_id in set(json.loads(row['payload']).get('asset_ids', [])):
                    usage[asset_id] = usage.get(asset_id, 0) + 1
        filtered = [row for row in rows if query.casefold() in row['filename'].casefold()]
        return {'total': len(rows), 'filtered': len(filtered), 'offset': offset,
                'integrity_checked': False,
                'items': [{**row, 'current_resource_count': usage.get(row['id'], 0)}
                          for row in filtered[offset:offset + limit]]}

    def release_folder(self, release_id):
        try:
            if str(uuid.UUID(release_id)) != release_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise DomainError('Invalid release identifier.')
        with self.db() as db:
            row = db.execute('SELECT folder FROM releases WHERE id=?', (release_id,)).fetchone()
        # Short physical names avoid Windows MAX_PATH with long Siemens filenames.
        return self._release_path(release_id,row['folder'] if row else None)

    def _release_path(self, release_id, folder):
        try:
            if str(uuid.UUID(release_id)) != release_id:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise DomainError('Invalid release identifier.')
        return offline_path(folder or self.root / 'r' / uuid.UUID(release_id).hex[:12] / 'nx')

    def host_folder(self, folder):
        for internal, external in self.display_paths.items():
            try:
                suffix = folder.relative_to(internal).as_posix()
            except ValueError:
                continue
            return (external + '/' + suffix).replace('/', '\\')
        return None

    def get_release(self, release_id):
        with self.db() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM releases WHERE id=?', (release_id,)).fetchone()
            launcher = db.execute('SELECT payload FROM test_launchers WHERE release_id=?', (release_id,)).fetchone()
        if not row:
            raise DomainError('Release not found.', 404)
        return self._release_value(row,launcher['payload'] if launcher else None)

    def _release_value(self, row, launcher=None, include_manifest=True):
        result = dict(row)
        for key in ('manifest', 'validation', 'review', 'catalog'):
            if key=='manifest' and not include_manifest:result.pop(key,None);continue
            try:result[key] = json.loads(result[key]) if result[key] else None
            except (ValueError,TypeError) as error:raise DomainError('Stored release '+key+' metadata is not valid JSON.',409) from error
        folder = self._release_path(result['id'],result.get('folder'))
        result['folder'] = str(folder);result['host_folder'] = self.host_folder(folder)
        try:result['test_launcher'] = json.loads(launcher) if launcher else None
        except (ValueError,TypeError) as error:raise DomainError('Stored test-launcher metadata is not valid JSON.',409) from error
        return result

    def releases(self):
        with self.db() as db:
            db.execute('BEGIN')
            rows = db.execute('SELECT * FROM releases ORDER BY created_at DESC').fetchall()
            launchers = {row['release_id']:row['payload'] for row in db.execute('SELECT release_id,payload FROM test_launchers')}
        return [self._release_value(row,launchers.get(row['id']),include_manifest=False) for row in rows]

    def workspace(self):
        """One SQLite read point, no filesystem inventory/audit verification or write."""
        with self.db() as db:
            db.execute('BEGIN')
            head = db.execute('SELECT sequence,hash FROM audit ORDER BY sequence DESC LIMIT 1').fetchone()
            resources = db.execute('SELECT * FROM resources ORDER BY updated_at DESC').fetchall()
            releases = db.execute('SELECT * FROM releases ORDER BY created_at DESC').fetchall()
            launchers = {row['release_id']:row['payload'] for row in db.execute('SELECT release_id,payload FROM test_launchers')}
            active = db.execute('SELECT release_id,generation FROM active WHERE singleton=1').fetchone()
        if not active:raise DomainError('Active metadata singleton is missing.',409)
        active_release = next((row for row in releases if row['id']==active['release_id']),None)
        if active['release_id'] and not active_release:raise DomainError('Active pointer references missing release metadata.',409)
        data = {'resources':[self._resource_value(row) for row in resources],
                'releases':[self._release_value(row,launchers.get(row['id']),include_manifest=False) for row in releases],
                'active':self._active_value(active,self._release_value(active_release,launchers.get(active_release['id'])) if active_release else None)}
        anchor = dict(head) if head else {'sequence':0,'hash':'0'*64}
        return {**data,'snapshot':{'schema_version':1,'captured_at':now(),'audit_head':anchor,
            'state_sha256':digest([anchor,data]),'consistency':'sqlite_read_transaction',
            'files_verified':False,'audit_verified':False,'signed':False}}

    def manifest(self, root):
        return [{'path': relative, 'size': path.stat().st_size, 'sha256': sha256(path)} for relative, path in tree_files(root)]

    def _snapshot(self, source, folder, omit=None, progress=None):
        if progress:progress('building',10,'Berechne unveraendertes Quellmanifest')
        before = self.manifest(source)
        if os.name == 'nt' and any(len(str(folder / entry['path'])) >= 255 for entry in before):
            raise DomainError('Destination paths exceed the Windows pilot limit; use a shorter data directory.')
        folder.mkdir(parents=True, exist_ok=False)
        for index,entry in enumerate(before):
            if omit and omit(entry['path']):
                continue
            source_file = source / entry['path']
            offline_path(source_file)
            target = offline_path(folder / entry['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)
            if sha256(target) != entry['sha256']:
                raise DomainError('Source changed during import; incomplete release retained as failed.', 409)
            if progress and (index%50==0 or index==len(before)-1):
                progress('building',15+int(60*(index+1)/max(len(before),1)),f'{index+1} / {len(before)} Quelldateien verarbeitet')
        expected = [entry for entry in before if not omit or not omit(entry['path'])]
        if progress:progress('verifying',80,'Pruefe Quelle und neue Kopie nochmals gegen SHA')
        if before != self.manifest(source) or expected != self.manifest(folder):
            raise DomainError('Source changed during import.', 409)
        return expected

    def preview_normalization(self, source_key, nx_version, legacy_version, defaults=None, resolutions=None,
                              legacy_omissions=None, source_path=None, target=None, target_path=None,
                              reference_resolutions=None):
        if bool(target) != bool(target_path):
            raise DomainError('Target alias and target path must be provided together.')
        source = self.folders.source(source_key, source_path)
        folder = self.folders.destination(target, target_path, source) if target else None
        if not offline_path(source / 'MACH' / 'resource').is_dir():
            raise DomainError('Der Quellordner hat keinen NX-Netzwerkordner-Aufbau (MACH/resource fehlt).')
        _, report = normalization_plan(source, self.standards, nx_version,
                                       legacy_version, defaults, resolutions, legacy_omissions,
                                       reference_resolutions=reference_resolutions)
        report['source_folder'] = str(source)
        if folder:
            report['destination'] = str(folder)
            report['host_destination'] = self.host_folder(folder)
        report['preview_hash'] = digest(report)
        return report

    def import_release(self, source_key, nx_version, name, actor, parent=None, normalization=None, defaults=None,
                       source_path=None, target=None, target_path=None, preview_hash=None,progress=None,on_started=None):
        with self.lock:
            if progress:progress('planning',5,'Pruefe gebundene Vorschau und Eingaben vor dem Kopieren')
            export_plan = None
            if parent:
                if source_path or target or target_path:
                    raise DomainError('Derived releases use managed storage, not custom import paths.')
                previous = self.get_release(parent)
                if previous['status'] in ('building', 'failed'):
                    raise DomainError('Cannot derive from an incomplete release.', 409)
                source = self.release_folder(parent)
                if self.manifest(source) != previous['manifest']:
                    raise DomainError('Parent release has been changed outside the API.', 409)
                nx_version = previous['nx_version']
                export_plan, export_report = self._build_export_plan(parent, defaults or {})
                if preview_hash and preview_hash != export_report['preview_hash']:
                    raise DomainError('Export preview changed; review the current preview first.', 409)
                if not export_report['can_export']:
                    error = export_report['conflicts'][0]
                    raise DomainError(error['reason'], error.get('status', 409))
            else:
                source = self.folders.source(source_key, source_path)
            if bool(target) != bool(target_path):
                raise DomainError('Target alias and target path must be provided together.')
            folder = self.folders.destination(target, target_path, source) if target else None
            outputs, normalization_report = {}, None
            if normalization:
                if parent:
                    raise DomainError('Normalize a configured source first, not an immutable parent release.')
                outputs, normalization_report = normalization_plan(source, self.standards, nx_version,
                    normalization['legacy_version'], normalization.get('defaults'), normalization.get('resolutions'), normalization.get('legacy_omissions'),
                    reference_resolutions=normalization.get('reference_resolutions'))
                normalization_report['source_folder'] = str(source)
                if folder:
                    normalization_report['destination'] = str(folder)
                    normalization_report['host_destination'] = self.host_folder(folder)
                preview_hash = digest(normalization_report)
                if normalization.get('preview_hash') != preview_hash:
                    raise DomainError('Normalization preview changed; review the current preview first.', 409)
                if not normalization_report['can_import']:
                    raise DomainError('Resolve all database/schema conflicts before import.', 409)
                normalization_report['preview_hash'] = preview_hash
            release_id = str(uuid.uuid4())
            if folder is None:
                folder = self.release_folder(release_id)
                while folder.parent.exists():
                    release_id = str(uuid.uuid4())
                    folder = self.release_folder(release_id)
            with self.db() as db:
                db.execute('INSERT INTO releases(id,name,nx_version,source,parent_id,status,created_at,folder) VALUES(?,?,?,?,?,?,?,?)',
                           (release_id, name, nx_version, str(source), parent, 'building', now(), str(folder)))
                self.audit(db, actor, 'release.started', {'id': release_id, 'source': str(source),
                                                        'folder': str(folder), 'parent': parent})
            try:
                if on_started:on_started(release_id)
                # Omit only the optional palette and, when normalized, legacy
                # database files. Originals and historical releases stay intact.
                omit = lambda relative: relative.casefold() == OPTIONAL_PALETTE.casefold() or (normalization and legacy_database(relative))
                manifest = self._snapshot(source, folder, omit,progress) if progress else self._snapshot(source,folder,omit)
                if parent and manifest != [entry for entry in previous['manifest'] if not omit(entry['path'])]:
                    raise DomainError('Parent changed after export planning.', 409)
                if normalization:
                    if progress:progress('building',82,'Schreibe ausschliesslich gepruefte Standardbibliotheken in die neue Kopie')
                    for path, text in outputs.items():
                        target_file = offline_path(folder / path)
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        # Preserve exactly the reviewed UTF-8/BOM bytes on Windows:
                        # text mode would translate LF and even turn preserved
                        # CRLF comments into CRCRLF, invalidating result_hashes.
                        target_file.write_bytes(text.encode('utf-8-sig'))
                        if sha256(target_file) != normalization_report['result_hashes'][path]:
                            raise DomainError('Normalized library differs from its reviewed bytes.', 409)
                    # Recompute the reviewed plan after copying: changed library
                    # inputs must never produce a release from a stale preview.
                    _, fresh = normalization_plan(source, self.standards, nx_version,
                        normalization['legacy_version'], normalization.get('defaults'), normalization.get('resolutions'), normalization.get('legacy_omissions'),
                        reference_resolutions=normalization.get('reference_resolutions'))
                    fresh['source_folder'] = str(source)
                    if target:
                        fresh['destination'] = str(folder)
                        fresh['host_destination'] = self.host_folder(folder)
                    if digest(fresh) != normalization_report['preview_hash']:
                        raise DomainError('Library inputs changed during normalization.', 409)
                    manifest = self.manifest(folder)
                catalog = export_plan['catalog'] if export_plan else []
                actions = export_plan['actions'] if export_plan else []
                exports = export_plan['exported_tools'] if export_plan else {}
                native_exports = export_plan['exported_native'] if export_plan else {}
                if export_plan:
                    if progress:progress('building',82,'Exportiere gepruefte Stammdaten und eigene Dateipakete')
                    self._apply_export_plan(folder, export_plan)
                if actions:
                    references = reference_report(folder, self.standards, nx_version)
                    if references['errors']:
                        raise DomainError('Export has missing/invalid references: ' + canonical(references['errors'][:10]), 409)
                if progress:progress('verifying',90,'Pruefe fertigen Datenstand und revisionsgebundenen Katalog')
                manifest = self.manifest(folder)
                if export_plan:
                    current={(r['id'],r['revision']) for r in self.resources()}
                    if current!={(r['id'],r['revision']) for r in export_report['catalog_revisions']}:
                        raise DomainError('Catalog changed during export; review a new preview. Partial output retained.',409)
                if progress:progress('registering',95,'Registriere neuen Entwurf und isoliertes NX-Testpaket')
                launcher = self._create_test_launcher(release_id, nx_version, folder, manifest)
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if export_plan:
                        current={(row['id'],row['revision']) for row in db.execute('SELECT id,revision FROM resources')}
                        if current!={(r['id'],r['revision']) for r in export_report['catalog_revisions']}:
                            raise DomainError('Catalog changed before export commit; review a new preview.',409)
                    if export_plan:
                        from .template_refs import verify as verify_template_dependencies
                        verify_template_dependencies(self,folder,export_plan['template_references'])
                        from .fixture_refs import verify as verify_fixture_dependencies
                        verify_fixture_dependencies(folder,export_plan['fixture_references'])
                    db.execute('UPDATE releases SET status=?,manifest=?,catalog=? WHERE id=?',
                               ('draft', canonical(manifest), canonical({'resources': catalog, 'exported_tools': exports,
                                   'exported_native': native_exports, 'export_actions': actions,
                                   'exported_file_packages':export_plan.get('exported_file_packages',{}) if export_plan else {},
                                   'schema_defaults':export_report['defaults'] if export_plan else {},
                                   'migration':(previous['catalog'] or {}).get('migration') if parent else None,
                                   'migration_exclusions':(previous['catalog'] or {}).get('migration_exclusions',{}) if parent else {},
                                   'reference_reviews':(previous['catalog'] or {}).get('reference_reviews',{}) if parent else {},
                                   'export_preview': export_report if export_plan else None, 'normalization': normalization_report,
                                   'optional_palette_omitted': OPTIONAL_PALETTE}), release_id))
                    self.audit(db, actor, 'release.imported', {'id': release_id, 'files': len(manifest),
                        'catalog': [{'id': r['id'], 'revision': r['revision']} for r in catalog],
                        'export_preview_hash': export_report['preview_hash'] if export_plan else None})
                    if launcher:
                        db.execute('INSERT INTO test_launchers(release_id,payload) VALUES(?,?)', (release_id, canonical(launcher)))
                        self.audit(db, actor, 'release.test_launcher.created', launcher)
                return self.get_release(release_id)
            except Exception as error:
                failure = {'integrity_passed': False, 'errors': [str(error)[:1500]], 'checked_at': now()}
                with self.db() as db:
                    db.execute('UPDATE releases SET status=?,validation=? WHERE id=?', ('failed', canonical(failure), release_id))
                    self.audit(db, actor, 'release.failed', {'id': release_id, 'error': str(error)[:1500]})
                raise

    def _create_test_launcher(self, release_id, nx_version, folder, manifest, ui_profile=None):
        if nx_version != '2512':
            return None  # No version-specific NX environment adapter exists yet.
        host = self.host_folder(folder) or (str(folder) if os.name == 'nt' else None)
        files, config = package_files(release_id, manifest, host, ui_profile) if ui_profile is not None else package_files(release_id, manifest, host)
        revision = content_revision(files)
        directory = offline_path(self.root / 'test-launchers' / release_id / revision)
        package = offline_path(directory / 'NX2512_Test.zip')
        if directory.exists():
            # Recover a complete, identical artifact after a DB update failure or template rollback.
            # Never overwrite an old revision (including a changed or incomplete one).
            existing = dict(tree_files(directory))
            if set(existing) != set(files) | {'NX2512_Test.zip'} or any(
                    existing[name].read_bytes() != data for name, data in files.items()):
                raise DomainError('Existing test package revision is changed or incomplete.', 409)
            try:
                with zipfile.ZipFile(package) as stored:
                    entries = stored.infolist()
                    if (len(entries) != len(files) or {entry.filename for entry in entries} != set(files) or
                            any(entry.file_size != len(files[entry.filename]) or stored.read(entry) != files[entry.filename]
                                for entry in entries)):
                        raise DomainError('Existing test archive does not match its revision.', 409)
            except zipfile.BadZipFile as error:
                raise DomainError('Existing test archive is invalid.', 409) from error
        else:
            directory.mkdir(parents=True, exist_ok=False)
            for name, content in files.items():
                offline_path(directory / name).write_bytes(content)
            package.write_bytes(archive(files))
        return {'release_id': release_id, 'status': 'ready' if host else 'needs_windows_path',
                'folder': str(directory), 'starter': str(directory / 'Start-NX2512-Test.cmd'),
                'windows_folder': host, 'nx_install_root': config['NXInstallRoot'],
                'test_root': config['TestRoot'], 'file_count': len(manifest),
                'manifest_sha256': config['ManifestSha256'], 'package_sha256': sha256(package),
                'template_sha256': template_sha256(), 'package_revision': revision, 'created_at': now(),
                **({'ui_profile':ui_profile} if ui_profile is not None else {})}

    def adopt_migration(self, folder, name, nx_version, manifest, migration, actor):
        """Register only a completed, manifest-bound job output. Never overwrite/import source."""
        with self.lock:
            folder=offline_path(folder)
            self.folders._target_safe(folder,releases=self.folders._releases(exclude_job=migration['job_id']))
            if self.manifest(folder)!=manifest:
                raise DomainError('Migration output differs from its reviewed manifest.',409)
            source_id=migration.get('source_release_id')
            catalog={'resources':[],'exported_tools':{},'exported_native':{},'exported_file_packages':{},'migration':migration,
                     'migration_exclusions':{},'schema_defaults':migration.get('defaults',{}),'reference_reviews':{}}
            if source_id:
                previous=self.get_release(source_id);old_folder=self.release_folder(source_id)
                from .filepackages import rebase_ownership
                catalog['exported_file_packages']=rebase_ownership(self,previous,manifest,nx_version)
                catalog['resources']=(previous['catalog'] or {}).get('resources',[])
                ownership={family:dict((previous['catalog'] or {}).get(family,{})) for family in ('exported_tools','exported_native')}
                imported={resource['id']:resource for resource in catalog['resources']}
                imported.update({resource['id']:resource for resource in self.resources()
                                 if (resource.get('origin') or {}).get('release_id')==source_id})
                for resource in imported.values():
                    origin=resource.get('origin') or {}
                    if origin.get('editable') and resource['id'] not in ownership['exported_native']:
                        ownership['exported_native'][resource['id']]={'revision':resource['revision'],
                            'library_id':origin['library_id'],'key':origin['key'],'records_hash':origin['records_hash']}
                for family in ('exported_tools','exported_native'):
                    for resource_id,owned in ownership[family].items():
                        identifier,key=('tool-metric',resource_identity(owned)[1]) if family=='exported_tools' else (owned['library_id'],owned['key'])
                        old_table=NativeTable.read(old_folder/SPECS[identifier]['path'])
                        target_table=NativeTable.read(folder/SPECS[identifier]['path'])
                        old_records=old_table.groups().get(key)
                        expected=owned.get('records_hash')
                        if expected is None and family=='exported_tools':
                            legacy=legacy_cam_table(owned['cam']);expected=records_hash(project_records(legacy.records,legacy,old_table))
                        if not old_records or records_hash(old_records)!=expected:
                            raise DomainError('Source catalog ownership does not match the immutable source library.',409)
                        try:
                            projected=project_records(old_records,old_table,target_table,migration.get('defaults',{}).get(identifier))
                            reviewed=[]
                            for decision in migration.get('reference_resolutions',[]):
                                if decision['library']!=identifier or decision['key']!=key:continue
                                count=0
                                for record in projected:
                                    if record['values'].get(decision['field'])==decision['previous']:
                                        record['values'][decision['field']]=decision['value'];count+=1
                                if count:reviewed.append(decision)
                            actual=target_table.groups().get(key)
                            matched=actual and records_hash(projected)==records_hash(actual)
                        except DomainError:matched=False
                        if matched:
                            catalog[family][resource_id]={**owned,'records_hash':records_hash(actual)}
                            if reviewed:catalog['reference_reviews'][resource_id]={'source_revision':owned.get('revision'),
                                'decisions':reviewed,'catalog_changed':False,
                                'note':'The new NX folder has reviewed references. Update the catalog explicitly before exporting this object again; ERP/current catalog values were not changed.'}
                        else:catalog['migration_exclusions'][resource_id]={'library_id':identifier,'key':key,
                            'migration_exclusion':True,
                            'reason':'Customer group was omitted/replaced during migration. Do not automatically overwrite the new standard; rerun migration with an explicit customer choice to restore it.'}
            release_id=str(uuid.uuid4())
            launcher=self._create_test_launcher(release_id,nx_version,folder,manifest)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO releases(id,name,nx_version,source,parent_id,status,manifest,catalog,created_at,folder) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (release_id,name,nx_version,migration['source_folder'],source_id,'draft',canonical(manifest),
                     canonical(catalog),now(),str(folder)))
                if launcher:db.execute('INSERT INTO test_launchers(release_id,payload) VALUES(?,?)',(release_id,canonical(launcher)))
                self.audit(db,actor,'migration.release.created',{'release_id':release_id,'migration':migration,'files':len(manifest)})
            return self.get_release(release_id)

    def sidt_root(self,version):
        with self.db() as db:
            selection=db.execute('SELECT job_id FROM sidt_inputs WHERE version=?',(version,)).fetchone()
            if not selection:return self.sidts.get(version)
            job=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(selection['job_id'],)).fetchone()
        if not job:raise DomainError('Selected extraction job is missing.',409)
        payload=decode_migration_report(job['payload'])
        if payload.get('mode')!='extract' or payload.get('status')!='completed':raise DomainError('Selected SIDT extraction is incomplete.',409)
        identifier=selection['job_id']
        try:valid_id=isinstance(identifier,str) and str(uuid.UUID(identifier))==identifier
        except (ValueError,TypeError,AttributeError):valid_id=False
        if (not valid_id or payload.get('id')!=identifier or not isinstance(payload.get('request'),dict)
                or payload['request'].get('version')!=version):
            raise DomainError('Selected extraction job does not match its SIDT version/identity.',409)
        path=offline_path(self.root/'upgrades'/identifier/'report.json')
        report=read_migration_report(path,payload.get('report_sha256'))
        from .recovery import extraction_paths
        files,sidt=extraction_paths(self,report,identifier,expected_version=version)
        if report['version']!=version or self.manifest(files)!=report['manifest']:
            raise DomainError('Selected SIDT extraction differs from its immutable manifest.',409)
        return sidt

    def prepare_test_launcher(self, release_id, actor):
        with self.lock:
            release = self.get_release(release_id)
            if release['status'] in ('building', 'failed'):
                raise DomainError('An incomplete release cannot have a test launcher.', 409)
            if release['nx_version'] != '2512':
                raise DomainError('Test launchers currently support NX2512 only.')
            folder = self.release_folder(release_id)
            if self.manifest(folder) != release['manifest']:
                raise DomainError('Release changed outside the API; test package creation blocked.', 409)
            previous = release['test_launcher']
            if previous:
                self.test_launcher_package(release_id)
                if previous.get('template_sha256') == template_sha256():
                    return previous
            launcher = self._create_test_launcher(release_id, release['nx_version'], folder, release['manifest'],
                ui_profile=previous.get('ui_profile') if previous else None)
            with self.db() as db:
                db.execute('INSERT INTO test_launchers(release_id,payload) VALUES(?,?) ON CONFLICT(release_id) DO UPDATE SET payload=excluded.payload', (release_id, canonical(launcher)))
                if previous:
                    self.audit(db, actor, 'release.test_launcher.refreshed', {'previous': previous, 'current': launcher})
                else:
                    self.audit(db, actor, 'release.test_launcher.created', launcher)
            return launcher

    def prepare_missing_test_launchers(self):
        with self.db() as db:
            rows = db.execute(
                "SELECT r.id,t.payload FROM releases r LEFT JOIN test_launchers t ON t.release_id=r.id "
                "WHERE r.nx_version='2512' AND r.status IN ('draft','validated','approved')").fetchall()
        fingerprint = template_sha256()
        ids = [row['id'] for row in rows if not row['payload'] or
               json.loads(row['payload']).get('template_sha256') != fingerprint]
        errors = []
        for release_id in ids:
            try:
                self.prepare_test_launcher(release_id, 'system')
            except (DomainError, OSError) as error:
                errors.append({'release_id': release_id, 'error': str(error)[:1500]})
        return errors

    def test_launcher_package(self, release_id):
        release = self.get_release(release_id)
        if release['status'] in ('building', 'failed') or not release['test_launcher']:
            raise DomainError('No completed test launcher is available for this release.', 409)
        directory = self.root / 'test-launchers' / release_id
        revision = release['test_launcher'].get('package_revision')
        if revision is not None:
            if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision):
                raise DomainError('Invalid test package revision.', 409)
            directory = directory / revision
        package = offline_path(directory / 'NX2512_Test.zip')
        if not package.is_file() or sha256(package) != release['test_launcher']['package_sha256']:
            raise DomainError('Stored test package changed outside the API.', 409)
        from .launchprofiles import validate_profile
        profile=validate_profile(release['test_launcher'].get('ui_profile'),release['manifest'])
        try:
            with zipfile.ZipFile(package) as stored:
                entry=stored.getinfo('release_test.json')
                if entry.file_size>262144:raise DomainError('Test package configuration is too large.',409)
                config=json.loads(stored.read(entry))
                if config.get('UIProfile')!=profile:raise DomainError('Stored test package UI selection differs from metadata.',409)
        except (zipfile.BadZipFile,KeyError,ValueError) as error:raise DomainError('Invalid test package configuration.',409) from error
        return package

    def preview_export(self, release_id, defaults=None):
        with self.lock:
            return self._build_export_plan(release_id, defaults or {})[1]

    def _build_export_plan(self, release_id, defaults):
        """Plan the actual export in memory. No folder, database or audit writes."""
        release = self.get_release(release_id)
        folder = self.release_folder(release_id)
        if release['status'] in ('building', 'failed') or self.manifest(folder) != release['manifest']:
            raise DomainError('Export requires a complete, unchanged parent release.', 409)
        catalog, previous = self.resources(), release['catalog'] or {}
        inherited=copy.deepcopy(previous.get('schema_defaults',(previous.get('migration') or {}).get('defaults',{})))
        for identifier,blocks in defaults.items():
            if not isinstance(blocks,dict):raise DomainError('Defaults must be library -> FORMAT block -> column -> string.')
            for block,fields in blocks.items():
                if not isinstance(fields,dict):raise DomainError('Defaults must be library -> FORMAT block -> column -> string.')
                inherited.setdefault(identifier,{}).setdefault(block,{}).update(fields)
        defaults=inherited
        exclusions=previous.get('migration_exclusions',{})
        plan = {'catalog': catalog, 'tables': {}, 'assets': {}, 'actions': [],
                'parent_files':{entry['path'].casefold():entry for entry in release['manifest']},
                'file_owners':{},'asset_assignments':{},
                'exported_tools': previous.get('exported_tools', {}),
                'exported_native': previous.get('exported_native', {})}
        conflicts, columns, targets, claims = [], {}, {}, {}
        expected_inputs = {entry['path'].casefold(): entry['sha256'] for entry in release['manifest']}
        def target(identifier):
            if identifier not in targets:
                targets[identifier] = NativeTable.read(folder / spec_for(identifier)['path'])
                if targets[identifier].source_sha256 != expected_inputs.get(spec_for(identifier)['path'].casefold()):
                    raise DomainError('Native library changed while planning the export: ' + identifier, 409)
            return targets[identifier]
        plan['load_table'] = target
        for identifier, blocks in defaults.items():
            spec_for(identifier)
            if not isinstance(blocks, dict):
                raise DomainError('Defaults must be library -> FORMAT block -> column -> string.')
            table = target(identifier)
            schemas = {b['id']: b for b in table.blocks}
            for block, fields in blocks.items():
                if block not in schemas or not isinstance(fields, dict):
                    raise DomainError('Unknown default FORMAT block: ' + identifier + '/' + block)
                for field, value in fields.items():
                    if field not in schemas[block]['fields'] or not isinstance(value, str) or any(c in value for c in '|\r\n\x00'):
                        raise DomainError('Invalid explicit column default: ' + identifier + '/' + block + '.' + field)
        for resource in catalog:
            identity = resource_identity(resource)
            if identity:
                if identity in claims:
                    conflicts.append({'library_id': identity[0], 'key': identity[1],
                        'resource_ids': [claims[identity], resource['id']], 'status': 409,
                        'reason': 'Multiple catalog owners claim the same NX library identifier.'})
                claims[identity] = resource['id']
            if resource['id'] in exclusions:
                plan['actions'].append({'resource_id':resource['id'],'revision':resource['revision'],
                    **exclusions[resource['id']],'action':'catalog_only'})
                continue
            if resource['cam_status'] == 'draft':
                plan['actions'].append({'resource_id': resource['id'], 'name': resource['name'],
                    'revision': resource['revision'], 'library_id': identity[0] if identity else None,
                    'key': identity[1] if identity else None, 'action': 'deferred',
                    'reason': 'Draft not exported; existing data in the parent stays unchanged.'})
                continue
            if resource['cam_status'] != 'approved' or not identity:
                if resource['cam_status'] == 'approved':
                    plan['actions'].append({'resource_id': resource['id'], 'revision': resource['revision'],
                        'library_id': None, 'key': None, 'action': 'catalog_only',
                        'reason': 'Catalog data only; no native NX export adapter assigned.'})
                continue
            try:
                cam = resource['cam']
                source = cam_table(cam) if cam.get('library_id') else legacy_cam_table(cam)
                table = target(identity[0])
                for record in source.records:
                    block = projection_block(record, source, table)
                    for field in block['fields']:
                        if field not in record['values']:
                            confirmed = field in defaults.get(identity[0], {}).get(block['id'], {})
                            entry = columns.setdefault((identity[0], block['id'], field), {
                                'library_id': identity[0], 'block_id': block['id'], 'field': field,
                                'confirmed': confirmed, 'value': defaults.get(identity[0], {}).get(block['id'], {}).get(field),
                                'resource_ids': []})
                            if resource['id'] not in entry['resource_ids']:
                                entry['resource_ids'].append(resource['id'])
            except DomainError as error:
                conflicts.append({'resource_id': resource['id'], 'library_id': identity[0],
                                  'key': identity[1], 'reason': str(error), 'status': error.status})
        for entry in columns.values():
            if not entry['confirmed']:
                conflicts.append({**entry, 'status': 400,
                    'reason': 'Explicit default required for new column ' + entry['block_id'] + '.' + entry['field']})
        references = None
        if not conflicts:
            try:
                export_catalog=[resource for resource in catalog if resource['id'] not in exclusions]
                plan['exported_tools'] = self._export_legacy_tools(folder, export_catalog,
                    previous.get('exported_tools', {}), release['nx_version'], defaults, plan['actions'], plan)
                plan['exported_native'] = self._export_native(folder, export_catalog,
                    previous.get('exported_native', {}), release['nx_version'], defaults, plan['actions'], plan)
                # Freeze all reference inputs at their manifest-bound bytes too;
                # do not reread mutable files or include untracked libraries.
                for identifier, spec in SPECS.items():
                    if spec['path'].casefold() in expected_inputs:
                        target(identifier)
                references = reference_report(folder, self.standards, release['nx_version'],
                                              {**targets, **plan['tables']}, complete_override=True)
                for error in references['errors']:
                    conflicts.append({**error, 'status': 409,
                        'resource_id': claims.get((error.get('library'),error.get('key'))),
                        'reason': 'Export has missing/invalid references: ' + canonical(error)})
            except DomainError as error:
                conflicts.append({'reason': str(error), 'status': error.status})
        names = {r['id']: r['name'] for r in catalog}
        for action in plan['actions']:
            action['name'] = names[action['resource_id']]
        assets = [{key: value for key, value in asset.items() if key != 'source'}
                  for asset in plan['assets'].values()]
        outputs = [{'path': SPECS[identifier]['path'],
                    'sha256': hashlib.sha256(table.text.encode('utf-8-sig')).hexdigest()}
                   for identifier, table in sorted(plan['tables'].items())]
        from .filepackages import plan_export as file_package_plan
        try:file_actions=file_package_plan(self,release,catalog,previous,plan)
        except DomainError as error:
            file_actions=[];conflicts.append({'reason':str(error),'status':error.status})
            plan['file_package_planning_error']=str(error)
        from .safety import export_path_casing
        try:
            removed={item['path'].casefold() for item in plan.get('file_deletions',[])}
            paths=[item['path'] for item in release['manifest'] if item['path'].casefold() not in removed]
            paths.extend(SPECS[identifier]['path'] for identifier in plan['tables'])
            paths.extend(item['path'] for item in plan['assets'].values())
            export_path_casing(paths)
        except DomainError as error:
            conflicts.append({'reason':str(error),'status':error.status})
        from .template_refs import planned as template_dependencies
        template_report=template_dependencies(self,release,plan)
        conflicts.extend({**item,'status':409} for item in template_report['errors'] if not item.get('upstream_conflict'))
        from .fixture_refs import planned as fixture_dependencies
        fixture_report=fixture_dependencies(self,release,plan)
        conflicts.extend({**item,'status':409} for item in fixture_report['errors'])
        assets = [{key:value for key,value in item.items() if key!='source'} for item in plan['assets'].values()]
        report = {'release_id': release_id, 'nx_version': release['nx_version'],'file_packages':file_actions,'native_preview_contract':1,
                  'reference_reviews': previous.get('reference_reviews',{}),'template_references':template_report,'fixture_references':fixture_report,
                  'parent_folder': str(folder), 'defaults': defaults,
                  'actions': plan['actions'], 'file_deletions':plan.get('file_deletions',[]), 'new_columns': list(columns.values()),
                  'conflicts': conflicts, 'references': references, 'assets': assets,
                  'outputs': outputs, 'can_export': not conflicts,
                  'catalog_revisions': [{'id': r['id'], 'revision': r['revision']} for r in catalog],
                  'parent_manifest_sha256': digest(release['manifest']),
                  'automatic_approval': False}
        report['preview_hash'] = digest([report, catalog, previous])
        return plan, report

    def _apply_export_plan(self, folder, plan):
        for item in plan.get('file_deletions',[]):
            target=offline_path(folder/item['path'])
            if not target.is_file() or sha256(target)!=item['sha256']:raise DomainError('Owned file changed before retirement.',409)
            target.unlink()  # Only the newly created derived copy; immutable parent retains original bytes.
        for identifier, table in plan['tables'].items():
            # Binary write avoids platform newline translation: bytes must match preview SHA.
            offline_path(folder / SPECS[identifier]['path']).write_bytes(table.text.encode('utf-8-sig'))
        for asset in plan['assets'].values():
            target = offline_path(folder / asset['path'])
            if sha256(offline_path(asset['source'])) != asset['sha256']:
                raise DomainError('Asset changed after export planning.', 409)
            target.parent.mkdir(parents=True, exist_ok=True)
            if asset.get('file_package') and asset['action']=='updated':
                if not target.is_file() or sha256(target)!=asset['previous_sha256']:raise DomainError('Owned copy changed before package update.',409)
                shutil.copyfile(asset['source'],target)
            elif not target.exists():
                shutil.copyfile(asset['source'], target)
            if sha256(target) != asset['sha256']:
                raise DomainError('Exported asset contents do not match the reviewed plan.', 409)

    def _copy_resource_assets(self, folder, resource, spec, plan):
        if resource.get('file_package'):return  # Explicit package paths replace guessed graphics placement.
        metadata={asset_id:self.asset(asset_id) for asset_id in resource['asset_ids']}
        if resource.get('model_package'):
            from .results import Results
            Results(self).validate_assignment(resource,[value[0] for value in metadata.values()])
        bindings=graphics_bindings(resource,[value[0] for value in metadata.values()],spec)
        for asset_id in resource['asset_ids']:
            if asset_id not in bindings:continue  # STEP/source documents stay private in explicit package mode.
            asset, source = metadata[asset_id]
            relative = bindings[asset_id]
            inherited=plan.get('parent_files',{}).get(relative.casefold())
            if inherited and inherited['path']!=relative:
                raise DomainError('Keep the exact inherited graphics path casing; binary references are not renamed.',409)
            target = offline_path(folder / relative)
            planned = plan['assets'].get(relative.casefold())
            if planned and planned['sha256'] != asset['sha256']:
                raise DomainError('Graphics filename has conflicting planned contents.', 409)
            if target.exists():
                if sha256(target) != asset['sha256']:
                    raise DomainError('Graphics filename already has different contents.', 409)
            plan['assets'][relative.casefold()] = {**asset, 'path': relative, 'source': source,
                                                'action': 'unchanged' if target.exists() else 'created'}
            plan.setdefault('file_owners',{}).setdefault(relative.casefold(),set()).add(resource['id'])
            plan.setdefault('asset_assignments',{}).setdefault(resource['id'],set()).add(asset_id)

    def _export_legacy_tools(self, folder, catalog, previous_exports, nx_version, defaults, actions, plan):
        exports, table, groups = dict(previous_exports), None, None
        for resource in catalog:
            previous = previous_exports.get(resource['id'])
            if resource['kind'] != 'tool' or 'library_id' in resource['cam'] or resource['cam_status'] == 'draft':
                continue
            if resource['cam_status'] == 'retired' and not previous:
                continue
            if table is None:
                path = offline_path(folder / TOOL_DATABASE)
                if not path.is_file():
                    raise DomainError('Release has no supported metric NX tool database.')
                table = plan['load_table']('tool-metric')
                groups = table.groups()
            source = legacy_cam_table(previous['cam'] if resource['cam_status'] == 'retired' else resource['cam'])
            records = project_records(source.records, source, table, defaults.get('tool-metric')) if resource['cam_status'] != 'retired' else None
            key = next(iter(source.groups()))
            expected = None
            if previous:
                old = legacy_cam_table(previous['cam'])
                if next(iter(old.groups())) != key:
                    raise DomainError('Exported tool identifiers are immutable; create a new resource.', 409)
                expected = previous.get('records_hash') or records_hash(project_records(old.records, old, table, defaults.get('tool-metric')))
            existing = groups.get(key)
            if existing and not previous:
                raise DomainError('NX library reference already exists; no automatic overwrite.')
            if existing and records_hash(existing) != expected:
                raise DomainError('Exported tool differs from its tracked revision.', 409)
            if resource['cam_status'] == 'retired':
                groups.pop(key, None)
                exports.pop(resource['id'], None)
                actions.append({'resource_id': resource['id'], 'revision': resource['revision'], 'library_id': 'tool-metric', 'key': key, 'action': 'retired'})
                continue
            geometry_check(resource, [self.asset(a)[0] for a in resource['asset_ids']], nx_version)
            groups[key] = records
            self._copy_resource_assets(folder, resource, SPECS['tool-metric'], plan)
            exports[resource['id']] = {**resource, 'records_hash': records_hash(records)}
            operation = 'unchanged' if existing and records_hash(existing) == records_hash(records) else ('updated' if existing else 'created')
            actions.append({'resource_id': resource['id'], 'revision': resource['revision'], 'library_id': 'tool-metric', 'key': key, 'action': operation})
        if table is not None:
            plan['tables']['tool-metric'] = NativeTable(table.render(groups))
        return exports

    def _export_native(self, folder, catalog, previous_exports, nx_version, defaults, actions, plan):
        actions = actions if actions is not None else []
        exports, changed = dict(previous_exports), {}
        for resource in catalog:
            cam = resource['cam']
            origin = resource.get('origin') or {}
            old_export = previous_exports.get(resource['id'], {})
            if resource['cam_status'] == 'retired' and (old_export or origin):
                if origin and not origin.get('editable'):
                    exports.pop(resource['id'], None)  # Retire catalog entry only, never remove Siemens data.
                    actions.append({'resource_id': resource['id'], 'revision': resource['revision'],
                        'library_id': origin['library_id'], 'key': origin['key'], 'action': 'catalog_only',
                        'reason': 'Siemens standard object stays in the NX database.'})
                    continue
                owned = old_export or origin
                spec = spec_for(owned['library_id'])
                path = offline_path(folder / spec['path'])
                if not path.is_file():
                    raise DomainError('Tracked native library is missing.', 409)
                if spec['id'] not in changed:
                    table = plan['tables'].get(spec['id']) or plan['load_table'](spec['id'])
                    changed[spec['id']] = (table, table.groups())
                table, groups = changed[spec['id']]
                existing = groups.get(owned['key'])
                if existing and records_hash(existing) != owned['records_hash']:
                    raise DomainError('Retirement conflicts with the tracked native object.', 409)
                groups.pop(owned['key'], None)
                exports.pop(resource['id'], None)
                actions.append({'resource_id': resource['id'], 'revision': resource['revision'], 'library_id': spec['id'], 'key': owned['key'], 'action': 'retired'})
                continue
            if resource['cam_status'] != 'approved' or 'library_id' not in cam:
                continue
            spec = spec_for(cam['library_id'])
            metadata = [self.asset(a)[0] for a in resource['asset_ids']]
            geometry_check(resource, metadata, nx_version)
            if resource['kind'] != spec['kind']:
                raise DomainError('Catalog kind and native library kind differ.')
            target_path = offline_path(folder / spec['path'])
            if not target_path.is_file():
                raise DomainError('Target release does not contain native library ' + spec['id'])
            if spec['id'] not in changed:
                changed[spec['id']] = (plan['tables'].get(spec['id']) or plan['load_table'](spec['id']), None)
            table, groups = changed[spec['id']]
            if groups is None:
                groups = table.groups()
                changed[spec['id']] = (table, groups)
            source = cam_table(cam)
            records = project_records(source.records, source, table, defaults.get(spec['id']))
            key = next(iter(source.groups()))
            existing = groups.get(key)
            if origin and (origin.get('key') != key or origin.get('library_id') != spec['id']):
                raise DomainError('Imported native identifiers/library are immutable; create a new resource.', 409)
            if old_export and (old_export['key'] != key or old_export['library_id'] != spec['id']):
                raise DomainError('Exported native identifiers/library are immutable; create a new resource.', 409)
            if existing and not origin and not old_export:
                raise DomainError('Native identifier already exists; import the object through catalog preview.', 409)
            if existing and records_hash(existing) != records_hash(records):
                if origin and not origin.get('editable'):
                    raise DomainError('Siemens standard catalog objects may not be overwritten.', 409)
                expected = old_export.get('records_hash') or origin.get('records_hash')
                if (not origin.get('editable') and not old_export) or records_hash(existing) != expected:
                    raise DomainError('Existing native object conflicts with catalog: ' + spec['id'] + '/' + key, 409)
            groups[key] = records
            self._copy_resource_assets(folder, resource, spec, plan)
            exports[resource['id']] = {'revision': resource['revision'], 'library_id': spec['id'],
                                       'key': key, 'records_hash': records_hash(records)}
            operation = 'unchanged' if existing and records_hash(existing) == records_hash(records) else ('updated' if existing else 'created')
            actions.append({'resource_id': resource['id'], 'revision': resource['revision'], 'library_id': spec['id'], 'key': key, 'action': operation})
        for identifier, (table, groups) in changed.items():
            plan['tables'][identifier] = NativeTable(table.render(groups))
        return exports

    def library_inventory(self, release_id):
        release = self.get_release(release_id)
        return {'nx_version': release['nx_version'], 'libraries': inventory(self.release_folder(release_id)),
                'references': reference_report(self.release_folder(release_id), self.standards, release['nx_version'])}

    def catalog_plan(self, release_id, identifiers, include_standard=False,selection=None):
        selection=catalog_selection(identifiers,selection)
        release = self.get_release(release_id)
        root = self.release_folder(release_id)
        if release['status'] in ('building', 'failed') or self.manifest(root) != release['manifest']:
            raise DomainError('Native import requires a complete, unchanged release.', 409)
        catalog = self.resources()
        candidates, existing, conflicts, managed = [], {r['id']: r for r in catalog}, [], []
        expected_inputs={item['path'].casefold():item['sha256'] for item in release['manifest']}
        source_inputs,standard_inputs,format_baselines={},{},{}
        claims = {}
        for resource in catalog:
            identity = resource_identity(resource)
            if identity:
                claims.setdefault(identity, []).append(resource)
        for identifier in dict.fromkeys(identifiers):
            spec = spec_for(identifier)
            table = NativeTable.read(root / spec['path'])
            if table.source_sha256!=expected_inputs.get(spec['path'].casefold()):
                raise DomainError('Native library changed while planning catalog adoption.',409)
            source_inputs[identifier]=table.source_sha256
            baseline = None
            standard_inputs[identifier]=None
            format_baselines[identifier]={}
            if release['nx_version'] in self.standards:
                path = offline_path(self.standards[release['nx_version']] / spec['path'])
                if path.is_file():
                    standard_table=NativeTable.read(path);baseline=standard_table.groups()
                    standard_inputs[identifier]=standard_table.source_sha256
                    format_baselines[identifier]={block['id']:list(block['fields']) for block in standard_table.blocks}
            if baseline is None and not include_standard:
                raise DomainError('Siemens baseline required to distinguish customer/standard objects.')
            groups=table.groups()
            if selection is not None:
                for missing in sorted(set(selection[identifier])-set(groups)):
                    conflicts.append({'library':identifier,'key':missing,'reason':'Selected complete object is missing from this release.'})
            for key, records in groups.items():
                if selection is not None and key not in selection[identifier]:continue
                standard = baseline is not None and key in baseline and records_hash(records) == records_hash(baseline[key])
                if standard and not include_standard:
                    if selection is not None:
                        conflicts.append({'library':identifier,'key':key,'reason':'Selected Siemens standard requires explicit read-only standard adoption; it cannot be overwritten.'})
                    continue
                resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'nx-native/' + identifier + '/' + key))
                cam = native_cam(identifier, release['nx_version'], table, records)
                try:validate_catalog_cam({'kind':spec['kind'],'cam':cam,'cam_status':'draft'})
                except DomainError as error:
                    conflicts.append({'library':identifier,'key':key,'reason':str(error)})
                    continue
                owners = claims.get((identifier, key), [])
                if not owners and resource_id in existing:
                    owners = [existing[resource_id]]
                if owners:
                    same = False
                    if len(owners) == 1:
                        owner = owners[0]
                        try:
                            owner_table = cam_table(owner['cam']) if owner['cam'].get('library_id') else legacy_cam_table(owner['cam'])
                            same = records_hash(project_records(owner_table.records, owner_table, table)) == records_hash(records)
                        except DomainError:
                            pass
                        # A previously exported old-schema row can already contain explicitly
                        # reviewed new-column values. Reuse that proof only for the same CAM data.
                        if not same:
                            snapshot = next((r for r in (release['catalog'] or {}).get('resources', []) if r['id'] == owner['id']), None)
                            exported = (release['catalog'] or {}).get('exported_native', {}).get(owner['id']) or (release['catalog'] or {}).get('exported_tools', {}).get(owner['id'])
                            same = bool(snapshot and snapshot['cam'] == owner['cam'] and exported and
                                        exported.get('records_hash') == records_hash(records))
                    if same:
                        managed.append({'library': identifier, 'key': key, 'resource_id': owner['id'],
                                        'revision': owner['revision']})
                    else:
                        conflicts.append({'library': identifier, 'key': key, 'resource_ids': [r['id'] for r in owners],
                            'reason': 'Identifier is already managed and differs; no duplicate catalog owner or automatic overwrite.'})
                    continue
                name = next((r['values'].get('DESCR') for r in records if r['values'].get('DESCR')), key)
                candidates.append({'id': resource_id, 'kind': spec['kind'], 'name': name[:200], 'erp': None,
                    'cam': cam, 'cam_status': 'draft', 'asset_ids': [],
                    'geometry': {'mode': 'none', 'status': 'needs_preparation', 'nx_version': '',
                                 'checks': {}, 'evidence': '', 'asset_hashes': {}},
                    'origin': {'release_id': release_id, 'library_id': identifier, 'path': spec['path'],
                               'key': key, 'records_hash': records_hash(records), 'editable': baseline is not None and not standard}})
        report = {'objects': [{'id': r['id'], 'library': r['cam']['library_id'], 'name': r['name'],
                              'key': r['origin']['key'], 'editable': r['origin']['editable'],
                              'kind':r['kind'],'record_count':len(r['cam']['records']),
                              'format_blocks':[{'id':block['id'],'class_name':block['class_name'],'fields':list(block['fields']),
                                  'record_count':sum(record['block_id']==block['id'] for record in r['cam']['records']),
                                  'baseline_fields':format_baselines[r['cam']['library_id']].get(block['id'])}
                                  for block in r['cam']['schemas']]} for r in candidates],
                  'count': len(candidates), 'conflicts': conflicts, 'can_import': not conflicts,
                  'already_managed': managed,
                  'catalog_revisions':[{'id':r['id'],'revision':r['revision']} for r in catalog],
                  'catalog_state_sha256':digest(sorted(catalog,key=lambda resource:resource['id'])),
                  'commit_contract_version':1,'draft_only':True,'nx_files_changed':False,'format_comparison':'same_block_id_only',
                  'source_inputs':source_inputs,'standard_inputs':standard_inputs,
                  'source_state':{'nx_version':release['nx_version'],'folder':str(root),'manifest_sha256':digest(release['manifest'])},
                  'release_id': release_id, 'libraries': identifiers, 'include_standard': include_standard}
        if selection is not None:
            report['selection']=selection
        report['preview_hash'] = digest([report, candidates, release['manifest'], catalog])
        return candidates, report

    def import_catalog(self, release_id, identifiers, include_standard, preview_hash, actor,operation_job_id=None,selection=None):
        with self.lock:
            candidates, report = self.catalog_plan(release_id, identifiers, include_standard,selection)
            if not report['can_import'] or preview_hash != report['preview_hash']:
                raise DomainError('Catalog preview changed or contains unresolved conflicts.', 409)
            stamp = now()
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                rows=db.execute('SELECT * FROM resources ORDER BY id').fetchall()
                current={(row['id'],row['revision']) for row in rows}
                if current!={(r['id'],r['revision']) for r in report['catalog_revisions']}:
                    raise DomainError('Catalog changed before native import commit; review a new preview.',409)
                if digest([self._resource_value(row) for row in rows])!=report['catalog_state_sha256']:
                    raise DomainError('Catalog contents changed before native import commit; review a new preview.',409)
                row=db.execute('SELECT * FROM releases WHERE id=?',(release_id,)).fetchone()
                if row is None:raise DomainError('Native source disappeared before native import commit.',409)
                latest=self._release_value(row);expected=report['source_state']
                if latest['status'] in ('building','failed') or latest['nx_version']!=expected['nx_version'] or latest['folder']!=expected['folder'] or digest(latest['manifest'])!=expected['manifest_sha256']:
                    raise DomainError('Native source metadata changed before native import commit.',409)
                root=offline_path(latest['folder'])
                for identifier,fingerprint in report['source_inputs'].items():
                    spec=spec_for(identifier);path=offline_path(root/spec['path'])
                    if not path.is_file() or sha256(path)!=fingerprint:
                        raise DomainError('Native library bytes changed before native import commit.',409)
                    baseline=self.standards.get(latest['nx_version'])
                    path=offline_path(baseline/spec['path']) if baseline is not None else None
                    actual=sha256(path) if path is not None and path.is_file() else None
                    if actual!=report['standard_inputs'][identifier]:
                        raise DomainError('Siemens baseline changed before native import commit.',409)
                for resource in candidates:
                    resource_id = resource.pop('id')
                    text = canonical(resource)
                    db.execute('INSERT INTO resources(id,revision,payload,external_key,updated_at) VALUES(?,?,?,?,?)', (resource_id, 1, text, None, stamp))
                    db.execute('INSERT INTO resource_versions(id,revision,payload,actor,created_at) VALUES(?,?,?,?,?)', (resource_id, 1, text, actor, stamp))
                from .job_results import resource_result
                result=resource_result(db,operation_job_id,actor,'catalog',[{'id':item['id'],'revision':1} for item in report['objects']])
                self.audit(db, actor, 'catalog.imported', {'release_id': release_id, 'objects': report['objects'],
                                                        'preview_hash': preview_hash,**result})
            return {'imported': len(candidates), 'status': 'draft', 'nx_files_changed': False}

    def validate_release(self, release_id, actor):
        with self.lock:
            release = self.get_release(release_id)
            if release['status'] in ('building', 'failed'):
                raise DomainError('Incomplete release cannot be validated.', 409)
            folder, errors = self.release_folder(release_id), []
            current = self.manifest(folder)
            if current != release['manifest']:
                errors.append('File list, size or SHA-256 differs from immutable manifest.')
            for relative, path in tree_files(folder):
                if path.suffix.lower() == '.pax':
                    try:
                        from .template_refs import read_xml
                        read_xml(path)
                    except DomainError as error:
                        errors.append('Malformed PAX XML or unsupported construct: ' + relative + ' (' + str(error) + ')')
            database = folder / TOOL_DATABASE
            tool_count = None
            if database.exists():
                try:
                    tool_count = len(read_database(database)[2])
                except DomainError as error:
                    errors.append(str(error))
            if not current:
                errors.append('Empty release.')
            validation = {'integrity_passed': not errors, 'errors': errors,
                          'file_count': len(current), 'tool_count': tool_count,
                          'functional_validation_required': True, 'checked_at': now()}
            references = reference_report(folder, self.standards, release['nx_version'])
            validation['libraries'] = inventory(folder)
            validation['references'] = references
            errors.extend(canonical(e) for e in references['errors'])
            from .fixture_refs import planned as fixture_dependencies
            fixture_report=fixture_dependencies(self,release,{'assets':{}})
            validation['fixture_references']=fixture_report
            errors.extend('KRX fixture reference: '+canonical(item) for item in fixture_report['errors'])
            validation['integrity_passed'] = not errors
            retained = release['status'] == 'approved' and self.review_current(release)
            reasons = []
            if release['status'] == 'approved':
                if not retained: reasons.append('Approval lacks the current manifest/version/log binding; repeat the NX review.')
                else:
                    try:
                        log = self.scan_log(release['review']['syslog']['source'],
                            self.host_folder(folder) or folder, release['nx_version'])
                        if log['sha256'] != release['review']['syslog']['sha256'] or not log['can_review']:
                            retained = False; reasons.append('Reviewed NX log changed or no longer qualifies.')
                    except DomainError:
                        retained = False; reasons.append('Reviewed NX log is missing, unreadable or changed.')
            validation.update(approval_retained=bool(retained and not errors),
                approval_requires_requalification=bool(release['review'] and (errors or not retained)),
                review_invalidation_reasons=reasons)
            with self.db() as db:
                # Rechecking an unchanged approved release does not discard its approval.
                status = 'approved' if not errors and retained else ('validated' if not errors else 'blocked')
                db.execute('UPDATE releases SET status=?,validation=?,review=? WHERE id=?',
                           (status, canonical(validation), canonical(release['review']) if release['review'] else None, release_id))
                self.audit(db, actor, 'release.validated', {'id': release_id, **validation})
            return validation

    def scan_log(self, alias, expected_folder=None, expected_nx_version=None, *, db=None):
        if db is None:
            with self.db() as connection:
                stored = connection.execute('SELECT * FROM syslogs WHERE id=?', (alias,)).fetchone()
        else:
            stored = db.execute('SELECT * FROM syslogs WHERE id=?', (alias,)).fetchone()
        if stored:
            path = offline_path(self.root / 'evidence' / stored['id'] / 'nx.syslog')
        elif alias in self.logs:
            path = offline_path(self.logs[alias])
        else:
            raise DomainError('Unknown NX system log alias.', 404)
        if not path.is_file():
            raise DomainError('NX system log file is missing.', 404)
        raw = read_bounded_file(path,max_bytes=MAX_LOG_BYTES,
            expected_size=stored['size'] if stored else None,expected_sha256=stored['sha256'] if stored else None,
            changed_message='Uploaded NX system log changed outside the API.' if stored else
                'NX log is still changing; close the test session or upload a saved copy.',
            limit_message='NX system log must contain 1 byte to 20 MB.')
        return scan_bytes(raw, alias, expected_folder, expected_nx_version)

    def _verify_review_commit_evidence(self, db, folder, manifest, report, nx_version):
        """Last read-only byte gate inside the transition's SQL transaction.

        SQL serializes metadata writers, not external filesystem writers. This
        is a fresh bounded log/full-manifest check, not an atomic FS snapshot.
        Historical receipt replay never enters this gate.
        """
        try:
            fresh = self.scan_log(report['source'], self.host_folder(folder) or folder, nx_version, db=db)
            if (fresh['sha256'] != report['sha256'] or not fresh['can_review'] or
                    fresh['expected_folder'] != report['expected_folder']):
                raise DomainError('NX log or display binding changed before final commit; rescan and repeat the review.', 409)
            if self.manifest(folder) != manifest:
                raise DomainError('Candidate files changed before final commit; validate and repeat the NX review.', 409)
        except (DomainError, OSError) as error:
            raise DomainError('Final NX evidence check failed; no approval or active-pointer switch committed. ' + str(error), 409) from error

    def list_logs(self):
        with self.db() as db:
            uploaded = [{**dict(row), 'source_type': 'uploaded'}
                        for row in db.execute('SELECT * FROM syslogs ORDER BY created_at DESC')]
        configured = [{'id': key, 'filename': path.name, 'source_type': 'configured', 'sha256': None,
                       'size': None, 'created_at': None} for key, path in self.logs.items()]
        return uploaded + configured

    def add_log(self, filename, content, actor,request_key=None):
        from . import log_requests
        log_requests.filename(filename);request_key=write_request_key(request_key)
        decode_log(content)
        identifier = 'uploaded-' + str(uuid.uuid4())
        directory = offline_path(self.root / 'evidence' / identifier)
        digest=hashlib.sha256(content).hexdigest()
        allocation={'id':identifier,'filename':filename,'sha256':digest,'size':len(content)}
        if request_key is not None:
            cached=log_requests.reserve(self,actor,request_key,allocation)
            if cached is not None:return cached
        try:
            # Legacy calls have no writer intent: retain their original backup lock.
            with self.lock if request_key is None else nullcontext():
                directory.mkdir(parents=True, exist_ok=False)
                path=offline_path(directory/'nx.syslog');path.write_bytes(content)
                if path.stat().st_size!=len(content) or sha256(path)!=digest:raise DomainError('Written system log differs from uploaded bytes; partial evidence retained.',409)
                with self.lock,self.db() as db:
                    db.execute('BEGIN IMMEDIATE');stamp=now()
                    db.execute('INSERT INTO syslogs(id,filename,sha256,size,created_at) VALUES(?,?,?,?,?)',(identifier,filename,digest,len(content),stamp))
                    result={**allocation,'created_at':stamp,'source_type':'uploaded'}
                    receipt=log_requests.commit(db,actor,request_key,result,stamp)
                    self.audit(db,actor,'syslog.uploaded',{**allocation,**receipt})
            return result
        except OSError as error:raise DomainError('Cannot store system log; partial evidence retained.',409) from error
        finally:log_requests.fail(self,actor,request_key)

    def log_metadata(self,identifier):
        with self.db() as db:row=db.execute('SELECT * FROM syslogs WHERE id=?',(identifier,)).fetchone()
        if not row:raise DomainError('Uploaded system log not found.',404)
        self.scan_log(identifier)  # Verifies the immutable stored bytes, not CAM qualification.
        return {**dict(row),'source_type':'uploaded'}

    def review_current(self, release):
        """Metadata qualification only; activation/validation recheck actual files."""
        review = release.get('review') or {}
        if not isinstance(review,dict):return False
        report = review.get('syslog') or {}
        if not isinstance(report,dict):return False
        version = report.get('version_check') or {}; tests = review.get('tests') or {}
        if not isinstance(version,dict) or not isinstance(tests,dict):return False
        return (review.get('contract_version') == 2 and review.get('nx_version') == release['nx_version'] and
            review.get('manifest_sha256') == digest(release['manifest']) and report.get('parser_version') == PARSER_VERSION and
            report.get('can_review') is True and version.get('status') == 'match' and version.get('expected') == release['nx_version'] and
            version.get('families') == [release['nx_version']] and set(tests) == FUNCTIONAL_TESTS and
            isinstance(report.get('sha256'),str) and bool(re.fullmatch(r'[a-f0-9]{64}',report['sha256'])) and
            isinstance(report.get('source'),str) and bool(report['source']) and
            report.get('expected_folder') == (release.get('host_folder') or release.get('folder')) and
            report.get('contains_cam_activity') is True and report.get('contains_release_reference') is True and
            report.get('blocking_error_count') == 0 and not report.get('blocking_errors') and
            all(value is True for value in tests.values()) and isinstance(review.get('evidence'), str) and
            len(review['evidence'].strip()) >= 20)

    def review_release(self, release_id, tests, evidence, log_alias, actor,
                       expected_log_sha256=None, expected_manifest_sha256=None,request_key=None):
        from . import release_requests as requests
        request_key=write_request_key(request_key)
        payload=copy.deepcopy({'tests':tests,'evidence':evidence,'syslog_source':log_alias,'expected_log_sha256':expected_log_sha256,'expected_manifest_sha256':expected_manifest_sha256})
        if request_key is not None:
            cached=requests.reserve(self,actor,request_key,'review',release_id,payload)
            if cached is not None:return cached
        try:return self._review_release_once(release_id,payload['tests'],payload['evidence'],payload['syslog_source'],actor,payload['expected_log_sha256'],payload['expected_manifest_sha256'],request_key,payload)
        finally:
            if request_key is not None:requests.fail(self,actor,request_key)

    def _review_release_once(self, release_id, tests, evidence, log_alias, actor,
                       expected_log_sha256=None, expected_manifest_sha256=None,request_key=None,request_payload=None):
        from . import release_requests as requests
        with self.lock:
            if set(tests) != FUNCTIONAL_TESTS or not all(value is True for value in tests.values()):
                raise DomainError('All six functional checks must be explicitly confirmed.', 409)
            if not isinstance(evidence, str) or not 20 <= len(evidence.strip()) <= 4000 or '\x00' in evidence:
                raise DomainError('Provide a meaningful functional review statement of 20-4000 characters.', 409)
            release = self.get_release(release_id)
            if expected_manifest_sha256 != digest(release['manifest']) or not expected_log_sha256:
                raise DomainError('Scan this candidate/log first; approval needs both current SHA bindings.', 409)
            # A normalized/derived release differs from its source. Evidence
            # must name the actual managed candidate, never just its parent.
            tested_folder = self.release_folder(release_id)
            # Docker's /data/r/<id>/nx and the Windows bind-mount path identify
            # the SAME managed candidate, never the source/parent directory.
            report = self.scan_log(log_alias, self.host_folder(tested_folder) or tested_folder, release['nx_version'])
            if report['sha256'] != expected_log_sha256:
                raise DomainError('The scanned NX log changed; scan again and repeat the functional checks.', 409)
            if not report['can_review']:
                raise DomainError('NX log needs the correct NX_VERSION header, candidate/CAM activity and no load errors; approval blocked.', 409)
            validation = self.validate_release(release_id, actor)
            if not validation['integrity_passed']:
                raise DomainError('Technical validation failed; approval blocked.', 409)
            # Configured logs can still be written by an external NX session.
            if self.scan_log(log_alias, self.host_folder(tested_folder) or tested_folder, release['nx_version'])['sha256'] != report['sha256']:
                raise DomainError('NX log changed while validating; repeat the review.', 409)
            review = {'contract_version': 2, 'manifest_sha256': expected_manifest_sha256, 'nx_version': release['nx_version'],
                      'tests': tests, 'evidence': evidence, 'syslog': report, 'actor': actor, 'reviewed_at': now()}
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                requests.gate(db,actor,request_key,'review',release_id,request_payload)
                current = db.execute('SELECT status,nx_version,folder,manifest FROM releases WHERE id=?', (release_id,)).fetchone()
                if (not current or current['status'] not in ('validated','approved') or current['nx_version'] != release['nx_version'] or
                        current['folder'] != str(tested_folder) or digest(json.loads(current['manifest'] or 'null')) != expected_manifest_sha256):
                    raise DomainError('Candidate identity/manifest changed during review; recheck before approving.', 409)
                self._verify_review_commit_evidence(db, tested_folder, release['manifest'], report, release['nx_version'])
                db.execute('UPDATE releases SET status=?,review=? WHERE id=?', ('approved', canonical(review), release_id))
                row=db.execute('SELECT * FROM releases WHERE id=?',(release_id,)).fetchone()
                launcher=db.execute('SELECT payload FROM test_launchers WHERE release_id=?',(release_id,)).fetchone()
                result=self._release_value(row,launcher['payload'] if launcher else None)
                receipt=requests.commit(db,actor,request_key,'review',release_id,request_payload,result,now())
                self.audit(db, actor, 'release.approved', {'id': release_id, **review,**receipt})
            return result

    def active(self):
        with self.db() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT release_id,generation FROM active WHERE singleton=1').fetchone()
            if not row:raise DomainError('Active metadata singleton is missing.',409)
            stored = db.execute('SELECT * FROM releases WHERE id=?',(row['release_id'],)).fetchone() if row['release_id'] else None
        if row['release_id'] and not stored:raise DomainError('Active pointer references missing release metadata.',409)
        return self._active_value(row,self._release_value(stored) if stored else None)

    def _active_value(self, row, release):
        row = dict(row)
        row['usable'] = bool(release and release['status'] == 'approved' and self.review_current(release))
        row['usability_reason'] = ('Current approval metadata is bound; runtime files are checked when validating/activating.' if row['usable']
                                  else 'Repeat the current NX review before use.' if release else 'No local active release.')
        row['folder'] = release['folder'] if row['usable'] else None
        row['host_folder'] = release.get('host_folder') if row['usable'] else None
        row['status'] = release['status'] if release else 'none'
        return row

    def activate(self, release_id, expected_generation, actor,request_key=None):
        from . import release_requests as requests
        request_key=write_request_key(request_key);payload={'expected_generation':expected_generation}
        if request_key is not None:
            cached=requests.reserve(self,actor,request_key,'activate',release_id,payload)
            if cached is not None:return cached
        try:return self._activate_once(release_id,expected_generation,actor,request_key,payload)
        finally:
            if request_key is not None:requests.fail(self,actor,request_key)

    def _activate_once(self, release_id, expected_generation, actor,request_key=None,request_payload=None):
        from . import release_requests as requests
        with self.lock:
            release = self.get_release(release_id)
            with self.db() as db:
                generation = db.execute('SELECT generation FROM active WHERE singleton=1').fetchone()[0]
            if generation != expected_generation:
                raise DomainError('Active pointer changed; reload before switching.', 409)
            if release['status'] != 'approved' or not self.review_current(release):
                raise DomainError('Only an explicitly approved release can be activated.', 409)
            if not self.validate_release(release_id, actor)['integrity_passed']:
                raise DomainError('Release integrity failed; activation blocked.', 409)
            qualified = self.get_release(release_id)
            if qualified['status'] != 'approved' or not self.review_current(qualified):
                raise DomainError('Approval no longer qualifies; repeat the NX review.', 409)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                requests.gate(db,actor,request_key,'activate',release_id,request_payload)
                current = db.execute('SELECT * FROM releases WHERE id=?', (release_id,)).fetchone()
                if not current or current['status'] != 'approved' or current['folder']!=qualified['folder'] or current['nx_version']!=qualified['nx_version'] or json.loads(current['manifest']) != qualified['manifest'] or json.loads(current['review'] or 'null') != qualified['review']:
                    raise DomainError('Release approval changed while activating; recheck.', 409)
                self._verify_review_commit_evidence(db, self._release_path(release_id, current['folder']),
                    qualified['manifest'], qualified['review']['syslog'], qualified['nx_version'])
                changed = db.execute('UPDATE active SET release_id=?,generation=generation+1 WHERE singleton=1 AND generation=?',
                                     (release_id, expected_generation)).rowcount
                if changed != 1:
                    raise DomainError('Active pointer changed; reload before switching.', 409)
                launcher=db.execute('SELECT payload FROM test_launchers WHERE release_id=?',(release_id,)).fetchone()
                result=self._active_value(db.execute('SELECT release_id,generation FROM active WHERE singleton=1').fetchone(),self._release_value(current,launcher['payload'] if launcher else None))
                receipt=requests.commit(db,actor,request_key,'activate',release_id,request_payload,result,now())
                self.audit(db, actor, 'release.activated', {'id': release_id, 'previous_generation': expected_generation,**receipt})
            return result

    def audit_entries(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY sequence DESC LIMIT 200')]
