"""Export metadata is not filesystem presence, activation, or CAM qualification."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_actual_export_evidence_classifier_handles_all_export_families_and_partial_proofs():
    candidates = [shutil.which('node'), r'C:\Program Files\nodejs\node.exe', '/mnt/c/Program Files/nodejs/node.exe']
    node = next((value for value in candidates if value and Path(value).is_file()), None)
    if not node:
        pytest.skip('JavaScript runtime unavailable.')
    source = (Path(__file__).parents[1] / 'nxserver/web/app.js').read_text(encoding='utf-8')
    assert 'function resourceExportEvidence(' in source
    data = source.split('globalThis.NXRequests=')[0]
    classifier = source[source.index('const exportEvidenceLabels='):source.index('function renderResources()')]
    script = r'''
const assert=require('node:assert/strict');
new Function(DATA)();
const classify=new Function(CLASSIFIER+';return resourceExportEvidence;')();
const sha='a'.repeat(64),id='11111111-1111-4111-8111-111111111111';
const resource={id,revision:3,kind:'holder',cam_status:'approved',cam:{library_id:'holder-metric'},file_package:null};
const release={id:'22222222-2222-4222-8222-222222222222',status:'draft',nx_version:'2512',catalog:{}};
const native={revision:3,library_id:'holder-metric',key:'CUSTOM',records_hash:sha};
const file={revision:3,nx_version:'2512',entries:[{path:'MACH/custom/model.prt',size:10,sha256:sha}]};
const make=(family,value)=>({...release,catalog:{[family]:{[id]:value}}});
assert.equal(classify(resource,null).state,'unselected');
assert.equal(classify(resource,release).state,'no_record');
assert.equal(classify(resource,make('exported_native',native)).state,'current');
assert.equal(classify(resource,make('exported_native',{...native,revision:2})).state,'older');
assert.equal(classify({...resource,cam_status:'draft'},make('exported_native',{...native,revision:2})).state,'older');
assert.equal(classify(resource,make('exported_native',{...native,revision:4})).state,'review');
for(const value of [null,{},[],{...native,revision:'3'},{...native,records_hash:'bad'},{...native,library_id:'tool-metric'}])assert.equal(classify(resource,make('exported_native',value)).state,'review');
for(const status of ['building','failed','blocked'])assert.equal(classify(resource,{...make('exported_native',native),status}).state,'review');
assert.equal(classify(resource,{...release,catalog:{resources:[resource]}}).state,'no_record');
assert.equal(classify({...resource,origin:{release_id:release.id,editable:false}},release).state,'origin');
assert.equal(classify({...resource,origin:{release_id:'other'}},release).state,'no_record');
const tool={...resource,kind:'tool',cam:{class_name:'MILL',values:{LIBRF:'CUSTOM'}}};
assert.equal(classify(tool,make('exported_tools',{...tool,records_hash:sha})).state,'current');
assert.equal(classify(tool,make('exported_tools',{...tool,id:'other',records_hash:sha})).state,'review');
const packaged={...resource,kind:'template',cam:{},file_package:{entries:[{path:file.entries[0].path,asset_id:'asset'}]}};
assert.equal(classify(packaged,make('exported_file_packages',file)).state,'current');
const retired=classify({...packaged,cam_status:'retired'},make('exported_file_packages',{...file,entries:[]}));
assert.equal(retired.state,'current');assert.match(retired.text,/kein Paketinhalt/);
for(const change of [{nx_version:'2406'},{qualification_retained:false},{exclusions:[{path:'MACH/custom/old.prt',reason:'Omitted during upgrade'}]},{entries:[]},{entries:[{...file.entries[0],size:-1}]},{entries:[file.entries[0],file.entries[0]]}])assert.equal(classify(packaged,make('exported_file_packages',{...file,...change})).state,'review');
const combined={...resource,file_package:packaged.file_package};
assert.equal(classify({...combined,cam_status:'retired'},make('exported_file_packages',{...file,entries:[]})).state,'current');
assert.equal(classify(combined,make('exported_native',native)).state,'review');
assert.equal(classify(combined,{...release,catalog:{exported_native:{[id]:native},exported_file_packages:{[id]:file}}}).state,'current');
assert.equal(classify(combined,{...release,catalog:{exported_native:{[id]:native},exported_file_packages:{[id]:{...file,revision:2}}}}).state,'review');
assert.equal(classify(combined,{...release,catalog:{exported_native:{[id]:{...native,revision:2}},exported_file_packages:{[id]:{...file,revision:2}}}}).state,'older');
assert.equal(classify(resource,{...release,catalog:{exported_native:Object.create({[id]:native})}}).state,'review');
const immutable={resource:combined,release:{...release,catalog:{exported_native:{[id]:native},exported_file_packages:{[id]:file}}}};
const before=JSON.stringify(immutable);classify(immutable.resource,immutable.release);assert.equal(JSON.stringify(immutable),before);
console.log('Export evidence states and independent native/file-package revisions verified.');
'''.replace('DATA', json.dumps(data)).replace('CLASSIFIER', json.dumps(classifier))
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Export evidence states' in result.stdout
