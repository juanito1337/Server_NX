"""Schema-driven native NX ASCII libraries. No paths supplied by API clients.

FORMAT blocks, not fixed column positions, are the schema. Section records stay
together; LIBRF alone is not a row key for holders, shanks or multitools.
"""
import copy
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .safety import DomainError, offline_path, tree_files, asset_filename


KINDS = {
    'tool': 'Werkzeug', 'holder': 'Werkzeughalter', 'shank': 'Werkzeugschaft',
    'tool_assembly': 'Komplettwerkzeug', 'segmented_tool': 'Werkzeugkontur',
    'trackpoint': 'Werkzeug-Bezugspunkte', 'multitool': 'Mehrfachwerkzeug',
    'fixture': 'Spannmittel', 'machine': 'Maschine', 'device': 'Maschinenzusatzgerät',
    'part_material': 'Werkstückwerkstoff', 'tool_material': 'Werkzeugwerkstoff',
    'feeds_speeds': 'Vorschub / Schnittgeschwindigkeit', 'machining_data': 'Bearbeitungsdaten',
    'tool_machining_data': 'Werkzeugspezifische Schnittdaten', 'cut_method': 'Bearbeitungsmethode',
    'process_force': 'Prozesskraftparameter', 'gauge': 'Prüfmittel',
    'inspection_tool': 'CMM-Taster', 'inspection_machine': 'CMM-Maschine',
    'inspection_device': 'CMM-Messkopf',
    'template':'NX-Vorlagenpaket','ui_standard':'NX-Oberflächenstandard',
}
SPECS = {}
MAX_CATALOG_SELECTION = 500


def catalog_selection(identifiers,selection):
    """Exact whole-object keys; None preserves the existing whole-library mode."""
    if selection is None:return None
    if not isinstance(selection,dict) or not selection or len(identifiers)!=len(set(identifiers)) or set(selection)!=set(identifiers):
        raise DomainError('Selection must name exactly the chosen, distinct libraries; empty selection is not whole-library import.')
    count=0;result={}
    for identifier,keys in selection.items():
        spec_for(identifier)
        if not isinstance(keys,list) or not keys or any(not isinstance(key,str) or not 1<=len(key)<=1024 or any(char in key for char in '|\r\n\x00') for key in keys):
            raise DomainError('Select complete library objects using nonempty keys up to 1024 characters.')
        if len(keys)!=len(set(keys)):raise DomainError('Selected object keys must be distinct within each library.')
        count+=len(keys)
        if count>MAX_CATALOG_SELECTION:raise DomainError('Select at most 500 complete objects per adoption.')
        result[identifier]=sorted(keys)
    return dict(sorted(result.items()))


def _spec(identifier, kind, path, legacy=None, graphics=None):
    SPECS[identifier] = {'id': identifier, 'kind': kind, 'label': KINDS[kind],
                         'path': path, 'legacy_path': legacy, 'graphics_dir': graphics}


for _unit in ('metric', 'english'):
    for _kind, _file in (('tool', 'tool'), ('holder', 'holder'), ('shank', 'shank'),
                         ('tool_assembly', 'assembly'), ('segmented_tool', 'segmented_tool'),
                         ('trackpoint', 'trackpoint'), ('multitool', 'multitool')):
        _spec(f'{_kind}-{_unit}', _kind, f'MACH/resource/library/tool/{_unit}/{_file}_database.dat',
              f'MACH/resource/library/tool/henning_dierk/{_file}_database.dat' if _unit == 'metric' else None,
              'MACH/resource/library/tool/graphics')
    _spec(f'fixture-{_unit}', 'fixture', f'MACH/resource/library/fixture_automation/{_unit}/fixture_database.dat',
          graphics='MACH/resource/library/fixture_automation/graphics')
    for _kind, _file in (('inspection_tool', 'insp_tool'), ('holder', 'holder'),
                         ('trackpoint', 'trackpoint'), ('segmented_tool', 'segmented_tool')):
        _spec(f'cmm-{_kind}-{_unit}', _kind, f'CMM_INSPECTION/resource/library/tool/{_unit}/{_file}_database.dat',
              graphics='CMM_INSPECTION/resource/library/tool/graphics')
for _kind, _file in (('machine', 'machine'), ('device', 'device')):
    _spec(_kind, _kind, f'MACH/resource/library/{_kind}/ascii/{_file}_database.dat',
          f'MACH/resource/library/{_kind}/henning_dierk/{_file}_database.dat')
    _spec(f'cmm-{_kind}', f'inspection_{_kind}', f'CMM_INSPECTION/resource/library/{_kind}/ascii/insp_{_file}_database.dat')
for _kind, _file in (('part_material', 'part_materials'), ('tool_material', 'tool_materials'),
                     ('feeds_speeds', 'feeds_speeds'), ('machining_data', 'machining_data'),
                     ('tool_machining_data', 'tool_machining_data'), ('cut_method', 'cut_methods'),
                     ('process_force', 'process_force_parameters')):
    _spec(_kind, _kind, f'MACH/resource/library/feeds_speeds/ascii/{_file}.dat',
          f'MACH/resource/library/feeds_speeds/henning_dierk/{_file}.dat')
    if _kind != 'process_force':
        _spec(f'cmm-{_kind}', _kind, f'CMM_INSPECTION/resource/library/feeds_speeds/ascii/{_file}.dat')

OPTIONAL_PALETTE = 'MACH/resource/dmg_templates.pax'


def spec_for(identifier):
    if identifier not in SPECS:
        raise DomainError('Unknown native NX library identifier.')
    return SPECS[identifier]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def decode(raw):
    try:
        return raw.decode('utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig')
    except UnicodeError as error:
        raise DomainError('NX library must be readable UTF-8/UTF-16; no lossy decoding.') from error


def _native_tokens(text):
    """Yield a complete continuation token without retaining every DATA row."""
    current_class='TABLE';pending=None
    for line in text.splitlines(keepends=True):
        stripped=line.strip();match=re.match(r'^(FORMAT|DATA)\b(.*)',stripped)
        if stripped.startswith('#CLASS '):
            if pending is not None:yield pending
            current_class=stripped[7:].strip();pending={'type':'raw','raw':line,'class':current_class}
        elif match:
            if pending is not None:yield pending
            pending={'type':match[1],'body':match[2].strip(),'raw':line,'class':current_class}
        elif stripped and not stripped.startswith('#'):
            if pending is None or pending['type'] not in ('FORMAT','DATA'):
                raise DomainError('Unexpected NX library content; no automatic rewrite.')
            pending['body']+=' '+stripped;pending['raw']+=line
        else:
            if pending is not None:yield pending
            pending={'type':'raw','raw':line,'class':current_class}
    if pending is not None:yield pending


def _native_values(body,block):
    # Retain the existing optional leading/trailing empty-pipe semantics,
    # without allocating one object per excess trailing pipe in malformed input.
    first,separator,rest=body.partition('|')
    if not first.strip():body=rest if separator else None
    values=[] if body is None else body.split('|',len(block['fields']))
    if len(values)>len(block['fields']) and not values[-1].replace('|','').strip():values.pop()
    if len(values)!=len(block['fields']):raise DomainError('NX DATA does not match FORMAT in '+block['id'])
    return [value.strip() for value in values]


class NativeTable:
    def __init__(self,text,*,schema_only=False,max_schemas=None,max_fields=None):
        if type(schema_only) is not bool or any(value is not None and (type(value) is not int or value<1) for value in (max_schemas,max_fields)):
            raise DomainError('Invalid native schema reader limits.')
        self.text,self.blocks,self.records,self.tokens='' if schema_only else text,[],[],[]
        self.schema_only=schema_only;self.row_count=0;field_count=0
        occurrences = Counter()
        block = None
        # Full readers preserve the same tokens/raw lines; schema readers only
        # retain definitions and a row count, never groups or value dictionaries.
        for token in _native_tokens(text):
            if not schema_only:self.tokens.append(token)
            if token['type'] == 'FORMAT':
                fields=token['body'].split(None,max_fields-field_count) if max_fields is not None else token['body'].split()
                if max_schemas is not None and len(self.blocks)>=max_schemas or max_fields is not None and field_count+len(fields)>max_fields:
                    raise DomainError('Native library exceeds the schema definition bound.')
                if not fields or len(fields) != len(set(fields)) or not ({'LIBRF', 'NAME'} & set(fields)):
                    raise DomainError('Invalid NX FORMAT block.')
                field_count+=len(fields)
                occurrences[token['class']] += 1
                block = {'id': token['class'] + ':' + str(occurrences[token['class']]),
                         'class_name': token['class'], 'fields': fields}
                block['schema_hash'] = digest([block['class_name'], fields])
                self.blocks.append(block)
                token['block'] = block['id']
            elif token['type'] == 'DATA':
                if block is None:
                    raise DomainError('NX DATA without FORMAT.')
                values=_native_values(token['body'],block)
                identifier='LIBRF' if 'LIBRF' in block['fields'] else 'NAME'
                if not values[block['fields'].index(identifier)]:
                    raise DomainError('Empty native library identifier.')
                self.row_count+=1
                if schema_only:continue
                values=dict(zip(block['fields'],values))
                record = {'block_id': block['id'], 'class_name': block['class_name'], 'values': values}
                self.records.append(record)
                token['record'] = record
                token['block'] = block['id']
        if not self.blocks:
            raise DomainError('No NX FORMAT blocks found.')

    @classmethod
    def read(cls, path):
        path = offline_path(path)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as error:
            raise DomainError('Native NX library file is missing.', 404) from error
        except OSError as error:
            raise DomainError('Native NX library file cannot be read.', 409) from error
        table = cls(decode(raw))
        table.source_sha256 = hashlib.sha256(raw).hexdigest()
        return table

    def block(self, identifier):
        found = [b for b in self.blocks if b['id'] == identifier]
        if len(found) != 1:
            raise DomainError('NX FORMAT block is not available in this version.')
        return found[0]

    def groups(self):
        if self.schema_only:raise DomainError('Schema-only reads do not contain object values.')
        groups = defaultdict(list)
        for record in self.records:
            groups[record['values'].get('LIBRF', record['values'].get('NAME'))].append(record)
        return dict(groups)

    def render(self, groups):
        additions = defaultdict(list)
        for records in groups.values():
            for record in records:
                additions[record['block_id']].append(record)
        output = []
        current = None
        # Replace data only. Keep Siemens FORMATs and comments in their places.
        for token in self.tokens:
            if token['type'] == 'FORMAT':
                if current is not None:
                    self._emit(output, additions.pop(current, []))
                current = token['block']
            elif token['type'] == 'DATA':
                continue
            elif token['raw'].strip().startswith('#CLASS ') and current is not None:
                self._emit(output, additions.pop(current, []))
            elif token['raw'].strip() == '#END_DATA' and current is not None:
                self._emit(output, additions.pop(current, []))
            output.append(token['raw'])
        if current is not None:
            self._emit(output, additions.pop(current, []))
        if additions:
            raise DomainError('NX export would lose a FORMAT block.')
        result = ''.join(output)
        NativeTable(result)  # Validate before a file can be written.
        return result

    def _emit(self, output, records):
        if records and output and not output[-1].endswith(('\n', '\r')):
            output.append('\n')
        for record in records:
            fields = self.block(record['block_id'])['fields']
            output.append('DATA | ' + ' | '.join(record['values'][f] for f in fields) + '\n')


def _canonical_records(records):
    return sorted(json.dumps(r, sort_keys=True) for r in records)


def unchanged_baseline_records(current, old):
    """Ignore only new columns/block occurrence numbers, never changed values."""
    remaining = list(current)
    if len(current) != len(old):
        return False
    for record in old:
        found = next((i for i, candidate in enumerate(remaining)
                      if candidate['class_name'] == record['class_name']
                      and all(candidate['values'].get(f) == v for f, v in record['values'].items())), None)
        if found is None:
            return False
        remaining.pop(found)
    return True


def projection_block(record, source, target):
    old = source.block(record['block_id'])
    candidates = [b for b in target.blocks if b['class_name'] == old['class_name']
                  and set(old['fields']) <= set(b['fields'])]
    if len(candidates) > 1 and 'RTYPE' in record['values']:
        candidates = [b for b in candidates if record['values']['RTYPE'] in {
            r['values'].get('RTYPE') for r in target.records if r['block_id'] == b['id']}]
    if len(candidates) != 1:
        raise DomainError('Removed/renamed/ambiguous columns or class: ' + old['id'])
    return candidates[0]


def project_records(records, source, target, defaults=None):
    """Project by name; new columns require an explicit, per-block default.

    Old columns may not be dropped (even empty ones) without an adapter. Block
    order changes/new classes do not shift values into the wrong columns.
    """
    defaults, result = defaults or {}, []
    for record in records:
        new = projection_block(record, source, target)
        values = dict(record['values'])
        for field in new['fields']:
            if field not in values:
                if field not in defaults.get(new['id'], {}):
                    raise DomainError('Explicit default required for new column ' + new['id'] + '.' + field)
                value = defaults[new['id']][field]
                if not isinstance(value, str) or any(c in value for c in '|\r\n\x00'):
                    raise DomainError('Invalid explicit column default.')
                values[field] = value
        result.append({'block_id': new['id'], 'class_name': new['class_name'], 'values': values})
    return result


def merge_tables(target, customer, baseline=None, defaults=None, resolutions=None):
    """Three-way, grouped merge. Only verified customer deltas override standard.

    Deletions of standard records are not inferred from a partial customer DB.
    Conflicting customizations require a per-group choice: standard or customer.
    """
    groups = copy.deepcopy(target.groups())
    old = baseline.groups() if baseline else {}
    conflicts, counts = [], Counter()
    for key, records in customer.groups().items():
        # Unchanged old standard records should not roll back the new standard.
        if key in old and _canonical_records(records) == _canonical_records(old[key]):
            counts['unchanged_old_standard'] += 1
            continue
        try:
            projected = project_records(records, customer, target, defaults)
        except DomainError as error:
            if (resolutions or {}).get(key) == 'standard':
                counts['explicitly_omitted_customer_groups'] += 1
                continue
            conflicts.append({'key': key, 'reason': str(error), 'type': 'schema', 'records': records})
            continue
        if (resolutions or {}).get(key) == 'standard':
            counts['resolved_standard' if key in groups else 'explicitly_omitted_customer_groups'] += 1
        elif key not in groups:
            groups[key] = projected
            counts['added_groups'] += 1
        elif _canonical_records(projected) == _canonical_records(groups[key]):
            counts['identical_groups'] += 1
        elif key in old and unchanged_baseline_records(groups[key], old[key]):
            groups[key] = projected
            counts['customer_only_changes'] += 1
        else:
            choice = (resolutions or {}).get(key)
            if choice == 'customer':
                groups[key] = projected
                counts['resolved_customer'] += 1
            elif choice == 'standard':
                counts['resolved_standard'] += 1
            else:
                conflicts.append({'key': key, 'reason': 'Same library identifier, different contents.',
                                  'type': 'data', 'standard': groups[key], 'customer': projected})
    return groups, {'counts': dict(counts), 'conflicts': conflicts}


def inventory(root):
    result = []
    for spec in SPECS.values():
        path = offline_path(root / spec['path'])
        if not path.is_file():
            continue
        try:
            table = NativeTable.read(path)
            result.append({**spec, 'row_count': len(table.records), 'object_count': len(table.groups()),
                           'schemas': table.blocks, 'errors': []})
        except DomainError as error:
            result.append({**spec, 'schemas': [], 'errors': [str(error)]})
    return result


def safe_graphics_path(spec, filename):
    """Files go into Siemens' designated graphics dir, never graphics/managed."""
    if not spec['graphics_dir']:
        raise DomainError('No supported standard graphics destination for this library.')
    asset_filename(filename)
    return str(PurePosixPath(spec['graphics_dir']) / filename)


def graphics_bindings(resource,assets,spec):
    bindings=resource.get('file_bindings') or []
    if not bindings:return {asset['id']:safe_graphics_path(spec,asset['filename']) for asset in assets}
    if not spec['graphics_dir']:raise DomainError('Library has no graphics destination.')
    known={asset['id']:asset for asset in assets};result={};paths=set();prefix=spec['graphics_dir']+'/'
    for item in bindings:
        if set(item)!={'asset_id','path'} or item['asset_id'] not in known or item['asset_id'] in result:
            raise DomainError('Invalid, duplicate or detached component binding.')
        path=item['path']
        if not isinstance(path,str) or not path.startswith(prefix):raise DomainError('Component must stay in its Siemens graphics directory.')
        suffix=path[len(prefix):]
        for component in suffix.split('/'):asset_filename(component)
        if PurePosixPath(suffix).name!=known[item['asset_id']]['filename'] or not suffix.lower().endswith('.prt'):
            raise DomainError('PRT component names cannot be changed by a filesystem binding.')
        if path.casefold() in paths:raise DomainError('Case-colliding graphics component paths.')
        paths.add(path.casefold());result[item['asset_id']]=path
    return result


def legacy_database(relative):
    parts = tuple(p.casefold() for p in PurePosixPath(relative).parts)
    return 'henning_dierk' in parts and relative.casefold().startswith('mach/resource/library/') and PurePosixPath(relative).suffix.lower().startswith('.dat')


def records_hash(records):
    return digest(_canonical_records(records))


def native_cam(identifier, version, table, records):
    return {'library_id': identifier, 'nx_version': version,
            'schemas': [b for b in table.blocks if b['id'] in {r['block_id'] for r in records}],
            'records': records}


def cam_table(cam):
    if set(cam) != {'library_id', 'nx_version', 'schemas', 'records'} or not cam['records']:
        raise DomainError('Native CAM data requires library_id, nx_version, schemas and grouped records.')
    spec_for(cam['library_id'])
    output = []
    seen = set()
    for block in cam['schemas']:
        if block['id'] in seen:
            raise DomainError('Repeated native FORMAT block identifier.')
        seen.add(block['id'])
        if not isinstance(block['fields'], list) or any(not isinstance(f, str) or not re.fullmatch(r'[A-Za-z0-9_]+', f) for f in block['fields']):
            raise DomainError('Invalid native FORMAT fields.')
        if not isinstance(block['class_name'], str) or not re.fullmatch(r'[A-Za-z0-9_]+', block['class_name']):
            raise DomainError('Invalid native class name.')
        output.extend(['#CLASS ' + block['class_name'] + '\n', 'FORMAT ' + ' '.join(block['fields']) + '\n'])
        for record in cam['records']:
            if record['block_id'] != block['id']:
                continue
            if record['class_name'] != block['class_name'] or set(record['values']) != set(block['fields']):
                raise DomainError('Provide all fields of the selected native FORMAT block.')
            if any(not isinstance(v, str) or any(c in v for c in '|\r\n\x00') for v in record['values'].values()):
                raise DomainError('Invalid native DATA field.')
            output.append('DATA | ' + ' | '.join(record['values'][f] for f in block['fields']) + '\n')
        output.append('#END_DATA\n')
    table = NativeTable(''.join(output))
    # Retain IDs from the source schema; a selected group may not use block 1.
    remap = {actual['id']: declared['id'] for actual, declared in zip(table.blocks, cam['schemas'])}
    for block in table.blocks:
        block['id'] = remap[block['id']]
    for record in table.records:
        record['block_id'] = remap[record['block_id']]
    if len(table.records) != len(cam['records']) or len(table.groups()) != 1:
        raise DomainError('A native resource must contain exactly one complete library object.')
    return table


def legacy_cam_table(cam):
    """Use the same schema/ownership rules for the older single-row tool editor."""
    if (set(cam) != {'class_name', 'values'} or not isinstance(cam['class_name'], str) or
            not isinstance(cam['values'], dict) or not cam['values'].get('LIBRF')):
        raise DomainError('Tools require class_name and values with a non-empty LIBRF.')
    block = {'id': cam['class_name'] + ':1', 'class_name': cam['class_name'], 'fields': list(cam['values'])}
    return cam_table({'library_id': 'tool-metric', 'nx_version': '', 'schemas': [block],
                      'records': [{'block_id': block['id'], **cam}]})


def validate_catalog_cam(resource):
    """One structural/type contract for every writer, independent of approval.

    File-package presence is not qualification: Store validates its paths,
    attachments, hashes and human checks separately before the atomic save.
    """
    cam=resource.get('cam',{});kind=resource.get('kind')
    if not isinstance(cam,dict):raise DomainError('CAM data must be a JSON object.')
    # Validate, never cast: large integers and historical canonical bytes stay intact.
    pending=[cam];seen=set()
    while pending:
        value=pending.pop()
        if isinstance(value,float) and not math.isfinite(value):
            raise DomainError('CAM data must contain only finite JSON numbers.')
        if isinstance(value,(dict,list,tuple)):
            identity=id(value)
            if identity in seen:continue
            seen.add(identity)
            pending.extend(value.values() if isinstance(value,dict) else value)
    if cam.get('library_id'):
        try:cam_table(cam)
        except (KeyError,TypeError,AttributeError,ValueError) as error:
            raise DomainError('Invalid native CAM data structure.') from error
        if spec_for(cam['library_id'])['kind']!=kind:
            raise DomainError('Catalog kind and native NX library differ.')
    elif resource.get('cam_status')=='approved':
        if kind=='tool':legacy_cam_table(cam)
        elif kind not in ('gauge','template','ui_standard'):
            if kind not in ('machine','fixture') or cam or resource.get('file_package') is None:
                raise DomainError('Approved native resources require a library-specific CAM record or a checked machine/fixture file package.')


def geometry_check(resource, assets, nx_version=None):
    """A file extension is not evidence of a simulation-ready CAM tool."""
    if resource['kind'] not in ('tool', 'tool_assembly', 'inspection_tool'):
        return
    geometry = resource.get('geometry') or {}
    if geometry.get('status') != 'validated':
        raise DomainError('Tool 3D/CAM preparation must be validated before export.')
    if len(geometry.get('evidence', '').strip()) < 20:
        raise DomainError('A documented 3D/CAM preparation check is required.')
    required = {'units', 'orientation', 'mount_point', 'cutting_geometry', 'non_cutting_geometry', 'collision_geometry'}
    if set(geometry.get('checks', {})) != required or not all(geometry['checks'].values()):
        raise DomainError('Confirm units, orientation, mounting and cutting/non-cutting/collision geometry.')
    if not re.fullmatch(r'\d{4}', geometry.get('nx_version', '')) or (nx_version and geometry['nx_version'] != nx_version):
        raise DomainError('The geometry check must match the target NX version.')
    mode = geometry.get('mode')
    if mode not in ('parametric_nx', 'nx_cam', 'step', 'manufacturer', 'none'):
        raise DomainError('Unknown geometry preparation mode.')
    if mode != 'parametric_nx':
        if mode != 'nx_cam' or not any(a['filename'].lower().endswith('.prt') for a in assets):
            raise DomainError('Generic STEP/manufacturer data needs CAM preparation and a checked NX-PRT result.')
        actual = {a['id']: a['sha256'] for a in assets}
        if geometry.get('asset_hashes') != actual:
            raise DomainError('Geometry evidence must bind all attached files by asset ID and SHA-256.')
    elif any(PurePosixPath(a['filename']).suffix.lower() in ('.prt', '.step', '.stp', '.igs', '.iges', '.x_t', '.p21') for a in assets):
        raise DomainError('Attached models require nx_cam preparation, not a parametric-only declaration.')


def reference_targets(identifier):
    """Relations depend on the library: PARTMAT is a description in part_materials.

    These adapters follow the attribute descriptions in the installed Siemens
    ASCII files. A familiar column name is not a relation in every database.
    """
    spec = spec_for(identifier)
    unit = 'english' if identifier.endswith('-english') else 'metric'
    prefix = 'cmm-' if identifier.startswith('cmm-') else ''
    if spec['kind'] in ('tool', 'inspection_tool'):
        return {'MATREF': prefix + 'tool_material', 'HLDREF': prefix + 'holder-' + unit,
                'HOLDREF': prefix + 'holder-' + unit, 'SHAREF': 'shank-' + unit,
                'SHANKREF': 'shank-' + unit, 'TPREF': prefix + 'trackpoint-' + unit}
    if spec['kind'] == 'tool_assembly':
        return {'TOOLREF': 'tool-' + unit, 'HOLDREF': prefix + 'holder-' + unit,
                'SHANKREF': 'shank-' + unit, 'HLDREF': prefix + 'holder-' + unit,
                'SHAREF': 'shank-' + unit}
    if spec['kind'] == 'multitool':
        return {'CUTTERLIBRF': 'tool-' + unit}
    if spec['kind'] in ('feeds_speeds', 'machining_data', 'process_force'):
        return {'PARTMAT': prefix + 'part_material', 'TOOLMAT': prefix + 'tool_material',
                'OPERTYPE': prefix + 'cut_method'}
    if spec['kind'] == 'tool_machining_data':
        return {'PARTMAT': prefix + 'part_material', 'CUTMTD': prefix + 'cut_method'}
    return {}


def reference_report(root, standards=None, nx_version=None, table_overrides=None, complete_override=False,
                     baseline_tables=None, include_candidates=False):
    """Check known relational fields. No dereferencing external/network paths."""
    tables, errors, warnings = {}, [], []
    for spec in SPECS.values():
        if table_overrides and spec['id'] in table_overrides:
            tables[spec['id']] = table_overrides[spec['id']]
            continue
        if complete_override:
            continue
        path = offline_path(root / spec['path'])
        if path.is_file():
            try:
                tables[spec['id']] = NativeTable.read(path)
            except DomainError as error:
                errors.append({'library': spec['id'], 'reason': str(error)})
    group_index = {identifier: table.groups() for identifier, table in tables.items()}
    candidate_cache = {}
    baseline_index = {}
    def baseline_groups(identifier):
        if identifier not in baseline_index:
            table = None
            if baseline_tables is not None:
                table = baseline_tables.get(identifier)
            elif standards and nx_version in standards:
                path = offline_path(standards[nx_version] / SPECS[identifier]['path'])
                if path.is_file():table = NativeTable.read(path)
            baseline_index[identifier] = table.groups() if table else {}
        return baseline_index[identifier]
    for identifier, table in tables.items():
        relations = reference_targets(identifier)
        baseline = baseline_groups(identifier)
        for key, records in table.groups().items():
            unchanged = baseline and key in baseline and records_hash(records) == records_hash(baseline[key])
            for record in records:
                for field, target in relations.items():
                    value = record['values'].get(field, '')
                    if value and (target not in tables or value not in group_index[target]):
                        entry = {'library': identifier, 'key': key, 'field': field, 'value': value,
                                 'target': target, 'reason': 'Missing library reference.',
                                 'standard_object': bool(unchanged)}
                        # Downgrade only a defect present in the target stock,
                        # not a valid stock relation broken by an omitted DB.
                        stock_warning = unchanged and value not in baseline_groups(target)
                        if include_candidates and not stock_warning:
                            # Similar identifiers are suggestions, NEVER aliases.
                            from difflib import SequenceMatcher
                            candidates = group_index.get(target, {})
                            closest = candidate_cache.get((target, value))
                            if closest is None:
                                closest = sorted(candidates, key=lambda k: (-SequenceMatcher(None, value, k).ratio(), k))[:20]
                                candidate_cache[(target, value)] = closest
                            entry['candidate_count'] = len(candidates)
                            entry['candidates'] = [{'key': k, 'label': next((candidates[k][0]['values'][f]
                                for f in ('MATNAM', 'MATNAME', 'DESCR', 'NAME') if candidates[k][0]['values'].get(f)), k)}
                                for k in closest]
                        (warnings if stock_warning else errors).append(entry)
    # Siemens' examples already contain many unresolved reference rows. Keep
    # them visible without returning megabytes of duplicate warning messages.
    summary = {}
    for warning in warnings:
        key = (warning['library'], warning['field'], warning['value'], warning['target'])
        entry = summary.setdefault(key, {**warning, 'affected_rows': 0, 'example_keys': []})
        entry['affected_rows'] += 1
        if len(entry['example_keys']) < 10 and warning['key'] not in entry['example_keys']:
            entry['example_keys'].append(warning['key'])
    return {'errors': errors, 'warnings': list(summary.values()), 'warning_rows': len(warnings),
            'automatic_geometry_validation': False}


def normalization_plan(source, standards, nx_version, legacy_version, defaults=None, resolutions=None, legacy_omissions=None,
                       source_version=None, reference_resolutions=None, include_target_libraries=False):
    """Read-only plan. Text results stay private; public report has fingerprints."""
    if nx_version not in standards or legacy_version not in standards:
        raise DomainError('Configure both target and original Siemens standard versions first.')
    if source_version is not None and source_version not in (nx_version, legacy_version):
        raise DomainError('Source version must identify the target or original standard version.')
    for identifier, blocks in (defaults or {}).items():
        spec_for(identifier)
        if not isinstance(blocks, dict) or any(not isinstance(fields, dict) for fields in blocks.values()):
            raise DomainError('Defaults must be library -> FORMAT block -> column -> string.')
        if any(not isinstance(value, str) or any(c in value for c in '|\r\n\x00')
               for fields in blocks.values() for value in fields.values()):
            raise DomainError('Invalid explicit schema default.')
    for identifier, sources in (resolutions or {}).items():
        spec = spec_for(identifier)
        if not isinstance(sources, dict) or any(path not in (spec['path'], spec['legacy_path']) for path in sources):
            raise DomainError('Conflict decisions must name a registered library source file.')
        if any(not isinstance(choices, dict) or any(v not in ('standard', 'customer') for v in choices.values()) for choices in sources.values()):
            raise DomainError('Conflict choices must be standard or customer, per library object.')
    target_root, old_root = standards[nx_version], standards[legacy_version]
    outputs, reports, conflicts, inputs = {}, [], [], {}
    # Unknown legacy databases must not disappear merely because the snapshot
    # filter omits old library paths. Only known active DBs and marked backups
    # are eligible for normalization/omission.
    known_legacy = {s['legacy_path'] for s in SPECS.values() if s['legacy_path']}
    seen_legacy = set()
    legacy_roots = {str(PurePosixPath(p).parent) for p in known_legacy}
    legacy_roots.add('MACH/resource/library/tool_backup/henning_dierk')
    for relative_root in sorted(legacy_roots):
        root = offline_path(source / relative_root)
        if not root.is_dir():
            continue
        for _, path in tree_files(root):
            relative = path.relative_to(source).as_posix()
            if not legacy_database(relative):
                continue
            seen_legacy.add(relative)
            inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            if relative in known_legacy:
                continue
            marked_backup = any('backup' in p.casefold() for p in PurePosixPath(relative).parts) or path.suffix.lower() == '.dat_bak'
            if not marked_backup and relative not in (legacy_omissions or []):
                conflicts.append({'library': 'unmapped-legacy-database', 'source': relative,
                                  'reason': 'Unknown legacy database requires an explicit adapter; not silently omitted.'})
    if any(path not in seen_legacy or path in known_legacy for path in (legacy_omissions or [])):
        raise DomainError('Explicit omissions may name only existing additional legacy databases, not active mapped libraries.')
    for spec in SPECS.values():
        source_path = offline_path(source / spec['path'])
        legacy_path = offline_path(source / spec['legacy_path']) if spec['legacy_path'] else None
        if not source_path.is_file() and not (legacy_path and legacy_path.is_file()):
            continue
        standard_path = offline_path(target_root / spec['path'])
        if not standard_path.is_file():
            conflicts.append({'library': spec['id'], 'reason': 'Target Siemens standard database missing.'})
            continue
        target = NativeTable.read(standard_path)
        inputs[str(standard_path)] = target.source_sha256
        combined = copy.deepcopy(target)
        # First standard-location customer data, then former Henning-Dierk data.
        # A migrated source DB uses the target-version schema; the legacy DB
        # still uses the original version. Select a matching baseline by schema.
        for customer_path in [p for p in (source_path, legacy_path) if p and p.is_file()]:
            customer = NativeTable.read(customer_path)
            inputs[str(customer_path)] = customer.source_sha256
            baseline_path = offline_path(old_root / spec['path'])
            baseline = NativeTable.read(baseline_path) if baseline_path.is_file() else None
            if baseline:
                inputs[str(baseline_path)] = baseline.source_sha256
            if customer_path == source_path and source_version != legacy_version and customer.blocks == target.blocks:
                baseline = target
            relative = customer_path.relative_to(source).as_posix()
            groups, report = merge_tables(combined, customer, baseline,
                                          (defaults or {}).get(spec['id']),
                                          (resolutions or {}).get(spec['id'], {}).get(relative, {}))
            reports.append({'library': spec['id'], 'source': customer_path.relative_to(source).as_posix(),
                            'target': spec['path'], **report['counts'], 'conflicts': len(report['conflicts'])})
            conflicts.extend({'library': spec['id'], 'source': customer_path.relative_to(source).as_posix(), **c}
                             for c in report['conflicts'])
            combined = NativeTable(combined.render(groups))
        outputs[spec['path']] = combined.text
    from .references import normalization_references
    outputs, references, applied = normalization_references(source, target_root, outputs, inputs,
        reference_resolutions or {}, include_target_libraries)
    conflicts.extend({**error, 'type': 'reference'} for error in references['errors'])
    return outputs, {'libraries': reports, 'conflicts': conflicts, 'can_import': not conflicts, 'input_hashes': inputs,
                     'references': references, 'reference_resolutions': applied,
                     'target_version': nx_version, 'legacy_version': legacy_version,
                     'source_version': source_version,
                     'explicit_legacy_omissions': legacy_omissions or [],
                     'optional_palette_omitted': OPTIONAL_PALETTE,
                     'result_hashes': {p: hashlib.sha256(t.encode('utf-8-sig')).hexdigest() for p, t in outputs.items()}}
