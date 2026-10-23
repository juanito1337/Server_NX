"""Explicit local qualification; never launched by the GitHub workflow.

server: real HTTP + backup + restore, no NX or production paths.
nx: pinned test journals + import packages, requires a separate isolated account.
An automatic NX pass never certifies the accompanied CAM acceptance.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

SERVER=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(SERVER))
from nxserver.safety import DomainError
from nxserver.safety import tree_files,file_fingerprint
from tools.nxlab.guard import Blocked,account_sid,executable_version,isolation,local,nx_environment,writable_lab_root
from tools.nxlab.measurements import compare
from tools.nxlab.runtime import Server,persistence,run_owned,seed


def write(path,value):
    with local(path).open('x',encoding='utf-8') as stream:json.dump(value,stream,indent=2,ensure_ascii=False)


def source_state():
    root=local(SERVER.parent)
    git=local(r'C:\Program Files\Git\cmd\git.exe')
    if not git.is_file():raise Blocked('Git for Windows required to bind evidence to source.')
    environment={key:value for key,value in os.environ.items() if key.upper() in ('SYSTEMROOT','WINDIR','COMSPEC')}
    environment.update(PATH=str(git.parent)+os.pathsep+r'C:\Windows\System32',
        GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_ATTR_NOSYSTEM='1')
    def command(*args):return subprocess.check_output([str(git),'-C',str(root),*args],env=environment)
    files=command('ls-files','--cached','--others','--exclude-standard','-z').decode('utf-8').split('\0')
    pins={name:hashlib.sha256(local(root/name).read_bytes()).hexdigest() for name in sorted(set(files)) if name}
    return {'commit':command('rev-parse','HEAD').decode().strip(),
            'dirty':bool(command('status','--porcelain')), 'files':pins,
            'source_sha256':hashlib.sha256(json.dumps(pins,sort_keys=True).encode()).hexdigest()}


def evidence(directory,summary):
    write(directory/'summary.json',summary)
    suite=ET.Element('testsuite',name='NX local '+summary['stage'],tests=str(len(summary['cases'])))
    failures=errors=0
    for case in summary['cases']:
        node=ET.SubElement(suite,'testcase',name=case['id'],time=str(case.get('seconds',0)))
        status=case['status']
        if status in ('failed','blocked','incomplete','awaiting_cam'):
            kind='failure' if status=='failed' else 'error'
            ET.SubElement(node,kind,type=status,message=case.get('message',status))
            failures+=kind=='failure';errors+=kind=='error'
    suite.set('failures',str(failures));suite.set('errors',str(errors))
    ET.ElementTree(suite).write(local(directory/'junit.xml'),encoding='utf-8',xml_declaration=True)


def archive_to(content,directory):
    directory=local(directory);directory.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        for entry in package.infolist():
            if entry.is_dir() or not re.fullmatch(r'[A-Za-z0-9_.-]+',entry.filename):
                raise DomainError('Unexpected job package entry.')
            if entry.file_size>32*1024*1024:raise DomainError('Oversize job package entry.')
            local(directory/entry.filename).write_bytes(package.read(entry))
    return directory


def phase(directory,task,installation,environment):
    directory=local(directory);directory.mkdir(parents=True,exist_ok=False)
    journal=local(SERVER/'tools/nxlab/journal.py').read_bytes()
    local(directory/'journal.py').write_bytes(journal)
    task={**task,'journal_sha256':hashlib.sha256(journal).hexdigest()}
    write(directory/'task.json',task)
    if task['phase']=='create':
        definition=local(installation/'STEP214UG/ugstep214.def').read_text(encoding='utf-8-sig')
        if re.search(r'(?i)w:|\\\\|//',definition):raise Blocked('STEP export settings contain network references.')
        definition=re.sub(r'(?m)^LOG_FILE\s*=.*$','LOG_FILE = export.log',definition)
        local(directory/'export.def').write_text(definition,encoding='utf-8')
    run_owned([str(local(installation/'NXBIN/run_journal.exe')),str(directory/'journal.py'),
               '-args',str(directory/'task.json')],directory=directory,environment=environment)
    result=json.loads(local(directory/'result.json').read_text(encoding='utf-8'))
    if result.get('status')!='passed' or result.get('case')!=task['case']['id'] or result.get('phase')!=task['phase']:
        raise RuntimeError('Missing/failed/mismatched journal result.')
    if task['phase'] in ('create','measure'):
        root=directory if task['phase']=='create' else local(task['part']).parent
        pins={};total=0
        for name,path in tree_files(root):
            if path.suffix.lower() not in ('.prt','.step','.stp'):continue
            if len(pins)>=80:raise RuntimeError('Reference artifact set exceeds 80 files.')
            pin=file_fingerprint(local(path),max_bytes=20*1024*1024);total+=pin['size']
            if not pin['size'] or total>256*1024*1024:raise RuntimeError('Reference artifacts exceed the positive byte budget.')
            pins[name]=pin
        if not pins:raise RuntimeError('Successful geometry observation has no model artifacts.')
        result['artifacts']={'directory':str(root),'files':pins}
    return result


def installation_for(profile):
    if profile.get('schema_version')!=1 or profile.get('nx_version')!='2406':raise Blocked('Unsupported local profile.')
    installation=local(profile['installation'])
    if str(installation).replace('\\','/').casefold()!='c:/siemens/nx2406':raise Blocked('Only qualified NX2406 root is configured.')
    build=executable_version(installation/'NXBIN/ugraf.exe')
    if build!='2406.5001.0.0':raise Blocked('Unqualified NX build: '+build)
    return installation


def positive_case(work,case,installation,environment,specs,baseline,baseline_root,source):
    from tools.nxlab.cam_target import bind_handoff
    identifier=case['id']
    result={'id':case['id'],'status':'failed','directory':str(work)}
    task={'case':case,'nx_version':'2406'}
    created=phase(work/'create',{**task,'phase':'create'},installation,environment)
    reference_measurement=phase(work/'reopen',{**task,'phase':'measure','part':created['generated']['reference']},installation,environment)
    observed=reference_measurement['observed']
    errors=compare(case,observed,specs['tolerances'])
    if errors:raise AssertionError('Reopened reference differs: '+','.join(errors))
    with Server(work/'server',sources={'nxlab':baseline_root}) as server:
        release=server.request('/api/v1/releases','POST',{'source':'nxlab','nx_version':'2406',
            'name':'NX lab '+identifier+' empty native target'})
        resource,asset,job,package=seed(server,local(created['generated']['step']).read_bytes(),
            tool_type=case['tool_type'],name='NX lab '+identifier)
        package_dir=archive_to(package,work/'import')
        run_owned([str(local(installation/'NXBIN/run_journal.exe')),str(package_dir/'nx_step_import.py')],
                  directory=package_dir,environment=environment)
        report=json.loads(local(package_dir/'output/report.json').read_text(encoding='utf-8'))
        if report['status']!='needs_selection':raise AssertionError('STEP import did not preserve a usable assembly.')
        server.request('/api/v1/geometry-jobs/'+job['id']+'/report','POST',{'report':report})
        import_measurement=phase(work/'import-reopen',{**task,'phase':'measure','part':str(package_dir/'output/tool.prt')},
                       installation,environment)
        imported=import_measurement['observed']
        imported_errors=compare(case,imported,specs['tolerances'])
        # STEP translators may not preserve construction points. Such
        # observations stay explicitly unverified for CAM acceptance.
        unmeasured=[key for key in ('axis','mount_point','insert_direction') if key in imported_errors and key not in imported]
        actual_errors=[key for key in imported_errors if key not in unmeasured]
        if actual_errors:raise AssertionError('Reopened STEP import differs: '+','.join(actual_errors))
        collector=server.request('/api/v1/geometry-jobs/'+job['id']+'/collector-package','POST',
            {'root_library_id':'tool-metric','root_key':'NXTEST_'+identifier.upper()},binary=True)
        archive_to(collector,work/'collector')
        handoff=bind_handoff(case=case,source=source,baseline=baseline,release=release,
            resource=resource,job=job,collector=work/'collector',collector_sha256=hashlib.sha256(collector).hexdigest(),
            observed=observed,imported=imported)
        handoff.update(server_data=str(server.data),settings=str(local(work/'server/settings.local.json')))
        handoff['artifacts']={'created':created['artifacts'],'reopened':reference_measurement['artifacts'],
            'import_reopened':import_measurement['artifacts']}
        result.update(job_id=job['id'],resource_id=resource['id'],server_data=str(server.data),
            target_release_id=release['id'],cam_handoff=str(work/'cam-handoff.json'),
            artifacts=handoff['artifacts'],
            reference_observed=observed,import_observed=imported,unmeasured=unmeasured,
            status='awaiting_cam',message='Automatic geometry observed. Native CAM export, collector, server adoption and final NX reload still required.')
    # Return a provisional context after this server is closed. Only the
    # controller publishes it after whole-case process/budget verification.
    handoff['server_closed']=True
    return result,handoff


def negative_case(work,fault,installation,environment,specs):
    result={'id':fault['id'],'status':'failed','directory':str(work)}
    task={'case':fault['generated'],'nx_version':'2406'}
    created=phase(work/'create',{**task,'phase':'create'},installation,environment)
    if fault['defect']=='missing_component':
        original=local(work/'create/holder.prt')
        original.rename(local(work/'create/holder.prt.retained'))
    try:
        observed=phase(work/'reopen',{**task,'phase':'measure','part':created['generated']['reference']},
                       installation,environment)['observed']
    except RuntimeError:
        report_path=local(work/'reopen/result.json')
        if not report_path.is_file():raise # No NX observation: infrastructure failure.
        failed=json.loads(report_path.read_text(encoding='utf-8'))
        message=failed.get('error','').lower()
        expected_failure=(fault['defect']=='missing_component' and 'unloaded component' in message)
        if not expected_failure:raise
        result.update(status='passed',detected=['missing_component'],expected_defect=fault['defect'])
    else:
        errors=compare(fault['oracle'],observed,specs['tolerances'])
        if not errors:raise AssertionError('Physical counterexample escaped the numerical oracle.')
        result.update(status='passed',detected=errors,observed=observed,expected_defect=fault['defect'])
    return result


def nx_cases(directory,profile,policy,summary):
    # No NX installation read and no NX process until OS isolation is attested.
    summary['isolation']=isolation(policy,profile['developer_sid'])
    installation=installation_for(profile);summary['nx_build']='2406.5001.0.0'
    environment=nx_environment(installation,directory/'runtime')
    specs=json.loads(local(SERVER/'tools/nxlab/fixtures.json').read_text(encoding='utf-8'))
    jobs_root=local('C:/NX_Migration/ToolJobs');jobs_root.mkdir(parents=True,exist_ok=True)
    preflight_root=local(jobs_root/('preflight-'+uuid.uuid4().hex[:12]))
    preflight=phase(preflight_root,{'phase':'preflight','case':{'id':'nx-preflight'},'nx_version':'2406'},installation,environment)
    summary['cases'].append({'id':'nx-preflight','status':'passed','capabilities':preflight['capabilities'],
        'message':'Session/API preflight only; modeling, STEP and assembly licenses are exercised by the model phases.'})
    from tools.nxlab.cam_target import prepare
    from tools.nxlab.cases import CASE_SECONDS,supervise
    from tools.nxlab.faults import counterexamples
    baseline_root=local(directory/'cam-target');baseline=prepare(installation,baseline_root)
    write(directory/'cam-target.json',baseline)
    # All positives finish before any physical counterexample starts. A failed
    # license/API preflight cannot be mistaken for a detected geometry defect.
    for kind,items in [('positive',specs['cases']),('negative',counterexamples(specs))]:
        for item in items:
            started=time.monotonic();identifier=item['id']
            result={'id':identifier,'status':'failed','execution_limit_seconds':CASE_SECONDS};summary['cases'].append(result)
            work=local(jobs_root/(('lab-' if kind=='positive' else 'fault-')+uuid.uuid4().hex[:12]));work.mkdir(exist_ok=False)
            result['directory']=str(work)
            try:
                result.update(supervise(work,case_id=identifier,kind=kind,profile=profile,policy=policy,
                    source=summary['source'],baseline=baseline,baseline_root=baseline_root,environment=environment,started=started))
            except Blocked as error:result.update(status='blocked',message=str(error));raise
            except Exception as error:result['message']=str(error);raise
            finally:result['seconds']=round(time.monotonic()-started,3)


def main(arguments=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('prepare','server','schemas','fixture-layout','nx'),required=True)
    parser.add_argument('--root',default=str(SERVER/'test-results/local'))
    parser.add_argument('--profile');parser.add_argument('--isolation-policy')
    options=parser.parse_args(arguments)
    root=writable_lab_root(options.root,SERVER);root.mkdir(parents=True,exist_ok=True)
    directory=local(root/(options.stage+'-'+uuid.uuid4().hex[:12]));directory.mkdir(exist_ok=False)
    summary={'schema_version':1,'stage':options.stage,'status':'incomplete','cases':[],
             'complete_product_acceptance':False,'production_access':False,'automatic_approval':False}
    started=time.monotonic()
    try:
        summary['source']=source_state()
        if options.stage=='prepare':
            write(directory/'profile.json',{'schema_version':1,'developer_sid':account_sid(),
                  'installation':'C:/Siemens/NX2406','nx_version':'2406'})
            summary['cases'].append({'id':'profile','status':'passed'})
            summary['status']='prepared'
        elif options.stage=='schemas':
            from tools.nxlab.schemas import inspect
            summary['nx_build'],summary['cases']=inspect('C:/Siemens/NX2406')
            summary['status']='passed' if all(case['status']=='passed' for case in summary['cases']) else 'failed'
        elif options.stage=='fixture-layout':
            from tools.nxlab.schemas import inspect_fixtures
            summary['nx_build'],summary['cases']=inspect_fixtures('C:/Siemens/NX2406')
            summary['status']='passed' if all(case['status']=='passed' for case in summary['cases']) else 'failed'
        elif options.stage=='server':
            # Text fixture only: explicitly not a geometric STEP or NX proof.
            step=(b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('NX lab server transport only'),'2;1');\n"
                  b"FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n"
                  b"#1=MANIFOLD_SOLID_BREP('synthetic',$);\nENDSEC;\nEND-ISO-10303-21;")
            result=persistence(directory/'server',step)
            summary['cases'].append({'id':'http-persistence',**result});summary['status']='passed'
        else:
            if not options.profile or not options.isolation_policy:
                raise Blocked('NX requires a prepared profile and administrator-reviewed dedicated-account isolation policy. No NX started.')
            profile=json.loads(local(options.profile).read_text(encoding='utf-8'))
            if profile.get('schema_version')!=1 or profile.get('nx_version')!='2406':raise Blocked('Unsupported local profile.')
            nx_cases(directory,profile,options.isolation_policy,summary)
            summary['status']='awaiting_cam'
        if source_state()!=summary['source']:raise RuntimeError('Source changed during qualification; evidence is not current.')
    except Blocked as error:
        summary['status']='blocked';summary['cases'].append({'id':'prerequisites','status':'blocked','message':str(error)})
    except Exception as error:
        summary['status']='failed';summary['cases'].append({'id':'execution','status':'failed','message':str(error)})
    finally:
        summary['seconds']=round(time.monotonic()-started,3);evidence(directory,summary)
        print(json.dumps({'report':str(directory),'status':summary['status'],'complete_product_acceptance':False}))
    return 0 if summary['status'] in ('passed','prepared') else 2 if summary['status'] in ('blocked','awaiting_cam') else 1


if __name__=='__main__':sys.exit(main())
