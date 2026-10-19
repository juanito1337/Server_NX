"""GUI refresh/download and real PowerShell preflight; never launches NX."""
import hashlib
import io
import json
import os
import subprocess
import zipfile

import pytest

from nxserver.safety import offline_path
from test_requests_browser import ui
from test_testlaunch import POWERSHELL
from installation_version_test_support import write_nx_image

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1' or os.name!='nt' or not POWERSHELL,
    reason='Opt-in isolated Edge/native starter preflight; never actual NX.')


def test_gui_refresh_delivers_client_version_guard_without_rewriting_old_archive(ui,tmp_path,monkeypatch):
    import nxserver.store as module
    from nxserver.testlaunch import template_sha256
    from playwright.sync_api import expect
    page,store,release,*_=ui;generate=module.package_files
    def legacy(*args,**kwargs):
        files,config=generate(*args,**kwargs)
        # Own simulated old archive: remove invocations, never edit a user archive.
        for name in ('Invoke-NXReleaseTest.ps1','New-NX2512TestEnvironment.ps1','Start-NX2512ManagedTest.ps1'):
            lines=files[name].splitlines(keepends=True)
            retained=[line for line in lines if not any(text in line for text in
                (b'Get-NXInstallationVersionMetadata',b'NX installation metadata:',b'$latestInstallationMetadata.Sha256'))]
            files[name]=b''.join(retained)
            assert files[name]!=b''.join(lines)
            config['ScriptHashes'][name]=hashlib.sha256(files[name]).hexdigest()
        files['release_test.json']=json.dumps(config).encode()
        return files,config
    with monkeypatch.context() as historical:
        historical.setattr(module,'package_files',legacy);historical.setattr(module,'template_sha256',lambda:'a'*64)
        previous=store.prepare_test_launcher(release['id'],'fixture')
    old_path=store.test_launcher_package(release['id']);old_archive=old_path.read_bytes()
    before=store.manifest(store.release_folder(release['id']))
    with zipfile.ZipFile(io.BytesIO(old_archive)) as old:
        assert b'Get-NXInstallationVersionMetadata' not in old.read('Start-NX2512ManagedTest.ps1')
    page.locator('#refresh').click();row=page.locator('#releases tr').filter(has_text=release['id'])
    row.get_by_role('button',name='Test-Starter aktualisieren',exact=True).click()
    expect(page.locator('#message')).to_contain_text('Test-Starter geprüft/aktualisiert')
    current=store.get_release(release['id'])['test_launcher']
    assert current['template_sha256']==template_sha256() and current['package_revision']!=previous['package_revision']
    with page.expect_download() as downloaded:row.get_by_role('button',name='Test-Starter herunterladen',exact=True).click()
    extracted=tmp_path/'downloaded';extracted.mkdir()
    with zipfile.ZipFile(downloaded.value.path()) as archive:
        config=json.loads(archive.read('release_test.json'))
        for name,sha in config['ScriptHashes'].items():assert hashlib.sha256(archive.read(name)).hexdigest()==sha
        for name in ('Invoke-NXReleaseTest.ps1','New-NX2512TestEnvironment.ps1','Start-NX2512ManagedTest.ps1'):
            assert b'Get-NXInstallationVersionMetadata' in archive.read(name)
        assert b'function Get-NXInstallationVersionMetadata' in archive.read('NXOfflineSafety.psm1')
        for info in archive.infolist():
            assert '/' not in info.filename and '\\' not in info.filename
            (extracted/info.filename).write_bytes(archive.read(info))
    install=tmp_path/'NX2512';write_nx_image(install,'2406')
    palette=install/'MACH/localization/german/ugs_manufacturing_templates_german.pax'
    palette.parent.mkdir(parents=True);palette.write_text('<Palette xmlns="urn:test"><PaletteEntry id="STD"/></Palette>',encoding='utf-8')
    runtime=tmp_path/'runtime'
    arguments=[str(offline_path(POWERSHELL)),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
        str(extracted/'Invoke-NXReleaseTest.ps1'),'-PackagePath',str(extracted/'release_test.json'),
        '-NXInstallRoot',str(install),'-TestRoot',str(runtime),'-CheckOnly']
    bad=subprocess.run(arguments,capture_output=True,text=True,timeout=60)
    assert bad.returncode!=0 and 'version metadata' in bad.stderr and not runtime.exists(),bad.stdout+bad.stderr
    write_nx_image(install,'2512')
    good=subprocess.run(arguments,capture_output=True,text=True,timeout=60)
    assert good.returncode==0,good.stdout+good.stderr
    assert 'Metadata only; no signature, license or runtime approval.' in good.stdout
    assert 'NX not started' in good.stdout and not (runtime/'temp').exists() and not (runtime/'user').exists()
    assert old_path.read_bytes()==old_archive and store.manifest(store.release_folder(release['id']))==before
    assert store.active()['release_id'] is None
