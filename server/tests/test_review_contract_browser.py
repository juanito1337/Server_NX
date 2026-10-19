"""Whole scan-reply contracts on disposable Edge/servers with synthetic evidence."""
import os

import pytest

from test_requests_browser import ui,hold,track,release as release_reply
from test_release_recovery_browser import ready

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated review reply contracts.')


@pytest.mark.parametrize('field',['sha256','manifest_sha256','parser_version','expected_folder','blocking_errors',
                                  'contains_cam_activity','version_evidence','wrong_family','can_review_type','wrong_source',
                                  'blocking_count','blocking_truncated','header_count','wrong_status','evidence_family','warnings'])
def test_incomplete_or_contradictory_scan_reply_never_qualifies(ui,field):
    from playwright.sync_api import expect
    page,store,initial,*_=ui
    ready(page,initial)
    before=store.audit_entries()
    def invalid(route):
        response=route.fetch();value=response.json()
        if field=='version_evidence':value['version_check'].pop('evidence')
        elif field=='wrong_family':value['version_check']['families']=['2406']
        elif field=='can_review_type':value['can_review']='true'
        elif field=='wrong_source':value['source']='unselected-synthetic-log'
        elif field=='blocking_count':value['blocking_error_count']=1
        elif field=='blocking_truncated':value['blocking_errors_truncated']=True
        elif field=='header_count':value['version_check']['header_count']=0
        elif field=='wrong_status':value['version_check']['status']='missing'
        elif field=='evidence_family':value['version_check']['evidence'][0]['family']='2406'
        else:value.pop(field)
        route.fulfill(response=response,json=value)
    page.route('**/scan',invalid)
    page.locator('#scan').click()
    for key in ('tools','machines','posts','simulation','fixtures','inspection'):page.locator('#'+key).check()
    page.locator('#evidence').fill('Synthetic manual confirmations must not bypass an invalid scan reply.')
    expect(page.locator('#message')).to_contain_text('Kein vollständiger gebundener NX-Protokollvertrag')
    expect(page.locator('#approve')).to_be_disabled()
    expect(page.locator('#syslog-state')).to_contain_text('Keine vollständige aktuelle Protokollvorschau')
    expect(page.locator('#details')).to_contain_text('not_verified')
    assert store.audit_entries()==before and store.get_release(initial['id'])['status']=='draft'
    assert store.active()['release_id'] is None
    assert page.evaluate('sessionStorage.getItem("nx.pending-release-transition.v1")') is None


@pytest.mark.parametrize('old_error',[False,True])
def test_log_selection_aba_discards_old_scan_reply_without_hanging(ui,old_error):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;ready(page,initial)
    before=store.audit_entries()
    hold(page,'/scan',old_error);track(page,'scan');page.locator('#scan').click()
    page.wait_for_function('()=>window.__held')
    # The selection is disabled while scanning. Simulate an explicit context
    # invalidation, including the same value again, without invoking any NX.
    page.evaluate('''()=>{const field=document.getElementById('syslog'),old=field.value;
      field.value='';field.dispatchEvent(new Event('change'));field.value=old;field.dispatchEvent(new Event('change'));}''')
    release_reply(page)
    expect(page.locator('#scan')).to_be_enabled()
    expect(page.locator('#approve')).to_be_disabled()
    expect(page.locator('#syslog-state')).to_contain_text('Protokollauswahl geändert')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    page.locator('#scan').click();expect(page.locator('#syslog-state')).to_contain_text('NX-Version: passend')
    assert store.audit_entries()==before and store.get_release(initial['id'])['status']=='draft'


@pytest.mark.parametrize('field',['final_evidence_byte_gate','filesystem_atomic_snapshot'])
def test_weaker_backend_contract_cannot_enable_approval(ui,field):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;ready(page,initial)
    before=store.audit_entries()
    page.evaluate('''async field=>{const response=await fetch('/api/v1/capabilities',{headers:{Authorization:'Bearer '+token}});
      const value=await response.json();value.release_transitions[field]=field==='filesystem_atomic_snapshot';NXReviews.configure(value);}''',field)
    expect(page.locator('#approve')).to_be_disabled()
    expect(page.locator('#review-gate')).to_contain_text('Server unterstützt den aktuellen Freigabevertrag')
    assert store.audit_entries()==before and store.active()['release_id'] is None


def test_complete_truncated_load_errors_keep_total_and_block_review(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;ready(page,initial);before=store.audit_entries()
    def many(route):
        response=route.fetch();value=response.json()
        value.update(blocking_error_count=250,blocking_errors_truncated=True,can_review=False,
            blocking_errors=[{'line':i+4,'message':'Failed to read in part: synthetic fixture'} for i in range(200)])
        route.fulfill(response=response,json=value)
    page.route('**/scan',many);page.locator('#scan').click()
    expect(page.locator('#syslog-state')).to_contain_text('250 Ladefehler')
    expect(page.locator('#approve')).to_be_disabled()
    expect(page.locator('#message')).not_to_contain_text('Kein vollständiger gebundener NX-Protokollvertrag')
    assert store.audit_entries()==before and store.active()['release_id'] is None
