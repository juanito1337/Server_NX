"""The three workspaces must not duplicate controls or mix daily work and upgrades."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


class Layout(HTMLParser):
    void = {'meta', 'input', 'link', 'br', 'hr', 'img'}

    def __init__(self, text):
        super().__init__()
        self.stack, self.controls, self.ids = [], {}, []
        self.feed(text)
        assert not self.stack

    def handle_starttag(self, tag, values):
        attributes = dict(values)
        identifier = attributes.get('id')
        panel = next((attrs['id'] for _, attrs in reversed(self.stack)
                      if attrs.get('role') == 'tabpanel'), None)
        if identifier:
            self.ids.append(identifier)
            self.controls[identifier] = (panel, attributes)
        if tag not in self.void:
            self.stack.append((tag, attributes))

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1][0] == tag, f'Unbalanced HTML near {tag}'
        self.stack.pop()


def test_workspaces_are_separate_and_all_control_ids_are_unique():
    html = (Path(__file__).parents[1] / 'nxserver' / 'web' / 'index.html').read_text(encoding='utf-8')
    layout = Layout(html)
    assert all(count == 1 for count in Counter(layout.ids).values())
    tabs = [attrs for _, attrs in layout.controls.values() if attrs.get('role') == 'tab']
    assert len(tabs) == 3
    assert layout.controls['tab-manage'][1]['aria-selected'] == 'true'
    assert 'hidden' not in layout.controls['panel-manage'][1]
    assert 'hidden' in layout.controls['panel-prepare'][1]
    assert 'hidden' in layout.controls['panel-update'][1]
    assert 'hidden' in layout.controls['review-box'][1]
    assert 'hidden' in layout.controls['export-box'][1]
    expected = {
        'panel-manage': ('auditview-box','auditview-title','auditview-query','auditview-read','auditview-close','auditview-state',
                         'auditview-items','auditview-page','auditview-prev','auditview-next','auditview-detail-state','auditview-detail','auditview-payload',
                         'resources', 'resource-query', 'resource-kind', 'resource-status', 'resource-library','resource-prev','resource-next','new',
                         'resource-release','resource-export-status','resource-export-context',
                         'library-release', 'native-library', 'library-read', 'library-prev','library-next','library-result-state','catalog-import',
                         'catalog-scope','catalog-select-page','catalog-clear-selection','catalog-selection-state','catalog-selection-list',
                         'catalog-objects','catalog-page-state','catalog-objects-prev','catalog-objects-next','catalog-source-state',
                         'history-box','history-title','history-name','history-state','history-reload','history-close','history-index','history-index-page','history-index-prev','history-index-next','history-before','history-after','history-area','history-query','history-compare','history-diff','history-diff-page','history-diff-prev','history-diff-next',
                         'erp-box','erp-state-filter','erp-kind-filter','erp-company-filter','erp-query','erp-read','erp-overview-state','erp-counts','erp-items','erp-page','erp-prev','erp-next','erp-company','erp-system','erp-lookup','erp-lookup-state','erp-lookup-result',
                         'erp2-box','erp2-status-read','erp2-status','erp2-migration','erp2-migration-preview','erp2-migration-state','erp2-migration-issues','erp2-migration-accept','erp2-migrate','erp2-management',
                         'erp2-query','erp2-company','erp2-system','erp2-articles-read','erp2-articles-state','erp2-articles','erp2-articles-page','erp2-articles-prev','erp2-articles-next',
                         'erp2-form','erp2-form-context','erp2-article-new','erp2-form-company_id','erp2-form-system_id','erp2-form-number','erp2-form-description','erp2-form-unit','erp2-form-source_revision','erp2-article-save',
                         'erp2-selected','erp2-variants-query','erp2-variants-read','erp2-variants-state','erp2-variants','erp2-variants-page','erp2-variants-prev','erp2-variants-next',
                         'erp2-resource','erp2-variant-read','erp2-variant-state','erp2-variant-detail','erp2-assign','erp2-unlink','erp2-variant-history',
                         'erp2-navigation-state','erp2-current-article','erp2-current-article-context','erp2-current-article-fields','erp2-current-article-open','erp2-current-article-history',
                         'erp2-history-box','erp2-history-title','erp2-history-read','erp2-history-state','erp2-history-items','erp2-history-page','erp2-history-prev','erp2-history-next',
                         'erp2-write-state','erp2-recovery','erp2-recovery-state','erp2-request-key','erp2-receipt','erp2-repeat','erp2-cancel','erp2-ticket-discard',
                         'releases', 'approve', 'review-path', 'syslog-file', 'syslog-upload',
                         'syslog', 'scan', 'syslog-state', 'review-gate', 'review-close', 'export-name', 'export-preview',
                         'export-create', 'export-defaults', 'export-actions', 'export-column-fields','export-reference-reviews',
                         'export-pax-box','export-pax-state','export-pax-items','export-pax-page','export-pax-prev','export-pax-next',
                         'export-files-box','export-files-view','export-files-change','export-files-query','export-files-load',
                         'export-files-state','export-files-totals','export-files-items','export-files-page','export-files-prev','export-files-next',
                         'export-native-box','export-native-library','export-native-change','export-native-query','export-native-load',
                         'export-native-state','export-native-files','export-native-objects','export-native-page','export-native-prev','export-native-next',
                         'export-native-detail','export-native-title','export-native-side','export-native-read','export-native-row-state',
                         'export-native-rows','export-native-row-page','export-native-row-prev','export-native-row-next',
                         'launch-profile-box','launch-profile-release','launch-profile-resource','launch-profile-path',
                         'launch-profile-preview','launch-profile-apply','launch-profile-download','launch-profile-state','launch-profile-report',
                         'integrity-files','integrity-anchor','integrity-start','integrity-state',
                         'integrity-checkpoint','integrity-issues','integrity-jobs',
                         'backup-create','backup-legacy','backup-snapshots','backup-jobs',
                         'recovery-backup','recovery-root','recovery-name','recovery-preview','recovery-unsigned','recovery-create',
                         'backup-request-box','backup-request-state','backup-request-key','backup-request-receipt','backup-request-repeat','backup-request-fence',
                         'log-upload-recovery','log-request-state','log-request-key','log-receipt','log-repeat','log-fence','log-ack',
                         'file-adopt-release','file-adopt-kind','file-adopt-resource','file-adopt-name','file-adopt-load',
                         'file-adopt-files','file-adopt-preview','file-adopt-apply','file-adopt-state','file-adopt-query',
                         'file-adopt-file-status','file-adopt-prev','file-adopt-next','file-adopt-selected',
                         'file-adopt-count','file-adopt-selection','file-adopt-clear',
                         'release-job-recovery','release-job-recovery-state','release-job-key',
                         'release-job-receipt','release-job-repeat','release-job-fence',
                         'release-job-refresh','release-job-state','release-job-list'),
        'panel-prepare': ('create-release', 'create-library', 'native-block', 'native-new', 'native-add-row',
                          'clone-box','clone-title','clone-source','clone-key','clone-name','clone-preview','clone-apply','clone-close','clone-state',
                          'clone-values-box','clone-values','clone-values-page','clone-values-prev','clone-values-next',
                          'editor-reference-box','editor-ref-target','editor-ref-preview','editor-ref-state','editor-ref-rows',
                          'editor-ref-page','editor-ref-prev','editor-ref-next','editor-ref-choice','editor-ref-query','editor-ref-search',
                          'editor-ref-key','editor-ref-options-state','editor-ref-options-prev','editor-ref-options-next','editor-ref-apply',
                          'name', 'kind', 'cam', 'native-fields','native-field-controls','native-field-state','native-field-block','native-field-query','native-row-policy',
                          'native-field-row','native-field-jump','native-field-prev','native-field-next','native-key-controls','native-key-state','native-key-new','native-key-change', 'assetfile', 'save','save-recovery-box',
                          'save-recovery-state','save-request-key','save-receipt','save-repeat','save-cancel',
                          'attachments', 'attachment-state', 'asset-query', 'asset-library-load',
                          'upload-recovery-box','upload-recovery-state','upload-request-key','upload-receipt','upload-repeat','upload-fence','upload-ack',
                          'asset-library-items', 'asset-library-prev', 'asset-library-next',
                          'graphics-editor','graphics-context','graphics-load','graphics-state','graphics-rows','graphics-preview','graphics-apply','graphics-default',
                          'geometry-mode', 'geometry-evidence', 'step-resource', 'step-job-create',
                          'step-recovery-box','step-recovery-state','step-request-key','step-receipt','step-repeat','step-fence','step-ack',
                          'geometry-report-summary', 'geometry-report-parts','schema-target-release',
                          'result-library','result-key','result-collector','result-upload','result-select',
                          'result-target','result-preview','result-apply','result-actions','result-files','result-file-state',
                          'result-columns-box','result-columns','result-columns-page','result-columns-prev','result-columns-next','result-columns-clear','model-bindings','model-package-detach',
                          'schema-preview','schema-apply','schema-state','schema-rows','schema-page','schema-prev','schema-next',
                          'file-package-editor','package-kind-state','package-enabled','package-version','package-load-assets','package-asset',
                          'package-root','package-add','package-entries','package-entry-query','package-entry-state','package-entry-prev','package-entry-next','package-checks','package-evidence','package-bind','package-state'),
        'panel-update': ('source', 'source-path', 'target', 'target-path', 'nxversion',
                         'upgrade-conflict-review','upgrade-conflict-title','upgrade-conflict-state','upgrade-conflict-query',
                         'upgrade-conflict-kind','upgrade-conflict-page','upgrade-conflict-prev','upgrade-conflict-next','upgrade-file-conflicts',
                         'import-preview', 'import', 'conflicts', 'import-defaults','upgrade-mode',
                         'upgrade-check','upgrade-preview','upgrade-build','upgrade-columns','upgrade-jobs',
                         'upgrade-review','upgrade-review-title','upgrade-review-summary','upgrade-review-context',
                         'upgrade-review-kind','upgrade-review-query','upgrade-review-items','upgrade-review-page',
                         'upgrade-review-prev','upgrade-review-next',
                         'upgrade-source-release','extract-version','extract-start','extract-state',
                         'upgrade-recovery-box','upgrade-recovery-state','upgrade-request-key',
                         'upgrade-receipt','upgrade-repeat','upgrade-cancel',
                         'reference-resolutions','reference-applied'),
    }
    for panel, controls in expected.items():
        for identifier in controls:
            assert layout.controls[identifier][0] == panel, identifier
    for identifier in ('connect', 'token', 'message', 'summary', 'active', 'details','file-download-box','file-download-title','file-download-state','file-download-cancel'):
        assert layout.controls[identifier][0] is None
    for identifier in ('release-validation-box','release-validation-state','release-validation-context','release-validation-kind','release-validation-query','release-validation-items','release-validation-page','release-validation-prev','release-validation-next'):
        assert layout.controls[identifier][0] is None
    assert 'hidden' in layout.controls['release-validation-box'][1]
    for identifier in ('historical-validation-box','historical-validation-state','historical-validation-context','historical-validation-query','historical-validation-items','historical-validation-page','historical-validation-prev','historical-validation-next'):
        assert layout.controls[identifier][0] is None
    assert 'hidden' in layout.controls['historical-validation-box'][1]
    for tab in tabs:
        assert layout.controls[tab['aria-controls']][1]['aria-labelledby'] == tab['id']
    assert layout.controls['native-export-policy'][0] == 'panel-prepare'
    for identifier in ('kind', 'camstatus'):
        assert layout.controls[identifier][1]['aria-describedby'] == 'native-export-policy'
    policy = html.split('<p id="native-export-policy">', 1)[1].split('</p>', 1)[0]
    assert 'Prüfmittel bleiben Katalogdaten ohne native NX-Ausgabe' in policy
    assert 'auch bei „Für Export geprüft“' in policy
    assert 'CMM-Taster' in policy and 'Dateipakete' in policy
    assert 'Speichern allein ändert keinen bestehenden NX-Ordner' in policy
