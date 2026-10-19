"""Execute the actual technical-report view model without a browser or NX."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_actual_validation_view_model_checks_binding_shape_and_warning_counts():
    candidates = [shutil.which('node'), r'C:\Program Files\nodejs\node.exe', '/mnt/c/Program Files/nodejs/node.exe']
    node = next((value for value in candidates if value and Path(value).is_file()), None)
    if not node:
        pytest.skip('JavaScript runtime unavailable.')
    source = (Path(__file__).parents[1] / 'nxserver/web/app.js').read_text(encoding='utf-8')
    model = source[source.index('function releaseValidationModel('):source.index('\nlet releaseValidationView=')]
    data = source.split('globalThis.NXRequests=')[0]
    script = r'''
const assert=require('node:assert/strict');new Function(DATA)();
const read=new Function(MODEL+';return releaseValidationModel;')(),copy=value=>JSON.parse(JSON.stringify(value));
const base={integrity_passed:true,errors:[],file_count:1,tool_count:1,functional_validation_required:true,
 checked_at:'2026-10-15T14:00:00.123456+00:00',approval_retained:false,approval_requires_requalification:false,review_invalidation_reasons:[],
 libraries:[{id:'tool-metric',label:'Werkzeug',path:'MACH/tool_database.dat',row_count:1,object_count:1,errors:[],
 schemas:[{id:'MILL:1',class_name:'MILL',fields:['LIBRF','MATREF'],schema_hash:'a'.repeat(64)}]}],
 references:{errors:[],warnings:[],warning_rows:0,automatic_geometry_validation:false}};
const release=report=>({id:'11111111-1111-4111-8111-111111111111',name:'Synthetic stand',nx_version:'2512',folder:'local/synthetic',host_folder:null,
 status:report.integrity_passed?(report.approval_retained?'approved':'validated'):'blocked',review:report.approval_retained?{}:null,validation:copy(report)});
let report=copy(base),stand=release(report),before=JSON.stringify({stand,report});let view=read(stand,report);
assert.equal(view.passed,true);assert.equal(view.libraries,1);assert.equal(view.rows[0].kind,'library');assert.equal(view.tools,1);
assert.equal(JSON.stringify({stand,report}),before);
for(const checked_at of ['2026-10-15T14:00:00Z','2026-10-15T14:00:00.123456789+00:00','2024-02-29T16:00:00+02:00','2000-02-29T14:00:00-01:00']){const value=copy(base);value.checked_at=checked_at;assert.equal(read(release(value),value).checked_at,checked_at);}
for(const checked_at of ['1900-02-29T14:00:00Z','2026-10-15T25:00:00Z','2026-10-15T14:60:00Z','2026-10-15T14:00:00+24:00']){const value=copy(base);value.checked_at=checked_at;assert.throws(()=>read(release(value),value));}
stand.validation=Object.fromEntries(Object.entries(stand.validation).reverse());assert.equal(read(stand,report).files,1);
for(const mutate of [
 v=>delete v.file_count,v=>v.file_count=-1,v=>v.file_count=true,v=>v.tool_count='1',v=>v.functional_validation_required=false,
 v=>v.integrity_passed=false,v=>v.errors=[null],v=>v.checked_at='invalid',v=>v.approval_retained='true',v=>v.review_invalidation_reasons=null,
 v=>v.references.automatic_geometry_validation=true,v=>v.references.warning_rows=1,v=>v.references.errors=[{library:'tool',reason:'Missing'}],
 v=>v.libraries.push(copy(v.libraries[0])),v=>v.libraries.push({...copy(v.libraries[0]),id:'second'}),
 v=>v.libraries[0].row_count='1',v=>v.libraries[0].object_count=2,v=>v.libraries[0].schemas=[],
 v=>v.libraries[0].schemas[0].fields=['LIBRF','LIBRF'],v=>v.libraries[0].schemas[0].schema_hash='invalid',
 v=>v.libraries[0].schemas.push(copy(v.libraries[0].schemas[0])),v=>v.libraries[0].errors=['Unreadable'],
 v=>v.libraries[0].path=null,v=>v.references.warnings=[{}]
]){const value=copy(base);mutate(value);assert.throws(()=>read(release(value),value));}
for(const change of [{validation:null},{status:'approved'},{id:'wrong'},{nx_version:'bad'},{folder:''}])assert.throws(()=>read({...release(report),...change},report));
stand=release(report);stand.validation.checked_at='2026-10-14T14:00:00+00:00';assert.throws(()=>read(stand,report));
report=copy(base);report.tool_count=null;assert.equal(read(release(report),report).tools,null);
report.integrity_passed=false;report.errors=['Unreadable DAT'];report.libraries[0]={id:'tool-metric',label:'Werkzeug',path:'MACH/tool_database.dat',errors:['Unreadable DAT'],schemas:[]};
view=read(release(report),report);assert.equal(view.errors,1);assert.match(view.rows[0].detail,/Nicht lesbar/);
report=copy(base);report.references.warnings=[{library:'tool-metric',field:'MATREF',value:'MISSING',target:'tool_material',reason:'Missing library reference.',standard_object:true,affected_rows:3,example_keys:['A','B']}];report.references.warning_rows=3;
view=read(release(report),report);assert.equal(view.warnings,1);assert.equal(view.warning_rows,3);assert.equal(view.rows[1].kind,'warning');
for(const mutate of [v=>v.references.warning_rows=2,v=>v.references.warnings[0].standard_object=false,v=>v.references.warnings[0].affected_rows=0,v=>v.references.warnings[0].example_keys=null]){const value=copy(report);mutate(value);assert.throws(()=>read(release(value),value));}
report=copy(base);report.approval_retained=true;assert.equal(read(release(report),report).approval_retained,true);
assert.throws(()=>read({...release(report),review:null},report));report.approval_requires_requalification=true;assert.throws(()=>read(release(report),report));
report=copy(base);report.integrity_passed=false;report.errors=[JSON.stringify({library:'tool-metric',field:'MATREF',key:'CUSTOM',value:'MISSING',target:'tool_material',reason:'Missing library reference.'})];
report.references.errors=[JSON.parse(report.errors[0])];report.review_invalidation_reasons=['Repeat NX test'];
view=read(release(report),report);assert.equal(view.rows[1].target,'tool-metric');assert.equal(view.rows[1].path,'MATREF');assert.equal(view.rows[2].kind,'review');
report=copy(base);Object.defineProperty(report,'__proto__',{value:{literal:'<img src=x>'},enumerable:true});
const original=Object.getOwnPropertyDescriptors(Object.prototype);read(release(report),report);assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype),original);
console.log('Technical validation model binding, malformed reports, warnings and approval distinctions verified.');
'''.replace('DATA', json.dumps(data)).replace('MODEL', json.dumps(model))
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Technical validation model binding' in result.stdout
