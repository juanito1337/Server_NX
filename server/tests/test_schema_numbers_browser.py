"""Free numeric CAM metadata must not block a native schema worklist."""
import json
import os

import pytest

from nxserver.integrity import snapshot
from test_requests_browser import ui,hold,track,release as release_reply
from test_schema_editor_browser import schema
from test_schemaimpact_browser import show,read

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated numeric schema metadata.')


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
@pytest.mark.parametrize('number',[1.0,1.25,-0.0,1e-7,2**53+1,10**400])
def test_allowed_free_decimal_metadata_does_not_block_other_schema_rows(schema,number):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema
    # Send actual Python JSON bytes through the isolated API, not JS stringify
    # (which would change 1.0, -0.0 and integers outside the safe JS range).
    response=page.request.post(page.url.split('/#')[0].rstrip('/')+'/api/v1/resources',
        headers={'Authorization':'Bearer isolated-requests-browser-'+'q'*40,'Content-Type':'application/json'},
        data=json.dumps({'kind':'gauge','name':'Numeric CAM metadata','cam_status':'draft','asset_ids':[],
                         'erp':None,'cam':{'measurement':number}}))
    assert response.ok and response.json()['cam']['measurement']==number
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    show(page,target);before=snapshot(store)['sha256'];read(page)
    expect(page.locator('#schema-impact-items tr').filter(has_text=resource['name'])).to_contain_text('Entscheidungen erforderlich')
    expect(page.locator('#schema-impact-items tr').filter(has_text='Numeric CAM metadata')).to_contain_text('Andere Bearbeitung')
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen').click()
    expect(page.locator('#panel-prepare')).to_be_visible();expect(page.locator('#name')).to_have_value(resource['name'])
    expect(page.locator('#schema-target-release')).to_have_value(target['id'])
    assert snapshot(store)['sha256']==before


def test_actual_streaming_number_reader_with_native_browser_response_and_tee_cancellation(ui):
    page,store,*_=ui;before=snapshot(store)['sha256']
    result=page.evaluate(r'''async()=>{
      const assert=(value,label)=>{if(!value)throw new Error(label);},encode=text=>new TextEncoder().encode(text);
      const digest=text=>{const hash=new jsSHA('SHA-256','UINT8ARRAY');hash.update(encode(text));return hash.getHash('HEX');};
      const raw='{"float":1.0,"negative_zero":-0.0,"label":"🛠","items":[1e-07,9007199254740993]}',bytes=encode(raw);
      for(const chunkSize of [1,2,7,4096]){
        let offset=0,cancelled=0;
        const body=new ReadableStream({pull(controller){if(offset===bytes.length){controller.close();return;}const chunk=bytes.subarray(offset,offset+chunkSize);offset+=chunk.length;controller.enqueue(chunk);},cancel(){cancelled++;}});
        const response=new Response(body),json=response.json.bind(response);let decoded=0;
        response.json=async()=>{decoded++;return json();};
        const value=await readNumberedResponse(response,()=>true);
        assert(NXUpgradePlan.hash(value)===digest('{"float": 1.0, "items": [1e-07, 9007199254740993], "label": "🛠", "negative_zero": -0.0}'),'Exact Python hash');
        assert(decoded===1&&cancelled===0&&response.bodyUsed,'Complete response');
      }
      for(const threshold of [0,3]){
        let checks=0,cancelled=0,error=null;
        const response=new Response(new ReadableStream({pull(controller){controller.enqueue(encode('{'));},cancel(){cancelled++;}}));
        try{await readNumberedResponse(response,()=>++checks<=threshold);}catch(value){error=value;}
        assert(NXRequests.isStale(error)&&cancelled===1&&response.bodyUsed,'Both tee branches cancelled on stale response');
      }
      for(const data of [encode('{"private":"synthetic-private-content",broken'),Uint8Array.from([0xc3,0x28])]){
        let error=null;try{await readNumberedResponse(new Response(data),()=>true);}catch(value){error=value;}
        assert(/vollständiger JSON-Zahlennachweis/.test(error?.message)&&!/synthetic-private-content|private/.test(error.message),'Redacted malformed response');
      }
      return true;
    }''')
    assert result is True and snapshot(store)['sha256']==before


@pytest.mark.parametrize('schema',[{'count':1}],indirect=True)
def test_cancelled_numbered_editor_read_finishes_after_logout_without_reopening(schema):
    from playwright.sync_api import expect
    page,store,target,resource,*_=schema;page.on('dialog',lambda dialog:dialog.accept())
    show(page,target);read(page);before=snapshot(store)['sha256']
    hold(page,'/api/v1/resources/'+resource['id'])
    button=page.locator('#schema-impact-items tr').filter(has_text=resource['name']).get_by_role('button',name='Im Schemaeditor prüfen')
    button.evaluate('(node)=>node.id="numeric-held-open"');track(page,'numeric-held-open')
    button.click();page.wait_for_function('()=>window.__held');page.locator('#disconnect').click()
    release_reply(page)
    expect(page.locator('#work')).to_be_hidden();expect(page.locator('#message')).to_have_text('Abgemeldet.')
    assert page.evaluate('token')=='' and page.evaluate('window.__oldActionFinished') is True
    assert snapshot(store)['sha256']==before
