"""Visible final-commit rejection; isolated server/Edge and synthetic parts only."""
import os

import pytest

from nxserver.results import Results
from test_requests_browser import ui
from test_result_jobs_browser import prepare, upload_ready

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated result final gate GUI.')


@pytest.mark.parametrize('change', ['target_inventory', 'archive'])
def test_gui_reports_rejected_commit_without_drafts_or_automatic_restart(ui, change):
    from playwright.sync_api import expect
    page, store, *_ = ui
    target, tool, job, packet = prepare(page, store); upload_ready(page, target)
    result = Results(store).list(job['id'])[0]
    before = store.resources()
    # Drift after a successful visible preview. GUI should retain a failed job
    # for inspection, never report a successful application or create drafts.
    if change == 'target_inventory':
        (store.release_folder(target['id']) / 'untracked.txt').write_bytes(b'Synthetic untracked late file')
    else:
        path = Results(store).archive(result['id']); path.write_bytes(path.read_bytes() + b'Synthetic corruption')
    page.locator('#result-apply').click()
    expect(page.locator('#message')).to_contain_text('Verwaltungsauftrag failed', timeout=20000)
    expect(page.locator('#message')).not_to_contain_text('Entwürfe gespeichert')
    assert next(value for value in store.resources() if value['id'] == tool['id']) == tool
    assert store.resources() == before
    assert Results(store).get(result['id'])['applied'] is None and store.active()['release_id'] is None
    with store.db() as db:
        rows = db.execute('SELECT payload FROM migration_jobs').fetchall()
    import json
    jobs = [json.loads(row['payload']) for row in rows]
    adopted = [value for value in jobs if value['mode'] == 'result_adopt']
    assert len(adopted) == 1 and adopted[0]['status'] == 'failed' and not adopted[0].get('domain_result')
    if change == 'target_inventory':
        assert len(adopted[0]['asset_allocations']) == 2
        assert 'no drafts/application committed' in adopted[0]['message']
