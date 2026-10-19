"""Malformed matching metadata must not pass by JavaScript string coercion."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('defect', ['schema_hash_list', 'id_list', 'version_list', 'host_object', 'empty_field', 'short_date', 'empty_error', 'invalid_calendar', 'missing_timezone'])
def test_actual_reader_rejects_matching_but_invalid_metadata_types(defect):
    node = next((value for value in (shutil.which('node'), r'C:\Program Files\nodejs\node.exe',
                                    '/mnt/c/Program Files/nodejs/node.exe') if value and Path(value).is_file()), None)
    if not node:
        pytest.skip('JavaScript runtime unavailable.')
    source = (Path(__file__).parents[1] / 'nxserver/web/app.js').read_text(encoding='utf-8')
    data = source.split('globalThis.NXRequests=')[0]
    model = source[source.index('function releaseValidationModel('):source.index('\nlet releaseValidationView=')]
    script = r'''
const assert=require('node:assert/strict');new Function(DATA)();
const read=new Function(MODEL+';return releaseValidationModel;')();
const report={integrity_passed:true,errors:[],file_count:1,tool_count:1,functional_validation_required:true,
 checked_at:'2026-10-15T14:00:00.123456+00:00',approval_retained:false,approval_requires_requalification:false,review_invalidation_reasons:[],
 libraries:[{id:'tool-metric',label:'Werkzeug',path:'MACH/tool_database.dat',row_count:1,object_count:1,errors:[],
 schemas:[{id:'MILL:1',class_name:'MILL',fields:['LIBRF','MATREF'],schema_hash:'a'.repeat(64)}]}],
 references:{errors:[],warnings:[],warning_rows:0,automatic_geometry_validation:false}};
const release={id:'11111111-1111-4111-8111-111111111111',name:'Synthetic',nx_version:'2512',folder:'local/synthetic',host_folder:null,status:'validated',review:null};
const defect=DEFECT;
if(defect==='schema_hash_list')report.libraries[0].schemas[0].schema_hash=[report.libraries[0].schemas[0].schema_hash];
else if(defect==='id_list')release.id=[release.id];
else if(defect==='version_list')release.nx_version=[release.nx_version];
else if(defect==='host_object')release.host_folder={path:'not text'};
else if(defect==='empty_field')report.libraries[0].schemas[0].fields.push('');
else if(defect==='short_date')report.checked_at='1';
else if(defect==='empty_error'){report.errors=[''];report.integrity_passed=false;release.status='blocked';}
else if(defect==='invalid_calendar')report.checked_at='2026-02-30T14:00:00+00:00';
else if(defect==='missing_timezone')report.checked_at='2026-10-15T14:00:00';
else throw new Error('Unknown isolated mutation');
release.validation=JSON.parse(JSON.stringify(report));
assert.throws(()=>read(release,report),/Kein vollständiger passender technischer Prüfbericht/);
console.log('Strict matching metadata rejection verified: '+defect);
'''.replace('DATA', json.dumps(data)).replace('MODEL', json.dumps(model)).replace('DEFECT', json.dumps(defect))
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, encoding='utf-8', timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Strict matching metadata rejection verified' in result.stdout
