"""Execute the actual dynamic JSON helpers: keys are data, never prototypes."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_dynamic_keys_roundtrip_without_prototype_mutation_or_existing_value_loss():
    candidates=[shutil.which('node'),r'C:\Program Files\nodejs\node.exe','/mnt/c/Program Files/nodejs/node.exe']
    node=next((value for value in candidates if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    source=(Path(__file__).parents[1]/'nxserver/web/app.js').read_text(encoding='utf-8')
    helper=source[source.index('globalThis.NXData='):source.index('globalThis.NXRequests=')]
    script=helper+r'''
    const assert=require('node:assert/strict');
    const root={};const original=Object.getOwnPropertyDescriptors(Object.prototype);
    for(const key of ['__proto__','constructor','prototype','toString']){
      const branch=NXData.branch(root,key);assert.equal(Object.getPrototypeOf(branch),null);
      NXData.set(branch,key,'literal value');assert.equal(NXData.get(branch,key),'literal value');
      NXData.set(branch,'','explicit empty key');
    }
    assert.equal(Object.getPrototypeOf(root),Object.prototype);
    assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype),original);
    const roundtrip=JSON.parse(JSON.stringify(root));
    for(const key of Object.keys(root))assert.equal(NXData.get(roundtrip[key],key),'literal value');
    assert.equal(NXData.get({},'constructor'),undefined);
    for(const value of [null,[],3,'wrong',true])assert.throws(()=>NXData.branch(value,'x'));
    for(const value of [null,[],3,'wrong',true]){
      const bad={existing:value};assert.throws(()=>NXData.branch(bad,'existing'));assert.equal(bad.existing,value);
    }
    const owned=JSON.parse('{"__proto__":{"present":"kept"}}');
    assert.equal(NXData.branch(owned,'__proto__').present,'kept');
    NXData.set(owned,'__proto__','now a scalar');assert.equal(owned.__proto__,'now a scalar');
    NXData.remove(owned,'__proto__');assert.equal(NXData.get(owned,'__proto__'),undefined);
    assert.equal(Object.getPrototypeOf(owned),Object.prototype);
    NXData.remove({},'constructor');assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype),original);
    console.log('Dynamic data assertions passed.');
    '''
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Dynamic data assertions passed.' in result.stdout
