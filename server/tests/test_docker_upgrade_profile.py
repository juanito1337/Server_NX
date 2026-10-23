"""Explicit full-upgrade profile using synthetic local fixtures, never Docker/NX."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_docker_policy import PROJECT, POWERSHELL, docker_probe, profile


def upgrade_profile(project, inputs):
    value,_=profile()
    value['services']['nxserver']['build']['context']=str(project/'server')
    value['secrets']['nx_admin_token']['file']=str(project/'docker/secrets/admin_token.txt')
    bindings={'/app/config/settings.json':str(project/'docker/settings.windows-upgrade.json'),
        '/inputs/nx2406-local':inputs['source'],'/standards/2406':r'C:\Siemens\NX2406',
        '/standards/2512':r'C:\Siemens\NX2512','/sidts/2406':inputs['sidt_2406'],
        '/sidts/2512':inputs['sidt_2512'],'/data/r':r'C:\NX_Migration\DockerReleases'}
    value['services']['nxserver']['volumes']=[{'type':'volume','source':'management-data','target':'/data'}]+[
        {'type':'bind','source':source,'target':target,'read_only':target!='/data/r','bind':{'create_host_path':False}}
        for target,source in bindings.items()]
    return value


def input_example():
    return {'source_nx_version':'2406','source':r'C:\OfflineTestCopies\nx2406',
        'sidt_2406':r'C:\OfflineSidts\2406','sidt_2512':r'C:\OfflineSidts\2512'}


def pure_input_check(tmp_path, value):
    if not POWERSHELL:pytest.skip('Executable Windows PowerShell policy, not Docker.')
    payload=tmp_path/'input.json';payload.write_text(json.dumps(value),encoding='utf-8')
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    command=f"""$ErrorActionPreference='Stop';
    $configuration=Get-Content -LiteralPath {quote(payload)} -Raw | ConvertFrom-Json;
    Import-Module {quote(PROJECT/'NXDockerSafety.psm1')} -Force;
    function global:Get-Item {{throw 'Unexpected filesystem access'}}
    function global:Test-Path {{throw 'Unexpected filesystem access'}}
    function global:Get-ChildItem {{throw 'Unexpected filesystem access'}}
    try {{$result=Get-NXWindowsUpgradeInputs -Configuration $configuration;Write-Output 'INPUT_OK'}}
    catch {{Write-Output ('INPUT_ERROR:'+ $_.Exception.Message);exit 9}}
    """
    return subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-Command',command],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20)


def test_explicit_version_and_three_separate_local_inputs_are_accepted_without_io(tmp_path):
    result=pure_input_check(tmp_path,input_example())
    assert result.returncode==0 and 'INPUT_OK' in result.stdout,result.stdout+result.stderr


@pytest.mark.parametrize('case',['missing','extra','new_version','numeric_version','relative','unc','protected','drive_root','system_root','traversal','ads','array','null','same_sidt','nested_source','control'])
def test_invalid_upgrade_inputs_are_rejected_before_filesystem_access(tmp_path,case):
    value=input_example()
    if case=='missing':value.pop('sidt_2406')
    elif case=='extra':value['admin_token']='synthetic-private-canary-do-not-print'
    elif case=='new_version':value['source_nx_version']='2512'
    elif case=='numeric_version':value['source_nx_version']=2406
    elif case=='relative':value['source']='nx2406'
    elif case=='unc':value['source']=r'\\never-read\share\nx2406'
    elif case=='protected':value['source']=r'W:\SiemensShare\nx2406'
    elif case=='drive_root':value['source']='C:\\'
    elif case=='system_root':value['source']=r'C:\Users'
    elif case=='traversal':value['source']=r'C:\OfflineTestCopies\..\nx2406'
    elif case=='ads':value['source']=r'C:\OfflineTestCopies\nx2406:stream'
    elif case=='array':value['source']=['not','a','path']
    elif case=='null':value['source']=None
    elif case=='same_sidt':value['sidt_2512']=value['sidt_2406']
    elif case=='nested_source':value['sidt_2406']=value['source']+'\\sidt'
    elif case=='control':value['source']='C:\\local\\line\ncopy'
    result=pure_input_check(tmp_path,value)
    assert result.returncode==9 and 'INPUT_ERROR:' in result.stdout,result.stdout+result.stderr
    assert 'Unexpected filesystem access' not in result.stdout+result.stderr
    assert 'synthetic-private-canary-do-not-print' not in result.stdout+result.stderr


def project_copy(tmp_path):
    project=tmp_path/'project';project.mkdir();assets=project/'server/nxserver/testlaunch_assets';assets.mkdir(parents=True)
    for name in ('Start-NX-Docker.ps1','NXDockerSafety.psm1','NXMigrationSafety.psm1'):shutil.copyfile(PROJECT/name,project/name)
    shutil.copyfile(PROJECT/'server/nxserver/testlaunch_assets/NXOfflineSafety.psm1',assets/'NXOfflineSafety.psm1')
    return project


def run_starter(project,tmp_path,docker_probe,inputs,value,action='Config',extra=()):
    inputs_file=tmp_path/'inputs.json';inputs_file.write_text(inputs if isinstance(inputs,str) else json.dumps(inputs),encoding='utf-8')
    config=tmp_path/'resolved.json';config.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    log=tmp_path/'commands.txt';received=tmp_path/'plan.json';environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT','NX_ADMIN_TOKEN','NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(name,None)
    environment.update(PATH=str(docker_probe.parent)+os.pathsep+environment.get('PATH',''),NX_WEB_PORT='8765',
        NX_UPGRADE_SOURCE='unapproved-inherited-value',NX_DOCKER_PROBE_CONFIG=str(config),
        NX_DOCKER_PROBE_LOG=str(log),NX_DOCKER_PROBE_INPUT=str(received),NX_DOCKER_PROBE_FAIL='',
        NX_DOCKER_PROBE_ENV=str(received)+'.env')
    result=subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(project/'Start-NX-Docker.ps1'),
        '-Action',action,'-Profile','Upgrade','-UpgradeInputsFile',str(inputs_file),*extra],env=environment,
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    return result,log,received


@pytest.mark.parametrize('action',['Config','Status','Stop'])
@pytest.mark.parametrize('unicode_path',[False,True])
def test_upgrade_read_stop_actions_pin_exact_mounts_without_requiring_sources(tmp_path,docker_probe,action,unicode_path):
    project=project_copy(tmp_path);inputs=input_example();value=upgrade_profile(project,inputs)
    if unicode_path:
        inputs['source']+='\\Ä $LITERAL'
        value=upgrade_profile(project,inputs)
    result,log,received=run_starter(project,tmp_path,docker_probe,inputs,value,action)
    assert result.returncode==0,result.stdout+result.stderr
    commands=log.read_text(encoding='utf-8').splitlines()
    assert len(commands)==4 and 'compose.windows-upgrade.yaml' in commands[2] and 'compose.windows.yaml' not in commands[2]
    assert json.loads(received.read_bytes().decode('utf-8'))==json.loads(json.dumps(value).replace('$','$$'))
    assert Path(str(received)+'.env').read_text(encoding='utf-8').splitlines()==[inputs[key].replace('\\','/') for key in ('source','sidt_2406','sidt_2512')]
    assert not (project/'docker').exists()
    assert not any('|up|' in line or '|down' in line or '--volumes' in line for line in commands)


@pytest.mark.parametrize('case',['wrong_version','network_source','overlap','bad_json','oversize'])
def test_upgrade_inputs_are_rejected_by_actual_starter_before_config_or_any_writes(tmp_path,docker_probe,case):
    project=project_copy(tmp_path);inputs=input_example();value=upgrade_profile(project,inputs)
    if case=='wrong_version':inputs['source_nx_version']='2512'
    elif case=='network_source':inputs['source']=r'W:\SiemensShare\nx2406'
    elif case=='overlap':inputs['sidt_2512']=inputs['sidt_2406']
    elif case=='bad_json':inputs='invalid-json-scalar'
    elif case=='oversize':inputs['unexpected']='synthetic-private-canary-do-not-print'+'x'*17000
    result,log,received=run_starter(project,tmp_path,docker_probe,inputs,value)
    assert result.returncode==1 and len(log.read_text(encoding='utf-8').splitlines())==2
    assert not received.exists() and not (project/'docker').exists()
    assert 'synthetic-private-canary-do-not-print' not in result.stdout+result.stderr


@pytest.mark.parametrize('case',['writable_sidt','missing_sidt','prepared_source','extra_mount'])
def test_resolved_upgrade_mount_drift_is_blocked_before_actions(tmp_path,docker_probe,case):
    project=project_copy(tmp_path);inputs=input_example();value=upgrade_profile(project,inputs)
    mounts=value['services']['nxserver']['volumes']
    if case=='writable_sidt':next(m for m in mounts if m['target']=='/sidts/2406')['read_only']=False
    elif case=='missing_sidt':mounts[:]=[m for m in mounts if m['target']!='/sidts/2512']
    elif case=='prepared_source':next(m for m in mounts if m['target']=='/inputs/nx2406-local')['target']='/inputs/nx2512-managed'
    else:mounts.append(copy.deepcopy(mounts[-1]))
    result,log,received=run_starter(project,tmp_path,docker_probe,inputs,value)
    assert result.returncode==1 and 'Docker-Testprofil:' in result.stdout+result.stderr
    assert len(log.read_text(encoding='utf-8').splitlines())==3 and not received.exists()
    assert not (project/'docker').exists()


@pytest.mark.parametrize('missing',['','source','sidt'])
def test_upgrade_start_uses_only_own_synthetic_folders_and_fails_before_writes_for_incomplete_inputs(tmp_path,docker_probe,missing):
    project=project_copy(tmp_path)
    roots={key:tmp_path/key for key in ('source','sidt_2406','sidt_2512','old','new')}
    for root in roots.values():root.mkdir()
    for suffix in ('MACH/resource','UGII'):(roots['source']/suffix).mkdir(parents=True)
    for key in ('old','new'):
        (roots[key]/'NXBIN').mkdir();(roots[key]/'NXBIN/ugraf.exe').write_bytes(b'Synthetic nonexecutable metadata')
    for key in ('sidt_2406','sidt_2512'):
        for suffix in ('applications/nx/ugii','applications/nx/nxplot'):(roots[key]/suffix).mkdir(parents=True)
        (roots[key]/'applications/nx/after_install.bat').write_text('Synthetic instructions, never executed',encoding='utf-8')
    if missing=='source':(roots['source']/'UGII').rmdir()
    if missing=='sidt':(roots['sidt_2512']/'applications/nx/after_install.bat').unlink()
    inputs={'source_nx_version':'2406',**{key:str(roots[key]) for key in ('source','sidt_2406','sidt_2512')}}
    value=upgrade_profile(project,inputs);output=tmp_path/'output'
    script=project/'Start-NX-Docker.ps1';text=script.read_text(encoding='utf-8')
    for old,new in [(r'C:\Siemens\NX2406',roots['old']),(r'C:\Siemens\NX2512',roots['new']),(r'C:\NX_Migration\DockerReleases',output)]:text=text.replace(old,str(new))
    script.write_text(text,encoding='utf-8')
    for mount in value['services']['nxserver']['volumes']:
        if mount['target'] in ('/standards/2406','/standards/2512','/data/r'):
            mount['source']=str({'/standards/2406':roots['old'],'/standards/2512':roots['new'],'/data/r':output}[mount['target']])
    before={str(p.relative_to(tmp_path)):p.read_bytes() for root in roots.values() for p in root.rglob('*') if p.is_file()}
    result,log,received=run_starter(project,tmp_path,docker_probe,inputs,value,'Start')
    commands=log.read_text(encoding='utf-8').splitlines()
    assert all(line.split('|')[:2]==['--host','npipe:////./pipe/dockerDesktopLinuxEngine'] for line in commands[2:])
    if missing:
        assert result.returncode==1 and 'Unvollstaendige' in result.stdout+result.stderr,result.stdout+result.stderr
        assert len(commands)==3 and not received.exists() and not output.exists() and not (project/'docker').exists()
    else:
        assert result.returncode==0,result.stdout+result.stderr
        assert output.is_dir() and '|up|-d|--build|--wait|--wait-timeout|120' in commands[-1]
        assert json.loads(received.read_bytes().decode('utf-8'))==value
    after={str(p.relative_to(tmp_path)):p.read_bytes() for root in roots.values() for p in root.rglob('*') if p.is_file()}
    assert before==after


def test_upgrade_settings_cover_both_sidt_versions_and_no_prepared_alias():
    settings=json.loads((PROJECT/'docker/settings.windows-upgrade.json').read_text(encoding='utf-8'))
    assert settings['sources']=={'nx2406-local':'/inputs/nx2406-local'}
    assert settings['sidts']=={'2406':'/sidts/2406','2512':'/sidts/2512'}
    assert set(settings['standards'])=={'2406','2512'} and not settings['logs']
    assert settings['data_dir']=='/data' and settings['display_paths']=={'/data/r':r'C:\NX_Migration\DockerReleases'}
    override=(PROJECT/'compose.windows-upgrade.yaml').read_text(encoding='utf-8')
    assert override.count('create_host_path: false')==7 and override.count('read_only: true')==6
    assert all('${'+name+':?' in override for name in ('NX_UPGRADE_SOURCE','NX_UPGRADE_SIDT_2406','NX_UPGRADE_SIDT_2512'))
    assert '/inputs/nx2512-managed' not in override and '/logs/nx-test' not in override


def test_profile_settings_feed_actual_upgrade_planner_with_synthetic_mount_mapping(tmp_path):
    from nxserver.store import Store
    from nxserver.migration import Planner,prerequisites
    from test_upgrades import installation,write
    settings=json.loads((PROJECT/'docker/settings.windows-upgrade.json').read_text(encoding='utf-8'))
    # Map declared container roots into this fixture, not real /data or C:.
    mapped=lambda value:tmp_path/value.lstrip('/')
    standards={v:mapped(p) for v,p in settings['standards'].items()}
    sidts={v:mapped(p) for v,p in settings['sidts'].items()}
    for version in ('2406','2512'):installation(standards[version],sidts[version],version)
    sources={key:mapped(path) for key,path in settings['sources'].items()};source=sources['nx2406-local']
    write(source,'MACH/custom/company.txt','Customer fixture')
    (source/'UGII').mkdir();(source/'MACH/resource').mkdir()
    store=Store(mapped(settings['data_dir']),sources,standards=standards,sidts=sidts)
    assert prerequisites(store,'2406','2512')['ready']
    destination=store.root/'r'/'new-NX2512'
    request={'source':'nx2406-local','source_path':str(source),'target':'managed','target_path':str(destination),
        'source_version':'2406','target_version':'2512','name':'Docker profile fixture',
        'defaults':{},'resolutions':{},'file_resolutions':{},'legacy_omissions':[],'source_release_id':None}
    before=store.manifest(source);plan,report=Planner(store).build(request)
    assert report['can_build'] and not destination.exists()
    assert any(item['path']=='MACH/custom/company.txt' for item in report['result_manifest'])
    assert store.manifest(source)==before and store.releases()==[]


@pytest.mark.skipif(not os.environ.get('NX_COMPOSE_BIN'),reason='Optional actual standalone Compose profile validator.')
def test_real_compose_resolves_explicit_upgrade_inputs_without_engine(tmp_path):
    for name in ('compose.yaml','compose.windows-upgrade.yaml'):shutil.copyfile(PROJECT/name,tmp_path/name)
    (tmp_path/'docker/secrets').mkdir(parents=True)
    (tmp_path/'docker/secrets/admin_token.txt').write_text('synthetic-not-live-'+'x'*40)
    shutil.copyfile(PROJECT/'docker/settings.windows-upgrade.json',tmp_path/'docker/settings.windows-upgrade.json')
    (tmp_path/'server').mkdir()
    environment={**os.environ,'NX_WEB_PORT':'8765','NX_UPGRADE_SOURCE':'C:/OfflineCopies/nx2406',
        'NX_UPGRADE_SIDT_2406':'C:/OfflineSidts/2406','NX_UPGRADE_SIDT_2512':'C:/OfflineSidts/2512'}
    response=subprocess.run([os.environ['NX_COMPOSE_BIN'],'-f',str(tmp_path/'compose.yaml'),'-f',str(tmp_path/'compose.windows-upgrade.yaml'),
        'config','--format','json'],cwd=tmp_path,env=environment,capture_output=True,text=True,check=True,timeout=30)
    value=json.loads(response.stdout);mounts={item['target']:item for item in value['services']['nxserver']['volumes']}
    assert set(mounts)=={'/data','/data/r','/app/config/settings.json','/inputs/nx2406-local','/standards/2406','/standards/2512','/sidts/2406','/sidts/2512'}
    for target,mount in mounts.items():
        if target=='/data':continue
        assert mount['bind']['create_host_path'] is False
        assert mount.get('read_only',False)==(target!='/data/r')
