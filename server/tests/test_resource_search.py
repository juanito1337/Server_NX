"""Run the actual metadata-only catalog search on every registered library."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nxserver.libraries import SPECS


def test_actual_search_covers_all_libraries_full_rows_future_fields_and_is_read_only():
    candidates = [shutil.which('node'), r'C:\Program Files\nodejs\node.exe', '/mnt/c/Program Files/nodejs/node.exe']
    node = next((value for value in candidates if value and Path(value).is_file()), None)
    if not node: pytest.skip('JavaScript runtime unavailable.')
    source = (Path(__file__).parents[1] / 'nxserver/web/app.js').read_text(encoding='utf-8')
    data = source[source.index('globalThis.NXData='):source.index('globalThis.NXRequests=')]
    script = data + '\nconst specs=' + json.dumps(list(SPECS.values())) + r''';
const assert=require('node:assert/strict'),prototype=Object.getOwnPropertyDescriptors(Object.prototype);
NXResourceSearch.configure(specs);
for(const spec of specs){
 const item={id:'synthetic',name:'Display name',kind:spec.kind,erp:{number:'OLD_SNAPSHOT'},cam:{library_id:spec.id,nx_version:'2512',
  records:[{class_name:'CLASS_A',block_id:'CLASS_A:1',values:{NAME:'NAME_KEY',FUTURE_COLUMN:'Known Ä value'}}]}};
 const before=JSON.stringify(item),value=NXResourceSearch.describe(item);
 assert.equal(value.library,spec.id);assert.deepEqual(value.keys,['NAME_KEY']);assert.equal(value.rows,1);
 for(const query of [spec.id,'name_key','future_column','known ä value','class_a','old_snapshot'])assert.equal(value.search.includes(query),true,query);
 assert.equal(NXResourceSearch.label(item).includes('NAME_KEY'),true);assert.equal(JSON.stringify(item),before);
 assert.equal(NXResourceSearch.describe(item),value);
}
const item={id:'many-rows',name:'All rows',kind:'tool',cam:{library_id:'tool-metric',records:[]}};
for(let index=0;index<1005;index++)item.cam.records.push({class_name:'BIG',values:{LIBRF:'WHOLE_GROUP',FUTURE_COLUMN:'row'+index}});
assert.equal(NXResourceSearch.describe(item).search.includes('row1004'),true);assert.deepEqual(NXResourceSearch.describe(item).keys,['WHOLE_GROUP']);
const changed={...item,cam:{...item.cam,records:[{values:{LIBRF:'NEW_REVISION_KEY'}}]}};
assert.equal(NXResourceSearch.describe(changed).search.includes('new_revision_key'),true);
assert.equal(NXResourceSearch.describe(changed).search.includes('row1004'),false);
const old={id:'old',name:'Old tool',kind:'tool',cam:{class_name:'LEGACY_CLASS',values:{LIBRF:'LEGACY_KEY',DIA:'12.5'}}};
assert.equal(NXResourceSearch.describe(old).library,'__legacy_tool__');assert.equal(NXResourceSearch.describe(old).search.includes('12.5'),true);
const free={id:'free',name:'Free gauge',kind:'gauge',cam:{measured:Infinity,values:{LIBRF:'NOT_A_NATIVE_TOOL'}},asset_ids:['ATTACHMENT_PRIVATE'],current_erp:{number:'LIVE_ITEM'}};
assert.equal(NXResourceSearch.describe(free).library,'__no_native__');for(const query of ['infinity','attachment_private','live_item','not_a_native_tool'])assert.equal(NXResourceSearch.describe(free).search.includes(query),false);
const literal=JSON.parse('{"id":"literal","name":"Literal","kind":"tool","cam":{"library_id":"tool-metric","records":[{"values":{"LIBRF":"__proto__","constructor":"LITERAL_VALUE","OPTIONAL":""}}]}}');
assert.equal(NXResourceSearch.describe(literal).search.includes('literal_value'),true);
assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype),prototype);
const choices=NXResourceSearch.choices([],'future-custom-library');for(const spec of specs)assert.equal(Object.hasOwn(choices,spec.id),true);
assert.equal(Object.hasOwn(choices,'future-custom-library'),true);
assert.equal(NXResourceSearch.choices([old]).__legacy_tool__.includes('Bibliotheks-ID'),true);
NXResourceSearch.configure([]);assert.equal(Object.hasOwn(NXResourceSearch.choices([]),'tool-metric'),false);
console.log('Actual search: '+specs.length+' libraries, complete rows, new fields, legacy/non-native distinctions and unchanged source data.');
'''
    result = subprocess.run([node, '-'], input=script, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
