"""Whole-folder/API integration, synthetic values and optional installed schemas."""
import json
from pathlib import Path

from nxserver.libraries import reference_report
from test_upgrades import upgrade_pilot, preview, finish
from test_management import HEADERS
from full_upgrade_test_support import all_schema_data, prepare, verify


def test_all_actual_library_definitions_flow_through_one_reviewed_upgrade(upgrade_pilot, all_schema_data):
    client, store, request, source = upgrade_pilot
    defaults, expected = prepare(store, source, all_schema_data)
    before = {str(root): store.manifest(root) for root in (source, *store.standards.values(), *store.sidts.values())}
    initial, blocked = preview(client, request)
    assert not blocked['can_build'] and not Path(request['target_path']).exists()
    assert {entry['library_id'] for entry in blocked['new_columns']} == set(defaults)
    assert all(not entry['confirmed'] for entry in blocked['new_columns'])
    request['defaults'] = defaults
    checked, report = preview(client, request)
    assert report['can_build'], json.dumps(report['conflicts'], ensure_ascii=False)[:12000]
    assert all(entry['confirmed'] for entry in report['new_columns'])
    result = finish(client, client.post('/api/v1/migrations/build', headers=HEADERS,
        json={**request, 'preview_id': checked['id'], 'preview_hash': report['preview_hash']}))
    assert result['status'] == 'completed', result
    folder = Path(request['target_path'])
    counts = verify(folder, expected)
    references = reference_report(folder, store.standards, '2512')
    assert not references['errors'], references['errors']
    release = store.get_release(result['release_id'])
    assert release['test_launcher'] and not release['review'] and store.active()['release_id'] is None
    for root in (source, *store.standards.values(), *store.sidts.values()):
        assert store.manifest(root) == before[str(root)]
    print(json.dumps({'schema_mode': all_schema_data['mode'], 'counts': counts, 'new_column_decisions': len(report['new_columns']),
        'reference_errors': len(references['errors']), 'starter_automatic': bool(release['test_launcher']),
        'active': store.active()['release_id'], 'nx_executed': False, 'geometry_qualified': False}))
