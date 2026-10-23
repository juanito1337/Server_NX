"""Version-aware HMI and durable recovery; no Siemens execution."""
import os
import pytest
from nxserver.geometry import GeometryJobs
from test_geometry_recovery_browser import prepare
from test_requests_browser import ui
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Explicit isolated Edge suite.')


def test_hmi_explicit_2406_target_is_pinned_after_a_lost_reply(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;tool,asset=prepare(page,store)
    page.locator('#step-nx-version').select_option('2406')
    lose(page,'/api/v1/geometry-jobs')
    page.locator('#step-job-create').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#step-nx-version').select_option('2512')
    page.locator('#step-repeat').click()
    expect(page.locator('#message')).to_contain_text('Auftrag registriert')
    expect(page.locator('#step-nx-version')).to_have_value('2512')
    jobs=GeometryJobs(store).list(tool['id'])
    assert len(jobs)==1 and jobs[0]['nx_version']=='2406'
    assert store.resources()[0]['cam_status']=='draft'


def test_hmi_legacy_default_is_2512_without_implicit_nx_start(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;tool,asset=prepare(page,store)
    expect(page.locator('#step-nx-version')).to_have_value('2512')
    page.locator('#step-job-create').click()
    expect(page.locator('#message')).to_contain_text('Auftrag registriert')
    jobs=GeometryJobs(store).list(tool['id'])
    assert len(jobs)==1 and jobs[0]['nx_version']=='2512'
    assert jobs[0]['status']=='awaiting_nx'
