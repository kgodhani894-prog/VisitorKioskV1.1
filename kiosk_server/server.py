from __future__ import annotations

import base64
import csv
import io
import hmac
import ipaddress
import logging
import json
import mimetypes
import os
import re
import shutil
import ssl
import secrets
import socket
import threading
import time
import uuid
from functools import wraps
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import Database
from .backups import backup_freshness, backup_is_due, create_backup, ensure_secondary_backup, latest_verified_backup, record_backup_success
from .notifications import OAUTH_CALLBACK_PATH, complete_microsoft365_auth_flow, disconnect_microsoft365, email_message, initiate_microsoft365_auth_flow, microsoft365_configuration_status, microsoft365_connection_status, microsoft365_oauth_setup_status, send_email
from .microsoft365_setup import generate_certificate, install_certificate, load_settings as load_microsoft365_setup, public_certificate, save_settings as save_microsoft365_setup, setup_status as microsoft365_setup_status, validate_app_settings
from .security import LoginThrottle, hash_password, sign_session, verify_password, verify_session
from .storage_lock import storage_operation_lock

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
logger = logging.getLogger(__name__)
DEFAULT_OWNER_PASSWORD = "change-me-before-use"
TEST_RUNTIME_MARKER = ".elite-rf-test-runtime"
TEST_RUNTIME_MARKER_CONTENT = "Elite RF test runtime\n"
SINGLE_KIOSK_LOCATION = "Main Lobby"
SERVICE_ID = "elite-rf-visitor-kiosk"
SERVICE_VERSION = "1"
MAX_NOTIFICATION_ATTEMPTS = 8
MAX_NOTIFICATION_RETRY_SECONDS = 60 * 60
MAX_REQUEST_BODY_BYTES = 7_000_000
REQUEST_READ_TIMEOUT_SECONDS = 15
CHECKIN_TEXT_LIMITS = {
    "first_name": 100,
    "last_name": 100,
    "phone": 50,
    "email": 254,
    "company": 150,
}


def safe_csv_cell(value):
    """Keep spreadsheet programs from evaluating untrusted exported text."""
    if not isinstance(value, str):
        return value
    formula_candidate = value.lstrip(" \t\r\n\v\f\x00\ufeff")
    if value.startswith(("\t", "\r", "\n")) or (formula_candidate and formula_candidate[0] in "=+-@"):
        return "'" + value
    return value


class RequestBodyError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def handle_request_body_errors(handler):
    @wraps(handler)
    def wrapped(self, *args, **kwargs):
        try:
            return handler(self, *args, **kwargs)
        except RequestBodyError as exc:
            return self.error_response(exc.status, str(exc))
    return wrapped


def load_config(root: Path = ROOT) -> dict:
    with open(root / "config.json", "r", encoding="utf-8") as file:
        return json.load(file)


def load_local_env(root: Path = ROOT) -> None:
    """Load simple KEY=value secrets from a local .env file without extra packages."""
    env_file = root / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            # A Windows/environment value set outside this file takes priority.
            os.environ.setdefault(key, value.strip())


def save_config(config: dict, root: Path = ROOT) -> None:
    """Atomically replace config.json so a sudden shutdown cannot leave partial JSON."""
    target = root / "config.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def is_synthetic_test_config(config: dict) -> bool:
    if str(config.get("database_path")) != "data/visitor-kiosk.sqlite3":
        return False
    if str(config.get("photo_storage_path")) != "storage/photos":
        return False
    if not verify_password("test-owner-password-1234", str(config.get("admin_password_hash", ""))):
        return False
    if not str(config.get("session_secret", "")).strip():
        return False
    if bool(config.get("cookie_secure", True)):
        return False
    if int(config.get("max_photo_bytes", 0)) != 1_000_000:
        return False
    if int(config.get("automatic_backup_hours", 0)) != 24:
        return False
    settings = config.get("notification_settings", {})
    if bool(settings.get("enabled", True)):
        return False
    if str(settings.get("provider", "")) != "microsoft_graph_email":
        return False
    if str(settings.get("message_template", "")) != "{visitor_name} from {company} is waiting for you at {location}.":
        return False
    employees = config.get("employees", [])
    if len(employees) != 1:
        return False
    employee = employees[0]
    return (
        str(employee.get("name", "")) == "Test Host"
        and str(employee.get("email", "")) == "test-host@example.invalid"
        and int(employee.get("active", 0)) == 1
    )


def runtime_root_has_production_looking_artifacts(root: Path) -> bool:
    if not root.exists():
        return False
    suspicious_paths = [
        root / "data",
        root / "storage" / "photos",
        root / "backups",
    ]
    for path in suspicious_paths:
        if not path.exists():
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".zip", ".env"}:
                return True
            if child.is_file() and child.name.lower() == ".env":
                return True
    return False


def validate_test_runtime_root(root: Path) -> Path:
    resolved = Path(root).resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError("Test mode runtime root must be outside the project folder.")

    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("Test mode runtime root must be a directory.")
        marker = resolved / TEST_RUNTIME_MARKER
        try:
            marked_disposable = marker.read_text(encoding="utf-8") == TEST_RUNTIME_MARKER_CONTENT
        except (OSError, UnicodeError):
            marked_disposable = False
        if not marked_disposable:
            raise ValueError("Existing test mode runtime root must contain the disposable test marker.")

    if (resolved / ".env").exists():
        raise ValueError("Test mode runtime root cannot contain a .env file.")

    config_path = resolved / "config.json"
    if resolved.exists() and not config_path.exists():
        raise ValueError("Existing test mode runtime root must contain a synthetic config.json.")
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("Test mode runtime root contains an unreadable config.json.") from exc
        if not is_synthetic_test_config(config):
            raise ValueError("Test mode runtime root already contains production-looking config.json.")
    elif runtime_root_has_production_looking_artifacts(resolved):
        raise ValueError("Test mode runtime root already contains production-looking data.")
    return resolved


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_client(client_address: tuple[str, int] | str | None) -> bool:
    if not client_address:
        return False
    host = client_address[0] if isinstance(client_address, tuple) else client_address
    return is_loopback_host(str(host))


def environment_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def warn_on_unsafe_configuration(config: dict, host: str, tls_enabled: bool = False, runtime_root: Path | None = None) -> None:
    session_secret = str(config.get("session_secret", "")).strip()
    if not os.environ.get("KIOSK_SESSION_SECRET"):
        logger.warning("KIOSK_SESSION_SECRET is not set, so the backend will use config.json for session signing.")
    if session_secret and session_secret.lower() in {"change-me", "default", "placeholder", "replace-me", "session-secret"}:
        logger.warning("config.json session_secret looks like a placeholder value.")
    if not is_loopback_host(host) and not (config.get("cookie_secure", False) or tls_enabled):
        logger.warning("cookie_secure is disabled while the server is listening on a non-loopback host.")
    notifications_enabled = bool(config.get("notification_settings", {}).get("enabled", True))
    connection = microsoft365_connection_status(runtime_root, os.environ.get("KIOSK_SESSION_SECRET") or session_secret) if runtime_root else {"connected": False}
    if notifications_enabled and not connection["connected"] and not all(microsoft365_configuration_status().values()):
        logger.warning("Notifications are enabled, but Microsoft 365 email configuration is incomplete.")


def load_tls_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    except (ssl.SSLError, OSError, ValueError) as exc:
        raise ValueError("Could not load the TLS certificate and private key.") from exc
    return context


def owner_setup_required(config: dict, allow_environment: bool = True) -> bool:
    if allow_environment and os.environ.get("KIOSK_ADMIN_PASSWORD_HASH"):
        return False
    password_hash = str(config.get("admin_password_hash", "")).strip()
    if not password_hash:
        return True
    return verify_password(DEFAULT_OWNER_PASSWORD, password_hash)


def notification_message(app: dict, visit: dict) -> tuple[str, str, str]:
    return email_message(visit)


def notification_retry_delay(attempts: int) -> int:
    return min(60 * (2 ** max(0, attempts - 1)), MAX_NOTIFICATION_RETRY_SECONDS)


def deliver_notification(
    app: dict,
    visit_id: int,
    employee: dict,
    message: tuple[str, str, str],
    *,
    prior_attempts: int = 0,
) -> tuple[str, str]:
    attempted = False
    if not app["config"].get("notification_settings", {}).get("enabled", True):
        status, detail = "skipped", "Notifications are disabled in the admin dashboard."
    elif app.get("test_mode"):
        status, detail = "skipped", "Notifications are disabled in test mode."
    else:
        attempted = True
        status, detail = send_email(employee, *message, runtime_root=app["root"], session_secret=os.environ.get("KIOSK_SESSION_SECRET") or str(app["config"].get("session_secret", "")))
    attempts = prior_attempts + int(attempted)
    next_attempt_at = 0
    if status == "pending":
        if attempts >= MAX_NOTIFICATION_ATTEMPTS:
            status = "failed"
            detail = "Notification retry limit reached; administrator review is required."
        else:
            next_attempt_at = int(time.time()) + notification_retry_delay(attempts)
    app["db"].update_notification(
        visit_id,
        status,
        detail,
        count_attempt=attempted,
        next_attempt_at=next_attempt_at,
    )
    return status, detail


def retry_pending_notifications(app: dict) -> None:
    for visit in app["db"].pending_notifications():
        employee = {
            "email": visit["employee_email"],
        }
        deliver_notification(
            app,
            visit["id"],
            employee,
            notification_message(app, visit),
            prior_attempts=int(visit.get("notification_attempts", 0)),
        )


def sync_directory(path: Path) -> None:
    """Persist directory-entry changes on POSIX hosts when supported."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def cleanup_pending_photo_deletions(db: Database, photos: Path) -> int:
    """Finish photo removals recorded transactionally in the database."""
    remaining = 0
    for filename in db.pending_photo_deletions():
        if (
            not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or Path(filename).name != filename
        ):
            logger.error("Skipped unsafe filename in pending visitor-photo deletion queue.")
            remaining += 1
            continue
        photo_path = photos / filename
        try:
            photo_path.unlink(missing_ok=True)
            sync_directory(photos)
            db.complete_photo_deletion(filename)
        except OSError:
            logger.exception("Could not finish a queued visitor-photo deletion.")
            remaining += 1
    return remaining


class KioskHandler(BaseHTTPRequestHandler):
    server_version = "EliteRFKiosk/0.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_READ_TIMEOUT_SECONDS)

    @property
    def app(self):
        return self.server.app

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/kiosk"):
            return self.send_file(STATIC / "kiosk.html")
        if path == "/admin":
            return self.send_file(STATIC / "admin.html")
        if path == "/api/system/identity":
            return self.json_response({"service": SERVICE_ID, "version": SERVICE_VERSION, "status": "ok"})
        if path == "/api/employees":
            return self.json_response({"employees": self.app["db"].list_active_employees_public()})
        if path == "/api/admin/visits":
            if not self.require_admin():
                return
            return self.json_response({"visits": self.app["db"].list_visits()})
        if path == "/api/admin/visits/export.csv":
            if not self.require_admin():
                return
            output = io.StringIO(newline="")
            fields = ["id", "created_at", "first_name", "last_name", "phone", "email", "company", "employee_name", "kiosk_id", "photo_filename", "status", "voided_at", "voided_reason", "notification_status"]
            writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(
                {key: safe_csv_cell(value) for key, value in visit.items() if key in fields}
                for visit in self.app["db"].list_all_visits()
            )
            raw = output.getvalue().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="elite-rf-visitor-log.csv"')
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/admin/visits/export.pdf":
            if not self.require_admin():
                return
            try:
                from .pdf_export import build_visits_pdf

                raw = build_visits_pdf(self.app["db"].list_all_visits())
            except Exception:
                logger.exception("Visitor database PDF export failed.")
                return self.error_response(500, "Could not create the visitor database PDF.")
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'inline; filename="elite-rf-visitor-database.pdf"')
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/api/admin/setup-state":
            setup_required = owner_setup_required(self.app["config"], allow_environment=not self.app.get("test_mode"))
            return self.json_response({
                "setup_required": setup_required,
                "setup_allowed": setup_required and self.setup_allowed_for_client(),
            })
        if path == "/api/admin/health":
            if not self.require_admin():
                return
            health = self.app["db"].health()
            root = self.app["root"]
            locations = {"records": self.app["db"].path.parent, "photos": self.app["photos"], "backups": root / "backups"}
            storage = []
            for label, location in locations.items():
                try:
                    capacity = shutil.disk_usage(location)
                    used_percent = round((capacity.total - capacity.free) * 100 / capacity.total) if capacity.total else 0
                    status = "critical" if used_percent >= 95 else "warning" if used_percent >= 85 else "ok"
                    storage.append({"area": label, "used_percent": used_percent, "free_bytes": capacity.free, "total_bytes": capacity.total, "status": status})
                except OSError:
                    storage.append({"area": label, "status": "unknown"})
            secondary = self.app.get("secondary_backup_path")
            if secondary is None:
                storage.append({"area": "secondary backups", "status": "unconfigured"})
            elif self.app.get("require_mounted_backup") and os.name == "posix" and not secondary.is_mount():
                storage.append({"area": "secondary backups", "status": "unmounted"})
            else:
                try:
                    capacity = shutil.disk_usage(secondary)
                    used_percent = round((capacity.total - capacity.free) * 100 / capacity.total) if capacity.total else 0
                    status = "critical" if used_percent >= 95 else "warning" if used_percent >= 85 else "ok"
                    storage.append({"area": "secondary backups", "used_percent": used_percent, "free_bytes": capacity.free, "total_bytes": capacity.total, "status": status})
                except OSError:
                    storage.append({"area": "secondary backups", "status": "unknown"})
            backup_status, backup_age = backup_freshness(root)
            freshness = {"area": "backup freshness", "status": backup_status}
            if backup_age is not None:
                freshness["age_seconds"] = backup_age
            storage.append(freshness)
            health["storage"] = storage
            return self.json_response(health)
        if path == "/api/admin/settings":
            if not self.require_admin():
                return
            settings = self.app["config"].get("notification_settings", {})
            return self.json_response({"notifications": {
                "enabled": bool(settings.get("enabled", True)),
                "provider": "microsoft_graph_email",
                "message_template": str(settings.get("message_template", "{visitor_name} from {company} is waiting for you at {location}.")),
            }})
        if path == "/api/admin/notifications/readiness":
            if not self.require_admin():
                return
            root = self.app["root"]
            secret = self.session_secret()
            return self.json_response({
                "email": microsoft365_configuration_status(),
                "connection": microsoft365_connection_status(root, secret),
                "oauth_setup": microsoft365_oauth_setup_status(root, secret),
                "setup_details": microsoft365_setup_status(root, secret),
                "test_mode": bool(self.app.get("test_mode")),
            })
        if path == "/api/admin/notifications/certificate":
            if not self.require_admin():
                return
            if self.app.get("test_mode"):
                return self.error_response(400, "Microsoft 365 setup is disabled in test mode.")
            try:
                certificate = public_certificate(self.app["root"], self.session_secret())
            except ValueError as exc:
                return self.error_response(404, str(exc))
            except OSError:
                return self.error_response(500, "The public certificate could not be read from protected host storage.")
            self.send_response(200)
            self.send_header("Content-Type", "application/pkix-cert")
            self.send_header("Content-Disposition", 'attachment; filename="elite-rf-microsoft365.cer"')
            self.send_header("Content-Length", str(len(certificate)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(certificate)
            return
        if path == "/api/admin/notifications/connect":
            if not self.require_admin():
                return
            return self.start_microsoft365_connection()
        if path == "/api/admin/employees":
            if not self.require_admin():
                return
            return self.json_response({"employees": self.app["db"].list_employees_admin()})
        if path.startswith("/photos/"):
            if not self.require_admin():
                return
            filename = Path(path).name
            return self.send_file(self.app["photos"] / filename)
        if path.startswith("/static/"):
            return self.send_file(STATIC / Path(path).name)
        return self.error_response(404, "Not found")

    @handle_request_body_errors
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/checkins":
            return self.create_checkin()
        if path == "/api/admin/setup":
            return self.setup_owner_password()
        if path == "/api/admin/login":
            return self.login()
        if path == "/api/admin/change-password":
            if not self.require_admin():
                return
            return self.change_password()
        if path == "/api/admin/logout":
            return self.logout()
        if path == "/api/admin/notifications/test":
            if not self.require_admin():
                return
            return self.test_notification()
        if path == "/api/admin/notifications/setup":
            if not self.require_admin():
                return
            return self.save_microsoft365_setup()
        if path == "/api/admin/notifications/certificate/generate":
            if not self.require_admin():
                return
            return self.generate_microsoft365_certificate()
        if path == "/api/admin/notifications/certificate/upload":
            if not self.require_admin():
                return
            return self.upload_microsoft365_certificate()
        if path == "/api/admin/notifications/disconnect":
            if not self.require_admin():
                return
            disconnect_microsoft365(self.app["root"])
            return self.json_response({"ok": True})
        if path == OAUTH_CALLBACK_PATH:
            return self.microsoft365_callback()
        if path == "/api/admin/employees":
            if not self.require_admin():
                return
            return self.create_employee()
        return self.error_response(404, "Not found")

    @handle_request_body_errors
    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/admin/visits/(\d+)", path)
        if match:
            if not self.require_admin():
                return
            body = self.read_json()
            status = str(body.get("status", ""))
            if status not in {"waiting", "met", "cancelled"}:
                return self.error_response(400, "Invalid status")
            ok = self.app["db"].update_visit_status(int(match.group(1)), status)
            return self.json_response({"ok": ok})
        match = re.fullmatch(r"/api/admin/employees/(\d+)", path)
        if match:
            if not self.require_admin():
                return
            return self.update_employee(int(match.group(1)))
        if path == "/api/admin/settings":
            if not self.require_admin():
                return
            return self.update_settings()
        if path == "/api/admin/notifications/test":
            if not self.require_admin():
                return
            return self.test_notification()
        return self.error_response(404, "Not found")

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/admin/visits/(\d+)", path)
        if match:
            if not self.require_admin():
                return
            if self.headers.get("X-Kiosk-Admin-Action") != "delete-visit":
                return self.error_response(403, "This admin action must come from the dashboard.")
            visit_id = int(match.group(1))
            result = None
            pending = False
            try:
                with storage_operation_lock(self.app["root"]):
                    result = self.app["db"].delete_visit(visit_id)
                    if result is None:
                        return self.error_response(404, "Visitor record not found.")
                    filename = result["photo_filename"]
                    try:
                        cleanup_pending_photo_deletions(self.app["db"], self.app["photos"])
                        pending = filename in self.app["db"].pending_photo_deletions() if filename else False
                    except Exception:
                        logger.exception("Visit was deleted, but photo cleanup needs retry.")
                        pending = bool(filename)
            except Exception:
                if result is None:
                    logger.exception("Administrator visit deletion failed before commit.")
                    return self.error_response(500, "The visitor record could not be deleted.")
                logger.exception("Visit deletion committed; photo cleanup needs retry.")
                pending = bool(result["photo_filename"])
            return self.json_response(
                {"ok": True, "photo_cleanup_pending": pending},
                status=202 if pending else 200,
            )
        match = re.fullmatch(r"/api/admin/employees/(\d+)", path)
        if match:
            if not self.require_admin():
                return
            result = self.app["db"].remove_employee(int(match.group(1)))
            return self.json_response({"ok": bool(result), "result": result})
        return self.error_response(404, "Not found")

    def login(self) -> None:
        if owner_setup_required(self.app["config"], allow_environment=not self.app.get("test_mode")):
            return self.error_response(409, "Owner password setup is required before sign-in.")
        client_key = str(getattr(self, "client_address", ("unknown", 0))[0])
        if self.app.get("external_https") and is_loopback_host(client_key):
            forwarded_for = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            try:
                client_key = str(ipaddress.ip_address(forwarded_for))
            except ValueError:
                pass
        throttle = self.app.setdefault("login_throttle", LoginThrottle())
        retry_after = throttle.retry_after(client_key)
        if retry_after:
            return self.error_response(429, "Too many sign-in attempts. Please wait five minutes and try again.")
        body = self.read_json()
        password = str(body.get("password", ""))
        expected_hash = self.app["config"]["admin_password_hash"]
        if not self.app.get("test_mode"):
            expected_hash = os.environ.get("KIOSK_ADMIN_PASSWORD_HASH") or expected_hash
        if not verify_password(password, expected_hash):
            throttle.record_failure(client_key)
            return self.error_response(401, "Wrong password")
        throttle.record_success(client_key)
        secret = self.session_secret()
        token = sign_session(secret)
        self.json_response({"ok": True}, cookie=self.session_cookie(token))

    def setup_owner_password(self) -> None:
        if not self.setup_allowed_for_client():
            return self.error_response(403, "First-run setup must be completed on the host computer.")
        if not self.app.get("test_mode") and os.environ.get("KIOSK_ADMIN_PASSWORD_HASH"):
            return self.error_response(409, "Password is managed by the host environment, not the dashboard.")
        if not owner_setup_required(self.app["config"], allow_environment=not self.app.get("test_mode")):
            return self.error_response(409, "Owner password is already set.")
        body = self.read_json()
        password = str(body.get("password", ""))
        confirm = str(body.get("confirm_password", ""))
        if password != confirm:
            return self.error_response(400, "Passwords do not match")
        if len(password) < 10:
            return self.error_response(400, "Use a password of at least 10 characters")
        with self.app["setup_lock"]:
            if not owner_setup_required(self.app["config"], allow_environment=not self.app.get("test_mode")):
                return self.error_response(409, "Owner password is already set.")
            config = dict(self.app["config"])
            config["admin_password_hash"] = hash_password(password)
            if self.app.get("test_mode") or not os.environ.get("KIOSK_SESSION_SECRET"):
                # Rotate the bootstrap secret only when the host is not already managing it.
                config["session_secret"] = secrets.token_urlsafe(48)
            save_config(config, self.app["root"])
            self.app["config"] = config
        token = sign_session(self.session_secret())
        self.json_response({"ok": True, "setup_required": False}, cookie=self.session_cookie(token))

    def change_password(self) -> None:
        if not self.app.get("test_mode") and os.environ.get("KIOSK_ADMIN_PASSWORD_HASH"):
            return self.error_response(409, "Password is managed by the host environment, not the dashboard.")
        body = self.read_json()
        current = str(body.get("current_password", ""))
        new = str(body.get("new_password", ""))
        confirm = str(body.get("confirm_password", ""))
        if not verify_password(current, self.app["config"]["admin_password_hash"]):
            return self.error_response(401, "Current password is incorrect")
        if new != confirm:
            return self.error_response(400, "New passwords do not match")
        if len(new) < 10:
            return self.error_response(400, "Use a password of at least 10 characters")
        config = self.app["config"]
        config["admin_password_hash"] = hash_password(new)
        # Rotating this secret immediately invalidates every old browser session.
        config["session_secret"] = secrets.token_urlsafe(48)
        save_config(config, self.app["root"])
        self.json_response({"ok": True}, cookie=self.session_cookie(sign_session(config["session_secret"])))

    def logout(self) -> None:
        self.json_response({"ok": True}, cookie="elite_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")

    def update_settings(self) -> None:
        body = self.read_json()
        enabled = bool(body.get("notifications_enabled", True))
        template = str(body.get("message_template", "")).strip()
        if not template or len(template) > 240:
            return self.error_response(400, "Enter a message template of 1 to 240 characters")
        allowed = {"{visitor_name}", "{company}", "{location}", "{host_name}"}
        placeholders = set(re.findall(r"\{[^{}]+\}", template))
        if not placeholders.issubset(allowed):
            return self.error_response(400, "Use only {visitor_name}, {company}, {location}, or {host_name} in the template")
        config = self.app["config"]
        config["notification_settings"] = {
            "enabled": enabled,
            "provider": "microsoft_graph_email",
            "message_template": template,
        }
        save_config(config, self.app["root"])
        return self.json_response({"ok": True, "notifications": config["notification_settings"]})

    def test_notification(self) -> None:
        body = self.read_json()
        try:
            employee_id = int(body.get("employee_id", 0))
        except Exception:
            return self.error_response(400, "Select an active employee")
        employee = self.app["db"].get_employee(employee_id)
        if not employee:
            return self.error_response(400, "Select an active employee")
        if not str(employee.get("email", "")).strip() or "@" not in str(employee.get("email", "")):
            return self.error_response(400, "Selected employee does not have a valid email address")
        if self.app.get("test_mode"):
            return self.json_response({"ok": True, "status": "skipped", "detail": "Test mode does not send email."})
        status, detail = send_email(employee, "Visitor waiting at Elite RF", "Elite RF email delivery test.", "<p>Elite RF email delivery test.</p>", runtime_root=self.app["root"], session_secret=self.session_secret())
        safe_detail = detail if status in {"sent", "skipped"} else "Microsoft 365 email request failed."
        return self.json_response({"ok": True, "status": status, "detail": safe_detail})

    def save_microsoft365_setup(self) -> None:
        if self.app.get("test_mode"):
            return self.error_response(400, "Microsoft 365 setup is disabled in test mode.")
        if not self.microsoft365_setup_transport_is_secure():
            return self.error_response(400, "Microsoft 365 setup must be completed on the host or through trusted HTTPS.")
        body = self.read_json()
        try:
            updated = validate_app_settings(body.get("tenant_id"), body.get("client_id"), body.get("redirect_uri"))
            existing = load_microsoft365_setup(self.app["root"], self.session_secret())
        except ValueError as exc:
            return self.error_response(400, str(exc))
        changed = any(existing.get(key) != value for key, value in updated.items())
        existing.update(updated)
        try:
            save_microsoft365_setup(self.app["root"], self.session_secret(), existing)
        except ValueError as exc:
            return self.error_response(400, str(exc))
        except OSError:
            return self.error_response(500, "Microsoft 365 setup could not be saved to protected host storage.")
        if changed:
            disconnect_microsoft365(self.app["root"])
        return self.json_response({"ok": True, "setup_details": microsoft365_setup_status(self.app["root"], self.session_secret())})

    def generate_microsoft365_certificate(self) -> None:
        if self.app.get("test_mode"):
            return self.error_response(400, "Microsoft 365 setup is disabled in test mode.")
        if not self.microsoft365_setup_transport_is_secure():
            return self.error_response(400, "Microsoft 365 setup must be completed on the host or through trusted HTTPS.")
        try:
            result = generate_certificate(self.app["root"], self.session_secret())
        except ValueError as exc:
            return self.error_response(400, str(exc))
        except OSError:
            return self.error_response(500, "The certificate could not be generated in protected host storage.")
        disconnect_microsoft365(self.app["root"])
        return self.json_response({"ok": True, **result})

    def upload_microsoft365_certificate(self) -> None:
        if self.app.get("test_mode"):
            return self.error_response(400, "Microsoft 365 setup is disabled in test mode.")
        if not self.microsoft365_setup_transport_is_secure():
            return self.error_response(400, "Microsoft 365 setup must be completed on the host or through trusted HTTPS.")
        body = self.read_json()
        certificate_value = str(body.get("certificate_base64", ""))
        private_key_value = str(body.get("private_key_base64", ""))
        if not certificate_value or not private_key_value or len(certificate_value) > 4_000_000 or len(private_key_value) > 4_000_000:
            return self.error_response(400, "Choose a public certificate and a private-key file no larger than 2 MB each.")
        try:
            certificate_bytes = base64.b64decode(certificate_value, validate=True)
            private_key_bytes = base64.b64decode(private_key_value, validate=True)
        except (ValueError, base64.binascii.Error):
            return self.error_response(400, "The selected certificate files could not be read.")
        if len(certificate_bytes) > 2_000_000 or len(private_key_bytes) > 2_000_000:
            return self.error_response(400, "Choose files no larger than 2 MB each.")
        try:
            result = install_certificate(self.app["root"], self.session_secret(), certificate_bytes, private_key_bytes)
        except ValueError as exc:
            return self.error_response(400, str(exc))
        except OSError:
            return self.error_response(500, "The certificate could not be saved in protected host storage.")
        disconnect_microsoft365(self.app["root"])
        return self.json_response({"ok": True, **result})

    def microsoft365_setup_transport_is_secure(self) -> bool:
        if self.app.get("tls_enabled") or self.app.get("external_https"):
            return True
        if not is_loopback_client(getattr(self, "client_address", None)):
            return False
        host = self.headers.get("Host", "").strip().lower()
        host = host[1:host.find("]")] if host.startswith("[") and "]" in host else host.split(":", 1)[0]
        return host in {"localhost", "127.0.0.1", "::1"}

    def employee_payload(self, body: dict) -> dict | None:
        name = str(body.get("name", "")).strip()
        if not name or len(name) > 120:
            self.error_response(400, "Employee name is required and must be 120 characters or less")
            return None
        email = str(body.get("email", "")).strip()
        if len(email) > 254:
            self.error_response(400, "Employee contact information is too long")
            return None
        if not email or "@" not in email:
            self.error_response(400, "Enter a valid employee email address")
            return None
        return {
            "name": name,
            "email": email,
            "phone": "",
            "alert_method": "email",
            "active": 1 if body.get("active", True) else 0,
        }

    def create_employee(self) -> None:
        data = self.employee_payload(self.read_json())
        if data is None:
            return
        return self.json_response({"ok": True, "employee": self.app["db"].create_employee(data)}, 201)

    def update_employee(self, employee_id: int) -> None:
        data = self.employee_payload(self.read_json())
        if data is None:
            return
        employee = self.app["db"].update_employee(employee_id, data)
        if not employee:
            return self.error_response(404, "Employee not found")
        return self.json_response({"ok": True, "employee": employee})

    def create_checkin(self) -> None:
        body = self.read_json()
        if not isinstance(body, dict):
            return self.error_response(400, "Request must be a JSON object")
        required = ["first_name", "last_name", "email", "company", "employee_id", "photo"]
        missing = [
            field for field in required
            if field not in body or not isinstance(body.get(field), (str, int)) or not str(body.get(field, "")).strip()
        ]
        if missing:
            return self.error_response(400, f"Missing required fields: {', '.join(missing)}")

        for field, maximum in CHECKIN_TEXT_LIMITS.items():
            value = body.get(field, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                return self.error_response(400, f"Invalid {field.replace('_', ' ')}")
            if len(value.strip()) > maximum:
                return self.error_response(400, f"{field.replace('_', ' ').title()} is too long")
        if "@" not in body["email"].strip():
            return self.error_response(400, "Enter a valid visitor email address")
        if not isinstance(body["photo"], str):
            return self.error_response(400, "Invalid photo")

        try:
            employee_id = int(body["employee_id"])
        except Exception:
            return self.error_response(400, "Invalid employee")

        employee = self.app["db"].get_employee(employee_id)
        if not employee:
            return self.error_response(400, "Employee not found")

        photo_data = str(body["photo"])
        if "," in photo_data:
            photo_data = photo_data.split(",", 1)[1]
        try:
            photo_bytes = base64.b64decode(photo_data, validate=True)
        except Exception:
            return self.error_response(400, "Invalid photo")
        if not photo_bytes.startswith(b"\xff\xd8"):
            return self.error_response(400, "Photo must be a JPEG")
        if len(photo_bytes) > int(self.app["config"].get("max_photo_bytes", 5_242_880)):
            return self.error_response(400, "Photo is too large")

        phone = str(body.get("phone", "")).strip()

        filename = f"{uuid.uuid4().hex}.jpg"
        # Write then rename, so a power cut cannot leave a half-written JPEG.
        final_photo = self.app["photos"] / filename
        temp_photo = self.app["photos"] / f".{filename}.tmp"
        try:
            with storage_operation_lock(self.app["root"]):
                with temp_photo.open("xb") as photo_file:
                    photo_file.write(photo_bytes)
                    photo_file.flush()
                    os.fsync(photo_file.fileno())
                os.replace(temp_photo, final_photo)
                sync_directory(self.app["photos"])
                visit_id = self.app["db"].create_visit(
                    {
                        "first_name": body["first_name"].strip(),
                        "last_name": body["last_name"].strip(),
                        "phone": phone,
                        "email": body["email"].strip(),
                        "company": body["company"].strip(),
                        "employee_id": employee["id"],
                        "employee_name": employee["name"],
                        "kiosk_id": SINGLE_KIOSK_LOCATION,
                        "photo_filename": filename,
                        "notification_status": "pending",
                        "notification_detail": "Waiting to send",
                    }
                )
        except Exception:
            for candidate in (temp_photo, final_photo):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
            logger.exception("Check-in save failed; staged photo removed.")
            return self.error_response(500, "Could not save check-in")
        visitor_name = f"{body['first_name'].strip()} {body['last_name'].strip()}"
        visit = {
            "first_name": body["first_name"].strip(),
            "last_name": body["last_name"].strip(),
            "company": body["company"].strip(),
            "kiosk_id": SINGLE_KIOSK_LOCATION,
            "employee_name": employee["name"],
        }
        notification_status, _ = deliver_notification(self.app, visit_id, employee, notification_message(self.app, visit))
        return self.json_response({
            "ok": True,
            "visit_id": visit_id,
            "photo_saved": final_photo.is_file() and final_photo.stat().st_size > 0,
            "record_saved": True,
            "notification_status": notification_status,
        })

    def read_json(self) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise RequestBodyError(400, "Transfer-encoded request bodies are not supported.")
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise RequestBodyError(411, "Content-Length is required.")
        if not length_header.isdecimal():
            raise RequestBodyError(400, "Content-Length is invalid.")
        length = int(length_header)
        if length < 1:
            raise RequestBodyError(400, "A JSON request body is required.")
        if length > MAX_REQUEST_BODY_BYTES:
            raise RequestBodyError(413, "Request is too large.")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise RequestBodyError(415, "Content-Type must be application/json.")
        try:
            raw = self.rfile.read(length)
        except (TimeoutError, socket.timeout) as exc:
            raise RequestBodyError(408, "Request body timed out.") from exc
        if len(raw) != length:
            raise RequestBodyError(400, "Request body was incomplete.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestBodyError(400, "Request body must contain valid JSON.") from exc
        if not isinstance(payload, dict):
            raise RequestBodyError(400, "JSON request body must be an object.")
        return payload

    def session_secret(self) -> str:
        if self.app.get("test_mode"):
            return self.app["config"]["session_secret"]
        return os.environ.get("KIOSK_SESSION_SECRET") or self.app["config"]["session_secret"]

    def read_urlencoded_form(self) -> dict[str, str]:
        if self.headers.get("Transfer-Encoding"):
            raise RequestBodyError(400, "Transfer-encoded request bodies are not supported.")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
            raise RequestBodyError(415, "Microsoft sign-in response has an invalid content type.")
        length_header = self.headers.get("Content-Length")
        if length_header is None or not length_header.isdecimal():
            raise RequestBodyError(400, "Microsoft sign-in response is incomplete.")
        length = int(length_header)
        if length < 1 or length > 65536:
            raise RequestBodyError(400, "Microsoft sign-in response is invalid.")
        try:
            raw = self.rfile.read(length)
        except (TimeoutError, socket.timeout) as exc:
            raise RequestBodyError(408, "Microsoft sign-in response timed out.") from exc
        if len(raw) != length:
            raise RequestBodyError(400, "Microsoft sign-in response is incomplete.")
        try:
            decoded = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=30)
        except (UnicodeDecodeError, ValueError) as exc:
            raise RequestBodyError(400, "Microsoft sign-in response is invalid.") from exc
        return {key: values[0] for key, values in decoded.items() if values}

    def oauth_cookie_value(self) -> str | None:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        morsel = jar.get("elite_m365_oauth")
        return morsel.value if morsel else None

    def start_microsoft365_connection(self) -> None:
        if self.app.get("test_mode"):
            return self.error_response(400, "Microsoft 365 connection is disabled in test mode.")
        if not (self.app.get("tls_enabled") or self.app.get("external_https")):
            return self.error_response(400, "Microsoft 365 connection requires the trusted HTTPS dashboard address.")
        try:
            state = secrets.token_urlsafe(32)
            browser_nonce = secrets.token_urlsafe(32)
            flow = initiate_microsoft365_auth_flow(state, self.app["root"], self.session_secret())
        except ValueError as exc:
            return self.error_response(503, str(exc))
        now = time.time()
        with self.app["oauth_lock"]:
            flows = self.app["oauth_flows"]
            for old_state, record in list(flows.items()):
                if record["expires_at"] < now:
                    flows.pop(old_state, None)
            if len(flows) >= 20:
                return self.error_response(429, "Too many Microsoft sign-in attempts. Wait a few minutes and try again.")
            flows[state] = {"flow": flow, "nonce": browser_nonce, "expires_at": now + 600}
        cookie = f"elite_m365_oauth={browser_nonce}; HttpOnly; SameSite=None; Secure; Path={OAUTH_CALLBACK_PATH}; Max-Age=600"
        return self.redirect_response(flow["auth_uri"], status=302, cookie=cookie)

    def microsoft365_callback(self) -> None:
        if self.app.get("test_mode") or not (self.app.get("tls_enabled") or self.app.get("external_https")):
            return self.redirect_response("/admin?m365=error", status=303, clear_oauth_cookie=True)
        try:
            response = self.read_urlencoded_form()
        except RequestBodyError as exc:
            return self.error_response(exc.status, str(exc))
        state = str(response.get("state", ""))
        cookie_nonce = self.oauth_cookie_value()
        with self.app["oauth_lock"]:
            record = self.app["oauth_flows"].pop(state, None) if state else None
        if not record or record["expires_at"] < time.time() or not cookie_nonce or not hmac.compare_digest(cookie_nonce, record["nonce"]):
            return self.redirect_response("/admin?m365=error", status=303, clear_oauth_cookie=True)
        try:
            complete_microsoft365_auth_flow(record["flow"], response, self.app["root"], self.session_secret())
        except ValueError:
            logger.warning("Microsoft 365 account connection failed; admin should review host configuration and reconnect.")
            return self.redirect_response("/admin?m365=error", status=303, clear_oauth_cookie=True)
        return self.redirect_response("/admin?m365=connected", status=303, clear_oauth_cookie=True)

    def setup_allowed_for_client(self) -> bool:
        if not self.app.get("test_mode") and environment_flag("KIOSK_DISABLE_REMOTE_OWNER_SETUP"):
            return False
        return is_loopback_client(getattr(self, "client_address", None))

    def admin_token(self) -> str | None:
        header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        jar.load(header)
        morsel = jar.get("elite_admin")
        return morsel.value if morsel else None

    def require_admin(self) -> bool:
        if verify_session(self.session_secret(), self.admin_token()):
            return True
        self.error_response(401, "Admin login required")
        return False

    def session_cookie(self, token: str) -> str:
        secure = "; Secure" if self.app.get("tls_enabled") or self.app.get("external_https") or self.app["config"].get("cookie_secure", False) else ""
        return f"elite_admin={token}; HttpOnly; SameSite=Strict; Path=/{secure}"

    def end_headers(self) -> None:
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; connect-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            return self.error_response(404, "File not found")
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, data: dict, status: int = 200, cookie: str | None = None) -> None:
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def redirect_response(self, location: str, status: int = 303, cookie: str | None = None, clear_oauth_cookie: bool = False) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if clear_oauth_cookie:
            self.send_header("Set-Cookie", f"elite_m365_oauth=; HttpOnly; SameSite=None; Secure; Path={OAUTH_CALLBACK_PATH}; Max-Age=0")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def error_response(self, status: int, message: str) -> None:
        self.json_response({"ok": False, "error": message}, status)

    def log_message(self, format: str, *args) -> None:
        return


def create_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    runtime_root: Path | None = None,
    test_mode: bool = False,
    tls_cert_path: Path | None = None,
    tls_key_path: Path | None = None,
) -> ThreadingHTTPServer:
    if test_mode and not is_loopback_host(host):
        raise ValueError("Test mode must bind to a loopback host such as 127.0.0.1.")
    if test_mode and runtime_root is None:
        raise ValueError("Test mode requires an explicit disposable runtime root.")
    if test_mode and (tls_cert_path or tls_key_path):
        raise ValueError("Test mode must remain HTTP-only.")
    if bool(tls_cert_path) != bool(tls_key_path):
        raise ValueError("Both TLS certificate and private key must be configured together.")
    root = Path(runtime_root or ROOT).resolve()
    if test_mode and runtime_root is not None:
        root = validate_test_runtime_root(root)
    if not test_mode:
        if root == ROOT:
            load_local_env()
        else:
            load_local_env(root)
    external_https = not test_mode and environment_flag("KIOSK_EXTERNAL_HTTPS")
    if external_https and not is_loopback_host(host):
        raise ValueError("KIOSK_EXTERNAL_HTTPS requires the backend to bind to loopback behind a local reverse proxy.")
    tls_context = None
    if tls_cert_path and tls_key_path:
        cert_path = Path(tls_cert_path).resolve()
        key_path = Path(tls_key_path).resolve()
        if not cert_path.exists() or not cert_path.is_file():
            raise ValueError(f"TLS certificate file not found: {cert_path}")
        if not key_path.exists() or not key_path.is_file():
            raise ValueError(f"TLS private key file not found: {key_path}")
        tls_context = load_tls_context(cert_path, key_path)
    if not test_mode and not is_loopback_host(host) and tls_context is None:
        raise ValueError("Production cannot bind to a non-loopback address without TLS. Use direct TLS or bind to loopback behind a trusted HTTPS reverse proxy.")
    config = load_config() if root == ROOT else load_config(root)
    db = Database(root / config["database_path"])
    db.seed_employees(config.get("employees", []))
    throttle_secret = os.environ.get("KIOSK_SESSION_SECRET") or str(config.get("session_secret", ""))
    login_throttle = LoginThrottle(database_path=db.path, fingerprint_secret=throttle_secret)
    photos = root / config["photo_storage_path"]
    photos.mkdir(parents=True, exist_ok=True)
    warn_on_unsafe_configuration(config, host, tls_enabled=tls_context is not None or external_https, runtime_root=root)
    server = ThreadingHTTPServer((host, port), KioskHandler)
    server.app = {
        "config": config,
        "db": db,
        "photos": photos,
        "root": root,
        "setup_lock": threading.Lock(),
        "login_throttle": login_throttle,
        "test_mode": test_mode,
        "external_https": external_https,
        "oauth_flows": {},
        "oauth_lock": threading.Lock(),
    }
    with storage_operation_lock(root):
        cleanup_pending_photo_deletions(db, photos)
    server.app["tls_enabled"] = False
    if tls_context is not None:
        try:
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
        except Exception as exc:
            try:
                server.server_close()
            except Exception:
                logger.exception("TLS startup failed and the listening socket could not be closed cleanly")
            raise ValueError("Could not enable TLS on the listening socket.") from exc
        server.app["tls_enabled"] = True
    interval = max(1, int(config.get("automatic_backup_hours", 24))) * 60 * 60
    retention_count = max(0, int(os.environ.get("KIOSK_BACKUP_RETENTION", "0")))
    copy_path_value = os.environ.get("KIOSK_BACKUP_COPY_PATH", "").strip()
    copy_destination = Path(copy_path_value).expanduser() if copy_path_value else None
    server.app["secondary_backup_path"] = copy_destination
    server.app["require_mounted_backup"] = environment_flag("KIOSK_REQUIRE_MOUNTED_BACKUP")
    backup_timer_managed = environment_flag("KIOSK_BACKUP_TIMER_MANAGED")
    stop_event = threading.Event()
    server.app["stop_event"] = stop_event

    def backup_loop() -> None:
        next_backup_check = time.monotonic()
        while not stop_event.is_set():
            try:
                with storage_operation_lock(server.app["root"]):
                    cleanup_pending_photo_deletions(server.app["db"], server.app["photos"])
            except Exception:
                logger.exception("Pending visitor-photo deletion cleanup failed")
            try:
                retry_pending_notifications(server.app)
            except Exception:
                logger.exception("Background notification retry failed")
            if time.monotonic() >= next_backup_check:
                try:
                    backup_updated = False
                    if not backup_timer_managed:
                        latest_backup = latest_verified_backup(root / "backups")
                        if backup_is_due(root, interval):
                            latest_backup = create_backup(
                                root,
                                retention_count=retention_count,
                            )
                            backup_updated = True
                        if copy_destination is not None and latest_backup is not None:
                            ensure_secondary_backup(
                                latest_backup,
                                copy_destination,
                                retention_count=retention_count,
                            )
                            backup_updated = True
                    if backup_updated:
                        record_backup_success(root)
                except Exception:
                    logger.exception("Background backup failed")
                    next_backup_check = time.monotonic() + 5 * 60
                else:
                    next_backup_check = time.monotonic() + min(interval, 60 * 60)
            stop_event.wait(60)

    if not test_mode:
        threading.Thread(target=backup_loop, name="kiosk-backup", daemon=True).start()
    original_server_close = server.server_close

    def server_close() -> None:
        stop_event.set()
        original_server_close()

    server.server_close = server_close
    return server
