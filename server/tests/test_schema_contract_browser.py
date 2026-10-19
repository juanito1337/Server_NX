"""Independent schema-row/value consistency, with disposable Edge and fake DAT."""
import json
import os

import pytest

from test_requests_browser import ui, track
from test_schema_editor_browser import schema, complete, value, check

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated schema preview contracts.')


@pytest.mark.parametrize('fault',['source_values','source_block','missing_row','duplicate_row','new_confirmation','new_value',
    'target_values','cam','source_sha','target_sha','key','version','conflicts','counts','decisions','field_status','identifier','target_schema'])
def test_changed_schema_preview_cannot_qualify_or_replace_editor(schema,fault):
    from playwright.sync_api import expect
    page,store,_,resource,count,column=schema;complete(page,count,column)
    original=page.locator('#cam').input_value();before=store.audit_entries()
    def invalid(route):
        response=route.fetch();report=response.json();row=report['rows'][0]
        if fault=='source_values':row['source_values']['DESCR']='Changed source'
        elif fault=='source_block':row['source_block']['class_name']='DIFFERENT'
        elif fault=='missing_row':report['rows'].pop();report['cam']['records'].pop()
        elif fault=='duplicate_row':report['rows'][1]=report['rows'][0]
        elif fault=='new_confirmation':row['new_fields'][0]['confirmed']=False
        elif fault=='new_value':row['new_fields'][0]['value']='Not confirmed'
        elif fault=='target_values':row['target_values']['DESCR']='Changed result'
        elif fault=='cam':report['cam']['records'][0]['values']['DESCR']='Changed final CAM'
        elif fault=='source_sha':report.pop('source_sha256')
        elif fault=='target_sha':report.pop('target_library_sha256')
        elif fault=='key':report['key']='OTHER'
        elif fault=='version':report['target_nx_version']='2406'
        elif fault=='conflicts':report['conflicts']=[{'index':0,'reason':'Unresolved'}]
        elif fault=='counts':report['changes']['added']+=1
        elif fault=='decisions':report['decisions']['0']['values'][column]='Unreviewed'
        elif fault=='field_status':row['fields'][0]['status']='discarded'
        elif fault=='identifier':row['fields'][0]['identifier']=False
        else:report['target_schemas'][0]['fields'].append('UNREVIEWED')
        route.fulfill(response=response,json=report)
    page.route('**/schema/preview',invalid);track(page,'schema-preview');page.locator('#schema-preview').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#schema-apply')).to_be_disabled()
    expect(page.locator('#schema-state')).to_contain_text('Keine vollständige aktuelle Schema-Vorschau')
    expect(page.locator('#cam')).to_have_value(original)
    assert store.audit_entries()==before and next(item for item in store.resources() if item['id']==resource['id'])==resource


def test_jointly_forged_success_cannot_hide_missing_explicit_new_values(schema):
    from playwright.sync_api import expect
    page,store,_,resource,*_=schema;before=store.audit_entries();original=page.locator('#cam').input_value()
    def invalid(route):
        response=route.fetch();report=response.json();report['conflicts']=[];report['can_apply']=True
        for row in report['rows']:row['conflicts']=[]
        report['cam']={'library_id':report['library_id'],'nx_version':report['target_nx_version'],
            'schemas':report['target_schemas'],'records':[{'block_id':row['target_block']['id'],
            'class_name':row['target_block']['class_name'],'values':row['target_values']} for row in report['rows']]}
        route.fulfill(response=response,json=report)
    page.route('**/schema/preview',invalid);track(page,'schema-preview');page.locator('#schema-preview').click()
    page.wait_for_function('()=>window.__oldActionFinished');expect(page.locator('#schema-apply')).to_be_disabled()
    expect(page.locator('#cam')).to_have_value(original)
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1


def test_explicit_edge_whitespace_is_rejected_and_correction_preserves_intent(schema):
    from playwright.sync_api import expect
    page,store,_,resource,*_=schema;original=page.locator('#cam').input_value();before=store.audit_entries()
    value(page,0).fill(' 17.5 ');check(page,0).check();page.locator('#schema-preview').click()
    expect(page.locator('#message')).to_contain_text('whitespace')
    expect(page.locator('#schema-apply')).to_be_disabled();expect(value(page,0)).to_have_value(' 17.5 ')
    value(page,0).fill('17.5');page.locator('#schema-preview').click()
    expect(page.locator('#schema-state')).to_contain_text('offene Entscheidung')
    expect(value(page,0)).to_have_value('17.5');expect(check(page,0)).to_be_checked()
    expect(page.locator('#cam')).to_have_value(original)
    assert store.audit_entries()==before and store.history(resource['id'])[0]['revision']==1


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('mode',['class_change','ambiguous'])
def test_explicit_mapping_discard_and_empty_value_still_prepare_native_and_legacy(ui,legacy,mode):
    from playwright.sync_api import expect
    from nxserver.libraries import NativeTable,SPECS,native_cam
    from test_libraries import write
    page,store,*_=ui
    if mode=='class_change':
        target='#CLASS NEWCLASS\nFORMAT NEW LIBRF EXTRA\nDATA |Standard|STD|0\n#END_DATA\n'
        block='NEWCLASS:1';column='EXTRA'
    else:
        target='#CLASS SAMPLE\nFORMAT LIBRF NEW EXTRA\nDATA |STD_A|Standard|0\nFORMAT LIBRF NEW MORE\nDATA |STD_B|Standard|0\n#END_DATA\n'
        block='SAMPLE:2';column='MORE'
    write(store.sources['test'],SPECS['tool-metric']['path'],target)
    release=store.import_release('test','2512','Explicit schema target','fixture')
    values={'LIBRF':'CUSTOM_EXPLICIT','OLD':'Preserved value','EMPTY':''}
    source=NativeTable('#CLASS SAMPLE\nFORMAT LIBRF OLD EMPTY\nDATA |CUSTOM_EXPLICIT|Preserved value|\n#END_DATA\n')
    cam={'class_name':'SAMPLE','values':values} if legacy else native_cam('tool-metric','2406',source,source.records)
    resource=store.save_resource({'kind':'tool','name':'Explicit schema decisions','cam_status':'draft',
        'asset_ids':[],'erp':None,'cam':cam},'fixture')
    audit=store.audit_entries();manifest=store.manifest(store.release_folder(release['id']))
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#schema-adaptation summary').click();page.locator('#schema-target-release').select_option(release['id'])
    page.locator('#schema-preview').click();expect(page.locator('#schema-state')).to_contain_text('offene Entscheidung')
    box=page.locator('[data-schema-row="0"]');box.locator('select').first.select_option(block)
    page.locator('#schema-preview').click();expect(box.locator('tbody tr')).to_have_count(3)
    box.locator('tbody tr').filter(has=page.locator('td:first-child',has_text='OLD')).locator('select').select_option('NEW')
    box.locator('tbody tr').filter(has=page.locator('td:first-child',has_text='EMPTY')).locator('select').select_option('!discard')
    if mode=='class_change':box.get_by_label('Klassenwechsel auf NEWCLASS ausdrücklich bestätigen',exact=True).check()
    page.locator('#schema-preview').click();expect(box.locator('[data-schema-field="'+column+'"]').first).to_be_visible()
    box.locator('[data-schema-confirm="'+column+'"]').check()
    page.locator('#schema-preview').click();expect(page.locator('#schema-apply')).to_be_enabled()
    page.on('dialog',lambda dialog:dialog.accept());page.locator('#schema-apply').click()
    expect(page.locator('#message')).to_contain_text('Schema in Editor übernommen')
    result=json.loads(page.locator('#cam').input_value())
    expected={'LIBRF':'CUSTOM_EXPLICIT','NEW':'Preserved value',column:''}
    if legacy:assert result=={'class_name':'NEWCLASS' if mode=='class_change' else 'SAMPLE','values':expected}
    else:assert result['nx_version']=='2512' and result['records'][0]['values']==expected
    assert store.audit_entries()==audit and store.history(resource['id'])[0]['revision']==1
    assert store.manifest(store.release_folder(release['id']))==manifest and store.active()['release_id'] is None
