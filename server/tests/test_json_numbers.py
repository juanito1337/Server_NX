"""Actual browser helpers preserve Python's JSON numbers for read-only hashes."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nxserver.safety import DomainError, offline_path

WEB = Path(__file__).parents[1] / 'nxserver/web'


def javascript(script, **packet):
    node = None
    for candidate in (shutil.which('node'), r'C:\Program Files\nodejs\node.exe',
                      '/mnt/c/Program Files/nodejs/node.exe'):
        if not candidate:
            continue
        try:
            path = offline_path(candidate)
            if path.is_file():
                node = str(path)
                break
        except DomainError:
            continue
    if not node:
        pytest.skip('JavaScript runtime unavailable.')
    app = (WEB / 'app.js').read_text(encoding='utf-8')
    sources = dict(
        helpers=app[app.index('globalThis.NXData='):app.index('\nconst $')],
        numbered=app[app.index('async function readNumberedResponse('):app.index('async function readWorkspaceResponse(')],
        upgrade=(WEB / 'upgrades.js').read_text(encoding='utf-8').split('globalThis.NXUpgrade=')[0],
    )
    runner = r'''
const assert=require('node:assert/strict'),crypto=require('node:crypto');let incoming='';
const digest=text=>crypto.createHash('sha256').update(text).digest('hex');
process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>incoming+=chunk);
process.stdin.on('end',async()=>{try{
const input=JSON.parse(incoming);new Function(input.helpers)();
globalThis.jsSHA=class{constructor(){this.value=crypto.createHash('sha256');}update(bytes){this.value.update(bytes);}getHash(){return this.value.digest('hex');}};
new Function(input.upgrade)();
''' + script + r'''
console.log('Actual numeric JSON assertions passed.');
}catch(error){console.error(error);process.exitCode=1;}});
'''
    result = subprocess.run([node, '-e', runner], input=json.dumps({**sources, **packet}, ensure_ascii=True),
                            capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Actual numeric JSON assertions passed.' in result.stdout


def test_wire_numbers_match_existing_python_hashes_without_rewriting_historical_json():
    value = {'float': 1.0, 'fraction': 0.015, 'negative_zero': -0.0,
             'exponents': [1e-20, 1e-7, 1e-6, 1e20, 1e30, 5e-324, 1.7976931348623157e308],
             'integers': [0, -1, 2**53 - 1, 2**53 + 1, -(2**60 + 3), 10**400],
             '2': 2.0, '10': 10.0, '': 0.25, '🛠': {'\ue000': -1.25, '\U00010000': 1.5},
             '__proto__': {'constructor': 3.0, 'prototype': [4.5, None, True, '1.0']},
             'quote"\\\n': 'control\n\t" and literal \\'}
    variants = []
    for compact in (False, True):
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False,
                               **({'separators': (',', ':')} if compact else {}))
        variants.append(dict(compact=compact, canonical=canonical,
                             sha=hashlib.sha256(canonical.encode()).hexdigest()))
    javascript(r'''
const original=Object.getOwnPropertyDescriptors(Object.prototype);
for(const raw of input.raws){
  const parsed=NXData.parseWire(raw);
  for(const expected of input.variants){
    assert.equal(NXUpgradePlan.hash(parsed,expected.compact),expected.sha);
  }
  assert.equal(NXData.numberLiteral(parsed,'float',parsed.float),'1.0');
  assert.equal(NXData.numberLiteral(parsed,'negative_zero',parsed.negative_zero),'-0.0');
  assert.equal(parsed.integers.at(-1),Infinity); // Raw integer, never invented Infinity JSON.
  assert.equal(NXData.numberLiteral(parsed.integers,'5',Infinity),input.huge);
  assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype),original);
}
''', raws=[json.dumps(value, ensure_ascii=False),
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))], variants=variants,
               huge=str(10**400))


def test_number_evidence_is_container_bound_atomic_and_not_invented_for_edited_values():
    javascript(r'''
const parsed=NXData.parseWire('{"x":1.0,"items":[1.25,-0.0],"__proto__":{"n":2.5}}');
assert.equal(NXUpgradePlan.hash(parsed),digest('{'+'"__proto__": {"n": 2.5}, "items": [1.25, -0.0], "x": 1.0}'));
const clone=value=>require('node:v8').deserialize(require('node:v8').serialize(value));
for(const unbound of [{x:1.25},clone(parsed),JSON.parse(JSON.stringify(parsed)),{x:Infinity},{x:NaN},{x:9007199254740992}]){
  assert.throws(()=>NXUpgradePlan.hash(unbound),/exakt unterstützten JSON-Wert/);
}
assert.equal(NXUpgradePlan.hash({x:1}),digest('{'+'"x": 1}'));
parsed.items[0]=2.25;assert.equal(NXData.numberLiteral(parsed.items,'0',2.25),null);
assert.throws(()=>NXUpgradePlan.hash(parsed),/exakt unterstützten JSON-Wert/);
const fresh={x:1.25,private_text:'synthetic-private-content'};
for(const raw of [
 '{"x":1.25,"private_text":"wrong"}',
 '{"x":1.25,"private_text":"synthetic-private-content"} trailing',
 '{"x":1.25,"x":1.25,"private_text":"synthetic-private-content"}',
 '{"x":1.25}', '{"x":2.25,"private_text":"synthetic-private-content"}',
 '{"x":1.25,"private_text":"synthetic-private-content","extra":true}'
]){
 assert.throws(()=>NXData.bindWire(fresh,raw),error=>/JSON-Zahlennachweis/.test(error.message)&&!/synthetic-private-content|wrong/.test(error.message));
 assert.equal(NXData.numberLiteral(fresh,'x',1.25),null); // No evidence from a partially matched response.
}
NXData.bindWire(fresh,'{"x":1.25,"private_text":"synthetic-private-content"}');
assert.equal(NXData.numberLiteral(fresh,'x',1.25),'1.25');
NXData.set(fresh,'x',1.25);assert.equal(NXData.numberLiteral(fresh,'x',1.25),null);
NXData.bindWire(fresh,'{"x":1.25,"private_text":"synthetic-private-content"}');
NXData.remove(fresh,'x');fresh.x=1.25;assert.equal(NXData.numberLiteral(fresh,'x',1.25),null);
NXData.bindWire(fresh,'{"x":1.25,"private_text":"synthetic-private-content"}');
fresh.x='text';NXData.bindWire(fresh,'{"x":"text","private_text":"synthetic-private-content"}');
fresh.x=1.25;assert.equal(NXData.numberLiteral(fresh,'x',1.25),null);
const alias={x:1};assert.throws(()=>NXData.bindWire([alias,alias],'[{"x":1.0},{"x":1}]'));
for(const raw of ['{"x":NaN}','{"x":Infinity}','{"x":01}','{"x":1e}','{"x":1.}','{"x":1,}','[1,]','{"x":1,"x":2}'])assert.throws(()=>NXData.parseWire(raw));
let deep='1.25';for(let i=0;i<10000;i++)deep='['+deep+']';
const nested=NXData.parseWire(deep);assert.throws(()=>NXUpgradePlan.hash(nested),/JSON-Strukturgrenze/);
''')


def test_numbered_reader_enforces_bound_and_cleans_up_on_mismatch_or_reader_failure():
    javascript(r'''
const read=new Function(input.numbered+'\nreturn readNumberedResponse;')(),encode=text=>new TextEncoder().encode(text);
function response(options={}){
 const state={reads:0,cancelled:0,originalCancelled:0,released:0,decoded:0};
 const data=options.data??encode('{"x":1.0}');
 const result={clone(){if(options.cloneError)throw new Error('synthetic-private-clone');return {body:{getReader(){return {
  async read(){state.reads++;if(options.readError)throw new Error('synthetic-private-read');return state.reads===1?{done:false,value:data}:{done:true};},
  async cancel(){state.cancelled++;if(options.cancelError)throw new Error('synthetic-private-cancel');},releaseLock(){state.released++;}
 };}}};},body:{async cancel(){state.originalCancelled++;if(options.cancelError)throw new Error('synthetic-private-cancel');}},
 async json(){state.decoded++;if(options.jsonError)throw new Error('synthetic-private-json');return options.value??{x:1};}};
 return [result,state];
}
for(const options of [{value:{x:2}},{readError:true},{cloneError:true},{jsonError:true},{readError:true,cancelError:true},{data:'not bytes'},
 {data:new Uint8Array(64*1024*1024+1)}]){
 const [value,state]=response(options);
 await assert.rejects(()=>read(value,()=>true),error=>/vollständiger JSON-Zahlennachweis/.test(error.message)&&!/synthetic-private/.test(error.message));
 assert.equal(state.originalCancelled,1);assert.equal(state.cancelled,options.cloneError?0:1);assert.equal(state.released,options.cloneError?0:1);
 if(options.data?.byteLength>64*1024*1024)assert.equal(state.decoded,0);
}
const [late,state]=response();let checks=0;
await assert.rejects(()=>read(late,()=>++checks<=4),error=>NXRequests.isStale(error));
assert.equal(state.decoded,1);assert.equal(state.originalCancelled,1);assert.equal(state.released,1);
''')


def test_actual_wire_stringifier_and_copy_keep_python_numeric_values_and_json_formatting():
    value = {'float': 1.0, 'negative_zero': -0.0, 'integer': 2**53+1,
             'huge': 10**400, 'small': 1e-7, 'scientific': 1e20,
             '__proto__': {'constructor': [1.25, 1.0, -0.0, None, True, '1.0']}}
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False)
    javascript(r'''
const source=NXData.parseWire(input.raw),original=Object.getOwnPropertyDescriptors(Object.prototype);
for(const gap of [0,2,4,'  ']){
  const text=NXData.stringifyWire(source,gap),copy=NXData.parseWire(text);
  assert.equal(NXUpgradePlan.hash(copy),input.sha);
  if(gap===2)assert.equal(text,input.pretty);
}
const copy=NXData.cloneWire(source);assert.notEqual(source,copy);assert.notEqual(source.__proto__,copy.__proto__);
assert.equal(NXUpgradePlan.hash(copy),input.sha);
assert.equal(NXData.stringifyWire(copy),NXData.stringifyWire(source));
source.__proto__.constructor[0]=2.25;assert.equal(copy.__proto__.constructor[0],1.25);
assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype),original);
for(const value of [null,true,'text',42,[null,'text',12],{x:null,optional:undefined,array:[undefined,,false]}]){
  for(const gap of [0,2,12,'--'])assert.equal(NXData.stringifyWire(value,gap),JSON.stringify(value,null,gap));
}
const alias={n:1.25},aliases=NXData.cloneWire([alias,alias]);assert.notEqual(aliases[0],aliases[1]);
assert.equal(NXData.stringifyWire({fraction:1.25}),'{'+'"fraction":1.25}');
const removed=NXData.parseWire('{"0":1.0}');NXData.remove(removed,0);removed['0']=1;
assert.equal(NXData.stringifyWire(removed),'{'+'"0":1}');
''', raw=json.dumps(value, ensure_ascii=False), pretty=json.dumps(value, ensure_ascii=False, indent=2),
               sha=hashlib.sha256(canonical.encode()).hexdigest())


def test_wire_stringifier_rejects_lost_proof_nonfinite_overflow_underflow_and_unsafe_values():
    javascript(r'''
const rejected=[{n:Infinity},{n:-Infinity},{n:NaN},{n:9007199254740992},{n:1e20},{n:1n},{n:new Date()},
 NXData.parseWire('{"n":1e400}'),NXData.parseWire('{"n":1e-400}'),NXData.parseWire('{"n":-1e-400}')];
const cyclic={};cyclic.self=cyclic;rejected.push(cyclic);
for(const value of rejected){value.private='synthetic-private-content';assert.throws(()=>NXData.stringifyWire(value),
 error=>/nicht sicher darstellbare Werte/.test(error.message)&&!/synthetic-private-content/.test(error.message));}
for(const raw of ['{"n":-0.0}','{"n":0e-400}','{"n":-0e9999}','{"n":5e-324}'])assert.equal(NXData.stringifyWire(NXData.parseWire(raw)),raw);
assert.throws(()=>NXData.parseWire('{"private":"synthetic-private-content",broken'),error=>/JSON-Text/.test(error.message)&&!/synthetic-private-content/.test(error.message));
const rounded=JSON.parse('{"n":9007199254740993}');assert.throws(()=>NXData.cloneWire(rounded),/nicht sicher darstellbare Werte/);
let deep={};let cursor=deep;for(let index=0;index<600;index++){cursor.child={};cursor=cursor.child;}
assert.throws(()=>NXData.stringifyWire(deep),/Strukturgrenze/);
assert.throws(()=>NXData.stringifyWire(Array(2000000).fill(null)),/Strukturgrenze/);
assert.throws(()=>NXData.stringifyWire({n:'x'.repeat(64*1024*1024)}),/Byte-Grenze/);
''')
