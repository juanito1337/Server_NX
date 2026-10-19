"""Old conflict controls cannot modify a newer form or repair malformed input."""
import os

import pytest

from test_upgrade_conflicts_browser import conflicts
from test_upgrade_recovery_browser import wizard, jobs

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated conflict control context.')


@pytest.mark.parametrize('change', ['filter', 'scope', 'preview', 'logout', 'tab'])
def test_old_or_hidden_control_does_not_write_decisions_after_context_change(wizard, change):
    from playwright.sync_api import expect
    page, store, source, target = wizard; conflicts(store, source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts select')).to_have_count(3, timeout=15000)
    page.evaluate('globalThis.__oldConflict=document.querySelector("#upgrade-file-conflicts select")')
    if change == 'filter': page.locator('#upgrade-conflict-query').fill('not present')
    elif change == 'scope':
        page.locator('#target-path').fill(str(target) + '-changed'); page.locator('#target-path').fill(str(target))
    elif change == 'preview': page.locator('#upgrade-preview').click()
    elif change == 'logout': page.locator('#disconnect').click()
    else: page.locator('#tab-prepare').click()
    before = page.locator('#upgrade-file-resolutions').input_value()
    page.evaluate('''async()=>{__oldConflict.value='customer';__oldConflict.dispatchEvent(new Event('change',{bubbles:true}));await __oldConflict.onchange();}''')
    expect(page.locator('#upgrade-file-resolutions')).to_have_value(before)
    assert not target.exists() and store.active()['release_id'] is None


@pytest.mark.parametrize('raw', ['[]', '{"unrelated":"not-a-choice"}'])
def test_invalid_raw_choices_remain_untouched_and_disable_dropdowns(wizard, raw):
    from playwright.sync_api import expect
    page, store, source, target = wizard; conflicts(store, source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts select')).to_have_count(3, timeout=15000)
    page.locator('#upgrade-file-resolutions').fill(raw)
    expect(page.locator('#upgrade-conflict-state')).to_contain_text('kein gültiges Objekt')
    for select in page.locator('#upgrade-file-conflicts select').all(): expect(select).to_be_disabled()
    expect(page.locator('#upgrade-file-resolutions')).to_have_value(raw)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert len(jobs(store)) == 1 and not target.exists()
