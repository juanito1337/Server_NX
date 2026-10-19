"""Run the actual GUI page validator against native parser output; no share/NX."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from nxserver.libraries import SPECS, NativeTable, digest, native_cam


def test_actual_page_contract_all_adapters_and_malformed_groups():
    node = next((value for value in (shutil.which('node'), r'C:\Program Files\nodejs\node.exe',
        '/mnt/c/Program Files/nodejs/node.exe') if value and Path(value).is_file()), None)
    if not node:
        pytest.skip('JavaScript runtime unavailable.')
    web = Path(__file__).parents[1] / 'nxserver/web'
    app = (web / 'app.js').read_text(encoding='utf-8')
    native = (web / 'native.js').read_text(encoding='utf-8')
    upgrade = (web / 'upgrades.js').read_text(encoding='utf-8')
    helpers = app[app.index('globalThis.NXData='):app.index('globalThis.NXRequests=')]
    hashes = upgrade[upgrade.index('globalThis.NXUpgradePlan='):upgrade.index('  const same=')] + 'return {hash};})();\n'
    predicates = native[native.index('  const schemaHex='):native.index('\n', native.index('  const schemaHex='))]
    validator = native[native.index('  function checkObjectPage('):native.index('  async function loadNative(')]
    # One group uses only the third occurrence, another spans all blocks. New
    # columns and prototype-shaped names are actual parser data, not a map API.
    table = NativeTable('#CLASS SAME\nFORMAT LIBRF RTYPE\nDATA |A|1\n'
        '#CLASS SAME\nFORMAT NAME EXTRA\nDATA |A|new column\n'
        '#CLASS SAME\nFORMAT LIBRF constructor __proto__\nDATA |A|literal|text\n'
        'DATA |__proto__|literal|prototype key\n#END_DATA\n')
    pages = []
    for spec in SPECS.values():
        pages.append({**spec, 'nx_version': '2512', 'schemas': table.blocks,
            'total': 2, 'filtered': 2, 'offset': 0, 'limit': 100,
            'input_sha256': digest(table.text), 'manifest_sha256': digest([]),
            'integrity_checked': True, 'scope': 'selected_native_library_bytes',
            'automatic_approval': False, 'items': [{'key': key,
                'cam': native_cam(spec['id'], '2512', table, rows)}
                for key, rows in table.groups().items()]})
    script = r'''
    const assert=require('node:assert/strict'),crypto=require('node:crypto');
    const structuredClone=value=>JSON.parse(JSON.stringify(value));
    let incoming='';process.stdin.setEncoding('utf8');process.stdin.on('data',chunk=>incoming+=chunk);
    process.stdin.on('end',()=>{const input=JSON.parse(incoming);
    globalThis.jsSHA=class{constructor(){this.value=crypto.createHash('sha256');}update(bytes){this.value.update(bytes);}getHash(){return this.value.digest('hex');}};
    eval(input.helpers);eval(input.hashes);
    const releases=[{id:'synthetic',nx_version:'2512'}],creationSpecs=new Map(input.pages.map(page=>[page.id,page]));
    const check=eval(input.predicates+'\n'+input.validator+'\ncheckObjectPage;');
    const validate=value=>check(value,'synthetic',value.id,0),base=input.pages[0];
    for(const page of input.pages){const before=JSON.stringify(page);validate(page);assert.equal(JSON.stringify(page),before);}
    let negative=0;function reject(change){const value=structuredClone(base);change(value);assert.throws(()=>validate(value));negative++;}
    for(const field of ['kind','label','path','legacy_path','graphics_dir','nx_version','scope','input_sha256','manifest_sha256'])reject(value=>value[field]='wrong');
    for(const field of ['total','filtered','offset','limit'])for(const number of [-1,1.5,null,true,'2',Number.MAX_SAFE_INTEGER+1])reject(value=>value[field]=number);
    for(const flag of ['integrity_checked','automatic_approval'])reject(value=>value[flag]=!value[flag]);
    reject(value=>value.filtered=3);reject(value=>value.total=0);reject(value=>value.filtered=0);
    reject(value=>value.items.pop());reject(value=>value.items.push(structuredClone(value.items[0])));
    reject(value=>value.items[1].key=value.items[0].key);reject(value=>value.items[0].key='');
    for(const field of ['items','schemas'])reject(value=>value[field]=null);
    for(const part of [null,{},[]])reject(value=>value.items[1]=part);
    for(const part of [null,{},[]])reject(value=>value.items[1].cam=part);
    for(const field of ['library_id','nx_version'])reject(value=>value.items[1].cam[field]='wrong');
    for(const field of ['schemas','records'])for(const part of [null,{},[]])reject(value=>value.items[1].cam[field]=part);
    reject(value=>value.schemas[1].id='SAME:4');reject(value=>value.schemas[1].id='SAME:1');
    reject(value=>value.schemas[1].schema_hash='0'.repeat(64));
    reject(value=>value.schemas[1].fields[0]='MISSING');
    reject(value=>value.schemas[1].fields.push('EXTRA'));
    reject(value=>value.items[1].cam.schemas[0].schema_hash='0'.repeat(64));
    reject(value=>value.items[1].cam.schemas[0].fields.reverse());
    reject(value=>value.items[1].cam.schemas.push(structuredClone(value.schemas[0])));
    reject(value=>value.items[0].cam.schemas.reverse());
    reject(value=>value.items[1].cam.records[0].block_id='SAME:1');
    reject(value=>value.items[1].cam.records[0].class_name='OTHER');
    reject(value=>value.items[1].cam.records[0].values.LIBRF='OTHER');
    reject(value=>delete value.items[1].cam.records[0].values.constructor);
    reject(value=>value.items[1].cam.records[0].values.constructor=42);
    reject(value=>value.items[1].cam.records[0].values.extra='not declared');
    reject(value=>value.items[1].cam.records[0].values=null);
    reject(value=>value.items[0].cam.extra='unsupported native envelope');
    const large=structuredClone(base);large.items[0].cam.records.push(...Array.from({length:1200},()=>structuredClone(large.items[0].cam.records[0])));
    large.items[0].cam.records[0].values.RTYPE='non-numeric preserved '+ '🔧'.repeat(4097);validate(large);
    const empty=structuredClone(base);empty.items=[];empty.total=empty.filtered=0;validate(empty);
    const noSchema=structuredClone(empty);noSchema.schemas=[];validate(noSchema);
    const last=structuredClone(base);last.offset=200;last.filtered=last.total=201;last.items=[last.items[0]];check(last,'synthetic',last.id,200);
    const sample=structuredClone(base);sample.limit=1;sample.items=[sample.items[0]];check(sample,'synthetic',sample.id,0,1);
    assert.throws(()=>check(sample,'synthetic',sample.id,0));
    assert.throws(()=>check(base,'synthetic',base.id,0,1));
    for(const limit of [0,-1,1.5,501,Number.MAX_SAFE_INTEGER+1,null,true,'1'])assert.throws(()=>check(sample,'synthetic',sample.id,0,limit));
    for(const offset of [-1,1.5,Number.MAX_SAFE_INTEGER+1,null,true,'0'])assert.throws(()=>check(sample,'synthetic',sample.id,offset,1));
    const long=structuredClone(base),key='K'.repeat(1025);long.items[1].key=key;long.items[1].cam.records[0].values.LIBRF=key;validate(long);
    assert.equal(({}).polluted,undefined);
    console.log('Page contract: '+input.pages.length+' adapters; '+negative+' malformed pages rejected; complete long groups preserved.');
    });
    '''
    result = subprocess.run([node, '-e', script], input=json.dumps(dict(helpers=helpers, hashes=hashes,
        predicates=predicates, validator=validator, pages=pages), ensure_ascii=False),
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'Page contract: {len(SPECS)} adapters;' in result.stdout
