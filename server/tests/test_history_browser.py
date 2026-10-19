"""Read-only history GUI and stale-response fences on disposable local data."""
import copy
import os

import pytest

from test_requests_browser import ui,payload

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge history workflows.')


def revise(ui,cam=None):
    from playwright.sync_api import expect
    page,store,_,resource=ui;value=payload(resource);value['name']='Updated fixture'
    if cam is not None:value['cam']=copy.deepcopy(cam)
    current=store.save_resource(value,'fixture',resource['id'],1)
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Updated fixture')
    return current


def open_history(page,name=None):
    from playwright.sync_api import expect
    page.locator('#tab-manage').click()
    row=page.locator('#resources tr')
    if name:row=row.filter(has_text=name)
    row.get_by_role('button',name='Historie',exact=True).click()
    expect(page.locator('#history-state')).to_contain_text('Aktueller Historienkopf')


def hold(page,fragment,name='old',error=False,transform='null'):
    page.evaluate("""([fragment,name,error,transform])=>{
      const original=window.fetch;window.__holds??={};const state=window.__holds[name]={held:false,decoded:false};
      window.fetch=async(...args)=>{let response=await original(...args);
        if(String(args[0]).includes(fragment)){
          window.fetch=original;
          if(error)response=new Response(JSON.stringify({detail:'Old delayed history error'}),{status:500});
          else if(transform!=='null'){const value=await response.json();
            if(transform==='bad_preview')value.items[value.items.length-1].after.text=42;
            else if(transform==='incomplete')value.complete=false;
            else if(transform==='bad_count')value.filtered=-1;
            else if(transform==='bad_pointer')value.items[0].path='/wrong';
            else if(transform==='bad_summary')value.items[0].revision=1;
            else throw new Error('Unknown isolated mutation.');
            response=new Response(JSON.stringify(value),{status:200});}
          const json=response.json.bind(response);response.json=async()=>{const value=await json();state.decoded=true;return value;};
          const observed=new WeakSet();function observeBody(body){if(!body||observed.has(body))return;observed.add(body);
            const cancel=body.cancel.bind(body);body.cancel=(...args)=>{state.decoded=true;return cancel(...args);};}
          observeBody(response.body);const clone=response.clone.bind(response);
          response.clone=(...args)=>{const value=clone(...args);observeBody(response.body);return value;};
          return new Promise(resolve=>{state.release=()=>resolve(response);state.held=true;});
        }return response;};
    }""",[fragment,name,error,transform])


def track(page,identifier,name='old'):
    page.evaluate("""([id,name])=>{const element=document.getElementById(id),handler=element.onclick;
      window.__actions??={};element.onclick=function(...args){element.onclick=handler;window.__actions[name]=false;
        const result=handler.apply(this,args);Promise.resolve(result).finally(()=>window.__actions[name]=true);return result;};}
    """,[identifier,name])


def wait_held(page,name='old'):page.wait_for_function('name=>window.__holds[name].held',arg=name)
def unhold(page,name='old'):
    page.evaluate('name=>window.__holds[name].release()',name)
    page.wait_for_function('name=>window.__holds[name].decoded&&window.__actions[name]',arg=name)


def test_history_does_not_adopt_revision_or_change_unsaved_editor(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;revise(ui,{'rows':[{'DIA':'12','RTYPE':'1'},{'DIA':'20','RTYPE':'2'}]})
    before=store.history(store.resources()[0]['id']);audit=store.audit_entries()
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#name').fill('Unsaved personal editor value')
    if page.locator('#editor-section details.technical').get_attribute('open') is None:
        page.locator('#editor-section details.technical > summary').click()
    page.locator('#cam').fill('{')
    open_history(page);expect(page.locator('#history-index tr')).to_have_count(2)
    page.locator('#history-compare').click();expect(page.locator('#history-diff')).to_contain_text('Technische CAM-Daten')
    expect(page.locator('#history-state')).to_contain_text('Keine Rücknahme')
    page.locator('#history-close').click();expect(page.locator('#history-box')).to_be_hidden()
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved personal editor value');expect(page.locator('#cam')).to_have_value('{')
    assert store.history(store.resources()[0]['id'])==before and store.audit_entries()==audit and store.active()['release_id'] is None


def test_revision_and_diff_pagination_keep_explicit_loaded_selection(ui):
    from playwright.sync_api import expect
    page,store,_,resource=ui;value=payload(resource)
    for revision in range(1,25):
        value['name']='Revision '+str(revision+1);value['cam']={f'field{index:03}':str(revision) for index in range(125)}
        store.save_resource(value,'fixture',resource['id'],revision)
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Revision 25')
    before=store.audit_entries();open_history(page)
    expect(page.locator('#history-index tr')).to_have_count(20);page.locator('#history-index-next').click()
    expect(page.locator('#history-index tr')).to_have_count(5);expect(page.locator('#history-before')).to_have_value('24');expect(page.locator('#history-after')).to_have_value('25')
    page.locator('#history-before').select_option('1');page.locator('#history-after').select_option('25');page.locator('#history-compare').click()
    expect(page.locator('#history-diff tr')).to_have_count(50);expect(page.locator('#history-diff-page')).to_contain_text('1–50 von 126')
    page.locator('#history-diff-next').click();expect(page.locator('#history-diff-page')).to_contain_text('51–100')
    page.locator('#history-diff-next').click();expect(page.locator('#history-diff tr')).to_have_count(26);expect(page.locator('#history-diff-next')).to_be_disabled()
    page.locator('#history-diff-prev').click();expect(page.locator('#history-diff-page')).to_contain_text('51–100')
    assert store.audit_entries()==before


def test_long_full_value_search_unicode_and_prototype_fields_are_plain_text_mobile(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;revise(ui,{'huge':'🙂'*1400+' UNIQUE_TAIL','__proto__':'<img src=x onerror="window.__injected=true">','constructor':{'prototype':'literal'}})
    audit=store.audit_entries();open_history(page);page.locator('#history-area').select_option('cam');page.locator('#history-compare').click()
    expect(page.locator('#history-diff')).to_contain_text('Anzeige gekürzt');expect(page.locator('#history-diff')).to_contain_text('<img src=x')
    assert page.locator('#history-diff img').count()==0 and page.evaluate('window.__injected===true') is False
    page.locator('#history-query').fill('unique_tail');page.locator('#history-compare').click()
    expect(page.locator('#history-diff tr')).to_have_count(1);expect(page.locator('#history-diff-page')).to_contain_text('von 1 Treffern')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    assert store.audit_entries()==audit


@pytest.mark.parametrize('old_error',[False,True])
@pytest.mark.parametrize('selector',['query','area','revision'])
def test_selection_aba_discards_late_diff_and_late_error(ui,old_error,selector):
    from playwright.sync_api import expect
    page,store,*_=ui;revise(ui,{'DIA':'12'});open_history(page);audit=store.audit_entries()
    hold(page,'/history-diff?',error=old_error);track(page,'history-compare');page.locator('#history-compare').click();wait_held(page)
    if selector=='query':page.locator('#history-query').fill('temporary');page.locator('#history-query').fill('')
    elif selector=='area':page.locator('#history-area').select_option('cam');page.locator('#history-area').select_option('')
    else:page.locator('#history-before').select_option('2');page.locator('#history-before').select_option('1')
    expect(page.locator('#history-diff tr')).to_have_count(0);page.locator('#history-compare').click()
    expect(page.locator('#history-diff-page')).to_contain_text('Änderungen insgesamt');new=page.locator('#history-state').inner_text()
    unhold(page);expect(page.locator('#history-state')).to_have_text(new);expect(page.locator('#history-compare')).to_be_enabled()
    assert store.audit_entries()==audit


def test_old_finally_does_not_unlock_a_new_pending_compare(ui):
    from playwright.sync_api import expect
    page,*_=ui;revise(ui,{'DIA':'12'});open_history(page)
    hold(page,'/history-diff?');track(page,'history-compare');page.locator('#history-compare').click();wait_held(page)
    page.locator('#history-query').fill('DIA');hold(page,'/history-diff?',name='new');track(page,'history-compare',name='new')
    page.locator('#history-compare').click();wait_held(page,'new');unhold(page)
    expect(page.locator('#history-compare')).to_be_disabled();expect(page.locator('#history-reload')).to_be_disabled()
    unhold(page,'new');expect(page.locator('#history-compare')).to_be_enabled();expect(page.locator('#history-diff tr')).to_have_count(1)


@pytest.mark.parametrize('action',['close','logout','different_resource'])
def test_late_diff_cannot_reopen_history_after_context_changes(ui,action):
    from playwright.sync_api import expect
    page,store,*_=ui;revise(ui,{'DIA':'12'});open_history(page)
    other=None
    if action=='different_resource':
        other=store.save_resource({'kind':'gauge','name':'Other fixture','cam':{},'erp':None,'asset_ids':[],'cam_status':'draft','geometry':{'mode':'none','status':'needs_preparation'}},'fixture')
        page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Other fixture')
    audit=store.audit_entries();hold(page,'/history-diff?');track(page,'history-compare');page.locator('#history-compare').click();wait_held(page)
    if action=='close':page.locator('#history-close').click()
    elif action=='logout':page.locator('#disconnect').click()
    else:open_history(page,'Other fixture')
    unhold(page)
    if action=='different_resource':expect(page.locator('#history-name')).to_contain_text(other['id']);expect(page.locator('#history-diff tr')).to_have_count(0)
    else:expect(page.locator('#history-box')).to_be_hidden()
    assert store.audit_entries()==audit


def test_current_catalog_revision_change_requires_explicit_history_reload(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;current=revise(ui,{'DIA':'12'});open_history(page);page.locator('#history-compare').click()
    expect(page.locator('#history-diff-page')).to_contain_text('Änderungen insgesamt')
    value=payload(current);value['name']='Third version';store.save_resource(value,'fixture',current['id'],2)
    page.locator('#refresh').click();expect(page.locator('#history-state')).to_contain_text('ausdrücklich neu laden')
    expect(page.locator('#history-compare')).to_be_disabled();page.locator('#history-reload').click()
    expect(page.locator('#history-state')).to_contain_text('Historienkopf Rev 3');expect(page.locator('#history-before')).to_have_value('2');expect(page.locator('#history-after')).to_have_value('3')
    expect(page.locator('#history-diff tr')).to_have_count(0);page.locator('#history-compare').click()
    expect(page.locator('#history-diff tr')).to_have_count(1)


@pytest.mark.parametrize('transform',['bad_preview','incomplete','bad_count','bad_pointer'])
def test_bad_diff_batch_never_renders_partial_complete_page(ui,transform):
    from playwright.sync_api import expect
    page,*_=ui;revise(ui,{'DIA':'12','LENGTH':'20'});open_history(page)
    hold(page,'/history-diff?',transform=transform);track(page,'history-compare');page.locator('#history-compare').click();wait_held(page);unhold(page)
    expect(page.locator('#history-state')).to_contain_text('Kein vollständiger gebundener Revisionsvergleich')
    expect(page.locator('#history-diff tr')).to_have_count(0);expect(page.locator('#history-diff-next')).to_be_disabled()
    page.locator('#history-compare').click();expect(page.locator('#history-diff tr')).to_have_count(3)


def test_pending_first_index_is_invalidated_by_a_new_workspace_revision(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;current=revise(ui,{'DIA':'12'})
    hold(page,'/history-summary?')
    page.evaluate("""()=>{window.__actions={old:false};void NXHistory.open(resources[0]).finally(()=>window.__actions.old=true);} """)
    wait_held(page);value=payload(current);value['name']='Third during first load';store.save_resource(value,'fixture',current['id'],2)
    page.locator('#refresh').click();expect(page.locator('#history-state')).to_contain_text('ausdrücklich neu laden');unhold(page)
    expect(page.locator('#history-index tr')).to_have_count(0);expect(page.locator('#history-compare')).to_be_disabled()
    page.locator('#history-reload').click();expect(page.locator('#history-state')).to_contain_text('Historienkopf Rev 3')


def test_bad_summary_never_partially_replaces_verified_index(ui):
    from playwright.sync_api import expect
    page,*_=ui;revise(ui,{'DIA':'12'});open_history(page)
    before=page.locator('#history-index').inner_text();hold(page,'/history-summary?',transform='bad_summary')
    track(page,'history-reload');page.locator('#history-reload').click();wait_held(page);unhold(page)
    expect(page.locator('#history-state')).to_contain_text('Kein unterstützter Revisionsbestand')
    assert page.locator('#history-index').inner_text()==before
    page.locator('#history-reload').click();expect(page.locator('#history-state')).to_contain_text('Historienkopf Rev 2')
