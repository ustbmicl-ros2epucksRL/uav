import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
DATA = Path(os.getenv("SENSE_DATA_DIR", str(ROOT / "data")))


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / "platform.sqlite3", timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    with connection() as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, path TEXT NOT NULL,
                kind TEXT NOT NULL, pages INTEGER, fingerprint TEXT, indexed_at TEXT);
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY, document_id TEXT NOT NULL, page INTEGER,
                text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, session TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, meta TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, scene TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS computation_jobs (
                id TEXT PRIMARY KEY, scene TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, snapshot TEXT NOT NULL,
                result TEXT, error TEXT, parent_id TEXT);
            CREATE INDEX IF NOT EXISTS jobs_status_created
                ON computation_jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS jobs_scene_created
                ON computation_jobs(scene, created_at);
        """)
        conn.execute('INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)', (now(),))


def backup(destination):
    """SQLite online backup includes committed WAL data; never overwrite a backup."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb'):
        pass
    try:
        with connection() as source, sqlite3.connect(destination) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('备份完整性检查失败')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def get(key, default=None):
    with connection() as conn:
        row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(key, value):
    with connection() as conn:
        conn.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))


def message(session, role, content, meta=None):
    with connection() as conn:
        conn.execute("INSERT INTO messages(session,role,content,meta,created_at) VALUES (?,?,?,?,?)",
                     (session, role, content, json.dumps(meta or {}, ensure_ascii=False), now()))


def history(session):
    with connection() as conn:
        rows = conn.execute("SELECT * FROM (SELECT * FROM messages WHERE session=? ORDER BY id DESC LIMIT 40) ORDER BY id", (session,)).fetchall()
    return [{**dict(r), "meta": json.loads(r["meta"] or "{}")} for r in rows]
