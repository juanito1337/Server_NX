"""CLI argument/endpoint binding with a harmless stand-in, never a real daemon."""
import json
import os
from pathlib import Path
import subprocess
import shutil

import pytest

from test_docker_policy import POWERSHELL,docker_probe,profile
from test_docker_upgrade_profile import project_copy


def run_endpoint_probe(tmp_path,docker_probe,endpoint,action,selected_context=False,inherited_host=None):
    project=project_copy(tmp_path);value,_=profile()
    value['services']['nxserver']['build']['context']=str(project/'server')
    value['services']['nxserver']['volumes'][1]['source']=str(project/'docker/settings.windows.json')
    value['secrets']['nx_admin_token']['file']=str(project/'docker/secrets/admin_token.txt')
    payload=tmp_path/'resolved.json';payload.write_text(json.dumps(value),encoding='utf-8')
    log=tmp_path/'calls.txt';state=tmp_path/'simulated-context-state.txt'
    environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT','NX_ADMIN_TOKEN','NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(name,None)
    environment.update(PATH=str(docker_probe.parent)+os.pathsep+environment.get('PATH',''),NX_WEB_PORT='8765',
        NX_DOCKER_PROBE_ENDPOINT=endpoint,NX_DOCKER_PROBE_LOG=str(log),NX_DOCKER_PROBE_CONFIG=str(payload),
        NX_DOCKER_PROBE_CONTEXT_STATE=str(state),NX_DOCKER_PROBE_FAIL='')
    if selected_context:
        environment['DOCKER_CONTEXT']='synthetic-selected-context'
        environment['DOCKER_HOST']='npipe:////./pipe/docker_engine'
    if inherited_host is not None:environment['DOCKER_HOST']=inherited_host
    result=subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(project/'Start-NX-Docker.ps1'),'-Action',action],
        env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    return result,log,state,project


@pytest.mark.parametrize('action',['Stop','Status','Config'])
@pytest.mark.parametrize('endpoint',['npipe:////./pipe/docker_engine','npipe:////./pipe/dockerDesktopLinuxEngine'])
@pytest.mark.parametrize('selected_context',[False,True])
def test_actions_use_verified_pipe_even_after_simulated_context_endpoint_changes(tmp_path,docker_probe,action,endpoint,selected_context):
    result,log,state,project=run_endpoint_probe(tmp_path,docker_probe,endpoint,action,selected_context)
    assert result.returncode==0,result.stdout+result.stderr
    assert state.read_text(encoding='utf-8')=='Simulated context endpoint changed after inspection'
    assert Path(str(state)+'.selected').read_text(encoding='utf-8').splitlines()==[endpoint,endpoint], 'Later commands drifted away from the inspected local pipe.'
    calls=[line.split('|') for line in log.read_text(encoding='utf-8').splitlines()]
    assert len(calls)==4 and calls[0][:2]==['context','show'] and calls[1][:2]==['context','inspect']
    assert all(call[:3]==['--host',endpoint,'compose'] for call in calls[2:])
    assert not (project/'docker').exists()
    assert not any('down' in call or '--volumes' in call or 'up' in call for call in calls)


@pytest.mark.parametrize('endpoint',['tcp://127.0.0.1:2375','tcp://synthetic.invalid:2376','ssh://synthetic.invalid',
    'unix:///var/run/docker.sock','npipe:////synthetic.invalid/pipe/docker_engine',
    'npipe:////./pipe/unapproved_engine','https://synthetic.invalid',''])
def test_nonapproved_endpoints_are_rejected_before_config_or_any_host_writes(tmp_path,docker_probe,endpoint):
    result,log,state,project=run_endpoint_probe(tmp_path,docker_probe,endpoint,'Stop')
    assert result.returncode==1 and 'lokale Docker-Desktop Pipe' in result.stdout+result.stderr,result.stdout+result.stderr
    assert len(log.read_text(encoding='utf-8').splitlines())==2
    assert not Path(str(state)+'.selected').exists() and not (project/'docker').exists()


@pytest.mark.parametrize('action',['Start','Stop','Status','Config'])
@pytest.mark.parametrize('host',['tcp://synthetic.invalid:2375','ssh://synthetic.invalid','npipe:////synthetic.invalid/pipe/docker_engine'])
def test_unapproved_inherited_host_is_rejected_before_even_context_inspection(tmp_path,docker_probe,action,host):
    result,log,state,project=run_endpoint_probe(tmp_path,docker_probe,'npipe:////./pipe/dockerDesktopLinuxEngine',action,True,host)
    assert result.returncode==1 and 'DOCKER_HOST' in result.stdout+result.stderr
    assert not log.exists() and not state.exists() and not (project/'docker').exists()


@pytest.mark.parametrize('action',['Stop','Status','Config'])
def test_multiple_local_docker_binaries_do_not_turn_resolution_into_an_array(tmp_path,docker_probe,monkeypatch,action):
    second=tmp_path/'second-local-cli';second.mkdir();shutil.copyfile(docker_probe,second/'docker.exe')
    # Both executables are our inert compiled stand-in. The actual installed
    # Docker CLI is excluded, so even an incorrect selection cannot reach it.
    monkeypatch.setenv('PATH',str(second)+os.pathsep+r'C:\Windows\System32')
    result,log,state,project=run_endpoint_probe(tmp_path,docker_probe,'npipe:////./pipe/docker_engine',action)
    assert result.returncode==0,result.stdout+result.stderr
    assert len(log.read_text(encoding='utf-8').splitlines())==4 and state.exists()
    assert not (project/'docker').exists()
