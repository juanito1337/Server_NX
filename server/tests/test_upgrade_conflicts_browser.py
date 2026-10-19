"""Three-way file decisions on synthetic shares, never real CAM data or NX."""
import hashlib
import json
import os

import pytest

from test_libraries import write
from test_upgrade_recovery_browser import wizard, jobs

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated file conflict review.')


def conflicts(store, source):
    cases = {'changed': ('Old stock', 'New stock', 'Customer changed'),
             'removed': ('Removed old stock', None, 'Customer retained'),
             'added': (None, 'New Siemens collision', 'Customer collision')}
    for name, values in cases.items():
        relative = 'MACH/custom/conflicts/' + name + '.txt'
        for root, text in zip((store.standards['2406'], store.standards['2512'], source), values):
            if text is not None: write(root, relative, text)
    return cases


def test_three_way_hashes_explain_changed_removed_and_added_files_before_any_write(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    cases = conflicts(store, source); before = store.manifest(source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-conflict-state')).to_contain_text('3 Dateikonflikte', timeout=15000)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    for name, values in cases.items():
        row = page.locator('#upgrade-file-conflicts tr').filter(has_text='conflicts/' + name + '.txt')
        for text in values:
            if text is not None: expect(row).to_contain_text(hashlib.sha256(text.encode()).hexdigest())
        if name == 'removed': expect(row).to_contain_text('Zielstandard entfernt')
        elif name == 'added': expect(row).to_contain_text('Neu im Zielstandard')
        else: expect(row).to_contain_text('Beide Seiten geändert')
    assert store.manifest(source) == before and not target.exists() and len(jobs(store)) == 1


def test_decisions_survive_paging_and_build_exact_selected_bytes(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    conflicts(store, source); before = store.manifest(source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts tr')).to_have_count(3, timeout=15000)
    for name, choice in [('changed', 'customer'), ('removed', 'standard'), ('added', 'standard')]:
        page.locator('#upgrade-conflict-query').fill('conflicts/' + name + '.txt')
        expect(page.locator('#upgrade-file-conflicts tr')).to_have_count(1)
        page.locator('#upgrade-file-conflicts select').select_option(choice)
    page.locator('#upgrade-conflict-query').fill('')
    choices = json.loads(page.locator('#upgrade-file-resolutions').input_value())
    assert choices == {'MACH/custom/conflicts/changed.txt': 'customer',
                       'MACH/custom/conflicts/removed.txt': 'standard',
                       'MACH/custom/conflicts/added.txt': 'standard'}
    expect(page.locator('#upgrade-conflict-state')).to_contain_text('3 ausdrücklich entschieden')
    expect(page.locator('#upgrade-build')).to_be_disabled()
    page.locator('#upgrade-preview').click(); expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt', timeout=15000)
    assert (target / 'MACH/custom/conflicts/changed.txt').read_text() == 'Customer changed'
    assert (target / 'MACH/custom/conflicts/added.txt').read_text() == 'New Siemens collision'
    assert not (target / 'MACH/custom/conflicts/removed.txt').exists()
    assert store.manifest(source) == before and store.active()['release_id'] is None


def test_many_conflicts_have_local_search_and_pages_without_new_jobs(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    for index in range(105):
        path = f'MACH/custom/conflicts/item{index:03d}.txt'
        write(store.standards['2406'], path, 'Old')
        write(store.standards['2512'], path, 'New')
        write(source, path, 'Customer')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-file-conflicts tr')).to_have_count(50, timeout=15000)
    page.locator('#upgrade-file-conflicts select').first.select_option('customer')
    for count in (50, 5):
        page.locator('#upgrade-conflict-next').click()
        expect(page.locator('#upgrade-file-conflicts tr')).to_have_count(count)
    page.locator('#upgrade-conflict-query').fill('ITEM000.TXT')
    expect(page.locator('#upgrade-file-conflicts tr')).to_have_count(1)
    expect(page.locator('#upgrade-file-conflicts select')).to_have_value('customer')
    expect(page.locator('#upgrade-conflict-prev')).to_be_disabled()
    expect(page.locator('#upgrade-conflict-next')).to_be_disabled()
    page.locator('#upgrade-conflict-query').fill('not present')
    expect(page.locator('#upgrade-file-conflicts tr')).to_have_count(0)
    assert len(jobs(store)) == 1 and not target.exists()
