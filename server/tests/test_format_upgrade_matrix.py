"""All FORMAT definitions: exact values, explicit new fields, no guessed layout."""
import json
from pathlib import Path

from nxserver.libraries import reference_report
from full_upgrade_test_support import all_schema_data
from format_upgrade_test_support import prepare_formats, column_decisions, verify_formats, omission_keys
from test_management import HEADERS
from test_upgrades import upgrade_pilot, preview, finish


def test_all_old_formats_project_or_require_an_explicit_omission(upgrade_pilot, all_schema_data):
    client, store, request, source = upgrade_pilot
    defaults, omissions, expected, counts = prepare_formats(store, source, all_schema_data)
    roots = (source, *store.standards.values(), *store.sidts.values())
    before = {str(root): store.manifest(root) for root in roots}
    _, report = preview(client, request)
    assert not report['can_build'] and not Path(request['target_path']).exists()
    assert {(entry['library_id'], entry['block_id'], entry['field']) for entry in report['new_columns']} == column_decisions(defaults)
    assert all(not entry['confirmed'] for entry in report['new_columns'])
    request['defaults'] = defaults
    checked, report = preview(client, request)
    assert all(entry['confirmed'] for entry in report['new_columns'])
    if omissions:
        assert not report['can_build']
        assert {(item['library'], item['source'], item['key']) for item in report['conflicts']} == omission_keys(omissions)
        assert all(item['type'] == 'schema' and 'ambiguous' in item['reason'] for item in report['conflicts'])
        denied = client.post('/api/v1/migrations/build', headers=HEADERS,
            json={**request, 'preview_id': checked['id'], 'preview_hash': report['preview_hash']})
        assert denied.status_code == 409 and store.releases() == [] and not Path(request['target_path']).exists()
        request['resolutions'] = omissions
        checked, report = preview(client, request)
    assert report['can_build'], json.dumps(report['conflicts'])[:12000]
    result = finish(client, client.post('/api/v1/migrations/build', headers=HEADERS,
        json={**request, 'preview_id': checked['id'], 'preview_hash': report['preview_hash']}))
    assert result['status'] == 'completed', result
    verify_formats(Path(request['target_path']), expected)
    assert not reference_report(Path(request['target_path']), store.standards, '2512')['errors']
    release = store.get_release(result['release_id'])
    assert release['test_launcher'] and not release['review'] and store.active()['release_id'] is None
    for root in roots:
        assert store.manifest(root) == before[str(root)]
    print(json.dumps({'schema_mode': all_schema_data['mode'], 'counts': counts,
        'new_column_decisions': len(column_decisions(defaults)), 'explicit_group_omissions': len(omission_keys(omissions)),
        'all_emitted_values_exact': True, 'nx_executed': False, 'geometry_qualified': False}))
