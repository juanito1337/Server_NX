"""Read-only CAM-to-ERP navigation on isolated 1:N data, never Business Central."""
import os
import uuid
from urllib.parse import urlencode

import pytest

from nxserver.erp_variants import ERPVariants
from nxserver.integrity import snapshot
from test_erp_variants_browser import enabled,show,articles,fields
from test_requests_browser import ui
from test_requests_browser import hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated ERP navigation.')


@pytest.fixture
def linked(enabled):
    from playwright.sync_api import expect
    page,store,article,resource=enabled;manager=ERPVariants(store)
    second=store.save_resource({'kind':'gauge','name':'Second independent CAM variant','cam':{},'erp':None,
        'asset_ids':[],'cam_status':'draft'},'fixture')
    for value in (resource,second):manager.assign(value['id'],article['id'],0,value['revision'],article['revision'],'admin',str(uuid.uuid4()))
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    return page,store,article,resource,second


def resource_button(page,resource):
    return page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='ERP-Bezug prüfen',exact=True)


def test_resource_inventory_opens_current_shared_article_without_replacing_editor_or_erp_form(linked):
    from playwright.sync_api import expect
    page,store,article,resource,_=linked
    page.locator('#new').click();page.locator('#name').fill('Technical work must remain unsaved');show(page)
    page.locator('#erp2-form').evaluate('(element)=>{element.open=true;}');fields(page,description='Unsaved commercial input')
    before=snapshot(store)['sha256'];writes=[]
    page.on('request',lambda request:writes.append(request.url) if request.method not in ('GET','HEAD') else None)
    resource_button(page,resource).click();expect(page.locator('#erp2-variant-state')).to_contain_text('Heutige Zuordnung gelesen')
    expect(page.locator('#erp2-current-article-fields tr')).to_have_count(6)
    expect(page.locator('#erp2-current-article-fields')).to_contain_text(article['erp']['number'])
    expect(page.locator('#name')).to_have_value('Technical work must remain unsaved')
    expect(page.locator('#erp2-form-description')).to_have_value('Unsaved commercial input')
    assert snapshot(store)['sha256']==before and writes==[]


def test_inspected_article_can_be_explicitly_selected_and_its_multiple_variants_loaded(linked):
    from playwright.sync_api import expect
    page,store,article,resource,second=linked;before=snapshot(store)['sha256']
    resource_button(page,resource).click();expect(page.locator('#erp2-current-article-open')).to_be_enabled()
    page.locator('#erp2-current-article-open').click();expect(page.locator('#erp2-selected')).to_contain_text(article['id'])
    page.locator('#erp2-variants-read').click();expect(page.locator('#erp2-variants tr')).to_have_count(2)
    expect(page.locator('#erp2-variants')).to_contain_text(second['name']);assert snapshot(store)['sha256']==before


def other_article(page,store,article):
    from playwright.sync_api import expect
    value=ERPVariants(store).upsert({**article['erp'],'system_id':'OTHER-ID','number':'OTHER-ARTICLE'},0,'erp',str(uuid.uuid4()))
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert');articles(page,2)
    return value


def test_inspection_preserves_another_explicit_assignment_target(linked):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked;other=other_article(page,store,article)
    page.locator('#erp2-articles tr').filter(has_text=other['id']).get_by_role('button',name='Zielartikel wählen',exact=True).click()
    before=snapshot(store)['sha256'];resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article-fields')).to_contain_text(article['erp']['number'])
    expect(page.locator('#erp2-selected')).to_contain_text(other['id']);assert snapshot(store)['sha256']==before


def test_current_article_history_is_read_only_and_uses_the_article_not_cam_revision(linked):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked;manager=ERPVariants(store);resources=store.resources()
    updated=manager.upsert({**article['erp'],'description':'Shared article revision two','source_revision':'source-two'},1,'erp',str(uuid.uuid4()))
    before=snapshot(store)['sha256'];resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article-context')).to_contain_text('Artikelrevision 2')
    expect(page.locator('#erp2-current-article-fields')).to_contain_text('Shared article revision two')
    page.locator('#erp2-current-article-history').click();expect(page.locator('#erp2-history-items > details')).to_have_count(2)
    expect(page.locator('#erp2-history-title')).to_contain_text(updated['erp']['number'])
    assert store.resources()==resources and snapshot(store)['sha256']==before


def test_unassigned_variant_has_no_inherited_or_guessed_article(enabled):
    from playwright.sync_api import expect
    page,store,article,resource=enabled;before=snapshot(store)['sha256'];resource_button(page,resource).click()
    expect(page.locator('#erp2-variant-detail')).to_contain_text('Aktuell kein Artikel zugeordnet')
    expect(page.locator('#erp2-current-article')).to_be_hidden();expect(page.locator('#erp2-current-article-open')).to_be_disabled()
    assert snapshot(store)['sha256']==before


def test_legacy_model_navigation_is_guidance_not_an_implicit_migration(ui):
    from playwright.sync_api import expect
    page,store,_,resource=ui;before=snapshot(store)['sha256'];requests=[]
    page.on('request',lambda request:requests.append((request.method,request.url)))
    resource_button(page,resource).click();expect(page.locator('#erp2-navigation-state')).to_contain_text('1:n-Modell ist nicht aktiv')
    expect(page.locator('#erp2-migrate')).to_be_disabled();assert requests==[] and snapshot(store)['sha256']==before
    assert ERPVariants(store).status()['enabled'] is False


@pytest.mark.parametrize('change',['selection','aba','logout'])
@pytest.mark.parametrize('old_error',[False,True])
def test_late_variant_navigation_never_renders_an_old_article_context(linked,change,old_error):
    from playwright.sync_api import expect
    page,store,_,resource,second=linked;before=snapshot(store)['sha256']
    hold(page,'/erp/variants/'+resource['id'],error=old_error)
    button=resource_button(page,resource);button.evaluate('(element)=>{element.id="synthetic-erp-navigation";}');track(page,'synthetic-erp-navigation')
    button.click();page.wait_for_function('()=>window.__held')
    if change=='logout':page.locator('#disconnect').click()
    else:
        page.locator('#erp2-resource').select_option(second['id'])
        if change=='aba':page.locator('#erp2-resource').select_option(resource['id'])
    release(page);expect(page.locator('#erp2-current-article')).to_be_hidden()
    expect(page.locator('#erp2-current-article-fields tr')).to_have_count(0)
    expect(page.locator('#message')).not_to_contain_text('Old delayed error');assert snapshot(store)['sha256']==before


def test_newer_resource_navigation_wins_without_old_success_or_error_overwrite(linked):
    from playwright.sync_api import expect
    page,store,article,resource,second=linked;other=other_article(page,store,article)
    ERPVariants(store).assign(second['id'],other['id'],1,second['revision'],other['revision'],'admin',str(uuid.uuid4()))
    before=snapshot(store)['sha256'];hold(page,'/erp/variants/'+resource['id'])
    button=resource_button(page,resource);button.evaluate('(element)=>{element.id="synthetic-erp-navigation";}');track(page,'synthetic-erp-navigation')
    button.click();page.wait_for_function('()=>window.__held');resource_button(page,second).click()
    expect(page.locator('#erp2-current-article-fields')).to_contain_text(other['erp']['number']);release(page)
    expect(page.locator('#erp2-resource')).to_have_value(second['id'])
    expect(page.locator('#erp2-current-article-fields')).to_contain_text(other['erp']['number']);assert snapshot(store)['sha256']==before


def test_server_article_change_requires_new_variant_read_before_target_navigation(linked):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked;resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article-open')).to_be_enabled()
    ERPVariants(store).upsert({**article['erp'],'description':'New commercial revision'},article['revision'],'erp',str(uuid.uuid4()))
    before=snapshot(store)['sha256'];page.locator('#erp2-current-article-open').click()
    expect(page.locator('#erp2-navigation-state')).to_contain_text('Nicht bestätigt')
    expect(page.locator('#erp2-current-article')).to_be_hidden();expect(page.locator('#erp2-assign')).to_be_disabled()
    page.locator('#erp2-variant-read').click();expect(page.locator('#erp2-current-article-fields')).to_contain_text('New commercial revision')
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('fault',['flags','resource','link','fields','article'])
def test_malformed_variant_response_clears_old_article_card_and_disables_navigation(linked,fault):
    from playwright.sync_api import expect
    page,store,_,resource,second=linked;resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article')).to_be_visible();before=snapshot(store)['sha256']
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='flags':value['resources_written']=True
        elif fault=='resource':value['resource_id']=second['id']
        elif fault=='link':value['link']['resource_id']=second['id']
        elif fault=='fields':value['article']['erp']['unit']='x'*51
        else:value['article']['id']=str(uuid.uuid4())
        route.fulfill(response=response,json=value)
    page.route('**/api/v1/erp/variants/'+resource['id'],invalid);page.locator('#erp2-variant-read').click()
    expect(page.locator('#erp2-variant-state')).to_contain_text('Nicht bestätigt')
    expect(page.locator('#erp2-current-article-fields tr')).to_have_count(0);expect(page.locator('#erp2-current-article-open')).to_be_disabled()
    page.locator('#erp2-current-article-open').evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    assert snapshot(store)['sha256']==before


def test_late_target_lookup_cannot_replace_a_newer_explicit_article_choice(linked):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked;other=other_article(page,store,article);resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article-open')).to_be_enabled();pin=ERPVariants(store).variant(resource['id'])['snapshot_sha256']
    query=urlencode({'company_id':article['erp']['company_id'],'system_id':article['erp']['system_id'],'query':'','offset':0,'limit':50,'snapshot_sha256':pin})
    before=snapshot(store)['sha256'];hold(page,'/erp/articles?'+query);track(page,'erp2-current-article-open')
    page.locator('#erp2-current-article-open').click();page.wait_for_function('()=>window.__held')
    page.locator('#erp2-articles tr').filter(has_text=other['id']).get_by_role('button',name='Zielartikel wählen',exact=True).click()
    release(page);expect(page.locator('#erp2-selected')).to_contain_text(other['id'])
    expect(page.locator('#erp2-navigation-state')).not_to_contain_text('ausdrücklich als Ziel gewählt');assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('fault',['snapshot','identity','revision','cardinality','flags'])
def test_invalid_article_target_page_never_changes_selected_article_or_forms(linked,fault):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked;other=other_article(page,store,article)
    page.locator('#erp2-articles tr').filter(has_text=other['id']).get_by_role('button',name='Zielartikel wählen',exact=True).click()
    resource_button(page,resource).click();expect(page.locator('#erp2-current-article-open')).to_be_enabled();before=snapshot(store)['sha256']
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='snapshot':value['snapshot_sha256']='0'*64
        elif fault=='identity':value['company_id']='not-the-selected-company'
        elif fault=='revision':value['items'][0]['revision']+=1
        elif fault=='cardinality':value['total']+=1
        else:value['automatic_approval']=True
        route.fulfill(response=response,json=value)
    page.route('**/api/v1/erp/articles?*',invalid);page.locator('#erp2-current-article-open').click()
    expect(page.locator('#erp2-navigation-state')).to_contain_text('Nicht bestätigt')
    expect(page.locator('#erp2-selected')).to_contain_text(other['id']);expect(page.locator('#erp2-assign')).to_be_disabled()
    expect(page.locator('#erp2-current-article')).to_be_hidden();assert snapshot(store)['sha256']==before


def test_unconfirmed_article_write_allows_inspection_but_not_new_target_selection(linked):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked;articles(page,1)
    page.locator('#erp2-articles tr').get_by_role('button',name='ERP-Felder bearbeiten',exact=True).click()
    page.locator('#erp2-form-description').fill('Submitted change with a lost reply');lose(page,'/api/v1/erp/articles/upsert')
    page.locator('#erp2-article-save').click();expect(page.locator('#erp2-write-state')).to_contain_text('Synthetic network reply lost')
    before=snapshot(store)['sha256'];resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article-fields')).to_contain_text('Submitted change with a lost reply')
    expect(page.locator('#erp2-current-article-open')).to_be_disabled()
    page.locator('#erp2-current-article-open').evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    assert snapshot(store)['sha256']==before


def test_erp_card_is_text_only_and_does_not_overflow_mobile_layout(linked):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked
    ERPVariants(store).upsert({**article['erp'],'description':'<img src=x onerror="window.__erpInjected=1">','source_revision':'r'*100},article['revision'],'erp',str(uuid.uuid4()))
    before=snapshot(store)['sha256'];page.set_viewport_size({'width':390,'height':844});resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article-fields')).to_contain_text('<img src=x')
    expect(page.locator('#erp2-current-article img')).to_have_count(0)
    assert page.evaluate('window.__erpInjected===undefined')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert snapshot(store)['sha256']==before


def test_valid_imported_unicode_fields_use_server_character_limits(linked):
    from playwright.sync_api import expect
    page,store,article,resource,*_=linked
    description='🔧'*150;source_revision='🔩'*70
    ERPVariants(store).upsert({**article['erp'],'description':description,'source_revision':source_revision},article['revision'],'erp',str(uuid.uuid4()))
    before=snapshot(store)['sha256'];resource_button(page,resource).click()
    expect(page.locator('#erp2-current-article-fields')).to_contain_text(description)
    expect(page.locator('#erp2-current-article-fields')).to_contain_text(source_revision)
    assert snapshot(store)['sha256']==before
