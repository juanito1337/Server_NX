"""Effective selected SIDT provider for package ownership; own inert files only."""
from pathlib import Path

import pytest

from nxserver.filepackages import FilePackages
from nxserver.safety import DomainError
from nxserver.store import TOOL_DATABASE
from nxserver.upgrades import LIVE,Upgrades
from test_extraction import fake_setup
from test_filepackage_commit import PATHS
from test_management import DATABASE
from test_selected_extraction_reports import replace_report


@pytest.fixture
def selected_packages(tmp_path,monkeypatch):
    from nxserver import extraction
    store,archive,calls=fake_setup(tmp_path,monkeypatch)
    payload=tmp_path/'payload'/'{app}'/'nx2512';added=[]
    source,standard=tmp_path/'source',tmp_path/'standard'
    for kind,relative in PATHS.items():
        content=b'<Synthetic stock="SIDT"/>' if relative.endswith(('.xml','.mtx')) else b'Synthetic SIDT stock, not CAD'
        file=source/relative;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(content)
        base=standard/relative;base.parent.mkdir(parents=True,exist_ok=True)
        base.write_bytes(b'<Synthetic stock="NX"/>' if relative.endswith(('.xml','.mtx')) else b'Synthetic NX stock before overlay')
        if relative.startswith('UGII/'):
            overlay=payload/'applications/nx/ugii'/relative[len('UGII/'):]
            overlay.parent.mkdir(parents=True,exist_ok=True);overlay.write_bytes(content);added.append(overlay)
    database=source/TOOL_DATABASE;database.parent.mkdir(parents=True,exist_ok=True);database.write_text(DATABASE)
    original=extraction.run_reader
    def reader(store,tool,arguments,child=lambda *args:None):
        result=original(store,tool,arguments,child)
        if arguments[0]=='-v':
            result+='\n'+'\n'.join(' '+str(file.stat().st_size)+' 2026-01-09 12:00 '+
                file.relative_to(payload.parent.parent).as_posix().replace('/','\\') for file in added)
        return result
    monkeypatch.setattr(extraction,'run_reader',reader)
    store.sources['test']=source;store.folders.sources['test']=source;store.standards['2512']=standard
    manager=Upgrades(store);job=manager.create_extraction('2512','fixture');LIVE[job['id']].result(timeout=15)
    assert manager.get(job['id'])['status']=='completed',manager.get(job['id'])
    manager.select_extraction(job['id'],0,'fixture');report=manager.report(job['id'])
    release=store.import_release('test','2512','Selected SIDT fixture','fixture')
    assert not store.sidts
    yield {'store':store,'manager':manager,'job':job,'report':report,'source':source,'standard':standard,
        'release':release,'sidt':Path(report['sidt_root']),'report_path':store.root/'upgrades'/job['id']/'report.json','calls':calls}
    LIVE[job['id']].result(timeout=15)


def parent_for(store,kind):
    if kind not in ('machine','fixture'):return None
    return store.save_resource({'kind':kind,'name':'Existing DAT draft','cam':{},'erp':None,'cam_status':'draft','asset_ids':[]},'fixture')['id']


@pytest.mark.parametrize('kind',['template','ui_standard'])
@pytest.mark.parametrize('baseline',['missing','different'])
def test_unchanged_selected_sidt_overlay_is_not_customer_ownership(selected_packages,kind,baseline):
    value=selected_packages;store=value['store'];relative=PATHS[kind]
    if baseline=='missing':(value['standard']/relative).unlink()  # Own disposable stock fixture only.
    audit=store.audit_entries();report,_=FilePackages(store).adoption_preview(value['release']['id'],kind,[relative])
    assert not report['can_adopt'] and any('Unchanged Siemens/SIDT' in row['reason'] for row in report['conflicts'])
    assert report['source_state']['sidt_root']==str(value['sidt'])
    assert report['baseline_inputs'][relative]['baseline_path']==str(value['sidt']/'applications/nx/ugii'/relative[len('UGII/'):])
    assert store.resources()==[] and store.audit_entries()==audit and not store.asset_inventory()['items']


@pytest.mark.parametrize('kind',['template','ui_standard'])
def test_customer_difference_is_bound_to_selected_overlay_and_adopted_as_draft(selected_packages,kind):
    value=selected_packages;store=value['store'];relative=PATHS[kind]
    content=b'<Customer fixture="only"/>' if kind=='ui_standard' else b'Synthetic customer part, not CAD'
    (value['source']/relative).write_bytes(content)
    release=store.import_release('test','2512','Customer modification','fixture');manager=FilePackages(store)
    before={name:store.manifest(value[name]) for name in ('source','standard','sidt')}
    report,_=manager.adoption_preview(release['id'],kind,[relative])
    assert report['can_adopt'] and report['source_state']['sidt_root']==str(value['sidt'])
    resource=manager.adopt(release['id'],kind,[relative],'Customer package',None,report['preview_hash'],'fixture')
    package=resource['file_package'];entry=package['entries'][0]
    assert resource['cam_status']=='draft' and package['checks']=={} and package['asset_hashes']=={}
    assert package['provenance']['baseline_inputs']==report['baseline_inputs']
    assert store.asset(entry['asset_id'])[1].read_bytes()==content
    assert all(store.manifest(value[name])==manifest for name,manifest in before.items())
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('kind',list(PATHS))
def test_invalid_selected_provider_is_not_silently_skipped_for_any_package_kind(selected_packages,kind):
    value=selected_packages;store=value['store'];parent=parent_for(store,kind);audit=store.audit_entries()
    replace_report(value['manager'],value['job'],value['report_path'],b'{}')
    with pytest.raises(DomainError):FilePackages(store).adoption_preview(value['release']['id'],kind,[PATHS[kind]],parent)
    assert store.audit_entries()==audit and not store.asset_inventory()['items']


@pytest.mark.parametrize('kind',['template','ui_standard'])
@pytest.mark.parametrize('drift',['corrupt_report','reselect'])
def test_selected_provider_is_rechecked_inside_final_ownership_commit(selected_packages,monkeypatch,kind,drift):
    value=selected_packages;store=value['store'];relative=PATHS[kind]
    (value['source']/relative).write_bytes(b'<Customer/>' if kind=='ui_standard' else b'Own customer fixture')
    release=store.import_release('test','2512','Customer modification','fixture');manager=FilePackages(store)
    report,_=manager.adoption_preview(release['id'],kind,[relative]);assert report['can_adopt']
    replacement=None
    if drift=='reselect':
        replacement=value['manager'].create_extraction('2512','fixture');LIVE[replacement['id']].result(timeout=15)
        assert value['manager'].get(replacement['id'])['status']=='completed'
    original=store.save_resource
    def mutate(*args,**kwargs):
        if drift=='corrupt_report':replace_report(value['manager'],value['job'],value['report_path'],b'{}')
        else:value['manager'].select_extraction(replacement['id'],1,'fixture')
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'save_resource',mutate)
    with pytest.raises(DomainError) as failure:manager.adopt(release['id'],kind,[relative],'Never commit',None,report['preview_hash'],'fixture')
    assert failure.value.status==409 and store.resources()==[] and store.active()['release_id'] is None
    assert len(store.asset_inventory()['items'])==1  # Copied immutable intent retained, not an owner.


@pytest.mark.parametrize('kind',['template','ui_standard'])
def test_selected_provider_has_priority_over_configured_raw_sidt(selected_packages,kind,tmp_path):
    value=selected_packages;store=value['store'];fallback=tmp_path/'fallback';fallback.mkdir()
    store.sidts['2512']=fallback
    report,_=FilePackages(store).adoption_preview(value['release']['id'],kind,[PATHS[kind]])
    assert not report['can_adopt'] and report['source_state']['sidt_root']==str(value['sidt'])
