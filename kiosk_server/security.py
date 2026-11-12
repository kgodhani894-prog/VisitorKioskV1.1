from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
import time
import threading
from pathlib import Path


def hash_password(password: str, *, iterations: int = 310_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode(),
        digest.hex(),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        method, iterations_text, salt_text, expected_hex = stored_hash.split("$", 3)
        if method != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations).hex()
        return hmac.compare_digest(actual, expected_hex)
    except Exception:
        return False


def sign_session(secret: str, *, ttl_seconds: int = 12 * 60 * 60) -> str:
    expires = int(time.time()) + ttl_seconds
    payload = f"admin:{expires}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(token.encode()).decode()


def verify_session(secret: str, token: str | None) -> bool:
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        role, expires_text, signature = decoded.split(":", 2)
        if role != "admin" or int(expires_text) < int(time.time()):
            return False
        payload = f"{role}:{expires_text}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


class LoginThrottle:
    """Throttle repeated password guessing, optionally persisting lockouts in SQLite."""

    def __init__(
        self,
        max_failures: int = 5,
        lock_seconds: int = 300,
        *,
        database_path: str | Path | None = None,
        fingerprint_secret: str | None = None,
    ):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._database_path = Path(database_path) if database_path is not None else None
        self._fingerprint_secret: bytes | None = None
        if self._database_path is not None:
            if not fingerprint_secret:
                raise ValueError("Persistent login throttling requires a fingerprint secret.")
            self._fingerprint_secret = fingerprint_secret.encode()
            connection = self._connect()
            try:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS kiosk_login_throttle (
                        client_id TEXT PRIMARY KEY,
                        failures INTEGER NOT NULL,
                        locked_until REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )"""
                )
                connection.commit()
            finally:
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        if self._database_path is None:
            raise RuntimeError("Persistent login throttling is not configured.")
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _client_id(self, key: str) -> str:
        assert self._fingerprint_secret is not None
        return hmac.new(self._fingerprint_secret, key.encode(), hashlib.sha256).hexdigest()

    def _persistent_retry_after(self, key: str, current: float) -> int:
        client_id = self._client_id(key)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM kiosk_login_throttle WHERE updated_at < ? AND locked_until <= ?",
                (current - 24 * 60 * 60, current),
            )
            row = connection.execute(
                "SELECT locked_until FROM kiosk_login_throttle WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            if row is None or float(row[0]) <= current:
                if row is not None and float(row[0]) > 0:
                    connection.execute("DELETE FROM kiosk_login_throttle WHERE client_id = ?", (client_id,))
                connection.commit()
                return 0
            connection.commit()
            return max(1, int(float(row[0]) - current + 0.999))
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _persistent_record_failure(self, key: str, current: float) -> int:
        client_id = self._client_id(key)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM kiosk_login_throttle WHERE updated_at < ? AND locked_until <= ?",
                (current - 24 * 60 * 60, current),
            )
            row = connection.execute(
                "SELECT failures, locked_until FROM kiosk_login_throttle WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            failures, locked_until = (int(row[0]), float(row[1])) if row else (0, 0.0)
            if locked_until > current:
                connection.commit()
                return max(1, int(locked_until - current + 0.999))
            if locked_until:
                failures = 0
            failures += 1
            locked_until = current + self.lock_seconds if failures >= self.max_failures else 0.0
            connection.execute(
                """INSERT INTO kiosk_login_throttle (client_id, failures, locked_until, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(client_id) DO UPDATE SET
                     failures = excluded.failures,
                     locked_until = excluded.locked_until,
                     updated_at = excluded.updated_at""",
                (client_id, failures, locked_until, current),
            )
            connection.commit()
            return max(0, int(locked_until - current))
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _persistent_record_success(self, key: str) -> None:
        client_id = self._client_id(key)
        connection = self._connect()
        try:
            connection.execute("DELETE FROM kiosk_login_throttle WHERE client_id = ?", (client_id,))
            connection.commit()
        finally:
            connection.close()

    def retry_after(self, key: str, now: float | None = None) -> int:
        current = time.time() if now is None else now
        if self._database_path is not None:
            return self._persistent_retry_after(key, current)
        with self._lock:
            failures, locked_until = self._entries.get(key, (0, 0.0))
            if locked_until <= current:
                if locked_until:
                    self._entries.pop(key, None)
                return 0
            return max(1, int(locked_until - current + 0.999))

    def record_failure(self, key: str, now: float | None = None) -> int:
        current = time.time() if now is None else now
        if self._database_path is not None:
            return self._persistent_record_failure(key, current)
        with self._lock:
            failures, locked_until = self._entries.get(key, (0, 0.0))
            if locked_until > current:
                return max(1, int(locked_until - current + 0.999))
            failures += 1
            if failures >= self.max_failures:
                locked_until = current + self.lock_seconds
            self._entries[key] = (failures, locked_until)
            return max(0, int(locked_until - current))

    def record_success(self, key: str) -> None:
        if self._database_path is not None:
            self._persistent_record_success(key)
            return
        with self._lock:
            self._entries.pop(key, None)

