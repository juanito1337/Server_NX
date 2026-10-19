"""Explicit per-release actions; synthetic folders, no real NX or activation."""
import os

import pytest

from test_requests_browser import ui, hold, track
from nxserver.integrity import snapshot
from test_history_browser import hold as named_hold, track as named_track, wait_held, unhold

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated release-action Edge tests.')


def action(page, target, mode='validate'):
    name = 'Prüfen' if mode == 'validate' else 'Test-Starter aktualisieren'
    button = page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name=name, exact=True)
    button.evaluate('(node)=>node.id="isolated-release-action"')
    return button


def finish(page):
    page.evaluate('window.__releaseHeld()')
    page.wait_for_function('()=>window.__oldActionFinished')


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
def test_direct_double_invocation_submits_only_one_release_action(ui, mode):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    original = store.manifest(store.release_folder(target['id']))
    submitted = []
    suffix = '/api/v1/releases/' + target['id'] + ('/validate' if mode == 'validate' else '/test-launcher')
    page.on('request', lambda request: submitted.append(request.url)
            if request.method == 'POST' and request.url.endswith(suffix) else None)
    button = action(page, target, mode)
    hold(page, suffix); track(page, 'isolated-release-action')
    button.click(); page.wait_for_function('()=>window.__held')
    expect(button).to_be_disabled()
    page.evaluate('async()=>{await document.getElementById("isolated-release-action").onclick();}')
    assert len(submitted) == 1
    finish(page)
    expect(action(page, target, mode)).to_be_enabled()
    assert store.manifest(store.release_folder(target['id'])) == original
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
@pytest.mark.parametrize('error', [False, True])
@pytest.mark.parametrize('change', ['tab_aba', 'close', 'newer_details', 'notice', 'logout'])
def test_late_action_does_not_replace_newer_display_intent(ui, mode, error, change):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    original = store.manifest(store.release_folder(target['id']))
    page.evaluate('details({synthetic:"current existing detail"})')
    button = action(page, target, mode)
    hold(page, '/api/v1/releases/' + target['id'] + ('/validate' if mode == 'validate' else '/test-launcher'), error)
    track(page, 'isolated-release-action')
    button.click(); page.wait_for_function('()=>window.__held')
    if change == 'tab_aba':
        page.locator('#tab-prepare').click();page.locator('#tab-manage').click()
    elif change == 'close':
        page.locator('#details-box > summary').click()
    elif change == 'logout':
        page.locator('#disconnect').click()
    elif change == 'newer_details':
        page.evaluate('details({synthetic:"newer independent detail"})')
    page.evaluate('message("Newer notice")')
    before = page.locator('#details').text_content()
    finish(page)
    if change != 'notice':
        expect(page.locator('#details')).to_have_text(before)
    if change in ('tab_aba', 'close', 'logout'):
        expect(page.locator('#details-box')).not_to_have_attribute('open', '')
    expect(page.locator('#message')).to_have_text('Newer notice')
    assert store.manifest(store.release_folder(target['id'])) == original
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
def test_refresh_keeps_busy_gate_and_detached_button_cannot_submit_again(ui, mode):
    from playwright.sync_api import expect
    page, _, target, _ = ui
    suffix = '/api/v1/releases/' + target['id'] + ('/validate' if mode == 'validate' else '/test-launcher')
    submitted = []
    page.on('request', lambda request: submitted.append(request.url)
            if request.method == 'POST' and request.url.endswith(suffix) else None)
    button = action(page, target, mode)
    page.evaluate('window.__oldReleaseControl=document.getElementById("isolated-release-action")')
    hold(page, suffix);track(page, 'isolated-release-action')
    button.click();page.wait_for_function('()=>window.__held')
    page.locator('#refresh').click()
    expect(action(page, target, mode)).to_be_disabled()
    page.evaluate('async()=>{await window.__oldReleaseControl.onclick();}')
    assert len(submitted) == 1
    finish(page)
    expect(action(page, target, mode)).to_be_enabled()
    assert len(submitted) == 1


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
def test_current_server_error_is_visible_and_releases_controls(ui, mode):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    before = snapshot(store)['sha256']
    suffix = '/api/v1/releases/' + target['id'] + ('/validate' if mode == 'validate' else '/test-launcher')
    page.route('**' + suffix, lambda route: route.fulfill(status=503, json={'detail': 'Synthetic action unavailable'}))
    action(page, target, mode).click()
    expect(page.locator('#message')).to_contain_text('Synthetic action unavailable')
    expect(action(page, target, mode)).to_be_enabled()
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
def test_success_with_failed_refresh_is_not_reported_as_a_failed_server_action(ui, mode):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    suffix = '/api/v1/releases/' + target['id'] + ('/validate' if mode == 'validate' else '/test-launcher')
    submitted = []
    page.on('request', lambda request: submitted.append(request.url)
            if request.method == 'POST' and request.url.endswith(suffix) else None)
    page.route('**/api/v1/workspace', lambda route: route.fulfill(status=503, json={'detail': 'Synthetic workspace unavailable'}))
    action(page, target, mode).click()
    expect(page.locator('#message')).to_contain_text('Serveraktion beantwortet; Bestand konnte nicht vollständig aktualisiert werden')
    expect(page.locator('#message')).to_contain_text('keinen zweiten Schreibversuch automatisch starten')
    expect(action(page, target, mode)).to_be_enabled()
    assert len(submitted) == 1 and store.active()['release_id'] is None


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
def test_detached_idle_button_cannot_start_a_server_action(ui, mode):
    page, store, target, _ = ui
    before = snapshot(store)['sha256']
    action(page, target, mode)
    page.evaluate('window.__detachedIdleControl=document.getElementById("isolated-release-action");renderReleases()')
    page.evaluate('async()=>{await window.__detachedIdleControl.onclick();}')
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
def test_old_session_finally_cannot_unlock_a_new_action_for_the_same_release(ui, mode):
    from playwright.sync_api import expect
    page, _, target, _ = ui
    suffix = '/api/v1/releases/' + target['id'] + ('/validate' if mode == 'validate' else '/test-launcher')
    button = action(page, target, mode)
    named_hold(page, suffix);named_track(page, 'isolated-release-action')
    button.click();wait_held(page)
    page.locator('#disconnect').click()
    page.locator('#token').fill('isolated-requests-browser-' + 'q' * 40)
    page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    button = action(page, target, mode)
    named_hold(page, suffix, name='new');named_track(page, 'isolated-release-action', name='new')
    button.click();wait_held(page, 'new')
    unhold(page)
    expect(button).to_be_disabled()
    unhold(page, 'new')
    expect(action(page, target, mode)).to_be_enabled()


@pytest.mark.parametrize('mode', ['validate', 'launcher'])
def test_independent_release_can_complete_without_stealing_the_first_owner(ui, mode):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    other = store.import_release('test', '2512', 'Other synthetic release', 'fixture')
    page.locator('#refresh').click()
    expect(page.locator('#releases tr')).to_have_count(2)
    suffix = '/api/v1/releases/' + target['id'] + ('/validate' if mode == 'validate' else '/test-launcher')
    button = action(page, target, mode)
    hold(page, suffix);track(page, 'isolated-release-action')
    button.click();page.wait_for_function('()=>window.__held')
    other_button = action(page, other, mode)
    expect(other_button).to_be_enabled()
    awaitable = other_button.evaluate('(node)=>node.onclick()')
    assert awaitable is None
    content = page.locator('#details').inner_text()
    expect(action(page, target, mode)).to_be_disabled()
    finish(page)
    expect(page.locator('#details')).to_have_text(content)
    expect(action(page, target, mode)).to_be_enabled()
    assert store.active()['release_id'] is None


def test_prepare_button_without_a_workspace_launcher_uses_the_same_double_click_gate(ui):
    from playwright.sync_api import expect
    page, _, target, _ = ui
    # Only the synthetic workspace hint is cleared, not server metadata/files.
    # Existing server-side initial package creation has separate core coverage.
    page.evaluate('id=>{releases.find(row=>row.id===id).test_launcher=null;renderReleases()}', target['id'])
    button = page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Test-Starter vorbereiten', exact=True)
    button.evaluate('(node)=>node.id="isolated-release-action"')
    suffix = '/api/v1/releases/' + target['id'] + '/test-launcher'
    submitted = []
    page.on('request', lambda request: submitted.append(request.url)
            if request.method == 'POST' and request.url.endswith(suffix) else None)
    hold(page, suffix);track(page, 'isolated-release-action')
    button.click();page.wait_for_function('()=>window.__held')
    page.evaluate('async()=>{await document.getElementById("isolated-release-action").onclick();}')
    assert len(submitted) == 1
    finish(page)
    expect(page.locator('#message')).to_contain_text('Testpaket für diesen vorhandenen Datenstand erstellt')
