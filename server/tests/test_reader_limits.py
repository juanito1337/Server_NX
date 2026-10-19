"""Bounded readers and XML rejection on synthetic offline files only."""
import hashlib
from contextlib import contextmanager
from pathlib import Path

import pytest

import nxserver.store as store_module
from nxserver import template_refs
from nxserver.filepackages import FilePackages
from nxserver.safety import DomainError
from test_filepackages import packages,body
from test_management import HEADERS,pilot


def observe(monkeypatch,path,before_read=None,alternate=None,short=False):
    original=Path.open;reads=[]
    @contextmanager
    def opened(candidate,*args,**kwargs):
        mode=args[0] if args else kwargs.get('mode','r')
        with original(alternate if candidate==path and alternate and 'r' in mode else candidate,*args,**kwargs) as stream:
            if candidate!=path:yield stream;return
            class Reader:
                def __getattr__(self,name):return getattr(stream,name)
                def read(self,amount=-1):
                    if before_read:before_read(original)
                    raw=stream.read(amount)
                    if short:raw=raw[:10]
                    reads.append((amount,len(raw)))
                    return raw
            yield Reader()
    monkeypatch.setattr(Path,'open',opened)
    return reads


def test_log_read_is_bounded_not_read_to_eof(pilot,monkeypatch):
    _,store,_,log=pilot;expected=hashlib.sha256(log.read_bytes()).hexdigest()
    reads=observe(monkeypatch,log)
    report=store.scan_log('test-log')
    assert report['sha256']==expected
    assert reads==[(store_module.MAX_LOG_BYTES+1,log.stat().st_size)]


def test_growing_log_never_reads_more_than_limit_plus_one(pilot,monkeypatch):
    _,store,_,log=pilot
    monkeypatch.setattr(store_module,'MAX_LOG_BYTES',512)
    def grow(original):
        with original(log,'ab') as stream:stream.write(b'\n'+b'x'*1024)
    reads=observe(monkeypatch,log,before_read=grow)
    with pytest.raises(DomainError) as error:store.scan_log('test-log')
    assert error.value.status==409
    assert reads and reads[0][0]==513 and reads[0][1]<=513


def test_short_log_read_with_unchanged_path_is_not_a_complete_scan(pilot,monkeypatch):
    _,store,_,log=pilot;observe(monkeypatch,log,short=True)
    with pytest.raises(DomainError) as error:store.scan_log('test-log')
    assert error.value.status==409


def test_wrong_open_descriptor_cannot_substitute_another_log(pilot,monkeypatch):
    _,store,_,log=pilot
    other=log.parent/'other-synthetic.syslog';other.write_bytes(b'NX_VERSION = 2512\nUG_APP_MANUFACTURING\n')
    observe(monkeypatch,log,alternate=other)
    with pytest.raises(DomainError) as error:store.scan_log('test-log')
    assert error.value.status==409


def test_log_disappearing_after_presence_check_is_a_conflict(pilot,monkeypatch):
    _,store,_,log=pilot;original=Path.stat;calls=[]
    def disappear(path,*args,**kwargs):
        if path==log and kwargs.get('follow_symlinks',True):
            calls.append(True)
            if len(calls)==2:raise FileNotFoundError('Synthetic file disappeared')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',disappear)
    with pytest.raises(DomainError) as error:store.scan_log('test-log')
    assert error.value.status==409


XML_FILES=[('template','UGII/templates/CUSTOM.pax'),('template','UGII/templates/CUSTOM.xml'),
           ('ui_standard','UGII/menus/roles/CUSTOM.mtx'),('ui_standard','UGII/menus/roles/CUSTOM.dpv')]


@pytest.mark.parametrize('kind,path',XML_FILES)
@pytest.mark.parametrize('case',['dtd','nodes','depth'])
@pytest.mark.parametrize('status',['draft','approved'])
def test_package_xml_bounds_block_save_without_resource_or_folder_writes(packages,monkeypatch,kind,path,case,status):
    client,store,release,source,_=packages
    raw={'dtd':b'<!DOCTYPE Root [<!ENTITY test "synthetic">]><Root>&test;</Root>',
         'nodes':b'<Root><Entry/><Entry/></Root>',
         'depth':b'<Root>'+b'<Entry>'*65+b'</Entry>'*65+b'</Root>'}[case]
    if case=='nodes':monkeypatch.setattr(template_refs,'MAX_NODES',2)
    value=body(store,kind,path,status=status,filename=Path(path).name,content=raw)
    before=store.audit_entries();source_before=store.manifest(source);release_before=store.get_release(release['id'])
    result=client.post('/api/v1/resources',headers=HEADERS,json=value)
    assert result.status_code==400,result.text
    assert 'XML' in result.json()['detail']
    assert store.resources()==[] and store.audit_entries()==before
    assert store.get_release(release['id'])==release_before and store.manifest(source)==source_before
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('kind,path',XML_FILES)
def test_invalid_package_xml_is_visible_in_adoption_preview_without_writes(packages,kind,path):
    _,store,_,source,_=packages
    file=source/path;file.parent.mkdir(parents=True,exist_ok=True)
    file.write_bytes(b'<!DOCTYPE Root [<!ENTITY test "synthetic">]><Root>&test;</Root>')
    target=store.import_release('test','2512','Synthetic customer XML','fixture')
    before=store.audit_entries();manifest=store.manifest(store.release_folder(target['id']))
    report,_=FilePackages(store).adoption_preview(target['id'],kind,[path])
    assert not report['can_adopt'] and any('XML' in item['reason'] for item in report['conflicts'])
    assert store.audit_entries()==before and store.resources()==[] and store.active()['release_id'] is None
    assert store.manifest(store.release_folder(target['id']))==manifest


@pytest.mark.parametrize('kind,path',[('template','UGII/templates/CUSTOM.pax'),('ui_standard','UGII/menus/roles/CUSTOM.mtx')])
def test_legacy_approved_xml_blocks_export_and_reports_upstream_incompleteness_once(packages,monkeypatch,kind,path):
    import nxserver.filepackages as module
    _,store,parent,*_=packages
    value=body(store,kind,path,filename=Path(path).name,
        content=b'<!DOCTYPE Root [<!ENTITY test "synthetic">]><Root>&test;</Root>')
    # Simulate the old parser policy on a properly audited, isolated resource;
    # do not forge production metadata, alter a real old revision, or approve NX.
    with monkeypatch.context() as older:
        older.setattr(module,'checked_xml',lambda *args:None)
        resource=store.save_resource(value,'synthetic-older-policy')
    before=store.audit_entries();manifest=store.manifest(store.release_folder(parent['id']))
    report=store.preview_export(parent['id'])
    assert not report['can_export'] and len(report['conflicts'])==1
    assert report['template_references']['complete'] is False
    assert report['template_references']['errors'][0]['upstream_conflict'] is True
    assert store.audit_entries()==before and store.resources()[0]==resource and len(store.releases())==1
    assert store.manifest(store.release_folder(parent['id']))==manifest and store.active()['release_id'] is None
