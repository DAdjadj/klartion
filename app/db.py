import sqlite3
import json
from datetime import datetime
from . import config
import uuid

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
            license_seat_id TEXT,
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

        CREATE TABLE IF NOT EXISTS category_rules (
            merchant    TEXT PRIMARY KEY,
            category    TEXT NOT NULL,
            user_id     TEXT NOT NULL DEFAULT 'default'
        );
    """)
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN start_sync_date TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN last_sync_at TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN last_balance TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN last_balance_currency TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN provider TEXT NOT NULL DEFAULT 'enablebanking'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN provider_credentials TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN sync_mode TEXT NOT NULL DEFAULT 'transactions'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE tokens ADD COLUMN license_seat_id TEXT")
    except sqlite3.OperationalError:
        pass
    rows = conn.execute("SELECT id FROM tokens WHERE license_seat_id IS NULL OR license_seat_id = ''").fetchall()
    for row in rows:
        conn.execute("UPDATE tokens SET license_seat_id = ? WHERE id = ?", (str(uuid.uuid4()), row["id"]))
    conn.commit()
    conn.close()

def save_tokens(session_id, access_token, bank_name, bank_country, expires_at, user_id="default", token_id=None, start_sync_date=""):
    conn = get_conn()
    if token_id:
        conn.execute("""
            UPDATE tokens
            SET access_token = ?, session_id = ?, bank_name = ?, bank_country = ?, expires_at = ?,
                start_sync_date = CASE WHEN ? <> '' THEN ? ELSE start_sync_date END
            WHERE id = ?
        """, (access_token, session_id, bank_name, bank_country, expires_at, start_sync_date, start_sync_date, token_id))
        saved_id = token_id
    else:
        cur = conn.execute("""
            INSERT INTO tokens (user_id, access_token, session_id, bank_name, bank_country, expires_at, start_sync_date, license_seat_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, access_token, session_id, bank_name, bank_country, expires_at, start_sync_date, str(uuid.uuid4())))
        saved_id = cur.lastrowid
    conn.commit()
    conn.close()
    return saved_id

def get_tokens(user_id="default"):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM tokens WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_tokens(user_id="default"):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM tokens WHERE user_id = ? ORDER BY created_at ASC", (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_token_by_id(token_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM tokens WHERE id = ?",
        (token_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def clear_tokens(user_id="default"):
    conn = get_conn()
    conn.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def clear_token_by_id(token_id):
    conn = get_conn()
    conn.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
    conn.commit()
    conn.close()

def get_known_tx_ids(user_id="default", tx_id_prefix=None):
    conn = get_conn()
    if tx_id_prefix:
        rows = conn.execute(
            "SELECT tx_id FROM transactions WHERE user_id = ? AND tx_id LIKE ?",
            (user_id, f"{tx_id_prefix}%")
        ).fetchall()
    else:
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

def get_pending_transactions(user_id="default", tx_id_prefix=None):
    conn = get_conn()
    if tx_id_prefix:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id = ? AND LOWER(status) = 'pending' AND tx_id LIKE ?",
            (user_id, f"{tx_id_prefix}%")
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE user_id = ? AND LOWER(status) = 'pending'",
            (user_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_provider_token(bank_name, provider, provider_credentials, user_id="default"):
    """Save a balance provider token (no session/access_token needed)."""
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO tokens (user_id, bank_name, bank_country, provider, provider_credentials, sync_mode, license_seat_id)
        VALUES (?, ?, '', ?, ?, 'balance', ?)
    """, (user_id, bank_name, provider, provider_credentials, str(uuid.uuid4())))
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_token_count(user_id="default"):
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM tokens WHERE user_id = ?", (user_id,)).fetchone()[0]
    conn.close()
    return count


def update_token_fields(token_id, **fields):
    allowed = {"access_token", "session_id", "bank_name", "bank_country", "expires_at", "start_sync_date", "last_sync_at", "last_balance", "last_balance_currency", "provider_credentials", "license_seat_id"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    conn = get_conn()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    params = list(updates.values()) + [token_id]
    conn.execute(f"UPDATE tokens SET {assignments} WHERE id = ?", params)
    conn.commit()
    conn.close()

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


def save_category_rules(rules: dict, user_id="default"):
    """Save merchant -> category mappings. rules is {merchant: category}."""
    if not rules:
        return
    conn = get_conn()
    for merchant, category in rules.items():
        conn.execute("""
            INSERT INTO category_rules (merchant, category, user_id)
            VALUES (?, ?, ?)
            ON CONFLICT(merchant) DO UPDATE SET category = excluded.category
        """, (merchant, category, user_id))
    conn.commit()
    conn.close()


def get_category_rules(user_id="default") -> dict:
    """Return {merchant: category} mapping."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT merchant, category FROM category_rules WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()
    return {r["merchant"]: r["category"] for r in rows}
