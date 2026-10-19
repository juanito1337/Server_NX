"""All registered libraries; synthetic data, optional read-only installed schemas."""
import copy
import hashlib
import os

import pytest

from nxserver.libraries import NativeTable, SPECS, OPTIONAL_PALETTE, reference_targets, decode
from nxserver.safety import offline_path, read_bounded_file

EXTRA = 'NX_UPGRADE_TEST_ONLY_COLUMN'


def definitions(blocks, extra=False):
    output = []
    for block in blocks:
        fields = [*block['fields'], *([EXTRA] if extra else [])]
        assert EXTRA not in block['fields']
        output.extend(['#CLASS ' + block['class_name'] + '\n', 'FORMAT ' + ' '.join(fields) + '\n'])
    output.append('#END_DATA\n')
    return NativeTable(''.join(output))


def key(prefix, index):
    return prefix + '_L' + str(index).zfill(3)


def row(table, block_id, identifier, label, references, extra_value=None):
    block = table.block(block_id)
    values = {field: '' for field in block['fields']}
    values['LIBRF' if 'LIBRF' in values else 'NAME'] = identifier
    for field in ('DESCR', 'MATNAM', 'MATNAME'):
        if field in values:
            values[field] = label
    for field in ('RTYPE', 'SEQ'):
        if field in values:
            values[field] = '1'
    for field, value in references.items():
        if field in values:
            values[field] = value
    if EXTRA in values:
        values[EXTRA] = extra_value
    return {'block_id': block_id, 'class_name': block['class_name'], 'values': values}


def prepare(store, source, data):
    # No original DATA/model bytes enter the fixture. Definitions are generated
    # or supplied by the separately guarded, read-only installed-schema reader.
    entries = data['libraries']
    assert len(entries) == len(SPECS)
    indexes = {entry['library_id']: index for index, entry in enumerate(entries)}
    defaults, expected = {}, {}
    old, new = store.standards['2406'], store.standards['2512']
    for index, entry in enumerate(entries):
        identifier = entry['library_id']
        spec = SPECS[identifier]
        old_info, new_info = entry['versions']['2406'], entry['versions']['2512']
        target = definitions(new_info['blocks'], extra=True)
        selected = entry['unambiguous_projections'][0] if old_info else None
        target_block = selected['new_block'] if selected else target.blocks[0]['id']
        stock_refs = {field: key('STOCK', indexes[library]) for field, library in reference_targets(identifier).items()}
        stock = row(target, target_block, key('STOCK', index), 'Synthetic NEW stock', stock_refs, 'TARGET_STOCK_2512')
        file = new / spec['path'];file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(target.render({key('STOCK', index): [stock]}), encoding='utf-8-sig')
        expected[identifier] = {'stock_key': key('STOCK', index), 'target_block': target_block,
            'target_schemas': copy.deepcopy(target.blocks), 'custom_key': None, 'legacy_key': None}
        if not old_info:
            continue
        baseline = definitions(old_info['blocks'])
        old_stock = row(baseline, selected['old_block'], key('STOCK', index), 'Synthetic OLD stock', stock_refs)
        old_text = baseline.render({key('STOCK', index): [old_stock]})
        file = old / spec['path'];file.parent.mkdir(parents=True, exist_ok=True);file.write_text(old_text, encoding='utf-8-sig')
        refs = {field: key('CUSTOM', indexes[library]) for field, library in reference_targets(identifier).items()}
        custom = row(baseline, selected['old_block'], key('CUSTOM', index), 'Synthetic customer ' + identifier, refs)
        source_text = baseline.render({key('STOCK', index): [old_stock], key('CUSTOM', index): [custom]})
        file = source / spec['path'];file.parent.mkdir(parents=True, exist_ok=True);file.write_text(source_text, encoding='utf-8-sig')
        expected[identifier]['custom_key'] = key('CUSTOM', index)
        defaults[identifier] = {target_block: {EXTRA: 'EXPLICIT_REVIEWED_2512'}}
        if spec['legacy_path']:
            legacy_refs = {field: key('HD' if SPECS[library]['legacy_path'] else 'CUSTOM', indexes[library])
                for field, library in reference_targets(identifier).items()}
            legacy = row(baseline, selected['old_block'], key('HD', index), 'Synthetic former HD ' + identifier, legacy_refs)
            file = source / spec['legacy_path'];file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(baseline.render({key('HD', index): [legacy]}), encoding='utf-8-sig')
            expected[identifier]['legacy_key'] = key('HD', index)
    (source / 'MACH/custom').mkdir(parents=True, exist_ok=True)
    (source / 'MACH/custom/customer.txt').write_text('Synthetic customer customization', encoding='utf-8')
    (source / OPTIONAL_PALETTE).write_text('<Deliberately invalid optional palette', encoding='utf-8')
    # Plain opaque fixture bytes, never passed to a real NX process.
    for extension in ('.ddb','.rtb','.grb','.gly','.csb','.abr','.stb','.dtx','.crb'):
        file = source / ('UGII/menus/profiles/All/SYNTHETIC' + extension)
        file.parent.mkdir(parents=True, exist_ok=True);file.write_bytes(b'SYNTHETIC; NOT AN NX PROFILE\x00' + extension.encode())
    return defaults, expected


def verify(folder, expected):
    counts = {'libraries': 0, 'stock': 0, 'custom': 0, 'legacy': 0}
    for identifier, value in expected.items():
        table = NativeTable.read(folder / SPECS[identifier]['path'])
        assert table.blocks == value['target_schemas']
        groups = table.groups()
        wanted = {value['stock_key'], *[value[name] for name in ('custom_key','legacy_key') if value[name]]}
        assert set(groups) == wanted, (identifier, set(groups), wanted)
        assert groups[value['stock_key']][0]['values'][EXTRA] == 'TARGET_STOCK_2512'
        counts['libraries'] += 1;counts['stock'] += 1
        for name, count in (('custom_key','custom'), ('legacy_key','legacy')):
            if value[name]:
                assert groups[value[name]][0]['values'][EXTRA] == 'EXPLICIT_REVIEWED_2512'
                counts[count] += 1
        if SPECS[identifier]['legacy_path']:
            assert not (folder / SPECS[identifier]['legacy_path']).exists()
    assert not (folder / OPTIONAL_PALETTE).exists()
    return counts

def synthetic_schema_data():
    entries = []
    for identifier, spec in SPECS.items():
        key_field = 'NAME' if spec['kind'] in ('machine', 'device', 'part_material', 'tool_material', 'cut_method') else 'LIBRF'
        fields = [key_field, 'RTYPE', 'DESCR', *reference_targets(identifier)]
        fields = list(dict.fromkeys(fields))
        old = NativeTable('#CLASS SYNTHETIC_INTEGRATION\nFORMAT ' + ' '.join(fields) + '\n#END_DATA\n')
        # Reverse the target column order too: migration must use field names.
        new = NativeTable('#CLASS SYNTHETIC_INTEGRATION\nFORMAT ' + ' '.join(reversed(fields)) + '\n#END_DATA\n')
        absent_old = identifier in ('fixture-metric', 'fixture-english')
        entries.append({'library_id': identifier, 'versions': {
            '2406': None if absent_old else {'blocks': old.blocks},
            '2512': {'blocks': new.blocks}},
            'unambiguous_projections': [] if absent_old else [{
                'old_block': old.blocks[0]['id'], 'new_block': new.blocks[0]['id']}]})
    return {'mode': 'synthetic', 'libraries': entries, 'original_inputs': {}}


def installed_schema_data():
    roots = {version: offline_path('C:/Siemens/NX' + version) for version in ('2406', '2512')}
    rows, inputs = [], {}
    for identifier, spec in SPECS.items():
        values = {}
        for version, root in roots.items():
            path = offline_path(root / spec['path'])
            if not path.is_file():
                values[version] = None
                continue
            raw = read_bounded_file(path, max_bytes=32*1024*1024)
            inputs[str(path)] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
            table = NativeTable(decode(raw), schema_only=True, max_schemas=1000, max_fields=100000)
            values[version] = {'blocks': copy.deepcopy(table.blocks)}
        assert values['2512'] is not None, 'Configured target stock library missing: ' + identifier
        choices = []
        if values['2406']:
            for block in values['2406']['blocks']:
                matching = [other for other in values['2512']['blocks'] if other['class_name'] == block['class_name']
                    and set(block['fields']) <= set(other['fields'])]
                if len(matching) == 1:
                    choices.append({'old_block': block['id'], 'new_block': matching[0]['id']})
            assert choices, 'No unambiguous installed schema projection: ' + identifier
        rows.append({'library_id': identifier, 'versions': values, 'unambiguous_projections': choices})
    return {'mode': 'installed', 'libraries': rows, 'original_inputs': inputs}


def verify_original_inputs(data):
    for path, metadata in data['original_inputs'].items():
        read_bounded_file(offline_path(path), max_bytes=32*1024*1024,
            expected_size=metadata['size'], expected_sha256=metadata['sha256'])


@pytest.fixture(params=['synthetic', 'installed'])
def all_schema_data(request):
    if request.param == 'installed':
        if os.name != 'nt' or os.environ.get('NX_STOCK_SCHEMA_TEST') != '1':
            pytest.skip('Opt-in NX_STOCK_SCHEMA_TEST=1: read-only installed DAT schemas on Windows, never NX/models.')
        data = installed_schema_data()
    else:
        data = synthetic_schema_data()
    try:
        yield data
    finally:
        verify_original_inputs(data)
