"""Execute the ordinary download controller with real streaming SHA, no network."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_actual_file_download_controller_bounds_ownership_cancellation_and_publication():
    node=next((value for value in (shutil.which('node'),r'C:\Program Files\nodejs\node.exe','/mnt/c/Program Files/nodejs/node.exe') if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    root=Path(__file__).parents[1]/'nxserver/web'
    app=(root/'app.js').read_text(encoding='utf-8')
    source=app[app.index('function cancelFileDownload('):app.index('async function downloadTestLauncher(release)')]
    vendor=(root/'vendor/jssha-3.3.2-sha256.js').read_text(encoding='utf-8')
    receive=(root/'downloads.js').read_text(encoding='utf-8')
    script=r'''
    const assert=require('node:assert/strict'),crypto=require('node:crypto');
    const vendorModule={exports:{}};
    new Function('module','exports',VENDOR)(vendorModule,vendorModule.exports);
    globalThis.jsSHA=vendorModule.exports;globalThis.Blob=require('node:buffer').Blob;
    let connectionGeneration=1,token='synthetic-only',fileDownloadOwner=null,releases=[];
    const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:true,disabled:true,className:''});return nodes.get(id);};
    const NXRequests={stale:()=>Object.assign(new Error('Stale synthetic context'),{name:'StaleUIReply'})};
    let emitted=[],revoked=0,fetches=0,response,nextBlob=null,lastSignal=null;
    globalThis.URL={createObjectURL(blob){nextBlob=blob;return 'blob:synthetic';},revokeObjectURL(){revoked++;}};
    globalThis.setTimeout=callback=>{callback();return 0;};
    globalThis.document={createElement(){return {click(){emitted.push({blob:nextBlob,name:this.download});}};}};
    globalThis.fetch=async(path,options)=>{fetches++;lastSignal=options.signal;return response;};
    new Function(RECEIVE)();
    const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
    function reply(bytes,{header=String(bytes.length),code=200,hold=false,media='application/pdf',parts=null}={}){
      const state={readers:0,reads:0,cancelled:0,released:0},chunks=parts||[bytes];let index=0,release=null;
      const value={state,ok:code===200,status:code,headers:{get:key=>key==='content-length'?header:key==='content-type'?media:null},
        async blob(){throw new Error('Forbidden whole response blob');},async arrayBuffer(){throw new Error('Forbidden whole body buffer');},
        async json(){throw new Error('Do not inspect unbounded private HTTP error payloads');},
        body:{getReader(){state.readers++;return {
          async read(){state.reads++;if(hold){hold=false;await new Promise(resolve=>{release=resolve;});}return index<chunks.length?{done:false,value:chunks[index++]}:{done:true};},
          async cancel(){state.cancelled++;},releaseLock(){state.released++;}
        };}}};
      value.resume=()=>{assert.ok(release);release();};return value;
    }
    CONTROLLER
    (async()=>{
      const bytes=Uint8Array.from([9,97,98,99,8]).subarray(1,4),expected={size:3,sha256:digest(bytes)};
      const clean=()=>{assert.equal(fileDownloadOwner,null);emitted=[];fetches=0;};
      for(const metadata of [{},{sha256:'bad'},{...expected,size:0},{...expected,size:'3'},{...expected,size:Number.MAX_SAFE_INTEGER+1},{...expected,size:64*1024*1024+1}]){
        clean();await assert.rejects(()=>downloadFile('synthetic','test.pdf',metadata));assert.equal(fetches,0);assert.equal(emitted.length,0);
      }
      for(const header of [null,'0','01','-1','invalid','9007199254740993','67108865']){
        clean();response=reply(bytes,{header});await assert.rejects(()=>downloadFile('synthetic','test.pdf',{sha256:expected.sha256}));
        assert.equal(response.state.readers,0);assert.equal(emitted.length,0);assert.equal(lastSignal.aborted,true);
      }
      for(const header of [null,'3']){
        clean();response=reply(bytes,{header,parts:[bytes.subarray(0,1),bytes.subarray(1)]});
        const result=await downloadFile('synthetic','Zeichnung ü.pdf',expected);
        assert.deepEqual(result,expected);assert.equal(emitted.length,1);assert.equal(emitted[0].name,'Zeichnung ü.pdf');
        assert.equal(emitted[0].blob.type,'application/pdf');assert.deepEqual(new Uint8Array(await emitted[0].blob.arrayBuffer()),bytes);
        assert.equal(response.state.readers,1);assert.equal(response.state.released,1);assert.equal(response.state.cancelled,0);
        assert.match($('file-download-state').textContent,/Prüfsumme und Größe bestätigt/);assert.equal($('file-download-cancel').disabled,true);
      }
      for(const body of [Uint8Array.from([97,98,100]),bytes.subarray(0,2),Uint8Array.from([97,98,99,100])]){
        clean();response=reply(body,{header:'3'});await assert.rejects(()=>downloadFile('synthetic','test.pdf',expected));
        assert.equal(emitted.length,0);assert.equal(response.state.cancelled,1);assert.equal(lastSignal.aborted,true);
      }
      clean();response=reply(bytes,{header:'4'});await assert.rejects(()=>downloadFile('synthetic','test.pdf',expected),/Downloadgröße/);assert.equal(response.state.readers,0);
      for(const code of [401,403,409,500]){
        clean();response=reply(bytes,{code});await assert.rejects(()=>downloadFile('synthetic','test.pdf',expected),new RegExp('HTTP '+code));
        assert.equal(response.state.readers,0);assert.equal(emitted.length,0);assert.equal(lastSignal.aborted,true);
      }
      clean();response=reply(bytes);await assert.rejects(()=>downloadFile('synthetic','test.pdf',{...expected,current:()=>false}),error=>error.name==='StaleUIReply');assert.equal(fetches,0);
      clean();response=reply(bytes);let valid=true;
      await assert.rejects(()=>downloadFile('synthetic','test.pdf',{...expected,current:()=>valid,prepare:async()=>{valid=false;}}),error=>error.name==='StaleUIReply');assert.equal(fetches,0);
      clean();response=reply(bytes);valid=true;
      await assert.rejects(()=>downloadFile('synthetic','test.pdf',{...expected,current:()=>valid,verify:async()=>{valid=false;}}),error=>error.name==='StaleUIReply');assert.equal(emitted.length,0);
      clean();response=reply(bytes,{hold:true});const oldResponse=response;
      const first=downloadFile('synthetic','old.pdf',expected).catch(error=>error);
      await new Promise(setImmediate);assert.ok(fileDownloadOwner);assert.equal(response.state.reads,1);
      await assert.rejects(()=>downloadFile('synthetic','duplicate.pdf',expected),/Dateidownload läuft bereits/);assert.equal(fetches,1);
      const oldSignal=lastSignal;cancelFileDownload();assert.equal(fileDownloadOwner,null);assert.equal(oldSignal.aborted,true);
      response=reply(bytes);await downloadFile('synthetic','new.pdf',expected);const success=$('file-download-state').textContent;
      oldResponse.resume();assert.equal((await first).name,'StaleUIReply');assert.equal(emitted.length,1);assert.equal(emitted[0].name,'new.pdf');
      assert.equal($('file-download-state').textContent,success);assert.equal(oldResponse.state.cancelled,1);
      clean();response=reply(bytes,{hold:true});const pending=response;
      const loggedOut=downloadFile('synthetic','private.pdf',expected).catch(error=>error);await new Promise(setImmediate);
      connectionGeneration++;token='';cancelFileDownload(true);pending.resume();assert.equal((await loggedOut).name,'StaleUIReply');
      assert.equal(emitted.length,0);assert.equal($('file-download-box').hidden,true);assert.equal($('file-download-state').textContent,'');
      assert.ok(revoked>=3);console.log('Actual ordinary download controller assertions passed.');
    })().catch(error=>{console.error(error);process.exitCode=1;});
    '''.replace('VENDOR',json.dumps(vendor)).replace('RECEIVE',json.dumps(receive)).replace('CONTROLLER',source)
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Actual ordinary download controller assertions passed.' in result.stdout
