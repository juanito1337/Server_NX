import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .dat import append_tools, read_database
from .libraries import (KINDS, SPECS, OPTIONAL_PALETTE, NativeTable, cam_table, digest,
                        geometry_check, inventory, legacy_database, native_cam,
                        normalization_plan, project_records, records_hash,
                        reference_report, safe_graphics_path, spec_for)
from .safety import DomainError, disjoint, offline_path, tree_files

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


class Store:
    def __init__(self, root, sources=None, logs=None, standards=None, display_paths=None):
        self.root = offline_path(root)
        self.sources = {key: offline_path(path) for key, path in (sources or {}).items()}
        self.logs = {key: offline_path(path) for key, path in (logs or {}).items()}
        self.standards = {key: offline_path(path) for key, path in (standards or {}).items()}
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
        for source in list(self.sources.values()) + list(self.logs.values()) + list(self.standards.values()):
            disjoint(self.root, source)
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
                INSERT OR IGNORE INTO active VALUES(1, NULL, 0);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS geometry_jobs (
                    id TEXT PRIMARY KEY, resource_id TEXT NOT NULL,
                    payload TEXT NOT NULL, report TEXT);
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                    action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
            ''')
            if 'folder' not in {row['name'] for row in db.execute('PRAGMA table_info(releases)')}:
                db.execute('ALTER TABLE releases ADD COLUMN folder TEXT')
            for row in db.execute('SELECT id FROM releases WHERE folder IS NULL').fetchall():
                legacy = self.root / 'releases' / row['id'] / 'folder'
                db.execute('UPDATE releases SET folder=? WHERE id=?', (str(legacy), row['id']))

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
        last = db.execute('SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1').fetchone()
        previous = last['hash'] if last else '0' * 64
        stamp, text = now(), canonical(payload)
        digest = hashlib.sha256(canonical([previous, actor, action, text, stamp]).encode()).hexdigest()
        db.execute('INSERT INTO audit(actor,action,payload,created_at,previous_hash,hash) VALUES(?,?,?,?,?,?)',
                   (actor, action, text, stamp, previous, digest))

    def save_resource(self, payload, actor, resource_id=None, expected=None):
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM resources WHERE id=?', (resource_id,)).fetchone() if resource_id else None
            if resource_id and not old:
                raise DomainError('Resource not found.', 404)
            if old and expected != old['revision']:
                raise DomainError('Resource changed; reload before saving.', 409)
            resource_id = resource_id or str(uuid.uuid4())
            revision = old['revision'] + 1 if old else 1
            if old:
                old_payload = json.loads(old['payload'])
                if old_payload.get('erp') != payload.get('erp'):
                    raise DomainError('ERP-owned fields may only be changed through ERP upsert.', 409)
                if old_payload.get('origin') != payload.get('origin'):
                    raise DomainError('Import provenance may not be changed through resource editing.', 409)
            elif payload.get('origin'):
                raise DomainError('Import provenance is assigned only by native catalog import.')
            erp = payload.get('erp')
            for asset_id in payload.get('asset_ids', []):
                if not db.execute('SELECT id FROM assets WHERE id=?', (asset_id,)).fetchone():
                    raise DomainError('Unknown asset identifier.')
            if payload.get('cam_status') == 'approved':
                metadata = [self.asset(asset_id)[0] for asset_id in payload.get('asset_ids', [])]
                geometry_check(payload, metadata)
                if payload.get('cam', {}).get('library_id'):
                    cam_table(payload['cam'])
                    if spec_for(payload['cam']['library_id'])['kind'] != payload['kind']:
                        raise DomainError('Catalog kind and native library kind differ.')
            external_key = canonical([erp['company_id'], erp['system_id']]) if erp else None
            text, stamp = canonical(payload), now()
            try:
                db.execute('INSERT INTO resources VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                           'revision=excluded.revision,payload=excluded.payload,external_key=excluded.external_key,updated_at=excluded.updated_at',
                           (resource_id, revision, text, external_key, stamp))
            except sqlite3.IntegrityError as error:
                raise DomainError('ERP object already linked to a resource.', 409) from error
            db.execute('INSERT INTO resource_versions VALUES(?,?,?,?,?)', (resource_id, revision, text, actor, stamp))
            self.audit(db, actor, 'resource.saved', {'id': resource_id, 'revision': revision, 'payload': payload})
        return {'id': resource_id, 'revision': revision, 'updated_at': stamp, **payload}

    def erp_upsert(self, data, actor):
        external_key = canonical([data['company_id'], data['system_id']])
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM resources WHERE external_key=?', (external_key,)).fetchone()
            erp = {key: data[key] for key in ('company_id', 'system_id', 'number', 'description', 'unit', 'source_revision')}
            if old:
                payload = json.loads(old['payload'])
                if payload['erp'] == erp:
                    return {'id': old['id'], 'revision': old['revision'], 'unchanged': True, **payload}
                if data.get('expected_revision') != old['revision']:
                    raise DomainError('ERP upsert requires current expected_revision.', 409)
                if data['kind'] != payload['kind']:
                    raise DomainError('ERP cannot change the resource kind.', 409)
                resource_id, revision = old['id'], old['revision'] + 1
                payload['erp'] = erp
                # CAM values and CAM approval are deliberately not ERP-owned.
            else:
                payload = {'kind': data['kind'], 'name': data['description'], 'erp': erp,
                           'cam': {}, 'cam_status': 'draft', 'asset_ids': []}
                resource_id, revision = str(uuid.uuid4()), 1
            text, stamp = canonical(payload), now()
            db.execute('INSERT INTO resources VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                       'revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at',
                       (resource_id, revision, text, external_key, stamp))
            db.execute('INSERT INTO resource_versions VALUES(?,?,?,?,?)', (resource_id, revision, text, actor, stamp))
            self.audit(db, actor, 'erp.upserted', {'id': resource_id, 'revision': revision, 'erp': erp})
        return {'id': resource_id, 'revision': revision, 'updated_at': stamp, **payload}

    def resources(self):
        with self.db() as db:
            return [{'id': row['id'], 'revision': row['revision'], 'updated_at': row['updated_at'], **json.loads(row['payload'])}
                    for row in db.execute('SELECT * FROM resources ORDER BY updated_at DESC')]

    def history(self, resource_id):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM resource_versions WHERE id=? ORDER BY revision', (resource_id,))]

    def add_asset(self, filename, content, actor):
        if not re.fullmatch(r'[\w. -]{1,120}', filename) or filename in ('.', '..') or filename.endswith(('.', ' ')):
            raise DomainError('Invalid asset filename.')
        if Path(filename).suffix.lower() not in {'.prt', '.step', '.stp', '.jpg', '.jpeg', '.png', '.pdf',
                                                '.bmp', '.x_t', '.igs', '.iges', '.dxf', '.p21', '.xlsx', '.krx'}:
            raise DomainError('Unsupported asset extension.')
        if filename.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(?i)(COM|LPT)[1-9]', filename.split('.')[0]):
            raise DomainError('Reserved Windows asset filename.')
        if not content or len(content) > 20 * 1024 * 1024:
            raise DomainError('Asset must contain 1 byte to 20 MB.')
        asset_id = str(uuid.uuid4())
        directory = offline_path(self.root / 'assets' / asset_id)
        directory.mkdir(parents=True, exist_ok=False)
        path = offline_path(directory / filename)
        path.write_bytes(content)
        digest = sha256(path)
        with self.lock, self.db() as db:
            db.execute('INSERT INTO assets VALUES(?,?,?,?,?)', (asset_id, filename, digest, len(content), now()))
            self.audit(db, actor, 'asset.created', {'id': asset_id, 'filename': filename, 'sha256': digest})
        return {'id': asset_id, 'filename': filename, 'sha256': digest, 'size': len(content)}

    def asset(self, asset_id):
        try:
            uuid.UUID(asset_id)
        except ValueError:
            raise DomainError('Invalid asset identifier.')
        with self.db() as db:
            row = db.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
        if not row:
            raise DomainError('Asset not found.', 404)
        result = dict(row)
        path = offline_path(self.root / 'assets' / result['id'] / result['filename'])
        if sha256(path) != result['sha256']:
            raise DomainError('Asset has changed outside the API.', 409)
        return result, path

    def release_folder(self, release_id):
        try:
            if str(uuid.UUID(release_id)) != release_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise DomainError('Invalid release identifier.')
        with self.db() as db:
            row = db.execute('SELECT folder FROM releases WHERE id=?', (release_id,)).fetchone()
        # Short physical names avoid Windows MAX_PATH with long Siemens filenames.
        return offline_path(row['folder'] if row and row['folder'] else self.root / 'r' / uuid.UUID(release_id).hex[:12] / 'nx')

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
            row = db.execute('SELECT * FROM releases WHERE id=?', (release_id,)).fetchone()
        if not row:
            raise DomainError('Release not found.', 404)
        result = dict(row)
        for key in ('manifest', 'validation', 'review', 'catalog'):
            result[key] = json.loads(result[key]) if result[key] else None
        result['folder'] = str(self.release_folder(release_id))
        result['host_folder'] = self.host_folder(self.release_folder(release_id))
        return result

    def releases(self):
        with self.db() as db:
            ids = [row['id'] for row in db.execute('SELECT id FROM releases ORDER BY created_at DESC')]
        return [{key: value for key, value in self.get_release(rid).items() if key != 'manifest'} for rid in ids]

    def manifest(self, root):
        return [{'path': relative, 'size': path.stat().st_size, 'sha256': sha256(path)} for relative, path in tree_files(root)]

    def _snapshot(self, source, folder, omit=None):
        before = self.manifest(source)
        if os.name == 'nt' and any(len(str(folder / entry['path'])) >= 255 for entry in before):
            raise DomainError('Destination paths exceed the Windows pilot limit; use a shorter data directory.')
        folder.mkdir(parents=True, exist_ok=False)
        for entry in before:
            if omit and omit(entry['path']):
                continue
            source_file = source / entry['path']
            offline_path(source_file)
            target = offline_path(folder / entry['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target)
            if sha256(target) != entry['sha256']:
                raise DomainError('Source changed during import; incomplete release retained as failed.', 409)
        expected = [entry for entry in before if not omit or not omit(entry['path'])]
        if before != self.manifest(source) or expected != self.manifest(folder):
            raise DomainError('Source changed during import.', 409)
        return expected

    def preview_normalization(self, source_key, nx_version, legacy_version, defaults=None, resolutions=None, legacy_omissions=None):
        if source_key not in self.sources:
            raise DomainError('Unknown administrator-configured source alias.')
        _, report = normalization_plan(self.sources[source_key], self.standards, nx_version,
                                       legacy_version, defaults, resolutions, legacy_omissions)
        report['preview_hash'] = digest(report)
        return report

    def import_release(self, source_key, nx_version, name, actor, parent=None, normalization=None, defaults=None):
        with self.lock:
            if parent:
                previous = self.get_release(parent)
                if previous['status'] in ('building', 'failed'):
                    raise DomainError('Cannot derive from an incomplete release.', 409)
                source = self.release_folder(parent)
                if self.manifest(source) != previous['manifest']:
                    raise DomainError('Parent release has been changed outside the API.', 409)
                nx_version = previous['nx_version']
            else:
                if source_key not in self.sources:
                    raise DomainError('Unknown administrator-configured source alias.')
                source = self.sources[source_key]
            outputs, normalization_report = {}, None
            if normalization:
                if parent:
                    raise DomainError('Normalize a configured source first, not an immutable parent release.')
                outputs, normalization_report = normalization_plan(source, self.standards, nx_version,
                    normalization['legacy_version'], normalization.get('defaults'), normalization.get('resolutions'), normalization.get('legacy_omissions'))
                preview_hash = digest(normalization_report)
                if normalization.get('preview_hash') != preview_hash:
                    raise DomainError('Normalization preview changed; review the current preview first.', 409)
                if not normalization_report['can_import']:
                    raise DomainError('Resolve all database/schema conflicts before import.', 409)
                normalization_report['preview_hash'] = preview_hash
            release_id = str(uuid.uuid4())
            folder = self.release_folder(release_id)
            while folder.parent.exists():
                release_id = str(uuid.uuid4())
                folder = self.release_folder(release_id)
            with self.db() as db:
                db.execute('INSERT INTO releases(id,name,nx_version,source,parent_id,status,created_at,folder) VALUES(?,?,?,?,?,?,?,?)',
                           (release_id, name, nx_version, str(source), parent, 'building', now(), str(folder)))
                self.audit(db, actor, 'release.started', {'id': release_id, 'source': str(source), 'parent': parent})
            try:
                # Omit only the optional palette and, when normalized, legacy
                # database files. Originals and historical releases stay intact.
                omit = lambda relative: relative.casefold() == OPTIONAL_PALETTE.casefold() or (normalization and legacy_database(relative))
                manifest = self._snapshot(source, folder, omit)
                if normalization:
                    for path, text in outputs.items():
                        target = offline_path(folder / path)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(text, encoding='utf-8-sig')
                    # Recompute the reviewed plan after copying: changed library
                    # inputs must never produce a release from a stale preview.
                    _, fresh = normalization_plan(source, self.standards, nx_version,
                        normalization['legacy_version'], normalization.get('defaults'), normalization.get('resolutions'), normalization.get('legacy_omissions'))
                    if digest(fresh) != normalization_report['preview_hash']:
                        raise DomainError('Library inputs changed during normalization.', 409)
                    manifest = self.manifest(folder)
                catalog = self.resources() if parent else []
                previously_exported = (previous['catalog'] or {}).get('exported_tools', {}) if parent else {}
                export_resources = [resource for resource in catalog
                                    if resource['kind'] == 'tool' and resource['cam_status'] == 'approved'
                                    and 'library_id' not in resource['cam']
                                    and not (resource['id'] in previously_exported
                                             and previously_exported[resource['id']]['cam_status'] == 'approved'
                                             and previously_exported[resource['id']]['cam'] == resource['cam']
                                             and previously_exported[resource['id']]['asset_ids'] == resource['asset_ids'])]
                tools = [resource['cam'] for resource in export_resources]
                if tools:
                    for resource in export_resources:
                        geometry_check(resource, [self.asset(a)[0] for a in resource['asset_ids']], nx_version)
                    if not (folder / TOOL_DATABASE).is_file():
                        raise DomainError('Release has no supported metric NX tool database.')
                    # Reject collisions, including an attempt to re-export an already present tool.
                    append_tools(folder / TOOL_DATABASE, tools)
                    for resource in export_resources:
                        for asset_id in resource['asset_ids']:
                            asset, source_asset = self.asset(asset_id)
                            target = offline_path(folder / safe_graphics_path(SPECS['tool-metric'], asset['filename']))
                            if target.exists():
                                raise DomainError('Asset export would overwrite an existing file.', 409)
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source_asset, target)
                    manifest = self.manifest(folder)
                native_exports = (previous['catalog'] or {}).get('exported_native', {}) if parent else {}
                native_exports = self._export_native(folder, catalog, native_exports, nx_version, defaults or {})
                manifest = self.manifest(folder)
                exports = dict(previously_exported)
                exports.update({resource['id']: resource for resource in export_resources})
                with self.db() as db:
                    db.execute('UPDATE releases SET status=?,manifest=?,catalog=? WHERE id=?',
                               ('draft', canonical(manifest), canonical({'resources': catalog, 'exported_tools': exports,
                                   'exported_native': native_exports, 'normalization': normalization_report,
                                   'optional_palette_omitted': OPTIONAL_PALETTE}), release_id))
                    self.audit(db, actor, 'release.imported', {'id': release_id, 'files': len(manifest),
                                                             'catalog': [{'id': r['id'], 'revision': r['revision']} for r in catalog]})
                return self.get_release(release_id)
            except Exception as error:
                failure = {'integrity_passed': False, 'errors': [str(error)[:1500]], 'checked_at': now()}
                with self.db() as db:
                    db.execute('UPDATE releases SET status=?,validation=? WHERE id=?', ('failed', canonical(failure), release_id))
                    self.audit(db, actor, 'release.failed', {'id': release_id, 'error': str(error)[:1500]})
                raise

    def _export_native(self, folder, catalog, previous_exports, nx_version, defaults):
        exports, changed = dict(previous_exports), {}
        for resource in catalog:
            cam = resource['cam']
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
            table, groups = changed.setdefault(spec['id'], (NativeTable.read(target_path), None))
            if groups is None:
                groups = table.groups()
                changed[spec['id']] = (table, groups)
            source = cam_table(cam)
            records = project_records(source.records, source, table, defaults.get(spec['id']))
            key = next(iter(source.groups()))
            existing = groups.get(key)
            origin = resource.get('origin') or {}
            old_export = previous_exports.get(resource['id'], {})
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
            for asset_id in resource['asset_ids']:
                asset, asset_path = self.asset(asset_id)
                target = offline_path(folder / safe_graphics_path(spec, asset['filename']))
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and sha256(target) != asset['sha256']:
                    raise DomainError('Standard graphics filename already has different contents.', 409)
                shutil.copyfile(asset_path, target)
            exports[resource['id']] = {'revision': resource['revision'], 'library_id': spec['id'],
                                       'key': key, 'records_hash': records_hash(records)}
        for identifier, (table, groups) in changed.items():
            path = offline_path(folder / SPECS[identifier]['path'])
            path.write_text(table.render(groups), encoding='utf-8-sig')
        if changed:
            report = reference_report(folder, self.standards, nx_version)
            if report['errors']:
                raise DomainError('Native export has missing/invalid references: ' + canonical(report['errors'][:10]), 409)
        return exports

    def library_inventory(self, release_id):
        release = self.get_release(release_id)
        return {'nx_version': release['nx_version'], 'libraries': inventory(self.release_folder(release_id)),
                'references': reference_report(self.release_folder(release_id), self.standards, release['nx_version'])}

    def catalog_plan(self, release_id, identifiers, include_standard=False):
        release = self.get_release(release_id)
        root = self.release_folder(release_id)
        if release['status'] in ('building', 'failed') or self.manifest(root) != release['manifest']:
            raise DomainError('Native import requires a complete, unchanged release.', 409)
        candidates, existing, conflicts = [], {r['id']: r for r in self.resources()}, []
        for identifier in dict.fromkeys(identifiers):
            spec = spec_for(identifier)
            table = NativeTable.read(root / spec['path'])
            baseline = None
            if release['nx_version'] in self.standards:
                path = offline_path(self.standards[release['nx_version']] / spec['path'])
                if path.is_file():
                    baseline = NativeTable.read(path).groups()
            if baseline is None and not include_standard:
                raise DomainError('Siemens baseline required to distinguish customer/standard objects.')
            for key, records in table.groups().items():
                standard = baseline is not None and key in baseline and records_hash(records) == records_hash(baseline[key])
                if standard and not include_standard:
                    continue
                resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'nx-native/' + identifier + '/' + key))
                cam = native_cam(identifier, release['nx_version'], table, records)
                if resource_id in existing:
                    if existing[resource_id]['cam'] != cam:
                        conflicts.append({'library': identifier, 'key': key, 'resource_id': resource_id,
                                          'reason': 'Catalog object differs; no automatic overwrite.'})
                    continue
                name = next((r['values'].get('DESCR') for r in records if r['values'].get('DESCR')), key)
                candidates.append({'id': resource_id, 'kind': spec['kind'], 'name': name[:200], 'erp': None,
                    'cam': cam, 'cam_status': 'draft', 'asset_ids': [],
                    'geometry': {'mode': 'none', 'status': 'needs_preparation', 'nx_version': '',
                                 'checks': {}, 'evidence': '', 'asset_hashes': {}},
                    'origin': {'release_id': release_id, 'library_id': identifier, 'path': spec['path'],
                               'key': key, 'records_hash': records_hash(records), 'editable': baseline is not None and not standard}})
        report = {'objects': [{'id': r['id'], 'library': r['cam']['library_id'], 'name': r['name'],
                              'key': r['origin']['key'], 'editable': r['origin']['editable']} for r in candidates],
                  'count': len(candidates), 'conflicts': conflicts, 'can_import': not conflicts,
                  'release_id': release_id, 'libraries': identifiers, 'include_standard': include_standard}
        report['preview_hash'] = digest([report, candidates, release['manifest']])
        return candidates, report

    def import_catalog(self, release_id, identifiers, include_standard, preview_hash, actor):
        with self.lock:
            candidates, report = self.catalog_plan(release_id, identifiers, include_standard)
            if not report['can_import'] or preview_hash != report['preview_hash']:
                raise DomainError('Catalog preview changed or contains unresolved conflicts.', 409)
            stamp = now()
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                for resource in candidates:
                    resource_id = resource.pop('id')
                    text = canonical(resource)
                    db.execute('INSERT INTO resources VALUES(?,?,?,?,?)', (resource_id, 1, text, None, stamp))
                    db.execute('INSERT INTO resource_versions VALUES(?,?,?,?,?)', (resource_id, 1, text, actor, stamp))
                self.audit(db, actor, 'catalog.imported', {'release_id': release_id, 'objects': report['objects'],
                                                        'preview_hash': preview_hash})
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
                        ET.parse(path)
                    except ET.ParseError:
                        errors.append('Malformed PAX XML: ' + relative)
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
            validation['integrity_passed'] = not errors
            with self.db() as db:
                # Rechecking an unchanged approved release does not discard its approval.
                status = release['status'] if not errors and release['status'] == 'approved' else ('validated' if not errors else 'blocked')
                db.execute('UPDATE releases SET status=?,validation=?,review=? WHERE id=?',
                           (status, canonical(validation), canonical(release['review']) if status == 'approved' else None, release_id))
                self.audit(db, actor, 'release.validated', {'id': release_id, **validation})
            return validation

    def scan_log(self, alias, expected_folder=None):
        if alias not in self.logs:
            raise DomainError('Unknown administrator-configured log alias.')
        path = offline_path(self.logs[alias])
        if path.stat().st_size > 20 * 1024 * 1024:
            raise DomainError('System log exceeds 20 MB.')
        raw = path.read_bytes()
        text = raw.decode('utf-8', errors='replace')
        errors = []
        expression = re.compile(r'Uid in component.*do not match|Failed to read in part|Failed to load and update part|Adding load status.*(?:641032|641044|640001)', re.I)
        for number, line in enumerate(text.splitlines(), 1):
            if expression.search(line):
                errors.append({'line': number, 'message': line.strip()[:1500]})
        expected = str(expected_folder).replace('\\', '/').casefold() if expected_folder is not None else None
        contains_reference = expected is None or bool(re.search(
            re.escape(expected) + r'''(?=$|[/\s'"<>;),])''', text.replace('\\', '/').casefold()))
        return {'source': alias, 'sha256': hashlib.sha256(raw).hexdigest(), 'blocking_errors': errors,
                'contains_cam_activity': 'ug_app_manufacturing' in text.lower(),
                'contains_release_reference': contains_reference}

    def review_release(self, release_id, tests, evidence, log_alias, actor):
        with self.lock:
            validation = self.validate_release(release_id, actor)
            if not validation['integrity_passed']:
                raise DomainError('Technical validation failed; approval blocked.', 409)
            if set(tests) != FUNCTIONAL_TESTS or not all(value is True for value in tests.values()):
                raise DomainError('All six functional checks must be explicitly confirmed.', 409)
            release = self.get_release(release_id)
            # A normalized/derived release differs from its source. Evidence
            # must name the actual managed candidate, never just its parent.
            tested_folder = self.release_folder(release_id)
            # Docker's /data/r/<id>/nx and the Windows bind-mount path identify
            # the SAME managed candidate, never the source/parent directory.
            report = self.scan_log(log_alias, self.host_folder(tested_folder) or tested_folder)
            if report['blocking_errors'] or not report['contains_cam_activity'] or not report['contains_release_reference']:
                raise DomainError('NX log has loading errors or no NX/CAM test activity; approval blocked.', 409)
            review = {'tests': tests, 'evidence': evidence, 'syslog': report, 'actor': actor, 'reviewed_at': now()}
            with self.db() as db:
                db.execute('UPDATE releases SET status=?,review=? WHERE id=?', ('approved', canonical(review), release_id))
                self.audit(db, actor, 'release.approved', {'id': release_id, **review})
            return self.get_release(release_id)

    def active(self):
        with self.db() as db:
            row = dict(db.execute('SELECT release_id,generation FROM active WHERE singleton=1').fetchone())
        release = self.get_release(row['release_id']) if row['release_id'] else None
        row['usable'] = bool(release and release['status'] == 'approved')
        row['folder'] = str(self.release_folder(row['release_id'])) if row['usable'] else None
        row['host_folder'] = self.host_folder(self.release_folder(row['release_id'])) if row['usable'] else None
        row['status'] = release['status'] if release else 'none'
        return row

    def activate(self, release_id, expected_generation, actor):
        with self.lock:
            release = self.get_release(release_id)
            if release['status'] != 'approved' or not release['review']:
                raise DomainError('Only an explicitly approved release can be activated.', 409)
            if self.scan_log(release['review']['syslog']['source'])['sha256'] != release['review']['syslog']['sha256']:
                raise DomainError('The reviewed NX log has changed; repeat the approval.', 409)
            if not self.validate_release(release_id, actor)['integrity_passed']:
                raise DomainError('Release integrity failed; activation blocked.', 409)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                changed = db.execute('UPDATE active SET release_id=?,generation=generation+1 WHERE singleton=1 AND generation=?',
                                     (release_id, expected_generation)).rowcount
                if changed != 1:
                    raise DomainError('Active pointer changed; reload before switching.', 409)
                self.audit(db, actor, 'release.activated', {'id': release_id, 'previous_generation': expected_generation})
            return self.active()

    def audit_entries(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY sequence DESC LIMIT 200')]
