import sqlite3
import json
from datetime import datetime
from . import config

def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tokens (
            id          INTEGER PRIMARY KEY,
            user_id     TEXT NOT NULL DEFAULT 'default',
            access_token TEXT,
            session_id  TEXT,
            bank_name   TEXT,
            bank_country TEXT,
            expires_at  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id              INTEGER PRIMARY KEY,
            user_id         TEXT NOT NULL DEFAULT 'default',
            tx_id           TEXT NOT NULL,
            notion_page_id  TEXT,
            status          TEXT DEFAULT 'pending',
            last_seen       TEXT,
            UNIQUE(user_id, tx_id)
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id          INTEGER PRIMARY KEY,
            user_id     TEXT NOT NULL DEFAULT 'default',
            ran_at      TEXT DEFAULT (datetime('now')),
            status      TEXT,
            tx_count    INTEGER DEFAULT 0,
            message     TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()

def save_tokens(session_id, access_token, bank_name, bank_country, expires_at, user_id="default"):
    conn = get_conn()
    conn.execute("""
        INSERT INTO tokens (user_id, access_token, session_id, bank_name, bank_country, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
    """, (user_id, access_token, session_id, bank_name, bank_country, expires_at))
    conn.commit()
    conn.close()

def get_tokens(user_id="default"):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM tokens WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def clear_tokens(user_id="default"):
    conn = get_conn()
    conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_known_tx_ids(user_id="default"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT tx_id FROM transactions WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return {r["tx_id"] for r in rows}

def upsert_transaction(tx_id, notion_page_id, status, user_id="default"):
    conn = get_conn()
    conn.execute("""
        INSERT INTO transactions (user_id, tx_id, notion_page_id, status, last_seen)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, tx_id) DO UPDATE SET
            notion_page_id = excluded.notion_page_id,
            status = excluded.status,
            last_seen = excluded.last_seen
    """, (user_id, tx_id, notion_page_id, status))
    conn.commit()
    conn.close()

def get_pending_transactions(user_id="default"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM transactions WHERE user_id = ? AND status = 'pending'", (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def log_sync(status, tx_count=0, message="", user_id="default"):
    conn = get_conn()
    conn.execute(
        "INSERT INTO sync_log (user_id, status, tx_count, message) VALUES (?, ?, ?, ?)",
        (user_id, status, tx_count, message)
    )
    conn.commit()
    conn.close()

def get_recent_syncs(limit=10, user_id="default"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sync_log WHERE user_id = ? ORDER BY ran_at DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_last_sync(user_id="default"):
    conn = get_conn()
    row = conn.execute(
        "SELECT ran_at FROM sync_log WHERE user_id = ? AND status = 'success' ORDER BY ran_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    return row["ran_at"] if row else None

def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def get_sync_log_page(page=1, per_page=5, user_id="default"):
    conn = get_conn()
    offset = (page - 1) * per_page
    total = conn.execute(
        "SELECT COUNT(*) FROM sync_log WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM sync_log WHERE user_id = ? ORDER BY ran_at DESC LIMIT ? OFFSET ?",
        (user_id, per_page, offset)
    ).fetchall()
    conn.close()
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "syncs": [dict(r) for r in rows],
        "page": page,
        "total_pages": total_pages,
    }

def clear_sync_log(user_id="default"):
    conn = get_conn()
    conn.execute("DELETE FROM sync_log WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
