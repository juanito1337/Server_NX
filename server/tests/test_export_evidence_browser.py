"""Real synthetic exports shown against the same immutable workspace snapshot."""
import os
import copy
import uuid

import pytest

from nxserver.libraries import SPECS, NativeTable, native_cam
from test_libraries import resource_body, simple, write
from test_requests_browser import ui
from nxserver.store import canonical

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated export evidence.')


def native_export(ui):
    page, store, original, gauge = ui
    write(store.sources['test'], SPECS['holder-metric']['path'], simple())
    base = store.import_release('test', '2512', 'Holder source', 'fixture')
    table = NativeTable(simple('CUSTOM_HOLDER', 'Synthetic holder'))
    holder = store.save_resource({'kind':'holder', 'name':'Export evidence holder', 'cam_status':'approved',
        'cam':native_cam('holder-metric','2512',table,table.records), 'asset_ids':[], 'erp':None}, 'fixture')
    first = store.import_release('', '', 'Export revision 1', 'fixture', parent=base['id'])
    return base, holder, first


def test_current_and_older_export_evidence_is_local_and_keeps_unsaved_editor(ui):
    from playwright.sync_api import expect
    page, store, original, gauge = ui
    base, holder, first = native_export(ui)
    old_manifest = store.manifest(store.release_folder(first['id']))
    page.locator('#refresh').click()
    expect(page.locator('#summary')).to_contain_text('2 Stammdatensätze')
    page.locator('#resource-release').select_option(first['id'])
    row = page.locator('#resources tr').filter(has_text='Export evidence holder')
    expect(row).to_contain_text('Aktuelle Katalogrevision registriert')
    expect(row).to_contain_text('DAT: Revision 1')
    row.get_by_role('button', name='Bearbeiten', exact=True).click()
    page.locator('#name').fill('Unsaved editor remains untouched')
    changed = store.save_resource({**resource_body(holder), 'name':'Export evidence holder draft', 'cam_status':'draft'},
        'fixture', holder['id'], holder['revision'])
    page.locator('#refresh').click()
    page.locator('#tab-manage').click()
    expect(page.locator('#resource-release')).to_have_value(first['id'])
    expect(row).to_contain_text('Ältere Katalogrevision registriert')
    expect(row).to_contain_text('DAT: Revision 1')
    before = store.audit_entries()
    page.locator('#resource-export-status').select_option('older')
    expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resource-export-status').select_option('no_record')
    expect(page.locator('#resources')).to_contain_text(gauge['name'])
    page.locator('#resource-export-status').select_option('')
    page.locator('#resource-release').select_option(base['id'])
    expect(row).to_contain_text('Kein eigener Exportbeleg')
    page.locator('#tab-prepare').click()
    expect(page.locator('#name')).to_have_value('Unsaved editor remains untouched')
    assert store.audit_entries() == before and store.active()['release_id'] is None
    approved = store.save_resource({**resource_body(changed), 'cam_status':'approved'}, 'fixture', holder['id'], changed['revision'])
    latest = store.import_release('', '', 'Export revision 3', 'fixture', parent=first['id'])
    page.locator('#refresh').click()
    page.locator('#tab-manage').click()
    page.locator('#resource-release').select_option(latest['id'])
    expect(row).to_contain_text('DAT: Revision '+str(approved['revision']))
    expect(row).to_contain_text('Aktuelle Katalogrevision registriert')
    expect(page.locator('#resource-export-context')).to_contain_text(latest['folder'])
    assert store.manifest(store.release_folder(first['id'])) == old_manifest
    page.locator('#disconnect').click()
    expect(page.locator('#resource-release')).to_have_value('')
    expect(page.locator('#resource-export-status')).to_be_disabled()
    expect(page.locator('#resource-export-context')).to_have_text('')


@pytest.mark.parametrize('fault', ['future_revision', 'bad_hash'])
def test_invalid_receipt_is_scoped_to_comparison_not_whole_workspace(ui, fault):
    from playwright.sync_api import expect
    page, store, original, gauge = ui
    base, holder, first = native_export(ui)
    catalog = copy.deepcopy(first['catalog'])
    receipt = catalog['exported_native'][holder['id']]
    receipt['revision' if fault == 'future_revision' else 'records_hash'] = 99 if fault == 'future_revision' else 'bad'
    with store.db() as db:
        db.execute('UPDATE releases SET catalog=? WHERE id=?', (canonical(catalog), first['id']))
    page.locator('#refresh').click()
    expect(page.locator('#summary')).to_contain_text('2 Stammdatensätze')
    page.locator('#resource-release').select_option(first['id'])
    page.locator('#resource-export-status').select_option('review')
    expect(page.locator('#resources tr')).to_have_count(1)
    expect(page.locator('#resources')).to_contain_text('unvollständig oder widersprüchlich')
    expect(page.locator('#resources')).not_to_contain_text('Aktuelle Katalogrevision registriert')
    page.locator('#resource-export-status').select_option('no_record')
    expect(page.locator('#resources')).to_contain_text(gauge['name'])
    assert store.active()['release_id'] is None


def test_receipt_filter_searches_all_metadata_before_paginating(ui):
    from playwright.sync_api import expect
    page, store, original, gauge = ui
    base, holder, first = native_export(ui)
    catalog = copy.deepcopy(first['catalog'])
    # Deliberate metadata-only fixture: this is NOT proof of 205 physical exports.
    with store.db() as db:
        for index in range(205):
            identifier = str(uuid.uuid4())
            body = copy.deepcopy(resource_body(holder))
            body['name'] = f'PROOF_PAGE_{index:04}'
            body['cam']['records'][0]['values']['LIBRF'] = f'PAGE_{index:04}'
            payload = canonical(body)
            stamp = f'2026-01-01T00:00:00.{index:06}+00:00'
            db.execute('INSERT INTO resources(id,revision,payload,external_key,updated_at) VALUES(?,1,?,NULL,?)', (identifier,payload,stamp))
            db.execute('INSERT INTO resource_versions(id,revision,payload,actor,created_at) VALUES(?,1,?,?,?)', (identifier,payload,'metadata fixture',stamp))
            catalog['exported_native'][identifier] = {'revision':1,'library_id':'holder-metric','key':f'PAGE_{index:04}','records_hash':'a'*64}
        db.execute('UPDATE releases SET catalog=? WHERE id=?', (canonical(catalog),first['id']))
        store.audit(db, 'fixture', 'fixture.export_evidence.seed', {'count':205})
    page.locator('#refresh').click()
    expect(page.locator('#summary')).to_contain_text('207 Stammdatensätze')
    before = store.audit_entries()
    page.locator('#resource-release').select_option(first['id'])
    page.locator('#resource-export-status').select_option('current')
    expect(page.locator('#resource-count')).to_contain_text('206 von 207')
    expect(page.locator('#resources tr')).to_have_count(100)
    page.locator('#resource-next').click()
    expect(page.locator('#resource-count')).to_contain_text('Anzeige 101–200')
    page.locator('#resource-next').click()
    expect(page.locator('#resources tr')).to_have_count(6)
    page.locator('#resource-query').fill('PROOF_PAGE_0204')
    expect(page.locator('#resources tr')).to_have_count(1)
    expect(page.locator('#resource-prev')).to_be_disabled()
    expect(page.locator('#resource-next')).to_be_disabled()
    assert store.audit_entries() == before and store.active()['release_id'] is None
