from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    alert_method TEXT NOT NULL DEFAULT 'email',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    email TEXT NOT NULL,
    company TEXT NOT NULL,
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    kiosk_id TEXT NOT NULL,
    photo_filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting',
    voided_at TEXT NOT NULL DEFAULT '',
    voided_reason TEXT NOT NULL DEFAULT '',
    notification_status TEXT NOT NULL DEFAULT 'pending',
    notification_detail TEXT NOT NULL DEFAULT '',
    notification_attempts INTEGER NOT NULL DEFAULT 0,
    notification_next_attempt_at INTEGER NOT NULL DEFAULT 0,
    notification_last_attempt_at INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(employee_id) REFERENCES employees(id)
);
CREATE TABLE IF NOT EXISTS pending_photo_deletions (
    photo_filename TEXT PRIMARY KEY,
    queued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS visits_delete_requires_admin_operation
BEFORE DELETE ON visits
WHEN kiosk_allow_visit_delete() != 1
BEGIN
    SELECT RAISE(ABORT, 'Visitor records may only be deleted through the authenticated admin operation.');
END;
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    visit_id INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class KioskConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self, *, allow_visit_delete: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, factory=KioskConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA secure_delete = ON")
        conn.create_function("kiosk_allow_visit_delete", 0, lambda: int(allow_visit_delete))
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current_version} is newer than this application supports ({SCHEMA_VERSION})."
                )
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TRIGGER IF EXISTS visits_never_delete")
            conn.execute("DROP TRIGGER IF EXISTS visits_retain_forever")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(visits)")}
            migrations = {
                "notification_attempts": "INTEGER NOT NULL DEFAULT 0",
                "notification_next_attempt_at": "INTEGER NOT NULL DEFAULT 0",
                "notification_last_attempt_at": "INTEGER NOT NULL DEFAULT 0",
                "voided_at": "TEXT NOT NULL DEFAULT ''",
                "voided_reason": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE visits ADD COLUMN {name} {declaration}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_visits_created_at_id ON visits(created_at DESC, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_visits_photo_filename ON visits(photo_filename)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_visits_notification_retry "
                "ON visits(notification_status, notification_next_attempt_at, id)"
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def seed_employees(self, employees: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            if count:
                return
            conn.executemany(
                """
                INSERT INTO employees (name, phone, email, alert_method, active)
                VALUES (:name, :phone, :email, :alert_method, :active)
                """,
                employees,
            )

    def list_active_employees_public(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, name FROM employees WHERE active = 1 ORDER BY name"
            ).fetchall()
            return [dict(row) for row in rows]

    def list_employees_admin(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM employees ORDER BY active DESC, name COLLATE NOCASE"
            ).fetchall()
            return [dict(row) for row in rows]

    def create_employee(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO employees (name, phone, email, alert_method, active)
                VALUES (:name, :phone, :email, :alert_method, :active)
                """,
                data,
            )
            row = conn.execute("SELECT * FROM employees WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return dict(row)

    def update_employee(self, employee_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE employees
                SET name = :name, phone = :phone, email = :email,
                    alert_method = :alert_method, active = :active
                WHERE id = :id
                """,
                {**data, "id": employee_id},
            )
            if not cursor.rowcount:
                return None
            row = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
            return dict(row)

    def remove_employee(self, employee_id: int) -> str | None:
        """Delete unused employees; deactivate employees with visitor history."""
        with self.connect() as conn:
            exists = conn.execute("SELECT id FROM employees WHERE id = ?", (employee_id,)).fetchone()
            if not exists:
                return None
            visit_count = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE employee_id = ?", (employee_id,)
            ).fetchone()[0]
            if visit_count:
                conn.execute("UPDATE employees SET active = 0 WHERE id = ?", (employee_id,))
                return "deactivated"
            conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            return "deleted"

    def get_employee(self, employee_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM employees WHERE id = ? AND active = 1",
                (employee_id,),
            ).fetchone()
            return dict(row) if row else None

    def create_visit(self, data: dict[str, Any]) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO visits (
                    first_name, last_name, phone, email, company, employee_id,
                    employee_name, kiosk_id, photo_filename, notification_status,
                    notification_detail
                )
                VALUES (
                    :first_name, :last_name, :phone, :email, :company, :employee_id,
                    :employee_name, :kiosk_id, :photo_filename, :notification_status,
                    :notification_detail
                )
                """,
                data,
            )
            return int(cursor.lastrowid)

    def update_notification(
        self,
        visit_id: int,
        status: str,
        detail: str,
        *,
        count_attempt: bool = False,
        next_attempt_at: int = 0,
    ) -> int:
        with self.connect() as conn:
            attempted_at = int(time.time()) if count_attempt else 0
            conn.execute(
                """
                UPDATE visits
                SET notification_status = ?, notification_detail = ?,
                    notification_attempts = notification_attempts + ?,
                    notification_last_attempt_at = CASE WHEN ? THEN ? ELSE notification_last_attempt_at END,
                    notification_next_attempt_at = ?
                WHERE id = ?
                """,
                (status, detail, int(count_attempt), int(count_attempt), attempted_at, next_attempt_at, visit_id),
            )
            row = conn.execute(
                "SELECT notification_attempts FROM visits WHERE id = ?", (visit_id,)
            ).fetchone()
            return int(row[0]) if row else 0

    def pending_notifications(self, now: int | None = None) -> list[dict[str, Any]]:
        """Return pending email notifications whose retry time has arrived."""
        current = int(time.time()) if now is None else int(now)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT visits.*, employees.email AS employee_email
                FROM visits JOIN employees ON employees.id = visits.employee_id
                WHERE visits.notification_status = 'pending'
                  AND visits.notification_next_attempt_at <= ?
                ORDER BY visits.id ASC
                """,
                (current,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_visits(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM visits ORDER BY created_at DESC, id DESC LIMIT 200"
            ).fetchall()
            return [dict(row) for row in rows]

    def list_all_visits(self) -> list[dict[str, Any]]:
        """Return the complete visitor history for controlled export/recovery."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM visits ORDER BY created_at ASC, id ASC").fetchall()
            return [dict(row) for row in rows]

    def update_visit_status(self, visit_id: int, status: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE visits SET status = ? WHERE id = ?",
                (status, visit_id),
            )
            return cursor.rowcount > 0

    def void_visit(self, visit_id: int, reason: str, detail: str = "") -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE visits SET status = 'voided', voided_at = CURRENT_TIMESTAMP, voided_reason = ? WHERE id = ? AND voided_at = ''",
                (reason, visit_id),
            )
            if not cursor.rowcount:
                return False
            conn.execute(
                "INSERT INTO audit_log (event_type, visit_id, detail) VALUES (?, ?, ?)",
                ("visit_voided", visit_id, detail or reason),
            )
            return True

    def delete_visit(self, visit_id: int) -> dict[str, Any] | None:
        """Delete one visit through the authorized app path and queue photo cleanup."""
        with self.connect(allow_visit_delete=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT photo_filename FROM visits WHERE id = ?", (visit_id,)
            ).fetchone()
            if row is None:
                return None

            filename = str(row["photo_filename"])
            if (
                not filename
                or filename in {".", ".."}
                or "/" in filename
                or "\\" in filename
                or Path(filename).name != filename
            ):
                raise ValueError("Stored visitor photo filename is unsafe; visit deletion was cancelled.")
            shared = conn.execute(
                "SELECT 1 FROM visits WHERE photo_filename = ? AND id != ? LIMIT 1",
                (filename, visit_id),
            ).fetchone()
            photo_cleanup_queued = not bool(shared)
            if photo_cleanup_queued:
                conn.execute(
                    "INSERT OR IGNORE INTO pending_photo_deletions (photo_filename) VALUES (?)",
                    (filename,),
                )

            conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
            conn.execute(
                "INSERT INTO audit_log (event_type, visit_id, detail) VALUES (?, ?, ?)",
                ("visit_deleted", visit_id, "Administrator deleted visitor record from the live system."),
            )
            return {
                "visit_id": visit_id,
                "photo_filename": filename if photo_cleanup_queued else None,
                "photo_cleanup_queued": photo_cleanup_queued,
            }

    def pending_photo_deletions(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT photo_filename FROM pending_photo_deletions ORDER BY queued_at, photo_filename"
            ).fetchall()
            return [str(row[0]) for row in rows]

    def complete_photo_deletion(self, filename: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM pending_photo_deletions WHERE photo_filename = ?",
                (filename,),
            )

    def health(self) -> dict[str, Any]:
        with self.connect() as conn:
            visits = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            employees = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE notification_status = 'failed'"
            ).fetchone()[0]
            pending_photo_deletions = conn.execute(
                "SELECT COUNT(*) FROM pending_photo_deletions"
            ).fetchone()[0]
            return {
                "visits": visits,
                "employees": employees,
                "failed_notifications": failed,
                "pending_photo_deletions": pending_photo_deletions,
            }
