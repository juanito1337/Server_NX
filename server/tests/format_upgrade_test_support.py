"""Every registered FORMAT, synthetic values only; no NX or real Store use."""
import copy

from nxserver.libraries import NativeTable, SPECS, reference_targets
from full_upgrade_test_support import definitions, row


def object_key(prefix, library_index, block_index):
    return f'{prefix}_L{library_index:03}_B{block_index:03}'


def prepare_formats(store, source, data):
    entries = data['libraries']
    indexes = {entry['library_id']: index for index, entry in enumerate(entries)}
    defaults, omissions, expected = {}, {}, {}
    counts = {'old_formats': 0, 'projected_formats': 0, 'ambiguous_formats': 0,
              'target_formats': 0, 'stock_groups': 0, 'custom_groups': 0, 'legacy_groups': 0}
    for index, entry in enumerate(entries):
        identifier = entry['library_id']
        spec = SPECS[identifier]
        # Installed mode keeps exactly the real target fields. Only the wholly
        # synthetic mode adds an artificial column to require a decision too.
        target = definitions(entry['versions']['2512']['blocks'], extra=data['mode'] == 'synthetic')
        refs = {field: object_key('STOCK', indexes[library], 0)
                for field, library in reference_targets(identifier).items()}
        stock = {}
        for number, block in enumerate(target.blocks):
            key = object_key('STOCK', index, number)
            record = row(target, block['id'], key, 'Synthetic target stock', refs, 'TARGET_TEST_VALUE')
            for field in block['fields']:
                if field not in {'LIBRF', 'NAME', 'RTYPE', 'SEQ', *refs}:
                    record['values'][field] = f'TARGET_TEST_{number}_{field}'
            stock[key] = [record]
        new_file = store.standards['2512'] / spec['path']
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text(target.render(stock), encoding='utf-8-sig')
        counts['target_formats'] += len(target.blocks)
        counts['stock_groups'] += len(stock)
        expected[identifier] = {'blocks': copy.deepcopy(target.blocks), 'groups': copy.deepcopy(stock), 'omitted': []}
        old_info = entry['versions']['2406']
        if old_info is None:
            continue
        baseline = definitions(old_info['blocks'])
        old_stock, customer, legacy = {}, {}, {}
        for number, block in enumerate(baseline.blocks):
            counts['old_formats'] += 1
            stock_key = object_key('STOCK', index, number)
            old_stock[stock_key] = [row(baseline, block['id'], stock_key, 'Synthetic old stock', refs)]
            candidates = [other for other in target.blocks if other['class_name'] == block['class_name']
                          and set(block['fields']) <= set(other['fields'])]
            assert candidates, (identifier, block['id'])
            unique = len(candidates) == 1
            counts['projected_formats' if unique else 'ambiguous_formats'] += 1
            for prefix, groups, relative, counter in (
                ('CUSTOM', customer, spec['path'], 'custom_groups'),
                ('HD', legacy, spec['legacy_path'], 'legacy_groups'),
            ):
                if relative is None:
                    continue
                key = object_key(prefix, index, number)
                record = row(baseline, block['id'], key, 'Synthetic ' + prefix, refs)
                for field in block['fields']:
                    if field not in {'LIBRF', 'NAME', 'RTYPE', 'SEQ', *refs}:
                        record['values'][field] = f'SOURCE_TEST_{prefix}_{number}_{field}'
                groups[key] = [record]
                if not unique:
                    # All synthetic target examples use the same RTYPE: none
                    # can disambiguate these layouts. Never pick by block order.
                    omissions.setdefault(identifier, {}).setdefault(relative, {})[key] = 'standard'
                    expected[identifier]['omitted'].append(key)
                    continue
                selected = candidates[0]
                values = dict(record['values'])
                for field in selected['fields']:
                    if field not in values:
                        value = 'EXPLICIT_TEST_ONLY_' + field
                        defaults.setdefault(identifier, {}).setdefault(selected['id'], {})[field] = value
                        values[field] = value
                expected[identifier]['groups'][key] = [{'block_id': selected['id'],
                    'class_name': selected['class_name'], 'values': values}]
                counts[counter] += 1
        old_file = store.standards['2406'] / spec['path']
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text(baseline.render(old_stock), encoding='utf-8-sig')
        source_file = source / spec['path']
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(baseline.render({**old_stock, **customer}), encoding='utf-8-sig')
        if spec['legacy_path']:
            legacy_file = source / spec['legacy_path']
            legacy_file.parent.mkdir(parents=True, exist_ok=True)
            legacy_file.write_text(baseline.render(legacy), encoding='utf-8-sig')
    return defaults, omissions, expected, counts


def column_decisions(defaults):
    return {(identifier, block, field) for identifier, blocks in defaults.items()
            for block, fields in blocks.items() for field in fields}


def verify_formats(folder, expected):
    for identifier, value in expected.items():
        table = NativeTable.read(folder / SPECS[identifier]['path'])
        assert table.blocks == value['blocks']
        assert table.groups() == value['groups'], identifier
        assert not set(value['omitted']) & set(table.groups())
        if SPECS[identifier]['legacy_path']:
            assert not (folder / SPECS[identifier]['legacy_path']).exists()


def omission_keys(omissions):
    return {(identifier, path, key) for identifier, paths in omissions.items()
            for path, keys in paths.items() for key in keys}
