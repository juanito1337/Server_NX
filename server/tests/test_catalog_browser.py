"""A complete workspace snapshot with bounded rendering; no real ERP/NX inputs."""
import os
import uuid

import pytest

from nxserver.store import canonical
from test_requests_browser import ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated catalog pages.')


def test_catalog_pages_search_whole_snapshot_and_keep_unsaved_editor(ui):
    from playwright.sync_api import expect
    page,store,release,resource=ui
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#name').fill('Unsaved editor kept through catalog navigation')
    with store.db() as db:
        for index in range(205):
            identifier=str(uuid.uuid4());value={'kind':'gauge' if index%2==0 else 'device','name':f'PAGE_{index:04}',
                'cam':{},'cam_status':'draft' if index%3 else 'retired','asset_ids':[],'erp':None}
            stamp=f'2026-01-01T00:00:00.{index:06}+00:00';text=canonical(value)
            db.execute('INSERT INTO resources(id,revision,payload,external_key,updated_at) VALUES(?,1,?,NULL,?)',(identifier,text,stamp))
            db.execute('INSERT INTO resource_versions(id,revision,payload,actor,created_at) VALUES(?,1,?,?,?)',(identifier,text,'synthetic seed',stamp))
        store.audit(db,'fixture','fixture.catalog.seed',{'count':205})
    page.locator('#refresh').click();expect(page.locator('#summary')).to_contain_text('206 Stammdatensätze')
    page.locator('#tab-manage').click();expect(page.locator('#resources tr')).to_have_count(100)
    first=page.locator('#resources tr').all_text_contents();page.locator('#resource-next').click()
    expect(page.locator('#resource-count')).to_contain_text('Anzeige 101–200');second=page.locator('#resources tr').all_text_contents()
    assert not set(first)&set(second)
    page.locator('#resource-next').click();expect(page.locator('#resources tr')).to_have_count(6)
    expect(page.locator('#resource-next')).to_be_disabled()
    page.locator('#resource-query').fill('PAGE_0000');expect(page.locator('#resources tr')).to_have_count(1)
    expect(page.locator('#resource-count')).to_contain_text('Anzeige 1–1')
    page.locator('#resource-query').fill('');page.locator('#resource-kind').select_option('device')
    expect(page.locator('#resource-count')).to_contain_text('102 von 206')
    page.locator('#resource-next').click();expect(page.locator('#resources tr')).to_have_count(2)
    page.locator('#resource-status').select_option('retired')
    expect(page.locator('#resource-count')).to_contain_text('Anzeige 1–34')
    page.locator('#resource-kind').select_option('');page.locator('#resource-status').select_option('');page.locator('#resource-query').fill('NO_MATCH')
    expect(page.locator('#resources tr')).to_have_count(0);expect(page.locator('#resource-empty')).to_be_visible()
    expect(page.locator('#resource-prev')).to_be_disabled();expect(page.locator('#resource-next')).to_be_disabled()
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved editor kept through catalog navigation')
    assert store.active()['release_id'] is None
