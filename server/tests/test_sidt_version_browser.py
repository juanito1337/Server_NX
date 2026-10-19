"""SIDT declarations in the real wizard on own servers/fresh Edge only."""
import copy
import hashlib
import json
import os
import re

import pytest

from nxserver.store import digest
from nxserver.sidt_version import RELATIVE
from test_upgrade_recovery_browser import wizard,jobs

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated SIDT declaration wizard.')


def test_wizard_shows_both_sidt_declarations_and_builds_without_copying_marker_files(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;before=store.manifest(source)
    page.locator('#upgrade-check').click()
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('SIDT-Deklarationen: NX2406 · NX2512')
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('keine Signatur-, NX-Ausführungs- oder Lizenzprüfung')
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt',timeout=15000)
    assert target.is_dir() and not (target/RELATIVE).exists() and store.manifest(source)==before
    assert len(store.releases())==1 and store.releases()[0]['test_launcher'] and store.active()['release_id'] is None


@pytest.mark.parametrize('fault',['wrong','missing','duplicate'])
def test_bad_sidt_declaration_is_visible_and_never_accepts_a_preview(wizard,fault):
    from playwright.sync_api import expect
    page,store,source,target=wizard;path=store.sidts['2512']/RELATIVE
    if fault=='missing':path.unlink()
    elif fault=='wrong':path.write_bytes(b'set NX_INST_DIR=NX2406\n')
    else:path.write_bytes(b'set NX_INST_DIR=NX2512\nset NX_INST_DIR=NX2406\n')
    page.locator('#upgrade-check').click()
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('NX2512:')
    if fault=='wrong':expect(page.locator('#upgrade-prerequisites')).to_contain_text('SIDT declares NX2406, expected NX2512')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#message')).to_contain_text('prerequisites are incomplete')
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert jobs(store)==[] and not target.exists() and not store.releases()


@pytest.mark.parametrize('fault',['contract','bool_contract','missing','wrong','script','provenance','hash'])
def test_rehashed_report_with_false_or_missing_sidt_declaration_cannot_arm_build(wizard,fault):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    def invalid(route):
        response=route.fetch();job=response.json()
        if job['status']!='completed':route.fulfill(response=response);return
        report=json.loads((store.root/'upgrades'/job['id']/'report.json').read_bytes())
        check=report['prerequisites'];profile=check['versions'][1]['profile'];proof=profile['version_declaration']
        if fault=='contract':check.pop('sidt_version_contract_version')
        elif fault=='bool_contract':check['sidt_version_contract_version']=True
        elif fault=='missing':profile.pop('version_declaration')
        elif fault=='wrong':proof['nx_version']='2406'
        elif fault=='script':proof['script_executed']=True
        elif fault=='provenance':proof['provenance_verified']=True
        else:proof['sha256']='not-a-sha'
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
