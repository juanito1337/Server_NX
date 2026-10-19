"""Late prepared-result drift inside the final SQL transaction; fake local bytes."""
import copy
from contextlib import contextmanager

import pytest

from nxserver.results import Results
from nxserver.safety import DomainError
from nxserver.store import canonical
from test_results import result_pilot, bundle


def inject_after_last_preview(monkeypatch, store, mutate):
    preview = Results.preview; database = store.db; state = {'count': 0, 'armed': False, 'injected': False}
    def planning(self, *args, **kwargs):
        result = preview(self, *args, **kwargs)
        state['count'] += 1
        if state['count'] == 2: state['armed'] = True
        return result
    class Connection:
        def __init__(self, db): self.connection = db
        def __getattr__(self, name): return getattr(self.connection, name)
        def execute(self, sql, *args):
            result = self.connection.execute(sql, *args)
            if sql.strip().upper() == 'BEGIN IMMEDIATE' and state['armed'] and not state['injected']:
                state['injected'] = True
                assert self.connection.in_transaction
                mutate(self.connection)
            return result
    @contextmanager
    def current_db():
        with database() as db: yield Connection(db)
    monkeypatch.setattr(Results, 'preview', planning); monkeypatch.setattr(store, 'db', current_db)
    return state


@pytest.mark.parametrize('change', ['target_bytes', 'extra_target', 'archive', 'step_input', 'step_package', 'new_asset',
    'target_version', 'target_folder', 'target_failed', 'target_manifest', 'result_metadata', 'asset_metadata',
    'missing_job', 'input_metadata', 'invalid_result_json', 'invalid_manifest_json'])
def test_late_drift_cannot_commit_drafts_or_application(result_pilot, monkeypatch, tmp_path, change):
    _, store, target, tool, job = result_pilot
    service = Results(store); raw, _ = bundle(job); result = service.upload(job['id'], raw, 'fixture')
    plan = service.preview(result['id'], target['id'])[0]
    root = store.release_folder(target['id']); archive = service.archive(result['id'])
    before = copy.deepcopy(store.resources()); history = store.history(tool['id'])
    def mutate(db):
        if change == 'target_bytes':
            path = root / target['manifest'][0]['path']; path.write_bytes(path.read_bytes() + b'\n# Synthetic late bytes\n')
        elif change == 'extra_target': (root / 'synthetic-extra.txt').write_bytes(b'Late extra file')
        elif change == 'archive': archive.write_bytes(raw + b'Synthetic late archive bytes')
        elif change == 'step_input':
            path = store.root / 'assets' / job['input']['id'] / job['input']['filename']; path.write_bytes(b'Synthetic late STEP')
        elif change == 'step_package':
            path = store.root / 'geometry-packages' / job['id'] / 'NX_STEP.zip'; path.write_bytes(b'Synthetic late package')
        elif change == 'new_asset':
            row = db.execute('SELECT * FROM assets WHERE id!=? ORDER BY rowid LIMIT 1', (job['input']['id'],)).fetchone()
            (store.root / 'assets' / row['id'] / row['filename']).write_bytes(b'Synthetic late copied PRT')
        elif change == 'target_version': db.execute("UPDATE releases SET nx_version='2406' WHERE id=?", (target['id'],))
        elif change == 'target_folder':
            # Same bytes at another fixture root must not silently change the
            # reviewed release's filesystem binding.
            import shutil
            other = tmp_path / 'other-target'; shutil.copytree(root, other)
            db.execute('UPDATE releases SET folder=? WHERE id=?', (str(other), target['id']))
        elif change == 'target_failed': db.execute("UPDATE releases SET status='failed' WHERE id=?", (target['id'],))
        elif change == 'target_manifest': db.execute("UPDATE releases SET manifest='[]' WHERE id=?", (target['id'],))
        elif change == 'result_metadata':
            changed = copy.deepcopy(result); changed['manifest']['objects'][0]['cam']['records'][0]['values']['DESCR'] = 'Unreviewed'
            db.execute('UPDATE geometry_results SET payload=? WHERE id=?', (canonical(changed), result['id']))
        elif change == 'asset_metadata': db.execute("UPDATE assets SET filename='different.prt' WHERE id!=?", (job['input']['id'],))
        elif change == 'missing_job': db.execute('DELETE FROM geometry_jobs WHERE id=?', (job['id'],))
        elif change == 'input_metadata': db.execute("UPDATE assets SET filename='different.step' WHERE id=?", (job['input']['id'],))
        elif change == 'invalid_result_json': db.execute("UPDATE geometry_results SET payload='{' WHERE id=?", (result['id'],))
        else: db.execute("UPDATE releases SET manifest='{' WHERE id=?", (target['id'],))
    state = inject_after_last_preview(monkeypatch, store, mutate)
    with pytest.raises(DomainError) as error: service.apply(result['id'], target['id'], plan['preview_hash'], 'fixture')
    assert error.value.status == 409 and state['injected']
    assert store.resources() == before and store.history(tool['id']) == history
    assert service.get(result['id'])['applied'] is None and store.active()['release_id'] is None
    assert not any(entry['action'] == 'geometry.result.applied' for entry in store.audit_entries())
    with store.db() as db: assert db.execute('SELECT COUNT(*) FROM assets').fetchone()[0] == 3


def test_successful_final_replan_is_read_only_and_uses_same_sql_snapshot(result_pilot, monkeypatch):
    from nxserver.store import Store
    _, store, target, tool, job = result_pilot
    service = Results(store); raw, _ = bundle(job); result = service.upload(job['id'], raw, 'fixture')
    plan = service.preview(result['id'], target['id'])[0]; verify = service._verify_apply_commit; checked = []
    def gate(db, *args):
        assert db.in_transaction
        original = store.db
        def forbidden(): pytest.fail('Final replan may not open another metadata connection.')
        store.db = forbidden
        try: verify(db, *args); checked.append(True)
        finally: store.db = original
    monkeypatch.setattr(service, '_verify_apply_commit', gate)
    monkeypatch.setattr(Store, '__init__', lambda *args, **kwargs: pytest.fail('No Store constructor inside final commit.'))
    applied = service.apply(result['id'], target['id'], plan['preview_hash'], 'fixture')
    assert checked == [True] and len(applied['resources']) == 2
    assert next(item for item in store.resources() if item['id'] == tool['id'])['revision'] == 2
    # Historical replay is not a new qualification and does not enter the gate.
    monkeypatch.setattr(service, '_verify_apply_commit', lambda *args: pytest.fail('Historical apply replay must not replan.'))
    assert service.apply(result['id'], target['id'], plan['preview_hash'], 'fixture') == applied


@pytest.mark.parametrize('change', ['archive', 'target', 'attachment', 'read_failure'])
def test_failed_background_commit_keeps_intents_and_replays_without_another_writer(result_pilot, monkeypatch, change):
    import uuid
    from pathlib import Path
    from nxserver.upgrades import LIVE
    from test_results import HEADERS
    client, store, target, tool, job = result_pilot
    service = Results(store); raw, _ = bundle(job); result = service.upload(job['id'], raw, 'fixture')
    plan = service.preview(result['id'], target['id'])[0]; archive = service.archive(result['id'])
    def mutate(db):
        if change == 'archive': archive.write_bytes(raw + b'Synthetic late bytes')
        elif change == 'target': (store.release_folder(target['id']) / 'late.txt').write_bytes(b'Synthetic late file')
        elif change == 'attachment':
            row = db.execute('SELECT * FROM assets WHERE id!=? LIMIT 1', (job['input']['id'],)).fetchone()
            (store.root / 'assets' / row['id'] / row['filename']).write_bytes(b'Late model bytes')
        else:
            original = Path.open
            def fail(path, *args, **kwargs):
                if path == archive: raise PermissionError('Synthetic final archive read failure')
                return original(path, *args, **kwargs)
            monkeypatch.setattr(Path, 'open', fail)
    state = inject_after_last_preview(monkeypatch, store, mutate)
    headers = {**HEADERS, 'Idempotency-Key': str(uuid.uuid4())}
    request = {'result_id': result['id'], 'target_release_id': target['id'], 'preview_hash': plan['preview_hash']}
    accepted = client.post('/api/v1/release-jobs/result_adopt', headers=headers, json=request)
    assert accepted.status_code == 202, accepted.text
    LIVE[accepted.json()['id']].result(timeout=30)
    done = client.get('/api/v1/migrations/' + accepted.json()['id'], headers=HEADERS).json()
    assert state['injected'] and done['status'] == 'failed', done
    assert 'no drafts/application committed' in done['message'] and not done.get('domain_result')
    assert len(done['asset_allocations']) == 2 and all(item['state'] == 'stored' for item in done['asset_allocations'])
    assert store.resources() == [tool] and Results(store).get(result['id'])['applied'] is None
    before = store.audit_entries()
    monkeypatch.setattr(client.app.state.upgrades.pool, 'submit', lambda *args: pytest.fail('Old receipt must not submit another writer.'))
    replay = client.post('/api/v1/release-jobs/result_adopt', headers=headers, json=request)
    assert replay.status_code == 202 and replay.json() == accepted.json() and store.audit_entries() == before
