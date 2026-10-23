"""Actual Edge/HTTP fixture workflows, synthetic bytes, no NX execution."""
import os
import pytest
from tools.nxlab.runtime import Server
from test_fixture_krx import fixture_store,package,FILES,ROOT

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Explicit isolated Edge suite.')


@pytest.fixture
def krx_ui(fixture_store,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    store,release,source,standard=fixture_store
    with Server(tmp_path/'http',store.root,standards={'2406':standard},sources={'local':source}) as server:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto(server.base);page.locator('#token').fill(server.token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                yield page,store,release,source
                assert not errors,errors
            finally:browser.close()


def test_hmi_krx_fixture_bind_revision_and_export_without_inventing_a_dat(krx_ui):
    from playwright.sync_api import expect
    page,store,release,source=krx_ui
    resource=store.save_resource(package(store),'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    expect(page.locator('#kind')).to_have_value('fixture');expect(page.locator('#package-version')).to_have_value('2406')
    expect(page.locator('#package-root option[value="MACH/resource/library/fixture/"]')).to_have_count(1)
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    page.locator('#package-evidence').fill('Synthetic GUI confirmations only; no physical NX/CAM acceptance.')
    for name in ('paths','dependencies','content','nx_test'):page.locator('#package-'+name).check()
    page.locator('#camstatus').select_option('approved');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=store.resources()[0]
    assert saved['cam']=={} and saved['revision']==2 and saved['file_package']['nx_version']=='2406'
    page.locator('#tab-manage').click()
    page.locator('#releases tr').filter(has_text=release['id']).get_by_role('button',name='Neuen Export ableiten',exact=True).click()
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    page.locator('#export-create').click()
    expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau',timeout=15000)
    exported=next(item for item in store.releases() if item['parent_id']==release['id'])
    assert all((store.release_folder(exported['id'])/path).read_bytes()==content for path,content in FILES.items())
    assert store.active()['release_id'] is None


def test_hmi_version_switch_retires_legacy_layout_choice_and_hash_binding(krx_ui):
    from playwright.sync_api import expect
    page,store,*_=krx_ui;store.save_resource(package(store),'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    page.locator('#package-version').fill('2512')
    expect(page.locator('#package-root option[value="MACH/resource/library/fixture/"]')).to_have_count(0)
    assert page.evaluate('Object.keys(NXFilePackages.fields().file_package.asset_hashes).length')==0
    page.locator('#package-bind').click();expect(page.locator('#message')).to_have_class('error')
    assert store.resources()[0]['revision']==1 and store.resources()[0]['cam_status']=='draft'


def test_hmi_existing_krx_fixture_adopts_a_new_draft_without_a_dat_parent(krx_ui):
    from playwright.sync_api import expect
    page,store,initial,source=krx_ui
    for path,content in FILES.items():
        file=source/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(content)
    release=store.import_release('local','2406','Synthetic customer fixture source','fixture')
    before=store.manifest(source)
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    page.locator('#file-adopt-release').select_option(release['id']);page.locator('#file-adopt-kind').select_option('fixture')
    expect(page.locator('#file-adopt-resource option').first).to_have_text('Neuer NX2406-KRX-Spannmittelentwurf')
    page.locator('#file-adopt-load').click();expect(page.locator('#file-adopt-files input')).to_have_count(len(FILES))
    for control in page.locator('#file-adopt-files input').all():control.check()
    page.locator('#file-adopt-name').fill('Customer vise from KRX')
    page.locator('#file-adopt-preview').click();expect(page.locator('#file-adopt-apply')).to_be_enabled()
    page.locator('#file-adopt-apply').click()
    expect(page.locator('#package-version')).to_have_value('2406',timeout=15000)
    expect(page.locator('#name')).to_have_value('Customer vise from KRX')
    resource=store.resources()[0]
    assert resource['cam']=={} and resource['cam_status']=='draft' and len(resource['asset_ids'])==len(FILES)
    assert store.manifest(source)==before and store.active()['release_id'] is None
