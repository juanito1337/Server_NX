"""A shorter group invalidates pending row-indexed reference responses."""
import json
import os

import pytest

from test_editor_references_browser import reference_ui,inspect,choose
from test_filepackages import packages
from test_filepackages_browser import package_ui
from test_requests_browser import hold,track,release

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated row/reference contexts.')


@pytest.mark.parametrize('old_error',[False,True])
def test_removed_row_discards_pending_reference_preview_and_all_old_indices(reference_ui,old_error):
    from playwright.sync_api import expect
    page,store,*_=reference_ui;before=store.audit_entries();original=json.loads(page.locator('#cam').input_value())
    hold(page,'/api/v1/editor-references/preview',error=old_error);track(page,'editor-ref-preview')
    page.locator('#editor-ref-preview').click();page.wait_for_function('()=>window.__held')
    page.on('dialog',lambda dialog:dialog.accept());page.locator('[data-native-remove="0"]').click()
    expected={**original,'records':original['records'][1:]};release(page)
    assert json.loads(page.locator('#cam').input_value())==expected and store.audit_entries()==before
    expect(page.locator('#editor-ref-rows tr')).to_have_count(0)
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    page.locator('#editor-ref-preview').click();expect(page.locator('#editor-ref-rows tr')).to_have_count(2)
    expect(page.locator('#editor-ref-rows')).to_contain_text('Zeile 1')


def test_pending_reference_apply_does_not_restore_a_removed_row_or_replace_its_neighbor(reference_ui):
    from playwright.sync_api import expect
    page,store,*_=reference_ui;before=store.audit_entries();original=json.loads(page.locator('#cam').input_value())
    inspect(page);choose(page);page.locator('#editor-ref-key').select_option('MAT_OK')
    hold(page,'/api/v1/editor-references/apply');track(page,'editor-ref-apply')
    page.locator('#editor-ref-apply').click();page.wait_for_function('()=>window.__held')
    page.on('dialog',lambda dialog:dialog.accept());page.locator('[data-native-remove="0"]').click();release(page)
    assert json.loads(page.locator('#cam').input_value())=={**original,'records':original['records'][1:]}
    expect(page.locator('#editor-ref-apply')).to_be_disabled();expect(page.locator('#editor-ref-choice')).to_be_hidden()
    expect(page.locator('#message')).to_contain_text('nur im Editor entfernt');assert store.audit_entries()==before
