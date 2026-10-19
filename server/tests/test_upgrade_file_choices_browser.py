"""Case-insensitive file choices stay explicit and editable in an isolated GUI."""
import json
import os

import pytest

from test_upgrade_conflicts_browser import conflicts
from test_upgrade_recovery_browser import wizard, jobs

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated file choice aliases.')

RELATIVE = 'MACH/custom/conflicts/changed.txt'


@pytest.mark.parametrize('values', [('standard', 'customer'), ('customer', 'standard'), ('customer', 'customer')])
def test_duplicate_aliases_disable_controls_preserve_raw_input_and_allow_correction(wizard, values):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    conflicts(store, source)
    before = store.manifest(source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts select')).to_have_count(3, timeout=15000)
    raw = json.dumps(dict(zip([RELATIVE, RELATIVE.lower()], values)))
    page.locator('#upgrade-file-resolutions').fill(raw)
    for control in page.locator('#upgrade-file-conflicts select').all():
        expect(control).to_be_disabled()
    expect(page.locator('#upgrade-conflict-state')).to_contain_text('eindeutigen Pfaden')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#message')).to_contain_text('Mehrdeutige Dateientscheidungen')
    expect(page.locator('#upgrade-file-resolutions')).to_have_value(raw)
    assert page.evaluate("sessionStorage.getItem('nx.pending-migration-request.v1')") is None
    assert len(jobs(store)) == 1
    page.locator('#upgrade-file-resolutions').fill(json.dumps({RELATIVE.lower(): 'customer'}))
    control = page.locator('#upgrade-file-conflicts tr').filter(has_text=RELATIVE).locator('select')
    expect(control).to_be_enabled()
    expect(control).to_have_value('customer')
    expect(page.locator('#upgrade-conflict-state')).to_contain_text('1 ausdrücklich entschieden')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts select')).to_have_count(2, timeout=15000)
    assert len(jobs(store)) == 2 and store.manifest(source) == before
    assert not target.exists() and store.active()['release_id'] is None


def test_dropdown_edits_or_removes_existing_case_alias_without_creating_a_second_key(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    conflicts(store, source)
    before = store.manifest(source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts select')).to_have_count(3, timeout=15000)
    page.locator('#upgrade-file-resolutions').fill(json.dumps({RELATIVE.lower(): 'customer'}))
    control = page.locator('#upgrade-file-conflicts tr').filter(has_text=RELATIVE).locator('select')
    expect(control).to_have_value('customer')
    control.select_option('standard')
    assert json.loads(page.locator('#upgrade-file-resolutions').input_value()) == {RELATIVE.lower(): 'standard'}
    control.select_option('')
    assert json.loads(page.locator('#upgrade-file-resolutions').input_value()) == {}
    choices = {'mach/custom/conflicts/changed.txt': 'customer',
        'mach/custom/conflicts/removed.txt': 'standard', 'mach/custom/conflicts/added.txt': 'standard'}
    page.locator('#upgrade-file-resolutions').fill(json.dumps(choices))
    expect(page.locator('#upgrade-conflict-state')).to_contain_text('3 ausdrücklich entschieden')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    assert json.loads(page.locator('#upgrade-file-resolutions').input_value()) == choices
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt', timeout=15000)
    assert (target / RELATIVE).read_text() == 'Customer changed'
    assert (target / 'MACH/custom/conflicts/added.txt').read_text() == 'New Siemens collision'
    assert not (target / 'MACH/custom/conflicts/removed.txt').exists()
    assert store.manifest(source) == before and store.active()['release_id'] is None


def test_server_authoritative_unicode_alias_error_clears_pending_acceptance_without_touching_input(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    conflicts(store, source)
    before = store.manifest(source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts select')).to_have_count(3, timeout=15000)
    # JS lowercase is a convenience, not the server's full Unicode casefold.
    # The authoritative API rejection must also remain safe and recoverable.
    raw = json.dumps({'MACH/custom/Straße.txt': 'customer', 'mach/custom/STRASSE.txt': 'standard'})
    page.locator('#upgrade-file-resolutions').fill(raw)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#message')).to_contain_text('case-insensitive', timeout=15000)
    expect(page.locator('#upgrade-file-resolutions')).to_have_value(raw)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert page.evaluate("sessionStorage.getItem('nx.pending-migration-request.v1')") is None
    assert len(jobs(store)) == 1 and store.manifest(source) == before
    page.locator('#upgrade-file-resolutions').fill('{}')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts select')).to_have_count(3, timeout=15000)
    expect(page.locator('#upgrade-preview')).to_be_enabled(timeout=15000)
    assert len(jobs(store)) == 2 and not target.exists() and store.active()['release_id'] is None
