"""Readable upgrade decisions, on synthetic shares and disposable Edge profiles."""
import json
import os
from pathlib import PurePosixPath

import pytest

from nxserver.libraries import OPTIONAL_PALETTE, SPECS
from test_libraries import simple, write
from test_upgrade_recovery_browser import jobs, wizard

pytestmark = pytest.mark.skipif(
    os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated upgrade review.',
)


def test_review_explains_customer_files_omissions_libraries_and_warnings(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    legacy = SPECS['holder-metric']['legacy_path']
    unknown = str(PurePosixPath(legacy).parent / 'additional_old.dat')
    write(source, legacy, simple('HD_HOLDER', 'Synthetic holder'))
    write(source, unknown, simple('UNKNOWN', 'Explicitly excluded fixture'))
    write(source, 'MACH/custom/customer-model.prt', 'Synthetic model; never open in NX')
    write(source, 'MACH/auxiliary/outside.txt', 'Not part of the shared folder')
    write(source, OPTIONAL_PALETTE, 'Optional omitted palette fixture')
    page.locator('#legacy-omissions').fill(json.dumps([unknown]))
    before = store.manifest(source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    expect(page.locator('#upgrade-review')).to_be_visible()
    expect(page.locator('#upgrade-review-summary')).to_contain_text('NX2406 → NX2512')
    expect(page.locator('#upgrade-review-summary')).to_contain_text('Zieldateien')
    expect(page.locator('#upgrade-review-context')).to_contain_text(str(source))
    expect(page.locator('#upgrade-review-context')).to_contain_text(str(target))
    page.locator('#upgrade-review-kind').select_option('warning')
    expect(page.locator('#upgrade-review-items tr')).to_have_count(1)
    expect(page.locator('#upgrade-review-items')).to_contain_text('customer-model.prt')
    expect(page.locator('#upgrade-review-items')).to_contain_text('NX-Kompatibilität prüfen')
    page.locator('#upgrade-review-kind').select_option('ignored')
    expect(page.locator('#upgrade-review-items')).to_contain_text('MACH/auxiliary/outside.txt')
    page.locator('#upgrade-review-kind').select_option('legacy_library_merged')
    page.locator('#upgrade-review-query').fill('additional_old.dat')
    expect(page.locator('#upgrade-review-items')).to_contain_text('Ausdrücklich ausgelassene Alt-Datenbank')
    expect(page.locator('#upgrade-review-items')).not_to_contain_text('Daten erfolgreich zusammengeführt')
    page.locator('#upgrade-review-query').fill('')
    page.locator('#upgrade-review-kind').select_option('optional_palette_omitted')
    expect(page.locator('#upgrade-review-items')).to_contain_text(OPTIONAL_PALETTE)
    page.locator('#upgrade-review-kind').select_option('library')
    expect(page.locator('#upgrade-review-items')).to_contain_text(SPECS['holder-metric']['path'])
    expect(page.locator('#upgrade-review-items')).to_contain_text('Neue Objektgruppen: 1')
    expect(page.locator('#upgrade-build')).to_be_enabled()
    assert len(jobs(store)) == 1 and not target.exists()
    assert store.manifest(source) == before and store.active()['release_id'] is None


def test_review_search_and_pagination_are_local_and_do_not_rebuild(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    for index in range(105):
        write(source, f'MACH/custom/batch/item{index:03d}.txt', 'Synthetic customer file')
    before = store.manifest(source)
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    page.locator('#upgrade-review-kind').select_option('customer_copied')
    page.locator('#upgrade-review-query').fill('BATCH/')
    found = []
    for count in (50, 50, 5):
        expect(page.locator('#upgrade-review-items tr')).to_have_count(count)
        found.extend(page.locator('#upgrade-review-items tr td:first-child').all_text_contents())
        if count == 50:
            page.locator('#upgrade-review-next').click()
    assert len(set(found)) == 105
    assert set(found) == {f'MACH/custom/batch/item{index:03d}.txt' for index in range(105)}
    expect(page.locator('#upgrade-review-next')).to_be_disabled()
    page.locator('#upgrade-review-query').fill('item104.txt')
    expect(page.locator('#upgrade-review-items tr')).to_have_count(1)
    expect(page.locator('#upgrade-review-prev')).to_be_disabled()
    expect(page.locator('#upgrade-review-next')).to_be_disabled()
    page.locator('#upgrade-review-query').fill('no-such-file')
    expect(page.locator('#upgrade-review-items tr')).to_have_count(0)
    expect(page.locator('#upgrade-review-page')).to_contain_text('0–0 von 0')
    expect(page.locator('#upgrade-build')).to_be_enabled()
    assert len(jobs(store)) == 1 and not target.exists() and store.manifest(source) == before


@pytest.mark.parametrize('field,value', [
    ('release-name', 'Changed review name'),
    ('import-defaults', '{} '),
    ('source-path', 'changed-source'),
])
def test_review_is_removed_on_input_change_even_after_returning_to_old_value(wizard, field, value):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    expect(page.locator('#upgrade-review')).to_be_visible()
    previous = page.locator('#' + field).input_value()
    page.locator('#' + field).fill(value)
    page.locator('#' + field).fill(previous)
    expect(page.locator('#upgrade-review')).to_be_hidden()
    expect(page.locator('#upgrade-review-items tr')).to_have_count(0)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    expect(page.locator('#upgrade-review')).to_be_visible()
    assert len(jobs(store)) == 2 and not target.exists()


def test_conflicted_preview_is_readable_but_not_buildable_and_logout_clears_it(wizard):
    from playwright.sync_api import expect
    page, store, source, target = wizard
    write(store.standards['2512'], SPECS['holder-metric']['path'], simple(extra=True))
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-review')).to_be_visible(timeout=15000)
    expect(page.locator('#upgrade-review-summary')).to_contain_text('Konflikt')
    expect(page.locator('#upgrade-review-summary')).to_contain_text('nicht bestätigt')
    expect(page.locator('#upgrade-build')).to_be_disabled()
    page.locator('#disconnect').click()
    expect(page.locator('#upgrade-review')).to_be_hidden()
    expect(page.locator('#upgrade-review-items tr')).to_have_count(0)
    expect(page.locator('#upgrade-review-query')).to_have_value('')
    assert len(jobs(store)) == 1 and not target.exists()
