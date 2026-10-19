"""Synthetic matching HTTP metadata; the physical fixture DB stays unchanged."""
import hashlib
import json
import os

import pytest

from test_requests_browser import ui
from nxserver.integrity import snapshot

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated validation-metadata Edge tests.')


@pytest.mark.parametrize('defect', ['schema_hash_list', 'short_date', 'invalid_calendar', 'empty_field'])
def test_matching_invalid_metadata_never_publishes_a_successful_summary(ui, defect):
    from playwright.sync_api import expect
    page, store, target, _ = ui
    store.validate_release(target['id'], 'fixture')
    before = snapshot(store)['sha256']
    def mutate(release):
        if defect == 'schema_hash_list':
            block = release['validation']['libraries'][0]['schemas'][0]
            block['schema_hash'] = [block['schema_hash']]
        elif defect == 'short_date':
            release['validation']['checked_at'] = '1'
        elif defect == 'invalid_calendar':
            release['validation']['checked_at'] = '2026-02-30T14:00:00+00:00'
        else:
            release['validation']['libraries'][0]['schemas'][0]['fields'].append('')
    def workspace(route):
        response = route.fetch();value = response.json()
        mutate(next(item for item in value['releases'] if item['id'] == target['id']))
        raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        headers = dict(response.headers)
        headers.update({'content-length': str(len(raw)), 'x-nx-workspace-bytes': str(len(raw)),
                        'x-nx-workspace-sha256': hashlib.sha256(raw).hexdigest()})
        route.fulfill(response=response, headers=headers, body=raw)
    def details(route):
        response = route.fetch();value = response.json();mutate(value)
        route.fulfill(response=response, json=value)
    page.route('**/api/v1/workspace', workspace)
    page.route('**/api/v1/releases/' + target['id'], details)
    page.locator('#refresh').click()
    expect(page.locator('#releases')).to_contain_text('validated')
    posts = []
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button', name='Details', exact=True).click()
    expect(page.locator('#release-validation-state')).to_contain_text('Lesbare Übersicht nicht bestätigt')
    expect(page.locator('#release-validation-items tr')).to_have_count(0)
    assert not posts and snapshot(store)['sha256'] == before
