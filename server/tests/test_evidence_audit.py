"""Acceptance rejects missing, stale or mutually inconsistent test evidence."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from nxserver.safety import DomainError
from tools.audit_test_evidence import audit, main
from tools.run_ci_tests import id_hash


@pytest.fixture
def cohort(tmp_path):
    ids=['tests/test_small.py::test_one','tests/test_small.py::test_two',
         'tests/test_small_browser.py::test_one','tests/test_small_browser.py::test_two']
    files={'server/nxserver/example.py':hashlib.sha256(b'inert source fixture').hexdigest()}
    source={'files':files,'sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),'commit':'a'*40}
    directories=[]
    for suite,eligible in [('core',ids[:2]),('browser',ids[2:])]:
        for index in range(2):
            directory=tmp_path/(suite+str(index));directory.mkdir();directories.append(directory)
            key=eligible[index];runtimes={'node':{'version':'v24.19.0'},'git':{'version':'git version 2.49.0'}} if suite=='core' else {}
            plan={'suite':suite,'shard_index':index,'shard_count':2,'ids':[key],'selected':1,'selected_sha256':id_hash([key]),
                  'collected':4,'collection_sha256':id_hash(ids),'source':source,'runtimes':runtimes}
            summary={'suite':suite,'shard_index':index,'shard_count':2,'exit_code':0,'execution_complete':True,
                'source_stable':True,'source_sha256':source['sha256'],'collected_only':False,'missing':[],
                'unexpected':[],'collection_errors':[],'infrastructure_errors':[],
                'counts':{'passed':1,'failed':0,'error':0,'skipped':0},'skips_and_failures':[],'runtimes':runtimes}
            for name,value in [('plan',plan),('summary',summary)]:
                (directory/(name+'.json')).write_text(json.dumps(value))
            events=[{'id':key,'phase':phase,'outcome':'passed'} for phase in ('setup','call','teardown')]
            (directory/'events.jsonl').write_text('\n'.join(map(json.dumps,events)))
            root=ET.Element('testsuite');module,name=key.split('::')
            ET.SubElement(root,'testcase',classname=module[:-3].replace('/','.'),name=name)
            ET.ElementTree(root).write(directory/'junit.xml')
    return directories


def change(directory,name,**fields):
    path=directory/(name+'.json');value=json.loads(path.read_text());value.update(fields);path.write_text(json.dumps(value))


def test_complete_partition_binds_all_evidence_but_never_claims_nx_acceptance(cohort):
    result=audit(cohort,{'core':2,'browser':2})
    assert result['status']=='passed' and result['unique_software_cases']==4
    assert result['counts']=={'core':{'passed':2},'browser':{'passed':2}}
    assert len(result['evidence'])==4 and all(len(item['files'])==4 for item in result['evidence'])
    assert not result['nx_execution_verified'] and not result['complete_product_acceptance']


@pytest.mark.parametrize('defect',['missing_group','duplicate_group','different_source','changed_source','failed',
    'incomplete','collect_only','missing_case','bad_hash','bad_counts','missing_runtime','missing_call',
    'missing_teardown','duplicate_event','bad_junit','different_collection','junit_failure'])
def test_partial_or_tampered_evidence_never_qualifies(cohort,defect):
    first=cohort[0]
    if defect=='missing_group':cohort.pop()
    elif defect=='duplicate_group':cohort.append(cohort[0])
    elif defect=='different_source':
        plan=json.loads((first/'plan.json').read_text());plan['source']['commit']='b'*40;change(first,'plan',source=plan['source'])
    elif defect=='changed_source':change(first,'summary',source_stable=False)
    elif defect=='failed':change(first,'summary',exit_code=1)
    elif defect=='incomplete':change(first,'summary',execution_complete=False)
    elif defect=='collect_only':change(first,'summary',collected_only=True)
    elif defect=='missing_case':change(first,'plan',ids=[])
    elif defect=='bad_hash':change(first,'plan',selected_sha256='0'*64)
    elif defect=='bad_counts':change(first,'summary',counts={'passed':2,'failed':0,'error':0,'skipped':0})
    elif defect=='missing_runtime':change(first,'plan',runtimes={})
    elif defect in ('missing_call','missing_teardown','duplicate_event'):
        path=first/'events.jsonl';events=path.read_text().splitlines()
        if defect=='duplicate_event':events.append(events[0])
        else:events.pop(1 if defect=='missing_call' else 2)
        path.write_text('\n'.join(events))
    elif defect=='bad_junit':(first/'junit.xml').write_text('<testsuite/>')
    elif defect=='different_collection':change(first,'plan',collection_sha256='b'*64)
    elif defect=='junit_failure':
        root=ET.parse(first/'junit.xml');ET.SubElement(root.getroot()[0],'failure');root.write(first/'junit.xml')
    with pytest.raises(DomainError):audit(cohort,{'core':2,'browser':2})


def test_default_acceptance_requires_two_core_and_four_browser_groups(cohort,tmp_path):
    output=tmp_path/'audit'
    assert main([argument for directory in cohort for argument in ('--run-dir',str(directory))]+['--output',str(output)])==1
    result=json.loads((output/'summary.json').read_text())
    assert result['status']=='failed' and not result['complete_product_acceptance']
    assert ET.parse(output/'junit.xml').find('.//failure') is not None


def test_audit_rejects_protected_inputs_before_first_lookup(monkeypatch):
    monkeypatch.setattr(Path,'lstat',lambda *a,**k:pytest.fail('Protected input cannot be looked up.'))
    with pytest.raises(DomainError):audit([r'W:\never-accessed'])
