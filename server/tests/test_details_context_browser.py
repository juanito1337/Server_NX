"""Read-only detail navigation on disposable data, no NX or real account."""
import os

import pytest

from test_requests_browser import ui, hold, track
from nxserver.integrity import snapshot

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated detail-context Edge tests.')


def pending(page, release, source, error=False):
    if source == 'release':
        button = page.locator('#releases tr').filter(has_text=release['id']).get_by_role('button', name='Details', exact=True)
        button.evaluate('(node)=>node.id="isolated-detail-request"')
        identifier, suffix = 'isolated-detail-request', '/api/v1/releases/' + release['id']
    else:
        identifier, suffix = source, '/api/v1/' + ('openapi.json' if source == 'openapi' else 'audit')
    hold(page, suffix, error)
    track(page, identifier)
    page.locator('#' + identifier).click()
    page.wait_for_function('()=>window.__held')


def finish(page):
    page.evaluate('window.__releaseHeld()')
    page.wait_for_function('()=>window.__oldActionFinished')


@pytest.mark.parametrize('source', ['audit', 'openapi', 'release'])
@pytest.mark.parametrize('error', [False, True])
@pytest.mark.parametrize('tab', ['prepare', 'update'])
def test_tab_aba_does_not_reopen_an_old_detail_reply(ui, source, error, tab):
    from playwright.sync_api import expect
    page, store, release, _ = ui
    before = snapshot(store)['sha256']
    pending(page, release, source, error)
    page.locator('#tab-' + tab).click()
    page.locator('#tab-manage').click()
    page.evaluate('message("Newer workspace notice")')
    finish(page)
    expect(page.locator('#details-box')).not_to_have_attribute('open', '')
    expect(page.locator('#message')).to_have_text('Newer workspace notice')
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('source', ['audit', 'openapi', 'release'])
@pytest.mark.parametrize('error', [False, True])
@pytest.mark.parametrize('close', ['mouse', 'keyboard'])
def test_explicitly_closed_details_are_not_reopened_by_an_old_reply(ui, source, error, close):
    from playwright.sync_api import expect
    page, store, release, _ = ui
    database = snapshot(store)['sha256']
    page.evaluate('details({synthetic:"existing details"})')
    before = page.locator('#details').inner_text()
    pending(page, release, source, error)
    if close == 'mouse':
        page.locator('#details-box > summary').click()
    else:
        page.locator('#details-box > summary').focus()
        page.locator('#details-box > summary').press('Enter')
    page.evaluate('message("Newer closing notice")')
    finish(page)
    expect(page.locator('#details-box')).not_to_have_attribute('open', '')
    expect(page.locator('#details')).to_have_text(before)
    expect(page.locator('#message')).to_have_text('Newer closing notice')
    assert snapshot(store)['sha256'] == database


@pytest.mark.parametrize('source', ['audit', 'openapi', 'release'])
@pytest.mark.parametrize('error', [False, True])
def test_a_newer_detail_result_keeps_its_own_contents(ui, source, error):
    from playwright.sync_api import expect
    page, store, release, _ = ui
    before = snapshot(store)['sha256']
    pending(page, release, source, error)
    page.evaluate('details({synthetic:"newer detail result"});message("Newer detail notice")')
    content = page.locator('#details').inner_text()
    finish(page)
    expect(page.locator('#details')).to_have_text(content)
    expect(page.locator('#details-box')).to_have_attribute('open', '')
    expect(page.locator('#message')).to_have_text('Newer detail notice')
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('source', ['audit', 'openapi', 'release'])
def test_current_detail_read_still_opens_and_preserves_editor(ui, source):
    from playwright.sync_api import expect
    page, store, release, _ = ui
    before = snapshot(store)['sha256']
    page.locator('#tab-prepare').click()
    page.locator('#name').fill('Unsaved detail-context editor')
    page.locator('#tab-manage').click()
    pending(page, release, source)
    # Clicking the already selected workspace is not a change of context.
    page.locator('#tab-manage').click()
    finish(page)
    expect(page.locator('#details-box')).to_have_attribute('open', '')
    expect(page.locator('#details')).not_to_have_text('Noch keine Prüfung ausgewählt.')
    page.locator('#tab-prepare').click()
    expect(page.locator('#name')).to_have_value('Unsaved detail-context editor')
    assert snapshot(store)['sha256'] == before
