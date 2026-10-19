"""Version prerequisites and plan evidence on isolated servers/fresh Edge only."""
import copy
import hashlib
import json
import os
import re

import pytest

from nxserver.store import digest
from test_upgrade_recovery_browser import wizard,jobs
from installation_version_test_support import write_nx_image

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated installation-version GUI tests.')


def test_wizard_shows_observed_versions_without_runtime_license_or_signature_claim(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;before=store.manifest(source)
    page.locator('#upgrade-check').click()
    state=page.locator('#upgrade-prerequisites')
    expect(state).to_contain_text('NX2406 → 2406.8500')
    expect(state).to_contain_text('NX2512 → 2512.8500')
    expect(state).to_contain_text('keine Signatur-, NX-Ausführungs- oder Lizenzprüfung')
    assert jobs(store)==[] and not target.exists() and store.manifest(source)==before
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt',timeout=15000)
    assert target.is_dir() and len(store.releases())==1 and store.manifest(source)==before
    assert store.releases()[0]['test_launcher'] and store.active()['release_id'] is None


def test_wrong_target_version_has_visible_error_and_no_accepted_preview(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;write_nx_image(store.standards['2512'],'2406')
    page.locator('#upgrade-check').click()
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('Configured NX2512 installation contains NX2406.8500')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#message')).to_contain_text('prerequisites are incomplete')
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert jobs(store)==[] and not target.exists() and not store.releases()


@pytest.mark.parametrize('fault',['missing_contract','missing_metadata','wrong_version','runtime_claim','signature_claim',
    'bad_sha','fixed_disagreement','text_disagreement','identity'])
def test_rehashed_but_incomplete_or_false_version_evidence_cannot_arm_build(wizard,fault):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    def invalid(route):
        response=route.fetch();job=response.json()
        if job['status']!='completed':route.fulfill(response=response);return
        report=json.loads((store.root/'upgrades'/job['id']/'report.json').read_bytes())
        prerequisites=report['prerequisites'];version=prerequisites['versions'][1];metadata=version['version_metadata']
        if fault=='missing_contract':prerequisites.pop('installation_version_contract_version')
        elif fault=='missing_metadata':version.pop('version_metadata')
        elif fault=='wrong_version':metadata['nx_version']='2406'
        elif fault=='runtime_claim':metadata['nx_execution_verified']=True
        elif fault=='signature_claim':metadata['digital_signature_verified']=True
        elif fault=='bad_sha':metadata['sha256']='not-a-sha'
        elif fault=='fixed_disagreement':metadata['product_version'][0]=2406
        elif fault=='text_disagreement':metadata['strings']['ProductVersion']='2406.8500'
        else:metadata['strings']['ProductName']='Another CAM product'
        unsigned=copy.deepcopy(report);unsigned.pop('preview_hash');report['preview_hash']=digest(unsigned)
        raw=json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        job['report_sha256']=hashlib.sha256(raw).hexdigest();job['summary']['preview_hash']=report['preview_hash']
        page.route('**/migrations/'+job['id']+'/report',lambda later:later.fulfill(status=200,content_type='application/json',body=raw))
        route.fulfill(response=response,json=job)
    page.route(re.compile(r'/api/v1/migrations/[a-f0-9-]{36}$'),invalid)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-state')).to_contain_text('Upgrade-Plan nicht bestätigt',timeout=15000)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert len(jobs(store))==1 and not target.exists() and not store.releases()
