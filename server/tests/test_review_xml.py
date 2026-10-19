"""Bounded technical PAX parsing, not template semantics or NX qualification."""
import pytest
from contextlib import contextmanager
from pathlib import Path

from nxserver import template_refs
from test_management import HEADERS, pilot, imported


@pytest.mark.parametrize('case',['internal_dtd','external_dtd','bytes','nodes','depth'])
def test_untrusted_pax_bounds_block_technical_validation(pilot,monkeypatch,case):
    client,store,source,_=pilot
    raw={
        'internal_dtd':b'<!DOCTYPE Palette [<!ENTITY test "synthetic">]><Palette>&test;</Palette>',
        'external_dtd':b'<!DOCTYPE Palette SYSTEM "https://invalid.example/no-fetch.dtd"><Palette/>',
        'bytes':b'<Palette>'+b' '*60+b'</Palette>',
        'nodes':b'<Palette><Entry/><Entry/></Palette>',
        'depth':b'<Palette>'+b'<Entry>'*65+b'</Entry>'*65+b'</Palette>',
    }[case]
    path=source/'synthetic.pax';path.write_bytes(raw)
    release=imported(client);source_before=store.manifest(source)
    if case=='bytes':monkeypatch.setattr(template_refs,'MAX_BYTES',40)
    if case=='nodes':monkeypatch.setattr(template_refs,'MAX_NODES',2)
    response=client.post('/api/v1/releases/'+release['id']+'/validate',headers=HEADERS)
    assert response.status_code==200,response.text
    report=response.json()
    assert not report['integrity_passed'] and any('PAX' in error for error in report['errors'])
    assert store.get_release(release['id'])['status']=='blocked' and store.active()['release_id'] is None
    assert store.manifest(source)==source_before


@pytest.mark.parametrize('raw',[b'<Palette><PaletteEntry id="synthetic"/></Palette>',
                               b'<Palette xmlns="urn:synthetic"><PaletteEntry/></Palette>',
                               b'<VendorSpecificXml><Entry/></VendorSpecificXml>'])
def test_generic_validation_does_not_invent_new_palette_schema_requirements(pilot,raw):
    client,store,source,_=pilot
    (source/'synthetic.pax').write_bytes(raw)
    release=imported(client)
    report=client.post('/api/v1/releases/'+release['id']+'/validate',headers=HEADERS).json()
    assert report['integrity_passed'] and report['functional_validation_required']
    assert store.get_release(release['id'])['status']=='validated' and store.active()['release_id'] is None


def test_pax_reader_never_uses_an_unbounded_read(tmp_path,monkeypatch):
    path=tmp_path/'synthetic.pax';path.write_bytes(b'<Palette/>')
    original=Path.open;reads=[]
    @contextmanager
    def observe(candidate,*args,**kwargs):
        with original(candidate,*args,**kwargs) as stream:
            if candidate!=path:yield stream;return
            class Reader:
                def read(self,amount=-1):
                    reads.append(amount)
                    assert amount==template_refs.MAX_BYTES+1
                    return stream.read(amount)
            yield Reader()
    monkeypatch.setattr(Path,'open',observe)
    assert template_refs.read_xml(path).tag=='Palette'
    assert reads==[template_refs.MAX_BYTES+1]


def test_pax_read_failure_is_a_blocking_validation_result(pilot,monkeypatch):
    client,store,source,_=pilot
    (source/'synthetic.pax').write_bytes(b'<Palette/>');release=imported(client)
    original_manifest=store.manifest;original_open=Path.open;armed=[]
    def manifest(root):
        value=original_manifest(root);armed.append(True);return value
    def fail(path,*args,**kwargs):
        if armed and path.suffix=='.pax':raise PermissionError('Synthetic unreadable PAX fixture')
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(store,'manifest',manifest);monkeypatch.setattr(Path,'open',fail)
    result=client.post('/api/v1/releases/'+release['id']+'/validate',headers=HEADERS)
    assert result.status_code==200,result.text
    assert not result.json()['integrity_passed'] and any('PAX file is unreadable' in error for error in result.json()['errors'])
    assert store.get_release(release['id'])['status']=='blocked' and store.active()['release_id'] is None
