"""CI orchestration contracts, tested using inert disposable pytest projects."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / 'tools/run_ci_tests.py'
spec = importlib.util.spec_from_file_location('nx_ci_runner', SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_core_and_browser_shards_partition_every_id_once():
    ids = ['tests/test_alpha.py::test_a', 'tests/test_alpha.py::TestGroup::test_b[x]',
           'tests/test_browser.py::test_ui', 'tests/test_more_browser.py::test_ui[a]',
           'tests/test_more_browser.py::test_ui[b]', 'tests/test_docker_integration.py::test_container']
    partitions = [ci.select_ids(ids, suite, index, 2)
                  for suite in ('core', 'browser') for index in range(2)]
    flat = [value for group in partitions for value in group]
    assert len(flat) == len(set(flat)) == len(ids) and set(flat) == set(ids)
    assert partitions == [ci.select_ids(list(reversed(ids)), suite, index, 2)
                          for suite in ('core', 'browser') for index in range(2)]
    assert ci.suite_for(r'tests\test_browser.py::test_ui') == 'browser'
    assert ci.select_ids(ids, 'container', 0, 1) == [ids[-1]]


@pytest.mark.parametrize('index,count', [(0, 0), (0, 33), (-1, 2), (2, 2), (True, 2), (0, True)])
def test_invalid_shards_fail_instead_of_selecting_an_unintended_subset(index, count):
    with pytest.raises(ValueError):
        ci.select_ids([], 'core', index, count)


def test_duplicate_ids_and_unknown_suite_are_rejected():
    with pytest.raises(ValueError):
        ci.select_ids(['test_a.py::test_a'] * 2, 'core', 0, 1)
    with pytest.raises(ValueError):
        ci.select_ids([], 'unknown', 0, 1)


@pytest.mark.parametrize('suite', ['core', 'browser', 'container'])
def test_environment_never_inherits_local_keys_or_licensed_optins(suite):
    source = {'PATH': 'local-placeholder', 'NX_ADMIN_TOKEN': 'synthetic-not-a-live-key',
              'NX_ERP_TOKEN_FILE': 'unused-private-placeholder', 'NX_STOCK_SCHEMA_TEST': '1',
              'NX_EXTRACTOR_TEST': '1', 'NX_COMPOSE_BIN': 'unused', 'NX_DOCKER_TEST': '1',
              'NX_BROWSER_TEST': '1', 'PYTEST_ADDOPTS': '-k hidden_subset',
              'PYTEST_PLUGINS': 'untrusted', 'PYTHONPATH': 'untrusted', 'KEEP_ME': 'yes'}
    result = ci.test_environment(source, suite)
    assert result['KEEP_ME'] == 'yes' and source['NX_ADMIN_TOKEN'] == 'synthetic-not-a-live-key'
    expected = {'NX_BROWSER_TEST': '1'} if suite == 'browser' else {'NX_DOCKER_TEST': '1'} if suite == 'container' else {}
    assert {key: value for key, value in result.items() if key.startswith('NX_')} == expected
    assert not {'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH'} & result.keys()
    assert result['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'


def prepared(tmp_path, ids=('tests/test_one.py::test_one',)):
    evidence = ci.Evidence(tmp_path, 'core', 0, 1)
    items = [SimpleNamespace(nodeid=value) for value in ids]
    calls = []
    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=lambda **values: calls.append(values)))
    evidence.pytest_collection_modifyitems(None, config, items)
    return evidence


def test_empty_selection_is_an_error_before_any_green_plan(tmp_path):
    evidence = ci.Evidence(tmp_path, 'browser', 0, 1)
    with pytest.raises(pytest.UsageError, match='empty green'):
        evidence.pytest_collection_modifyitems(None, None, [SimpleNamespace(nodeid='test_core.py::test_a')])
    assert not (tmp_path / 'plan.json').exists()


def test_missing_execution_cannot_be_success_even_with_zero_pytest_exit(tmp_path):
    evidence = prepared(tmp_path)
    assert evidence.finish(0) == 3
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert not summary['execution_complete'] and len(summary['missing']) == 1


def test_teardown_failure_overrides_a_passing_call(tmp_path):
    evidence = prepared(tmp_path)
    for phase, failed in [('call', False), ('teardown', True)]:
        evidence.pytest_runtest_logreport(SimpleNamespace(nodeid='tests/test_one.py::test_one',
            when=phase, failed=failed, skipped=False, passed=not failed,
            outcome='failed' if failed else 'passed', longrepr='Synthetic teardown error'))
    assert evidence.finish(0) == 1
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert summary['execution_complete'] and summary['counts']['error'] == 1


@pytest.mark.parametrize('code', [1, 2, 3, 4, 5])
def test_original_pytest_failure_codes_are_not_hidden(tmp_path, code):
    assert prepared(tmp_path).finish(code) == code


@pytest.mark.parametrize('mode', ['pass', 'fail', 'skip', 'collection_error', 'collect_only'])
def test_real_runner_records_pytest_outcome_without_loading_real_project_tests(tmp_path, mode):
    fixture = tmp_path / 'fixture';fixture.mkdir()
    if mode == 'collection_error':
        text = 'def invalid syntax\n'
    elif mode == 'skip':
        text = 'import pytest\n\ndef test_fixture():\n    pytest.skip("Synthetic platform skip")\n'
    elif mode == 'fail':
        text = 'def test_fixture():\n    assert False, "Synthetic expected failure"\n'
    else:
        text = 'import os\n\ndef test_fixture():\n    assert "NX_ADMIN_TOKEN" not in os.environ\n    assert "NX_EXTRACTOR_TEST" not in os.environ\n'
    (fixture / 'test_fixture.py').write_text(text, encoding='utf-8')
    output = tmp_path / 'reports'
    program = ('import pathlib,runpy,sys; script=sys.argv[1];root=pathlib.Path(sys.argv[2]);'
               'args=sys.argv[3:];module=runpy.run_path(script);sys.exit(module["main"](args,test_root=root))')
    environment = dict(os.environ, NX_ADMIN_TOKEN='synthetic-ci-test-only', NX_EXTRACTOR_TEST='1')
    command = [sys.executable, '-E', '-s', '-c', program, str(SCRIPT), str(fixture),
               '--suite', 'core', '--report-dir', str(output)]
    if mode == 'collect_only':
        command.append('--collect-only')
    result = subprocess.run(command, cwd=SCRIPT.parents[1], env=environment,
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == (1 if mode == 'fail' else 2 if mode == 'collection_error' else 0), result.stdout + result.stderr
    reports = list(output.glob('core-*'));assert len(reports) == 1
    summary = json.loads((reports[0] / 'summary.json').read_text())
    assert summary['exit_code'] == result.returncode
    assert (reports[0] / 'junit.xml').is_file()
    assert summary['execution_complete'] is (mode not in ('collect_only', 'collection_error'))
    if mode == 'collection_error':
        assert summary['collection_errors'] and not (reports[0] / 'plan.json').exists()
    if mode not in ('collect_only', 'collection_error'):
        assert summary['counts'][{'pass': 'passed', 'fail': 'failed', 'skip': 'skipped'}[mode]] == 1


def test_workflow_uses_pinned_actions_and_has_no_deployment_or_error_bypass():
    workflow = (SCRIPT.parents[2] / '.github/workflows/tests.yml').read_text(encoding='utf-8')
    actions = re.findall(r'uses:\s+([^\s]+)', workflow)
    assert actions and all(re.fullmatch(r'actions/[a-z-]+@[a-f0-9]{40}', action) for action in actions)
    assert 'contents: read' in workflow and 'persist-credentials: false' in workflow
    for unsafe in ('pull_request_target:', 'self-hosted', 'continue-on-error:', 'secrets.', 'git push', 'docker push'):
        assert unsafe not in workflow
    assert 'fail-fast: false' in workflow and 'if: always()' in workflow
    assert '--suite core --shard-count 2' in workflow
    assert '--suite browser --shard-count 4' in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.container_test" in workflow
    assert "value != 'success'" in workflow
