"""No Engine: resolved fixture names must protect other projects from cleanup."""
import copy
import json
import subprocess
import shutil
from pathlib import Path

import pytest

from docker_fixture_policy import fixture_profile, validate_fixture_profile
from test_docker_integration import test_image_runtime_readonly_sources_and_persistence_after_recreation as docker_workflow

PROJECT = 'nx-docker-test-012345abcdef'


def test_only_exact_owned_fixture_profile_is_accepted_and_copied(tmp_path):
    profile = fixture_profile(PROJECT, 38157, tmp_path, tmp_path/'server')
    before = copy.deepcopy(profile)
    result = validate_fixture_profile(profile, PROJECT, 38157, tmp_path, tmp_path/'server')
    assert profile == before and result is not profile
    assert result['volumes']['management-data']['name'] == PROJECT+'_management-data'


def test_known_compose_normalizations_keep_the_same_owned_profile(tmp_path):
    value=fixture_profile(PROJECT,38157,tmp_path,tmp_path/'server')
    service=value['services']['nxserver']
    service['security_opt']=['no-new-privileges']
    service['ports'][0]['published']=38157
    service['ports'][0].pop('mode')
    service['secrets'][0]['target']='/run/secrets/nx_admin_token'
    value['secrets']['nx_admin_token']['name']=PROJECT+'_nx_admin_token'
    value['volumes']['management-data']['driver']='local'
    value['networks']['default']['driver']='bridge'
    for mount in service['volumes']:
        if not mount['read_only']:mount.pop('read_only')
    service['volumes'].reverse()
    approved=validate_fixture_profile(value,PROJECT,38157,tmp_path,tmp_path/'server')
    assert approved['services']['nxserver']['ports'][0]['published']=='38157'
    assert approved['volumes']['management-data']=={'name':PROJECT+'_management-data'}


@pytest.mark.parametrize('fault',['root_array','missing_services','null_service','null_ports','string_port','null_mount','null_bind','relative_source'])
def test_malformed_resolved_metadata_fails_closed(tmp_path,fault):
    value=fixture_profile(PROJECT,38157,tmp_path,tmp_path/'server')
    service=value['services']['nxserver']
    if fault=='root_array':value=[]
    elif fault=='missing_services':value.pop('services')
    elif fault=='null_service':value['services']['nxserver']=None
    elif fault=='null_ports':service['ports']=None
    elif fault=='string_port':service['ports']=['8765:8765']
    elif fault=='null_mount':service['volumes'][1]=None
    elif fault=='null_bind':service['volumes'][1]['bind']=None
    else:service['volumes'][1]['source']='relative/settings.json'
    with pytest.raises(ValueError,match='no start or cleanup authorized'):
        validate_fixture_profile(value,PROJECT,38157,tmp_path,tmp_path/'server')


@pytest.mark.parametrize('fault', ['global_volume','external_volume','volume_options','global_network','external_network',
    'extra_service','command','entrypoint','root','writable_root','privileged','docker_socket',
    'writable_input','extra_mount','duplicate_mount','host_directory_creation','parent_path','network_path',
    'production_path','other_secret','secret_environment','public_port','wrong_port','wrong_build',
    'wrong_image','numeric_bool','numeric_mount_bool','build_ssh','remote_build','host_network',
    'extra_environment','unbounded_logs','template_path'])
def test_unapproved_profiles_never_authorize_start_or_cleanup(tmp_path, fault):
    value = fixture_profile(PROJECT,38157,tmp_path,tmp_path/'server')
    service=value['services']['nxserver'];mount=service['volumes'][2]
    if fault=='global_volume':value['volumes']['management-data']['name']='nx-management_management-data'
    elif fault=='external_volume':value['volumes']['management-data']['external']=True
    elif fault=='volume_options':value['volumes']['management-data']['driver_opts']={'device':'unapproved'}
    elif fault=='global_network':value['networks']['default']['name']='nx-management_default'
    elif fault=='external_network':value['networks']['default']['external']=True
    elif fault=='extra_service':value['services']['other']={}
    elif fault in ('command','entrypoint'):service[fault]=['unapproved']
    elif fault=='root':service['user']='0:0'
    elif fault=='writable_root':service['read_only']=False
    elif fault=='privileged':service['privileged']=True
    elif fault=='docker_socket':service['volumes'].append({'type':'bind','source':'/var/run/docker.sock','target':'/var/run/docker.sock'})
    elif fault=='writable_input':mount['read_only']=False
    elif fault=='extra_mount':service['volumes'].append(copy.deepcopy(mount))
    elif fault=='duplicate_mount':service['volumes'][1]=copy.deepcopy(mount)
    elif fault=='host_directory_creation':mount['bind']['create_host_path']=True
    elif fault=='parent_path':mount['source']=str(tmp_path/'other/../source')
    elif fault=='network_path':mount['source']='//synthetic/do-not-read'
    elif fault=='production_path':mount['source']='W:/SiemensShare/nx2406'
    elif fault=='other_secret':value['secrets']['nx_admin_token']['file']=str(tmp_path/'other.txt')
    elif fault=='secret_environment':value['secrets']['nx_admin_token']['environment']='DO_NOT_READ'
    elif fault=='public_port':service['ports'][0]['host_ip']='0.0.0.0'
    elif fault=='wrong_port':service['ports'][0]['published']='8765'
    elif fault=='wrong_build':service['build']['context']=str(tmp_path/'other')
    elif fault=='wrong_image':service['image']='nx-management:local'
    elif fault=='numeric_bool':service['init']=1
    elif fault=='numeric_mount_bool':mount['read_only']=1
    elif fault=='build_ssh':service['build']['ssh']=['default']
    elif fault=='remote_build':service['build']['context']='https://invalid.example/build'
    elif fault=='host_network':service['network_mode']='host'
    elif fault=='extra_environment':service['environment']['NX_ADMIN_TOKEN']='synthetic-not-real'
    elif fault=='unbounded_logs':service['logging']['options']['max-size']='100g'
    else:mount['source']=str(tmp_path/'${UNAPPROVED}/source')
    before=copy.deepcopy(value)
    with pytest.raises(ValueError,match='no start or cleanup authorized'):
        validate_fixture_profile(value,PROJECT,38157,tmp_path,tmp_path/'server')
    assert value==before


@pytest.mark.parametrize('fault', ['global_volume','external_volume','command','other_source','approved_up_failure',
                                  'existing_volume','existing_network','existing_container','existing_image'])
def test_real_workflow_gates_start_and_cleanup_and_pins_approved_config(tmp_path,monkeypatch,fault):
    calls=[];frozen=[]
    monkeypatch.delenv('DOCKER_HOST',raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT',raising=False)
    which=shutil.which
    monkeypatch.setattr('shutil.which',lambda name: 'synthetic-docker' if name in ('docker','docker.exe') else which(name))
    def run(args,**kwargs):
        calls.append(args)
        if args[1:3]==['context','show']:output='synthetic-local'
        elif args[1:3]==['context','inspect']:output='unix:///synthetic/docker.sock'
        elif args[1]=='info':output='linux'
        elif args[1]=='compose' and args[-3:]==['config','--format','json']:
            project=args[args.index('-p')+1]
            build=Path(docker_workflow.__code__.co_filename).resolve().parents[2]/'server'
            value=fixture_profile(project,int(kwargs['env']['NX_WEB_PORT']),tmp_path,build)
            if fault=='global_volume':value['volumes']['management-data']['name']='nx-management_management-data'
            elif fault=='external_volume':value['volumes']['management-data']['external']=True
            elif fault=='command':value['services']['nxserver']['command']=['unapproved']
            elif fault=='other_source':value['services']['nxserver']['volumes'][2]['source']=str(tmp_path/'unapproved')
            output=json.dumps(value)
        elif args[1:3] in (['volume','ls'],['network','ls'],['image','ls']) or args[1:3]==['ps','-a']:
            kind={'volume':'volume','network':'network','image':'image','ps':'container'}[args[1]]
            output='synthetic pre-existing resource' if fault=='existing_'+kind else ''
        elif 'up' in args:
            config=Path(args[args.index('-f')+1]);assert config.name=='verified-compose.json'
            frozen.append(config.read_bytes())
            # A subsequent edit of the original override cannot redirect cleanup.
            (tmp_path/'override.json').write_text('{"volumes":{"management-data":{"name":"unapproved"}}}')
            raise RuntimeError('Synthetic up failure after approval; no Engine was invoked.')
        elif args[-2:]==['down','--volumes']:
            config=Path(args[args.index('-f')+1]);assert config.read_bytes()==frozen[0]
            value=json.loads(config.read_bytes());project=args[args.index('-p')+1]
            assert value['volumes']['management-data']['name']==project+'_management-data'
            assert value['name']==project and project.startswith('nx-docker-test-')
            output='synthetic cleanup only'
        else:raise AssertionError('Unexpected synthetic Docker command')
        return subprocess.CompletedProcess(args,0,stdout=output,stderr='')
    monkeypatch.setattr(subprocess,'run',run)
    if fault=='approved_up_failure':
        with pytest.raises(RuntimeError,match='Synthetic up failure'):
            docker_workflow(tmp_path)
        assert len(frozen)==1 and any(args[-2:]==['down','--volumes'] for args in calls)
    elif fault.startswith('existing_'):
        with pytest.raises(AssertionError,match='Pre-existing test resources'):
            docker_workflow(tmp_path)
        assert not (tmp_path/'verified-compose.json').exists()
        assert not any('up' in args or 'down' in args or '--volumes' in args for args in calls)
    else:
        with pytest.raises(ValueError,match='no start or cleanup authorized'):
            docker_workflow(tmp_path)
        assert not (tmp_path/'verified-compose.json').exists()
        assert not any('up' in args or 'down' in args or '--volumes' in args for args in calls)
