"""Run isolated core/browser/container suites with reproducible shard evidence.

No live settings, credentials, Siemens installations or deployment are needed.
The container suite is an explicit opt-in; never part of the default suite.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
import sys
import time
import uuid
import xml.etree.ElementTree as ET

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from nxserver.safety import DomainError, offline_path, tree_files


def source_manifest():
    files={}
    for folder in ('nxserver','tests','tools'):
        for relative,path in tree_files(SERVER/folder):
            if set(path.parts)&{'__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'} or path.suffix=='.pyc':continue
            files[folder+'/'+relative]=hashlib.sha256(path.read_bytes()).hexdigest()
    for path in [*SERVER.glob('requirements*.txt'),SERVER.parent/'.github/workflows/tests.yml']:
        path=offline_path(path)
        if path.is_file():files[str(path.relative_to(SERVER.parent)).replace('\\','/')]=hashlib.sha256(path.read_bytes()).hexdigest()
    # Read this checkout's ordinary metadata only, never follow a .git worktree
    # pointer or inherit GIT_DIR/global config that could reference another drive.
    commit=os.environ.get('GITHUB_SHA')
    metadata=offline_path(SERVER.parent/'.git')
    if metadata.is_dir():
        head=offline_path(metadata/'HEAD').read_text(encoding='ascii').strip()
        if re.fullmatch('[a-f0-9]{40}',head):commit=head
        elif head.startswith('ref: '):
            ref=head[5:]
            if '..' in ref.split('/') or not re.fullmatch(r'refs/heads/[A-Za-z0-9_./-]+',ref):
                raise DomainError('Unsupported checkout HEAD reference.')
            loose=offline_path(metadata/ref)
            if loose.is_file():commit=loose.read_text(encoding='ascii').strip()
            else:
                packed=offline_path(metadata/'packed-refs')
                if packed.is_file():
                    for line in packed.read_text(encoding='ascii').splitlines():
                        if line.endswith(' '+ref):commit=line.split(' ',1)[0]
    if commit is not None and not re.fullmatch('[a-f0-9]{40}',commit):raise DomainError('Invalid source commit metadata.')
    return {'sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),'files':files,'commit':commit}


def suite_for(nodeid):
    filename = nodeid.split('::', 1)[0].replace('\\', '/').rsplit('/', 1)[-1]
    return 'browser' if filename.endswith('_browser.py') else 'core'


def select_ids(nodeids, suite, shard_index, shard_count):
    if type(shard_count) is not int or not 1 <= shard_count <= 32:
        raise ValueError('Shard count must be 1-32.')
    if type(shard_index) is not int or not 0 <= shard_index < shard_count:
        raise ValueError('Shard index must be zero-based and below shard count.')
    if suite not in ('core', 'browser', 'container'):
        raise ValueError('Unknown test suite.')
    if len(nodeids) != len(set(nodeids)):
        raise ValueError('Duplicate collected test IDs.')
    eligible = sorted(value for value in nodeids if
        (value.split('::', 1)[0].replace('\\', '/').rsplit('/', 1)[-1] == 'test_docker_integration.py'
         if suite == 'container' else suite_for(value) == suite))
    return eligible[shard_index::shard_count]


def test_environment(environment, suite):
    # Never inherit local NX opt-ins, credentials, pytest selection or plugins.
    result = {key: value for key, value in environment.items()
              if not key.upper().startswith(('NX_', 'UGII_', 'UGCAM_', 'GIT_', 'NODE_', 'PSMODULE')) and key not in
              ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH')}
    result.update(PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
                  PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', GIT_CONFIG_NOSYSTEM='1',
                  GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM='1')
    if suite == 'browser':
        result['NX_BROWSER_TEST'] = '1'
    if suite == 'container':
        result['NX_DOCKER_TEST'] = '1'
    return result


def id_hash(values):
    return hashlib.sha256('\n'.join(sorted(values)).encode('utf-8')).hexdigest()


def powershell_settings(directory, environment):
    """Own the module cache; never inspect a caller's cache or module paths.

    Windows PowerShell constructs its default module path when PSModulePath is
    absent. Its analysis cache can contain paths from the hosted image/user.
    https://learn.microsoft.com/powershell/module/microsoft.powershell.core/about/about_environment_variables
    """
    root_text=environment.get('SYSTEMROOT') or environment.get('SystemRoot') or ''
    if not root_text or not os.path.isabs(root_text):raise DomainError('An absolute local Windows system root is required.')
    root=offline_path(root_text)
    home=offline_path(root/'System32/WindowsPowerShell/v1.0')
    executable=offline_path(home/'powershell.exe');modules=offline_path(home/'Modules')
    if not executable.is_file() or not modules.is_dir():raise DomainError('Windows PowerShell 5.1 runtime is required.')
    directory=offline_path(directory)
    updates={'PSModulePath':str(modules),'PSModuleAnalysisCachePath':str(offline_path(directory/'powershell-module.cache')),
             'PSDisableModuleAnalysisCacheCleanup':'1'}
    # Temp/profile values are used by PowerShell and the C# compiler, so check
    # these inputs before starting either process, including their drive type.
    for key in ('TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','ProgramFiles'):
        value=environment.get(key) or environment.get(key.upper())
        if value:offline_path(value)
    return executable,updates


def prepare_powershell(directory):
    from tools.nxlab.processes import OwnedJob
    executable,updates=powershell_settings(directory,os.environ)
    environment={key:value for key,value in os.environ.items()
        if not key.upper().startswith('PSMODULE') and key.upper()!='PSDISABLEMODULEANALYSISCACHECLEANUP'}
    environment.update(updates)
    script=offline_path(directory/'powershell-preflight.ps1')
    script.write_text("""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
Add-Type -TypeDefinition 'public static class NXCICompilerProbe { public static int Result() { return 1; } }'
$result=[pscustomobject]@{version=$PSVersionTable.PSVersion.ToString();compiler=([NXCICompilerProbe]::Result() -eq 1);module_paths=@($env:PSModulePath -split ';');module_cache=$env:PSModuleAnalysisCachePath}
$result | ConvertTo-Json -Compress
""",encoding='utf-8')
    log=offline_path(directory/'powershell-preflight.log');started=time.monotonic()
    try:
        with log.open('xb') as stream, OwnedJob() as owned:
            process=owned.start([str(executable),'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)],
                cwd=directory,env=environment,stdout=stream,stderr=subprocess.STDOUT)
            try:code=process.wait(timeout=60)
            except subprocess.TimeoutExpired as error:raise DomainError('Windows PowerShell/compiler preflight timed out; inspect its local log.') from error
            if owned.active():raise DomainError('PowerShell preflight left an owned descendant running.')
        if code:raise DomainError('Windows PowerShell/compiler preflight failed; inspect its local log.')
        with log.open('rb') as stream:raw=stream.read(64*1024+1)
        if len(raw)>64*1024:raise DomainError('PowerShell preflight output exceeds its bounded receipt.')
        value=json.loads(raw.decode('utf-8-sig'))
        if (not isinstance(value,dict) or not isinstance(value.get('version'),str) or
            not value['version'].startswith('5.1.') or value.get('compiler') is not True or
            not isinstance(value.get('module_paths'),list) or not value['module_paths']):
            raise DomainError('Windows PowerShell 5.1/C# compiler receipt differs.')
        if value['module_cache']!=updates['PSModuleAnalysisCachePath']:raise DomainError('PowerShell module cache is not owned by this run.')
        paths=[str(offline_path(path)) for path in value['module_paths']]
        return {'contract_version':1,'scope':'windows_ci_runtime_preflight','path':str(executable),
                'version':value['version'],'module_paths':paths,'module_cache':value['module_cache'],
                'compiler_verified':True,'seconds':round(time.monotonic()-started,3),
                'script_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),
                'receipt_sha256':hashlib.sha256(raw).hexdigest()}
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise DomainError('Windows PowerShell preflight receipt is missing or malformed; inspect its local log.') from error


def required_runtime(name, requested=None):
    """Find only in the previously guarded PATH; never use the working directory."""
    candidates = [requested] if requested else [str(Path(folder) / (name + ('.exe' if os.name == 'nt' else '')))
        for folder in os.environ.get('PATH', '').split(os.pathsep) if folder]
    executable = None
    for candidate in candidates:
        if not os.path.isabs(candidate):
            raise DomainError('Runtime executable requires an absolute local path.')
        path = offline_path(candidate)  # Lexical rejection precedes every lookup.
        if path.is_file():
            executable = path
            break
    if executable is None:
        raise DomainError(name + ' is required for the full core suite; specify --' + name + '-executable.')
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    try:
        result = subprocess.run([str(executable), '--version'], env=environment,
            capture_output=True, text=True, encoding='utf-8', timeout=15, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise DomainError(name + ' runtime preflight failed.') from error
    version = result.stdout.strip()
    pattern = r'v24\.\d+\.\d+' if name == 'node' else r'git version [0-9]+\.[0-9]+(?:\.[A-Za-z0-9.-]+)?'
    if not re.fullmatch(pattern, version):
        raise DomainError('Unsupported ' + name + ' runtime; Node.js 24 and an ordinary Git CLI are required.')
    # The existing tests discover runtimes with shutil.which(). Change only this
    # runner process and its children, never the user's Windows PATH setting.
    os.environ['PATH'] = str(executable.parent) + os.pathsep + os.environ.get('PATH', '')
    return {'path': str(executable), 'version': version}


def write_json(path, value):
    with offline_path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)


class Evidence:
    def __init__(self, directory, suite, index, count, source=None):
        self.directory, self.suite = directory, suite
        self.index, self.count = index, count
        self.plan, self.outcomes, self.errors = None, {}, []
        self.collection_errors = []
        self.infrastructure_errors = []
        self.runtimes = {}
        self.source=source
        # Tests intentionally replace Path.open/lstat to prove that protected
        # paths are never touched. Keep evidence on a prevalidated owned handle,
        # independent of application filesystem doubles active during the call.
        self.events=offline_path(directory/'events.jsonl').open('x',encoding='utf-8')
        self.encode=json.dumps
        self.clock=time.time_ns

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors.append({'id': report.nodeid, 'reason': str(report.longrepr)[:2000]})

    def pytest_collection_modifyitems(self, session, config, items):
        import pytest
        if getattr(session, 'testsfailed', 0):
            # Preserve pytest's collection error (2), not a misleading empty-
            # shard usage error (4), and do not certify a partial collection.
            return
        all_ids = [item.nodeid for item in items]
        selected = select_ids(all_ids, self.suite, self.index, self.count)
        chosen = set(selected)
        if not selected:
            raise pytest.UsageError('No tests selected for this suite/shard; refusing an empty green job.')
        self.plan = {'contract_version': 1, 'suite': self.suite, 'shard_index': self.index,
                     'shard_count': self.count, 'collected': len(all_ids),
                     'collection_sha256': id_hash(all_ids), 'selected': len(selected),
                     'selected_sha256': id_hash(selected), 'ids': selected,
                     'github_sha': os.environ.get('GITHUB_SHA'),
                     'licensed_inputs_enabled': False, 'automatic_deployment': False}
        self.plan.update(python_version=sys.version,platform=sys.platform,source=self.source,runtimes=self.runtimes)
        write_json(self.directory / 'plan.json', self.plan)
        deselected = [item for item in items if item.nodeid not in chosen]
        items[:] = [item for item in items if item.nodeid in chosen]
        config.hook.pytest_deselected(items=deselected)

    def pytest_runtest_logreport(self, report):
        # Keep progress if interrupted before pytest publishes final JUnit.
        event={'id':report.nodeid,'phase':report.when,'outcome':report.outcome,
               'duration':getattr(report,'duration',0),'time_ns':self.clock()}
        if report.failed or report.skipped:event['reason']=str(report.longrepr)[:2000]
        self.events.write(self.encode(event,ensure_ascii=False)+'\n')
        self.events.flush()
        if report.failed:
            self.outcomes[report.nodeid] = 'failed' if report.when == 'call' else 'error'
        elif report.skipped and self.outcomes.get(report.nodeid) not in ('failed', 'error'):
            self.outcomes[report.nodeid] = 'skipped'
        elif report.when == 'call' and report.passed:
            self.outcomes[report.nodeid] = 'passed'
        if report.failed or report.skipped:
            self.errors.append({'id': report.nodeid, 'phase': report.when,
                                'outcome': report.outcome, 'reason': str(report.longrepr)[:2000]})

    def finish(self, exit_code, collected_only=False):
        self.events.close()
        try:stable=source_manifest()==self.source if self.source is not None else None
        except (OSError,DomainError):stable=False
        expected = set(self.plan['ids']) if self.plan else set()
        actual = set(self.outcomes)
        complete = bool(expected) and expected == actual and not collected_only
        counts = {name: list(self.outcomes.values()).count(name)
                  for name in ('passed', 'failed', 'error', 'skipped')}
        valid = bool(self.plan) and (collected_only or complete)
        result = int(exit_code) if exit_code else (0 if valid else 3)
        if not result and (counts['failed'] or counts['error']):
            result = 1
        if stable is False:result=result or 3
        summary = {'contract_version': 1, 'suite': self.suite, 'shard_index': self.index,
                   'shard_count': self.count, 'exit_code': result, 'counts': counts,
                   'collected_only': collected_only, 'execution_complete': complete,
                   'missing': sorted(expected - actual), 'unexpected': sorted(actual - expected),
                   'skips_and_failures': self.errors, 'collection_errors': self.collection_errors,
                   'infrastructure_errors': self.infrastructure_errors, 'runtimes': self.runtimes,
                   'automatic_deployment': False}
        summary.update(source_stable=stable,source_sha256=self.source['sha256'] if self.source else None)
        write_json(self.directory / 'summary.json', summary)
        print(json.dumps({key: value for key, value in summary.items()
                          if key not in ('missing', 'unexpected', 'skips_and_failures', 'collection_errors')}))
        return result

    def infrastructure_failure(self, message):
        self.infrastructure_errors.append(message)
        suite = ET.Element('testsuite', name='runtime preflight', tests='1', errors='1', failures='0')
        case = ET.SubElement(suite, 'testcase', name='required-runtime')
        ET.SubElement(case, 'error', type='infrastructure', message=message)
        ET.ElementTree(suite).write(self.directory / 'junit.xml', encoding='utf-8', xml_declaration=True)
        return self.finish(2)


def main(arguments=None, *, test_root=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('core', 'browser', 'container'), default='core')
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--report-dir', default=str(SERVER / 'test-results'))
    parser.add_argument('--collect-only', action='store_true')
    parser.add_argument('--node-executable', help='Absolute local Node.js 24 executable, for the core suite.')
    parser.add_argument('--git-executable', help='Absolute local Git executable, for the core suite.')
    options = parser.parse_args(arguments)
    try:
        select_ids([], options.suite, options.shard_index, options.shard_count)
    except ValueError as error:
        parser.error(str(error))
    environment = test_environment(os.environ, options.suite)
    os.environ.clear()
    os.environ.update(environment)
    # Sanitise PATH before any test child can inherit it, using the application's
    # existing local-path guard. Never look up rejected W:/UNC entries.
    local = []
    for candidate in environment.get('PATH', '').split(os.pathsep):
        try:
            if candidate and os.path.isabs(candidate):
                path = offline_path(candidate)
                if path.is_dir():
                    local.append(str(path))
        except Exception:
            continue
    os.environ['PATH'] = os.pathsep.join(local)
    directory = offline_path(Path(options.report_dir))
    directory.mkdir(parents=True, exist_ok=True)
    directory = offline_path(directory / (options.suite + '-' + str(options.shard_index) + '-' + uuid.uuid4().hex))
    directory.mkdir(exist_ok=False)
    print('Test evidence: ' + str(directory), flush=True)
    evidence = Evidence(directory, options.suite, options.shard_index, options.shard_count, source_manifest())
    try:
        if not options.collect_only:
            if os.name=='nt' and test_root is None:
                os.environ.update(powershell_settings(directory,os.environ)[1])
                evidence.runtimes['powershell']=prepare_powershell(directory)
            if options.suite == 'core' and test_root is None:
                evidence.runtimes.update({name: required_runtime(name, getattr(options, name + '_executable'))
                                         for name in ('node', 'git')})
            if options.suite == 'browser':
                # Missing Playwright/Edge is an infrastructure error, not hundreds of skips.
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel='msedge', headless=True)
                    browser.close()
    except Exception as error:
        return evidence.infrastructure_failure(str(error))
    import pytest
    root = offline_path(test_root or SERVER / 'tests')
    args = [str(root), '--rootdir=' + str(root.parent), '-q', '--tb=short',
            '--junitxml=' + str(directory / 'junit.xml')]
    if options.collect_only:
        args.append('--collect-only')
    os.chdir(SERVER)
    status = pytest.main(args, plugins=[evidence])
    return evidence.finish(status, options.collect_only)


if __name__ == '__main__':
    sys.exit(main())
