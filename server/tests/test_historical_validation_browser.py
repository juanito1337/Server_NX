"""Old stored diagnostics are readable, never current qualification evidence."""
import json
import os

import pytest

from nxserver.integrity import snapshot
from nxserver.store import canonical
from test_requests_browser import ui

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated historical validation view.')


def old_report(store, target, count=62):
    value = {'integrity_passed': False, 'errors': [json.dumps({'library': 'tool-metric', 'field': 'MATREF',
             'key': 'SYNTHETIC_' + str(index), 'target': 'tool_material', 'value': 'OLD_MISSING_' + str(index),
             'reason': 'Historical missing library reference.'}) for index in range(count)],
             'file_count': 7, 'tool_count': 3, 'functional_validation_required': True,
             'checked_at': '2026-10-06T11:23:22.642276+00:00'}
    with store.db() as db:
        db.execute("UPDATE releases SET validation=?,status='blocked',review=NULL WHERE id=?", (canonical(value), target['id']))
    return value


def test_old_report_is_readable_paged_and_explicitly_not_a_current_check_without_any_request(ui):
    from playwright.sync_api import expect
    page, store, target, *_ = ui; report = old_report(store, target)
    page.locator('#refresh').click(); expect(page.locator('#releases')).to_contain_text('blocked')
    page.locator('#tab-prepare').click(); page.locator('#name').fill('Unsaved before historical read')
    page.locator('#tab-manage').click()
    before = snapshot(store)['sha256']; requests = []
    page.on('request', lambda request: requests.append(request.url) if '/api/v1/' in request.url else None)
    row = page.locator('#releases tr').filter(has_text=target['id'])
    row.get_by_role('button', name='Historische Befunde', exact=True).click()
    expect(page.locator('#historical-validation-state')).to_contain_text('62 damals gemeldete Befunde')
    expect(page.locator('#historical-validation-state')).to_contain_text('keine aktuelle Datei- oder NX-Prüfung')
    expect(page.locator('#historical-validation-items tr')).to_have_count(50)
    page.locator('#historical-validation-next').click(); expect(page.locator('#historical-validation-items tr')).to_have_count(12)
    page.locator('#historical-validation-query').fill('OLD_MISSING_61')
    expect(page.locator('#historical-validation-items tr')).to_have_count(1)
    assert json.loads(page.locator('#details').inner_text()) == report
    page.locator('#tab-prepare').click(); expect(page.locator('#name')).to_have_value('Unsaved before historical read')
    assert not requests and snapshot(store)['sha256'] == before


@pytest.mark.parametrize('change', ['refresh', 'tab', 'logout', 'snapshot'])
def test_old_row_button_cannot_reopen_a_replaced_or_hidden_release(ui, change):
    from playwright.sync_api import expect
    page, store, target, *_ = ui; old_report(store, target, 1)
    page.locator('#refresh').click(); expect(page.locator('#releases')).to_contain_text('blocked')
    page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Historische Befunde').evaluate('(node)=>window.__oldHistorical=node')
    if change == 'refresh': page.locator('#refresh').click(); expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    elif change == 'tab': page.locator('#tab-prepare').click()
    elif change == 'logout': page.locator('#disconnect').click()
    else: page.evaluate('releases=releases.map(value=>({...value}))')
    database = snapshot(store)['sha256']; requests = []
    page.on('request', lambda request: requests.append(request.url) if '/api/v1/' in request.url else None)
    page.evaluate('async()=>await __oldHistorical.onclick()')
    expect(page.locator('#historical-validation-box')).to_be_hidden()
    assert not requests and snapshot(store)['sha256'] == database


@pytest.mark.parametrize('change', ['new_report', 'current_check', 'details', 'tab', 'logout'])
def test_historical_view_is_invalidated_by_newer_results_or_navigation(ui, change):
    from playwright.sync_api import expect
    page, store, target, *_ = ui; old_report(store, target, 1)
    page.locator('#refresh').click(); expect(page.locator('#releases')).to_contain_text('blocked')
    row = page.locator('#releases tr').filter(has_text=target['id'])
    row.get_by_role('button', name='Historische Befunde').click()
    expect(page.locator('#historical-validation-items tr')).to_have_count(1)
    if change == 'new_report':
        old_report(store, target, 2); page.locator('#refresh').click()
        expect(page.locator('#historical-validation-state')).to_contain_text('Altbericht geändert')
        expect(page.locator('#historical-validation-items tr')).to_have_count(0)
    elif change == 'current_check':
        row.get_by_role('button', name='Prüfen', exact=True).click()
        expect(page.locator('#release-validation-state')).to_contain_text('Technisch ohne blockierende Befunde')
        expect(page.locator('#historical-validation-box')).to_be_hidden()
    else:
        if change == 'details': page.locator('#openapi').click(); expect(page.locator('#details')).to_contain_text('openapi')
        elif change == 'tab': page.locator('#tab-prepare').click()
        else: page.locator('#disconnect').click()
        expect(page.locator('#historical-validation-box')).to_be_hidden()


@pytest.mark.parametrize('fault', ['tool_count', 'errors', 'timestamp'])
def test_malformed_historical_report_keeps_raw_data_and_no_partial_rows(ui, fault):
    from playwright.sync_api import expect
    page, store, target, *_ = ui; report = old_report(store, target, 1)
    if fault == 'tool_count': report['tool_count'] = '3'
    elif fault == 'errors': report['errors'] = [None]
    else: report['checked_at'] = '2026-02-30T12:00:00Z'
    with store.db() as db: db.execute('UPDATE releases SET validation=? WHERE id=?', (canonical(report), target['id']))
    page.locator('#refresh').click(); expect(page.locator('#releases')).to_contain_text('blocked')
    database = snapshot(store)['sha256']
    page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Historische Befunde').click()
    expect(page.locator('#historical-validation-state')).to_contain_text('Historische Übersicht nicht bestätigt')
    expect(page.locator('#historical-validation-items tr')).to_have_count(0)
    assert json.loads(page.locator('#details').inner_text()) == report and snapshot(store)['sha256'] == database


def test_plain_historical_palette_error_and_markup_remain_only_text(ui):
    from playwright.sync_api import expect
    page, store, target, *_ = ui; report = old_report(store, target, 1)
    report['errors'] = ['Malformed PAX XML: <img src=x onerror="window.__historicalInjected=true">']
    with store.db() as db: db.execute('UPDATE releases SET validation=? WHERE id=?', (canonical(report), target['id']))
    page.locator('#refresh').click(); expect(page.locator('#releases')).to_contain_text('blocked')
    database = snapshot(store)['sha256']
    page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Historische Befunde').click()
    expect(page.locator('#historical-validation-items')).to_contain_text('Malformed PAX XML')
    assert page.locator('#historical-validation-items img').count() == 0
    assert page.evaluate('window.__historicalInjected===true') is False
    assert snapshot(store)['sha256'] == database
