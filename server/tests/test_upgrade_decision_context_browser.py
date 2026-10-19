"""Decision controls belong to one visible source/report, never a later form."""
import json
import os

import pytest

from nxserver.libraries import NativeTable, SPECS
from test_libraries import simple, write
from test_upgrade_recovery_browser import wizard, jobs

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated decision-context GUI; no NX.')


def decision_fixture(page, store, source, kind, workflow='upgrade'):
    relative = SPECS['holder-metric']['path']
    if kind.startswith('column'):
        write(store.standards['2512'], relative, simple(descr='Synthetic new stock', extra=True))
        selector = '#upgrade-columns input[type="checkbox"]' if kind == 'column' else '#upgrade-columns input:not([type="checkbox"])'
    elif kind == 'library':
        write(source, relative, simple(descr='Synthetic customer change'))
        if workflow == 'prepared':
            # A target-schema source legitimately uses the target as baseline.
            # Use a genuine old-schema/new-standard conflict for this test.
            write(store.standards['2512'], relative, simple(descr='Synthetic new stock', extra=True))
            page.locator('#import-defaults').fill(json.dumps({'holder-metric': {'SAMPLE:1': {'EXTRA': 'EXPLICIT_TEST_ONLY'}}}))
        selector = '#conflicts select'
    else:
        write(source, 'MACH/resource/library/tool/henning_dierk/unknown_database.dat', simple('SYNTHETIC_UNKNOWN'))
        selector = '#conflicts select'
    if workflow == 'prepared':
        page.locator('#upgrade-mode').select_option('prepared')
    page.locator('#upgrade-preview' if workflow == 'upgrade' else '#import-preview').click()
    from playwright.sync_api import expect
    expect(page.locator(selector)).to_have_count(1, timeout=15000)
    page.evaluate('(selector)=>{globalThis.__oldDecision=document.querySelector(selector);}', selector)
    if kind.startswith('column'):
        page.evaluate('''()=>{const box=__oldDecision.closest('fieldset');globalThis.__oldColumn=box.querySelector('input:not([type="checkbox"])');globalThis.__oldCheck=box.querySelector('input[type="checkbox"]');__oldColumn.value='STALE_TEST_VALUE';}''')
    return selector


def raw_decisions(page):
    return {name: page.locator('#' + name).input_value()
            for name in ('import-defaults', 'import-resolutions', 'legacy-omissions', 'reference-resolutions')}


def change_context(page, source, target, change, selector, workflow='upgrade'):
    from playwright.sync_api import expect
    if change in ('target', 'roundtrip'):
        page.locator('#target-path').fill(str(target) + '-later')
        if change == 'roundtrip':
            page.locator('#target-path').fill(str(target))
    elif change == 'source':
        page.locator('#source-path').fill(str(source) + '-later')
    elif change == 'preview':
        page.locator('#upgrade-preview' if workflow == 'upgrade' else '#import-preview').click()
        page.wait_for_function('''(selector)=>[...document.querySelectorAll(selector)].some(node=>node!==globalThis.__oldDecision&&node.tagName===__oldDecision.tagName&&(__oldDecision.tagName!=='BUTTON'||node.textContent===__oldDecision.textContent))''', arg=selector, timeout=15000)
    elif change == 'logout':
        page.locator('#disconnect').click()
        expect(page.locator('#work')).to_be_hidden()
    elif change == 'mode':
        page.locator('#upgrade-mode').select_option('prepared' if workflow == 'upgrade' else 'upgrade')
    elif change == 'mode-roundtrip':
        page.locator('#upgrade-mode').select_option('prepared' if workflow == 'upgrade' else 'upgrade')
        page.locator('#upgrade-mode').select_option(workflow)
    elif change == 'normalize':
        page.locator('#normalize').uncheck()
    else:
        page.locator('#tab-prepare').click()


def invoke_old(page, kind):
    page.evaluate('''async(kind)=>{
        if(kind.startsWith('column'))__oldCheck.checked=true;
        else __oldDecision.value=kind==='legacy'?'omit':'standard';
        if(kind==='column-input')await __oldDecision.oninput();
        else await __oldDecision.onchange();
    }''', kind)


def assert_no_target_or_mutations(store, source, target, before_source, before_jobs):
    assert store.manifest(source) == before_source and len(jobs(store)) == before_jobs
    assert not target.exists() and not target.with_name(target.name + '-later').exists()
    assert store.releases() == [] and store.resources() == [] and store.active()['release_id'] is None


@pytest.mark.parametrize('kind', ['column', 'column-input', 'library', 'legacy'])
@pytest.mark.parametrize('change', ['target', 'source', 'roundtrip', 'preview', 'logout', 'tab', 'mode'])
def test_obsolete_decision_cannot_write_to_later_context(wizard, kind, change):
    page, store, source, target = wizard
    selector = decision_fixture(page, store, source, kind)
    before_source = store.manifest(source)
    change_context(page, source, target, change, selector)
    before = raw_decisions(page)
    before_jobs = len(jobs(store))
    invoke_old(page, kind)
    assert raw_decisions(page) == before
    assert_no_target_or_mutations(store, source, target, before_source, before_jobs)


@pytest.mark.parametrize('kind', ['library', 'legacy'])
@pytest.mark.parametrize('change', ['roundtrip', 'preview', 'logout', 'tab', 'mode-roundtrip', 'normalize'])
def test_prepared_import_obsolete_decisions_cannot_write(wizard, kind, change):
    page, store, source, target = wizard
    selector = decision_fixture(page, store, source, kind, 'prepared')
    before_source = store.manifest(source)
    change_context(page, source, target, change, selector, 'prepared')
    before, before_jobs = raw_decisions(page), len(jobs(store))
    invoke_old(page, kind)
    assert raw_decisions(page) == before
    assert_no_target_or_mutations(store, source, target, before_source, before_jobs)


@pytest.mark.parametrize('workflow', ['upgrade', 'prepared'])
@pytest.mark.parametrize('operation', ['apply', 'remove'])
@pytest.mark.parametrize('change', ['target', 'preview', 'tab'])
def test_obsolete_reference_apply_or_undo_cannot_write(wizard, workflow, operation, change):
    from playwright.sync_api import expect
    from test_references import reference_data
    page, store, source, target = wizard
    reference_data(store, source)
    before_source = store.manifest(source)
    if workflow == 'prepared':
        page.locator('#upgrade-mode').select_option('prepared')
    preview_button = '#upgrade-preview' if workflow == 'upgrade' else '#import-preview'
    table = '#upgrade-file-conflicts' if workflow == 'upgrade' else '#conflicts'
    page.locator(preview_button).click()
    expect(page.locator(table + ' input[list]')).to_have_count(2, timeout=15000)
    first = page.locator(table + ' tr').first
    first.locator('input[list]').fill('TMC0_000055')
    if operation == 'remove':
        first.get_by_role('checkbox').check()
        first.get_by_role('button', name='Zuordnung bestätigen').click()
        page.locator(preview_button).click()
        selector = '#reference-applied button'
        expect(page.locator(selector)).to_have_count(2, timeout=15000)
    else:
        selector = table + ' button'
    page.evaluate('''(data)=>{
        const nodes=[...document.querySelectorAll(data.selector)];
        globalThis.__oldDecision=nodes.find(node=>node.textContent===(data.operation==='remove'?'Zuordnung entfernen':'Zuordnung bestätigen'));
    }''', {'selector': selector, 'operation': operation})
    assert page.evaluate('Boolean(globalThis.__oldDecision)')
    # On preview, wait for a replacement of exactly the same button kind.
    change_context(page, source, target, change, selector, workflow)
    before, before_jobs = raw_decisions(page), len(jobs(store))
    page.evaluate('async()=>{await __oldDecision.onclick();}')
    assert raw_decisions(page) == before
    assert_no_target_or_mutations(store, source, target, before_source, before_jobs)


@pytest.mark.parametrize('kind', ['library', 'legacy'])
def test_current_prepared_decisions_still_build_the_reviewed_result(wizard, kind):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    selector = decision_fixture(page, store, source, kind, 'prepared')
    before = store.manifest(source)
    page.locator(selector).select_option('customer' if kind == 'library' else 'omit')
    if kind == 'library':
        assert json.loads(page.locator('#import-resolutions').input_value())['holder-metric'][SPECS['holder-metric']['path']]['STD'] == 'customer'
    else:
        assert json.loads(page.locator('#legacy-omissions').input_value()) == ['MACH/resource/library/tool/henning_dierk/unknown_database.dat']
    expect(page.locator('#import')).to_be_disabled()
    page.locator('#import-preview').click()
    expect(page.locator('#import')).to_be_enabled(timeout=15000)
    page.locator('#import').click()
    expect(page.locator('#message')).to_contain_text('Neuer Ordner erstellt', timeout=15000)
    assert len(store.releases()) == 1 and store.releases()[0]['test_launcher']
    if kind == 'library':
        assert NativeTable.read(target / SPECS['holder-metric']['path']).groups()['STD'][0]['values']['DESCR'] == 'Synthetic customer change'
    else:
        assert not (target / 'MACH/resource/library/tool/henning_dierk/unknown_database.dat').exists()
    assert store.manifest(source) == before and store.active()['release_id'] is None
