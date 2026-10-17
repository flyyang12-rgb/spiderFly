"""Durable creation receipts, committed with the object; no runtime data copies."""
from __future__ import annotations

import hashlib
import json
import re

from fastapi import HTTPException

from ..database import utc_now


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS creation_requests (
        actor_id INTEGER NOT NULL, kind TEXT NOT NULL, request_id TEXT NOT NULL,
        intent_hash TEXT NOT NULL, object_id INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(actor_id,kind,request_id))""")


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def validate_key(key):
    if key is not None and (not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', key)):
        raise HTTPException(400, '创建请求编号无效')
    return key


def find(conn, actor, kind, key, intent_hash=None):
    validate_key(key)
    if key is None:
        return None
    row = conn.execute('SELECT * FROM creation_requests WHERE actor_id=? AND kind=? AND request_id=?',
                       (actor, kind, key)).fetchone()
    if row and intent_hash is not None and row['intent_hash'] != intent_hash:
        raise HTTPException(409, '该请求编号已用于其他内容，请核对原创建结果')
    return dict(row) if row else None


def remember(conn, actor, kind, key, intent_hash, object_id):
    if key is not None:
        conn.execute('INSERT INTO creation_requests VALUES(?,?,?,?,?,?)',
                     (actor, kind, key, intent_hash, object_id, utc_now()))
