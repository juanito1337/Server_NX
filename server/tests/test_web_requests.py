"""Run the actual browser request gate in JavaScript, without a browser/server."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_actual_shared_gate_discards_stale_success_errors_sessions_and_scopes():
    candidates=[shutil.which('node'),r'C:\Program Files\nodejs\node.exe','/mnt/c/Program Files/nodejs/node.exe']
    node=next((value for value in candidates if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    source=(Path(__file__).parents[1]/'nxserver/web/app.js').read_text(encoding='utf-8')
    helper=source[source.index('globalThis.NXRequests='):source.index('\nconst $')]
    script='let connectionGeneration=0;\n'+helper+r'''
    const assert=require('node:assert/strict');
    (async()=>{
      let first=NXRequests.begin('list.a'),second=NXRequests.begin('list.a');
      assert.equal(first.current(),false);assert.equal(second.current(),true);
      await assert.rejects(()=>first.wait(Promise.resolve('old')),error=>NXRequests.isStale(error));
      await assert.rejects(()=>first.wait(Promise.reject(new Error('old error'))),error=>NXRequests.isStale(error));
      assert.equal(await second.wait(Promise.resolve('new')),'new');
      const genuine=new Error('current error');
      await assert.rejects(()=>second.wait(Promise.reject(genuine)),error=>error===genuine);
      assert.equal(await NXRequests.settle(Promise.reject(NXRequests.stale())),undefined);
      await assert.rejects(()=>NXRequests.settle(Promise.reject(genuine)),error=>error===genuine);
      let a=NXRequests.begin('scope.a'),b=NXRequests.begin('different.b');
      NXRequests.invalidate('scope.');assert.equal(a.current(),false);assert.equal(b.current(),true);
      let selected='one';a=NXRequests.begin('field',()=>selected==='one');
      selected='two';assert.equal(a.current(),false);selected='one';NXRequests.invalidate('field');assert.equal(a.current(),false);
      a=NXRequests.begin('session');connectionGeneration++;assert.equal(a.current(),false);
      a=NXRequests.begin('epoch');NXRequests.reset();assert.equal(a.current(),false);
      b=NXRequests.begin('fresh');assert.equal(b.current(),true);
      a=NXRequests.begin('invalid-json',()=>{throw new SyntaxError('Incomplete input');});assert.equal(a.current(),false);
      a=NXRequests.begin('__proto__');assert.equal(a.current(),true);
      assert.equal(Object.prototype.polluted,undefined);
      assert.equal(NXJobState.label({status:'queued',progress:0}),'queued · 0 %');
      const job={status:'extracting',progress:100,execution:{contract_version:1,persisted_status:'completed',writer_may_be_active:true,child:'live'}};
      assert.match(NXJobState.label(job),/Unterprozess aktiv/);
      assert.match(NXJobState.label(job),/Gespeicherter Status: completed/);
      job.execution.child='unknown';assert.match(NXJobState.label(job),/Unterprozess ungeklärt/);
      job.execution={contract_version:1,historical:true};assert.match(NXJobState.label(job),/Historischer Auftrag/);
      job.execution={contract_version:1,future:'failed'};assert.match(NXJobState.label(job),/Task unerwartet beendet/);
      console.log('Shared gate assertions passed.');
    })().catch(error=>{console.error(error);process.exitCode=1;});
    '''
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Shared gate assertions passed.' in result.stdout
