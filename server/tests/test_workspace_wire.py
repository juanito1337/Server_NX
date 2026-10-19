"""Actual streaming JSON reader: byte proof, cancellation and safe failures."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_actual_workspace_wire_reader_checks_raw_bytes_not_js_number_spelling():
    node=next((value for value in (shutil.which('node'),r'C:\Program Files\nodejs\node.exe',
        '/mnt/c/Program Files/nodejs/node.exe') if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    source=(Path(__file__).parents[1]/'nxserver/web/app.js').read_text(encoding='utf-8')
    helpers=source[source.index('globalThis.NXData='):source.index('\nconst $')]
    reader=source[source.index('async function readWorkspaceResponse('):source.index('async function api(')]
    script=r'''
    const assert=require('node:assert/strict'),crypto=require('node:crypto');let incoming='';
    process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>incoming+=chunk);
    process.stdin.on('end',async()=>{try{
      const input=JSON.parse(incoming);let connectionGeneration=0;eval(input.helpers);
      globalThis.jsSHA=class{constructor(){this.value=crypto.createHash('sha256');}update(bytes){this.value.update(bytes);}getHash(){return this.value.digest('hex');}};
      const read=eval(input.reader+'\nreadWorkspaceResponse;'),encode=value=>new TextEncoder().encode(value),digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
      const raw='{"cam":{"float":1.0,"tolerance":0.015,"small":1e-20,"__proto__":"literal","label":"🔧"}}',bytes=encode(raw);
      function response(data=bytes,options={}){
        const state={reads:0,cancelled:0,released:0},headers={'x-nx-workspace-contract':'1','x-nx-workspace-bytes':String(data.length),'x-nx-workspace-sha256':digest(data),'content-type':'application/json; charset=utf-8',...options.headers};
        let offset=0;const cancel=async()=>{state.cancelled++;if(options.cancelError)throw new Error('Private cancel details');};
        const body={cancel,getReader:()=>({cancel,releaseLock:()=>{state.released++;},read:async()=>{
          state.reads++;if(options.readError)throw new Error('Private read details');if(options.badChunk)return {done:false,value:'not bytes'};
          if(offset===data.length)return {done:true};const value=data.subarray(offset,offset+(options.chunk||1));offset+=value.length;return {done:false,value};}})};
        return [{headers:{get:key=>headers[key.toLowerCase()]??null},body:options.noBody?null:body},state];
      }
      for(const chunk of [1,2,7,4096]){const [value,state]=response(bytes,{chunk});assert.deepEqual(await read(value,()=>true),JSON.parse(raw));assert.equal(state.cancelled,0);assert.equal(state.released,1);}
      assert.notEqual(digest(encode(JSON.stringify(JSON.parse(raw)))),digest(bytes));
      for(const headers of [
        {'x-nx-workspace-contract':null},{'x-nx-workspace-contract':'2'},
        {'x-nx-workspace-bytes':null},{'x-nx-workspace-bytes':'0'},{'x-nx-workspace-bytes':'01'},
        {'x-nx-workspace-bytes':'9007199254740992'},{'x-nx-workspace-bytes':String(bytes.length-1)},
        {'x-nx-workspace-bytes':String(bytes.length+1)},{'x-nx-workspace-sha256':null},
        {'x-nx-workspace-sha256':'A'.repeat(64)},{'x-nx-workspace-sha256':'0'.repeat(64)},
        {'content-type':'text/html'}]){
        const [value,state]=response(bytes,{headers});await assert.rejects(()=>read(value,()=>true),error=>/bytegeprüfter/.test(error.message));assert.equal(state.cancelled,1);
      }
      for(const data of [encode('{"private_data":"must not echo",broken'),Uint8Array.from([0xc3,0x28])]){
        const [value,state]=response(data);await assert.rejects(()=>read(value,()=>true),error=>/bytegeprüfter/.test(error.message)&&!/private_data|must not echo/.test(error.message));assert.equal(state.cancelled,1);assert.equal(state.released,1);
      }
      for(const options of [{readError:true},{badChunk:true},{readError:true,cancelError:true}]){
        const [value,state]=response(bytes,options);await assert.rejects(()=>read(value,()=>true),error=>/bytegeprüfter/.test(error.message)&&!/Private/.test(error.message));assert.equal(state.cancelled,1);assert.equal(state.released,1);
      }
      for(const threshold of [0,2]){let checks=0;const [value,state]=response();await assert.rejects(()=>read(value,()=>++checks<=threshold),error=>NXRequests.isStale(error));assert.equal(state.cancelled,1);assert.equal(state.released,1);assert.ok(state.reads<=threshold);}
      const [empty]=response(bytes,{noBody:true});await assert.rejects(()=>read(empty,()=>true),error=>/bytegeprüfter/.test(error.message));
      console.log('Actual workspace wire-reader assertions passed.');
    }catch(error){console.error(error);process.exitCode=1;}});
    '''
    result=subprocess.run([node,'-e',script],input=json.dumps(dict(helpers=helpers,reader=reader),ensure_ascii=False),
        capture_output=True,text=True,encoding='utf-8',timeout=40)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Actual workspace wire-reader assertions passed.' in result.stdout
