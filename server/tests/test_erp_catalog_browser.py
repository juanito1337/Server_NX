"""Read-only ERP overview and current identity lookup in disposable Edge fixtures."""
import json
import os

import pytest

from test_requests_browser import ui,hold,track,release as release_reply
from test_erp_catalog import ERP,body
from test_history_browser import hold as hold_fragment,track as track_named,wait_held,unhold

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge ERP overview.')


def show(page):
    page.locator('#tab-manage').click();page.locator('#erp-box > summary').click()


def read(page):
    from playwright.sync_api import expect
    page.locator('#erp-read').click();expect(page.locator('#erp-overview-state')).to_contain_text('Ein aktueller SQLite-Lesestand')


def lookup(page,company='company',system='system'):
    from playwright.sync_api import expect
    page.locator('#erp-company').fill(company);page.locator('#erp-system').fill(system);page.locator('#erp-lookup').click()
    expect(page.locator('#erp-lookup-state')).to_contain_text('Heute gespeicherter Datensatz')


def test_reading_erp_preserves_unsaved_editor_and_current_lookup_differs_from_historical_reply(ui):
    from playwright.sync_api import expect
    import uuid
    from nxserver.write_requests import get
    page,store,*_=ui;key=str(uuid.uuid4());first=store.erp_upsert(ERP,'erp',request_key=key)
    second=store.save_resource({**body(first),'name':'Current CAM fixture','cam':{'SERIAL':'12'}},'fixture',first['id'],1)
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Current CAM fixture')
    page.locator('#resources tr').filter(has_text='Initial fixture').get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#name').fill('Unsaved editor value');audit=store.audit_entries();history=store.history(first['id'])
    show(page);read(page);expect(page.locator('#erp-items tr')).to_have_count(2);lookup(page)
    expect(page.locator('#erp-lookup-result')).to_contain_text('Current CAM fixture');expect(page.locator('#erp-lookup-state')).to_contain_text('Rev 2')
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved editor value')
    assert store.history(first['id'])==history and store.audit_entries()==audit and get(store,'erp',key)['result']==first and store.active()['release_id'] is None


def test_pagination_full_search_and_company_filter_are_snapshot_bound(ui):
    from playwright.sync_api import expect
    page,store,*_=ui
    for index in range(56):store.erp_upsert({**ERP,'company_id':'__proto__' if index==55 else 'company','system_id':str(index),'number':f'ITEM{index:03}'},'erp')
    show(page);read(page);expect(page.locator('#erp-items tr')).to_have_count(50);page.locator('#erp-next').click()
    expect(page.locator('#erp-page')).to_contain_text('51–57');expect(page.locator('#erp-items tr')).to_have_count(7)
    page.locator('#erp-query').fill('ITEM055');read(page);expect(page.locator('#erp-items tr')).to_have_count(1)
    page.locator('#erp-query').fill('');page.locator('#erp-company-filter').select_option('__proto__');read(page);expect(page.locator('#erp-items tr')).to_have_count(1)
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')


@pytest.mark.parametrize('old_error',[False,True])
def test_filter_aba_discards_old_response_and_error(ui,old_error):
    from playwright.sync_api import expect
    page,store,*_=ui;store.erp_upsert(ERP,'erp');show(page)
    hold_fragment(page,'/erp/catalog-preview?',error=old_error);track_named(page,'erp-read');page.locator('#erp-read').click();wait_held(page)
    page.locator('#erp-query').fill('temporary');page.locator('#erp-query').fill('');read(page)
    state=page.locator('#erp-overview-state').inner_text();unhold(page);expect(page.locator('#erp-overview-state')).to_have_text(state)
    expect(page.locator('#erp-items tr')).to_have_count(2);expect(page.locator('#erp-read')).to_be_enabled()


@pytest.mark.parametrize('old_error',[False,True])
def test_identity_aba_does_not_restore_old_lookup(ui,old_error):
    from playwright.sync_api import expect
    page,store,*_=ui;store.erp_upsert(ERP,'erp');show(page);page.locator('#erp-company').fill('company');page.locator('#erp-system').fill('system')
    hold_fragment(page,'/erp/resources/lookup?',error=old_error);track_named(page,'erp-lookup');page.locator('#erp-lookup').click();wait_held(page)
    page.locator('#erp-system').fill('other');page.locator('#erp-system').fill('system');page.locator('#erp-lookup').click()
    expect(page.locator('#erp-lookup-state')).to_contain_text('Heute gespeicherter Datensatz');state=page.locator('#erp-lookup-state').inner_text()
    unhold(page);expect(page.locator('#erp-lookup-state')).to_have_text(state);expect(page.locator('#erp-lookup-result tr')).to_have_count(1)


def test_old_finally_cannot_unlock_new_pending_lookup(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;store.erp_upsert(ERP,'erp');show(page);page.locator('#erp-company').fill('company');page.locator('#erp-system').fill('system')
    hold_fragment(page,'/erp/resources/lookup?');track_named(page,'erp-lookup');page.locator('#erp-lookup').click();wait_held(page)
    page.locator('#erp-system').fill('other');hold_fragment(page,'/erp/resources/lookup?',name='new');track_named(page,'erp-lookup',name='new')
    page.locator('#erp-lookup').click();wait_held(page,'new');unhold(page);expect(page.locator('#erp-lookup')).to_be_disabled()
    unhold(page,'new');expect(page.locator('#erp-lookup')).to_be_enabled();expect(page.locator('#erp-lookup-result tr')).to_have_count(0)


@pytest.mark.parametrize('operation',['overview','lookup'])
def test_workspace_change_invalidates_first_pending_read_and_logout_removes_late_results(ui,operation):
    from playwright.sync_api import expect
    page,store,*_=ui;value=store.erp_upsert(ERP,'erp');show(page)
    identifier='erp-read' if operation=='overview' else 'erp-lookup';fragment='/erp/catalog-preview?' if operation=='overview' else '/erp/resources/lookup?'
    if operation=='lookup':page.locator('#erp-company').fill('company');page.locator('#erp-system').fill('system')
    hold_fragment(page,fragment);track_named(page,identifier);page.locator('#'+identifier).click();wait_held(page)
    store.save_resource({**body(value),'name':'New current fixture'},'fixture',value['id'],1)
    page.locator('#refresh').click();expect(page.locator('#erp-overview-state')).to_contain_text('Bestand seit Übersicht geändert')
    unhold(page);expect(page.locator('#erp-items tr')).to_have_count(0);expect(page.locator('#erp-lookup-result tr')).to_have_count(0)
    hold_fragment(page,fragment,name='new');track_named(page,identifier,name='new');page.locator('#'+identifier).click();wait_held(page,'new')
    page.locator('#disconnect').click();unhold(page,'new');expect(page.locator('#work')).to_be_hidden()
    assert page.locator('#erp-items tr').count()==page.locator('#erp-lookup-result tr').count()==0


@pytest.mark.parametrize('change',['count','flags','last_item','identity','source_hash'])
def test_bad_batch_never_renders_partial_overview_and_explicit_retry_works(ui,change):
    from playwright.sync_api import expect
    page,store,*_=ui;store.erp_upsert(ERP,'erp');show(page)
    def broken(route):
        response=route.fetch();value=response.json()
        if change=='count':value['counts']['linked']+=1
        elif change=='flags':value['resources_written']=True
        elif change=='last_item':value['items'][-1]['warnings']=None
        elif change=='identity':value['identity_fields']=['number']
        else:value['catalog_sha256']='wrong'
        route.fulfill(response=response,json=value)
    pattern='**/erp/catalog-preview?*';page.route(pattern,broken);page.locator('#erp-read').click()
    expect(page.locator('#erp-overview-state')).to_contain_text('Keine vollständige gebundene ERP-Übersicht');expect(page.locator('#erp-items tr')).to_have_count(0)
    page.unroute(pattern,broken);read(page);expect(page.locator('#erp-items tr')).to_have_count(2)


def test_nonunique_number_is_warning_not_association_and_conflict_is_visible(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;store.erp_upsert(ERP,'erp');second=store.erp_upsert({**ERP,'system_id':'other'},'erp')
    show(page);read(page);expect(page.locator('#erp-items')).to_contain_text('Artikelnummer bezeichnet mehrere ERP-Kennungen')
    with store.db() as db:db.execute('UPDATE resources SET external_key=NULL WHERE id=?',(second['id'],))
    read(page);expect(page.locator('#erp-items')).to_contain_text('ERP-Felder und Index widersprechen sich');lookup(page)
    page.locator('#erp-system').fill('other');page.locator('#erp-lookup').click();expect(page.locator('#erp-lookup-state')).to_contain_text('nicht eindeutig aufgelöst')
    assert len(store.resources())==3 and store.active()['release_id'] is None


def test_opening_saved_record_is_explicit_and_a_conflicting_or_stale_header_is_not_adopted(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;value=store.erp_upsert(ERP,'erp')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Synthetic ERP fixture');page.locator('#new').click();page.locator('#name').fill('Unsaved new editor')
    show(page);read(page);row=page.locator('#erp-items tr').filter(has_text='Synthetic ERP fixture')
    page.once('dialog',lambda dialog:dialog.dismiss());row.get_by_role('button',name='Gespeicherte Stammdaten öffnen').click()
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved new editor')
    page.locator('#tab-manage').click();page.once('dialog',lambda dialog:dialog.accept());row.get_by_role('button',name='Gespeicherte Stammdaten öffnen').click()
    expect(page.locator('#name')).to_have_value('Synthetic ERP fixture')
    page.locator('#tab-manage').click()
    from test_erp_catalog import alter
    alter(store,value,lambda data:data.update(name='New header without a new revision'))
    read(page);row=page.locator('#erp-items tr').filter(has_text='New header without a new revision');row.get_by_role('button',name='Gespeicherte Stammdaten öffnen').click()
    expect(page.locator('#message')).to_contain_text('Bestand zuerst aktualisieren')
    assert page.locator('#name').input_value()=='Synthetic ERP fixture'
