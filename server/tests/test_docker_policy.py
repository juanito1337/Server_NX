"""Executable PowerShell policy on synthetic JSON. Never a Docker Engine test."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

PROJECT=Path(__file__).resolve().parents[2]
POWERSHELL=shutil.which('powershell.exe') if os.name=='nt' else None


def profile(port=8765):
    bindings={
        '/app/config/settings.json':r'C:\SyntheticNxProject\docker\settings.windows.json',
        '/inputs/nx2512-managed':r'C:\NX_Migration\NX2512_Managed_20261006',
        '/standards/2406':r'C:\Siemens\NX2406','/standards/2512':r'C:\Siemens\NX2512',
        '/logs/nx-test':r'C:\NX_Migration\TestRuntime\temp','/data/r':r'C:\NX_Migration\DockerReleases'}
    mounts=[{'type':'volume','source':'management-data','target':'/data'}]+[
        {'type':'bind','source':source,'target':target,'read_only':target!='/data/r','bind':{'create_host_path':False}}
        for target,source in bindings.items()]
    return {'name':'nx-management','services':{'nxserver':{
        'build':{'context':r'C:\SyntheticNxProject\server','dockerfile':'Dockerfile'},
        'image':'nx-management:local','user':'10001:10001','read_only':True,'init':True,'restart':'unless-stopped','stop_grace_period':'30s',
        'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],'tmpfs':['/tmp:size=64m,mode=1777'],
        'environment':{'NX_ADMIN_TOKEN_FILE':'/run/secrets/nx_admin_token'},
        'ports':[{'host_ip':'127.0.0.1','published':str(port),'target':8765,'protocol':'tcp','mode':'ingress'}],
        'logging':{'driver':'json-file','options':{'max-size':'10m','max-file':'3'}},
        'networks':{'default':None},'volumes':mounts,'secrets':[{'source':'nx_admin_token','target':'nx_admin_token'}]}},
        'volumes':{'management-data':{'name':'nx-management_management-data'}},
        'networks':{'default':{'name':'nx-management_default'}},
        'secrets':{'nx_admin_token':{'file':r'C:\SyntheticNxProject\docker\secrets\admin_token.txt'}}},bindings


def execute(tmp_path,value,bindings,port=8765):
    if not POWERSHELL:pytest.skip('Executable Windows PowerShell policy requires Windows, not Docker.')
    payload=tmp_path/'policy.json';payload.write_text(json.dumps({'configuration':value,'bindings':bindings,'port':port}),encoding='utf-8')
    quote=lambda text:"'"+str(text).replace("'","''")+"'"
    script=fr"""$ErrorActionPreference='Stop';
    $inputData=Get-Content -LiteralPath {quote(payload)} -Raw | ConvertFrom-Json;
    Import-Module {quote(PROJECT/'NXDockerSafety.psm1')} -Force;
    $permitted=@{{}};foreach($property in $inputData.bindings.PSObject.Properties){{$permitted[$property.Name]=$property.Value}};
    function global:Get-Item {{throw 'Unexpected filesystem access'}}
    function global:Test-Path {{throw 'Unexpected filesystem access'}}
    function global:Get-ChildItem {{throw 'Unexpected filesystem access'}}
    try {{Assert-NXLocalDockerProfile -Configuration $inputData.configuration -ProjectRoot 'C:\SyntheticNxProject' -PermittedBindings $permitted -WebPort $inputData.port;Write-Output 'POLICY_OK'}}
    catch {{Write-Output ('POLICY_ERROR:'+ $_.Exception.Message);exit 9}}
    """
    return subprocess.run([POWERSHELL,'-NoProfile','-Command',script],capture_output=True,text=True,timeout=20)


@pytest.mark.parametrize('port',[8765,8766,1,65535])
def test_fixed_local_profile_is_accepted_without_filesystem_or_daemon_access(tmp_path,port):
    value,bindings=profile(port);result=execute(tmp_path,value,bindings,port)
    assert result.returncode==0 and 'POLICY_OK' in result.stdout,result.stdout+result.stderr


BAD_SERVICE_FIELDS=['command','entrypoint','privileged','cap_add','devices','device_cgroup_rules','network_mode','pid','ipc','uts','userns_mode','provider','pre_start','post_start','pre_stop','develop','models','configs','healthcheck','extends','profiles','external_links','volumes_from','container_name','env_file','runtime','deploy','build_extra']


@pytest.mark.parametrize('field',BAD_SERVICE_FIELDS)
def test_execution_or_host_access_extensions_are_rejected_without_daemon_access(tmp_path,field):
    value,bindings=profile();value['services']['nxserver'][field]='synthetic-unapproved-value';result=execute(tmp_path,value,bindings)
    assert result.returncode==9 and 'POLICY_ERROR:' in result.stdout and 'Unexpected filesystem access' not in result.stdout


@pytest.mark.parametrize('case',['all_interfaces','extra_port','wrong_port','wrong_target','udp','port_host_mode','root','writable_root','no_init','capability','no_security','extra_env','wrong_secret_env','wrong_image','foreign_volume','remote_volume_driver','volume_options','external_volume','foreign_network','external_network','host_network_options','extra_service','extra_top_config','secret_environment','secret_external','wrong_secret_path','secret_target','remote_build','wrong_dockerfile','build_secret','build_ssh','build_cache_publish','readwrite_input','readonly_data','readonly_output','auto_host_path','mount_subpath','extra_mount','duplicate_mount','case_changed_mount','network_source','parent_traversal','data_bind','log_unbounded','tmpfs_extra','grace_period','foreign_project'])
def test_resolved_config_mutations_are_fail_closed(tmp_path,case):
    value,bindings=profile();service=value['services']['nxserver'];mount=service['volumes'][2]
    if case=='all_interfaces':service['ports'][0]['host_ip']='0.0.0.0'
    elif case=='extra_port':service['ports'].append(copy.deepcopy(service['ports'][0]))
    elif case=='wrong_port':service['ports'][0]['published']='9999'
    elif case=='wrong_target':service['ports'][0]['target']=80
    elif case=='udp':service['ports'][0]['protocol']='udp'
    elif case=='port_host_mode':service['ports'][0]['mode']='host'
    elif case=='root':service['user']='0:0'
    elif case=='writable_root':service['read_only']=False
    elif case=='no_init':service['init']=False
    elif case=='capability':service['cap_drop']=[]
    elif case=='no_security':service['security_opt']=[]
    elif case=='extra_env':service['environment']['NX_ADMIN_TOKEN']='synthetic-private-canary-do-not-print'
    elif case=='wrong_secret_env':service['environment']['NX_ADMIN_TOKEN_FILE']='/inputs/token'
    elif case=='wrong_image':service['image']='unapproved-image:local'
    elif case=='foreign_volume':value['volumes']['management-data']['name']='other-app-data'
    elif case=='remote_volume_driver':value['volumes']['management-data']['driver']='remote'
    elif case=='volume_options':value['volumes']['management-data']['driver_opts']={'device':'unapproved'}
    elif case=='external_volume':value['volumes']['management-data']['external']=True
    elif case=='foreign_network':value['networks']['default']['name']='foreign-network'
    elif case=='external_network':value['networks']['default']['external']=True
    elif case=='host_network_options':service['networks']['default']={'ipv4_address':'192.168.0.4'}
    elif case=='extra_service':value['services']['other']={}
    elif case=='extra_top_config':value['configs']={'private':'unapproved'}
    elif case=='secret_environment':value['secrets']['nx_admin_token']['environment']='synthetic-private-canary-do-not-print'
    elif case=='secret_external':value['secrets']['nx_admin_token']['external']=True
    elif case=='wrong_secret_path':value['secrets']['nx_admin_token']['file']=r'W:\SiemensShare\nx2406\secret.txt'
    elif case=='secret_target':service['secrets'][0]['target']='another-file'
    elif case=='remote_build':service['build']['context']='https://invalid.example/source.git'
    elif case=='wrong_dockerfile':service['build']['dockerfile']='AnotherDockerfile'
    elif case=='build_secret':service['build']['secrets']=['synthetic']
    elif case=='build_ssh':service['build']['ssh']=['default']
    elif case=='build_cache_publish':service['build']['cache_to']=['type=registry,ref=invalid.example/image']
    elif case=='readwrite_input':mount['read_only']=False
    elif case=='readonly_data':service['volumes'][0]['read_only']=True
    elif case=='readonly_output':service['volumes'][-1]['read_only']=True
    elif case=='auto_host_path':mount['bind']['create_host_path']=True
    elif case=='mount_subpath':service['volumes'][0]['volume']={'subpath':'unapproved'}
    elif case=='extra_mount':service['volumes'].append({'type':'bind','source':'/var/run/docker.sock','target':'/var/run/docker.sock'})
    elif case=='duplicate_mount':service['volumes'][-1]=copy.deepcopy(mount)
    elif case=='case_changed_mount':mount['target']='/INPUTS/nx2512-managed'
    elif case=='network_source':mount['source']=r'\\synthetic\do-not-read'
    elif case=='parent_traversal':mount['source']=r'C:\NX_Migration\other\..\NX2512_Managed_20261006'
    elif case=='data_bind':service['volumes'][0].update(type='bind',source=r'C:\unapproved')
    elif case=='log_unbounded':service['logging']['options']['max-size']='1g'
    elif case=='tmpfs_extra':service['tmpfs'].append('/unapproved')
    elif case=='grace_period':service['stop_grace_period']='1s'
    else:value['name']='foreign-project'
    result=execute(tmp_path,value,bindings);assert result.returncode==9 and 'POLICY_ERROR:' in result.stdout
    assert 'synthetic-private-canary-do-not-print' not in result.stdout+result.stderr
    assert 'Unexpected filesystem access' not in result.stdout


def test_profile_gate_precedes_all_compose_mutations_and_directory_secret_creation():
    source=(PROJECT/'Start-NX-Docker.ps1').read_text(encoding='utf-8')
    gate=source.index('Assert-NXLocalDockerProfile -Configuration')
    for marker in ("Invoke-Compose -Arguments @('stop', 'nxserver')",'if ($Action -eq \'Status\')',"if ($Action -eq 'Config')",'[System.IO.Directory]::CreateDirectory($releasePath)',"Invoke-Compose -Arguments @('up'",'$validatedComposeFile = New-Object System.IO.FileStream'):
        assert gate<source.index(marker)
    assert "@('down')" not in source and '--volumes' not in source and 'prune' not in source
    assert "Assert-NXTreeNoReparsePoint -Root $releasePath" in source


@pytest.fixture(scope='module')
def docker_probe(tmp_path_factory):
    if not POWERSHELL:pytest.skip('Harmless Windows CLI stand-in requires Windows, not Docker.')
    root=tmp_path_factory.mktemp('docker-probe');executable=root/'docker.exe'
    code=r'''using System;using System.IO;using System.Linq;
    public class DockerPolicyProbe {
      public static int Main(string[] args){
        File.AppendAllText(Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_LOG"),String.Join("|",args)+Environment.NewLine);
        string explicitHost=null;
        if(args.Length>=2 && args[0]=="--host"){explicitHost=args[1];args=args.Skip(2).ToArray();}
        string endpoint=Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_ENDPOINT")??"npipe:////./pipe/dockerDesktopLinuxEngine";
        string drift=Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_CONTEXT_STATE");
        if(!String.IsNullOrEmpty(drift) && File.Exists(drift) && args.Length>0 && args[0]!="context"){
          string selected=explicitHost??"unapproved-simulated-remote";
          File.AppendAllText(drift+".selected",selected+Environment.NewLine);
        }
        if(args.Length>1 && args[0]=="context" && args[1]=="show"){Console.WriteLine("isolated-desktop");return 0;}
        if(args.Length>1 && args[0]=="context" && args[1]=="inspect"){
          Console.WriteLine(endpoint);
          if(!String.IsNullOrEmpty(drift))File.WriteAllText(drift,"Simulated context endpoint changed after inspection");
          return 0;
        }
        if(args.Length>0 && args[0]=="info"){Console.WriteLine("linux");return 0;}
        if(args.Length>0 && args[0]=="compose" && args.Contains("config") && args.Contains("json")){
          string approved=File.ReadAllText(Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_CONFIG"));
          string changed=Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_CHANGE_SOURCE");
          if(!String.IsNullOrEmpty(changed))File.WriteAllText(changed,"UNAPPROVED_SOURCE_AFTER_VALIDATION");
          string capture=Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_ENV");
          if(!String.IsNullOrEmpty(capture))File.WriteAllLines(capture,new string[]{Environment.GetEnvironmentVariable("NX_UPGRADE_SOURCE")??"",Environment.GetEnvironmentVariable("NX_UPGRADE_SIDT_2406")??"",Environment.GetEnvironmentVariable("NX_UPGRADE_SIDT_2512")??""},new System.Text.UTF8Encoding(false));
          byte[] output=System.Text.Encoding.UTF8.GetBytes(approved+Environment.NewLine);
          using(var stream=Console.OpenStandardOutput()){stream.Write(output,0,output.Length);}return 0;
        }
        if(args.Length>0 && args[0]=="compose" && (args.Contains("stop") || args.Contains("ps") || args.Contains("up") || (args.Contains("config") && args.Contains("--quiet")))){
          string received=Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_INPUT");
          int planIndex=Array.IndexOf(args,"-f");
          if(planIndex>=0 && args.Length>planIndex+1 && Path.GetFileName(args[planIndex+1]).StartsWith("nx-compose-") && !String.IsNullOrEmpty(received)){
            string plan=args[planIndex+1];
            using(var source=new FileStream(plan,FileMode.Open,FileAccess.Read,FileShare.ReadWrite))
            using(var target=new FileStream(received,FileMode.Create,FileAccess.Write)){source.CopyTo(target);}
            File.AppendAllText(received+".seen",Convert.ToBase64String(File.ReadAllBytes(received))+Environment.NewLine);
            bool writeBlocked=false,deleteBlocked=false;
            try{using(var writer=new FileStream(plan,FileMode.Open,FileAccess.Write,FileShare.ReadWrite)) {writer.WriteByte(0);}}
            catch(IOException){writeBlocked=true;}
            try{File.Delete(plan);}catch(IOException){deleteBlocked=true;}
            File.WriteAllText(received+".lock",writeBlocked+"|"+deleteBlocked);
          }
          string phase=args.Contains("up")?"up":args.Contains("--quiet")?"config":args.Contains("stop")?"stop":"ps";
          if(phase==Environment.GetEnvironmentVariable("NX_DOCKER_PROBE_FAIL")){Console.Error.WriteLine("Synthetic Compose action failure");return 37;}
          Console.WriteLine("Read/stop probe complete");return 0;
        }
        Console.WriteLine("Unexpected probe action");return 21;
      }
    }'''
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    result=subprocess.run([POWERSHELL,'-NoProfile','-Command',f'Add-Type -TypeDefinition {quote(code)} -OutputAssembly {quote(executable)} -OutputType ConsoleApplication'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    return executable


@pytest.mark.parametrize('action',['Start','Stop','Status','Config'])
@pytest.mark.parametrize('mutation',['command','volume_name'])
def test_actual_starter_rejects_bad_resolved_profile_before_actions_or_host_writes(tmp_path,docker_probe,action,mutation):
    project=tmp_path/'project';project.mkdir();assets=project/'server/nxserver/testlaunch_assets';assets.mkdir(parents=True)
    for name in ('Start-NX-Docker.ps1','NXDockerSafety.psm1','NXMigrationSafety.psm1'):shutil.copyfile(PROJECT/name,project/name)
    shutil.copyfile(PROJECT/'server/nxserver/testlaunch_assets/NXOfflineSafety.psm1',assets/'NXOfflineSafety.psm1')
    value,bindings=profile();value['services']['nxserver']['build']['context']=str(project/'server')
    value['secrets']['nx_admin_token']['file']=str(project/'docker/secrets/admin_token.txt')
    value['services']['nxserver']['volumes'][1]['source']=str(project/'docker/settings.windows.json')
    if mutation=='command':value['services']['nxserver']['command']=['unapproved-command']
    else:value['volumes']['management-data']['name']='unapproved-app-volume'
    config=tmp_path/'config.json';config.write_text(json.dumps(value),encoding='utf-8');log=tmp_path/'commands.txt'
    environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT'):environment.pop(name,None)
    environment.update(PATH=str(docker_probe.parent)+os.pathsep+environment.get('PATH',''),NX_WEB_PORT='8765',NX_DOCKER_PROBE_CONFIG=str(config),NX_DOCKER_PROBE_LOG=str(log))
    result=subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(project/'Start-NX-Docker.ps1'),'-Action',action],env=environment,capture_output=True,text=True,timeout=30)
    assert result.returncode==1 and 'Docker-Testprofil:' in result.stdout+result.stderr,result.stdout+result.stderr
    commands=log.read_text(encoding='utf-8').splitlines();assert len(commands)==3 and 'context|show' in commands[0] and 'context|inspect' in commands[1] and 'config|--format|json' in commands[2]
    assert not (project/'docker').exists()  # No secret/settings/output preparation reached.
    assert not any('|up|' in line or '|stop|' in line or '|down' in line for line in commands)


@pytest.mark.parametrize('action,ending',[('Stop','stop|nxserver'),('Status','ps|nxserver'),('Config','config|--quiet')])
def test_actual_starter_valid_read_stop_actions_need_no_sources_and_retain_all_data(tmp_path,docker_probe,action,ending):
    project=tmp_path/'project';project.mkdir();assets=project/'server/nxserver/testlaunch_assets';assets.mkdir(parents=True)
    for name in ('Start-NX-Docker.ps1','NXDockerSafety.psm1','NXMigrationSafety.psm1'):shutil.copyfile(PROJECT/name,project/name)
    shutil.copyfile(PROJECT/'server/nxserver/testlaunch_assets/NXOfflineSafety.psm1',assets/'NXOfflineSafety.psm1')
    value,_=profile();value['services']['nxserver']['build']['context']=str(project/'server')
    value['secrets']['nx_admin_token']['file']=str(project/'docker/secrets/admin_token.txt')
    value['services']['nxserver']['volumes'][1]['source']=str(project/'docker/settings.windows.json')
    config=tmp_path/'config.json';config.write_text(json.dumps(value),encoding='utf-8');log=tmp_path/'commands.txt'
    environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT'):environment.pop(name,None)
    environment.update(PATH=str(docker_probe.parent)+os.pathsep+environment.get('PATH',''),NX_WEB_PORT='8765',NX_DOCKER_PROBE_CONFIG=str(config),NX_DOCKER_PROBE_LOG=str(log))
    result=subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(project/'Start-NX-Docker.ps1'),'-Action',action],env=environment,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    commands=log.read_text(encoding='utf-8').splitlines();assert len(commands)==4 and commands[-1].endswith(ending)
    assert not (project/'docker').exists() and not any('|down' in line or '|up|' in line or '--volumes' in line for line in commands)


@pytest.mark.parametrize('case',['service_case','volume_case','network_case','secret_case','root_bool_string','init_bool_string','mount_bool_string','bind_bool_string'])
def test_case_sensitive_names_and_boolean_types_do_not_change_container_meaning(tmp_path,case):
    value,bindings=profile();service=value['services']['nxserver']
    if case=='service_case':value['services']['NXSERVER']=value['services'].pop('nxserver')
    elif case=='volume_case':value['volumes']['Management-Data']=value['volumes'].pop('management-data')
    elif case=='network_case':value['networks']['DEFAULT']=value['networks'].pop('default')
    elif case=='secret_case':value['secrets']['NX_ADMIN_TOKEN']=value['secrets'].pop('nx_admin_token')
    elif case=='root_bool_string':service['read_only']='true'
    elif case=='init_bool_string':service['init']='true'
    elif case=='mount_bool_string':service['volumes'][2]['read_only']='true'
    else:service['volumes'][2]['bind']['create_host_path']='false'
    result=execute(tmp_path,value,bindings);assert result.returncode==9 and 'POLICY_ERROR:' in result.stdout


@pytest.mark.parametrize('action,ending',[('Stop','stop|nxserver'),('Status','ps|nxserver'),('Config','config|--quiet')])
@pytest.mark.parametrize('directory',['project','project Ä $NX_PLAN_DRIFT'])
@pytest.mark.parametrize('failed',[False,True])
def test_actual_starter_uses_only_validated_locked_plan_after_original_yaml_changes(tmp_path,docker_probe,action,ending,directory,failed):
    project=tmp_path/directory;project.mkdir();assets=project/'server/nxserver/testlaunch_assets';assets.mkdir(parents=True)
    for name in ('Start-NX-Docker.ps1','NXDockerSafety.psm1','NXMigrationSafety.psm1'):shutil.copyfile(PROJECT/name,project/name)
    shutil.copyfile(PROJECT/'server/nxserver/testlaunch_assets/NXOfflineSafety.psm1',assets/'NXOfflineSafety.psm1')
    original=project/'compose.yaml';original.write_text('ORIGINAL_SOURCE_BEFORE_VALIDATION',encoding='utf-8')
    value,_=profile();value['services']['nxserver']['build']['context']=str(project/'server')
    value['secrets']['nx_admin_token']['file']=str(project/'docker/secrets/admin_token.txt')
    value['services']['nxserver']['volumes'][1]['source']=str(project/'docker/settings.windows.json')
    config=tmp_path/'config.json';config.write_text(json.dumps(value),encoding='utf-8')
    log=tmp_path/'commands.txt';received=tmp_path/'received-plan.json'
    environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT','NX_ADMIN_TOKEN','NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(name,None)
    environment.update(PATH=str(docker_probe.parent)+os.pathsep+environment.get('PATH',''),NX_WEB_PORT='8765',
        NX_PLAN_DRIFT='UNAPPROVED_INTERPOLATED_PATH',NX_DOCKER_PROBE_CONFIG=str(config),NX_DOCKER_PROBE_LOG=str(log),
        NX_DOCKER_PROBE_CHANGE_SOURCE=str(original),NX_DOCKER_PROBE_INPUT=str(received),
        NX_DOCKER_PROBE_FAIL={'Stop':'stop','Status':'ps','Config':'config'}[action] if failed else '')
    result=subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(project/'Start-NX-Docker.ps1'),'-Action',action],env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    assert result.returncode==(1 if failed else 0),result.stdout+result.stderr
    if failed:assert 'Exit 37' in result.stdout+result.stderr
    assert original.read_text(encoding='utf-8')=='UNAPPROVED_SOURCE_AFTER_VALIDATION'
    commands=log.read_text(encoding='utf-8').splitlines();assert len(commands)==4
    invocation=commands[-1].split('|')
    assert invocation.count('-f')==1, 'Action reused mutable original YAML instead of its validated plan.'
    plan=Path(invocation[invocation.index('-f')+1]);assert plan.name.startswith('nx-compose-') and plan.suffix=='.json'
    assert commands[-1].endswith(ending) and str(original) not in commands[-1]
    assert received.is_file(), 'Validated plan was not passed to Compose.'
    raw=received.read_bytes();assert not raw.startswith(b'\xef\xbb\xbf')
    # Compose reloads the plan: each literal dollar must survive that second
    # interpolation. This is a plan/encoding contract, NOT an Engine proof.
    assert json.loads(raw.decode('utf-8'))==json.loads(json.dumps(value).replace('$','$$'))
    assert Path(str(received)+'.lock').read_text(encoding='utf-8')=='True|True'
    assert not plan.exists(), 'Only the owned temporary plan must be cleaned after the action.'
    assert not (project/'docker').exists()
    assert not any('|down' in line or '|up|' in line or '--volumes' in line for line in commands)


@pytest.mark.parametrize('failure_phase',['','config','up'])
def test_start_actions_share_one_locked_plan_and_cleanup_on_success_or_failure(tmp_path,docker_probe,failure_phase):
    import base64
    project=tmp_path/'project';project.mkdir();assets=project/'server/nxserver/testlaunch_assets';assets.mkdir(parents=True)
    for name in ('Start-NX-Docker.ps1','NXDockerSafety.psm1','NXMigrationSafety.psm1'):shutil.copyfile(PROJECT/name,project/name)
    shutil.copyfile(PROJECT/'server/nxserver/testlaunch_assets/NXOfflineSafety.psm1',assets/'NXOfflineSafety.psm1')
    source_paths=[tmp_path/('input-'+str(index)) for index in range(4)]
    for path in source_paths:path.mkdir()
    output=tmp_path/'test-releases'
    # Relocate only fixed host constants in this disposable script copy. The
    # actual starter gates, directory/secret ACL flow and actions remain intact.
    script=project/'Start-NX-Docker.ps1';text=script.read_text(encoding='utf-8')
    old_paths=[r'C:\NX_Migration\NX2512_Managed_20261006',r'C:\Siemens\NX2406',r'C:\Siemens\NX2512',r'C:\NX_Migration\TestRuntime\temp',r'C:\NX_Migration\DockerReleases']
    for old,new in zip(old_paths,source_paths+[output]):
        assert old in text;text=text.replace(old,str(new))
    script.write_text(text,encoding='utf-8')
    value,_=profile();service=value['services']['nxserver'];service['build']['context']=str(project/'server')
    value['secrets']['nx_admin_token']['file']=str(project/'docker/secrets/admin_token.txt')
    replacements=dict(zip(['/inputs/nx2512-managed','/standards/2406','/standards/2512','/logs/nx-test','/data/r'],source_paths+[output]))
    replacements['/app/config/settings.json']=project/'docker/settings.windows.json'
    for mount in service['volumes']:
        if mount['target'] in replacements:mount['source']=str(replacements[mount['target']])
    original=project/'compose.yaml';original.write_text('ORIGINAL_SOURCE_BEFORE_VALIDATION',encoding='utf-8')
    config=tmp_path/'config.json';config.write_text(json.dumps(value),encoding='utf-8')
    log=tmp_path/'commands.txt';received=tmp_path/'received-plan.json';environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT','NX_ADMIN_TOKEN','NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(name,None)
    environment.update(PATH=str(docker_probe.parent)+os.pathsep+environment.get('PATH',''),NX_WEB_PORT='8765',
        NX_DOCKER_PROBE_CONFIG=str(config),NX_DOCKER_PROBE_LOG=str(log),NX_DOCKER_PROBE_CHANGE_SOURCE=str(original),
        NX_DOCKER_PROBE_INPUT=str(received),NX_DOCKER_PROBE_FAIL=failure_phase)
    result=subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Action','Start'],env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    assert result.returncode==(1 if failure_phase else 0),result.stdout+result.stderr
    if failure_phase:assert 'Exit 37' in result.stdout+result.stderr
    commands=log.read_text(encoding='utf-8').splitlines()
    assert all(line.split('|')[:2]==['--host','npipe:////./pipe/dockerDesktopLinuxEngine'] for line in commands[2:])
    actions=[line.split('|') for line in commands if 'nx-compose-' in line]
    assert len(actions)==(1 if failure_phase=='config' else 2)
    plans=[Path(args[args.index('-f')+1]) for args in actions]
    assert len(set(plans))==1 and not plans[0].exists()
    assert all(args.count('-f')==1 and str(original) not in args for args in actions)
    assert actions[0][-2:]==['config','--quiet']
    if failure_phase!='config':assert actions[1][-6:]==['up','-d','--build','--wait','--wait-timeout','120']
    seen=Path(str(received)+'.seen').read_text(encoding='utf-8').splitlines()
    assert len(seen)==len(actions)
    assert all(json.loads(base64.b64decode(item).decode('utf-8'))==value for item in seen)
    assert Path(str(received)+'.lock').read_text(encoding='utf-8')=='True|True'
    assert output.is_dir() and (project/'docker/secrets/admin_token.txt').is_file()
    assert all(not list(path.iterdir()) for path in source_paths)
    assert not any('|down' in line or '--volumes' in line for line in commands)
