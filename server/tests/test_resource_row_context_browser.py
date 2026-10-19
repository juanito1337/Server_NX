"""Stale resource-row actions must not discard a newer synthetic draft."""
import os

import pytest

from test_requests_browser import ui
from nxserver.integrity import snapshot

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated resource-row Edge tests.')


def test_detached_edit_button_does_not_discard_the_new_editor(ui):
    from playwright.sync_api import expect
    page, store, _, _ = ui
    before = snapshot(store)['sha256']
    page.locator('#resources tr').get_by_role('button', name='Bearbeiten', exact=True).evaluate('(node)=>window.__oldResourceEdit=node')
    page.locator('#resource-query').fill('no synthetic match')
    expect(page.locator('#resources tr')).to_have_count(0)
    page.locator('#tab-prepare').click();page.locator('#name').fill('Newer unsaved draft')
    page.locator('#editor-section details.technical > summary').click()
    page.locator('#cam').fill('{')
    page.evaluate('async()=>{await window.__oldResourceEdit.onclick();}')
    expect(page.locator('#name')).to_have_value('Newer unsaved draft')
    expect(page.locator('#cam')).to_have_value('{')
    assert snapshot(store)['sha256'] == before


def test_hidden_resource_action_does_not_replace_the_editor_in_another_workspace(ui):
    from playwright.sync_api import expect
    page, store, _, _ = ui
    before = snapshot(store)['sha256']
    page.locator('#resources tr').get_by_role('button', name='Bearbeiten', exact=True).evaluate('(node)=>window.__oldResourceEdit=node')
    page.locator('#tab-prepare').click();page.locator('#name').fill('Newer hidden-row draft')
    page.evaluate('async()=>{await window.__oldResourceEdit.onclick();}')
    expect(page.locator('#name')).to_have_value('Newer hidden-row draft')
    assert snapshot(store)['sha256'] == before


@pytest.mark.parametrize('label', ['Bearbeiten', 'Historie', 'ERP-Bezug prüfen'])
@pytest.mark.parametrize('change', ['filter_aba', 'refresh', 'new_revision', 'hidden', 'logout', 'replaced_snapshot'])
def test_old_row_actions_keep_current_editor_view_and_database(ui, label, change):
    from playwright.sync_api import expect
    page, store, _, resource = ui
    page.locator('#resources tr').get_by_role('button', name=label, exact=True).evaluate('(node)=>window.__oldResourceAction=node')
    page.locator('#tab-prepare').click();page.locator('#name').fill('Unsaved row-context draft')
    page.locator('#editor-section details.technical > summary').click();page.locator('#cam').fill('{')
    if change != 'hidden':
        page.locator('#tab-manage').click()
    if change == 'filter_aba':
        page.locator('#resource-query').fill('no synthetic match')
        expect(page.locator('#resources tr')).to_have_count(0)
        page.locator('#resource-query').fill('')
        expect(page.locator('#resources tr')).to_have_count(1)
    elif change in ('refresh', 'new_revision'):
        if change == 'new_revision':
            from test_requests_browser import payload
            value = payload(resource);value['name'] = 'Updated synthetic resource'
            store.save_resource(value, 'fixture', resource['id'], resource['revision'])
        page.locator('#refresh').click()
        expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
        if change == 'new_revision':
            expect(page.locator('#resources')).to_contain_text('Updated synthetic resource')
    elif change == 'logout':
        page.locator('#disconnect').click()
    elif change == 'replaced_snapshot':
        # Simulated metadata-only replacement with the old DOM still attached.
        page.evaluate('resources=resources.map(value=>({...value}))')
    before = snapshot(store)['sha256']
    name, cam = page.locator('#name').input_value(), page.locator('#cam').input_value()
    workspace = page.evaluate('workspace')
    history_visible = page.locator('#history-box').is_visible()
    requests = []
    page.on('request', lambda request: requests.append(request.method) if '/api/v1/' in request.url else None)
    page.evaluate('async()=>{await window.__oldResourceAction.onclick();}')
    expect(page.locator('#name')).to_have_value(name)
    expect(page.locator('#cam')).to_have_value(cam)
    assert page.evaluate('workspace') == workspace
    assert page.locator('#history-box').is_visible() == history_visible
    assert not requests and snapshot(store)['sha256'] == before


@pytest.mark.parametrize('label', ['Bearbeiten', 'Historie', 'ERP-Bezug prüfen'])
def test_current_visible_row_actions_still_work_without_an_implicit_save(ui, label):
    from playwright.sync_api import expect
    page, store, _, resource = ui
    before = snapshot(store)['sha256']
    page.locator('#resources tr').get_by_role('button', name=label, exact=True).click()
    if label == 'Bearbeiten':
        expect(page.locator('#panel-prepare')).to_be_visible()
        expect(page.locator('#name')).to_have_value(resource['name'])
    elif label == 'Historie':
        expect(page.locator('#history-state')).to_contain_text('Aktueller Historienkopf')
        expect(page.locator('#history-box')).to_be_visible()
    else:
        expect(page.locator('#erp2-navigation-state')).to_contain_text('1:n-Modell ist nicht aktiv')
    assert snapshot(store)['sha256'] == before
