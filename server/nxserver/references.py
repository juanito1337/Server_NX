"""Explicit, preview-bound native reference corrections, never fuzzy aliases."""
import copy

from .libraries import NativeTable, SPECS, reference_report, reference_targets, spec_for
from .safety import DomainError, offline_path


def choices(value):
    if not isinstance(value, dict):
        raise DomainError('Reference decisions must be library -> object -> field -> old value -> target key.')
    result = []
    for library, groups in value.items():
        spec_for(library)
        if not isinstance(groups, dict):
            raise DomainError('Invalid reference decision groups.')
        for key, fields in groups.items():
            if not isinstance(key, str) or not key or len(key) > 500 or not isinstance(fields, dict):
                raise DomainError('Invalid reference object key.')
            for field, values in fields.items():
                if field not in reference_targets(library) or not isinstance(values, dict):
                    raise DomainError('Column is not a known relation in this library: ' + str(field))
                for previous, target in values.items():
                    if any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 500
                           or any(c in v for c in '|\r\n\x00') for v in (previous, target)) or previous == target:
                        raise DomainError('Reference corrections require distinct, non-empty exact keys.')
                    result.append((library, key, field, previous, target))
                    if len(result) > 1000:
                        raise DomainError('At most 1,000 explicit reference corrections per preview.')
    # Object insertion order changes on durable canonical JSON roundtrips.
    # These independently validated exact corrections are an unordered mapping;
    # keep application/report order deterministic, not native DAT row order.
    return sorted(result)


def normalization_references(source, target_root, outputs, inputs, decisions, include_target=False):
    """Check the actual planned libraries, not unavailable fallback databases.

    Prepared-folder import contains source files + merged outputs. Whole-share
    upgrades also contain the new installation baseline. All baseline bytes used
    for the decisions/warning classification are included in the input binding.
    """
    selected = choices(decisions)
    tables, baselines = {}, {}

    def read(path):
        table = NativeTable.read(offline_path(path))
        if str(path) in inputs and inputs[str(path)] != table.source_sha256:
            raise DomainError('Reference input changed during planning.', 409)
        inputs[str(path)] = table.source_sha256
        return table

    for identifier, spec in SPECS.items():
        baseline = offline_path(target_root / spec['path'])
        if baseline.is_file():
            baselines[identifier] = read(baseline)
        if spec['path'] in outputs:
            tables[identifier] = NativeTable(outputs[spec['path']])
        elif include_target and identifier in baselines:
            tables[identifier] = baselines[identifier]
        else:
            path = offline_path(source / spec['path'])
            if path.is_file():
                tables[identifier] = read(path)
    before = reference_report(source, table_overrides=tables, complete_override=True,
                              baseline_tables=baselines)
    eligible = {(e['library'], e['key'], e['field'], e['value'])
                for e in before['errors'] if 'value' in e and not e.get('standard_object')}
    changed, applied = {}, []
    for library, key, field, previous, target_key in selected:
        if (library, key, field, previous) not in eligible:
            raise DomainError('Reference decision no longer matches an unresolved customer reference: '
                              + library + ' / ' + key + ' / ' + field, 409)
        target_library = reference_targets(library)[field]
        if target_library not in tables or target_key not in tables[target_library].groups():
            raise DomainError('Reference target does not exist in the planned active library: '
                              + target_library + ' / ' + target_key, 409)
        if library not in changed:
            changed[library] = copy.deepcopy(tables[library])
        groups = changed[library].groups()
        count = 0
        for record in groups[key]:
            if record['values'].get(field) == previous:
                record['values'][field] = target_key
                count += 1
        if not count:
            raise DomainError('Reference decision overlaps another correction.', 409)
        # Reparse rendered bytes, preserving grouping, FORMATs and comments.
        changed[library] = NativeTable(changed[library].render(groups))
        applied.append({'library': library, 'key': key, 'field': field, 'previous': previous,
                        'value': target_key, 'target': target_library, 'affected_rows': count})
    for library, table in changed.items():
        tables[library] = table
        outputs[SPECS[library]['path']] = table.text
    after = reference_report(source, table_overrides=tables, complete_override=True,
                             baseline_tables=baselines, include_candidates=True)
    return outputs, after, applied
