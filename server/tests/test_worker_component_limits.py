"""Bounded standalone STEP diagnostics; fake NX objects, no real NX execution."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nxserver import nxworker
from nxserver.diagnostics import Report
from test_geometry import pilot, HEADERS
from test_worker_reads import job_files
from test_worker_outputs import fake_import


def open_components(session, build):
    original = session.Parts.OpenBaseDisplay

    def opened(*args):
        part, status = original(*args)
        part.ComponentAssembly.RootComponent = SimpleNamespace(GetChildren=lambda: build(part))
        return part, status

    session.Parts.OpenBaseDisplay = opened


def leaf(part, name):
    return SimpleNamespace(Name=name, Prototype=part, GetChildren=lambda: [])


def chain(part, count):
    current = None
    for index in reversed(range(count)):
        child = current
        current = SimpleNamespace(Name='Synthetic node ' + str(index), Prototype=part,
            GetChildren=lambda child=child: [] if child is None else [child])
    return [] if current is None else [current]


def check_retained_report(fixture, result, previous):
    directory, job, session, api, client, store = fixture
    assert Path.cwd() == previous
    assert (directory / 'output/tool.prt').read_bytes() == b'Synthetic PRT bytes, not actual CAD'
    assert json.loads((directory / 'output/report.json').read_text(encoding='utf-8')) == result
    assert not result['automatic_cam_approval']
    response = client.post('/api/v1/geometry-jobs/' + job['id'] + '/report',
                          headers=HEADERS, json={'report': result})
    assert response.status_code == 200, response.text
    assert store.resources()[0]['cam_status'] == 'draft'
    assert store.active()['release_id'] is None


def test_journal_limits_match_the_server_diagnostic_contract():
    schema = Report.model_json_schema()
    assert schema['properties']['components']['maxItems'] == nxworker.MAX_REPORT_COMPONENTS
    assert schema['$defs']['Component']['properties']['id']['maxLength'] == nxworker.MAX_COMPONENT_ID_LENGTH


@pytest.mark.parametrize('count', [0, 1, 2, 3])
def test_component_count_boundary_keeps_a_valid_retained_report(fake_import, monkeypatch, count):
    directory, job, session, api, *_ = fake_import
    monkeypatch.setattr(nxworker, 'MAX_REPORT_COMPONENTS', 2)
    open_components(session, lambda part: [leaf(part, 'Synthetic ' + str(index)) for index in range(count)])
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert len(result['components']) == min(count, 2)
    assert result['status'] == ('failed' if count > 2 else 'needs_structure')
    if count > 2:
        assert any('Component count exceeds' in message for message in result['errors'])
    check_retained_report(fake_import, result, previous)


@pytest.mark.parametrize('depth', [2, 3])
def test_component_id_length_boundary_is_checked_before_appending(fake_import, monkeypatch, depth):
    directory, job, session, api, *_ = fake_import
    monkeypatch.setattr(nxworker, 'MAX_COMPONENT_ID_LENGTH', 4)
    open_components(session, lambda part: chain(part, depth))
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert len(result['components']) == 2
    assert all(len(item['id']) <= 4 for item in result['components'])
    assert result['status'] == ('failed' if depth > 2 else 'needs_structure')
    if depth > 2:
        assert any('Component ID depth exceeds' in message for message in result['errors'])
    check_retained_report(fake_import, result, previous)


def test_recursive_api_double_cannot_grow_the_diagnostic_forever(fake_import, monkeypatch):
    directory, job, session, api, *_ = fake_import
    monkeypatch.setattr(nxworker, 'MAX_COMPONENT_ID_LENGTH', 6)

    def cycle(part):
        component = leaf(part, 'Synthetic repeated node')
        component.GetChildren = lambda: [component]
        return [component]

    open_components(session, cycle)
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert result['status'] == 'failed' and len(result['components']) == 3
    assert any('Component ID depth exceeds' in message for message in result['errors'])
    check_retained_report(fake_import, result, previous)


@pytest.mark.parametrize('bound', ['count', 'depth'])
def test_overflow_does_not_inspect_the_rejected_prototype(fake_import, monkeypatch, bound):
    directory, job, session, api, *_ = fake_import
    inspected = []

    class Rejected:
        @property
        def Prototype(self):
            inspected.append(True)
            raise AssertionError('Rejected component prototype must not be inspected.')

    if bound == 'count':
        monkeypatch.setattr(nxworker, 'MAX_REPORT_COMPONENTS', 2)
        build = lambda part: [leaf(part, 'First'), leaf(part, 'Second'), Rejected()]
        expected_count, message = 2, 'Component count exceeds'
    else:
        monkeypatch.setattr(nxworker, 'MAX_COMPONENT_ID_LENGTH', 2)

        def build(part):
            parent = leaf(part, 'Parent')
            parent.GetChildren = lambda: [Rejected()]
            return [parent]

        expected_count, message = 1, 'Component ID depth exceeds'
    open_components(session, build)
    previous = Path.cwd()
    result = nxworker.run_job(directory, session, api)
    assert result['status'] == 'failed' and len(result['components']) == expected_count
    assert not inspected
    assert any(message in error for error in result['errors'])
    check_retained_report(fake_import, result, previous)
