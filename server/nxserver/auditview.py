"""Bounded, metadata-first audit navigation. Never repairs or rewrites history."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager

from .safety import DomainError, offline_path

MAX_INTEGER = 2**53 - 1
MAX_PAYLOAD = 8 * 1024 * 1024
HEX = re.compile(r'^[a-f0-9]{64}$')
FLAGS = {'sqlite_read_only': True, 'sqlite_read_transaction': True,
         'audit_chain_verified': False, 'files_verified': False, 'signed': False}


@contextmanager
def _db(store):
    path = offline_path(store.root / 'management.sqlite3')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise DomainError('Invalid bounded audit position.')


def _hash(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise DomainError('Invalid audit hash.')


def _head(db, sequence, digest):
    if (sequence is None) != (digest is None):
        raise DomainError('Supply both audit head sequence and hash.')
    if sequence is not None:
        _integer(sequence)
        _hash(digest)
    row = db.execute('SELECT sequence,hash FROM audit ORDER BY sequence DESC LIMIT 1').fetchone()
    head = dict(row) if row else {'sequence': 0, 'hash': '0' * 64}
    _integer(head['sequence'])
    _hash(head['hash'])
    if sequence is not None and head != {'sequence': sequence, 'hash': digest}:
        raise DomainError('Audit head changed; reload the audit overview before continuing.', 409)
    return head


def _metadata(row):
    value = dict(row)
    _integer(value['sequence'], 1)
    _integer(value['payload_bytes'])
    for field in ('actor', 'action', 'created_at'):
        if not isinstance(value[field], str) or not value[field]:
            raise DomainError('Invalid stored audit metadata; original history retained.', 409)
    _hash(value['hash'])
    _hash(value['previous_hash'])
    return value


def summary(store, offset=0, limit=20, query='', sequence=None, digest=None):
    _integer(offset)
    _integer(limit, 1)
    if limit > 50 or not isinstance(query, str) or len(query) > 200:
        raise DomainError('Invalid bounded audit query.')
    if offset and sequence is None:
        raise DomainError('Older audit pages require the selected audit head.')
    where = "instr(actor,?)>0 OR instr(action,?)>0 OR instr(created_at,?)>0 OR instr(CAST(sequence AS TEXT),?)>0"
    params = (query,) * 4
    with _db(store) as db:
        head = _head(db, sequence, digest)
        total = db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
        filtered = db.execute('SELECT COUNT(*) FROM audit WHERE ' + where, params).fetchone()[0]
        rows = db.execute('SELECT sequence,actor,action,created_at,previous_hash,hash,'
                          'length(CAST(payload AS BLOB)) AS payload_bytes FROM audit WHERE ' + where +
                          ' ORDER BY sequence DESC LIMIT ? OFFSET ?', (*params, limit, offset))
        items = [_metadata(row) for row in rows]
    return {'contract_version': 1, 'head': head, 'total': total, 'filtered': filtered,
            'offset': offset, 'limit': limit, 'query': query, 'items': items, **FLAGS}


def entry(store, position, expected_hash, sequence, digest):
    _integer(position, 1)
    _hash(expected_hash)
    if sequence is None or digest is None:
        raise DomainError('Audit details require the selected audit head.')
    with _db(store) as db:
        head = _head(db, sequence, digest)
        row = db.execute('SELECT sequence,actor,action,created_at,previous_hash,hash,'
                         'length(CAST(payload AS BLOB)) AS payload_bytes FROM audit WHERE sequence=?',
                         (position,)).fetchone()
        if row is None:
            raise DomainError('Audit entry not found.', 404)
        item = _metadata(row)
        if item['hash'] != expected_hash:
            raise DomainError('Selected audit entry changed; reload its overview.', 409)
        if item['payload_bytes'] > MAX_PAYLOAD:
            raise DomainError('Audit payload exceeds the bounded detail reader; original retained.', 409)
        raw = db.execute('SELECT payload FROM audit WHERE sequence=?', (position,)).fetchone()[0]
        if not isinstance(raw, str):
            raise DomainError('Invalid stored audit payload; original retained.', 409)
        text = json.dumps([item['previous_hash'], item['actor'], item['action'], raw, item['created_at']],
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != expected_hash:
            raise DomainError('Audit entry bytes differ from their stored hash; original retained.', 409)
    return {'contract_version': 1, 'head': head, 'entry': item, 'payload': raw,
            'entry_hash_verified': True, **FLAGS}
