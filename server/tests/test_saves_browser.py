"""Lost, delayed and never-sent save responses; only isolated Edge/server state."""
import json
import os

import pytest

from test_requests_browser import ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated write recovery.')


def lose(page,suffix,before_send=False):
    page.evaluate("""([suffix,before])=>{const original=window.fetch;
      window.fetch=async(...args)=>{
        if(String(args[0]).endsWith(suffix)&&['POST','PUT'].includes(args[1]?.method)){
          window.fetch=original;window.__sentKey=args[1].headers['Idempotency-Key'];window.__sentBody=args[1].body;
          if(!before)await original(...args);
          throw new TypeError('Synthetic network reply lost');
        }return original(...args);
      };} """,[suffix,before_send])


@pytest.mark.parametrize('new',[True,False])
def test_lost_commit_reply_repeated_with_same_key_never_duplicates_and_preserves_edits(ui,new):
    from playwright.sync_api import expect
    page,store,release,resource=ui
    if new:page.locator('#new').click();page.locator('#kind').select_option('gauge')
    else:page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#name').fill('Submitted over unreliable link')
    path='/api/v1/resources'+('' if new else '/'+resource['id']);lose(page,path)
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    assert len(store.resources())==(2 if new else 1)
    saved=next(value for value in store.resources() if value['name']=='Submitted over unreliable link')
    revision=saved['revision'];audit=store.audit_entries();key=page.locator('#save-request-key').inner_text()
    expect(page.locator('#save')).to_be_disabled();page.locator('#name').fill('Typed after lost response')
    page.locator('#save-repeat').click();expect(page.locator('#save')).to_be_enabled()
    assert store.audit_entries()==audit and next(value for value in store.resources() if value['id']==saved['id'])['revision']==revision
    expect(page.locator('#name')).to_have_value('Typed after lost response')
    assert page.evaluate('window.__sentKey')==key
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    expect(page.locator('#message')).to_contain_text(' / Rev '+str(revision+1)+'.')
    result=next(value for value in store.resources() if value['id']==saved['id'])
    assert result['name']=='Typed after lost response' and result['revision']==revision+1
    assert len(store.resources())==(2 if new else 1)


def test_reload_keeps_only_non_secret_ticket_and_recovers_committed_revision(ui):
    from playwright.sync_api import expect
    page,store,release,resource=ui
    page.locator('#new').click();page.locator('#kind').select_option('gauge');page.locator('#name').fill('Secret-ish engineering label, not cached')
    lose(page,'/api/v1/resources');page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")');ticket=json.loads(raw)
    assert set(ticket)=={'contract_version','key','path','method','created_at'}
    assert 'engineering label' not in raw and 'isolated-requests-browser' not in raw and 'cam' not in raw
    page.reload();page.locator('#token').fill('isolated-requests-browser-'+'q'*40);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-prepare').click()
    expect(page.locator('#save-repeat')).to_be_disabled();expect(page.locator('#save')).to_be_disabled()
    page.locator('#name').fill('New unsaved editor after reload');page.locator('#save-receipt').click()
    expect(page.locator('#save')).to_be_enabled();expect(page.locator('#name')).to_have_value('New unsaved editor after reload')
    assert page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")') is None
    assert len(store.resources())==2 and next(value for value in store.resources() if 'engineering label' in value['name'])['revision']==1


def test_never_sent_request_is_fenced_not_assumed_stopped_from_missing_receipt(ui):
    from playwright.sync_api import expect
    page,store,release,resource=ui;page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#new').click();page.locator('#kind').select_option('gauge');page.locator('#name').fill('Not yet sent')
    lose(page,'/api/v1/resources',True);page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#save-request-key').inner_text();sent=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#save-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still finish')
    expect(page.locator('#save')).to_be_disabled();assert len(store.resources())==1
    page.locator('#save-cancel').click();expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#name')).to_have_value('Not yet sent')
    status=page.evaluate("""async([key,body])=>{const response=await fetch('/api/v1/resources',{method:'POST',headers:{Authorization:'Bearer '+'isolated-requests-browser-'+'q'.repeat(40),'Idempotency-Key':key,'Content-Type':'application/json'},body:JSON.stringify(body)});return response.status;}""",[key,sent])
    assert status==409 and len(store.resources())==1
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert len(store.resources())==2


def test_cancel_of_lost_success_returns_commit_without_deleting_data(ui):
    from playwright.sync_api import expect
    page,store,release,resource=ui;page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click();page.locator('#name').fill('Already committed')
    lose(page,'/api/v1/resources/'+resource['id']);page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost');page.locator('#save-cancel').click()
    expect(page.locator('#message')).to_contain_text('Schreibbeleg bestätigt');expect(page.locator('#save')).to_be_enabled()
    assert len(store.resources())==1 and store.resources()[0]['name']=='Already committed' and store.resources()[0]['revision']==2
