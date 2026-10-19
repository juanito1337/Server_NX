"""Synthetic output inventory failures stay readable diagnostics, never a real NX run."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nxserver import nxworker
from nxserver.safety import DomainError
from test_geometry import pilot,HEADERS
from test_worker_reads import job_files
from test_reader_limits import observe


@pytest.fixture
def fake_import(job_files,tmp_path,monkeypatch,pilot):
    directory,job=job_files;client,store=pilot
    base=tmp_path/'fake-installation';settings=base/'STEP214UG/step214ug.def'
    settings.parent.mkdir(parents=True);settings.write_text('LOG_FILE = source.log\n',encoding='utf-8')
    monkeypatch.setattr(nxworker,'check_environment',lambda *args:base)
    part=SimpleNamespace(FullPath=str(directory/'output/tool.prt'),PartUnits='Millimeters',IsFullyLoaded=True,
        Bodies=[SimpleNamespace(JournalIdentifier='BODY 1',Name='synthetic',IsSolidBody=True,IsConvergentBody=False,
                                GetFaces=lambda:[],Layer=1,Color=42)],ComponentAssembly=SimpleNamespace(RootComponent=None))
    class Importer:
        ObjectTypes=SimpleNamespace()
        def SetMode(self,*args):pass
        def Commit(self):Path(self.OutputFile).write_bytes(b'Synthetic PRT bytes, not actual CAD')
        def Destroy(self):pass
    class Parts:
        Work=Display=None
        def __init__(self):self.opened=False
        def __iter__(self):return iter([part] if self.opened else [])
        def OpenBaseDisplay(self,*args):self.opened=True;return part,SimpleNamespace(NumberUnloadedParts=0,Dispose=lambda:None)
    session=SimpleNamespace(Parts=Parts(),IsCamSessionInitialized=lambda:False,DexManager=SimpleNamespace(CreateStep214Importer=Importer))
    api=SimpleNamespace(BaseImporter=SimpleNamespace(Mode=SimpleNamespace(NativeFileSystem='native'),PartUnitOption=SimpleNamespace(NotSet='not-set')),
                        Step214Importer=SimpleNamespace(ImportToOption=SimpleNamespace(NewPart='new')))
    return directory,job,session,api,client,store


def test_output_hashes_do_not_read_whole_files_into_memory(fake_import,monkeypatch):
    directory,job,session,api,*_=fake_import
    reads=observe(monkeypatch,directory/'output/tool.prt')
    result=nxworker.run_job(directory,session,api)
    assert result['status']=='needs_structure' and not result['automatic_cam_approval']
    assert reads and all(0<amount<=1024*1024 for amount,_ in reads)


@pytest.mark.parametrize('failure',['open','short','wrong_descriptor','enumeration'])
def test_output_inventory_failure_is_saved_failed_diagnostic_not_an_exception(fake_import,monkeypatch,failure):
    directory,job,session,api,client,store=fake_import
    part=directory/'output/tool.prt';original_open=Path.open;original_cwd=Path.cwd()
    if failure=='open':
        def fail(path,*args,**kwargs):
            if path==part and (args[0] if args else kwargs.get('mode','r'))=='rb':raise PermissionError('Synthetic output read failure')
            return original_open(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',fail)
    elif failure=='short':observe(monkeypatch,part,short=True)
    elif failure=='wrong_descriptor':
        other=directory/'other-synthetic.prt';other.write_bytes(b'Synthetic PRT bytes, not actual CAD')
        observe(monkeypatch,part,alternate=other)
    else:monkeypatch.setattr(nxworker,'tree_files',lambda *args:(_ for _ in ()).throw(DomainError('Synthetic output enumeration failure')))
    result=nxworker.run_job(directory,session,api)
    assert result['status']=='failed' and result['errors'] and not result['automatic_cam_approval']
    assert all(item['path']!='tool.prt' for item in result['files'])
    assert part.is_file() and Path.cwd()==original_cwd
    assert json.loads((directory/'output/report.json').read_text(encoding='utf-8'))==result
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/report',headers=HEADERS,json={'report':result})
    assert response.status_code==200,response.text
    assert store.resources()[0]['cam_status']=='draft' and store.active()['release_id'] is None


def test_file_count_limit_records_failed_partial_inventory_that_can_be_uploaded(fake_import,monkeypatch):
    directory,job,session,api,client,store=fake_import
    monkeypatch.setattr(nxworker,'MAX_REPORT_FILES',1)
    result=nxworker.run_job(directory,session,api)
    assert result['status']=='failed' and len(result['files'])==1
    assert any('partial file inventory' in message for message in result['errors'])
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/report',headers=HEADERS,json={'report':result})
    assert response.status_code==200,response.text
    assert (directory/'output/tool.prt').exists() and store.resources()[0]['cam_status']=='draft'
