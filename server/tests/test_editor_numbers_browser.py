"""Exact Python JSON numbers through the actual isolated editor/write workflow."""
import json
import os

import pytest

from test_requests_browser import ui, payload, hold, track, release
from test_saves_browser import lose

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
                               reason='Opt-in isolated numeric editor/write fidelity.')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def fresh(page):
    track(page, 'refresh');page.locator('#refresh').click()
    page.wait_for_function('()=>window.__oldActionFinished')


def cam_text(page, value):
    page.locator('#editor-section details.technical').evaluate('(node)=>node.open=true')
    page.locator('#cam').fill(json.dumps(value, ensure_ascii=False))


@pytest.mark.parametrize('number', [1.0, -0.0, 2**53+1, 1e20, 10**400])
def test_name_only_edit_preserves_display_and_saved_cam_numbers_and_old_revision(ui, number):
    from playwright.sync_api import expect
    page,store,_,resource=ui;cam={'measurement':number,'nested':[number,{'__proto__':number,'text':'1.0'}]}
    value=payload(resource);value['cam']=cam
    seeded=store.save_resource(value,'fixture',resource['id'],resource['revision'])
    old={item['revision']:item['payload'] for item in store.history(resource['id'])}
    assert canonical(json.loads(old[seeded['revision']])['cam'])==canonical(cam)
    fresh(page)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    shown=json.loads(page.locator('#cam').input_value())
    page.locator('#name').fill('Only a name changed')
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=store.resources()[0]
    assert saved['revision']==seeded['revision']+1 and saved['name']=='Only a name changed'
    assert canonical(saved['cam'])==canonical(cam)
    assert canonical(shown)==canonical(cam)
    assert canonical(json.loads(page.locator('#details').inner_text())['cam'])==canonical(cam)
    after={item['revision']:item['payload'] for item in store.history(resource['id'])}
    assert all(after[revision]==text for revision,text in old.items())


def test_new_free_json_draft_keeps_mixed_numeric_types_and_literal_keys(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;cam={'integer':2**53+1,'float':1.0,'negative_zero':-0.0,'small':1e-7,
                         'huge_integer':10**400,'__proto__':{'constructor':[1e20,0.015,'1.0']}}
    page.locator('#new').click();page.locator('#kind').select_option('gauge')
    page.locator('#name').fill('Numeric new draft');cam_text(page,cam)
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=next(item for item in store.resources() if item['name']=='Numeric new draft')
    assert saved['revision']==1 and canonical(saved['cam'])==canonical(cam)
    assert canonical(json.loads(page.locator('#details').inner_text())['cam'])==canonical(cam)


@pytest.mark.parametrize('before_send',[False,True])
def test_lost_numeric_save_repeats_exact_body_without_duplicate_and_keeps_later_editor(ui,before_send):
    from playwright.sync_api import expect
    page,store,*_=ui;cam={'n':2**53+1,'float':1.0,'zeros':[-0.0],'large':[10**400],
                         'private_note':'numeric-engineering-not-cached'}
    later={'n':2**53+3,'float':2.0}
    page.locator('#new').click();page.locator('#kind').select_option('gauge')
    page.locator('#name').fill('Numeric unreliable write');cam_text(page,cam)
    lose(page,'/api/v1/resources',before_send)
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#save-request-key').inner_text();sent=page.evaluate('window.__sentBody')
    assert canonical(json.loads(sent)['cam'])==canonical(cam)
    ticket=page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")')
    assert 'numeric-engineering-not-cached' not in ticket and '"cam"' not in ticket and 'isolated-requests-browser-' not in ticket
    cam_text(page,later);page.locator('#name').fill('Later numeric editor')
    page.evaluate("""()=>{const original=window.fetch;window.__repeatBodies=[];window.fetch=(...args)=>{
      if(String(args[0]).endsWith('/api/v1/resources')&&args[1]?.method==='POST')window.__repeatBodies.push([args[1].headers['Idempotency-Key'],args[1].body]);return original(...args);};}""")
    page.locator('#save-repeat').click();expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#name')).to_have_value('Later numeric editor')
    assert canonical(json.loads(page.locator('#cam').input_value()))==canonical(later)
    assert page.evaluate('window.__repeatBodies')==[[key,sent]]
    saved=next(item for item in store.resources() if item['name']=='Numeric unreliable write')
    assert saved['revision']==1 and canonical(saved['cam'])==canonical(cam) and len(store.resources())==2
    assert canonical(json.loads(page.evaluate('NXData.stringifyWire(editing.cam)')))==canonical(cam)
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text(' / Rev 2.')
    saved=next(item for item in store.resources() if item['id']==saved['id'])
    assert saved['name']=='Later numeric editor' and canonical(saved['cam'])==canonical(later)


def test_later_exact_integer_edit_is_not_erased_when_both_numbers_round_to_same_js_number(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;first={'n':2**53};later={'n':2**53+1}
    page.locator('#new').click();page.locator('#kind').select_option('gauge')
    page.locator('#name').fill('Same JS double, different JSON integer');cam_text(page,first)
    hold(page,'/api/v1/resources');track(page,'save');page.locator('#save').click()
    page.wait_for_function('()=>window.__held');cam_text(page,later);release(page)
    expect(page.locator('#message')).to_contain_text('Spätere Editoränderungen bleiben ungespeichert')
    assert canonical(json.loads(page.locator('#cam').input_value()))==canonical(later)
    saved=next(item for item in store.resources() if item['name']=='Same JS double, different JSON integer')
    assert saved['revision']==1 and canonical(saved['cam'])==canonical(first)
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text(' / Rev 2.')
    assert canonical(next(item for item in store.resources() if item['id']==saved['id'])['cam'])==canonical(later)


@pytest.mark.parametrize('recovery',['receipt','cancel'])
def test_committed_numeric_write_receipt_keeps_exact_resource_evidence(ui,recovery):
    from playwright.sync_api import expect
    page,store,*_=ui;cam={'n':2**53+1,'float':1.0,'large':10**400}
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#new').click();page.locator('#kind').select_option('gauge')
    page.locator('#name').fill('Numeric receipt');cam_text(page,cam)
    lose(page,'/api/v1/resources');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    saved=next(item for item in store.resources() if item['name']=='Numeric receipt');audit=store.audit_entries()
    page.locator('#name').fill('Later editor survives the receipt')
    page.locator('#save-'+recovery).click();expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#name')).to_have_value('Later editor survives the receipt')
    assert canonical(json.loads(page.evaluate('NXData.stringifyWire(editing.cam)')))==canonical(cam)
    assert saved['revision']==1 and canonical(saved['cam'])==canonical(cam)
    assert len(store.resources())==2 and store.audit_entries()==audit


@pytest.mark.parametrize('raw',[
    '{"private":"numeric-input-not-for-error","n":1e400}',
    '{"private":"numeric-input-not-for-error","n":1e-400}',
    '{"private":"numeric-input-not-for-error","n":1,"n":2}',
    '{"private":"numeric-input-not-for-error","n":NaN}',
])
def test_invalid_or_out_of_float_range_editor_json_never_starts_a_write(ui,raw):
    from playwright.sync_api import expect
    page,store,*_=ui;before=store.audit_entries();attempts=[]
    page.on('request',lambda request:attempts.append(request.url) if '/api/v1/resources' in request.url and request.method in ('POST','PUT') else None)
    page.locator('#new').click();page.locator('#kind').select_option('gauge');page.locator('#name').fill('Invalid numeric input')
    page.locator('#editor-section details.technical').evaluate('(node)=>node.open=true');page.locator('#cam').fill(raw)
    track(page,'save');page.locator('#save').click();page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#message')).to_have_class('error');expect(page.locator('#message')).not_to_contain_text('numeric-input-not-for-error')
    expect(page.locator('#save')).to_be_enabled()
    assert not attempts and len(store.resources())==1 and store.audit_entries()==before
    assert page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")') is None
