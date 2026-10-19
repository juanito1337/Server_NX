"""The common JSON API reader preserves numeric evidence on every JSON route."""
import json
import os

import pytest

from nxserver.integrity import snapshot
from test_requests_browser import ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated API JSON numeric evidence.')


def test_real_openapi_int64_bound_is_displayed_exactly_without_changing_editor(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;before=snapshot(store)['sha256']
    page.locator('#tab-prepare').click();page.locator('#name').fill('Unsaved before exact OpenAPI')
    page.locator('#openapi').click();expect(page.locator('#details-box')).to_have_attribute('open','')
    value=json.loads(page.locator('#details').inner_text())
    params=value['paths']['/api/v1/resources/{resource_id}/history-summary']['get']['parameters']
    assert next(item for item in params if item['name']=='offset')['schema']['maximum']==2**63-1
    expect(page.locator('#name')).to_have_value('Unsaved before exact OpenAPI')
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('method',['GET','POST','PUT'])
def test_generic_json_routes_keep_number_evidence_not_only_named_resource_routes(ui,method):
    page,store,*_=ui;before=snapshot(store)['sha256']
    value={'float':1.0,'negative_zero':-0.0,'large_int':2**53+1,'huge_int':10**400,
           'nested':{'__proto__':[1e20,0.015,1e-7]}}
    raw=json.dumps(value)
    page.route('**/api/v1/isolated-number-reader-probe',lambda route:route.fulfill(status=200,content_type='application/json',body=raw))
    text=page.evaluate("""async method=>NXData.stringifyWire(await api('isolated-number-reader-probe',method,method==='GET'?undefined:{}))""",method)
    assert json.dumps(json.loads(text),sort_keys=True)==json.dumps(value,sort_keys=True)
    assert snapshot(store)['sha256']==before
