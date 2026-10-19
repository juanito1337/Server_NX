"""Real FORMAT names/new fields through visible upgrade controls; no real NX."""
import json
import os

import pytest

from nxserver.libraries import reference_report
from full_upgrade_test_support import all_schema_data
from format_upgrade_test_support import prepare_formats, column_decisions, verify_formats, omission_keys
from test_upgrade_recovery_browser import wizard

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated FORMAT matrix GUI; no NX/models.')


def test_all_format_new_fields_and_ambiguous_groups_use_explicit_gui_decisions(wizard, all_schema_data):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    defaults, omissions, expected, counts = prepare_formats(store, source, all_schema_data)
    roots = (source, *store.standards.values(), *store.sidts.values())
    before = {str(root): store.manifest(root) for root in roots}
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-columns fieldset')).to_have_count(len(column_decisions(defaults)), timeout=60000)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert not target.exists() and store.releases() == []
    for identifier, blocks in defaults.items():
        for block, fields in blocks.items():
            for field, value in fields.items():
                group = page.locator('#upgrade-columns fieldset').filter(
                    has=page.get_by_text(f'{identifier} · {block}.{field}', exact=True))
                group.locator('input:not([type="checkbox"])').fill(value)
                group.get_by_role('checkbox').check()
    assert json.loads(page.locator('#import-defaults').input_value()) == defaults
    page.locator('#upgrade-preview').click()
    if omissions:
        expect(page.locator('#upgrade-state')).to_contain_text(f'{len(omission_keys(omissions))} Konflikte.', timeout=60000)
        expect(page.locator('#upgrade-build')).to_be_disabled()
        for identifier, paths in omissions.items():
            for path, keys in paths.items():
                for key in keys:
                    conflict = page.locator('#conflicts tr').filter(has=page.get_by_text(identifier + ' / ' + key, exact=True))
                    expect(conflict).to_contain_text(path)
                    conflict.get_by_role('combobox').select_option('standard')
        assert json.loads(page.locator('#import-resolutions').input_value()) == omissions
        assert not target.exists() and store.releases() == []
        page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=60000)
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt', timeout=60000)
    verify_formats(target, expected)
    assert not reference_report(target, store.standards, '2512')['errors']
    assert len(store.releases()) == 1 and store.releases()[0]['test_launcher']
    assert not store.releases()[0]['review'] and store.active()['release_id'] is None
    for root in roots:
        assert store.manifest(root) == before[str(root)]
    print(json.dumps({'schema_mode': all_schema_data['mode'], 'counts': counts,
        'new_fields_confirmed_in_controls': len(column_decisions(defaults)),
        'explicit_group_omissions': len(omission_keys(omissions)), 'all_emitted_values_exact': True, 'nx_executed': False}))
