"""Connection failures and startup diagnostics on disposable local data only."""
import os
from html.parser import HTMLParser
from pathlib import Path

import pytest

from test_requests_browser import ui
from test_requests_browser import hold,track,release as release_reply

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge connection tests.')
TOKEN='isolated-requests-browser-'+'q'*40


class ScriptSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources=[]

    def handle_starttag(self,tag,attributes):
        source=dict(attributes).get('src')
        if tag=='script' and source:
            self.sources.append(source.lstrip('/'))


def startup_scripts():
    parser=ScriptSources()
    parser.feed((Path(__file__).parents[1]/'nxserver/web/index.html').read_text(encoding='utf-8'))
    assert parser.sources and len(parser.sources)==len(set(parser.sources))
    assert parser.sources[0]=='bootstrap.js'
    return [source for source in parser.sources if source!='bootstrap.js']


def test_failed_session_reset_is_visible_and_clears_runtime_credential(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries();requests=[]
    page.on('request',lambda request:requests.append(request.url) if '/api/v1/' in request.url else None)
    page.evaluate('()=>{NXFolders.reset=()=>{throw new Error("Synthetic reset failure with private content");};}')
    page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Oberfläche konnte nicht sicher initialisiert werden')
    expect(page.locator('#message')).not_to_contain_text('private content')
    assert page.evaluate('token')=='' and not requests and store.audit_entries()==before


@pytest.fixture
def raw_page(ui):
    original,store,*_=ui;context=original.context.browser.new_context();page=context.new_page()
    yield page,store,original.url.split('/#')[0]
    context.close()


@pytest.mark.parametrize('script',startup_scripts())
def test_missing_script_blocks_login_with_safe_diagnostics(raw_page,script):
    from playwright.sync_api import expect
    page,store,base=raw_page;before=store.audit_entries();requests=[]
    page.route('**/'+script,lambda route:route.fulfill(status=404,body='Synthetic missing module'))
    page.on('request',lambda request:requests.append(request.url) if '/api/v1/' in request.url else None)
    page.goto(base)
    expect(page.locator('#connect')).to_be_disabled()
    expect(page.locator('#connection-state')).to_contain_text('Oberfläche konnte nicht sicher initialisiert werden')
    assert not requests and store.audit_entries()==before
    assert page.evaluate('NXClientBoot.status().failed') is True


def test_missing_bootstrap_retains_static_login_lock(raw_page):
    from playwright.sync_api import expect
    page,store,base=raw_page;requests=[]
    page.route('**/bootstrap.js',lambda route:route.fulfill(status=404,body='Synthetic missing bootstrap'))
    page.on('request',lambda request:requests.append(request.url) if '/api/v1/' in request.url else None)
    page.goto(base)
    expect(page.locator('#connect')).to_be_disabled();expect(page.locator('#connection-state')).to_contain_text('Startprüfung')
    assert not requests


def test_missing_schema_worklist_workspace_hook_blocks_a_mixed_client_bundle(raw_page):
    from playwright.sync_api import expect
    page,store,base=raw_page;before=store.audit_entries();requests=[]
    def old_module(route):
        response=route.fetch();route.fulfill(response=response,body=response.text()+'\ndelete NXSchemaImpact.workspaceChanged;\n')
    page.route('**/schemas.js',old_module)
    page.on('request',lambda request:requests.append(request.url) if '/api/v1/' in request.url else None)
    page.goto(base)
    expect(page.locator('#connect')).to_be_disabled()
    expect(page.locator('#connection-state')).to_contain_text('Oberfläche konnte nicht sicher initialisiert werden')
    assert not requests and store.audit_entries()==before and page.evaluate('NXClientBoot.status().failed') is True


@pytest.mark.parametrize('method',['bindWire','parseWire','numberLiteral','stringifyWire','cloneWire'])
def test_missing_json_number_helper_blocks_a_mixed_client_bundle(raw_page,method):
    from playwright.sync_api import expect
    page,store,base=raw_page;before=store.audit_entries();requests=[]
    def old_module(route):
        response=route.fetch();route.fulfill(response=response,body=response.text()+'\ndelete NXData.'+method+';\n')
    page.route('**/app.js',old_module)
    page.on('request',lambda request:requests.append(request.url) if '/api/v1/' in request.url else None)
    page.goto(base)
    expect(page.locator('#connect')).to_be_disabled()
    expect(page.locator('#connection-state')).to_contain_text('Oberfläche konnte nicht sicher initialisiert werden')
    assert not requests and store.audit_entries()==before and page.evaluate('NXClientBoot.status().failed') is True


def test_login_has_one_owner_and_logout_discards_held_capabilities(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries()
    hold(page,'/api/v1/capabilities');track(page,'connect')
    page.locator('#connect').click();page.wait_for_function('()=>window.__held')
    expect(page.locator('#connect')).to_be_disabled();expect(page.locator('#connection-state')).to_contain_text('Verbindung wird geprüft')
    page.locator('#disconnect').click();release_reply(page)
    expect(page.locator('#work')).to_be_hidden();expect(page.locator('#message')).to_have_text('Abgemeldet.')
    expect(page.locator('#connect')).to_be_enabled();assert page.evaluate('token')=='' and store.audit_entries()==before


def test_login_does_not_reveal_workspace_before_initial_snapshot_finishes(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries()
    hold(page,'/api/v1/workspace');track(page,'connect');page.locator('#connect').click();page.wait_for_function('()=>window.__held')
    expect(page.locator('#work')).to_be_hidden()
    release_reply(page);expect(page.locator('#message')).to_contain_text('Verbunden');expect(page.locator('#work')).to_be_visible()
    assert store.audit_entries()==before


def test_same_session_superseded_login_read_exits_connecting_instead_of_hanging(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries()
    hold(page,'/api/v1/workspace');track(page,'connect');page.locator('#connect').click();page.wait_for_function('()=>window.__held')
    page.evaluate('async()=>{await refresh();}')
    release_reply(page)
    expect(page.locator('#connect')).to_be_enabled();expect(page.locator('#work')).to_be_hidden()
    expect(page.locator('#message')).to_contain_text('Verbindung konnte nicht vollständig geprüft werden')
    assert page.evaluate('token')=='' and page.evaluate('NXClientBoot.status().phase')=='idle'
    assert store.audit_entries()==before


def test_rejected_credentials_leave_no_session_and_allow_explicit_retry(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries()
    page.locator('#token').fill('invalid-synthetic-credential');page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbindung abgelehnt');expect(page.locator('#work')).to_be_hidden()
    assert page.evaluate('token')=='' and page.evaluate('NXClientBoot.status().phase')=='idle'
    page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    assert store.audit_entries()==before


def test_bad_rpc_reply_is_not_echoed_and_does_not_open_partial_workspace(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries()
    page.route('**/api/v1/sources',lambda route:route.fulfill(status=503,json={'detail':'Synthetic private payload; must not be echoed'}))
    page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbindung konnte nicht vollständig geprüft werden')
    expect(page.locator('#message')).not_to_contain_text('private payload');expect(page.locator('#work')).to_be_hidden()
    assert page.evaluate('token')=='' and store.audit_entries()==before


def test_runtime_fault_invalidates_credential_without_caching_exception_or_erasing_tickets(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries()
    page.evaluate('''()=>{sessionStorage.setItem('synthetic.pending.ticket','nonsecret fixture');
      window.dispatchEvent(new ErrorEvent('error',{message:'Synthetic private exception details'}));}''')
    expect(page.locator('#connect')).to_be_disabled();expect(page.locator('#work')).to_be_hidden()
    expect(page.locator('#connection-state')).not_to_contain_text('private exception')
    assert page.evaluate('token')=='' and page.evaluate('sessionStorage.getItem("synthetic.pending.ticket")')=='nonsecret fixture'
    assert 'private' not in str(page.evaluate('NXClientBoot.status()')) and store.audit_entries()==before


def test_without_javascript_page_stays_locked_and_explains_requirement(ui):
    from playwright.sync_api import expect
    original,store,*_=ui;context=original.context.browser.new_context(java_script_enabled=False)
    try:
        page=context.new_page();page.goto(original.url.split('/#')[0])
        expect(page.locator('#connect')).to_be_disabled();expect(page.locator('#connection-state')).to_contain_text('JavaScript aktivieren')
        expect(page.locator('#work')).to_be_hidden()
    finally:context.close()
