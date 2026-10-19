"""Isolated whole-folder GUI upgrades and lost replies; no executable NX/installer."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nxserver.store import Store
from test_libraries import write,simple
from test_upgrades import installation
from test_saves_browser import lose
from test_requests_browser import hold,track,release

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge upgrade recovery.')
TOKEN='isolated-upgrade-recovery-'+'u'*40


@pytest.fixture
def wizard(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright,expect
    old,new,source,output=[tmp_path/name for name in ('old','new','source','out')]
    sidt_old,sidt_new=tmp_path/'so',tmp_path/'sn'
    installation(old,sidt_old,'2406');installation(new,sidt_new,'2512')
    # Use the real adapter path, but strictly synthetic table/model bytes.
    from nxserver.libraries import SPECS
    relative=SPECS['holder-metric']['path']
    write(old,relative,simple(descr='Old stock'));write(new,relative,simple(descr='New stock'))
    write(source,relative,simple(descr='Old stock').replace('#END_DATA','DATA |CUSTOM|Customer\n#END_DATA'))
    write(source,'MACH/custom/customer.txt','Synthetic customization');output.mkdir()
    store=Store(tmp_path/'m',{'test':source},standards={'2406':old,'2512':new},sidts={'2406':sidt_old,'2512':sidt_new},target_roots={'output':output})
    settings={'data_dir':str(store.root),'sources':{'test':str(source)},'standards':{'2406':str(old),'2512':str(new)},
        'sidts':{'2406':str(sidt_old),'2512':str(sidt_new)},'target_roots':{'output':str(output)}}
    settings_path=tmp_path/'settings.json';settings_path.write_text(json.dumps(settings),encoding='utf-8')
    environment=dict(os.environ)
    for name in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(name,None)
    environment['NX_ADMIN_TOKEN']=TOKEN
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(settings_path),'--port',str(port)],
        cwd=Path(__file__).resolve().parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:urllib.request.urlopen(base+'/health',timeout=1).close();break
            except (urllib.error.URLError,TimeoutError):
                if process.poll() is not None:raise AssertionError('Isolated upgrade server ended')
                time.sleep(.1)
        else:raise AssertionError('Isolated upgrade server not ready')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)));page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base);page.locator('#token').fill(TOKEN);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-update').click()
                page.locator('#source').select_option('test');page.locator('#source-path').fill(str(source))
                page.locator('#target').select_option('output');page.locator('#target-path').fill(str(output/'nx'))
                page.locator('#release-name').fill('Synthetic recovery upgrade')
                yield page,store,source,output/'nx'
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def jobs(store):
    with store.db() as db:return [json.loads(row[0]) for row in db.execute('SELECT payload FROM migration_jobs')]


def test_live_reader_overrides_terminal_ui_and_blocks_both_workspaces(wizard):
    from playwright.sync_api import expect
    from nxserver.store import canonical
    page,store,source,target=wizard
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    original=jobs(store)[0];identifier=original['id']
    # Own synthetic server's real lifetime is used as a known-live reader.
    # No process is stopped or launched, no production path is involved.
    original.update(child_pid=original['owner_pid'],child_stamp=original['owner_stamp'])
    with store.db() as db:db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(original),identifier))
    page.locator('#upgrade-jobs-refresh').click()
    expect(page.locator('#upgrade-jobs')).to_contain_text('Unterprozess aktiv')
    expect(page.locator('#upgrade-jobs')).to_contain_text('Gespeicherter Status: completed')
    expect(page.locator('#upgrade-preview')).to_be_disabled()
    expect(page.locator('#upgrade-build')).to_be_disabled()
    page.locator('#tab-manage').click()
    page.locator('#release-job-refresh').click()
    assert page.evaluate('NXReleaseJobs.blocked()')
    assert jobs(store)[0]['status']=='completed' and not target.exists()
    original.update(child_pid=None,child_stamp=None)
    with store.db() as db:db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(original),identifier))
    page.locator('#release-job-refresh').click()
    page.locator('#tab-update').click();page.locator('#upgrade-jobs-refresh').click()
    expect(page.locator('#upgrade-preview')).to_be_enabled()
    expect(page.locator('#upgrade-jobs')).not_to_contain_text('Unterprozess aktiv')
    assert len(jobs(store))==1 and not target.exists() and store.active()['release_id'] is None


def test_lost_preview_retries_same_acceptance_and_does_not_apply_to_changed_inputs(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;lose(page,'/api/v1/migrations/preview')
    page.locator('#upgrade-preview').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#upgrade-request-key').inner_text();expect(page.locator('#upgrade-preview')).to_be_disabled()
    page.locator('#release-name').fill('Changed after submitting');page.locator('#import-defaults').fill('{')
    page.locator('#upgrade-repeat').click();expect(page.locator('#upgrade-recovery-box')).to_be_hidden()
    expect(page.locator('#upgrade-state')).to_contain_text('Auswahl passt nicht mehr',timeout=15000)
    expect(page.locator('#upgrade-build')).to_be_disabled();expect(page.locator('#release-name')).to_have_value('Changed after submitting')
    expect(page.locator('#import-defaults')).to_have_value('{')
    assert len(jobs(store))==1 and not target.exists() and not store.releases()
    assert page.evaluate('window.__sentKey')==key


@pytest.mark.parametrize('recover_action',['receipt','cancel'])
def test_lost_build_after_reload_recovers_original_target_not_another_build(wizard,recover_action):
    from playwright.sync_api import expect
    page,store,source,target=wizard;before=store.manifest(source)
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    lose(page,'/api/v1/migrations/build');page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-migration-request.v1")');ticket=json.loads(raw)
    assert set(ticket)=={'contract_version','key','path','method','created_at'}
    assert str(source) not in raw and TOKEN not in raw and 'Synthetic recovery upgrade' not in raw
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-update').click()
    expect(page.locator('#upgrade-repeat')).to_be_disabled();expect(page.locator('#upgrade-preview')).to_be_disabled()
    page.locator('#release-name').fill('New unsubmitted wizard name')
    page.locator('#upgrade-'+recover_action).click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt',timeout=15000)
    expect(page.locator('#release-name')).to_have_value('New unsubmitted wizard name')
    assert len(jobs(store))==2 and len(store.releases())==1 and target.is_dir()
    assert store.manifest(source)==before and (target/'MACH/custom/customer.txt').read_text()=='Synthetic customization'
    assert store.active()['release_id'] is None and store.active()['generation']==0
    assert page.evaluate('sessionStorage.getItem("nx.pending-migration-request.v1")') is None


def test_never_sent_start_404_is_not_stop_proof_and_fence_blocks_delayed_request(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;lose(page,'/api/v1/migrations/preview',before_send=True)
    page.locator('#upgrade-preview').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#upgrade-request-key').inner_text();payload=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#upgrade-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still be accepted')
    expect(page.locator('#upgrade-preview')).to_be_disabled();assert jobs(store)==[]
    page.locator('#upgrade-cancel').click();expect(page.locator('#upgrade-preview')).to_be_enabled()
    status=page.evaluate("""async([key,payload,token])=>{const response=await fetch('/api/v1/migrations/preview',{
      method:'POST',headers:{Authorization:'Bearer '+token,'Idempotency-Key':key,'Content-Type':'application/json'},body:JSON.stringify(payload)});return response.status;}""",[key,payload,TOKEN])
    assert status==409 and jobs(store)==[] and not target.exists()
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    assert len(jobs(store))==1


def test_old_acceptance_reply_after_logout_cannot_replace_new_wizard(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;hold(page,'/api/v1/migrations/preview');track(page,'upgrade-preview')
    page.locator('#upgrade-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-update').click()
    page.locator('#release-name').fill('Unsubmitted new connection')
    page.locator('#upgrade-receipt').click();expect(page.locator('#upgrade-recovery-box')).to_be_hidden()
    expect(page.locator('#upgrade-state')).to_contain_text('Auswahl passt nicht mehr',timeout=15000)
    release(page)
    expect(page.locator('#release-name')).to_have_value('Unsubmitted new connection');expect(page.locator('#upgrade-build')).to_be_disabled()
    assert len(jobs(store))==1 and not target.exists()


def test_definite_validation_rejection_is_fenced_before_corrected_start(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;page.locator('#nxversion').fill('26')
    page.locator('#upgrade-preview').click();expect(page.locator('#message')).to_contain_text('pattern')
    expect(page.locator('#upgrade-preview')).to_be_enabled();expect(page.locator('#upgrade-recovery-box')).to_be_hidden()
    assert jobs(store)==[]
    with store.db() as db:assert db.execute('SELECT state FROM migration_requests').fetchone()[0]=='cancelled'
    page.locator('#nxversion').fill('2512');page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000);assert len(jobs(store))==1
