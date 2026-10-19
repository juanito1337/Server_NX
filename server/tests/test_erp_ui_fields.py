"""Actual ERP GUI field predicates agree with server Unicode scalar lengths."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_actual_shared_erp_field_predicate_handles_unicode_limits_and_invalid_surrogates():
    node=next((value for value in (shutil.which('node'),r'C:\Program Files\nodejs\node.exe','/mnt/c/Program Files/nodejs/node.exe') if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    source=(Path(__file__).parents[1]/'nxserver/web/erp.js').read_text(encoding='utf-8')
    fields=source[source.index('  const fields='):source.index('\n',source.index('  const fields='))]
    predicates=source[source.index('  const fieldLength='):source.index('  function validArticle(')]
    script='const assert=require("node:assert/strict");const record=value=>value!==null&&typeof value==="object"&&!Array.isArray(value);\n'+fields+predicates+r'''
    const base=Object.fromEntries(Object.keys(fields).map(name=>[name,'valid']));assert.equal(validFields(base),true);
    for(const [name,max] of Object.entries(fields)){
      for(const text of ['a'.repeat(max),'🔧'.repeat(max),'A'+'🔩'.repeat(max-1)])assert.equal(validFields({...base,[name]:text}),true);
      for(const text of ['a'.repeat(max+1),'🔧'.repeat(max+1),'bad\ud800','bad\udfff'])assert.equal(validFields({...base,[name]:text}),false);
      assert.equal(validFields({...base,[name]:''}),name==='unit');
      assert.equal(validFields({...base,[name]:null}),false);
    }
    assert.equal(validFields({...base,extra:'not supported'}),false);
    assert.equal(validFields({...base,description:'🔧'.repeat(150),source_revision:'🔩'.repeat(70)}),true);
    assert.equal(fieldLength('A🔧B'),3);assert.equal(fieldLength('\ud800'),-1);
    console.log('Actual ERP UI Unicode field assertions passed.');
    '''
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Actual ERP UI Unicode field assertions passed.' in result.stdout
