"""Selected SIDT reports: own immutable fixtures only, no installer/NX process."""
import hashlib
import json
from pathlib import Path

import pytest

from nxserver.safety import DomainError
from nxserver.store import canonical
from nxserver.upgrades import Upgrades,LIVE,MAX_REPORT_BYTES
from test_extraction import fake_setup


@pytest.fixture
def selected(tmp_path,monkeypatch):
    store,archive,calls=fake_setup(tmp_path,monkeypatch);manager=Upgrades(store)
    job=manager.create_extraction('2512','fixture');LIVE[job['id']].result(timeout=15)
    assert manager.get(job['id'])['status']=='completed'
    manager.select_extraction(job['id'],0,'fixture')
    report=manager.report(job['id']);path=store.root/'upgrades'/job['id']/'report.json'
    yield store,manager,job,report,path,calls
    LIVE[job['id']].result(timeout=15)


def replace_report(manager,job,path,raw):
    path.write_bytes(raw);manager._update(job['id'],report_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('fault',['duplicate_root','duplicate_nested','nonfinite','overflow_float'])
def test_selected_provider_rejects_ambiguous_or_nonfinite_json_even_with_matching_hash(selected,fault):
    store,manager,job,report,path,calls=selected;raw=canonical(report).encode()
    if fault=='duplicate_root':raw=b'{"version":"2406",'+raw[1:]
    elif fault=='duplicate_nested':raw=b'{"extra":{"x":1,"x":2},'+raw[1:]
    elif fault=='nonfinite':raw=b'{"extra":NaN,'+raw[1:]
    else:raw=b'{"extra":1e999,'+raw[1:]
    replace_report(manager,job,path,raw);audit=store.audit_entries()
    with pytest.raises(DomainError):store.sidt_root('2512')
    with pytest.raises(DomainError):manager.report(job['id'])
    assert store.audit_entries()==audit and calls==['-v','-t','-x']


def test_selected_provider_checks_real_byte_limit_before_opening_report(selected,monkeypatch):
    store,manager,job,report,path,calls=selected
    with path.open('wb') as stream:stream.truncate(MAX_REPORT_BYTES+1)
    original=Path.open
    def forbidden(candidate,*args,**kwargs):
        if candidate==path:pytest.fail('Oversize report must be rejected before any content read.')
        return original(candidate,*args,**kwargs)
    monkeypatch.setattr(Path,'open',forbidden)
    with pytest.raises(DomainError):store.sidt_root('2512')
    with pytest.raises(DomainError):manager.report(job['id'])
    assert calls==['-v','-t','-x']


@pytest.mark.parametrize('fault',['array','empty','folder','manifest','version'])
def test_selected_provider_returns_structured_error_for_invalid_extraction_metadata(selected,fault):
    store,manager,job,report,path,calls=selected
    if fault=='array':value=[]
    elif fault=='empty':value={}
    else:value={key:item for key,item in report.items() if key!=fault}
    replace_report(manager,job,path,canonical(value).encode())
    with pytest.raises(DomainError):store.sidt_root('2512')
    assert calls==['-v','-t','-x'] and not store.releases()


@pytest.mark.parametrize('fault',['wrong_version','folder_null','sidt_null','manifest_kind','manifest_path','manifest_size','manifest_sha','executed','activation'])
def test_contradictory_extraction_metadata_never_becomes_a_selected_provider(selected,fault):
    import copy
    store,manager,job,report,path,calls=selected;value=copy.deepcopy(report)
    if fault=='wrong_version':value['version']='2406'
    elif fault=='folder_null':value['folder']=None
    elif fault=='sidt_null':value['sidt_root']=None
    elif fault=='manifest_kind':value['manifest']={}
    elif fault=='manifest_path':value['manifest'][0]['path']=None
    elif fault=='manifest_size':value['manifest'][0]['size']=True
    elif fault=='manifest_sha':value['manifest'][0]['sha256']='bad'
    elif fault=='executed':value['installer_executed']=True
    else:value['automatic_activation']=True
    replace_report(manager,job,path,canonical(value).encode());audit=store.audit_entries()
    with pytest.raises(DomainError):store.sidt_root('2512')
    assert store.audit_entries()==audit and calls==['-v','-t','-x']


@pytest.mark.parametrize('fault',['missing_mode','missing_status','missing_hash','missing_request','wrong_request','wrong_id'])
def test_selected_job_identity_and_version_are_checked_before_report_path_lookup(selected,monkeypatch,fault):
    import nxserver.store as module
    store,manager,job,report,path,calls=selected
    with store.db() as db:
        value=json.loads(db.execute('SELECT payload FROM migration_jobs WHERE id=?',(job['id'],)).fetchone()[0])
        if fault.startswith('missing_'):value.pop(fault.removeprefix('missing_') if fault!='missing_hash' else 'report_sha256')
        elif fault=='wrong_request':value['request']['version']='2406'
        else:value['id']='../not-a-job'
        db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(value),job['id']))
    original=module.offline_path
    def guard(candidate):
        if Path(candidate).name=='report.json' and Path(candidate)!=path:
            pytest.fail('Untrusted payload ID must not choose a filesystem target.')
        return original(candidate)
    monkeypatch.setattr(module,'offline_path',guard)
    with pytest.raises(DomainError):store.sidt_root('2512')
    assert calls==['-v','-t','-x']


@pytest.mark.parametrize('encoding',['utf-8','utf-8-sig','utf-16','utf-32'])
def test_existing_supported_json_encodings_remain_readable_and_provider_is_read_only(selected,encoding):
    store,manager,job,report,path,calls=selected
    raw=canonical(report).encode(encoding);replace_report(manager,job,path,raw)
    before=store.audit_entries()
    assert manager.report(job['id'])==report and store.sidt_root('2512')==Path(report['sidt_root'])
    assert path.read_bytes()==raw and store.audit_entries()==before


@pytest.mark.parametrize('raw',[b'{"x":NaN}',b'{"x":Infinity}',b'{"x":-Infinity}',b'{"x":1e999}',b'{"x":-1e999}',
    b'{"x":1,"x":2}',b'{"x":{"y":1,"y":2}}',b'[]',b'null',b'\xff'])
def test_shared_decoder_rejects_ambiguous_nonfinite_or_unsupported_json(raw):
    from nxserver.migration_report import decode
    with pytest.raises(DomainError):decode(raw)


@pytest.mark.parametrize('value',[None,True,12,'bad','A'*64])
def test_unbound_report_never_reaches_file_reader(monkeypatch,value):
    import nxserver.migration_report as module
    monkeypatch.setattr(module,'read_bounded_file',lambda *args,**kwargs:pytest.fail('Missing SHA binding must fail before filesystem access.'))
    with pytest.raises(DomainError):module.read('not-used',value)


def test_shared_decoder_keeps_finite_numbers_unknown_metadata_and_string_values():
    from nxserver.migration_report import decode
    value=decode(b'{"future":{"text":"NaN","numbers":[1.25,1e100,-0.0]},"count":4}')
    assert value=={'future':{'text':'NaN','numbers':[1.25,1e100,-0.0]},'count':4}
