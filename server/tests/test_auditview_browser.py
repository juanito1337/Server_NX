"""Real Edge audit navigation against disposable local data, never the pilot."""
import json
import os

import pytest

from nxserver.integrity import snapshot
from test_requests_browser import ui, hold, track, release
from test_auditview import seed

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated audit navigation.')
SUMMARY = '/api/v1/audit-summary?offset=0&limit=20&query='


def test_pages_filter_and_exact_entry_payload_preserve_editor_and_database(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui
    seed(store, 227)
    before = snapshot(store)['sha256']; original = store.audit_entries()[0]
    mutations = []
    page.on('request', lambda request: mutations.append(request.url) if request.method != 'GET' else None)
    page.locator('#tab-prepare').click(); page.locator('#name').fill('Unsaved CAM draft')
    page.locator('#auditview-open').click()
    expect(page.locator('#auditview-items tr')).to_have_count(20)
    page.locator('#auditview-items tr').first.get_by_role('button', name='Eintrag lesen').click()
    expect(page.locator('#auditview-detail')).to_be_visible()
    expect(page.locator('#auditview-payload')).to_have_text(original['payload'])
    expect(page.locator('#auditview-detail-state')).to_contain_text('Einzelhash bestätigt')
    assert page.locator('#auditview-payload script').count() == 0
    for _ in range(10):
        page.locator('#auditview-next').click()
        expect(page.locator('#auditview-read')).to_be_enabled()
    expect(page.locator('#auditview-page')).to_contain_text('201–220')
    page.locator('#auditview-query').fill('CAM Ä')
    expect(page.locator('#auditview-items tr')).to_have_count(0)
    expect(page.locator('#auditview-next')).to_be_disabled()
    page.locator('#auditview-read').click()
    expect(page.locator('#auditview-state')).to_contain_text('113 passende')
    assert set(page.locator('#auditview-items tr td:nth-child(3)').all_text_contents()) == {'CAM Ä'}
    page.locator('#auditview-close').click(); expect(page.locator('#auditview-box')).to_be_hidden()
    page.locator('#tab-prepare').click(); expect(page.locator('#name')).to_have_value('Unsaved CAM draft')
    assert not mutations and snapshot(store)['sha256'] == before


def test_new_event_rejects_old_page_and_requires_explicit_refresh(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui; seed(store, 25)
    page.locator('#auditview-open').click(); expect(page.locator('#auditview-items tr')).to_have_count(20)
    seed(store, 1); before = snapshot(store)['sha256']
    page.locator('#auditview-next').click()
    expect(page.locator('#auditview-state')).to_contain_text('Audit head changed')
    expect(page.locator('#auditview-items tr')).to_have_count(0)
    expect(page.locator('#auditview-next')).to_be_disabled()
    page.locator('#auditview-read').click(); expect(page.locator('#auditview-items tr')).to_have_count(20)
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('change', ['close', 'filter', 'tab', 'logout'])
@pytest.mark.parametrize('old_error', [False, True])
def test_late_overview_cannot_reopen_or_repopulate_a_newer_context(ui, change, old_error):
    from playwright.sync_api import expect
    page, store, *_ = ui; before = snapshot(store)['sha256']
    page.locator('#auditview-open').click(); expect(page.locator('#auditview-read')).to_be_enabled()
    hold(page, SUMMARY, old_error); track(page, 'auditview-read')
    page.locator('#auditview-read').click(); page.wait_for_function('()=>window.__held')
    if change == 'close': page.locator('#auditview-close').click()
    elif change == 'filter': page.locator('#auditview-query').fill('new query')
    elif change == 'tab': page.locator('#tab-update').click(); page.locator('#tab-manage').click()
    else: page.locator('#disconnect').click()
    page.evaluate('message("Newer operator notice")'); release(page)
    expect(page.locator('#auditview-items tr')).to_have_count(0)
    expect(page.locator('#auditview-detail')).to_be_hidden()
    expect(page.locator('#message')).to_have_text('Newer operator notice')
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('bad', ['duplicate', 'offset', 'head', 'flags'])
def test_invalid_overview_is_not_partially_published(ui, bad):
    from playwright.sync_api import expect
    page, store, *_ = ui; before = snapshot(store)['sha256']
    def corrupt(route):
        response = route.fetch(); value = response.json()
        if bad == 'duplicate': value['items'][1] = value['items'][0]
        elif bad == 'offset': value['offset'] = 20
        elif bad == 'head': value['head']['sequence'] = 0
        else: value['signed'] = True
        route.fulfill(status=200, content_type='application/json', body=json.dumps(value))
    page.route('**/api/v1/audit-summary?*', corrupt)
    page.locator('#auditview-open').click()
    expect(page.locator('#auditview-state')).to_contain_text('passt nicht')
    expect(page.locator('#auditview-items tr')).to_have_count(0)
    assert snapshot(store)['sha256'] == before


def test_detail_bytes_are_checked_against_selected_hash_before_display(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui; before = snapshot(store)['sha256']
    def corrupt(route):
        response = route.fetch(); value = response.json()
        value['payload'] = value['payload'].replace('fixture', 'fixturE')
        route.fulfill(status=200, content_type='application/json', body=json.dumps(value))
    page.route('**/api/v1/audit-entries/*', corrupt)
    page.locator('#auditview-open').click(); expect(page.locator('#auditview-items tr')).not_to_have_count(0)
    page.locator('#auditview-items tr').first.get_by_role('button', name='Eintrag lesen').click()
    expect(page.locator('#auditview-detail-state')).to_contain_text('passt nicht')
    expect(page.locator('#auditview-payload')).to_be_empty()
    assert snapshot(store)['sha256'] == before

def test_returning_to_first_page_does_not_silently_change_selected_audit_head(ui):
    from playwright.sync_api import expect
    page, store, *_ = ui; seed(store, 25)
    page.locator('#auditview-open').click(); expect(page.locator('#auditview-items tr')).to_have_count(20)
    page.locator('#auditview-next').click(); expect(page.locator('#auditview-page')).to_contain_text('21–')
    seed(store, 1); before = snapshot(store)['sha256']
    page.locator('#auditview-prev').click()
    expect(page.locator('#auditview-state')).to_contain_text('Audit head changed')
    expect(page.locator('#auditview-items tr')).to_have_count(0)
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('change', ['reload', 'close', 'filter', 'tab', 'logout'])
@pytest.mark.parametrize('old_error', [False, True])
def test_late_entry_never_repopulates_a_changed_context(ui, change, old_error):
    from playwright.sync_api import expect
    page, store, *_ = ui
    before = snapshot(store)['sha256']; latest = store.audit_entries()[0]
    page.locator('#auditview-open').click(); expect(page.locator('#auditview-items tr')).not_to_have_count(0)
    target = page.locator('#auditview-items tr').first.get_by_role('button', name='Eintrag lesen')
    target.evaluate('(node)=>node.id="isolated-audit-entry"')
    suffix = '/api/v1/audit-entries/' + str(latest['sequence']) + '?expected_hash=' + latest['hash'] + '&head_sequence=' + str(latest['sequence']) + '&head_hash=' + latest['hash']
    hold(page, suffix, old_error); track(page, 'isolated-audit-entry'); target.click()
    page.wait_for_function('()=>window.__held')
    if change == 'reload':
        page.locator('#auditview-read').click(); expect(page.locator('#auditview-read')).to_be_enabled()
    elif change == 'close': page.locator('#auditview-close').click()
    elif change == 'filter': page.locator('#auditview-query').fill('newer metadata query')
    elif change == 'tab': page.locator('#tab-update').click(); page.locator('#tab-manage').click()
    else: page.locator('#disconnect').click()
    page.evaluate('message("Newer audit detail notice")'); release(page)
    expect(page.locator('#auditview-detail')).to_be_hidden()
    expect(page.locator('#auditview-payload')).to_be_empty()
    expect(page.locator('#message')).to_have_text('Newer audit detail notice')
    assert snapshot(store)['sha256'] == before
