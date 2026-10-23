"""Synthetic native-column/UI contracts: special identifiers remain ordinary data."""
import json
import os

import pytest

from nxserver.libraries import NativeTable
from nxserver.store import TOOL_DATABASE
from test_management import tool_payload
from test_requests_browser import ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge keys.')


def test_new_special_columns_roundtrip_through_gui_defaults_and_new_release_export(ui):
    from playwright.sync_api import expect
    page,store,old,resource=ui
    source=store.sources['test']/TOOL_DATABASE
    source.write_text('#CLASS end_mill\nFORMAT LIBRF DESCR DIA __proto__ constructor toString\n'
        'DATA |STD_1|Stock|10|stock-a|stock-b|stock-c\n#END_DATA\n',encoding='utf-8')
    target=store.import_release('test','2512','Extended schema','fixture')
    block_id=NativeTable.read(store.release_folder(target['id'])/TOOL_DATABASE).blocks[0]['id']
    original=store.manifest(store.release_folder(target['id']))
    tool=store.save_resource(tool_payload('PROTO_TOOL'),'fixture')
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    page.locator('#releases tr').filter(has_text='Extended schema').get_by_role('button',name='Neuen Export ableiten',exact=True).click()
    page.locator('#export-preview').click();expect(page.locator('#export-column-fields fieldset')).to_have_count(3)
    for name,value in [('__proto__','literal-a'),('constructor','literal-b'),('toString','literal-c')]:
        box=page.locator('#export-column-fields fieldset').filter(has=page.locator('legend',has_text=name))
        box.locator('input:not([type=checkbox])').fill(value);box.locator('input[type=checkbox]').check()
    encoded=page.locator('#export-defaults').input_value();defaults=json.loads(encoded)
    assert defaults['tool-metric'][block_id]=={'__proto__':'literal-a','constructor':'literal-b','toString':'literal-c'}
    assert page.evaluate('Object.prototype["literal-a"]===undefined')
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled(timeout=15000)
    page.locator('#export-create').click();expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau erstellt',timeout=15000)
    exported=next(value for value in store.releases() if value['parent_id']==target['id'])
    rows=NativeTable.read(store.release_folder(exported['id'])/TOOL_DATABASE).groups()['PROTO_TOOL']
    assert rows[0]['values']['__proto__']=='literal-a' and rows[0]['values']['constructor']=='literal-b' and rows[0]['values']['toString']=='literal-c'
    assert store.manifest(store.release_folder(target['id']))==original
    assert store.active()['release_id'] is None
    assert next(value for value in store.resources() if value['id']==tool['id'])==tool


def test_conflict_identifier_and_nested_constructor_are_plain_json_and_bad_branch_is_not_replaced(ui):
    from playwright.sync_api import expect
    page,store,release,resource=ui
    page.on('dialog',lambda dialog:dialog.accept());page.locator('#tab-update').click()
    # Prepared-folder conflict controls are intentionally inert in full-upgrade
    # mode. Select their real workflow before checking literal JSON key handling.
    page.locator('#upgrade-mode').select_option('prepared')
    # Contract supplied by a synthetic planner, no real source/target data writes.
    page.evaluate("""()=>NXNative.renderConflicts({references:{errors:[]},conflicts:[{
        library:'tool-metric',source:'fixture.dat',key:'__proto__',type:'data',reason:'Synthetic conflict'}]})""")
    choice=page.locator('#conflicts select');choice.select_option('customer')
    value=json.loads(page.locator('#import-resolutions').input_value())
    assert value=={'tool-metric':{'fixture.dat':{'__proto__':'customer'}}}
    choice.select_option('');assert json.loads(page.locator('#import-resolutions').input_value())['tool-metric']['fixture.dat']=={}
    page.locator('#import-resolutions').fill('{"tool-metric":"keep this invalid user value"}')
    choice.select_option('customer');expect(page.locator('#message')).to_have_class('error')
    assert json.loads(page.locator('#import-resolutions').input_value())=={'tool-metric':'keep this invalid user value'}
    expect(page.locator('#import')).to_be_disabled()
    assert store.active()['release_id'] is None and len(store.releases())==1
