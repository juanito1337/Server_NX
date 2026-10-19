"""PAX technical findings in the export GUI, synthetic fixtures, no actual NX."""
import json
import os

import pytest

from test_requests_browser import ui,hold,track,release as release_reply
from test_preview_context_browser import open_export
from test_template_refs import package

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated PAX dependency display.')


def prepare(ui,reference='PART.prt',image=False):
    from playwright.sync_api import expect
    page,store,target,*_=ui;resource=store.save_resource(package(store,reference,image=image),'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    open_export(page,target);return page,store,target,resource


@pytest.mark.parametrize('reference',['MISSING.prt',r'W:\SiemensShare\nx2406\do-not-read.prt','${UNKNOWN}/PART.prt','@DB/ITEM/0'])
def test_missing_or_unsupported_pax_reference_is_visible_and_blocks_new_export(ui,reference):
    from playwright.sync_api import expect
    page,store,target,resource=prepare(ui,reference);audit=store.audit_entries();page.locator('#export-preview').click()
    expect(page.locator('#export-conflicts')).to_contain_text('CUSTOM.pax');expect(page.locator('#export-create')).to_be_disabled()
    page.locator('#export-pax-box').evaluate('(element)=>{element.open=true;}')
    expect(page.locator('#export-pax-items')).to_contain_text(reference)
    expect(page.locator('#export-pax-state')).to_contain_text('1 Konflikte')
    assert len(store.releases())==1 and store.audit_entries()==audit


def test_valid_pax_and_model_have_explicit_technical_findings_without_automatic_nx_approval(ui):
    from playwright.sync_api import expect
    page,store,target,resource=prepare(ui,image=True);audit=store.audit_entries();page.locator('#export-preview').click()
    expect(page.locator('#export-create')).to_be_enabled();page.locator('#export-pax-box').evaluate('(element)=>{element.open=true;}')
    expect(page.locator('#export-pax-items > tr')).to_have_count(2)
    expect(page.locator('#export-pax-items')).to_contain_text('UGII/templates/PART.prt')
    expect(page.locator('#export-pax-state')).to_contain_text('Kein NX-/Geometrie-/UI-Nachweis')
    assert len(store.releases())==1 and store.audit_entries()==audit
    page.locator('#export-close').click();expect(page.locator('#export-pax-items > tr')).to_have_count(0)


@pytest.mark.parametrize('field',['template_references','path_rewrites','hash'])
def test_malformed_pax_contract_does_not_enable_export_or_render_partial_success(ui,field):
    from playwright.sync_api import expect
    page,store,target,resource=prepare(ui,image=True)
    def bad(route):
        response=route.fetch();value=response.json()
        if field=='template_references':value.pop(field)
        elif field=='path_rewrites':value['template_references'][field]=True
        else:value['template_references']['references'][-1]['sha256']='invalid'
        route.fulfill(response=response,json=value)
    page.route('**/derive-preview',bad);page.locator('#export-preview').click()
    expect(page.locator('#message')).to_contain_text('Kein vollständiger gebundener PAX-Verweisvertrag')
    expect(page.locator('#export-create')).to_be_disabled();expect(page.locator('#export-pax-items > tr')).to_have_count(0)
    assert len(store.releases())==1


def test_new_spaltenwerte_aba_discards_late_pax_findings(ui):
    from playwright.sync_api import expect
    page,store,target,resource=prepare(ui);hold(page,'/derive-preview');track(page,'export-preview')
    page.locator('#export-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#export-defaults').fill('{');page.locator('#export-defaults').fill('{}');release_reply(page)
    expect(page.locator('#export-create')).to_be_disabled();expect(page.locator('#export-pax-items > tr')).to_have_count(0)
    assert len(store.releases())==1


def test_pax_findings_are_paged_without_losing_declared_references(ui):
    from playwright.sync_api import expect
    page,store,target,*_=ui;value=package(store)
    declaration='<Palette xmlns="urn:test">'+''.join('<PaletteEntry id="E'+str(index)+'"><ObjectData><Filename>PART.prt</Filename></ObjectData></PaletteEntry>' for index in range(53))+'</Palette>'
    asset=store.add_asset('CUSTOM.pax',declaration.encode(),'fixture');value['asset_ids'][0]=asset['id'];value['file_package']['entries'][0]['asset_id']=asset['id'];value['file_package']['asset_hashes']={identifier:store.asset(identifier)[0]['sha256'] for identifier in value['asset_ids']}
    resource=store.save_resource(value,'fixture');page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name']);open_export(page,target)
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled();page.locator('#export-pax-box').evaluate('(element)=>{element.open=true;}')
    expect(page.locator('#export-pax-items > tr')).to_have_count(50);page.locator('#export-pax-next').click()
    expect(page.locator('#export-pax-items > tr')).to_have_count(3);expect(page.locator('#export-pax-page')).to_contain_text('51–53 von 53')
    page.locator('#export-pax-prev').click();expect(page.locator('#export-pax-items > tr')).to_have_count(50)
    assert len(store.releases())==1 and store.active()['release_id'] is None
