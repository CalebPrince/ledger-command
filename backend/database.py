"""
database.py
------------
SQLite connection handling + schema bootstrap for the AI-Powered
Accounting Command Center.

Tables:
  users                        -> all system accounts (all 4 roles)
  employee_client_assignments  -> maps a client to their account officer
  audit_logs                   -> compliance trail for admin/system actions
  clients_data / inbox_items   -> lightweight demo data used by the
                                   scoped dashboard endpoints in routes.py
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from auth import hash_password

DB_PATH = Path(__file__).parent / "accounting_command_center.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(seed: bool = True) -> None:
    """Create tables if they do not exist and seed a default Super Admin."""
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                email           TEXT NOT NULL UNIQUE,
                hashed_password TEXT NOT NULL,
                role            TEXT NOT NULL CHECK (role IN
                                    ('super_admin', 'admin', 'employee', 'client')),
                status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN
                                    ('active', 'suspended')),
                company_name    TEXT,
                is_independent  INTEGER NOT NULL DEFAULT 0,
                created_by      INTEGER REFERENCES users(id),
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS employee_client_assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                client_id   INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                assigned_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id    INTEGER,
                actor_role  TEXT,
                action      TEXT NOT NULL,
                detail      TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Demo data so the scoped endpoints have something real to filter.
            CREATE TABLE IF NOT EXISTS inbox_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject     TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'unread',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS data_clean_rows (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                row_label   TEXT NOT NULL,
                flag        TEXT NOT NULL DEFAULT 'needs_review',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Real feature tables backing the client/employee portal pages.
            CREATE TABLE IF NOT EXISTS invoices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                invoice_number  TEXT NOT NULL,
                amount_cents    INTEGER NOT NULL,
                status          TEXT NOT NULL DEFAULT 'unpaid' CHECK (status IN ('unpaid', 'paid', 'overdue')),
                due_date        TEXT NOT NULL,
                created_by      INTEGER REFERENCES users(id),
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                paid_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                stored_filename TEXT NOT NULL,
                original_name   TEXT NOT NULL,
                content_type    TEXT,
                size_bytes      INTEGER,
                uploaded_by     INTEGER REFERENCES users(id),
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS checklist_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                label           TEXT NOT NULL,
                is_complete     INTEGER NOT NULL DEFAULT 0,
                created_by      INTEGER REFERENCES users(id),
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                sender_id       INTEGER NOT NULL REFERENCES users(id),
                sender_role     TEXT NOT NULL,
                body            TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS chaser_campaigns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                employee_id     INTEGER NOT NULL REFERENCES users(id),
                invoice_id      INTEGER REFERENCES invoices(id),
                status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed')),
                notes           TEXT,
                last_chased_at  TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS composio_scopes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key       TEXT NOT NULL UNIQUE,
                label           TEXT NOT NULL,
                high_level      INTEGER NOT NULL DEFAULT 0,
                enabled         INTEGER NOT NULL DEFAULT 1,
                updated_by      INTEGER REFERENCES users(id),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Generic key/value store for platform-wide settings a Super Admin
            -- configures at runtime (e.g. the Gemini API key) instead of an
            -- environment variable, so it can be changed without a restart.
            CREATE TABLE IF NOT EXISTS app_settings (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                updated_by  INTEGER REFERENCES users(id),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Simulated AI-agent suggestions: a canned template bank stands in
            -- for real model output, but the review workflow (pending queue,
            -- explicit human Approve/Reject, real side-effects on approval) is
            -- fully real -- this is the human-in-the-loop control surface.
            CREATE TABLE IF NOT EXISTS ai_suggestions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                suggestion_type TEXT NOT NULL CHECK (suggestion_type IN
                                    ('reconciliation_flag', 'client_reminder', 'invoice_draft')),
                title           TEXT NOT NULL,
                detail          TEXT NOT NULL,
                payload         TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                                    ('pending', 'approved', 'rejected')),
                generated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                reviewed_by     INTEGER REFERENCES users(id),
                reviewed_at     TEXT
            );
            """
        )

        # Lightweight migration: older DBs created before `company_name` existed
        # won't have the column since CREATE TABLE IF NOT EXISTS is a no-op on
        # an existing table. Add it if missing so upgrades don't break.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "company_name" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN company_name TEXT")
        if "is_independent" not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN is_independent INTEGER NOT NULL DEFAULT 0")

        if seed:
            existing = conn.execute(
                "SELECT id FROM users WHERE role = 'super_admin' LIMIT 1"
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO users (name, email, hashed_password, role, status, created_by)
                       VALUES (?, ?, ?, 'super_admin', 'active', NULL)""",
                    (
                        "System Super Admin",
                        "superadmin@firm.com",
                        hash_password("ChangeMe123!"),
                    ),
                )
                print(
                    "[seed] Created default Super Admin -> "
                    "superadmin@firm.com / ChangeMe123!  (CHANGE THIS PASSWORD)"
                )

            existing_scopes = conn.execute("SELECT id FROM composio_scopes LIMIT 1").fetchone()
            if not existing_scopes:
                conn.executemany(
                    """INSERT INTO composio_scopes (scope_key, label, high_level, enabled)
                       VALUES (?, ?, ?, 1)""",
                    [
                        ("gmail_read", "Gmail — Read Inbox", 0),
                        ("drive_write", "Google Drive — Write Files", 0),
                        ("slack_notify", "Slack — Send Notifications", 0),
                        ("project_key_rotation", "Rotate Project API Key", 1),
                        ("folder_path_config", "Modify Root Folder Path", 1),
                    ],
                )


def log_audit(actor_id: int | None, actor_role: str | None, action: str, detail: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO audit_logs (actor_id, actor_role, action, detail) VALUES (?, ?, ?, ?)",
            (actor_id, actor_role, action, detail),
        )
