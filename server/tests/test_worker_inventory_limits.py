"""Producer/consumer report bounds with fake NX objects and disposable stores."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nxserver import nxworker
from nxserver.diagnostics import Report
from nxserver.safety import DomainError
from nxserver.store import canonical
from test_geometry import pilot
from test_worker_reads import job_files
from test_worker_outputs import fake_import
from test_worker_component_limits import check_retained_report, open_components, leaf


def body(index=0, name='Synthetic body'):
    return SimpleNamespace(JournalIdentifier='BODY ' + str(index), Name=name,
        IsSolidBody=True, IsConvergentBody=False, GetFaces=lambda: [], Layer=1, Color=42)


def replace_bodies(session, values):
    original = session.Parts.OpenBaseDisplay

    def opened(*args):
        part, status = original(*args)
        part.Bodies = values
        return part, status

    session.Parts.OpenBaseDisplay = opened


def replace_parts(session, monkeypatch, count, rejected=None):
    original = session.Parts.OpenBaseDisplay
    parts = []

    def opened(*args):
        part, status = original(*args)
        for index in range(count):
            path = Path(part.FullPath) if index == 0 else Path(part.FullPath).with_name('part_' + str(index) + '.prt')
            if index:
                path.write_bytes(b'Synthetic component, not actual CAD')
            parts.append(SimpleNamespace(**{**vars(part), 'FullPath': str(path)}))
        if rejected is not None:
            parts.append(rejected)
        return part, status

    session.Parts.OpenBaseDisplay = opened
    monkeypatch.setattr(type(session.Parts), '__iter__', lambda self: iter(parts if self.opened else []))


def check_uploadable(fixture, result, previous):
    check_retained_report(fixture, result, previous)
    raw = (fixture[0] / 'output/report.json').read_bytes()
    assert len(raw) <= nxworker.MAX_REPORT_BYTES
    assert raw == canonical(result).encode('utf-8')


def test_inventory_limits_match_the_server_contract():
    schema = Report.model_json_schema()
    assert schema['properties']['parts']['maxItems'] == nxworker.MAX_REPORT_PARTS
    assert schema['$defs']['Part']['properties']['bodies']['maxItems'] == nxworker.MAX_PART_BODIES
    assert all(schema['properties'][key]['maxItems'] == nxworker.MAX_REPORT_MESSAGES for key in ('errors', 'warnings'))
    assert nxworker.MAX_REPORT_BYTES == 1024 * 1024
    assert nxworker.MAX_MESSAGE_LENGTH == 16000


@pytest.mark.parametrize('count', [0, 1, 2, 3])
def test_loaded_part_count_boundary(fake_import, monkeypatch, count):
    directory, _, session, api, *_ = fake_import
    monkeypatch.setattr(nxworker, 'MAX_REPORT_PARTS', 2)
    replace_parts(session, monkeypatch, count)
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert len(result['parts']) == min(count, 2)
    assert result['status'] == ('failed' if count > 2 else 'needs_structure')
    if count > 2:
        assert any('Part count exceeds' in text for text in result['errors'])
    check_uploadable(fake_import, result, previous)


@pytest.mark.parametrize('count', [0, 1, 2, 3])
def test_body_count_boundary_never_records_a_silently_incomplete_part(fake_import, monkeypatch, count):
    directory, _, session, api, *_ = fake_import
    monkeypatch.setattr(nxworker, 'MAX_PART_BODIES', 2)
    replace_bodies(session, [body(index) for index in range(count)])
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert result['status'] == ('failed' if count > 2 else 'needs_structure')
    if count > 2:
        assert not result['parts']
        assert any('Body count exceeds' in text for text in result['errors'])
    else:
        assert len(result['parts'][0]['bodies']) == count
    check_uploadable(fake_import, result, previous)


@pytest.mark.parametrize('kind', ['parts', 'bodies'])
def test_count_rejection_happens_before_reading_next_nx_object(fake_import, monkeypatch, kind):
    directory, _, session, api, *_ = fake_import
    inspected = []

    class Rejected:
        def __getattr__(self, name):
            inspected.append(name)
            raise AssertionError('Rejected NX object must not be inspected.')

    if kind == 'parts':
        monkeypatch.setattr(nxworker, 'MAX_REPORT_PARTS', 2)
        replace_parts(session, monkeypatch, 2, Rejected())
        message = 'Part count exceeds'
    else:
        monkeypatch.setattr(nxworker, 'MAX_PART_BODIES', 2)
        replace_bodies(session, [body(0), body(1), Rejected()])
        message = 'Body count exceeds'
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert not inspected
    assert result['status'] == 'failed' and any(message in text for text in result['errors'])
    check_uploadable(fake_import, result, previous)


def test_nonempty_session_rejects_after_first_background_part(fake_import, monkeypatch):
    directory, _, session, api, *_ = fake_import
    visited = []

    def background(self):
        visited.append('first')
        yield object()
        pytest.fail('Empty-session guard must not enumerate additional background parts.')

    monkeypatch.setattr(type(session.Parts), '__iter__', background)
    previous = Path.cwd()
    with pytest.raises(DomainError, match='fresh, empty NX session'):
        nxworker.run_job(directory, session, api)
    assert visited == ['first'] and Path.cwd() == previous
    assert not (directory / 'output').exists()


def test_incremental_budget_counts_exact_compact_utf8_bytes(monkeypatch):
    value = {'path': 'ä.prt', 'bodies': []}
    report = {'parts': []}
    exact = len(canonical({'parts': [value]}).encode('utf-8'))
    monkeypatch.setattr(nxworker, 'MAX_REPORT_BYTES', exact)
    budget = nxworker._ReportBudget(report)
    budget.append('parts', value)
    assert budget.size == exact
    with pytest.raises(DomainError, match='Diagnostic byte budget'):
        budget.append('parts', value)
    assert report == {'parts': [value]}


def test_unicode_inventory_with_valid_counts_still_obeys_one_mib_limit(fake_import):
    directory, _, session, api, *_ = fake_import
    replace_bodies(session, [body(index, 'ä' * 800) for index in range(1000)])
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert result['status'] == 'failed'
    assert any('Diagnostic byte budget' in text for text in result['errors'])
    check_uploadable(fake_import, result, previous)


@pytest.mark.parametrize('field', ['parts', 'components', 'files'])
def test_incremental_budget_bounds_each_inventory(fake_import, monkeypatch, field):
    directory, _, session, api, *_ = fake_import
    monkeypatch.setattr(nxworker, 'MAX_REPORT_BYTES', 3000)
    if field == 'parts':
        replace_parts(session, monkeypatch, 20)
    elif field == 'components':
        open_components(session, lambda part: [leaf(part, 'ä' * 800) for _ in range(20)])
    else:
        original = nxworker.tree_files

        def files(output):
            for index in range(20):
                (output / ('synthetic_' + str(index) + '.txt')).write_bytes(b'Synthetic evidence')
            return original(output)

        monkeypatch.setattr(nxworker, 'tree_files', files)
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert result['status'] == 'failed'
    assert any('Diagnostic byte budget' in text for text in result['errors'])
    assert len(result[field]) < 20
    check_uploadable(fake_import, result, previous)


def test_normal_unicode_metadata_is_not_rewritten_or_pretty_printed(fake_import):
    directory, _, session, api, *_ = fake_import
    replace_bodies(session, [body(name='Fräser – Prüfung Ø12')])
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert result['status'] == 'needs_structure'
    assert result['parts'][0]['bodies'][0]['name'] == 'Fräser – Prüfung Ø12'
    check_uploadable(fake_import, result, previous)


@pytest.mark.parametrize('failure', ['long', 'nul', 'many_errors', 'many_warnings'])
def test_diagnostic_text_limits_are_explicit_and_uploadable(fake_import, monkeypatch, failure):
    directory, _, session, api, *_ = fake_import
    summarize = nxworker.summarize

    def oversized(*args):
        result = summarize(*args)
        if failure == 'long':
            result['errors'] = ['ä' * 16001]
        elif failure == 'nul':
            result['errors'] = ['NX error\x00synthetic detail']
        else:
            result['errors' if failure == 'many_errors' else 'warnings'] = ['Synthetic message'] * 10001
        return result

    monkeypatch.setattr(nxworker, 'summarize', oversized)
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert result['status'] == 'failed'
    assert result['errors'][0].startswith('Diagnostic messages exceed')
    assert all(len(result[key]) <= 10000 for key in ('errors', 'warnings'))
    assert all(len(text) <= 16000 and '\x00' not in text for key in ('errors', 'warnings') for text in result[key])
    check_uploadable(fake_import, result, previous)


@pytest.mark.parametrize('field', ['parts', 'components', 'files', 'errors', 'warnings'])
def test_final_overhead_trimming_retains_prefix_and_explicit_failure(field, monkeypatch):
    # Unit-level serializer test; real API acceptance is covered above. Keep a
    # realistic pinned identity so the fallback cannot rely on dropping it.
    report = {'job_id': 'Synthetic immutable identity', 'status': 'needs_selection',
        'automatic_cam_approval': False, 'solid_prototypes': 1, 'body_prototypes': 1,
        'component_occurrences': 1, 'parts': [], 'components': [], 'files': [],
        'errors': [], 'warnings': []}
    values = ['ä' * 200 + str(index) for index in range(20)]
    report[field] = values[:]
    monkeypatch.setattr(nxworker, 'MAX_REPORT_BYTES', 1500)
    text = nxworker._bounded_report_text(report)
    assert len(text.encode('utf-8')) <= 1500 and json.loads(text) == report
    assert report['status'] == 'failed' and not report['automatic_cam_approval']
    assert report['errors'][0].startswith('Diagnostic byte budget')
    assert all(key not in report for key in ('solid_prototypes', 'body_prototypes', 'component_occurrences'))
    retained = report[field][1:] if field == 'errors' else report[field]
    assert 0 < len(retained) < len(values) and retained == values[:len(retained)]


def test_final_trimming_can_bound_multiple_arrays_without_losing_identity(monkeypatch):
    fields = ('parts', 'components', 'files', 'errors', 'warnings')
    report = {field: ['ä' * 200 + str(index) for index in range(20)] for field in fields}
    report.update(job_id='Pinned synthetic identity', status='needs_selection', automatic_cam_approval=False)
    monkeypatch.setattr(nxworker, 'MAX_REPORT_BYTES', 1500)
    text = nxworker._bounded_report_text(report)
    assert len(text.encode('utf-8')) <= 1500
    assert report['job_id'] == 'Pinned synthetic identity' and report['status'] == 'failed'
    assert report['errors'][0].startswith('Diagnostic byte budget')
    assert sum(not report[field] for field in fields) >= 2


def test_impossible_identity_budget_is_not_silently_rewritten(monkeypatch):
    report = {field: [] for field in ('parts', 'components', 'files', 'errors', 'warnings')}
    report.update(job_id='Pinned synthetic identity', status='failed', automatic_cam_approval=False)
    monkeypatch.setattr(nxworker, 'MAX_REPORT_BYTES', 10)
    with pytest.raises(DomainError, match='identity alone exceeds'):
        nxworker._bounded_report_text(report)
    assert report['job_id'] == 'Pinned synthetic identity'
