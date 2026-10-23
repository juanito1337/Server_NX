"""NX2512 journal: import a pinned STEP into a fresh local job directory.

No vendor DLL execution, CAM setup, library export, geometry role guessing or
automatic approval. NX APIs checked against installed NX2512 pythonStubs.
This file is copied as nx_step_import.py into downloadable job packages.
"""
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

WORKER_FILES=('nx_step_import.py','nx_safety.py','stepfiles.py')
MAX_JOB_BYTES=32*1024*1024  # CAM snapshot plus metadata; API requests are capped at 30 MiB.
MAX_HELPER_BYTES=1024*1024
MAX_STEP_BYTES=20*1024*1024
MAX_REPORT_FILES=10000
# Keep standalone journal diagnostics within the server's Report contract.
MAX_REPORT_COMPONENTS=10000
MAX_COMPONENT_ID_LENGTH=500
MAX_REPORT_PARTS=5000
MAX_PART_BODIES=10000
MAX_REPORT_BYTES=1024*1024
MAX_REPORT_MESSAGES=10000
MAX_MESSAGE_LENGTH=16000


def _report_json(value):
    # Match the server's canonical UTF-8 size, including non-ASCII names.
    # Pretty-printing can make an otherwise valid report too large for the GUI.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class _ReportBudget:
    """Account for growing metadata without repeatedly serializing the inventory."""
    def __init__(self, report):
        self.report = report
        self.size = len(_report_json(report).encode('utf-8'))

    def append(self, field, value):
        cost = len(_report_json(value).encode('utf-8')) + bool(self.report[field])
        if self.size + cost > MAX_REPORT_BYTES:
            raise DomainError('Diagnostic byte budget exceeded; only a partial ' + field + ' inventory is recorded.')
        self.report[field].append(value)
        self.size += cost


def _diagnostic_message(value):
    text = str(value).replace('\x00', '\\0')
    suffix = ' [diagnostic text truncated]'
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH-len(suffix)] + suffix
    return text


def _bounded_report_text(report):
    """Keep failed evidence uploadable; never silently qualify a partial report.

    Incremental inventory accounting leaves only summary/error overhead here.
    If that overhead crosses the limit, retain prefixes of the metadata arrays,
    not modified geometry. Binary search avoids quadratic full-report encoding.
    """
    changed = False
    for field in ('errors', 'warnings'):
        original = report[field]
        report[field] = [_diagnostic_message(value) for value in original[:MAX_REPORT_MESSAGES]]
        changed |= report[field] != original
    if changed:
        report['status'] = 'failed'
        report['errors'] = ['Diagnostic messages exceed text/count bounds; only partial diagnostic text is recorded.'] + report['errors'][:MAX_REPORT_MESSAGES-1]
    text = _report_json(report)
    if len(text.encode('utf-8')) <= MAX_REPORT_BYTES:
        return text
    report['status'] = 'failed'
    report['errors'] = ['Diagnostic byte budget exceeded; only a partial diagnostic inventory is recorded.'] + report['errors'][:MAX_REPORT_MESSAGES-1]
    for field in ('solid_prototypes', 'body_prototypes', 'component_occurrences'):
        report.pop(field, None)
    fields = ['parts', 'components', 'files', 'warnings', 'errors']
    while True:
        text = _report_json(report)
        if len(text.encode('utf-8')) <= MAX_REPORT_BYTES:
            return text
        candidates = [field for field in fields if len(report[field]) > (1 if field == 'errors' else 0)]
        if not candidates:
            raise DomainError('Diagnostic identity alone exceeds the report byte budget.')
        field = max(candidates, key=lambda key: len(_report_json(report[key]).encode('utf-8')))
        values = report[field]
        low, high = (1 if field == 'errors' else 0), len(values)
        while low < high:
            middle = (low + high + 1) // 2
            report[field] = values[:middle]
            if len(_report_json(report).encode('utf-8')) <= MAX_REPORT_BYTES:
                low = middle
            else:
                high = middle - 1
        report[field] = values[:low]


if '_nx_verified_helper_modules' not in globals():
    from .nxbootstrap import _nx_verified_helper_modules


def _verified_helpers(value):
    """Compile pinned bytes, not ambient imports or cached pyc; no NX calls."""
    modules=_nx_verified_helper_modules(value,'job.json','worker_hashes',WORKER_FILES,
        (('safety','nx_safety.py'),('stepfiles','stepfiles.py')),max_job_bytes=MAX_JOB_BYTES,max_helper_bytes=MAX_HELPER_BYTES)
    return modules['safety'],modules['stepfiles']


try:
    from .safety import DomainError, offline_path, tree_files,read_bounded_file,file_fingerprint
    from .stepfiles import PROTOCOLS, TOOL_TYPES, preflight
except ImportError:
    _safety,_step=_verified_helpers(Path(__file__).absolute().parent)
    DomainError,offline_path,tree_files,read_bounded_file=(_safety.DomainError,_safety.offline_path,_safety.tree_files,_safety.read_bounded_file)
    file_fingerprint=_safety.file_fingerprint
    PROTOCOLS,TOOL_TYPES,preflight=_step.PROTOCOLS,_step.TOOL_TYPES,_step.preflight


def check_environment(session, nx_version='2512'):
    # Inspect path-valued NX variables as text BEFORE any filesystem lookup.
    values = dict(os.environ)
    for key in ('UGII_BASE_DIR', 'UGII_CUSTOM_DIRECTORY_FILE', 'UGII_CAM_RESOURCE_DIR',
                'UGII_CAM_LIBRARY_TOOL_DIR', 'UGII_CAM_LIBRARY_TOOL_GRAPHICS_PATH', 'UGII_USER_DIR'):
        values[key] = session.GetEnvironmentVariableValue(key) or values.get(key, '')
    for key, value in values.items():
        if not key.upper().startswith(('UGII_', 'UGCAM_', 'NX_')):
            continue
        if re.search(r'(?i)(?:^|[;=\s"\'])w:|\\\\|//|/mnt/w(?:/|$)|siemensshare', value):
            raise DomainError('NX has a network/production environment setting: ' + key + '. Use an isolated local NX session.')
    base = values.get('UGII_BASE_DIR', '')
    if nx_version not in ('2406', '2512') or base.replace('\\', '/').rstrip('/').casefold() != 'c:/siemens/nx'+nx_version:
        raise DomainError('This pilot requires the pinned local C:/Siemens/NX installation for '+nx_version+'.')
    return offline_path(base)


def summarize(parts, components, tool_type):
    errors, warnings = [], []
    bodies = [b for p in parts for b in p['bodies']]
    solids = [b for b in bodies if b['solid']]
    if not solids:
        errors.append('No solid bodies imported. Repair/export a solid model first.')
    if not components:
        errors.append('Flat model: the Assistant requires a non-flat assembly. Create an assembly manually; no guessed body splitting.')
    if not tool_type.startswith('turning_') and len(solids) < 2:
        errors.append('Milling/drilling needs separate cutting and non-cutting bodies; one united solid is insufficient.')
    if any(not p['fully_loaded'] for p in parts):
        errors.append('Not all component prototypes are fully loaded.')
    if any(not b['solid'] for b in bodies):
        warnings.append('Sheet bodies present; do not treat them as verified collision solids.')
    if any(b.get('convergent') for b in bodies):
        errors.append('Faceted/convergent bodies require review or B-rep repair before this Assistant workflow.')
    warnings.append('Counts are prototype counts, not occurrence counts. Separate bodies alone do not prove correct cutting/shank/holder roles.')
    warnings.append('Dimensions, units, mounting face/centre, insert direction and collision envelope are NOT verified.')
    return {'status': 'needs_structure' if errors else 'needs_selection',
            'errors': errors, 'warnings': warnings, 'solid_prototypes': len(solids),
            'body_prototypes': len(bodies), 'component_occurrences': len(components),
            'automatic_cam_approval': False}


def inventory_part(part, output):
    path = offline_path(part.FullPath)
    try:
        relative = path.relative_to(output).as_posix()
    except ValueError as error:
        raise DomainError('NX loaded a component outside the isolated output directory.') from error
    bodies = []
    for b in part.Bodies:
        if len(bodies) >= MAX_PART_BODIES:
            raise DomainError('Body count exceeds diagnostic bounds; only a partial part inventory is recorded.')
        bodies.append({'id': b.JournalIdentifier, 'name': b.Name, 'solid': bool(b.IsSolidBody),
                       'convergent': bool(b.IsConvergentBody), 'face_count': len(b.GetFaces()),
                       'layer': b.Layer, 'color': b.Color})
    return {'path': relative, 'units': str(part.PartUnits),
            'fully_loaded': bool(part.IsFullyLoaded), 'bodies': bodies}


def job_directory(value):
    directory = offline_path(value)
    if os.name != 'nt':
        raise DomainError('This NX pilot journal is Windows-only.')
    try:
        relative = directory.relative_to(offline_path('C:/NX_Migration/ToolJobs'))
    except ValueError as error:
        raise DomainError('Unpack jobs below C:\\NX_Migration\\ToolJobs, not into source or installation folders.') from error
    if not relative.parts or len(str(directory)) > 160:
        raise DomainError('Use a short, dedicated child directory below C:\\NX_Migration\\ToolJobs.')
    return directory


def local_settings(settings, output):
    """Stock translator definition with explicit local seeds and local log."""
    definition = settings.read_text(encoding='utf-8-sig')
    def local_seed(match):
        name = match.group(2).strip()
        if not re.fullmatch(r'[A-Za-z0-9_. -]+\.prt', name):
            raise DomainError('Unexpected translator base-template path; no external seed parts are allowed.')
        seed = offline_path(settings.parent / name)
        if not seed.is_file():
            raise DomainError('NX translator base-template missing: ' + name)
        return match.group(1) + ' = ' + str(seed)
    definition = re.sub(r'(?m)^(BASE_PART_(?:MICRON|MM|METER|IN))\s*=\s*([^\r\n]+)', local_seed, definition)
    definition = re.sub(r'(?m)^LOG_FILE\s*=.*$', 'LOG_FILE = translator.log', definition)
    # No implicit rescaling. Keep the STEP unit; NX import result units are
    # reported, and dimensions still require operator verification.
    definition = re.sub(r'(?m)^CONVERT_STEP_FILES_TO\s*=.*$', 'CONVERT_STEP_FILES_TO = ', definition)
    definition = re.sub(r'(?m)^AUTOMATIC_SURF_SEW\s*=.*$', 'AUTOMATIC_SURF_SEW = Off', definition)
    definition = re.sub(r'(?m)^SMOOTH_BSURFS\s*=.*$', 'SMOOTH_BSURFS = Off', definition)
    definition = re.sub(r'(?m)^DO_IMPORT_MERGING_REDUNDANT_TOPOLOGY\s*=.*$', 'DO_IMPORT_MERGING_REDUNDANT_TOPOLOGY = Off', definition)
    target = offline_path(output / 'translator.def')
    target.write_text(definition, encoding='utf-8')
    return target


def run_job(directory, session, nx):
    directory = job_directory(directory)
    job = json.loads(read_bounded_file(directory/'job.json',max_bytes=MAX_JOB_BYTES).decode('utf-8-sig'))
    if job.get('contract_version') != 1 or job.get('nx_version') not in ('2406','2512') or job.get('tool_type') not in TOOL_TYPES:
        raise DomainError('Unsupported NX job contract.')
    # The package is pinned to these exact helper sources, not current server code.
    if set(job.get('worker_hashes', {})) != {'nx_step_import.py', 'nx_safety.py', 'stepfiles.py'}:
        raise DomainError('Incomplete worker contract.')
    for filename, expected in job['worker_hashes'].items():
        if filename not in {'nx_step_import.py', 'nx_safety.py', 'stepfiles.py'}:
            raise DomainError('Invalid worker file.')
        read_bounded_file(directory/filename,max_bytes=MAX_HELPER_BYTES,expected_sha256=expected,
            changed_message='Worker package changed: '+filename)
    source = offline_path(directory / 'input.step')
    check = preflight(read_bounded_file(source,max_bytes=MAX_STEP_BYTES,expected_size=job['input']['size'],
        expected_sha256=job['input']['sha256'],changed_message='STEP differs from the pinned job input.'))
    if check['sha256'] != job['input']['sha256'] or check['protocol'] != job['input']['protocol']:
        raise DomainError('STEP differs from the pinned job input.')
    base = check_environment(session) if job['nx_version']=='2512' else check_environment(session, job['nx_version'])
    # Never modify an existing part or CAM session, including background parts.
    if session.Parts.Work is not None or session.Parts.Display is not None or any(True for _ in session.Parts):
        raise DomainError('Close all parts first. This import requires a fresh, empty NX session.')
    if session.IsCamSessionInitialized():
        raise DomainError('A CAM session is already initialized. Restart an isolated NX session in Modeling.')
    output = offline_path(directory / 'output')
    output.mkdir(exist_ok=False)  # One run only; failures retained, never overwritten.
    report = {'contract_version': 1, 'job_id': job['id'], 'resource_id': job['resource_id'],
              'resource_revision': job['resource_revision'], 'input_sha256': check['sha256'],
              'resource_hash': job['resource_hash'], 'worker_hashes': job['worker_hashes'],
              'nx_version': job['nx_version'], 'installation': str(base), 'tool_type': job['tool_type'],
              'status': 'failed', 'parts': [], 'components': [], 'files': [],
              'errors': [], 'warnings': [], 'automatic_cam_approval': False}
    previous = Path.cwd()
    try:
        budget = _ReportBudget(report)
        # Some translators write log/component files relative to cwd.
        os.chdir(output)
        protocol = check['protocol']
        settings = offline_path(base / PROTOCOLS[protocol][1])
        settings_copy = local_settings(settings, output)
        importer = getattr(session.DexManager, 'CreateStep' + protocol + 'Importer')()
        try:
            importer.SetMode(nx.BaseImporter.Mode.NativeFileSystem)
            importer.SettingsFile = str(settings_copy)
            importer.InputFile = str(source)
            importer.OutputFile = str(output / 'tool.prt')
            importer.ImportTo = getattr(nx, 'Step' + protocol + 'Importer').ImportToOption.NewPart
            importer.ImportToTeamcenter = False
            importer.FileOpenFlag = False
            importer.FlattenAssembly = False
            importer.PartUnit = nx.BaseImporter.PartUnitOption.NotSet  # Preserve source units; do not assume mm.
            importer.ObjectTypes.Solids = True
            importer.ObjectTypes.Surfaces = True
            importer.ProcessHoldFlag = True
            importer.Optimize = False
            if protocol != '242':
                importer.SimplifyGeometry = False
                importer.SewSurfaces = False
                importer.SmoothBSurfaces = False
            importer.Commit()  # Documented to return None, NOT the imported part.
        finally:
            importer.Destroy()
        top = offline_path(output / 'tool.prt')
        if not top.is_file():
            raise DomainError('STEP translator did not produce output/tool.prt; inspect translator.log.')
        part, status = session.Parts.OpenBaseDisplay(str(top))
        try:
            if status.NumberUnloadedParts:
                raise DomainError('NX component load errors: ' + '; '.join(
                    status.GetStatusDescription(i) for i in range(status.NumberUnloadedParts)))
        finally:
            status.Dispose()
        for item in session.Parts:
            if len(report['parts']) >= MAX_REPORT_PARTS:
                raise DomainError('Part count exceeds diagnostic bounds; only a partial part inventory is recorded.')
            budget.append('parts', inventory_part(item, output))
        root = part.ComponentAssembly.RootComponent
        if root is not None:
            pending = [(root, '')]
            while pending:
                parent, parent_id = pending.pop()
                for index, component in enumerate(parent.GetChildren()):
                    component_id = parent_id + '/' + str(index)
                    # Stop before inspecting the next prototype or growing the
                    # pending tree; retain an uploadable partial failed report.
                    if len(report['components']) >= MAX_REPORT_COMPONENTS:
                        raise DomainError('Component count exceeds diagnostic bounds; only a partial component inventory is recorded.')
                    if len(component_id) > MAX_COMPONENT_ID_LENGTH:
                        raise DomainError('Component ID depth exceeds diagnostic bounds; only a partial component inventory is recorded.')
                    prototype = offline_path(component.Prototype.FullPath)
                    try:
                        relative = prototype.relative_to(output).as_posix()
                    except ValueError as error:
                        raise DomainError('Component reference escapes the job output.') from error
                    budget.append('components', {'id': component_id, 'name': component.Name, 'part': relative})
                    pending.append((component, component_id))
        report.update(summarize(report['parts'], report['components'], job['tool_type']))
    except Exception as error:
        report['status'] = 'failed'
        report['errors'].append(_diagnostic_message(error))
    finally:
        os.chdir(previous)
        # Local file list only. Original STEP, source attachments and NX libraries
        # are never altered. No cleanup removes failed diagnostic evidence.
        report['files']=[]
        try:
            candidates=tree_files(output)
            if len(candidates)>MAX_REPORT_FILES:
                report['status']='failed'
                report['errors'].append('Output file count exceeds diagnostic bounds; only a partial file inventory is recorded.')
            budget = _ReportBudget(report)
            for relative,path in candidates[:MAX_REPORT_FILES]:
                try:
                    item = {'path':relative,**file_fingerprint(path)}
                except (DomainError,OSError) as error:
                    report['status']='failed'
                    budget.append('errors', _diagnostic_message('Output file '+relative+': '+str(error)))
                    continue
                budget.append('files', item)
        except (DomainError,OSError) as error:
            report['status']='failed';report['errors'].append(_diagnostic_message('Output enumeration failed: '+str(error)))
        offline_path(output / 'report.json').write_text(_bounded_report_text(report), encoding='utf-8')
    return report


def main():
    import NXOpen
    session = NXOpen.Session.GetSession()
    try:
        result = run_job(Path(__file__).absolute().parent, session, NXOpen)
        session.ListingWindow.Open()
        session.ListingWindow.WriteLine(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        session.ListingWindow.Open()
        session.ListingWindow.WriteLine('Import ABGEBROCHEN: ' + str(error))
        raise


if __name__ == '__main__':
    main()
