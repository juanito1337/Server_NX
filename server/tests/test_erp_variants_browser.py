"""ERP 1:N GUI, migration and recovery in disposable Edge/server/SQLite fixtures."""
import json
import os
import uuid

import pytest

from nxserver.erp_variants import ERPVariants
from nxserver.integrity import snapshot,scan
from test_requests_browser import ui
from test_erp_catalog import ERP,BODY
from test_saves_browser import lose
from test_history_browser import hold,track,wait_held,unhold

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated ERP 1:N GUI.')


def show(page):
    page.locator('#tab-manage').click();page.locator('#erp2-box').evaluate('(element)=>{element.open=true;}')


def migrate(page):
    from playwright.sync_api import expect
    show(page);expect(page.locator('#erp2-status')).to_contain_text('Altes 1:1-Modell')
    page.locator('#erp2-migration-preview').click();expect(page.locator('#erp2-migration-state')).to_contain_text('Umstellung prüfbar')
    page.locator('#erp2-migration-accept').check();page.locator('#erp2-migrate').click()
    expect(page.locator('#erp2-status')).to_contain_text('1:n-Modell aktiv')


def fields(page,**changes):
    value={name:data for name,data in ERP.items() if name!='kind'};value.update(changes)
    for name,data in value.items():page.locator('#erp2-form-'+name).fill(data)


def articles(page,count=None):
    from playwright.sync_api import expect
    page.locator('#erp2-articles-read').click();expect(page.locator('#erp2-articles-state')).to_contain_text('Gemeinsame Artikel gelesen')
    if count is not None:expect(page.locator('#erp2-articles > tr')).to_have_count(count)


def choose(page,number='ITEM1'):
    from playwright.sync_api import expect
    page.locator('#erp2-articles > tr').filter(has_text=number).get_by_role('button',name='Zielartikel wählen').click()
    expect(page.locator('#erp2-selected')).to_contain_text(number)


def test_explicit_migration_article_create_many_variants_and_independent_history(ui):
    from playwright.sync_api import expect
    page,store,release,resource=ui;page.on('dialog',lambda dialog:dialog.accept())
    second=store.save_resource({**BODY,'name':'Second independent CAM fixture'},'fixture');page.locator('#refresh').click()
    old_resources=store.resources();old_files=store.manifest(store.release_folder(release['id']))
    show(page);before=snapshot(store)['sha256'];page.locator('#erp2-migration-preview').click()
    expect(page.locator('#erp2-migration-state')).to_contain_text('Umstellung prüfbar');assert snapshot(store)['sha256']==before
    expect(page.locator('#erp2-migrate')).to_be_disabled();page.locator('#erp2-migration-accept').check();page.locator('#erp2-migrate').click()
    expect(page.locator('#erp2-status')).to_contain_text('1:n-Modell aktiv');expect(page.locator('#erp-read')).to_be_disabled()
    page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page);page.locator('#erp2-article-save').click()
    expect(page.locator('#erp2-write-state')).to_contain_text('ERP-Schreibbeleg bestätigt');articles(page,1);choose(page)
    for item in (resource,second):
        page.locator('#erp2-resource').select_option(item['id']);page.locator('#erp2-variant-read').click();expect(page.locator('#erp2-variant-state')).to_contain_text('Heutige Zuordnung gelesen')
        page.locator('#erp2-assign').click();expect(page.locator('#erp2-write-state')).to_contain_text('variant.assign')
        articles(page,1);choose(page)
    page.locator('#erp2-variants-read').click();expect(page.locator('#erp2-variants > tr')).to_have_count(2)
    page.locator('#erp2-variants > tr').first.get_by_role('button',name='Zuordnungshistorie',exact=True).click()
    expect(page.locator('#erp2-history-items > details')).to_have_count(1)
    page.locator('#erp2-articles > tr').get_by_role('button',name='ERP-Felder bearbeiten').click()
    page.locator('#erp2-form-description').fill('Shared commercial change only');page.locator('#erp2-article-save').click()
    expect(page.locator('#erp2-form-context')).to_contain_text('Revision 2')
    articles(page,1);page.locator('#erp2-articles > tr').get_by_role('button',name='Artikelhistorie',exact=True).click()
    expect(page.locator('#erp2-history-items > details')).to_have_count(2)
    assert store.resources()==old_resources and store.manifest(store.release_folder(release['id']))==old_files
    assert scan(store)['error_count']==0 and store.active()['release_id'] is None


def test_lost_article_reply_retry_uses_same_key_and_preserves_later_input(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());migrate(page)
    page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page)
    lose(page,'/api/v1/erp/articles/upsert');page.locator('#erp2-article-save').click()
    expect(page.locator('#erp2-write-state')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#erp2-request-key').inner_text();audit=store.audit_entries();page.locator('#erp2-form-description').fill('Typed after lost reply')
    page.locator('#erp2-repeat').click();expect(page.locator('#erp2-recovery')).to_be_hidden()
    expect(page.locator('#erp2-form-description')).to_have_value('Typed after lost reply');expect(page.locator('#erp2-form-context')).to_contain_text('Revision 1')
    assert store.audit_entries()==audit and page.evaluate('window.__sentKey')==key
    page.locator('#erp2-article-save').click();expect(page.locator('#erp2-form-context')).to_contain_text('Revision 2')
    assert ERPVariants(store).read()['items'][0]['erp']['description']=='Typed after lost reply'


def test_never_sent_migration_is_fenced_without_creating_model_tables(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());show(page)
    page.locator('#erp2-migration-preview').click();expect(page.locator('#erp2-migration-state')).to_contain_text('Umstellung prüfbar')
    page.locator('#erp2-migration-accept').check();lose(page,'/api/v1/erp/model/migrate',True);page.locator('#erp2-migrate').click()
    expect(page.locator('#erp2-write-state')).to_contain_text('Synthetic network reply lost');page.locator('#erp2-receipt').click()
    expect(page.locator('#erp2-write-state')).to_contain_text('in-flight write cannot finish');expect(page.locator('#erp2-recovery')).to_be_visible()
    page.locator('#erp2-cancel').click();expect(page.locator('#erp2-recovery')).to_be_hidden();assert ERPVariants(store).status()['enabled'] is False
    expect(page.locator('#erp2-migrate')).to_be_disabled()


def test_reload_keeps_only_ticket_and_recovers_article_without_overwriting_new_form(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());migrate(page)
    page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,description='Commercial fields not cached')
    lose(page,'/api/v1/erp/articles/upsert');page.locator('#erp2-article-save').click();expect(page.locator('#erp2-write-state')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-erp-write.v1")');value=json.loads(raw)
    assert set(value)=={'contract_version','key','action','method','path','created_at','request_sha256','target_id','expected_revision'}
    assert 'Commercial fields' not in raw and 'ITEM1' not in raw and 'isolated-requests-browser' not in raw
    page.reload();page.locator('#token').fill('isolated-requests-browser-'+'q'*40);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    show(page);page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,company_id='new-company',description='New unsaved inputs')
    expect(page.locator('#erp2-repeat')).to_be_disabled();page.locator('#erp2-receipt').click();expect(page.locator('#erp2-recovery')).to_be_hidden()
    expect(page.locator('#erp2-form-description')).to_have_value('New unsaved inputs');assert ERPVariants(store).read()['total']==1


@pytest.fixture
def enabled(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept())
    manager=ERPVariants(store);manager.migrate(manager.migration_preview()['preview_hash'],'admin',str(uuid.uuid4()))
    value=manager.upsert({name:data for name,data in ERP.items() if name!='kind'},0,'erp',str(uuid.uuid4()))
    page.locator('#refresh').click();expect(page.locator('#erp2-status')).to_contain_text('1:n-Modell aktiv');show(page)
    return page,store,value,ui[3]


@pytest.mark.parametrize('fault',['enabled','model_version','policy','automatic','erp_connected'])
def test_invalid_status_cannot_unlock_migration_or_legacy_operations(ui,fault):
    from playwright.sync_api import expect
    page,store,*_=ui;show(page);before=snapshot(store)['sha256']
    def bad(route):
        response=route.fetch();value=response.json()
        if fault=='enabled':value['enabled']=True
        elif fault=='model_version':value['model_version']=2
        elif fault=='policy':value['cardinality_policy']='guessed'
        elif fault=='automatic':value['automatic_migration']=True
        else:value['erp_connected']=True
        route.fulfill(response=response,json=value)
    page.route('**/api/v1/erp/model',bad);page.locator('#erp2-status-read').click()
    expect(page.locator('#erp2-status')).to_contain_text('Kein vollständiger ERP-Modellstatus')
    expect(page.locator('#erp2-migrate')).to_be_disabled();expect(page.locator('#erp-read')).to_be_disabled();assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('fault',['hash','counts','technical_write','can_migrate','resources'])
def test_invalid_migration_preview_does_not_qualify_write(ui,fault):
    from playwright.sync_api import expect
    page,store,*_=ui;show(page);before=snapshot(store)['sha256']
    def bad(route):
        response=route.fetch();value=response.json()
        if fault=='hash':value['preview_hash']='0'*64
        elif fault=='counts':value['legacy_links']+=1
        elif fault=='technical_write':value['technical_resources_written']=True
        elif fault=='can_migrate':value['conflicts']=[{'entity':'fixture','issues':['invalid']}]
        else:value['resources']+=1
        route.fulfill(response=response,json=value)
    page.route('**/erp/model/migration-preview',bad);page.locator('#erp2-migration-preview').click()
    expect(page.locator('#erp2-migration-state')).to_contain_text('Keine vollständige gebundene ERP-Umstellungsvorschau')
    page.locator('#erp2-migration-accept').check();expect(page.locator('#erp2-migrate')).to_be_disabled();assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('fault',['count','last_item','snapshot','company','flags'])
def test_bad_article_page_clears_rows_and_allows_explicit_retry(enabled,fault):
    from playwright.sync_api import expect
    page,store,*_=enabled;articles(page,1);before=snapshot(store)['sha256']
    def bad(route):
        response=route.fetch();value=response.json()
        if fault=='count':value['filtered']+=1
        elif fault=='last_item':value['items'][0]['erp']['unit']=False
        elif fault=='snapshot':value['snapshot_sha256']='bad'
        elif fault=='company':value['company_id']='not chosen'
        else:value['resources_written']=True
        route.fulfill(response=response,json=value)
    pattern='**/erp/articles?*';page.route(pattern,bad);page.locator('#erp2-articles-read').click()
    expect(page.locator('#erp2-articles-state')).to_contain_text('Keine vollständige gebundene Artikelseite');expect(page.locator('#erp2-articles > tr')).to_have_count(0)
    page.unroute(pattern,bad);articles(page,1);assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('fault',['resource_revision','legacy','article','link'])
def test_bad_current_variant_never_unlocks_assignment(enabled,fault):
    from playwright.sync_api import expect
    page,store,value,resource=enabled;articles(page,1);choose(page);page.locator('#erp2-resource').select_option(resource['id']);before=snapshot(store)['sha256']
    def bad(route):
        response=route.fetch();data=response.json()
        if fault=='resource_revision':data['resource_revision']+=1
        elif fault=='legacy':data['legacy_erp_snapshot']={}
        elif fault=='article':data['article']=value
        else:data['link']={'resource_id':resource['id'],'revision':1,'article_id':value['id'],'updated_at':value['updated_at']}
        route.fulfill(response=response,json=data)
    page.route('**/erp/variants/'+resource['id'],bad);page.locator('#erp2-variant-read').click()
    expect(page.locator('#erp2-variant-state')).to_contain_text('Kein vollständiger aktueller Variantenbezug')
    expect(page.locator('#erp2-assign')).to_be_disabled();expect(page.locator('#erp2-unlink')).to_be_disabled();assert snapshot(store)['sha256']==before


def test_article_filter_aba_and_old_finally_cannot_unlock_new_read(enabled):
    from playwright.sync_api import expect
    page,store,*_=enabled;hold(page,'/erp/articles?');track(page,'erp2-articles-read');page.locator('#erp2-articles-read').click();wait_held(page)
    page.locator('#erp2-query').fill('temporary');page.locator('#erp2-query').fill('')
    hold(page,'/erp/articles?',name='new');track(page,'erp2-articles-read',name='new');page.locator('#erp2-articles-read').click();wait_held(page,'new')
    unhold(page);expect(page.locator('#erp2-articles-read')).to_be_disabled();expect(page.locator('#erp2-articles > tr')).to_have_count(0)
    unhold(page,'new');expect(page.locator('#erp2-articles-read')).to_be_enabled();expect(page.locator('#erp2-articles > tr')).to_have_count(1)


@pytest.mark.parametrize('context',['resource_aba','logout','catalog'])
def test_late_variant_read_does_not_restore_old_context(enabled,context):
    from playwright.sync_api import expect
    page,store,value,resource=enabled;articles(page,1);choose(page);page.locator('#erp2-resource').select_option(resource['id'])
    hold(page,'/erp/variants/'+resource['id']);track(page,'erp2-variant-read');page.locator('#erp2-variant-read').click();wait_held(page)
    if context=='resource_aba':page.locator('#erp2-resource').select_option('');page.locator('#erp2-resource').select_option(resource['id'])
    elif context=='logout':page.locator('#disconnect').click()
    else:
        from test_requests_browser import payload
        body=payload(resource);body['name']='Changed technical resource';store.save_resource(body,'fixture',resource['id'],1)
        page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Changed technical resource')
    unhold(page);expect(page.locator('#erp2-assign')).to_be_disabled();assert page.locator('#erp2-variant-detail').inner_text()==''


def test_variant_reassign_unlink_and_delayed_commit_do_not_restore_old_inputs(enabled):
    from playwright.sync_api import expect
    page,store,value,resource=enabled;manager=ERPVariants(store)
    second=manager.upsert({**value['erp'],'system_id':'other','number':'ITEM2'},0,'erp',str(uuid.uuid4()))
    articles(page,2);choose(page);page.locator('#erp2-resource').select_option(resource['id']);page.locator('#erp2-variant-read').click()
    expect(page.locator('#erp2-assign')).to_be_enabled();hold(page,'/erp/variants/'+resource['id']);track(page,'erp2-assign');page.locator('#erp2-assign').click();wait_held(page)
    choose(page,'ITEM2');page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,description='Unsaved later commercial inputs')
    unhold(page);expect(page.locator('#erp2-form-description')).to_have_value('Unsaved later commercial inputs')
    expect(page.locator('#erp2-selected')).to_contain_text('ITEM2');expect(page.locator('#erp2-selected')).to_contain_text('veraltet')
    assert manager.variant(resource['id'])['article']['id']==value['id']
    articles(page,2);choose(page,'ITEM2');page.locator('#erp2-variant-read').click();expect(page.locator('#erp2-assign')).to_be_enabled();page.locator('#erp2-assign').click()
    expect(page.locator('#erp2-recovery')).to_be_hidden();page.locator('#erp2-variant-read').click();expect(page.locator('#erp2-unlink')).to_be_enabled();page.locator('#erp2-unlink').click()
    expect(page.locator('#erp2-recovery')).to_be_hidden();assert manager.variant(resource['id'])['link']['article_id'] is None
    assert [item['article_id'] for item in manager.history('variant',resource['id'])['items']]==[value['id'],second['id'],None]


def test_paging_search_history_and_mobile_are_bounded(enabled):
    from playwright.sync_api import expect
    page,store,value,resource=enabled;manager=ERPVariants(store)
    for index in range(52):manager.upsert({**value['erp'],'system_id':'more'+str(index),'description':'Fixture '+str(index)},0,'erp',str(uuid.uuid4()))
    articles(page,50);page.locator('#erp2-articles-next').click();expect(page.locator('#erp2-articles > tr')).to_have_count(3)
    page.locator('#erp2-query').fill('Fixture 51');articles(page,1)
    page.locator('#erp2-query').fill('');page.locator('#erp2-company').fill(value['erp']['company_id']);page.locator('#erp2-system').fill(value['erp']['system_id']);articles(page,1)
    for index in range(1,23):manager.upsert({**value['erp'],'source_revision':str(index),'description':'Revision '+str(index)},index,'erp',str(uuid.uuid4()))
    articles(page,1);page.locator('#erp2-articles > tr').get_by_role('button',name='Artikelhistorie',exact=True).click()
    expect(page.locator('#erp2-history-items > details')).to_have_count(20);page.locator('#erp2-history-next').click();expect(page.locator('#erp2-history-items > details')).to_have_count(3)
    expect(page.locator('#erp2-history-items > details').first.locator('summary')).to_contain_text('Revision 21')
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')


@pytest.mark.parametrize('fault',['action','request_sha256','result','flags'])
def test_wrong_receipt_keeps_ticket_and_cannot_acknowledge_an_unrelated_commit(enabled,fault):
    from playwright.sync_api import expect
    page,store,*_=enabled;page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,system_id='new-article')
    def bad(route):
        response=route.fetch();value=response.json()
        if fault=='action':value['action']='variant.assign'
        elif fault=='request_sha256':value['request_sha256']='0'*64
        elif fault=='result':value['result']['erp']['description']='Unrelated response'
        else:value['automatic_approval']=True
        route.fulfill(response=response,json=value)
    pattern='**/erp/requests/*';page.route(pattern,bad);page.locator('#erp2-article-save').click()
    expect(page.locator('#erp2-recovery')).to_be_visible();expect(page.locator('#erp2-write-state')).to_contain_text('nicht bestätigt')
    before=store.audit_entries();page.unroute(pattern,bad);page.locator('#erp2-receipt').click();expect(page.locator('#erp2-recovery')).to_be_hidden()
    assert store.audit_entries()==before


def test_broken_ticket_blocks_writes_until_explicit_local_ack_without_server_change(enabled):
    from playwright.sync_api import expect
    page,store,*_=enabled;page.evaluate('sessionStorage.setItem("nx.pending-erp-write.v1","not valid json")');page.reload()
    page.locator('#token').fill('isolated-requests-browser-'+'q'*40);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden');show(page)
    page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,system_id='new-article');before=snapshot(store)['sha256']
    expect(page.locator('#erp2-article-save')).to_be_disabled();expect(page.locator('#erp2-recovery-state')).to_contain_text('nicht lesbar')
    page.locator('#erp2-ticket-discard').click();expect(page.locator('#erp2-article-save')).to_be_enabled();assert snapshot(store)['sha256']==before


def test_lost_committed_migration_recovers_after_reload_without_second_migration(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());show(page)
    page.locator('#erp2-migration-preview').click();expect(page.locator('#erp2-migration-state')).to_contain_text('Umstellung prüfbar');page.locator('#erp2-migration-accept').check()
    lose(page,'/api/v1/erp/model/migrate');page.locator('#erp2-migrate').click();expect(page.locator('#erp2-write-state')).to_contain_text('Synthetic network reply lost')
    audit=store.audit_entries();assert ERPVariants(store).status()['enabled']
    page.reload();page.locator('#token').fill('isolated-requests-browser-'+'q'*40);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden');show(page)
    expect(page.locator('#erp2-status')).to_contain_text('1:n-Modell aktiv');expect(page.locator('#erp-read')).to_be_disabled();expect(page.locator('#erp2-repeat')).to_be_disabled()
    page.locator('#erp2-receipt').click();expect(page.locator('#erp2-recovery')).to_be_hidden();assert store.audit_entries()==audit


def test_lost_assignment_retry_does_not_use_new_resource_selection(enabled):
    from playwright.sync_api import expect
    page,store,value,resource=enabled;articles(page,1);choose(page);page.locator('#erp2-resource').select_option(resource['id']);page.locator('#erp2-variant-read').click()
    expect(page.locator('#erp2-assign')).to_be_enabled();lose(page,'/api/v1/erp/variants/'+resource['id']);page.locator('#erp2-assign').click();expect(page.locator('#erp2-write-state')).to_contain_text('Synthetic network reply lost')
    audit=store.audit_entries();page.locator('#erp2-resource').select_option('');page.locator('#erp2-repeat').click();expect(page.locator('#erp2-recovery')).to_be_hidden()
    expect(page.locator('#erp2-resource')).to_have_value('');assert store.audit_entries()==audit and ERPVariants(store).variant(resource['id'])['article']['id']==value['id']


@pytest.mark.parametrize('fault',['revision','value','flags','entity'])
def test_history_contract_is_validated_before_any_rows_are_shown(enabled,fault):
    from playwright.sync_api import expect
    page,store,value,_=enabled;articles(page,1);before=snapshot(store)['sha256']
    def bad(route):
        response=route.fetch();data=response.json()
        if fault=='revision':data['items'][0]['revision']=2
        elif fault=='value':data['items'][0]['erp']['description']=False
        elif fault=='flags':data['nx_files_written']=True
        else:data['id']=str(uuid.uuid4())
        route.fulfill(response=response,json=data)
    page.route('**/erp/articles/'+value['id']+'/history?*',bad);page.locator('#erp2-articles > tr').get_by_role('button',name='Artikelhistorie',exact=True).click()
    expect(page.locator('#erp2-history-state')).to_contain_text('Keine vollständige gebundene ERP-Historie');expect(page.locator('#erp2-history-items > details')).to_have_count(0)
    assert snapshot(store)['sha256']==before


def test_reading_shared_erp_does_not_replace_unsaved_technical_cam_editor(enabled):
    from playwright.sync_api import expect
    page,store,value,resource=enabled
    ERPVariants(store).assign(resource['id'],value['id'],0,resource['revision'],value['revision'],'admin',str(uuid.uuid4()))
    page.locator('#resources > tr').get_by_role('button',name='Bearbeiten',exact=True).click();page.locator('#name').fill('Unsaved technical CAM label')
    show(page);before=snapshot(store)['sha256'];articles(page,1);choose(page);page.locator('#erp2-variants-read').click();expect(page.locator('#erp2-variants > tr')).to_have_count(1)
    page.locator('#erp2-resource').select_option(resource['id']);page.locator('#erp2-variant-read').click();expect(page.locator('#erp2-variant-state')).to_contain_text('Heutige Zuordnung gelesen')
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved technical CAM label');assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('company,system',[('__proto__','constructor'),(' Company ','🛠-system')])
def test_request_hash_keeps_exact_opaque_unicode_and_reserved_identity_strings(enabled,company,system):
    from playwright.sync_api import expect
    page,store,*_=enabled;page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,company_id=company,system_id=system,description='Überprüfung ä / CAM')
    page.locator('#erp2-article-save').click();expect(page.locator('#erp2-recovery')).to_be_hidden();expect(page.locator('#erp2-form-context')).to_contain_text('Revision 1')
    values=ERPVariants(store).read(company_id=company,system_id=system)['items'];assert len(values)==1 and values[0]['erp']['company_id']==company and values[0]['erp']['system_id']==system


def test_unavailable_ticket_storage_prevents_network_write(enabled):
    from playwright.sync_api import expect
    page,store,*_=enabled;page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,system_id='not-sent')
    page.evaluate("()=>{window.__originalSessionSet=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key==='nx.pending-erp-write.v1')throw new Error('Synthetic unavailable storage');return window.__originalSessionSet.call(this,key,value);};}")
    before=snapshot(store)['sha256'];page.locator('#erp2-article-save').click();expect(page.locator('#erp2-write-state')).to_contain_text('nichts gesendet');expect(page.locator('#erp2-article-save')).to_be_disabled()
    assert snapshot(store)['sha256']==before


def test_loaded_current_details_are_removed_when_catalog_changes(enabled):
    from playwright.sync_api import expect
    from test_requests_browser import payload
    page,store,value,resource=enabled;ERPVariants(store).assign(resource['id'],value['id'],0,resource['revision'],value['revision'],'admin',str(uuid.uuid4()))
    articles(page,1);choose(page);page.locator('#erp2-resource').select_option(resource['id']);page.locator('#erp2-variant-read').click()
    expect(page.locator('#erp2-variant-detail')).to_contain_text('Aktuell: ITEM1')
    changed=payload(resource);changed['name']='Changed technical label';store.save_resource(changed,'fixture',resource['id'],1)
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Changed technical label')
    expect(page.locator('#erp2-variant-detail')).to_have_text('');expect(page.locator('#erp2-assign')).to_be_disabled()


def test_external_erp_change_invalidates_loaded_views_without_any_cam_revision_change(enabled):
    from playwright.sync_api import expect
    page,store,value,resource=enabled;manager=ERPVariants(store);manager.assign(resource['id'],value['id'],0,resource['revision'],value['revision'],'admin',str(uuid.uuid4()))
    page.locator('#refresh').click();articles(page,1);choose(page);page.locator('#erp2-resource').select_option(resource['id']);page.locator('#erp2-variant-read').click()
    expect(page.locator('#erp2-variant-detail')).to_contain_text('Aktuell: ITEM1');before=store.resources()
    manager.upsert({**value['erp'],'description':'Changed externally in ERP model'},1,'erp',str(uuid.uuid4()))
    assert store.resources()==before
    page.locator('#refresh').click();expect(page.locator('#erp2-articles-state')).to_contain_text('Verwaltungs-/CAM-Bestand geändert')
    expect(page.locator('#erp2-variant-detail')).to_have_text('');expect(page.locator('#erp2-assign')).to_be_disabled()
