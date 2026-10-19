"""Complete-group row edits retain historical exports for all native adapters."""
import copy
import json
from pathlib import Path

import pytest

from nxserver.libraries import NativeTable,SPECS,native_cam
from nxserver.store import Store
from test_libraries import write
from test_management import tool_payload


def group(key):
    return ('#CLASS A\nFORMAT LIBRF NAME SEQ\nDATA |'+key+'|First row|1\nDATA |'+key+'|Second row|9\n'
            '#CLASS B\nFORMAT NAME DESCR RTYPE\nDATA |'+key+'|Optional body|BODY\n#END_DATA\n')


@pytest.mark.parametrize('identifier',list(SPECS))
@pytest.mark.parametrize('remaining',[(0,2),(0,)])
def test_smaller_customer_group_replaces_only_owned_rows_in_a_new_explicit_export(tmp_path,identifier,remaining):
    spec=SPECS[identifier];source=tmp_path/'source';write(source,spec['path'],group('STD'))
    store=Store(tmp_path/'managed',{'test':source});base=store.import_release('test','2512','Original','fixture')
    table=NativeTable(group('CUSTOM'));original=native_cam(identifier,'2512',table,table.records)
    body={'kind':spec['kind'],'name':'Customer group','cam':original,'cam_status':'approved','asset_ids':[],
          'erp':None,'geometry':tool_payload()['geometry']}
    resource=store.save_resource(body,'fixture');first=store.import_release('','','First owned export','fixture',parent=base['id'])
    historical_manifest=store.manifest(Path(first['folder']));source_manifest=store.manifest(source)
    changed=copy.deepcopy(original);changed['records']=[changed['records'][index] for index in remaining]
    draft=store.save_resource({**body,'cam':changed,'cam_status':'draft'},'fixture',resource['id'],resource['revision'])
    assert draft['revision']==2 and draft['cam']==changed
    # Draft changes must not silently remove rows from an already exported group.
    held=store.import_release('','','Draft is not deployed','fixture',parent=first['id'])
    old=NativeTable.read(Path(first['folder'])/spec['path']).groups()
    assert NativeTable.read(Path(held['folder'])/spec['path']).groups()['CUSTOM']==old['CUSTOM']
    approved=store.save_resource({**body,'cam':changed},'fixture',resource['id'],draft['revision'])
    last=store.import_release('','','Explicit smaller group export','fixture',parent=held['id'])
    actual=NativeTable.read(Path(last['folder'])/spec['path']).groups()
    # DAT output IDs depend on the full file; field/class contents do not.
    values=lambda rows:[(row['class_name'],row['values']) for row in rows]
    assert values(actual['CUSTOM'])==values(changed['records']) and actual['STD']==old['STD']
    assert last['catalog']['exported_native'][resource['id']]['revision']==approved['revision']
    assert store.manifest(Path(first['folder']))==historical_manifest and store.manifest(source)==source_manifest
    assert json.loads(store.history(resource['id'])[0]['payload'])['cam']==original
    assert store.active()['release_id'] is None
