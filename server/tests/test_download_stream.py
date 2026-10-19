"""Run real browser streaming SHA helper in Node; all bytes are synthetic."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest


WEB=Path(__file__).parents[1]/'nxserver/web'


def test_vendor_bundle_matches_pinned_upstream_and_includes_required_licenses():
    directory=WEB/'vendor';metadata=json.loads((directory/'provenance.json').read_text(encoding='utf-8'))
    assert metadata['commit']=='694747f7ba4d4493208ea7a259438eaab71917a1'
    assert metadata['version']=='3.3.2' and metadata['unmodified_bundle'] and not metadata['runtime_cdn']
    assert hashlib.sha256((directory/'jssha-3.3.2-sha256.js').read_bytes()).hexdigest()==metadata['sha256']=='932fa0e677f4866f8a54c958acf20401372069c36ae067b5920f58c0feb5a464'
    assert all((directory/name).is_file() for name in metadata['license_files'])
    assert 'Redistribution and use' in (directory/'LICENSE-jssha.txt').read_text(encoding='utf-8')
    assert 'Apache License' in (directory/'LICENSE-Apache-2.0.txt').read_text(encoding='utf-8')


def test_local_download_scripts_are_served_exactly_and_loaded_in_dependency_order(tmp_path):
    from fastapi.testclient import TestClient
    from nxserver.api import create_app
    from nxserver.store import Store
    token='isolated-static-download-'+'x'*40
    with TestClient(create_app(Store(tmp_path/'managed'),token)) as client:
        html=client.get('/').text
        scripts=['/vendor/jssha-3.3.2-sha256.js','/downloads.js','/backups.js']
        assert [html.index('src="'+path+'"') for path in scripts]==sorted(html.index('src="'+path+'"') for path in scripts)
        for path in scripts:
            response=client.get(path)
            assert response.status_code==200 and response.content==(WEB/path.lstrip('/')).read_bytes()
            assert 'javascript' in response.headers['content-type']
        # Public executable assets are not permission to read an archive.
        assert client.get('/api/v1/backups').status_code==401
        assert client.get('/api/v1/backups/00000000-0000-4000-8000-000000000001/download').status_code==401
    project=WEB.parents[2]
    assert 'COPY nxserver ./nxserver' in (project/'server/Dockerfile').read_text(encoding='utf-8')


def test_streaming_hash_vectors_bounds_nonzero_offsets_and_cancellation():
    candidates=[shutil.which('node'),r'C:\Program Files\nodejs\node.exe','/mnt/c/Program Files/nodejs/node.exe']
    node=next((value for value in candidates if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    bundle=(WEB/'vendor/jssha-3.3.2-sha256.js').read_text(encoding='utf-8')
    helper=(WEB/'downloads.js').read_text(encoding='utf-8')
    script='const vendorModule={exports:{}};\nnew Function("module","exports",'+json.dumps(bundle)+')(vendorModule,vendorModule.exports);\nglobalThis.jsSHA=vendorModule.exports;\nglobalThis.Blob=require("node:buffer").Blob;\n'+helper+r'''
    const assert=require('node:assert/strict'),crypto=require('node:crypto');
    const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
    function response(parts,callback=()=>{}){
      const state={reads:0,cancelled:0,released:0};let index=0;
      return {state,body:{getReader(){return {async read(){state.reads++;callback(index);return index<parts.length?{done:false,value:parts[index++]}:{done:true};},async cancel(){state.cancelled++;},releaseLock(){state.released++;}};}}};
    }
    (async()=>{
      for(const count of [1,3,55,56,63,64,65,127,128,129,1024,1025]){
        const bytes=Uint8Array.from({length:count},(_,index)=>(index*37+19)%256),parts=[];
        for(let index=0;index<bytes.length;index+=7)parts.push(bytes.subarray(index,index+7));
        const reply=response(parts),result=await NXDownload.receive(reply,{size:count,sha256:digest(bytes)});
        assert.equal(result.verified,true);assert.equal(result.sha256,digest(bytes));assert.equal(result.blob.size,count);
        assert.deepEqual(new Uint8Array(await result.blob.arrayBuffer()),bytes);assert.equal(reply.state.cancelled,0);assert.equal(reply.state.released,1);
      }
      const abc=Uint8Array.from([9,97,98,99,8]).subarray(1,4);
      assert.equal((await NXDownload.receive(response([abc]),{size:3,sha256:digest(abc)})).sha256,'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
      for(const mismatch of ['same_size_corruption','short','long']){
        const reply=response([abc]);const expected={size:mismatch==='short'?4:mismatch==='long'?2:3,sha256:mismatch==='same_size_corruption'?'0'.repeat(64):digest(abc)};
        await assert.rejects(()=>NXDownload.receive(reply,expected),/SHA|unvollständig|Größe/);assert.equal(reply.state.cancelled,1);assert.equal(reply.state.released,1);
      }
      let alive=true;const cancelled=response([abc],()=>{alive=false;});
      await assert.rejects(()=>NXDownload.receive(cancelled,{size:3,sha256:digest(abc)},{alive:()=>alive}),/Sitzung/);assert.equal(cancelled.state.cancelled,1);
      const failedWriter={async write(){throw new Error('Synthetic disk failure');}};
      const failed=response([abc]);await assert.rejects(()=>NXDownload.receive(failed,{size:3,sha256:digest(abc)},{writer:failedWriter}),/disk failure/);assert.equal(failed.state.cancelled,1);
      const interrupted=response([abc],()=>{throw new Error('Synthetic interrupted reader');});
      await assert.rejects(()=>NXDownload.receive(interrupted,{size:3,sha256:digest(abc)}),/interrupted reader/);assert.equal(interrupted.state.cancelled,1);assert.equal(interrupted.state.released,1);
      const huge=response([]);await assert.rejects(()=>NXDownload.receive(huge,{size:65*1024*1024,sha256:'0'.repeat(64)}),/Dateispeicher/);assert.equal(huge.state.reads,0);
      for(const metadata of [{size:0,sha256:'0'.repeat(64)},{size:Number.MAX_SAFE_INTEGER+1,sha256:'0'.repeat(64)},{size:3,sha256:'invalid'}])assert.throws(()=>NXDownload.metadata(metadata),/Ungültiger/);
      const writes=[];let changedDuringWrite=true;const reply=response([abc]);
      await assert.rejects(()=>NXDownload.receive(reply,{size:3,sha256:digest(abc)},{writer:{async write(value){writes.push(value.length);changedDuringWrite=false;}},alive:()=>changedDuringWrite}),/Sitzung/);assert.deepEqual(writes,[3]);
      const chunk=new Uint8Array(64*1024).fill(97),repeat=1040,size=chunk.length*repeat;
      const expectedHash=crypto.createHash('sha256');for(let index=0;index<repeat;index++)expectedHash.update(chunk);
      let index=0,total=0,progress=0;
      const large={body:{getReader(){return {async read(){return index++<repeat?{done:false,value:chunk}:{done:true};},async cancel(){throw new Error('Unexpected cancel');},releaseLock(){}};}}};
      const result=await NXDownload.receive(large,{size,sha256:expectedHash.digest('hex')},{writer:{async write(value){total+=value.length;}},progress:()=>progress++});
      assert.equal(result.blob,null);assert.equal(result.size,65*1024*1024);assert.equal(total,size);assert.equal(result.verified,true);assert.ok(progress>0);
      const original=jsSHA;globalThis.jsSHA=undefined;
      await assert.rejects(()=>NXDownload.receive(response([abc]),{size:3,sha256:digest(abc)}),/Komponente fehlt/);globalThis.jsSHA=original;
      console.log('Actual streaming SHA and bounded download assertions passed.');
    })().catch(error=>{console.error(error);process.exitCode=1;});
    '''
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Actual streaming SHA and bounded download assertions passed.' in result.stdout
