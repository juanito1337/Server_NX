"""GUI refresh of a legacy launcher, including a blocked release, no actual NX."""
import hashlib
import json
import os

import pytest

from test_requests_browser import ui,hold,track,release as release_reply

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge launcher refresh.')


def old_launcher(store,target,monkeypatch):
    import nxserver.store as module
    original=module.package_files
    def legacy(*args):
        files,config=original(*args);files['Start-NX2512ManagedTest.ps1']+=b'\n# Synthetic previous revision\n'
        config['ScriptHashes']['Start-NX2512ManagedTest.ps1']=hashlib.sha256(files['Start-NX2512ManagedTest.ps1']).hexdigest()
        files['release_test.json']=json.dumps(config).encode();return files,config
    with monkeypatch.context() as patch:
        patch.setattr(module,'package_files',legacy);patch.setattr(module,'template_sha256',lambda:'f'*64)
        previous=store.prepare_test_launcher(target['id'],'fixture');path=store.test_launcher_package(target['id']);data=path.read_bytes()
    return previous,path,data


@pytest.mark.parametrize('blocked',[False,True])
def test_gui_explicit_refresh_preserves_legacy_package_source_and_role_state(ui,monkeypatch,blocked):
    from playwright.sync_api import expect
    from nxserver.testlaunch import template_sha256
    page,store,target,*_=ui;previous,path,data=old_launcher(store,target,monkeypatch)
    if blocked:
        with store.db() as db:
            db.execute("UPDATE releases SET status='blocked' WHERE id=?",(target['id'],));store.audit(db,'fixture','release.validated',{'id':target['id'],'synthetic_blocked':True})
    manifest=store.manifest(store.release_folder(target['id']));page.locator('#refresh').click()
    row=page.locator('#releases tr').filter(has_text=target['id']);row.get_by_role('button',name='Test-Starter aktualisieren',exact=True).click()
    expect(page.locator('#message')).to_contain_text('Test-Starter geprüft/aktualisiert')
    current=store.get_release(target['id'])['test_launcher'];assert current['template_sha256']==template_sha256()
    assert current['package_revision']!=previous['package_revision'] and path.read_bytes()==data
    assert current.get('ui_profile') is None and store.manifest(store.release_folder(target['id']))==manifest and store.active()['release_id'] is None
    assert store.get_release(target['id'])['status']==('blocked' if blocked else 'draft')
