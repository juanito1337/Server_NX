"""Opt-in real Edge GUI workflow. Never uses live settings or user profiles.

NX_BROWSER_TEST=1 python -m pytest tests/test_browser.py -q
Uses the installed Edge, not playwright install msedge (which could replace it).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nxserver.libraries import OPTIONAL_PALETTE, SPECS
from test_geometry import report_for
from test_upgrades import installation


@pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated browser test.')
def test_gui_normalizes_imports_catalog_and_edits_native_columns(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect
    source, old, new = tmp_path / 'inputs' / 'nested-share', tmp_path / 'old', tmp_path / 'new'
    output = tmp_path / 'output'
    output.mkdir()
    for root in (source, old, new):
        for identifier in ('tool-metric', 'holder-metric', 'fixture-metric'):
            path = root / SPECS[identifier]['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            fields = 'LIBRF DESCR NX_ADDITIONAL_COLUMN' if identifier == 'fixture-metric' else ('NAME DESCR' if identifier == 'holder-metric' else 'LIBRF DESCR')
            data = '|STD|Siemens|Version-specific value' if identifier == 'fixture-metric' else '|STD|Siemens'
            path.write_text(f'#CLASS SAMPLE\nFORMAT {fields}\nDATA {data}\n#END_DATA\n', encoding='utf-8')
    legacy = source / SPECS['tool-metric']['legacy_path']
    legacy.parent.mkdir(parents=True)
    legacy.write_text('#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |HD_TOOL|Henning tool\n#END_DATA\n', encoding='utf-8')
    palette = source / OPTIONAL_PALETTE
    palette.write_text('<Palette></Palette><PaletteEntry/>', encoding='utf-8')
    originals = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    sidt_old,sidt_new=tmp_path/'sidt-old',tmp_path/'sidt-new'
    installation(old,sidt_old,'2406');installation(new,sidt_new,'2512')
    upgrade_source=source.parent/'nx2406-working-copy'
    path=upgrade_source/SPECS['holder-metric']['path'];path.parent.mkdir(parents=True)
    path.write_text('#CLASS SAMPLE\nFORMAT NAME DESCR\nDATA |STD|Siemens\nDATA |UPG_HOLDER|Upgrade holder\n#END_DATA\n',encoding='utf-8')
    upgrade_original=path.read_bytes()
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'data_dir': str(tmp_path / 'managed'), 'sources': {'test': str(source)},
                                    'standards': {'2406': str(old), '2512': str(new)}, 'logs': {},
                                    'sidts': {'2406':str(sidt_old),'2512':str(sidt_new)},
                                    'source_roots': {'inputs': str(source.parent)},
                                    'target_roots': {'output': str(output)}}), encoding='utf-8')
    token = 'isolated-browser-token-not-real-' + 'z' * 32
    environment = {**os.environ, 'NX_ADMIN_TOKEN': token}
    environment.pop('NX_ERP_TOKEN', None)
    environment.pop('NX_ERP_TOKEN_FILE', None)
    environment.pop('NX_ADMIN_TOKEN_FILE', None)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'nxserver', '--settings', str(settings), '--port', str(port)],
        cwd=Path(__file__).resolve().parents[1], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/health', timeout=1).close()
                break
            except (urllib.error.URLError, TimeoutError):
                if process.poll() is not None:
                    raise AssertionError('Isolated GUI server terminated.')
                time.sleep(0.1)
        else:
            raise AssertionError('Isolated GUI server not ready.')
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width': 1400, 'height': 950})
                failures = []
                page.on('pageerror', lambda error: failures.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept('GUI Export' if dialog.type == 'prompt' else None))
                page.goto(base)
                page.locator('#token').fill(token)
                page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                assert page.locator('#kind option').count() == 23
                expect(page.get_by_role('tab')).to_have_count(3)
                expect(page.locator('#tab-manage')).to_have_attribute('aria-selected', 'true')
                expect(page.locator('#panel-manage')).to_be_visible()
                expect(page.locator('#panel-prepare')).to_be_hidden()
                expect(page.locator('#panel-update')).to_be_hidden()
                expect(page.locator('#review-box')).to_be_hidden()
                expect(page.locator('#resource-empty')).to_contain_text('Noch keine verwalteten Stammdaten')
                assert page.locator('#resource-kind option').count() == 24
                # Tab switches are navigation only, never an import or save.
                mutations = []
                page.on('request', lambda request: mutations.append(request.url) if request.method in ('POST', 'PUT', 'DELETE') else None)
                page.locator('#tab-manage').focus()
                page.keyboard.press('ArrowRight')
                expect(page.locator('#tab-prepare')).to_be_focused()
                expect(page.locator('#tab-prepare')).to_have_attribute('aria-selected', 'true')
                page.locator('#name').fill('Unsaved draft survives tab switches')
                page.locator('#geometry-evidence').fill('Pending local NX checks')
                page.locator('#tab-update').click()
                expect(page.locator('#panel-manage')).to_be_hidden()
                expect(page.locator('#panel-update')).to_be_visible()
                expect(page.locator('#extract-start')).to_be_disabled()
                expect(page.locator('#extract-state')).to_contain_text('Keine EXE-Pakete konfiguriert')
                page.locator('#tab-update').focus()
                page.keyboard.press('Home')
                expect(page.locator('#tab-manage')).to_be_focused()
                page.locator('#tab-prepare').click()
                expect(page.locator('#name')).to_have_value('Unsaved draft survives tab switches')
                expect(page.locator('#geometry-evidence')).to_have_value('Pending local NX checks')
                assert mutations == []
                page.locator('#tab-update').click()
                page.locator('#upgrade-mode').select_option('prepared')
                page.locator('#source').select_option('inputs')
                page.locator('#source-browse').click()
                expect(page.locator('#folder-dialog')).to_be_visible()
                page.locator('#folder-entries').get_by_role('button', name='nested-share', exact=True).click()
                expect(page.locator('#folder-path')).to_have_text(str(source))
                page.locator('#folder-select').click()
                expect(page.locator('#source-path')).to_have_value(str(source))
                page.locator('#target').select_option('output')
                page.locator('#target-browse').click()
                expect(page.locator('#folder-path')).to_have_text(str(output))
                page.locator('#folder-select').click()
                page.locator('#target-path').fill(str(output / 'nx2512-gui'))
                page.locator('#tab-manage').click()
                page.locator('#tab-update').click()
                expect(page.locator('#source-path')).to_have_value(str(source))
                expect(page.locator('#target-path')).to_have_value(str(output / 'nx2512-gui'))
                page.locator('#import-preview').click()
                expect(page.locator('#import-state')).to_contain_text('konfliktfrei')
                page.locator('#target-path').fill(str(output / 'nx2512-selected'))
                expect(page.locator('#import')).to_be_disabled()
                page.locator('#import-preview').click()
                expect(page.locator('#import-state')).to_contain_text(str(output / 'nx2512-selected'))
                page.locator('#import').click()
                expect(page.locator('#message')).to_contain_text('Neuer Ordner erstellt')
                expect(page.locator('#panel-manage')).to_be_visible()
                expect(page.locator('#panel-update')).to_be_hidden()
                expect(page.locator('#releases').get_by_role('button', name='Test-Starter herunterladen')).to_have_count(1)
                with page.expect_download() as launcher_download:
                    page.locator('#releases').get_by_role('button', name='Test-Starter herunterladen').click()
                assert launcher_download.value.suggested_filename.startswith('NX2512_Test_')
                assert launcher_download.value.failure() is None
                page.locator('#library-inventory').click()
                expect(page.locator('#native-library option')).to_have_count(3)
                page.locator('#native-library').select_option('holder-metric')
                page.locator('#library-read').click()
                expect(page.locator('#native-objects tr')).to_have_count(1)
                page.locator('#tab-prepare').click()
                page.locator('#create-inventory').click()
                expect(page.locator('#create-library option')).to_have_count(3)
                page.locator('#create-library').select_option('fixture-metric')
                page.locator('#create-schema').click()
                page.locator('#native-new').click()
                expect(page.locator('#native-fields input')).to_have_count(3)
                expect(page.locator('#native-fields')).to_contain_text('NX_ADDITIONAL_COLUMN')
                page.locator('#create-library').select_option('holder-metric')
                page.locator('#create-schema').click()
                expect(page.locator('#native-block option')).to_have_count(1)
                page.locator('#native-new').click()
                expect(page.locator('#native-fields input')).to_have_count(2)
                expect(page.locator('#native-fields')).to_contain_text('NAME')
                page.locator('#name').fill('GUI holder')
                page.locator('#native-fields input').nth(0).fill('GUI_HOLDER')
                page.locator('#native-fields input').nth(1).fill('GUI holder description')
                page.locator('#camstatus').select_option('approved')
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                # An old-schema fixture must get an explicit value for the target's new column.
                fixture_cam = {'library_id': 'fixture-metric', 'nx_version': '2406',
                    'schemas': [{'id': 'SAMPLE:1', 'class_name': 'SAMPLE', 'fields': ['LIBRF', 'DESCR']}],
                    'records': [{'block_id': 'SAMPLE:1', 'class_name': 'SAMPLE',
                                 'values': {'LIBRF': 'GUI_FIXTURE', 'DESCR': 'Old-schema fixture'}}]}
                page.locator('#kind').select_option('fixture')
                page.locator('#name').fill('GUI fixture')
                page.locator('#editor-section details.technical summary').click()
                page.locator('#cam').fill(json.dumps(fixture_cam))
                page.locator('#camstatus').select_option('approved')
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                page.locator('#back-to-manage').click()
                expect(page.locator('#panel-manage')).to_be_visible()
                page.locator('#catalog-preview').click()
                expect(page.locator('#catalog-state')).to_contain_text('1 neue Objekte, 0 Konflikte')
                page.locator('#catalog-import').click()
                expect(page.locator('#message')).to_contain_text('Stammdaten übernommen')
                expect(page.locator('#resources tr')).to_have_count(3)
                page.locator('#resource-kind').select_option('holder')
                expect(page.locator('#resources tr')).to_have_count(1)
                expect(page.locator('#resources')).to_contain_text('GUI holder')
                page.locator('#resource-query').fill('no matching resource')
                expect(page.locator('#resource-empty')).to_contain_text('Keine Stammdaten passen')
                expect(page.locator('#resources tr')).to_have_count(0)
                page.locator('#resource-query').fill('GUI')
                expect(page.locator('#resources tr')).to_have_count(1)
                page.locator('#resource-query').fill('')
                page.locator('#resource-kind').select_option('')
                page.locator('#resource-status').select_option('draft')
                expect(page.locator('#resources tr')).to_have_count(1)
                expect(page.locator('#resources')).to_contain_text('Henning tool')
                page.locator('#resource-status').select_option('')
                page.locator('#releases').get_by_role('button', name='Neuen Export ableiten').click()
                expect(page.locator('#export-box')).to_be_visible()
                expect(page.locator('#export-create')).to_be_disabled()
                page.locator('#export-name').fill('GUI Export')
                page.locator('#export-preview').click()
                expect(page.locator('#export-state')).to_contain_text('Konflikt')
                expect(page.locator('#export-column-fields')).to_contain_text('NX_ADDITIONAL_COLUMN')
                expect(page.locator('#export-create')).to_be_disabled()
                page.locator('#export-box').screenshot(path=str(tmp_path / 'export-new-column.png'))
                page.locator('#export-column-fields input:not([type="checkbox"])').fill('Confirmed GUI column')
                page.locator('#export-column-fields input[type="checkbox"]').check()
                expect(page.locator('#export-create')).to_be_disabled()
                page.locator('#export-preview').click()
                expect(page.locator('#export-state')).to_contain_text('Vorschau exportierbar')
                expect(page.locator('#export-actions')).to_contain_text('GUI holder')
                expect(page.locator('#export-actions')).to_contain_text('Entwurf zurückgestellt')
                expect(page.locator('#releases tr')).to_have_count(1)
                page.locator('#export-box').screenshot(path=str(tmp_path / 'export-ready.png'))
                page.locator('#export-create').click()
                expect(page.locator('#releases tr')).to_have_count(2)
                page.locator('#resources tr').filter(has_text='Henning tool').get_by_role('button', name='Bearbeiten').click()
                expect(page.locator('#panel-prepare')).to_be_visible()
                expect(page.locator('#editing')).to_contain_text('Henning tool')
                expect(page.locator('#native-fields input').nth(0)).not_to_be_editable()
                # Browsing another library cannot replace the independently loaded editor schema.
                page.locator('#tab-manage').click()
                page.locator('#native-library').select_option('tool-metric')
                page.locator('#library-read').click()
                page.locator('#tab-prepare').click()
                expect(page.locator('#create-library')).to_have_value('holder-metric')
                expect(page.locator('#native-add-row')).to_be_disabled()
                expect(page.locator('#name')).to_have_value('Henning tool')
                page.locator('#camstatus').select_option('approved')
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('preparation must be validated')
                page.locator('#camstatus').select_option('draft')
                step = b"ISO-10303-21;HEADER;FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));ENDSEC;DATA;#1=MANIFOLD_SOLID_BREP('fixture',$);ENDSEC;END-ISO-10303-21;"
                page.locator('#assetfile').set_input_files({'name': 'browser-fixture.step', 'mimeType': 'application/octet-stream', 'buffer': step})
                page.locator('#upload').click()
                expect(page.locator('#message')).to_contain_text('Datei gespeichert')
                expect(page.locator('#attachments tr')).to_have_count(1)
                expect(page.locator('#attachments')).to_contain_text('browser-fixture.step')
                expect(page.locator('#geometry-status')).to_have_value('needs_preparation')
                with page.expect_download() as attached_download:
                    page.locator('#attachments').get_by_role('button',name='Herunterladen',exact=True).click()
                assert attached_download.value.suggested_filename=='browser-fixture.step'
                assert attached_download.value.failure() is None
                page.locator('#asset-library summary').click()
                page.locator('#asset-query').fill('browser-fixture')
                page.locator('#asset-library-load').click()
                expect(page.locator('#asset-library-items tr')).to_have_count(1)
                expect(page.locator('#asset-library-items')).to_contain_text('0 Stammdatensätze')
                expect(page.locator('#asset-library-items').get_by_role('button',name='Zugeordnet',exact=True)).to_be_disabled()
                page.locator('#attachments').get_by_role('button',name='Zuordnung entfernen',exact=True).click()
                expect(page.locator('#attachments tr')).to_have_count(0)
                page.locator('#asset-library-items').get_by_role('button',name='Zuordnen',exact=True).click()
                expect(page.locator('#attachments tr')).to_have_count(1)
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                expect(page.locator('#step-asset option')).to_have_count(1)
                # Saved unlink/reuse must preserve the same original file instead of re-uploading.
                page.locator('#back-to-manage').click()
                page.locator('#resources tr').filter(has_text='Henning tool').get_by_role('button',name='Bearbeiten').click()
                expect(page.locator('#attachments tr')).to_have_count(1)
                page.locator('#attachments').get_by_role('button',name='Zuordnung entfernen',exact=True).click()
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                expect(page.locator('#step-asset option')).to_have_count(0)
                page.locator('#back-to-manage').click()
                page.locator('#resources tr').filter(has_text='Henning tool').get_by_role('button',name='Bearbeiten').click()
                page.locator('#asset-library summary').click()
                page.locator('#asset-query').fill('browser-fixture')
                page.locator('#asset-library-load').click()
                expect(page.locator('#asset-library-items')).to_contain_text('0 Stammdatensätze')
                page.locator('#asset-library-items').get_by_role('button',name='Zuordnen',exact=True).click()
                expect(page.locator('#attachments tr')).to_have_count(1)
                page.locator('#attachments').screenshot(path=str(tmp_path/'attachments.png'))
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                expect(page.locator('#step-asset option')).to_have_count(1)
                expect(page.locator('#step-tool-type option')).to_have_count(10)
                page.locator('#step-preview').click()
                expect(page.locator('#message')).to_contain_text('STEP-Text geprüft')
                page.locator('#step-job-create').click()
                expect(page.locator('#geometry-jobs tr')).to_have_count(1)
                with page.expect_download() as downloaded:
                    page.locator('#geometry-jobs').get_by_role('button', name='Importpaket herunterladen').click()
                download = downloaded.value
                assert download.suggested_filename.startswith('NX_STEP_')
                assert download.failure() is None
                expect(page.locator('#geometry-jobs')).to_contain_text('awaiting_nx')
                # Synthetic report validation does not assert that any NX geometry was tested.
                job = page.evaluate("JSON.parse(document.getElementById('details').textContent)")
                report=report_for(job)
                invalid_report={**report,'files':[{**report['files'][0],'path':'W:/SiemensShare/nx2406/tool.prt'}]}
                page.locator('#geometry-jobs').get_by_role('button',name='Bericht zuordnen',exact=True).click()
                page.locator('#geometry-report-file').set_input_files({'name':'bad-report.json','mimeType':'application/json','buffer':json.dumps(invalid_report).encode()})
                page.locator('#geometry-report-upload').click()
                expect(page.locator('#message')).to_have_class('error')
                expect(page.locator('#geometry-jobs')).to_contain_text('awaiting_nx')
                page.locator('#geometry-report-file').set_input_files({'name':'report.json','mimeType':'application/json','buffer':json.dumps(report).encode()})
                page.locator('#geometry-report-upload').click()
                expect(page.locator('#geometry-report-state')).to_contain_text('Diagnose zugeordnet')
                expect(page.locator('#geometry-report-summary')).to_contain_text('Unverifizierter Bericht')
                expect(page.locator('#geometry-report-parts tr')).to_have_count(2)
                expect(page.locator('#geometry-jobs')).to_contain_text('needs_selection')
                assert page.evaluate("resources.find(resource=>resource.name==='Henning tool').cam_status")=='draft'
                page.locator('#geometry-report-parts').screenshot(path=str(tmp_path/'diagnostic-parts.png'))
                page.screenshot(path=str(tmp_path / 'stammdaten-aufbereitung.png'), full_page=True)
                # These are synthetic protocol-contract tests, NOT real NX/CAM acceptance.
                # The acceptance UI stays in the daily management workspace, not upgrades.
                page.locator('#tab-manage').click()
                export_row = page.locator('#releases tr').filter(has_text='GUI Export')
                export_row.get_by_role('button', name='Abnehmen', exact=True).click()
                expect(page.locator('#review-box')).to_be_visible()
                export_folder = page.evaluate("releases.find(release => release.name === 'GUI Export').folder")
                expect(page.locator('#review-path')).to_contain_text(export_folder)
                original_folder = str(output / 'nx2512-selected')
                wrong_log = ('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\nSynthetic test, not an NX session\n' + original_folder).encode()
                page.locator('#syslog-file').set_input_files({'name': 'wrong-candidate.syslog',
                    'mimeType': 'text/plain', 'buffer': wrong_log})
                page.locator('#syslog-upload').click()
                expect(page.locator('#message')).to_contain_text('unveränderlich gespeichert')
                page.locator('#scan').click()
                expect(page.locator('#syslog-state')).to_contain_text('richtiger NX-Datenstand: nein')
                for key in ('tools', 'machines', 'posts', 'simulation', 'fixtures', 'inspection'):
                    page.locator('#' + key).check()
                page.locator('#evidence').fill('Synthetic browser contract confirmations, not real CAM approval.')
                expect(page.locator('#approve')).to_be_disabled()
                expect(export_row).not_to_contain_text('approved')
                correct_log = ('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\nSynthetic Prüfung, not an NX session\n' + export_folder).encode('utf-16')
                page.locator('#syslog-file').set_input_files({'name': 'candidate.syslog',
                    'mimeType': 'text/plain', 'buffer': correct_log})
                page.locator('#syslog-upload').click()
                expect(page.locator('#message')).to_contain_text('unveränderlich gespeichert')
                page.locator('#scan').click()
                expect(page.locator('#syslog-state')).to_contain_text('richtiger NX-Datenstand: ja')
                expect(page.locator('#syslog-state')).to_contain_text('0 Ladefehler')
                expect(page.locator('#syslog-state')).to_contain_text('NX-Version: passend')
                expect(page.locator('#approve')).to_be_disabled()
                for key in ('tools', 'machines', 'posts', 'simulation', 'fixtures', 'inspection'):
                    page.locator('#' + key).check()
                page.locator('#approve').click()
                expect(page.locator('#message')).to_contain_text('Fachliche Abnahme dokumentiert')
                expect(export_row).to_contain_text('approved')
                export_row.get_by_role('button', name='Lokal aktivieren', exact=True).click()
                expect(page.locator('#active')).to_contain_text(export_folder)
                page.locator('#tab-update').click()
                page.locator('#import-preview').click()
                expect(page.locator('#message')).to_contain_text('already exists')
                expect(page.locator('#import')).to_be_disabled()
                assert not failures, failures
                page.screenshot(path=str(tmp_path / 'versionsupdate.png'), full_page=True)
                page.locator('#tab-manage').click()
                page.screenshot(path=str(tmp_path / 'native-management.png'), full_page=True)
                # The whole-folder wizard uses synthetic installations and raw old data.
                page.locator('#tab-update').click()
                page.locator('#upgrade-mode').select_option('upgrade')
                expect(page.locator('#prepared-import-controls')).to_be_hidden()
                page.locator('#source-path').fill(str(upgrade_source))
                page.locator('#target-path').fill(str(output/'nx2512-wizard'))
                page.locator('#release-name').fill('GUI Whole Upgrade')
                page.locator('#upgrade-check').click()
                expect(page.locator('#upgrade-prerequisites')).to_contain_text('SIDT-Kopierprofile vorhanden')
                expect(page.locator('#upgrade-prerequisites')).to_contain_text('Installationsmetadaten: NX2406')
                expect(page.locator('#upgrade-prerequisites')).to_contain_text('NX2512')
                page.locator('#upgrade-preview').click()
                expect(page.locator('#upgrade-state')).to_contain_text('Vorschau baubar',timeout=15000)
                expect(page.locator('#upgrade-build')).to_be_enabled()
                page.locator('#upgrade-controls').screenshot(path=str(tmp_path/'upgrade-wizard.png'))
                page.locator('#upgrade-build').click()
                expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt',timeout=15000)
                expect(page.locator('#releases tr')).to_have_count(3)
                expect(page.locator('#active')).to_contain_text(export_folder)
                # Schema review is editor-only; preserves unsaved titles, defers
                # approval and creates a revision only when explicitly saved.
                fixture_row=page.locator('#resources tr').filter(has_text='GUI fixture')
                fixture_row.get_by_role('button',name='Bearbeiten',exact=True).click()
                before_schema=page.evaluate("({id:editing.id,revision:editing.revision,cam:structuredClone(editing.cam)})")
                expect(page.locator('#camstatus')).to_have_value('approved')
                page.locator('#name').fill('GUI fixture with reviewed schema')
                page.locator('#geometry-status').select_option('validated')
                page.locator('#geometry-checks input[type="checkbox"]').evaluate_all('(items)=>items.forEach(item=>item.checked=true)')
                page.locator('#schema-adaptation summary').click()
                schema_target=page.evaluate("releases.find(release=>release.name==='GUI Export').id")
                page.locator('#schema-target-release').select_option(schema_target)
                page.locator('#schema-preview').click()
                expect(page.locator('#schema-state')).to_contain_text('offene Entscheidung')
                expect(page.locator('#schema-rows')).to_contain_text('NX_ADDITIONAL_COLUMN')
                page.locator('[data-schema-field="NX_ADDITIONAL_COLUMN"]').fill('Confirmed GUI column')
                page.locator('[data-schema-confirm="NX_ADDITIONAL_COLUMN"]').check()
                page.locator('#schema-preview').click()
                expect(page.locator('#schema-state')).to_contain_text('Schema-Vorschau vollständig')
                expect(page.locator('#schema-apply')).to_be_enabled()
                page.locator('#native-fields input').nth(1).fill('Changed during schema review')
                expect(page.locator('#schema-apply')).to_be_disabled()
                # Source changes require a fresh decision set, never stale row mapping.
                page.locator('#schema-preview').click()
                expect(page.locator('#schema-state')).to_contain_text('offene Entscheidung')
                page.locator('[data-schema-field="NX_ADDITIONAL_COLUMN"]').fill('Confirmed GUI column')
                page.locator('[data-schema-confirm="NX_ADDITIONAL_COLUMN"]').check()
                page.locator('#schema-preview').click()
                expect(page.locator('#schema-apply')).to_be_enabled()
                page.locator('#schema-apply').click()
                expect(page.locator('#message')).to_contain_text('Schema in Editor übernommen')
                expect(page.locator('#name')).to_have_value('GUI fixture with reviewed schema')
                expect(page.locator('#camstatus')).to_have_value('draft')
                expect(page.locator('#geometry-status')).to_have_value('needs_preparation')
                assert page.locator('#geometry-checks input:checked').count()==0
                expect(page.locator('#native-fields input')).to_have_count(3)
                assert page.evaluate('editing.revision')==before_schema['revision']
                unchanged=page.evaluate("id=>api('resources').then(items=>items.find(item=>item.id===id))",before_schema['id'])
                assert unchanged['cam']==before_schema['cam']
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                saved_schema=page.evaluate("id=>api('resources').then(items=>items.find(item=>item.id===id))",before_schema['id'])
                assert saved_schema['revision']==before_schema['revision']+1
                assert saved_schema['cam']['nx_version']=='2512'
                assert saved_schema['cam_status']=='draft'
                expect(page.locator('#active')).to_contain_text(export_folder)
                expect(page.locator('#releases tr')).to_have_count(3)
                # Renaming and dropping an empty obsolete column also work in
                # the actual controls, without silently mapping by position.
                page.locator('#tab-manage').click()
                page.locator('#resources tr').filter(has_text='GUI fixture with reviewed schema').get_by_role('button',name='Bearbeiten',exact=True).click()
                renamed_source=json.loads(page.locator('#cam').input_value())
                renamed_source['schemas'][0]['fields']=['LIBRF','OLD_DESCR','NX_ADDITIONAL_COLUMN','LEGACY_EMPTY']
                values=renamed_source['records'][0]['values'];values['OLD_DESCR']=values.pop('DESCR');values['LEGACY_EMPTY']=''
                page.locator('#editor-section details.technical summary').click()
                page.locator('#cam').fill(json.dumps(renamed_source))
                if not page.locator('#schema-adaptation').evaluate('(element)=>element.open'):
                    page.locator('#schema-adaptation summary').click()
                page.locator('#schema-preview').click()
                expect(page.locator('#schema-state')).to_contain_text('offene Entscheidung')
                page.locator('#schema-rows tbody tr').filter(has_text='OLD_DESCR').locator('select').select_option('DESCR')
                page.locator('#schema-rows tbody tr').filter(has_text='LEGACY_EMPTY').locator('select').select_option('!discard')
                page.locator('#schema-preview').click()
                expect(page.locator('#schema-state')).to_contain_text('1 umbenannt, 1 ausdrücklich weggelassen')
                page.locator('#schema-apply').click()
                expect(page.locator('#message')).to_contain_text('Schema in Editor übernommen')
                assert 'OLD_DESCR' not in json.loads(page.locator('#cam').input_value())['records'][0]['values']
                assert 'LEGACY_EMPTY' not in json.loads(page.locator('#cam').input_value())['records'][0]['values']
                # A source edit while a review response is delayed discards it.
                page.evaluate("""() => {
                    const original=window.fetch;
                    window.fetch=(...args)=>original(...args).then(response=>
                        String(args[0]).includes('/schema/preview')?new Promise(resolve=>{
                            window.releaseStaleSchemaResponse=()=>resolve(response);
                        }):response);
                }""")
                page.locator('#schema-preview').click()
                page.wait_for_function("()=>typeof window.releaseStaleSchemaResponse==='function'")
                page.locator('#native-fields input').nth(1).fill('Changed while schema request was in flight')
                page.evaluate('window.releaseStaleSchemaResponse()')
                page.wait_for_timeout(150)
                expect(page.locator('#schema-apply')).to_be_disabled()
                expect(page.locator('#schema-rows fieldset')).to_have_count(0)
                expect(page.locator('#native-fields input').nth(1)).to_have_value('Changed while schema request was in flight')
                # Mobile navigation remains usable without a horizontally overflowing layout.
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('#tab-prepare').click()
                expect(page.locator('#panel-prepare')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                page.screenshot(path=str(tmp_path / 'stammdaten-mobile.png'), full_page=True)
                # A late authenticated response cannot restore controls after logout.
                page.locator('#tab-manage').click()
                export_row.get_by_role('button', name='Neuen Export ableiten', exact=True).click()
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (...args) => original(...args).then(response =>
                        String(args[0]).includes('/derive-preview') ? new Promise(resolve => {
                            window.releaseStaleExportResponse = () => resolve(response);
                        }) : response);
                }""")
                page.locator('#export-preview').click()
                page.wait_for_function("() => typeof window.releaseStaleExportResponse === 'function'")
                page.locator('#tab-prepare').click()
                page.evaluate('delete window.releaseStaleSchemaResponse')
                page.locator('#schema-preview').click()
                page.wait_for_function("()=>typeof window.releaseStaleSchemaResponse==='function'")
                page.locator('#disconnect').click()
                page.evaluate('window.releaseStaleExportResponse()')
                page.evaluate('window.releaseStaleSchemaResponse()')
                page.wait_for_timeout(150)  # Flush the released response and its JSON continuation.
                expect(page.locator('#work')).to_be_hidden()
                expect(page.locator('#export-box')).to_be_hidden()
                expect(page.locator('#message')).to_have_text('Abgemeldet.')
                expect(page.locator('#token')).to_have_value('')
                expect(page.locator('#name')).to_have_value('')
                assert page.locator('#create-library option').count() == 0
                assert page.locator('#schema-target-release option').count() == 0
                expect(page.locator('#schema-apply')).to_be_disabled()
                assert not failures, failures
            finally:
                browser.close()
        assert all(p.read_bytes() == content for p, content in originals.items())
        managed = list((tmp_path / 'managed' / 'r').glob('*/nx'))
        managed.append(output / 'nx2512-selected')
        managed.append(output / 'nx2512-wizard')
        assert len(managed) == 3
        assert path.read_bytes()==upgrade_original
        assert all(not (p / OPTIONAL_PALETTE).exists() and not (p / SPECS['tool-metric']['legacy_path']).exists() for p in managed)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
