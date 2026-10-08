"""Git ignore policy, exercised only in a fresh synthetic temporary repository."""
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[2]
GIT = shutil.which('git')
pytestmark = pytest.mark.skipif(GIT is None, reason='Git CLI not installed.')


def test_sensitive_and_licensed_inputs_are_excluded_but_sources_remain_visible(tmp_path):
    shutil.copyfile(PROJECT / '.gitignore', tmp_path / '.gitignore')
    subprocess.run([GIT, '-c', 'init.templateDir=', 'init', '--quiet', str(tmp_path)],
                   check=True, capture_output=True, text=True)
    excluded = [
        'nx2406/MACH/resource/customer.dat',
        'installer_extracted/ANALYSE.md',
        'migration_reports/report.json',
        'Setup_NX2512_V1.14.exe',
        'NX CAM 3D Tool Assistant.zip',
        'models/customer.prt',
        'models/customer.step',
        'libraries/tool_database.dat',
        'libraries/tool_database.dat_bak',
        'templates/company.pax',
        'roles/company.mtx',
        'defaults/company.dpv',
        'libraries/customer.xlsx',
        'docker/secrets/admin_token.txt',
        'secrets/erp_token.txt',
        'server/settings.json',
        'server/settings.production.json',
        'client/settings.local.json',
        'server/.venv/Scripts/python.exe',
        'server/data/management.sqlite3',
        'data/management.sqlite3-wal',
        'data/management.sqlite3-shm',
        'runtime/test.syslog',
        '.env',
        '.env.production',
        'certificates/private.key',
        'client/bin/Release/client.dll',
    ]
    included = [
        '.gitignore', '.gitattributes', 'README_NX_Migration.md',
        'NXMigrationSafety.psm1', 'compose.yaml',
        'server/settings.example.json', 'docker/settings.json',
        'docker/settings.windows.json', '.env.example',
        'server/nxserver/api.py', 'server/nxserver/web/app.js',
        'server/tests/test_repository.py', 'client/src/Launcher.cs',
    ]
    # NUL-delimited bytes avoid Windows newline translation and Git path
    # quoting, including archive names containing spaces.
    result = subprocess.run([GIT, 'check-ignore', '--no-index', '-z', '--stdin'],
                            input=('\0'.join(excluded + included) + '\0').encode(),
                            cwd=tmp_path, check=True, capture_output=True)
    assert set(result.stdout.decode().rstrip('\0').split('\0')) == set(excluded)
