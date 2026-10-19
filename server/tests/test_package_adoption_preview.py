"""Early adoption conflicts and bounded reads; synthetic offline folders only."""
import os

import pytest

from nxserver.filepackages import FilePackages
from nxserver.integrity import snapshot
from nxserver.safety import DomainError
from test_filepackage_commit import setup
from test_filepackages import packages


def test_baseline_file_blocking_a_customer_subdirectory_is_a_preview_conflict(tmp_path):
    store,_,source,standard,_=setup(tmp_path,'template');relative='UGII/templates/Sub/ITEM.prt'
    (source/relative).parent.mkdir();(source/relative).write_bytes(b'Customer fixture')
    (standard/'UGII/templates/Sub').write_bytes(b'Standard file, not directory')
    release=store.import_release('test','2512','Nested source','fixture');before=snapshot(store)['sha256']
    report,_=FilePackages(store).adoption_preview(release['id'],'template',[relative])
    assert not report['can_adopt'] and report['conflicts'] and snapshot(store)['sha256']==before


def test_oversized_source_is_not_hashed_before_a_blocking_preview(packages,monkeypatch):
    from nxserver import filepackages
    _,store,_,source,*_=packages;relative='UGII/templates/LARGE.prt';path=source/relative;path.parent.mkdir(parents=True);path.write_bytes(b'Synthetic oversized bytes')
    release=store.import_release('test','2512','Large fixture','fixture');monkeypatch.setattr(filepackages,'MAX_BYTES',1)
    def forbidden(*args):raise AssertionError('Out-of-bound source/baseline must not be hashed')
    monkeypatch.setattr(filepackages,'sha256',forbidden)
    report,_=FilePackages(store).adoption_preview(release['id'],'template',[relative])
    assert not report['can_adopt'] and not store.asset_inventory()['items']


@pytest.mark.skipif(os.name=='nt',reason='Two physical case-distinct directories require a case-sensitive fixture filesystem.')
def test_directory_case_collision_is_blocked_before_any_asset_copy(tmp_path):
    store,_,source,standard,_=setup(tmp_path,'template');paths=['UGII/templates/Sub/A.prt','UGII/templates/sub/B.prt']
    for relative in paths:
        path=source/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'Customer fixture')
    release=store.import_release('test','2512','Case fixture','fixture');manager=FilePackages(store);before=snapshot(store)['sha256']
    report,_=manager.adoption_preview(release['id'],'template',paths)
    assert not report['can_adopt'] and any('casing' in item['reason'] for item in report['conflicts'])
    with pytest.raises(DomainError):manager.adopt(release['id'],'template',paths,'Never copy',None,report['preview_hash'],'fixture')
    assert snapshot(store)['sha256']==before and not store.asset_inventory()['items']


def test_noncanonical_case_alias_is_rejected_before_source_byte_reads(tmp_path,monkeypatch):
    from nxserver import filepackages
    store,release,*_=setup(tmp_path,'template');before=snapshot(store)['sha256']
    def forbidden(*args):raise AssertionError('A case alias must not be resolved against the filesystem')
    monkeypatch.setattr(filepackages,'file_content',forbidden)
    with pytest.raises(DomainError,match='canonical path'):FilePackages(store).adoption_preview(release['id'],'template',['UGII/templates/custom.prt'])
    assert snapshot(store)['sha256']==before


def test_oversized_baseline_is_a_conflict_without_reading_its_bytes(tmp_path,monkeypatch):
    from nxserver import filepackages
    store,release,source,standard,*_=setup(tmp_path,'template');relative='UGII/templates/CUSTOM.prt'
    (standard/relative).write_bytes(b'Standard oversized fixture')
    monkeypatch.setattr(filepackages,'MAX_BASELINE_BYTES',len((source/relative).read_bytes()))
    # Make the baseline strictly larger than the source limit.
    (standard/relative).write_bytes(b'B'*(filepackages.MAX_BASELINE_BYTES+1))
    original=filepackages.read_bounded_file;fingerprint=filepackages.file_fingerprint;calls=[]
    def counted(path,**kwargs):
        calls.append(str(path));return original(path,**kwargs)
    monkeypatch.setattr(filepackages,'read_bounded_file',counted)
    def fingerprinted(path,**kwargs):calls.append(str(path));return fingerprint(path,**kwargs)
    monkeypatch.setattr(filepackages,'file_fingerprint',fingerprinted)
    report,_=FilePackages(store).adoption_preview(release['id'],'template',[relative])
    assert not report['can_adopt'] and report['baseline_inputs'][relative]['baseline_path'] is None
    assert report['baseline_inputs'][relative]['read_error']
    assert len(calls)==2  # Source bytes, then a baseline stat/bound check (no read).


def test_empty_stock_placeholder_remains_comparable_and_does_not_prevent_adoption(tmp_path):
    import hashlib
    store,release,source,standard,*_=setup(tmp_path,'template');relative='UGII/templates/CUSTOM.prt'
    (standard/relative).write_bytes(b'');manager=FilePackages(store)
    report,_=manager.adoption_preview(release['id'],'template',[relative])
    assert report['can_adopt'] and report['baseline_inputs'][relative]['sha256']==hashlib.sha256(b'').hexdigest()
    resource=manager.adopt(release['id'],'template',[relative],'Customer replaces empty placeholder',None,report['preview_hash'],'fixture')
    assert resource['cam_status']=='draft'


def test_larger_stock_file_does_not_inherit_the_attachment_size_limit(tmp_path,monkeypatch):
    from nxserver import filepackages
    store,release,source,standard,*_=setup(tmp_path,'template');relative='UGII/templates/CUSTOM.prt'
    source_size=(source/relative).stat().st_size;monkeypatch.setattr(filepackages,'MAX_BYTES',source_size)
    (standard/relative).write_bytes(b'S'*(source_size+10))
    report,_=FilePackages(store).adoption_preview(release['id'],'template',[relative])
    assert report['can_adopt'] and report['baseline_inputs'][relative]['sha256']


@pytest.mark.parametrize('entry',['source','baseline'])
def test_valid_source_and_baseline_use_the_same_bounded_reader(tmp_path,monkeypatch,entry):
    from nxserver import filepackages
    store,release,source,standard,*_=setup(tmp_path,'template');relative='UGII/templates/CUSTOM.prt'
    if entry=='baseline':(standard/relative).write_bytes(b'Different standard fixture')
    def forbidden(*args):raise AssertionError('Adoption previews must not use the old unbounded hash helper')
    monkeypatch.setattr(filepackages,'sha256',forbidden)
    report,_=FilePackages(store).adoption_preview(release['id'],'template',[relative])
    assert report['can_adopt'] and not store.asset_inventory()['items']
