"""Visible failed/partial worker evidence, synthetic report and disposable Edge."""
import json
import os

import pytest

from nxserver.geometry import GeometryJobs
from test_geometry import report_for
from test_geometry_recovery_browser import prepare
from test_requests_browser import ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated failed output diagnostic UI.')


@pytest.mark.parametrize('failure', ['output', 'component_count', 'component_depth', 'part_count', 'body_count', 'report_bytes'])
def test_partial_output_read_failure_is_visible_and_never_qualifies_geometry(ui, failure):
    from playwright.sync_api import expect
    page,store,*_=ui;tool,_=prepare(page,store)
    page.locator('#step-job-create').click();expect(page.locator('#message')).to_contain_text('Auftrag registriert')
    job=GeometryJobs(store).list(tool['id'])[0]
    report=report_for(job)
    message = {
        'output': 'Output file tool.prt: Synthetic output read failure',
        'component_count': 'Component count exceeds diagnostic bounds; only a partial component inventory is recorded.',
        'component_depth': 'Component ID depth exceeds diagnostic bounds; only a partial component inventory is recorded.',
        'part_count': 'Part count exceeds diagnostic bounds; only a partial part inventory is recorded.',
        'body_count': 'Body count exceeds diagnostic bounds; only a partial part inventory is recorded.',
        'report_bytes': 'Diagnostic byte budget exceeded; only a partial diagnostic inventory is recorded.',
    }[failure]
    report.update(status='failed', errors=[message])
    if failure == 'output':
        report['files'] = [item for item in report['files'] if item['path'] != 'tool.prt']
    page.locator('#geometry-jobs').get_by_role('button',name='Bericht zuordnen',exact=True).click()
    page.locator('#geometry-report-file').set_input_files({'name':'synthetic-failed-output.json','mimeType':'application/json',
                                                         'buffer':json.dumps(report).encode('utf-8')})
    page.locator('#geometry-report-upload').click()
    expect(page.locator('#geometry-report-state')).to_contain_text('Diagnose zugeordnet')
    expect(page.locator('#geometry-jobs')).to_contain_text('Import fehlgeschlagen (failed)')
    expect(page.locator('#geometry-report-summary')).to_contain_text('1 Fehler')
    expect(page.locator('#geometry-report-summary')).to_contain_text('keine CAM-/Kollisionsfreigabe')
    expect(page.locator('#details')).to_contain_text(message)
    current=next(value for value in store.resources() if value['id']==tool['id'])
    assert current==tool and current['cam_status']=='draft' and current['geometry']['status']=='needs_preparation'
    assert store.active()['release_id'] is None and GeometryJobs(store).get(job['id'])['status']=='failed'
