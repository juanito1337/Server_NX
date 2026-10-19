"""Selective catalog GUI, separate synthetic folders/processes; no actual NX."""
import json
import os

import pytest

from nxserver.libraries import SPECS
from test_catalog_selection import database
from test_libraries import write
from test_filepackages_browser import package_ui
from test_filepackages import packages
from test_requests_browser import hold,track,release as release_reply
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated selective catalog Edge tests.')


@pytest.fixture
def selection_ui(package_ui):
    from playwright.sync_api import expect
    page,store,previous,source=package_ui
    standard=store.standards['2512']
    write(source,SPECS['holder-metric']['path'],database(['STD',*[f'KEY_{index:04}' for index in range(505)]]))
    write(standard,SPECS['holder-metric']['path'],database(['STD']))
    write(source,SPECS['tool-metric']['path'],database(['STD','A','B','__proto__']))
    write(standard,SPECS['tool-metric']['path'],database(['STD']))
    target=store.import_release('test','2512','Synthetic selected catalog','fixture')
    page.locator('#refresh').click()
    expect(page.locator('#library-release option[value="'+target['id']+'"]')).to_have_count(1)
    page.locator('#library-release').select_option(target['id']);page.locator('#library-inventory').click()
    expect(page.locator('#catalog-libraries input')).to_have_count(2)
    page.locator('#catalog-scope').select_option('objects')
    yield page,store,target,previous,source


def read(page,library,query=''):
    from playwright.sync_api import expect
    page.locator('#native-library').select_option(library);page.locator('#library-query').fill(query)
    page.locator('#library-read').click();expect(page.locator('#library-result-state')).to_contain_text('ausgewählte DAT-Datei SHA-geprüft')


def select(page,library,key):
    page.get_by_role('checkbox',name='Objekt '+library+' / '+key+' auswählen',exact=True).check()


def test_explicit_multilibrary_multpage_groups_remain_selected_and_import_only_three(selection_ui):
    from playwright.sync_api import expect
    page,store,target,previous,source=selection_ui;before=store.manifest(store.release_folder(target['id']))
    read(page,'holder-metric');select(page,'holder-metric','KEY_0000')
    page.locator('#library-next').click();expect(page.locator('#library-result-state')).to_contain_text('101–200')
    select(page,'holder-metric','KEY_0104')
    read(page,'tool-metric','__proto__');select(page,'tool-metric','__proto__')
    expect(page.locator('#catalog-selection-state')).to_contain_text('3 vollständige Objekte')
    page.locator('#tab-prepare').click();page.locator('#name').fill('Unsaved editor remains after selective adoption')
    page.locator('#tab-manage').click();page.locator('#catalog-preview').click()
    expect(page.locator('#catalog-import')).to_be_enabled();expect(page.locator('#catalog-objects > tr')).to_have_count(3)
    proposal=json.loads(page.locator('#details').inner_text())
    assert proposal['selection']=={'holder-metric':['KEY_0000','KEY_0104'],'tool-metric':['__proto__']}
    page.locator('#catalog-import').click();expect(page.locator('#message')).to_contain_text('Stammdaten übernommen: 3',timeout=15000)
    assert len(store.resources())==3 and all(len(item['cam']['records'])==2 for item in store.resources())
    assert store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved editor remains after selective adoption')
    page.locator('#tab-manage').click();page.locator('#catalog-preview').click()
    expect(page.locator('#catalog-state')).to_contain_text('3 bereits verwaltet');expect(page.locator('#catalog-import')).to_be_disabled()
    page.locator('#catalog-objects > tr').first.get_by_role('button',name='Stammdaten öffnen').click()
    expect(page.locator('#panel-prepare')).to_be_visible();expect(page.locator('#native-fields input')).to_have_count(8)


def test_empty_selection_never_imports_all_and_page_selection_limit_is_atomic(selection_ui):
    from playwright.sync_api import expect
    page,store,target,previous,source=selection_ui
    page.locator('#catalog-preview').click();expect(page.locator('#message')).to_contain_text('Mindestens ein vollständiges Objekt')
    expect(page.locator('#catalog-import')).to_be_disabled();assert store.resources()==[]
    read(page,'holder-metric')
    for number in range(5):
        page.locator('#catalog-select-page').click();expect(page.locator('#catalog-selection-state')).to_contain_text(str((number+1)*100)+' vollständige Objekte')
        page.locator('#library-next').click();expect(page.locator('#library-result-state')).to_contain_text(str((number+1)*100+1)+'–')
    page.locator('#catalog-select-page').click();expect(page.locator('#message')).to_contain_text('Grenze von 500')
    expect(page.locator('#catalog-selection-state')).to_contain_text('500 vollständige Objekte')
    assert page.locator('#native-objects input:checked').count()==0
    page.locator('#include-standard').check();page.locator('#catalog-preview').click()
    expect(page.locator('#catalog-import')).to_be_enabled();expect(page.locator('#catalog-objects > tr')).to_have_count(100)
    expect(page.locator('#catalog-page-state')).to_contain_text('1–100 von 500')
    page.locator('#catalog-objects-next').click();expect(page.locator('#catalog-page-state')).to_contain_text('101–200 von 500')
    expect(page.locator('#catalog-objects > tr')).to_have_count(100)
    assert store.resources()==[]
    page.locator('#catalog-clear-selection').click();expect(page.locator('#catalog-selection-state')).to_contain_text('0 vollständige Objekte')
    expect(page.locator('#catalog-import')).to_be_disabled()


@pytest.mark.parametrize('old_error',[False,True])
def test_changed_selection_even_away_and_back_discards_old_preview(selection_ui,old_error):
    from playwright.sync_api import expect
    page,store,target,previous,source=selection_ui;read(page,'tool-metric','A');select(page,'tool-metric','A')
    hold(page,'/api/v1/releases/'+target['id']+'/catalog-preview',error=old_error);track(page,'catalog-preview')
    page.locator('#catalog-preview').click();page.wait_for_function('()=>window.__held')
    choice=page.get_by_role('checkbox',name='Objekt tool-metric / A auswählen',exact=True)
    choice.uncheck();choice.check();release_reply(page)
    expect(page.locator('#catalog-import')).to_be_disabled();expect(page.locator('#catalog-objects > tr')).to_have_count(0)
    expect(page.locator('#catalog-state')).to_contain_text('Stammdatenvorschau neu prüfen')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.resources()==[]


def test_lost_selected_acceptance_reload_keeps_exact_server_selection_not_new_choice(selection_ui):
    from playwright.sync_api import expect
    from test_management import ADMIN
    page,store,target,previous,source=selection_ui;read(page,'tool-metric','__proto__');select(page,'tool-metric','__proto__')
    page.locator('#catalog-preview').click();expect(page.locator('#catalog-import')).to_be_enabled()
    lose(page,'/api/v1/release-jobs/catalog');page.locator('#catalog-import').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    hint=json.loads(page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")'))
    assert set(hint)=={'contract_version','key','mode','created_at'} and '__proto__' not in str(hint)
    page.reload();page.locator('#token').fill(ADMIN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');expect(page.locator('#catalog-selection-state')).to_contain_text('0 vollständige Objekte')
    page.locator('#release-job-receipt').click();expect(page.locator('#message')).to_contain_text('Stammdaten übernommen: 1',timeout=15000)
    assert len(store.resources())==1 and store.resources()[0]['origin']['key']=='__proto__'
    with store.db() as db:jobs=[json.loads(row[0]) for row in db.execute('SELECT payload FROM migration_jobs')]
    assert len(jobs)==1 and jobs[0]['request']['selection']=={'tool-metric':['__proto__']}
    assert store.active()['release_id'] is None


def test_source_change_clears_old_selection_without_whole_library_fallback(selection_ui):
    from playwright.sync_api import expect
    page,store,target,previous,source=selection_ui;read(page,'tool-metric','A');select(page,'tool-metric','A')
    page.locator('#catalog-preview').click();expect(page.locator('#catalog-import')).to_be_enabled()
    page.locator('#library-release').select_option(previous['id'])
    expect(page.locator('#catalog-selection-state')).to_contain_text('0 vollständige Objekte')
    expect(page.locator('#catalog-scope')).to_have_value('objects');expect(page.locator('#catalog-import')).to_be_disabled()
    page.locator('#catalog-preview').click();expect(page.locator('#message')).to_contain_text('Mindestens ein vollständiges Objekt')
    assert store.resources()==[]
