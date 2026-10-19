"""Isolated GUI approval/version/race contracts; no real NX or CAM evidence."""
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

from nxserver.store import Store,TOOL_DATABASE
from test_management import DATABASE


@pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge workflow.')
def test_review_version_gate_and_late_scan_approval_logout_responses(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright,expect
    source=tmp_path/'source';database=source/TOOL_DATABASE;database.parent.mkdir(parents=True)
    database.write_text(DATABASE,encoding='utf-8');store=Store(tmp_path/'managed',{'test':source})
    first=store.import_release('test','2512','Review A','fixture');second=store.import_release('test','2512','Review B','fixture')
    initial=[store.manifest(store.release_folder(release['id'])) for release in (first,second)]
    settings=tmp_path/'settings.json';settings.write_text(json.dumps({'data_dir':str(store.root),'sources':{'test':str(source)}}),encoding='utf-8')
    environment=dict(os.environ);token='isolated-review-browser-token-'+'v'*40
    for key in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(key,None)
    environment['NX_ADMIN_TOKEN']=token
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(settings),'--port',str(port)],
        cwd=Path(__file__).resolve().parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:urllib.request.urlopen(base+'/health',timeout=1).close();break
            except (urllib.error.URLError,TimeoutError):
                if process.poll() is not None:raise AssertionError('Isolated review server ended.')
                time.sleep(.1)
        else:raise AssertionError('Isolated review server not ready.')
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base);page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                def choose(name):
                    page.locator('#releases tr').filter(has_text=name).get_by_role('button',name='Abnehmen',exact=True).click()
                    expect(page.locator('#review-name')).to_have_text(name)
                def upload(name,version,folder):
                    raw=(f'NX_VERSION = {version}\nUG_APP_MANUFACTURING\nSynthetic field\x1dvalue\n{folder}').encode('utf-8-sig')
                    page.locator('#syslog-file').set_input_files({'name':name,'mimeType':'text/plain','buffer':raw})
                    page.locator('#syslog-upload').click()
                    expect(page.locator('#syslog option:checked')).to_contain_text(name,timeout=15000)
                def confirm():
                    for key in ('tools','machines','posts','simulation','fixtures','inspection'):page.locator('#'+key).check()
                    page.locator('#evidence').fill('Synthetic browser-only confirmations; not real CAM qualification.')
                # Hold an actual server response only after its request completes.
                # The UI can continue selecting/closing while the old response is pending.
                def hold(suffix):
                    page.evaluate("""suffix=>{
                        window.__originalReviewFetch=window.fetch;
                        window.__reviewHeld=false;window.__reviewDecoded=false;
                        window.fetch=async(...args)=>{
                            const response=await window.__originalReviewFetch(...args);
                            if(String(args[0]).endsWith(suffix)){
                                window.fetch=window.__originalReviewFetch;
                                const json=response.json.bind(response);
                                response.json=async()=>{const value=await json();window.__reviewDecoded=true;return value;};
                                const observed=new WeakSet();function observeBody(body){if(!body||observed.has(body))return;observed.add(body);
                                  const cancel=body.cancel.bind(body);body.cancel=(...args)=>{window.__reviewDecoded=true;return cancel(...args);};}
                                observeBody(response.body);const clone=response.clone.bind(response);
                                response.clone=(...args)=>{const value=clone(...args);observeBody(response.body);return value;};
                                return await new Promise(resolve=>{window.__releaseReview=()=>resolve(response);window.__reviewHeld=true;});
                            }
                            return response;
                        };
                    }""",suffix)
                def release():
                    page.evaluate('window.__releaseReview()');page.wait_for_function('()=>window.__reviewDecoded')
                choose('Review A');upload('wrong-version.syslog','2406.1000',first['folder'])
                page.locator('#scan').click();expect(page.locator('#syslog-state')).to_contain_text('NX-Version: falsch')
                confirm();expect(page.locator('#approve')).to_be_disabled()
                assert store.get_release(first['id'])['status']=='draft'
                upload('candidate-a.syslog','2512.8500',first['folder'])
                hold('/scan');page.locator('#scan').click();page.wait_for_function('()=>window.__reviewHeld')
                choose('Review B');release()
                expect(page.locator('#review-name')).to_have_text('Review B')
                expect(page.locator('#syslog-state')).to_contain_text('NX_VERSION-Header erforderlich')
                expect(page.locator('#approve')).to_be_disabled()
                choose('Review A');page.locator('#scan').click()
                expect(page.locator('#syslog-state')).to_contain_text('NX-Version: passend');confirm()
                expect(page.locator('#approve')).to_be_enabled()
                hold('/review');page.locator('#approve').click();page.wait_for_function('()=>window.__reviewHeld')
                choose('Review B');release()
                expect(page.locator('#review-name')).to_have_text('Review B')
                expect(page.locator('#approve')).to_be_disabled()
                assert store.get_release(first['id'])['status']=='approved'  # Submitted action finishes, never cancelled by navigation.
                assert store.get_release(second['id'])['status']=='draft' and store.active()['release_id'] is None
                upload('candidate-b.syslog','2512.8500',second['folder'])
                hold('/scan');page.locator('#scan').click();page.wait_for_function('()=>window.__reviewHeld')
                page.locator('#disconnect').click();release()
                expect(page.locator('#work')).to_be_hidden();expect(page.locator('#review-box')).to_be_hidden()
                expect(page.locator('#message')).to_have_text('Abgemeldet.')
                expect(page.locator('#details')).to_have_text('Noch keine Prüfung ausgewählt.')
                assert [store.manifest(store.release_folder(value['id'])) for value in (first,second)]==initial
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
