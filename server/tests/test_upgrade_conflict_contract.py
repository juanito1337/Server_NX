"""Execute the real browser plan validator on backend-produced file conflicts."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nxserver.libraries import SPECS
from test_upgrades import upgrade_pilot, preview, write


def test_file_comparison_requires_complete_consistent_hash_evidence_even_after_rehash(upgrade_pilot):
    client, store, request, source = upgrade_pilot
    relative = 'MACH/custom/conflict.txt'
    for root, text in [(store.standards['2406'], 'Old'), (store.standards['2512'], 'New'), (source, 'Customer')]:
        write(root, relative, text)
    job, report = preview(client, request)
    candidates = [shutil.which('node'), r'C:\Program Files\nodejs\node.exe', '/mnt/c/Program Files/nodejs/node.exe']
    node = next((value for value in candidates if value and Path(value).is_file()), None)
    if not node: pytest.skip('JavaScript runtime unavailable.')
    web = Path(__file__).parents[1] / 'nxserver/web'
    script = '''const assert=require('node:assert/strict');
const holder={exports:{}};new Function('module','exports',VENDOR)(holder,holder.exports);globalThis.jsSHA=holder.exports;
new Function(DATA)();new Function(SOURCE)();const original=PACKET;
assert.equal(NXUpgradePlan.verify(original.report,original.job,original.request,original.specs),true);
const faults=[
 value=>delete value.customer_sha256,
 value=>delete value.old_sha256,
 value=>delete value.target_sha256,
 value=>value.customer_sha256='invalid',
 value=>value.old_sha256=12,
 value=>value.target_sha256=false,
 value=>value.old_sha256=value.target_sha256,
 value=>value.customer_sha256=value.old_sha256,
 value=>value.customer_sha256=value.target_sha256,
 value=>{value.old_sha256=null;value.target_sha256=null;},
 value=>value.path='../outside.txt'
];
for(let index=0;index<=faults.length;index++){
 const packet=JSON.parse(JSON.stringify(original)),value=packet.report.conflicts.find(value=>value.category==='file');
 if(index===faults.length)packet.report.conflicts.push({...value});else faults[index](value);
 const unsigned={...packet.report};delete unsigned.preview_hash;packet.report.preview_hash=NXUpgradePlan.hash(unsigned);
 packet.job.report_sha256=NXUpgradePlan.hash(packet.report,true);packet.job.summary.preview_hash=packet.report.preview_hash;
 packet.job.conflicts=packet.report.conflicts.length;
 assert.throws(()=>NXUpgradePlan.verify(packet.report,packet.job,packet.request,packet.specs),'Fault '+index);
}
console.log('Real file-conflict proof validator: valid backend plan plus 12 inconsistent rehashed plans checked.');'''
    values = {'VENDOR': (web / 'vendor/jssha-3.3.2-sha256.js').read_text(encoding='utf-8'),
              'DATA': (web / 'app.js').read_text(encoding='utf-8').split('globalThis.NXRequests=')[0],
              'SOURCE': (web / 'upgrades.js').read_text(encoding='utf-8').split('globalThis.NXUpgrade=')[0],
              'PACKET': {'report': report, 'job': job, 'request': job['request'], 'specs': list(SPECS.values())}}
    for name, value in values.items():
        encoded = json.dumps(value, ensure_ascii=True)
        script = script.replace(name, 'JSON.parse(' + json.dumps(encoded) + ')' if name == 'PACKET' else encoded)
    result = subprocess.run([node, '-'], input=script, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
