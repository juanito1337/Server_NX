"""Import a local test folder and retain its validation evidence; never activate it."""
import argparse
import json

from nxserver.store import Store


parser = argparse.ArgumentParser()
parser.add_argument('--data-dir', required=True)
parser.add_argument('--source', required=True)
parser.add_argument('--syslog', required=True)
options = parser.parse_args()
store = Store(options.data_dir, {'nx2512-managed': options.source}, {'maschinentest-20260923': options.syslog})
matching = [release for release in store.releases()
            if release['source'] == str(store.sources['nx2512-managed']) and release['status'] not in ('building', 'failed')]
release = store.get_release(matching[0]['id']) if matching else store.import_release('nx2512-managed', '2512', 'NX2512 gepruefter Migrationsbuild', 'local-verification')
validation = store.validate_release(release['id'], 'local-verification')
report = store.scan_log('maschinentest-20260923')
print(json.dumps({'release_id': release['id'], 'folder': release['folder'], 'validation': validation,
                  'nx_loading_error_count': len(report['blocking_errors']), 'active': store.active()}, indent=2))
