"""Explicitly refresh a legacy unsafe PATH binding without rewriting its archive."""
import hashlib
import io
import json
import os
import zipfile

import pytest

from test_requests_browser import ui

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated Edge starter PATH refresh; never real NX.')


def test_gui_refresh_downloads_guarded_binding_and_preserves_old_archive(ui, monkeypatch):
    import nxserver.store as module
    from nxserver.testlaunch import template_sha256
    from playwright.sync_api import expect
    page, store, release, *_ = ui
    generate = module.package_files
    def legacy(*args, **kwargs):
        files, config = generate(*args, **kwargs)
        name = 'Start-NX2512ManagedTest.ps1'
        current = files[name]
        old = current.replace(b'    PATH = $nxSearchPath', b'    PATH = "$install\\NXBIN;$install\\UGII;$env:PATH"')
        assert old != current
        files[name] = old
        config['ScriptHashes'][name] = hashlib.sha256(old).hexdigest()
        files['release_test.json'] = json.dumps(config).encode('utf-8')
        return files, config
    with monkeypatch.context() as historical:
        historical.setattr(module, 'package_files', legacy)
        historical.setattr(module, 'template_sha256', lambda: 'e' * 64)
        previous = store.prepare_test_launcher(release['id'], 'fixture')
    archive = store.test_launcher_package(release['id'])
    before_archive = archive.read_bytes()
    before_manifest = store.manifest(store.release_folder(release['id']))
    with zipfile.ZipFile(io.BytesIO(before_archive)) as old:
        assert b'PATH = "$install\\NXBIN;$install\\UGII;$env:PATH"' in old.read('Start-NX2512ManagedTest.ps1')
    page.locator('#refresh').click()
    row = page.locator('#releases tr').filter(has_text=release['id'])
    row.get_by_role('button', name='Test-Starter aktualisieren', exact=True).click()
    expect(page.locator('#message')).to_contain_text('Test-Starter geprüft/aktualisiert')
    current = store.get_release(release['id'])['test_launcher']
    assert current['template_sha256'] == template_sha256() and current['package_revision'] != previous['package_revision']
    with page.expect_download() as download:
        row.get_by_role('button', name='Test-Starter herunterladen', exact=True).click()
    assert download.value.suggested_filename.endswith('.zip')
    with zipfile.ZipFile(download.value.path()) as new:
        config = json.loads(new.read('release_test.json'))
        for name in ('NXOfflineSafety.psm1', 'Start-NX2512ManagedTest.ps1'):
            assert hashlib.sha256(new.read(name)).hexdigest() == config['ScriptHashes'][name]
        assert b'function Get-NXOfflineSearchPath' in new.read('NXOfflineSafety.psm1')
        assert b'without PATH separators or quotes' in new.read('NXOfflineSafety.psm1')
        assert b'PATH = $nxSearchPath' in new.read('Start-NX2512ManagedTest.ps1')
    assert archive.read_bytes() == before_archive
    assert store.manifest(store.release_folder(release['id'])) == before_manifest and store.active()['release_id'] is None
