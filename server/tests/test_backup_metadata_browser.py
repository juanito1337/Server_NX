"""One corrupt local receipt must not disable all other recovery choices."""
import os

import pytest

from nxserver.backups import Backups
from test_requests_browser import ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge backup metadata workflow.')


@pytest.mark.parametrize('defect',['null','duplicate_status'])
def test_corrupt_receipt_is_visible_without_actions_and_valid_snapshot_still_previews(ui,defect):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept())
    for count in (1,2):
        page.locator('#backup-create').click()
        expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)
        expect(page.locator('#backup-snapshots tr')).to_have_count(count)
    manager=Backups(store);receipts=manager.list();assert len(receipts)==2
    broken,good=receipts;path=manager._folder(broken['id'])/'receipt.json';raw=path.read_bytes()
    raw=b'null' if defect=='null' else b'{"status":"invalid",'+raw[1:]
    path.write_bytes(raw);resources=store.resources();history=store.audit_entries()
    revisions={resource['id']:store.history(resource['id']) for resource in resources}
    page.locator('#backup-refresh').click()
    invalid=page.locator('#backup-snapshots tr').filter(has_text='Ungültiger Beleg')
    expect(invalid).to_have_count(1)
    expect(invalid.get_by_role('button')).to_have_count(0)
    choices=page.locator('#recovery-backup option').evaluate_all('(items)=>items.map(item=>item.value).filter(Boolean)')
    assert choices==[good['id']]
    page.locator('#recovery-backup').select_option(good['id'])
    page.locator('#recovery-name').fill('OnlyValidSnapshot')
    page.locator('#recovery-preview').click()
    expect(page.locator('#recovery-state')).to_contain_text('Geprüftes neues Ziel',timeout=20000)
    expect(page.locator('#recovery-create')).to_be_disabled()
    assert path.read_bytes()==raw and store.resources()==resources
    # Preview adds audit entries; existing entries and resource versions stay exact.
    current={item['sequence']:item for item in store.audit_entries()}
    assert all(current[item['sequence']]==item for item in history)
    assert all(store.history(identifier)==value for identifier,value in revisions.items())
    assert not manager.restore_roots['local'].exists()
