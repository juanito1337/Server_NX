"""New values must survive DAT serialization exactly; no genuine NX data."""
import pytest

from nxserver.schemas import plan
from nxserver.safety import DomainError
from test_schemas import setup


@pytest.mark.parametrize('text',[' 17','17 ','\t17','17\t','\u00a017','17\u00a0',' '])
def test_new_schema_values_never_silently_drop_edge_whitespace(tmp_path,text):
    store,_,resource,request=setup(tmp_path)
    request['decisions']={'0':{'values':{'EXTRA':text}}};before=store.audit_entries()
    with pytest.raises(DomainError):plan(store,request)
    assert store.audit_entries()==before and store.resources()[0]==resource


@pytest.mark.parametrize('text',['','17.5','two words','a\tb'])
def test_exact_empty_and_internal_whitespace_values_remain_supported(tmp_path,text):
    store,_,resource,request=setup(tmp_path)
    request['decisions']={'0':{'values':{'EXTRA':text}}}
    result=plan(store,request)
    assert result['can_apply'] and result['cam']['records'][0]['values']['EXTRA']==text
    assert store.resources()[0]==resource
