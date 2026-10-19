"""The old-report reader never manufactures current integrity or approval."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_real_historical_model_is_typed_bounded_read_only_and_separate_from_current_proof():
    candidates = [shutil.which('node'), r'C:\Program Files\nodejs\node.exe', '/mnt/c/Program Files/nodejs/node.exe']
    node = next((value for value in candidates if value and Path(value).is_file()), None)
    if not node: pytest.skip('JavaScript runtime unavailable.')
    source = (Path(__file__).parents[1] / 'nxserver/web/app.js').read_text(encoding='utf-8')
    data = source.split('globalThis.NXRequests=')[0]
    model = source[source.index('function historicalValidationCandidate('):source.index('\nlet historicalValidationView=')]
    script = r'''const assert=require('node:assert/strict');new Function(DATA)();
const read=new Function(MODEL+';return historicalValidationModel;')(),copy=value=>JSON.parse(JSON.stringify(value));
const base={id:'11111111-1111-4111-8111-111111111111',name:'Synthetic old stand',nx_version:'2512',folder:'local/fixture',host_folder:null,status:'blocked',
 validation:{integrity_passed:false,errors:['Old XML finding'],file_count:7,tool_count:3,functional_validation_required:true,checked_at:'2026-10-06T11:23:22.642276+00:00'}};
const before=JSON.stringify(base),view=read(base);assert.equal(JSON.stringify(base),before);
assert.equal(view.historical,true);assert.equal(view.read_only,true);
for(const key of ['current_validation_confirmed','files_verified','approval_confirmed'])assert.equal(view[key],false);
assert.equal(view.rows[0].detail,'Old XML finding');assert.equal(view.files,7);
for(const mutate of [
 v=>v.id='wrong',v=>v.nx_version=2512,v=>v.folder='',v=>v.status='failed',
 v=>v.validation.errors=[null],v=>v.validation.errors=[' '],v=>v.validation.integrity_passed=true,
 v=>v.validation.file_count=true,v=>v.validation.tool_count='3',v=>v.validation.functional_validation_required=false,
 v=>v.validation.checked_at='2026-02-30T12:00:00Z',v=>v.validation.checked_at='1900-02-29T12:00:00Z',
 v=>v.validation.checked_at='2026-10-06T24:00:00Z',v=>v.validation.checked_at='2026-10-06T12:00:00+24:00',
 v=>v.validation.approval_retained=false,v=>v.validation.approval_requires_requalification=false,v=>v.validation.review_invalidation_reasons=[]
]){const value=copy(base);mutate(value);assert.throws(()=>read(value));}
for(const stamp of ['2024-02-29T12:00:00Z','2000-02-29T12:00:00+01:00','2026-10-06T12:00:00.123456789-01:00']){const value=copy(base);value.validation.checked_at=stamp;assert.equal(read(value).checked_at,stamp);}
const empty=copy(base);empty.status='validated';empty.validation.errors=[];empty.validation.integrity_passed=true;
const old=read(empty);assert.equal(old.rows.length,0);assert.equal(old.current_validation_confirmed,false);assert.equal(old.approval_confirmed,false);
const relational=copy(base);relational.validation.errors=[JSON.stringify({library:'tool-metric',field:'MATREF',key:'CUSTOM',value:'MISSING',target:'tool_material',reason:'Old missing reference'})];
assert.equal(read(relational).rows[0].field,'MATREF');assert.match(read(relational).rows[0].detail,/MISSING/);
const unsupported=copy(base);unsupported.validation.errors=Array(100001).fill('Same');assert.throws(()=>read(unsupported));
console.log('Actual historical reader preserves original data and never upgrades old evidence into current qualification.');'''
    script = script.replace('DATA', json.dumps(data)).replace('MODEL', json.dumps(model))
    result = subprocess.run([node, '-'], input=script, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
