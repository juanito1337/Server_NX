"""Require a complete, source-bound set of software test shards before acceptance.

This audits existing local evidence only. It never starts NX or grants CAM approval.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

SERVER=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(SERVER))
from nxserver.safety import DomainError, offline_path, read_bounded_file
from tools.run_ci_tests import id_hash, suite_for


def require(condition,message):
    if not condition:raise DomainError(message)


def read(directory,name,limit):
    raw=read_bounded_file(offline_path(directory/name),max_bytes=limit)
    return raw,hashlib.sha256(raw).hexdigest()


def junit_outcomes(raw):
    require(b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(),'JUnit declarations are not permitted.')
    outcomes={}
    for node in ET.fromstring(raw).iter('testcase'):
        components=node.attrib['classname'].split('.')
        require(len(components)>=2 and components[0]=='tests','JUnit test identity is not a project test.')
        key='tests/'+components[1]+'.py::'+'::'.join(components[2:]+[node.attrib['name']])
        require(key not in outcomes,'Repeated JUnit test identity.')
        outcomes[key]=('error' if node.find('error') is not None else 'failed' if node.find('failure') is not None
                       else 'skipped' if node.find('skipped') is not None else 'passed')
    return outcomes


def audit(directories,expected=None):
    expected={'core':2,'browser':4} if expected is None else expected
    require(expected and all(name in ('core','browser','container') and type(count) is int and 1<=count<=32
        for name,count in expected.items()),'Explicit supported suites and shard counts are required.')
    groups={name:{} for name in expected};source=None;source_commit=None;all_ids=set();collection=None
    pins=[];suite_counts={name:Counter() for name in expected};skips=[]
    for value in directories:
        directory=offline_path(value);documents={};hashes={}
        for name,limit in [('plan.json',16*1024**2),('summary.json',8*1024**2),
                           ('junit.xml',64*1024**2),('events.jsonl',128*1024**2)]:
            documents[name],hashes[name]=read(directory,name,limit)
        plan=json.loads(documents['plan.json']);summary=json.loads(documents['summary.json'])
        suite=plan['suite'];index=plan['shard_index'];count=plan['shard_count']
        require(suite in expected and type(index) is int and type(count) is int and
            count==expected[suite] and 0<=index<count,'Unexpected suite or shard shape.')
        require(index not in groups[suite],'Repeated shard; reruns cannot fill missing groups.')
        require(all(summary.get(field)==plan.get(field) for field in ('suite','shard_index','shard_count')),
                'Plan and summary identify different shards.')
        require(summary.get('exit_code')==0 and type(summary['exit_code']) is int and
            summary.get('execution_complete') is True and summary.get('source_stable') is True and
            summary.get('collected_only') is False,'Run failed, was interrupted, collected only or changed source.')
        require(not any(summary.get(field) for field in ('missing','unexpected','collection_errors','infrastructure_errors')),
                'Incomplete execution or infrastructure errors remain.')
        source_info=plan['source'];files=source_info['files']
        digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
        require(files and source_info['sha256']==digest and summary['source_sha256']==digest,'Source manifest binding differs.')
        if source is None:source=digest;source_commit=source_info.get('commit')
        require(source==digest and source_commit==source_info.get('commit'),'Evidence mixes different source states.')
        if suite=='core':
            runtimes=plan.get('runtimes',{})
            require(runtimes==summary.get('runtimes') and
                re.fullmatch(r'v24\.\d+\.\d+',runtimes.get('node',{}).get('version','')) and
                runtimes.get('git',{}).get('version','').startswith('git version '),
                'Full core evidence requires explicit Node.js 24 and Git preflight bindings.')
        ids=plan['ids']
        require(isinstance(ids,list) and ids and len(ids)==len(set(ids))==plan['selected'] and
            id_hash(ids)==plan['selected_sha256'],'Selected test identities are incomplete or altered.')
        require(all((key.split('::',1)[0]=='tests/test_docker_integration.py' if suite=='container'
            else suite_for(key)==suite) for key in ids),'Test identity belongs to another suite.')
        outcomes=junit_outcomes(documents['junit.xml'])
        require(set(outcomes)==set(ids),'JUnit does not cover the complete selected set.')
        events={key:{} for key in ids}
        for line in documents['events.jsonl'].splitlines():
            event=json.loads(line);key=event['id'];phase=event['phase']
            require(key in events and phase in ('setup','call','teardown') and phase not in events[key],
                    'Unexpected or repeated event phase.')
            events[key][phase]=event['outcome']
        for key,outcome in outcomes.items():
            phases=events[key]
            require(phases.get('teardown')=='passed' and phases.get('setup') in ('passed','skipped') and
                'failed' not in phases.values(),'A test lacks successful teardown or has failing events.')
            expected_outcome='skipped' if 'skipped' in phases.values() else 'passed'
            require(outcome==expected_outcome and (outcome=='skipped' or phases.get('call')=='passed'),
                    'JUnit and incremental execution disagree.')
        counts={name:list(outcomes.values()).count(name) for name in ('passed','failed','error','skipped')}
        require(counts==summary['counts'],'JUnit and summary counts disagree.')
        require(not counts['failed'] and not counts['error'],'A test did not pass.')
        skipped=[item for item in summary['skips_and_failures'] if item['outcome']=='skipped']
        require({item['id'] for item in skipped}=={key for key,outcome in outcomes.items() if outcome=='skipped'},
                'Skipped cases require explicit reasons.')
        require(not any('runtime unavailable' in item['reason'].lower() or 'git cli not installed' in item['reason'].lower()
            for item in skipped),'Required runtime tests were skipped.')
        skips.extend(skipped);suite_counts[suite].update(outcomes.values());groups[suite][index]=plan
        if suite!='container':
            require(not all_ids.intersection(ids),'Test identity executed in multiple software shards.')
            all_ids.update(ids)
            identity=(plan['collected'],plan['collection_sha256'])
            if collection is None:collection=identity
            require(collection==identity,'Shards collected different tests.')
        pins.append({'directory':str(directory),'files':hashes})
    require(all(set(groups[name])==set(range(count)) for name,count in expected.items()),'Required test groups are missing.')
    if collection is not None:
        require(len(all_ids)==collection[0] and id_hash(all_ids)==collection[1],
                'The union of software shards does not match the full collection.')
    for suite,shards in groups.items():
        eligible=sorted(key for plan in shards.values() for key in plan['ids'])
        for index,plan in shards.items():
            require(plan['ids']==eligible[index::expected[suite]],'Shard partition differs from the deterministic selection.')
    return {'contract_version':1,'status':'passed','source_sha256':source,'commit':source_commit,
        'counts':{name:dict(counts) for name,counts in suite_counts.items()},'unique_software_cases':len(all_ids),
        'skips':skips,'evidence':pins,'nx_execution_verified':False,'complete_product_acceptance':False}


def main(arguments=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',action='append',required=True)
    parser.add_argument('--output',required=True,help='New local audit directory; existing evidence is never overwritten.')
    options=parser.parse_args(arguments)
    output=offline_path(options.output);output.mkdir(parents=True,exist_ok=False)
    try:result=audit(options.run_dir)
    except (DomainError,OSError,ValueError,KeyError,TypeError,ET.ParseError) as error:
        result={'contract_version':1,'status':'failed','message':str(error),
            'nx_execution_verified':False,'complete_product_acceptance':False}
    offline_path(output/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    suite=ET.Element('testsuite',name='complete software evidence',tests='1',failures=str(int(result['status']!='passed')))
    case=ET.SubElement(suite,'testcase',name='source-bound-complete-shards')
    if result['status']!='passed':ET.SubElement(case,'failure',message=result['message'])
    ET.ElementTree(suite).write(offline_path(output/'junit.xml'),encoding='utf-8',xml_declaration=True)
    print(json.dumps({'status':result['status'],'report':str(output),'complete_product_acceptance':False}))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':sys.exit(main())
