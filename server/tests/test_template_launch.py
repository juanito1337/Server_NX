"""Actual Windows starter/palette regressions; synthetic files, never Siemens NX."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import xml.etree.ElementTree as ET

from test_testlaunch import executable_launcher,launcher,POWERSHELL

pytestmark=pytest.mark.skipif(not POWERSHELL,reason='Windows PowerShell/CMD fixture; never actual NX.')
ASSETS=Path(__file__).parents[1]/'nxserver/testlaunch_assets'
TEMPLATE_DIRS={
    'UGII_CAM_TEMPLATE_PART_DIR':'MACH/resource/template_part',
    'UGII_CAM_TEMPLATE_PART_METRIC_DIR':'MACH/resource/template_part/metric',
    'UGII_CAM_TEMPLATE_PART_ENGLISH_DIR':'MACH/resource/template_part/english',
    'UGII_CAM_TEMPLATE_IMAGE_DIR':'MACH/resource/template_part/images',
    'UGII_CAM_TEMPLATE_SET_DIR':'MACH/resource/template_set',
    'UGII_CAM_CONFIG_DIR':'MACH/resource/configuration',
}


def test_child_cannot_inherit_old_local_cam_template_directories(executable_launcher):
    store,release,extracted,install,runtime,output,env,prepare=executable_launcher
    template_output=output.parent/'template-environment.txt';env['NX_TEST_TEMPLATE_OUTPUT']=str(template_output)
    for name in TEMPLATE_DIRS:env[name]=r'C:\OfflineOldNX2406\wrong_templates'
    result=subprocess.run(prepare[:-1],capture_output=True,text=True,env=env,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    values=dict(line.split('=',1) for line in template_output.read_text(encoding='utf-8-sig').splitlines())
    assert set(values)==set(TEMPLATE_DIRS)
    for name,relative in TEMPLATE_DIRS.items():assert Path(values[name])==store.release_folder(release['id'])/relative
    assert store.manifest(store.release_folder(release['id']))==release['manifest'] and store.active()['release_id'] is None


@pytest.fixture
def palette(tmp_path_factory):
    root=tmp_path_factory.mktemp('pt');stage=root/'s';stage.mkdir();system=root/'system.pax';custom=root/'custom.pax';out=root/'out'/'merged.pax'
    system.write_text('<Palette xmlns="urn:test"><PaletteEntry id="STD"/></Palette>',encoding='utf-8')
    references={'preview':'${UGII_CAM_TEMPLATE_PART_METRIC_DIR}preview.png','part':'${UGII_CAM_TEMPLATE_PART_METRIC_DIR}part.prt','configuration':'${UGII_CAM_CONFIG_DIR}machine.dat'}
    for relative in ('MACH/resource/template_part/metric/preview.png','MACH/resource/template_part/metric/part.prt','MACH/resource/configuration/machine.dat'):
        path=stage/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Synthetic reference; not actual NX data')
    text='<Palette xmlns="urn:test"><PaletteEntry id="CUSTOM"><Presentation><PreviewImage location="'+references['preview']+'"/></Presentation><ObjectData><Filename>'+references['part']+'</Filename><Configuration>'+references['configuration']+'</Configuration></ObjectData></PaletteEntry></Palette>'
    custom.write_text(text,encoding='utf-8')
    command=[POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ASSETS/'New-NX2512TestPalette.ps1'),
        '-SystemPalette',str(system),'-CustomPalette',str(custom),'-OutputPalette',str(out),'-StagingRoot',str(stage)]
    return root,stage,system,custom,out,command


@pytest.mark.parametrize('field',['Filename','Configuration','PreviewImage'])
def test_duplicate_reference_nodes_cannot_hide_unchecked_network_reference(palette,field):
    root,stage,system,custom,out,command=palette;text=custom.read_text()
    if field=='PreviewImage':text=text.replace('</Presentation>','<PreviewImage location="W:/SiemensShare/nx2406/do-not-read.png"/></Presentation>')
    else:text=text.replace('</ObjectData>','<'+field+'>W:/SiemensShare/nx2406/do-not-read</'+field+'></ObjectData>')
    custom.write_text(text);result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode!=0,result.stdout+result.stderr
    assert not out.exists()


def test_valid_palette_is_merged_without_changing_sources_and_all_valid_references_are_kept(palette):
    root,stage,system,custom,out,command=palette;before=(system.read_bytes(),custom.read_bytes())
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    document=ET.fromstring(out.read_bytes());assert [entry.attrib['id'] for entry in document]==['STD','CUSTOM']
    assert (system.read_bytes(),custom.read_bytes())==before
    assert '${UGII_CAM_TEMPLATE_PART_METRIC_DIR}' in out.read_text(encoding='utf-8')
    same=subprocess.run(command,capture_output=True,text=True,timeout=30);assert same.returncode!=0 and 'already exists' in same.stderr


@pytest.mark.parametrize('field',['Filename','Configuration','PreviewImage'])
def test_every_valid_repeated_reference_is_checked_but_not_silently_dropped(palette,field):
    root,stage,system,custom,out,command=palette;text=custom.read_text()
    if field=='PreviewImage':text=text.replace('</Presentation>','<PreviewImage location="${UGII_CAM_TEMPLATE_PART_METRIC_DIR}preview.png"/></Presentation>')
    else:
        value='${UGII_CAM_TEMPLATE_PART_METRIC_DIR}part.prt' if field=='Filename' else '${UGII_CAM_CONFIG_DIR}machine.dat'
        text=text.replace('</ObjectData>','<'+field+'>'+value+'</'+field+'></ObjectData>')
    custom.write_text(text);result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert sum(node.tag.rsplit('}',1)[-1]==field for node in ET.fromstring(out.read_bytes()).iter())==2


@pytest.mark.parametrize('target',['relative','foreign','missing','unknown_variable','traversal'])
def test_reference_outside_snapshot_or_unknown_is_not_guessed(palette,target):
    root,stage,system,custom,out,command=palette;text=custom.read_text()
    outside=root/'outside.prt';outside.write_text('External fixture must not become staging evidence')
    value={'relative':'outside.prt','foreign':str(outside),'missing':'${UGII_CAM_TEMPLATE_PART_METRIC_DIR}missing.prt',
        'unknown_variable':'${UNKNOWN_TEMPLATE_DIR}part.prt','traversal':'${UGII_CAM_TEMPLATE_PART_DIR}../../../../outside.prt'}[target]
    custom.write_text(text.replace('${UGII_CAM_TEMPLATE_PART_METRIC_DIR}part.prt',value))
    result=subprocess.run(command,capture_output=True,text=True,cwd=root,timeout=30)
    assert result.returncode!=0 and not out.exists()
    assert outside.read_text()=='External fixture must not become staging evidence'


@pytest.mark.parametrize('which',['system','customer'])
@pytest.mark.parametrize('mode',['internal_dtd','external_dtd','wrong_root','oversize'])
def test_bounded_xml_reader_rejects_dtd_and_invalid_root_before_output(palette,which,mode):
    root,stage,system,custom,out,command=palette;path=system if which=='system' else custom
    if mode=='internal_dtd':text='<!DOCTYPE Palette [<!ENTITY sample "synthetic">]>'+path.read_text()
    elif mode=='external_dtd':text='<!DOCTYPE Palette SYSTEM "http://127.0.0.1:9/never-fetch.dtd">'+path.read_text()
    elif mode=='wrong_root':text='<NotPalette/>'
    else:text='<Palette>'+'x'*(20*1024*1024)+'</Palette>'
    path.write_text(text);result=subprocess.run(command,capture_output=True,text=True,encoding='oem',errors='replace',timeout=30)
    assert result.returncode!=0 and not out.exists()
    if 'dtd' in mode:assert 'DTD' in result.stderr


@pytest.mark.parametrize('check_only',[True,False])
def test_uncontrolled_network_environment_is_checked_without_creating_runtime_files(executable_launcher,check_only):
    store,release,extracted,install,runtime,output,env,prepare=executable_launcher;env['UGII_UNKNOWN_TEMPLATE_PATH']=r'W:\SiemensShare\nx2406'
    result=subprocess.run(prepare if check_only else prepare[:-1],capture_output=True,text=True,env=env,timeout=60)
    assert result.returncode!=0 and 'UGII_UNKNOWN_TEMPLATE_PATH' in result.stderr
    assert not output.exists() and not (runtime/'temp').exists()
