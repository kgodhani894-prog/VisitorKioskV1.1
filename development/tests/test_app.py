from __future__ import annotations

import argparse
import base64
import gc
import importlib.util
import json
import os
import runpy
import socket
import ssl
import stat
import subprocess
import sys
import threading
import shutil
import sqlite3
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.parse import urlencode
from unittest.mock import patch

import run as kiosk_run
from kiosk_server import backups as kiosk_backups
from kiosk_server import server as kiosk_server
from kiosk_server import notifications as kiosk_notifications
from kiosk_server import microsoft365_setup as kiosk_microsoft365_setup
from kiosk_server.backups import backup_is_due, create_backup, ensure_secondary_backup, verify_backup
from kiosk_server.db import Database
from kiosk_server.security import LoginThrottle, hash_password, verify_password, sign_session, verify_session


def load_create_shortcuts_module():
    path = Path(kiosk_server.ROOT) / "tools" / "create_shortcuts.py"
    spec = importlib.util.spec_from_file_location("elite_rf_create_shortcuts", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_build_release_module():
    path = Path(kiosk_server.ROOT) / "tools" / "build_release.py"
    spec = importlib.util.spec_from_file_location("elite_rf_build_release", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_restore_backup_module():
    path = Path(kiosk_server.ROOT) / "tools" / "restore_backup.py"
    spec = importlib.util.spec_from_file_location("elite_rf_restore_backup", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_supervisor_module():
    path = Path(kiosk_server.ROOT) / "tools" / "supervise.py"
    spec = importlib.util.spec_from_file_location("elite_rf_supervisor", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def create_minimal_release_source(source_root: Path) -> None:
    source_root.mkdir(parents=True, exist_ok=True)
    builder = load_build_release_module()
    root_files = builder.FILES
    nested_files = (
        tuple(f"tools/{name}" for name in builder.TOOLS)
        + tuple(f"tools/linux/{name}" for name in builder.LINUX_TOOLS)
        + tuple(f"docs/{name}" for name in builder.OPERATOR_DOCS)
    )
    for relative in (*root_files, *builder.APP_FILES, *nested_files):
        source = Path(kiosk_server.ROOT) / relative
        target = source_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for directory, filenames in (("kiosk_server", builder.KIOSK_SERVER_FILES), ("static", builder.STATIC_FILES)):
        for filename in filenames:
            source = Path(kiosk_server.ROOT) / directory / filename
            target = source_root / directory / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def snapshot_file_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def extract_package_archive(archive: Path) -> tuple[Path, Path]:
    extract_root = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(archive) as package_zip:
        package_zip.extractall(extract_root)
    return extract_root, extract_root


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp())
        self.db_path = self.tempdir / "visitor-kiosk.sqlite3"
        self.photos_path = self.tempdir / "photos"
        self.photos_path.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.db_path)
        self.db.seed_employees([
            {
                "name": "Test Host",
                "phone": "+15555550100",
                "email": "host@example.com",
                "alert_method": "email",
                "active": 1,
            }
        ])
        self.employee = self.db.list_active_employees_public()[0]
        self.config = {
            "database_path": str(self.db_path),
            "photo_storage_path": str(self.photos_path.relative_to(self.tempdir)),
            "admin_password_hash": hash_password("test-admin-password"),
            "session_secret": "test-session-secret",
            "cookie_secure": False,
            "max_photo_bytes": 1_000_000,
            "notification_settings": {
                "enabled": True,
                "provider": "microsoft_graph_email",
                "message_template": "{visitor_name} from {company} is waiting for you at {location}.",
            },
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), kiosk_server.KioskHandler)
        self.server.app = {"config": self.config, "db": self.db, "photos": self.photos_path, "root": self.tempdir, "setup_lock": threading.Lock(), "test_mode": False, "external_https": False, "tls_enabled": False, "oauth_flows": {}, "oauth_lock": threading.Lock()}
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.save_config_patch = patch("kiosk_server.server.save_config")
        self.send_email_patch = patch("kiosk_server.server.send_email")
        self.mock_save_config = self.save_config_patch.start()
        self.mock_save_config.return_value = None
        self.mock_send_email = self.send_email_patch.start()
        self.mock_send_email.return_value = ("skipped", "Microsoft 365 email is not configured.")

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=5)
        finally:
            self.save_config_patch.stop()
            self.send_email_patch.stop()
            shutil.rmtree(self.tempdir, ignore_errors=True)

    def request_json(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        cookie: str | None = None,
        extra_headers: dict | None = None,
    ):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        if extra_headers:
            headers.update(extra_headers)
        body = None if data is None else json.dumps(data).encode()
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode()), dict(response.headers)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode()), dict(exc.headers)

    def request_no_redirect(self, method: str, path: str, *, body: bytes | None = None, headers: dict | None = None):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, response_headers, newurl):
                return None

        request = Request(self.base_url + path, data=body, headers=headers or {}, method=method)
        opener = build_opener(NoRedirect)
        try:
            with opener.open(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def raw_http_request(self, headers: str, body: bytes = b"") -> str:
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3) as connection:
            connection.settimeout(3)
            request = (
                "POST /api/admin/login HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Connection: close\r\n"
                f"{headers}\r\n\r\n"
            ).encode("ascii") + body
            connection.sendall(request)
            chunks = []
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("iso-8859-1")

    def login_cookie(self) -> str:
        status, payload, headers = self.request_json("POST", "/api/admin/login", {"password": "test-admin-password"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        cookie = headers["Set-Cookie"]
        return cookie.split(";", 1)[0]

    def invoke_setup_handler(self, *, client_address=("127.0.0.1", 1234), password="owner-password-1234", confirm_password="owner-password-1234"):
        handler = kiosk_server.KioskHandler.__new__(kiosk_server.KioskHandler)
        handler.server = self.server
        handler.client_address = client_address
        handler.read_json = lambda: {"password": password, "confirm_password": confirm_password}
        captured = {"json": None, "error": None}

        def json_response(data, status=200, cookie=None):
            captured["json"] = {"status": status, "data": data, "cookie": cookie}

        def error_response(status, message):
            captured["error"] = {"status": status, "message": message}

        handler.json_response = json_response
        handler.error_response = error_response
        kiosk_server.KioskHandler.setup_owner_password(handler)
        return captured

    def invoke_test_notification_handler(self, *, employee_id=1, test_mode=False, client_address=("127.0.0.1", 1234), payload=None):
        handler = kiosk_server.KioskHandler.__new__(kiosk_server.KioskHandler)
        handler.server = self.server
        handler.client_address = client_address
        handler.read_json = lambda: payload if payload is not None else {"employee_id": employee_id}
        handler.headers = type("Headers", (), {"get": lambda self, key, default=None: ""})()
        captured = {"json": None, "error": None}

        def json_response(data, status=200, cookie=None):
            captured["json"] = {"status": status, "data": data, "cookie": cookie}

        def error_response(status, message):
            captured["error"] = {"status": status, "message": message}

        handler.json_response = json_response
        handler.error_response = error_response
        self.server.app["test_mode"] = test_mode
        kiosk_server.KioskHandler.test_notification(handler)
        self.server.app["test_mode"] = False
        return captured

    def test_password_hashing(self):
        stored = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", stored))
        self.assertFalse(verify_password("wrong", stored))

    @unittest.skipUnless(os.name == "posix", "POSIX file permissions are not available")
    def test_admin_password_setup_preserves_config_permissions(self):
        root = Path(tempfile.mkdtemp())
        config_path = root / "config.json"
        try:
            config_path.write_text(json.dumps({"admin_password_hash": "old-hash"}), encoding="utf-8")
            config_path.chmod(0o640)
            script_path = Path(kiosk_server.ROOT) / "tools" / "set_admin_password.py"
            with patch("getpass.getpass", side_effect=["synthetic-admin-password", "synthetic-admin-password"]), patch.object(sys, "argv", [str(script_path), "--config", str(config_path)]):
                runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o640)
            updated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(verify_password("synthetic-admin-password", updated["admin_password_hash"]))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_debian_environment_reconciler_enforces_safe_flags_and_preserves_secret(self):
        helper_path = Path(kiosk_server.ROOT) / "tools" / "linux" / "configure_kiosk_env.py"
        spec = importlib.util.spec_from_file_location("elite_rf_debian_env", helper_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        root = Path(tempfile.mkdtemp())
        env_file = root / "kiosk.env"
        try:
            env_file.write_text(
                "KIOSK_SESSION_SECRET=synthetic-preserved-secret\n"
                "KIOSK_EXTERNAL_HTTPS=false\n"
                "KIOSK_DISABLE_REMOTE_OWNER_SETUP=false\n"
                "KIOSK_REQUIRE_MOUNTED_BACKUP=false\n"
                "KIOSK_BACKUP_TIMER_MANAGED=false\n"
                "KIOSK_BACKUP_RETENTION=0\n"
                "M365_CLIENT_ID=synthetic-client-id\n",
                encoding="utf-8",
            )
            module.reconcile_environment(env_file)
            values = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertEqual(values["KIOSK_SESSION_SECRET"], "synthetic-preserved-secret")
            self.assertEqual(values["M365_CLIENT_ID"], "synthetic-client-id")
            self.assertEqual(values["KIOSK_EXTERNAL_HTTPS"], "true")
            self.assertEqual(values["KIOSK_DISABLE_REMOTE_OWNER_SETUP"], "true")
            self.assertEqual(values["KIOSK_REQUIRE_MOUNTED_BACKUP"], "true")
            self.assertEqual(values["KIOSK_BACKUP_TIMER_MANAGED"], "true")
            self.assertEqual(values["KIOSK_BACKUP_RETENTION"], "30")
            self.assertEqual(sum(line.startswith("KIOSK_EXTERNAL_HTTPS=") for line in env_file.read_text(encoding="utf-8").splitlines()), 1)
            env_file.write_text("M365_CLIENT_ID=synthetic-client-id\n", encoding="utf-8")
            module.reconcile_environment(env_file)
            regenerated = dict(line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertGreaterEqual(len(regenerated["KIOSK_SESSION_SECRET"]), 40)
            self.assertEqual(regenerated["KIOSK_BACKUP_RETENTION"], "30")
            self.assertEqual(regenerated["KIOSK_EXTERNAL_HTTPS"], "true")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_service_identity_endpoint_identifies_elite_rf(self):
        status, payload, _ = self.request_json("GET", "/api/system/identity")
        self.assertEqual(status, 200)
        self.assertEqual(payload["service"], "elite-rf-visitor-kiosk")
        self.assertEqual(payload["status"], "ok")

    def test_admin_login_locks_repeated_failures_per_client(self):
        self.server.app["login_throttle"] = LoginThrottle(max_failures=2, lock_seconds=300)
        status, _, _ = self.request_json("POST", "/api/admin/login", {"password": "wrong-one"})
        self.assertEqual(status, 401)
        status, payload, _ = self.request_json("POST", "/api/admin/login", {"password": "wrong-two"})
        self.assertEqual(status, 401)
        status, payload, _ = self.request_json("POST", "/api/admin/login", {"password": "wrong-three"})
        self.assertEqual(status, 429)
        self.assertIn("five minutes", payload["error"])
        status, _, _ = self.request_json("POST", "/api/admin/login", {"password": "test-admin-password"})
        self.assertEqual(status, 429)

    def test_admin_login_default_locks_repeated_failures(self):
        for attempt in range(5):
            status, payload, _ = self.request_json("POST", "/api/admin/login", {"password": f"wrong-{attempt}"})
            self.assertEqual(status, 401)
            self.assertEqual(payload["error"], "Wrong password")
        status, payload, _ = self.request_json("POST", "/api/admin/login", {"password": "test-admin-password"})
        self.assertEqual(status, 429)
        self.assertIn("five minutes", payload["error"])

    def test_login_throttle_persists_lockouts_without_storing_client_ip(self):
        state_path = self.tempdir / "login-throttle.sqlite3"
        client_ip = "192.0.2.123"
        first_process = LoginThrottle(
            max_failures=2,
            lock_seconds=300,
            database_path=state_path,
            fingerprint_secret="synthetic-session-secret",
        )
        self.assertEqual(first_process.record_failure(client_ip, now=1000), 0)
        self.assertEqual(first_process.record_failure(client_ip, now=1001), 300)

        restarted_process = LoginThrottle(
            max_failures=2,
            lock_seconds=300,
            database_path=state_path,
            fingerprint_secret="synthetic-session-secret",
        )
        self.assertEqual(restarted_process.retry_after(client_ip, now=1002), 299)
        self.assertNotIn(client_ip.encode(), state_path.read_bytes())
        restarted_process.record_success(client_ip)
        self.assertEqual(restarted_process.retry_after(client_ip, now=1003), 0)

    def test_admin_health_reports_storage_warning_and_critical_thresholds(self):
        cookie = self.login_cookie()
        for free_bytes, expected_status, expected_percent in ((15, "warning", 85), (5, "critical", 95)):
            usage = type("Usage", (), {"total": 100, "used": 100 - free_bytes, "free": free_bytes})()
            with patch("kiosk_server.server.shutil.disk_usage", return_value=usage):
                status, payload, _ = self.request_json("GET", "/api/admin/health", cookie=cookie)
            self.assertEqual(status, 200)
            local_areas = [item for item in payload["storage"] if item["area"] in {"records", "photos", "backups"}]
            self.assertEqual(len(local_areas), 3)
            self.assertTrue(all(item["status"] == expected_status for item in local_areas))
            self.assertTrue(all(item["used_percent"] == expected_percent for item in local_areas))

    def test_admin_health_reports_missing_and_stale_backup_success(self):
        cookie = self.login_cookie()
        status, payload, _ = self.request_json("GET", "/api/admin/health", cookie=cookie)
        self.assertEqual(status, 200)
        freshness = next(item for item in payload["storage"] if item["area"] == "backup freshness")
        self.assertEqual(freshness["status"], "missing")

        marker = self.server.app["root"] / "backups" / ".last-successful-backup"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("old successful backup\n", encoding="ascii")
        old_time = time.time() - 31 * 60 * 60
        os.utime(marker, (old_time, old_time))
        status, payload, _ = self.request_json("GET", "/api/admin/health", cookie=cookie)
        self.assertEqual(status, 200)
        freshness = next(item for item in payload["storage"] if item["area"] == "backup freshness")
        self.assertEqual(freshness["status"], "warning")

        old_time = time.time() - 49 * 60 * 60
        os.utime(marker, (old_time, old_time))
        status, payload, _ = self.request_json("GET", "/api/admin/health", cookie=cookie)
        self.assertEqual(status, 200)
        freshness = next(item for item in payload["storage"] if item["area"] == "backup freshness")
        self.assertEqual(freshness["status"], "critical")

    def test_json_requests_reject_missing_malformed_and_oversized_lengths(self):
        self.assertIn(" 411 ", self.raw_http_request("Content-Type: application/json"))
        self.assertIn(" 400 ", self.raw_http_request("Content-Type: application/json\r\nContent-Length: nope"))
        self.assertIn(" 413 ", self.raw_http_request("Content-Type: application/json\r\nContent-Length: 7000001"))

    def test_json_requests_reject_malformed_json_and_transfer_encoding(self):
        malformed = self.raw_http_request("Content-Type: application/json\r\nContent-Length: 1", b"{")
        self.assertIn(" 400 ", malformed)
        transfer_encoded = self.raw_http_request("Content-Type: application/json\r\nTransfer-Encoding: chunked")
        self.assertIn(" 400 ", transfer_encoded)

    def test_json_request_body_read_timeout_returns_408(self):
        with patch.object(kiosk_server, "REQUEST_READ_TIMEOUT_SECONDS", 0.05):
            response = self.raw_http_request("Content-Type: application/json\r\nContent-Length: 10", b"{")
        self.assertIn(" 408 ", response)

    def test_windows_sensitive_path_protection_uses_explicit_acl(self):
        if os.name != "nt":
            self.skipTest("Windows ACL verification")
        if not shutil.which("icacls.exe") or not shutil.which("powershell.exe"):
            self.skipTest("Windows ACL tools are unavailable on this host")
        root = Path(tempfile.mkdtemp())
        try:
            protected = root / ".env"
            protected.write_text("synthetic-placeholder", encoding="utf-8")
            protected_tree = root / "data"
            nested_directory = protected_tree / "nested"
            nested_directory.mkdir(parents=True)
            nested_file = nested_directory / "visitor.sqlite3"
            nested_file.write_bytes(b"synthetic database")
            helper = Path(kiosk_server.ROOT) / "tools" / "windows_security.ps1"
            escaped_helper = str(helper).replace("'", "''")
            escaped_protected = str(protected).replace("'", "''")
            escaped_tree = str(protected_tree).replace("'", "''")
            escaped_nested = str(nested_file).replace("'", "''")
            command = (
                "$ErrorActionPreference='Stop'; "
                f". '{escaped_helper}'; "
                f"Protect-EliteRfPath '{escaped_protected}'; "
                f"Protect-EliteRfPath '{escaped_tree}'; "
                f"& icacls.exe '{escaped_protected}'; & icacls.exe '{escaped_tree}'; & icacls.exe '{escaped_nested}'; "
                "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-Command", command],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("NT AUTHORITY\\SYSTEM:(F)", result.stdout)
            self.assertIn("BUILTIN\\Administrators:(F)", result.stdout)
            self.assertNotIn("(I)", result.stdout)
            self.assertNotIn("CodexSandboxUsers", result.stdout)
            self.assertEqual(protected.read_text(encoding="utf-8"), "synthetic-placeholder")
            self.assertEqual(nested_file.read_bytes(), b"synthetic database")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_clean_release_package_has_runtime_allowlist_and_sanitized_config(self):
        builder = load_build_release_module()
        output_root = Path(tempfile.mkdtemp())
        extract_root = None
        try:
            release_root, archive = builder.build_release(Path(kiosk_server.ROOT), output_root)
            self.assertTrue(release_root.is_dir())
            self.assertTrue(archive.is_file())
            self.assertEqual(sorted(path.name for path in release_root.iterdir()), [archive.name])
            extract_root, package_root = extract_package_archive(archive)
            self.assertTrue((package_root / "app" / "run.py").is_file())
            self.assertTrue((package_root / "app" / "static" / "kiosk.html").is_file())
            self.assertTrue((package_root / "app" / "kiosk_server" / "pdf_export.py").is_file())
            packaged_requirements = (package_root / "requirements.txt").read_text(encoding="utf-8")
            self.assertIn("reportlab>=4.2.0,<5", packaged_requirements)
            linux_tools = package_root / "app" / "tools" / "linux"
            self.assertTrue((linux_tools / "configure_kiosk_env.py").is_file())
            service = (linux_tools / "elite-rf-visitor-kiosk.service").read_text(encoding="utf-8")
            backup_service = (linux_tools / "elite-rf-visitor-kiosk-backup.service").read_text(encoding="utf-8")
            apache = (linux_tools / "elite-rf-visitor-kiosk.apache.conf").read_text(encoding="utf-8")
            installer = (linux_tools / "install_debian.sh").read_text(encoding="utf-8")
            self.assertIn("--host 127.0.0.1 --port 8080", service)
            self.assertIn("ProtectSystem=strict", service)
            self.assertIn(".restore-in-progress", service)
            self.assertIn(".restore-in-progress", backup_service)
            self.assertIn("ProxyPass        / http://127.0.0.1:8080/", apache)
            self.assertIn("RequestHeader unset X-Forwarded-For", apache)
            self.assertIn("SSLProtocol all -SSLv3 -TLSv1 -TLSv1.1", apache)
            self.assertIn('<LocationMatch "^/(admin|api/admin|photos|oauth/microsoft/callback)(/|$)">', apache)
            self.assertIn('<Location "/api/checkins">', apache)
            self.assertIn("Require ip 192.0.2.0/24", apache)
            self.assertIn("Require ip 198.51.100.10/32", apache)
            self.assertIn("KIOSK_EXTERNAL_HTTPS=true", installer)
            self.assertIn("headers ssl authz_host", installer)
            self.assertIn("10-elite-rf-backup-storage.conf", installer)
            self.assertIn("Path(sys.argv[1]).is_mount()", installer)
            self.assertIn("Daily backups remain disabled", installer)
            self.assertIn('for unit in "$SERVICE_NAME-backup.service"', installer)
            self.assertIn('ReadWritePaths=-%s', installer)
            self.assertIn('RequiresMountsFor=%s', installer)
            self.assertLess(
                installer.index('systemctl start "$SERVICE_NAME-backup.service"'),
                installer.index('systemctl enable --now "$SERVICE_NAME-backup.timer"'),
            )
            self.assertIn('configure_kiosk_env.py" "$ETC_ROOT/kiosk.env"', installer)
            self.assertIn('chown root:"$SERVICE_USER" "$ETC_ROOT"', installer)
            self.assertIn('systemctl restart "$SERVICE_NAME.service"', installer)
            self.assertIn("DEBIAN APACHE DEPLOYMENT section in INSTALL_INSTRUCTIONS.txt", installer)
            self.assertTrue((package_root / "app" / "static" / "elite-rf-logo.png").is_file())
            self.assertTrue((package_root / "app" / "RELEASE_MANIFEST.txt").is_file())
            self.assertEqual(
                sorted(path.name for path in package_root.iterdir()),
                [".env.example", "INSTALL_INSTRUCTIONS.txt", "SET UP ELITE RF ON THIS COMPUTER.bat", "START ELITE RF SAFE TEST MODE.bat", "START ELITE RF VISITOR KIOSK.bat", "app", "requirements.txt"],
            )
            self.assertTrue((package_root / "app" / "tools" / "company_setup.ps1").is_file())
            self.assertTrue((package_root / "app" / "tools" / "restore_backup.py").is_file())
            self.assertFalse((package_root / "tests").exists())
            self.assertFalse((package_root / "docs" / "CHECKPOINT.md").exists())
            self.assertFalse((package_root / ".env").exists())
            config = json.loads((package_root / "app" / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["employees"], [])
            self.assertTrue(verify_password("change-me-before-use", config["admin_password_hash"]))
            with zipfile.ZipFile(archive) as package_zip:
                names = package_zip.namelist()
                unpacked_size = sum(entry.file_size for entry in package_zip.infolist())
            self.assertLessEqual(len(names), builder.MAX_PACKAGE_FILES)
            self.assertLessEqual(unpacked_size, builder.MAX_PACKAGE_BYTES)
            self.assertNotIn("tests/test_app.py", names)
            self.assertNotIn(".env", names)
            self.assertNotIn("docs/CHECKPOINT.md", names)
            self.assertNotIn("Elite-RF-Visitor-Kiosk/tests/test_app.py", names)
            self.assertNotIn("Elite-RF-Visitor-Kiosk/.env", names)
            self.assertNotIn("Elite-RF-Visitor-Kiosk/docs/CHECKPOINT.md", names)
            self.assertNotIn("Elite-RF-Visitor-Kiosk", names)
            self.assertIn("app/run.py", names)
            self.assertTrue(all(".codex" not in name.lower() for name in names))
        finally:
            if extract_root is not None:
                shutil.rmtree(extract_root, ignore_errors=True)
            shutil.rmtree(output_root, ignore_errors=True)

    def test_clean_release_builder_rejects_missing_required_file(self):
        builder = load_build_release_module()
        source_root = Path(tempfile.mkdtemp())
        output_root = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(FileNotFoundError):
                builder.build_release(source_root, output_root)
        finally:
            shutil.rmtree(source_root, ignore_errors=True)
            shutil.rmtree(output_root, ignore_errors=True)

    def test_clean_release_builder_excludes_stray_files_and_rejects_populated_example_credentials(self):
        builder = load_build_release_module()
        source_root = Path(tempfile.mkdtemp())
        output_root = Path(tempfile.mkdtemp())
        extract_root = None
        try:
            create_minimal_release_source(source_root)
            (source_root / "static" / "stray-private.key").write_bytes(b"-----BEGIN PRIVATE KEY-----\nnot-real\n")
            (source_root / "kiosk_server" / "debug-export.db").write_bytes(b"not-a-database")
            release_root, archive = builder.build_release(source_root, output_root)
            self.assertTrue(release_root.is_dir())
            self.assertTrue(archive.is_file())
            extract_root, package_root = extract_package_archive(archive)
            self.assertFalse((package_root / "app" / "static" / "stray-private.key").exists())
            self.assertFalse((package_root / "app" / "kiosk_server" / "debug-export.db").exists())

            example = source_root / ".env.example"
            example.write_text(
                "M365_CLIENT_ID=do-not-package-this\nKIOSK_SESSION_SECRET=do-not-package-this-either\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as error:
                builder.build_release(source_root, output_root)
            self.assertNotIn("do-not-package-this", str(error.exception))
            self.assertNotIn("do-not-package-this-either", str(error.exception))
        finally:
            if extract_root is not None:
                shutil.rmtree(extract_root, ignore_errors=True)
            shutil.rmtree(source_root, ignore_errors=True)
            shutil.rmtree(output_root, ignore_errors=True)

    def test_clean_release_builder_preserves_previous_release_on_validation_failure(self):
        builder = load_build_release_module()
        source_root = Path(tempfile.mkdtemp())
        output_root = Path(tempfile.mkdtemp())
        baseline_extract_root = None
        current_extract_root = None
        next_extract_root = None
        try:
            create_minimal_release_source(source_root)
            release_root, archive = builder.build_release(source_root, output_root)
            baseline_extract_root, baseline_package_root = extract_package_archive(archive)
            baseline_tree = snapshot_file_tree(baseline_package_root)
            baseline_archive = archive.read_bytes()
            baseline_children = [archive.name]

            example = source_root / ".env.example"
            example.write_text(
                "M365_CLIENT_ID=do-not-package-this\nKIOSK_SESSION_SECRET=do-not-package-this-either\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as error:
                builder.build_release(source_root, output_root)
            self.assertNotIn("do-not-package-this", str(error.exception))
            self.assertNotIn("do-not-package-this-either", str(error.exception))
            current_extract_root, current_package_root = extract_package_archive(archive)
            self.assertEqual(snapshot_file_tree(current_package_root), baseline_tree)
            self.assertEqual(archive.read_bytes(), baseline_archive)
            self.assertEqual(sorted(child.name for child in output_root.iterdir()), baseline_children)

            example.write_text("# safe placeholder\n", encoding="utf-8")
            admin_css = source_root / "static" / "admin.css"
            original_css = admin_css.read_text(encoding="utf-8")
            admin_css.write_text(
                original_css + "\n/* -----BEGIN PRIVATE KEY----- synthetic -----END PRIVATE KEY----- */\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                builder.build_release(source_root, output_root)
            next_extract_root, next_package_root = extract_package_archive(archive)
            self.assertEqual(snapshot_file_tree(next_package_root), baseline_tree)
            self.assertEqual(archive.read_bytes(), baseline_archive)
            self.assertEqual(sorted(child.name for child in output_root.iterdir()), baseline_children)
        finally:
            if baseline_extract_root is not None:
                shutil.rmtree(baseline_extract_root, ignore_errors=True)
            if current_extract_root is not None:
                shutil.rmtree(current_extract_root, ignore_errors=True)
            if next_extract_root is not None:
                shutil.rmtree(next_extract_root, ignore_errors=True)
            shutil.rmtree(source_root, ignore_errors=True)
            shutil.rmtree(output_root, ignore_errors=True)

    def test_clean_release_builder_rejects_output_inside_supplied_source(self):
        builder = load_build_release_module()
        source_root = Path(tempfile.mkdtemp())
        try:
            create_minimal_release_source(source_root)
            with self.assertRaises(ValueError):
                builder.build_release(source_root, source_root / "release")
        finally:
            shutil.rmtree(source_root, ignore_errors=True)

    def test_company_setup_check_only_does_not_create_or_print_private_settings(self):
        parent = Path(tempfile.mkdtemp())
        project = parent / "CompanyInstall" / "app"
        tools = project / "tools"
        try:
            tools.mkdir(parents=True)
            setup_source = Path(kiosk_server.ROOT) / "tools" / "company_setup.ps1"
            (tools / "company_setup.ps1").write_text(setup_source.read_text(encoding="utf-8"), encoding="utf-8")
            security_source = Path(kiosk_server.ROOT) / "tools" / "windows_security.ps1"
            (tools / "windows_security.ps1").write_text(security_source.read_text(encoding="utf-8"), encoding="utf-8")
            (tools / "python_runtime.ps1").write_text(textwrap.dedent(f'''\
function Find-KioskPython([string]$Root) {{
  return @{{ File = '{sys.executable.replace("'", "''")}'; Args = @() }}
}}
'''), encoding="utf-8")
            for relative in (
                "run.py", "config.json", "kiosk_server/server.py", "static/kiosk.html",
                "tools/diagnose.ps1", "tools/install_startup_tasks.ps1",
            ):
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}" if path.name == "config.json" else "placeholder", encoding="utf-8")
            (parent / "CompanyInstall" / ".env.example").write_text(
                "KIOSK_SESSION_SECRET=replace-with-a-long-random-secret\nM365_CLIENT_ID=\n",
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned",
                    "-File", str(tools / "company_setup.ps1"), "-ProjectPath", str(project),
                    "-CheckOnly", "-SkipDiagnostic",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Private company settings", completed.stdout)
            self.assertFalse((parent / "CompanyInstall" / ".env").exists())
            self.assertNotIn("replace-with-a-long-random-secret", completed.stdout + completed.stderr)
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_signed_sessions(self):
        token = sign_session("secret")
        self.assertTrue(verify_session("secret", token))
        self.assertFalse(verify_session("other-secret", token))

    def test_database_visit_lifecycle(self):
        temp = Path(tempfile.mkdtemp())
        try:
            db = Database(temp / "test.sqlite3")
            db.seed_employees([
                {"name": "Test Host", "phone": "", "email": "host@example.com", "alert_method": "email", "active": 1}
            ])
            employee = db.list_active_employees_public()[0]
            edited = db.update_employee(employee["id"], {
                "name": "Updated Host", "phone": "+15555550100", "email": "host@example.com",
                "alert_method": "email", "active": 1,
            })
            self.assertEqual(edited["name"], "Updated Host")
            self.assertEqual(len(db.list_employees_admin()), 1)
            inactive = db.update_employee(employee["id"], {
                "name": "Updated Host", "phone": "+15555550100", "email": "host@example.com",
                "alert_method": "email", "active": 0,
            })
            self.assertEqual(inactive["active"], 0)
            self.assertEqual(db.list_active_employees_public(), [])
            db.update_employee(employee["id"], {
                "name": "Updated Host", "phone": "+15555550100", "email": "host@example.com",
                "alert_method": "email", "active": 1,
            })
            visit_id = db.create_visit({
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": employee["id"],
                "employee_name": employee["name"],
                "kiosk_id": "Main Lobby",
                "photo_filename": "test.jpg",
                "notification_status": "skipped",
                "notification_detail": "test",
            })
            self.assertEqual(len(db.list_visits()), 1)
            db.update_notification(visit_id, "pending", "network unavailable")
            self.assertEqual(len(db.pending_notifications()), 1)
            self.assertTrue(db.update_visit_status(visit_id, "met"))
            with self.assertRaises(sqlite3.IntegrityError):
                with db.connect() as conn:
                    conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
            self.assertEqual(len(db.list_visits()), 1)
            self.assertEqual(db.remove_employee(employee["id"]), "deactivated")
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_legacy_database_migration_preserves_visits_and_installs_guarded_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "visitor-kiosk.sqlite3"
            original = Database(path)
            employee = original.create_employee({
                "name": "Migration Host", "phone": "", "email": "host@example.com",
                "alert_method": "email", "active": 1,
            })
            visit_id = original.create_visit({
                "first_name": "Existing", "last_name": "Visitor", "phone": "",
                "email": "visitor@example.com", "company": "Example Co",
                "employee_id": employee["id"], "employee_name": employee["name"],
                "kiosk_id": "Main Lobby", "photo_filename": "existing.jpg",
                "notification_status": "skipped", "notification_detail": "test",
            })
            with original.connect() as connection:
                connection.execute("DROP TRIGGER visits_delete_requires_admin_operation")
                connection.execute("DROP TABLE pending_photo_deletions")
                connection.execute("DROP INDEX idx_visits_created_at_id")
                connection.execute("DROP INDEX idx_visits_photo_filename")
                connection.execute("DROP INDEX idx_visits_notification_retry")
                connection.execute(
                    "CREATE TRIGGER visits_retain_forever BEFORE DELETE ON visits "
                    "BEGIN SELECT RAISE(ABORT, 'retained'); END"
                )
                connection.execute("PRAGMA user_version = 0")

            migrated = Database(path)
            self.assertEqual(len(migrated.list_visits()), 1)
            with migrated.connect() as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
            deletion = migrated.delete_visit(visit_id)
            self.assertEqual(deletion["visit_id"], visit_id)
            self.assertEqual(migrated.list_visits(), [])

    def test_required_secondary_backup_destination_must_be_mounted(self):
        destination = Path(tempfile.gettempdir()) / "kiosk-secondary-backup-test"
        with patch.dict(os.environ, {"KIOSK_REQUIRE_MOUNTED_BACKUP": "true"}):
            with patch("kiosk_server.backups.os.name", "posix"), patch.object(Path, "is_mount", return_value=False):
                with self.assertRaisesRegex(OSError, "not a mounted filesystem"):
                    kiosk_backups._require_mounted_destination(destination)

    def test_required_secondary_backup_fails_before_creating_local_archive(self):
        temp = Path(tempfile.mkdtemp())
        try:
            destination = temp / "secondary"
            with patch.dict(os.environ, {"KIOSK_REQUIRE_MOUNTED_BACKUP": "true"}):
                with patch.object(kiosk_backups, "_require_mounted_destination", side_effect=OSError("not mounted")):
                    with self.assertRaisesRegex(OSError, "not mounted"):
                        create_backup(temp, copy_destination=destination)
            self.assertFalse((temp / "backups").exists())
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_required_secondary_backup_fails_before_local_archive_when_destination_is_unwritable(self):
        temp = Path(tempfile.mkdtemp())
        try:
            destination = temp / "secondary"
            with patch.dict(os.environ, {"KIOSK_REQUIRE_MOUNTED_BACKUP": "true"}):
                with patch("kiosk_server.backups.os.name", "posix"), \
                        patch("kiosk_server.backups._require_mounted_destination"), \
                        patch("kiosk_server.backups.tempfile.NamedTemporaryFile", side_effect=PermissionError("read-only")):
                    with self.assertRaisesRegex(OSError, "not writable"):
                        create_backup(temp, copy_destination=destination)
            self.assertFalse((temp / "backups").exists())
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_backup_contains_sqlite_snapshot(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "data").mkdir()
            (temp / "storage" / "photos").mkdir(parents=True)
            (temp / "config.json").write_text("{}", encoding="utf-8")
            db = Database(temp / "data" / "visitor-kiosk.sqlite3")
            del db
            gc.collect()
            (temp / "storage" / "photos" / "visitor.jpg").write_bytes(b"jpeg")
            original_tempdir = tempfile.TemporaryDirectory
            with patch(
                "kiosk_server.backups.tempfile.TemporaryDirectory",
                lambda *args, **kwargs: original_tempdir(ignore_cleanup_errors=True),
            ):
                target = create_backup(temp)
            self.assertTrue(target.exists())
            import zipfile
            with zipfile.ZipFile(target) as archive:
                manifest = json.loads(archive.read("BACKUP_MANIFEST.json"))
                self.assertIn("data/visitor-kiosk.sqlite3", archive.namelist())
                self.assertIn("storage/photos/visitor.jpg", archive.namelist())
                self.assertEqual(manifest["photo_count"], 1)
                self.assertTrue(manifest["secrets_included"])
                self.assertFalse(manifest["encrypted"])
                self.assertEqual(
                    set(manifest["files"]),
                    {"config.json", "data/visitor-kiosk.sqlite3", "storage/photos/visitor.jpg"},
                )
            verify_backup(target)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_backup_sync_helpers_flush_completed_files_and_directories(self):
        temp = Path(tempfile.mkdtemp())
        try:
            payload = temp / "completed-backup.zip"
            payload.write_bytes(b"verified backup bytes")
            with patch("kiosk_server.backups.os.fsync", wraps=os.fsync) as fsync:
                kiosk_backups._sync_file(payload)
                kiosk_backups._sync_directory(temp)
            expected_syncs = 2 if os.name == "posix" else 1
            self.assertGreaterEqual(fsync.call_count, expected_syncs)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_backup_rejects_database_visit_with_missing_photo(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "data").mkdir()
            (temp / "storage" / "photos").mkdir(parents=True)
            (temp / "config.json").write_text("{}", encoding="utf-8")
            db = Database(temp / "data" / "visitor-kiosk.sqlite3")
            employee = db.create_employee({
                "name": "Test Host", "phone": "", "email": "host@example.test",
                "alert_method": "email", "active": 1,
            })
            db.create_visit({
                "first_name": "Sample", "last_name": "Visitor", "phone": "", "email": "",
                "company": "Example", "employee_id": employee["id"], "employee_name": "Test Host",
                "kiosk_id": "Test Lobby", "photo_filename": "missing-photo.jpg",
                "notification_status": "pending", "notification_detail": "Waiting to send",
            })

            with self.assertRaisesRegex(ValueError, "missing a photo referenced"):
                create_backup(temp)
            self.assertFalse(list((temp / "backups").glob("*.zip")))
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_backup_includes_recoverable_state_but_excludes_secret_files(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "data").mkdir()
            (temp / "storage" / "photos" / "archive").mkdir(parents=True)
            (temp / "config.json").write_text("{}", encoding="utf-8")
            (temp / "RELEASE_MANIFEST.txt").write_text("release inventory", encoding="utf-8")
            (temp / ".env").write_text("sensitive", encoding="utf-8")
            (temp / "server.key").write_text("private key", encoding="utf-8")
            Database(temp / "data" / "visitor-kiosk.sqlite3")
            (temp / "storage" / "photos" / "one.jpg").write_bytes(b"one")
            (temp / "storage" / "photos" / "archive" / "two.jpg").write_bytes(b"two")

            target = create_backup(temp)
            import zipfile
            with zipfile.ZipFile(target) as archive:
                names = set(archive.namelist())
                self.assertIn("config.json", names)
                self.assertIn("data/visitor-kiosk.sqlite3", names)
                self.assertIn("storage/photos/one.jpg", names)
                self.assertIn("storage/photos/archive/two.jpg", names)
                self.assertIn("RELEASE_MANIFEST.txt", names)
                self.assertNotIn(".env", names)
                self.assertNotIn("server.key", names)
            verify_backup(target)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_secondary_backup_replaces_a_damaged_existing_copy(self):
        temp = Path(tempfile.mkdtemp())
        secondary = Path(tempfile.mkdtemp())
        try:
            (temp / "data").mkdir()
            (temp / "storage" / "photos").mkdir(parents=True)
            (temp / "config.json").write_text("{}", encoding="utf-8")
            Database(temp / "data" / "visitor-kiosk.sqlite3")
            source = create_backup(temp)
            damaged = secondary / source.name
            damaged.write_bytes(b"damaged")

            repaired = ensure_secondary_backup(source, secondary)

            self.assertEqual(repaired.read_bytes(), source.read_bytes())
            verify_backup(repaired)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
            shutil.rmtree(secondary, ignore_errors=True)

    def test_backup_verification_rejects_uninventoried_content(self):
        temp = Path(tempfile.mkdtemp())
        try:
            (temp / "data").mkdir()
            (temp / "storage" / "photos").mkdir(parents=True)
            (temp / "config.json").write_text("{}", encoding="utf-8")
            Database(temp / "data" / "visitor-kiosk.sqlite3")
            target = create_backup(temp)
            import zipfile
            with zipfile.ZipFile(target, "a") as archive:
                archive.writestr("unexpected.txt", "not inventoried")

            with self.assertRaisesRegex(ValueError, "recovery manifest"):
                verify_backup(target)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_verified_backup_retention_and_secondary_copy(self):
        temp = Path(tempfile.mkdtemp())
        secondary = Path(tempfile.mkdtemp())
        try:
            (temp / "data").mkdir()
            (temp / "storage" / "photos").mkdir(parents=True)
            (temp / "config.json").write_text("{}", encoding="utf-8")
            Database(temp / "data" / "visitor-kiosk.sqlite3")
            for _ in range(3):
                target = create_backup(temp, retention_count=2, copy_destination=secondary)
                verify_backup(target)
            self.assertEqual(len(list((temp / "backups").glob("*.zip"))), 2)
            self.assertEqual(len(list(secondary.glob("*.zip"))), 2)
            self.assertFalse(backup_is_due(temp, 24 * 60 * 60))
        finally:
            shutil.rmtree(temp, ignore_errors=True)
            shutil.rmtree(secondary, ignore_errors=True)

    def test_notification_retry_is_delayed_and_stops_at_limit(self):
        visit_id = self.db.create_visit({
            "first_name": "Retry", "last_name": "Visitor", "phone": "", "email": "retry@example.com",
            "company": "Example", "employee_id": self.employee["id"], "employee_name": "Test Host",
            "kiosk_id": "Main Lobby", "photo_filename": "retry.jpg", "notification_status": "pending",
            "notification_detail": "",
        })
        self.mock_send_email.return_value = ("pending", "temporary")
        status, _ = kiosk_server.deliver_notification(
            self.server.app, visit_id, {"email": "retry@example.com"}, ("Subject", "plain", "<p>html</p>")
        )
        self.assertEqual(status, "pending")
        visit = self.db.list_visits()[0]
        self.assertEqual(visit["notification_attempts"], 1)
        self.assertGreater(visit["notification_next_attempt_at"], int(__import__("time").time()))
        self.assertEqual(self.db.pending_notifications(now=visit["notification_next_attempt_at"] - 1), [])
        status, _ = kiosk_server.deliver_notification(
            self.server.app,
            visit_id,
            {"email": "retry@example.com"},
            ("Subject", "plain", "<p>html</p>"),
            prior_attempts=kiosk_server.MAX_NOTIFICATION_ATTEMPTS - 1,
        )
        self.assertEqual(status, "failed")
        self.assertEqual(self.db.list_visits()[0]["notification_status"], "failed")

    def test_graph_rate_limit_is_retryable_without_exposing_response(self):
        error = HTTPError("https://graph.microsoft.com", 429, "secret response", {}, None)
        employee = {"email": "host@example.com"}
        with patch.dict(os.environ, {"M365_SENDER_ADDRESS": "alerts@example.com"}, clear=False), \
             patch.object(kiosk_notifications, "_access_token", return_value="token"), \
             patch.object(kiosk_notifications.urllib.request, "urlopen", side_effect=error):
            status, detail = kiosk_notifications.send_email(employee, "Subject", "plain", "<p>html</p>")
        self.assertEqual(status, "pending")
        self.assertIn("429", detail)
        self.assertNotIn("secret response", detail)

    def test_ambiguous_graph_timeout_is_pending_without_exposing_detail(self):
        employee = {"email": "host@example.com"}
        with patch.dict(os.environ, {"M365_SENDER_ADDRESS": "alerts@example.com"}, clear=False), \
             patch.object(kiosk_notifications, "_access_token", return_value="token"), \
             patch.object(kiosk_notifications.urllib.request, "urlopen", side_effect=TimeoutError("secret detail")):
            status, detail = kiosk_notifications.send_email(employee, "Subject", "plain", "<p>html</p>")
        self.assertEqual(status, "pending")
        self.assertIn("uncertain", detail)
        self.assertNotIn("secret detail", detail)

    def test_supervisor_restarts_failed_backend_with_backoff(self):
        module = load_supervisor_module()
        child_one = type("Child", (), {"wait": lambda self: 2})()
        child_two = type("Child", (), {"wait": lambda self: 0})()
        with patch.object(module.subprocess, "Popen", side_effect=[child_one, child_two]) as popen, patch.object(module.time, "sleep") as sleep:
            result = module.supervise(Path(kiosk_server.ROOT), "0.0.0.0", 8080)
        self.assertEqual(result, 0)
        self.assertEqual(popen.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_database_connections_enable_sqlite_safety_pragmas(self):
        with self.db.connect() as conn:
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(busy_timeout, 5000)
        self.assertEqual(str(journal_mode).lower(), "wal")

    def test_startup_warning_helper_avoids_secret_values(self):
        with self.assertLogs("kiosk_server.server", level="WARNING") as captured:
            kiosk_server.warn_on_unsafe_configuration(
                {
                    "session_secret": "change-me",
                    "cookie_secure": False,
                    "notification_settings": {"enabled": True},
                },
                host="0.0.0.0",
            )
        combined = "\n".join(captured.output)
        self.assertIn("KIOSK_SESSION_SECRET is not set", combined)
        self.assertIn("cookie_secure is disabled", combined)
        self.assertIn("Microsoft 365 email configuration is incomplete", combined)
        self.assertNotIn("change-me", combined)

    def test_create_server_starts_with_logging_and_pragmas_intact(self):
        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def start(self):
                return None

        temp = Path(tempfile.mkdtemp())
        try:
            temp_db = temp / "visitor-kiosk.sqlite3"
            temp_photos = temp / "photos"
            temp_photos.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {
                "KIOSK_SESSION_SECRET": "",
                "M365_TENANT_ID": "",
                "M365_CLIENT_ID": "",
                "M365_SENDER_ADDRESS": "",
            }, clear=False), patch.object(kiosk_server, "load_config", return_value={
                "database_path": str(temp_db),
                "photo_storage_path": str(temp_photos),
                "admin_password_hash": hash_password("server-test-password"),
                "session_secret": "server-test-secret",
                "cookie_secure": False,
                "automatic_backup_hours": 24,
                "notification_settings": {"enabled": False, "message_template": "{visitor_name}"},
                "employees": [],
            }), patch.object(kiosk_server, "load_local_env", return_value=None), patch.object(kiosk_server, "retry_pending_notifications", return_value=None) as retry_pending, patch.object(kiosk_server, "create_backup", return_value=temp / "backup.zip"), patch.object(kiosk_server.threading, "Thread", FakeThread):
                with self.assertLogs("kiosk_server.server", level="WARNING"):
                    server = kiosk_server.create_server(host="127.0.0.1", port=0)
                try:
                    self.assertTrue(server.app["db"].path.exists())
                    self.assertEqual(server.app["login_throttle"]._database_path, server.app["db"].path)
                    retry_pending.assert_not_called()
                finally:
                    server.server_close()
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_https_wrapping_requires_both_tls_files(self):
        temp = Path(tempfile.mkdtemp())
        try:
            cert = temp / "server.crt"
            key = temp / "server.key"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("private-key", encoding="utf-8")
            with patch.object(kiosk_server, "load_config", return_value={
                "database_path": "visitor-kiosk.sqlite3",
                "photo_storage_path": "photos",
                "admin_password_hash": hash_password("server-test-password"),
                "session_secret": "server-test-secret",
                "cookie_secure": False,
                "automatic_backup_hours": 24,
                "notification_settings": {"enabled": False, "message_template": "{visitor_name}"},
                "employees": [],
            }), patch.object(kiosk_server, "load_local_env", return_value=None), patch.object(kiosk_server, "retry_pending_notifications", return_value=None), patch.object(kiosk_server, "create_backup", return_value=temp / "backup.zip"), patch.object(kiosk_server.threading, "Thread", lambda *args, **kwargs: type("T", (), {"start": lambda self: None})()), patch.object(ssl.SSLContext, "load_cert_chain", return_value=None) as load_cert_chain, patch.object(ssl.SSLContext, "wrap_socket", side_effect=lambda sock, **kwargs: sock) as wrap_socket:
                server = kiosk_server.create_server(host="0.0.0.0", port=0, runtime_root=temp, test_mode=False, tls_cert_path=cert, tls_key_path=key)
            try:
                load_cert_chain.assert_called_once()
                wrap_socket.assert_called_once()
                self.assertTrue(server.app["tls_enabled"])
            finally:
                server.server_close()
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_production_remote_http_is_rejected_before_binding(self):
        temp = Path(tempfile.mkdtemp())
        try:
            with patch.object(kiosk_server, "ThreadingHTTPServer") as http_server:
                with self.assertRaisesRegex(ValueError, "cannot bind to a non-loopback address without TLS"):
                    kiosk_server.create_server(host="0.0.0.0", port=0, runtime_root=temp, test_mode=False)
            http_server.assert_not_called()
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_https_configuration_rejects_invalid_tls_contents_before_binding(self):
        temp = Path(tempfile.mkdtemp())
        try:
            cert = temp / "server.crt"
            key = temp / "server.key"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("private-key", encoding="utf-8")
            with patch.object(ssl.SSLContext, "load_cert_chain", side_effect=ssl.SSLError("bad cert")) as load_cert_chain, patch.object(kiosk_server, "ThreadingHTTPServer") as http_server:
                with self.assertRaises(ValueError) as captured:
                    kiosk_server.create_server(host="0.0.0.0", port=0, runtime_root=temp, test_mode=False, tls_cert_path=cert, tls_key_path=key)
            self.assertIn("Could not load the TLS certificate and private key", str(captured.exception))
            load_cert_chain.assert_called_once()
            http_server.assert_not_called()
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_https_configuration_rejects_missing_partner_file(self):
        temp = Path(tempfile.mkdtemp())
        try:
            cert = temp / "server.crt"
            cert.write_text("certificate", encoding="utf-8")
            with self.assertRaises(ValueError) as captured:
                kiosk_server.create_server(host="0.0.0.0", port=0, runtime_root=temp, test_mode=False, tls_cert_path=cert, tls_key_path=None)
            self.assertIn("Both TLS certificate and private key", str(captured.exception))
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_https_configuration_rejects_missing_tls_files_clearly(self):
        temp = Path(tempfile.mkdtemp())
        try:
            with patch.object(kiosk_server, "load_config", return_value={
                "database_path": "visitor-kiosk.sqlite3",
                "photo_storage_path": "photos",
                "admin_password_hash": hash_password("server-test-password"),
                "session_secret": "server-test-secret",
                "cookie_secure": False,
                "automatic_backup_hours": 24,
                "notification_settings": {"enabled": False, "message_template": "{visitor_name}"},
                "employees": [],
            }), patch.object(kiosk_server, "load_local_env", return_value=None), patch.object(kiosk_server, "retry_pending_notifications", return_value=None), patch.object(kiosk_server, "create_backup", return_value=temp / "backup.zip"), patch.object(kiosk_server.threading, "Thread", lambda *args, **kwargs: type("T", (), {"start": lambda self: None})()):
                with self.assertRaises(ValueError) as captured:
                    kiosk_server.create_server(host="0.0.0.0", port=0, runtime_root=temp, test_mode=False, tls_cert_path=temp / "missing.crt", tls_key_path=temp / "missing.key")
            self.assertIn("TLS certificate file not found", str(captured.exception))
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_https_wrap_failure_closes_listener_and_skips_background_work(self):
        temp = Path(tempfile.mkdtemp())
        try:
            cert = temp / "server.crt"
            key = temp / "server.key"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("private-key", encoding="utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]

            with patch.object(kiosk_server, "load_config", return_value={
                "database_path": "visitor-kiosk.sqlite3",
                "photo_storage_path": "photos",
                "admin_password_hash": hash_password("server-test-password"),
                "session_secret": "server-test-secret",
                "cookie_secure": False,
                "automatic_backup_hours": 24,
                "notification_settings": {"enabled": False, "message_template": "{visitor_name}"},
                "employees": [],
            }), patch.object(kiosk_server, "load_local_env", return_value=None), patch.object(kiosk_server, "retry_pending_notifications") as retry_pending, patch.object(kiosk_server, "create_backup") as create_backup_mock, patch.object(kiosk_server.threading, "Thread") as thread_mock, patch.object(ssl.SSLContext, "load_cert_chain", return_value=None), patch.object(ssl.SSLContext, "wrap_socket", side_effect=RuntimeError("wrap failed")):
                with self.assertRaises(ValueError) as captured:
                    kiosk_server.create_server(host="127.0.0.1", port=port, runtime_root=temp, test_mode=False, tls_cert_path=cert, tls_key_path=key)

            self.assertIn("Could not enable TLS on the listening socket", str(captured.exception))
            self.assertIsInstance(captured.exception.__cause__, RuntimeError)
            retry_pending.assert_not_called()
            create_backup_mock.assert_not_called()
            thread_mock.assert_not_called()

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check_socket:
                check_socket.bind(("127.0.0.1", port))
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_test_mode_rejects_tls_configuration(self):
        parent = Path(tempfile.mkdtemp())
        runtime = parent / "runtime"
        try:
            kiosk_run.create_test_root(runtime)
            cert = parent / "server.crt"
            key = parent / "server.key"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("private-key", encoding="utf-8")
            with self.assertRaises(ValueError) as captured:
                kiosk_server.create_server("127.0.0.1", 0, runtime_root=runtime, test_mode=True, tls_cert_path=cert, tls_key_path=key)
            self.assertIn("HTTP-only", str(captured.exception))
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_public_hostname_is_used_for_https_display_urls(self):
        self.assertEqual(kiosk_run.display_host_for_launch("0.0.0.0", True, "kiosk.company.local"), "kiosk.company.local")
        self.assertEqual(kiosk_run.display_host_for_launch("0.0.0.0", False, "kiosk.company.local"), "127.0.0.1")

    def test_external_https_launch_url_uses_apache_origin_not_backend_port(self):
        self.assertEqual(
            kiosk_run.format_launch_base_url("https", "kiosk.company.local", 8080, external_https=True),
            "https://kiosk.company.local",
        )
        self.assertEqual(
            kiosk_run.format_launch_base_url("https", "kiosk.company.local", 8443, tls_enabled=True),
            "https://kiosk.company.local:8443",
        )
        self.assertEqual(
            kiosk_run.format_launch_base_url("http", "127.0.0.1", 8080),
            "http://127.0.0.1:8080",
        )

    def test_launcher_imports_dotenv_values_without_printing_them(self):
        temp = Path(tempfile.mkdtemp())
        try:
            env_file = temp / ".env"
            env_file.write_text("KIOSK_TLS_CERT_PATH=C:\\pilot\\server.crt\nKIOSK_TLS_KEY_PATH=C:\\pilot\\server.key\nKIOSK_PUBLIC_HOSTNAME=kiosk.company.local\n", encoding="utf-8")
            helper = (Path(kiosk_server.ROOT) / "tools" / "python_runtime.ps1").resolve()
            command = (
                f". '{helper}'; "
                f"Import-KioskEnvironment '{env_file}'; "
                "if ($env:KIOSK_TLS_CERT_PATH -ne 'C:\\pilot\\server.crt' -or "
                "$env:KIOSK_TLS_KEY_PATH -ne 'C:\\pilot\\server.key' -or "
                "$env:KIOSK_PUBLIC_HOSTNAME -ne 'kiosk.company.local') { exit 1 }"
            )
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-Command", command], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("server.crt", result.stdout + result.stderr)
            self.assertNotIn("server.key", result.stdout + result.stderr)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_run_main_loads_dotenv_before_tls_resolution_in_production(self):
        events = []
        fake_server = type("FakeServer", (), {})()
        fake_server.server_port = 8443
        fake_server.app = {
            "config": {
                "admin_password_hash": hash_password("prod-admin-password"),
                "session_secret": "prod-session-secret",
                "cookie_secure": False,
            },
            "tls_enabled": False,
        }
        fake_server.serve_forever = lambda: None
        fake_server.server_close = lambda: None
        captured = {}

        def load_env():
            events.append("load_env")
            os.environ["KIOSK_TLS_CERT_PATH"] = r"C:\pilot\server.crt"
            os.environ["KIOSK_TLS_KEY_PATH"] = r"C:\pilot\server.key"
            os.environ["KIOSK_PUBLIC_HOSTNAME"] = "kiosk.company.local"

        def create_server(host, port, runtime_root=None, test_mode=False, tls_cert_path=None, tls_key_path=None):
            events.append("create_server")
            captured.update({
                "host": host,
                "port": port,
                "runtime_root": runtime_root,
                "test_mode": test_mode,
                "tls_cert_path": tls_cert_path,
                "tls_key_path": tls_key_path,
            })
            return fake_server

        args = argparse.Namespace(host="0.0.0.0", port=8080, runtime_root=None, test_mode=False, tls_cert=None, tls_key=None)
        with patch.dict(os.environ, {}, clear=True), patch.object(kiosk_run.argparse.ArgumentParser, "parse_args", return_value=args), patch.object(kiosk_run, "load_local_env", side_effect=load_env) as load_env_mock, patch.object(kiosk_run, "create_server", side_effect=create_server):
            kiosk_run.main()
        load_env_mock.assert_called_once()
        self.assertEqual(events[:2], ["load_env", "create_server"])
        self.assertEqual(captured["tls_cert_path"], Path(r"C:\pilot\server.crt"))
        self.assertEqual(captured["tls_key_path"], Path(r"C:\pilot\server.key"))

    def test_run_main_skips_dotenv_and_tls_env_in_test_mode(self):
        parent = Path(tempfile.mkdtemp())
        runtime = parent / "runtime"
        events = []
        fake_server = type("FakeServer", (), {})()
        fake_server.server_port = 8123
        fake_server.app = {"config": {"admin_password_hash": hash_password("test-admin-password"), "session_secret": "test-session-secret", "cookie_secure": False}, "tls_enabled": False}
        fake_server.serve_forever = lambda: None
        fake_server.server_close = lambda: None
        captured = {}

        def load_env():
            events.append("load_env")

        def create_server(host, port, runtime_root=None, test_mode=False, tls_cert_path=None, tls_key_path=None):
            events.append("create_server")
            captured.update({
                "host": host,
                "port": port,
                "runtime_root": runtime_root,
                "test_mode": test_mode,
                "tls_cert_path": tls_cert_path,
                "tls_key_path": tls_key_path,
            })
            return fake_server

        args = argparse.Namespace(host="127.0.0.1", port=8080, runtime_root=runtime, test_mode=True, tls_cert=None, tls_key=None)
        try:
            with patch.dict(os.environ, {
                "KIOSK_TLS_CERT_PATH": r"C:\pilot\server.crt",
                "KIOSK_TLS_KEY_PATH": r"C:\pilot\server.key",
                "KIOSK_PUBLIC_HOSTNAME": "kiosk.company.local",
            }, clear=True), patch.object(kiosk_run.argparse.ArgumentParser, "parse_args", return_value=args), patch.object(kiosk_run, "load_local_env", side_effect=load_env) as load_env_mock, patch.object(kiosk_run, "create_server", side_effect=create_server):
                kiosk_run.main()
            load_env_mock.assert_not_called()
            self.assertEqual(events, ["create_server"])
            self.assertIsNone(captured["tls_cert_path"])
            self.assertIsNone(captured["tls_key_path"])
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_run_main_open_admin_uses_started_server_url(self):
        fake_server = type("FakeServer", (), {})()
        fake_server.server_port = 8123
        fake_server.app = {"config": {"admin_password_hash": hash_password("admin"), "session_secret": "secret", "cookie_secure": False}}
        fake_server.serve_forever = lambda: None
        fake_server.server_close = lambda: None
        args = argparse.Namespace(host="127.0.0.1", port=0, runtime_root=None, test_mode=False, tls_cert=None, tls_key=None, open_admin=True, pause_on_error=False)
        with patch.dict(os.environ, {}, clear=True), patch.object(kiosk_run.argparse.ArgumentParser, "parse_args", return_value=args), patch.object(kiosk_run, "load_local_env"), patch.object(kiosk_run, "create_server", return_value=fake_server), patch.object(kiosk_run.webbrowser, "open", return_value=True) as open_browser:
            kiosk_run.main()
        open_browser.assert_called_once_with("http://127.0.0.1:8123/admin")

    def test_run_main_pause_on_error_keeps_startup_failure_readable(self):
        args = argparse.Namespace(host="127.0.0.1", port=0, runtime_root=None, test_mode=False, tls_cert=None, tls_key=None, open_admin=False, pause_on_error=True)
        with patch.dict(os.environ, {}, clear=True), patch.object(kiosk_run.argparse.ArgumentParser, "parse_args", return_value=args), patch.object(kiosk_run, "load_local_env"), patch.object(kiosk_run, "create_server", side_effect=ValueError("synthetic startup failure")), patch("builtins.input", return_value="") as pause:
            with self.assertRaises(SystemExit) as raised:
                kiosk_run.main()
        self.assertEqual(raised.exception.code, 1)
        pause.assert_called_once_with("Press Enter to close.")

    def test_run_main_pause_on_error_keeps_test_mode_host_validation_readable(self):
        args = argparse.Namespace(host="0.0.0.0", port=0, runtime_root=None, test_mode=True, tls_cert=None, tls_key=None, open_admin=False, pause_on_error=True)
        with patch.object(kiosk_run.argparse.ArgumentParser, "parse_args", return_value=args), patch.object(kiosk_run, "create_test_root") as create_test_root, patch("builtins.input", return_value="") as pause:
            with self.assertRaises(SystemExit) as raised:
                kiosk_run.main()
        self.assertEqual(raised.exception.code, 1)
        create_test_root.assert_not_called()
        pause.assert_called_once_with("Press Enter to close.")

    def test_run_main_pause_on_error_keeps_incomplete_tls_validation_readable(self):
        args = argparse.Namespace(
            host="0.0.0.0",
            port=8080,
            runtime_root=None,
            test_mode=False,
            tls_cert=Path(r"C:\pilot\server.crt"),
            tls_key=None,
            open_admin=False,
            pause_on_error=True,
        )
        with patch.dict(os.environ, {}, clear=True), patch.object(kiosk_run.argparse.ArgumentParser, "parse_args", return_value=args), patch.object(kiosk_run, "load_local_env"), patch.object(kiosk_run, "create_server") as create_server, patch("builtins.input", return_value="") as pause:
            with self.assertRaises(SystemExit) as raised:
                kiosk_run.main()
        self.assertEqual(raised.exception.code, 1)
        create_server.assert_not_called()
        pause.assert_called_once_with("Press Enter to close.")

    def test_find_python_rejects_codex_and_unsigned_candidates_behaviorally(self):
        module = load_create_shortcuts_module()
        temp = Path(tempfile.mkdtemp())
        try:
            codex_python = temp / ".codex" / "runtime" / "python.exe"
            codex_python.parent.mkdir(parents=True, exist_ok=True)
            codex_python.write_text("", encoding="utf-8")
            unsigned_python = temp / "UnsignedPython" / "python.exe"
            unsigned_python.parent.mkdir(parents=True, exist_ok=True)
            unsigned_python.write_text("", encoding="utf-8")
            signed_python = temp / "SignedPython" / "python.exe"
            signed_python.parent.mkdir(parents=True, exist_ok=True)
            signed_python.write_text("", encoding="utf-8")

            with patch.object(module.sys, "executable", str(codex_python)), patch.object(module.shutil, "which", side_effect=[str(unsigned_python), str(signed_python)]):
                with patch.object(module, "has_supported_python_version", return_value=True), patch.object(module, "authenticode_details", side_effect=[("NotSigned", ""), ("Valid", "Python Software Foundation")]):
                    selected = module.find_python(None)
            self.assertEqual(selected, signed_python.resolve())
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_find_python_rejects_explicit_codex_candidate(self):
        module = load_create_shortcuts_module()
        temp = Path(tempfile.mkdtemp())
        try:
            explicit_codex = temp / ".codex" / "runtime" / "python.exe"
            explicit_codex.parent.mkdir(parents=True, exist_ok=True)
            explicit_codex.write_text("", encoding="utf-8")
            signed_python = temp / "SignedPython" / "python.exe"
            signed_python.parent.mkdir(parents=True, exist_ok=True)
            signed_python.write_text("", encoding="utf-8")

            with patch.object(module.sys, "executable", str(signed_python)), patch.object(module.shutil, "which", return_value=None):
                with patch.object(module, "has_supported_python_version", return_value=True), patch.object(module, "authenticode_details", return_value=("Valid", "Python Software Foundation")):
                    selected = module.find_python(explicit_codex)
            self.assertEqual(selected, signed_python.resolve())
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_find_python_requires_signed_non_codex_python(self):
        module = load_create_shortcuts_module()
        temp = Path(tempfile.mkdtemp())
        try:
            unsigned_python = temp / "UnsignedPython" / "python.exe"
            unsigned_python.parent.mkdir(parents=True, exist_ok=True)
            unsigned_python.write_text("", encoding="utf-8")
            with patch.object(module.sys, "executable", str(unsigned_python)), patch.object(module.shutil, "which", return_value=None):
                with patch.object(module, "has_supported_python_version", return_value=True), patch.object(module, "authenticode_details", return_value=("NotSigned", "")):
                    with self.assertRaises(RuntimeError) as captured:
                        module.find_python(None)
            self.assertIn("signed non-Codex Python 3.11+", str(captured.exception))
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_install_startup_tasks_use_canonical_launcher_and_discovered_python(self):
        parent = Path(tempfile.mkdtemp())
        project = parent / "project"
        tools = project / "tools"
        tools.mkdir(parents=True)
        installer = tools / "install_startup_tasks.ps1"
        installer.write_text((Path(kiosk_server.ROOT) / "tools" / "install_startup_tasks.ps1").read_text(encoding="utf-8"), encoding="utf-8")
        (tools / "python_runtime.ps1").write_text(textwrap.dedent('''
function Find-KioskPython([string]$Root) {
  return @{ File = 'C:\\Python312\\python.exe'; Args = @() }
}
function Import-KioskEnvironment([string]$Path) {
}
'''), encoding="utf-8")
        wrapper = parent / "invoke.ps1"
        wrapper.write_text(textwrap.dedent(f'''
$ErrorActionPreference = 'Stop'
$global:Actions = @()
function New-ScheduledTaskAction {{
  param([string]$Execute, [string]$Argument, [string]$WorkingDirectory)
  [pscustomobject]@{{ Execute = $Execute; Argument = $Argument; WorkingDirectory = $WorkingDirectory }}
}}
function New-ScheduledTaskTrigger {{
  param([switch]$AtStartup, [switch]$Daily, [datetime]$At)
  [pscustomobject]@{{ AtStartup = $AtStartup.IsPresent; Daily = $Daily.IsPresent; At = $At }}
}}
function New-ScheduledTaskSettingsSet {{
  param([switch]$StartWhenAvailable, [int]$RestartCount, [timespan]$RestartInterval, [string]$MultipleInstances, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries)
  [pscustomobject]@{{ RestartCount = $RestartCount; StartWhenAvailable = $StartWhenAvailable.IsPresent; MultipleInstances = $MultipleInstances }}
}}
function New-ScheduledTaskPrincipal {{
  param([string]$UserId, [string]$LogonType, [string]$RunLevel)
  [pscustomobject]@{{ UserId = $UserId; LogonType = $LogonType; RunLevel = $RunLevel }}
}}
function Register-ScheduledTask {{
  param($TaskName, $Action, $Trigger, $Settings, $Principal, [switch]$Force)
  $global:Actions = @($global:Actions) + [pscustomobject]@{{
    TaskName = $TaskName
    Execute = $Action.Execute
    Argument = $Action.Argument
    WorkingDirectory = $Action.WorkingDirectory
    RestartCount = $Settings.RestartCount
    StartWhenAvailable = $Settings.StartWhenAvailable
    MultipleInstances = $Settings.MultipleInstances
    UserId = $Principal.UserId
    LogonType = $Principal.LogonType
  }}
}}
& '{installer}' -ProjectPath '{project}'
Write-Output ('TASKS:' + ($global:Actions | ConvertTo-Json -Compress))
'''), encoding="utf-8")
        try:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File", str(wrapper)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("TASKS:", result.stdout)
            payload_text = result.stdout.rsplit("TASKS:", 1)[1].strip()
            self.assertTrue(payload_text, result.stdout)
            payload = json.loads(payload_text)
            startup = next(item for item in payload if item["TaskName"] == "Elite RF Visitor Kiosk")
            backup = next(item for item in payload if item["TaskName"] == "Elite RF Visitor Kiosk Backup")
            self.assertEqual(startup["Execute"], r"C:\Python312\python.exe")
            self.assertIn("supervise.py", startup["Argument"])
            self.assertNotIn("-3", startup["Argument"])
            self.assertEqual(startup["WorkingDirectory"], str(project))
            self.assertEqual(startup["RestartCount"], 999)
            self.assertTrue(startup["StartWhenAvailable"])
            self.assertEqual(startup["MultipleInstances"], "IgnoreNew")
            self.assertEqual(startup["UserId"], r"NT AUTHORITY\SYSTEM")
            self.assertEqual(startup["LogonType"], "ServiceAccount")
            self.assertEqual(backup["Execute"], r"C:\Python312\python.exe")
            self.assertIn("backup.py", backup["Argument"])
            self.assertNotIn("-3", backup["Argument"])
            self.assertEqual(backup["WorkingDirectory"], str(project))
            self.assertEqual(backup["UserId"], r"NT AUTHORITY\SYSTEM")
        finally:
            shutil.rmtree(parent, ignore_errors=True)
    def test_session_cookie_is_secure_when_tls_is_active(self):
        handler = kiosk_server.KioskHandler.__new__(kiosk_server.KioskHandler)
        handler.server = self.server
        handler.server.app["tls_enabled"] = True
        cookie = kiosk_server.KioskHandler.session_cookie(handler, "token-value")
        self.assertIn("Secure", cookie)
        handler.server.app["tls_enabled"] = False

    def test_create_server_test_mode_uses_isolated_root_and_ephemeral_port(self):
        parent = Path(tempfile.mkdtemp())
        temp = parent / "runtime"
        project_db = Path(kiosk_server.ROOT) / "data" / "visitor-kiosk.sqlite3"
        project_db_snapshot = project_db.stat().st_mtime_ns if project_db.exists() else None
        try:
            kiosk_run.create_test_root(temp)
            server = kiosk_server.create_server("127.0.0.1", 0, runtime_root=temp, test_mode=True)
            try:
                self.assertEqual(server.server_address[0], "127.0.0.1")
                self.assertNotEqual(server.server_port, 0)
                self.assertTrue(server.app["test_mode"])
                self.assertEqual(server.app["root"], temp.resolve())
                self.assertTrue((temp / "data" / "visitor-kiosk.sqlite3").exists())
                self.assertTrue((temp / "storage" / "photos").exists())
                if project_db_snapshot is None:
                    self.assertFalse(project_db.exists())
                else:
                    self.assertEqual(project_db.stat().st_mtime_ns, project_db_snapshot)
            finally:
                server.server_close()
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_test_mode_configuration_is_synthetic(self):
        root = kiosk_run.create_test_root()
        try:
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["database_path"], "data/visitor-kiosk.sqlite3")
            self.assertEqual(config["photo_storage_path"], "storage/photos")
            self.assertEqual(config["employees"][0]["name"], "Test Host")
            self.assertEqual(config["employees"][0]["email"], "test-host@example.invalid")
            self.assertFalse(config["notification_settings"]["enabled"])
            self.assertNotEqual(config["session_secret"], self.config["session_secret"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_test_mode_accepts_existing_disposable_runtime_root(self):
        parent = Path(tempfile.mkdtemp())
        root = parent / "runtime"
        try:
            prepared = kiosk_run.create_test_root(root)
            self.assertEqual(prepared.resolve(), root.resolve())
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["employees"][0]["name"], "Test Host")
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_test_mode_rejects_existing_unmarked_empty_root_without_changes(self):
        root = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(ValueError):
                kiosk_run.create_test_root(root)
            self.assertEqual(list(root.iterdir()), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_test_mode_rejects_existing_unmarked_photo_root_without_changes(self):
        root = Path(tempfile.mkdtemp())
        photo = root / "storage" / "photos" / "visitor.jpg"
        photo.parent.mkdir(parents=True)
        photo.write_bytes(b"synthetic photo")
        try:
            with self.assertRaises(ValueError):
                kiosk_run.create_test_root(root)
            self.assertTrue(photo.exists())
            self.assertFalse((root / kiosk_server.TEST_RUNTIME_MARKER).exists())
            self.assertFalse((root / "config.json").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_test_mode_rejects_unmarked_synthetic_config(self):
        root = kiosk_run.create_test_root()
        marker = root / kiosk_server.TEST_RUNTIME_MARKER
        marker.unlink()
        try:
            with self.assertRaises(ValueError):
                kiosk_run.create_test_root(root)
            self.assertFalse(marker.exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_create_server_rejects_unmarked_runtime_root(self):
        root = kiosk_run.create_test_root()
        marker = root / kiosk_server.TEST_RUNTIME_MARKER
        marker.unlink()
        try:
            with self.assertRaises(ValueError):
                kiosk_server.create_server("127.0.0.1", 0, runtime_root=root, test_mode=True)
            self.assertFalse(marker.exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_test_mode_reuses_marked_root_with_disposable_data(self):
        parent = Path(tempfile.mkdtemp())
        root = parent / "runtime"
        try:
            kiosk_run.create_test_root(root)
            photo = root / "storage" / "photos" / "test-visitor.jpg"
            photo.parent.mkdir(parents=True, exist_ok=True)
            photo.write_bytes(b"synthetic photo")
            server = kiosk_server.create_server("127.0.0.1", 0, runtime_root=root, test_mode=True)
            try:
                self.assertTrue(photo.exists())
                self.assertEqual(server.app["root"], root.resolve())
            finally:
                server.server_close()
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_test_mode_rejects_existing_runtime_root_with_production_config(self):
        root = Path(tempfile.mkdtemp())
        try:
            (root / "config.json").write_text(json.dumps({
                "database_path": "data/visitor-kiosk.sqlite3",
                "photo_storage_path": "storage/photos",
                "admin_password_hash": hash_password("production-admin-password"),
                "session_secret": "production-session-secret",
                "cookie_secure": False,
                "max_photo_bytes": 1_000_000,
                "automatic_backup_hours": 24,
                "notification_settings": {
                    "enabled": True,
                    "provider": "microsoft_graph_email",
                    "message_template": "{visitor_name} from {company} is waiting for you at {location}.",
                },
                "employees": [],
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                kiosk_run.create_test_root(root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_test_mode_rejects_project_root_runtime_root(self):
        with self.assertRaises(ValueError):
            kiosk_server.create_server("127.0.0.1", 0, runtime_root=Path(kiosk_server.ROOT), test_mode=True)

    def test_test_mode_rejects_non_loopback_before_creating_runtime_files(self):
        root = Path(tempfile.mkdtemp()) / "not-created"
        with self.assertRaises(ValueError):
            kiosk_server.create_server("0.0.0.0", 0, runtime_root=root, test_mode=True)
        self.assertFalse(root.exists())

    def test_test_mode_skips_env_loading_and_background_thread(self):
        parent = Path(tempfile.mkdtemp())
        temp = parent / "runtime"
        try:
            kiosk_run.create_test_root(temp)
            with patch.object(kiosk_server, "load_local_env") as load_env, patch.object(kiosk_server.threading, "Thread") as thread:
                server = kiosk_server.create_server("127.0.0.1", 0, runtime_root=temp, test_mode=True)
            try:
                load_env.assert_not_called()
                thread.assert_not_called()
            finally:
                server.server_close()
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_test_mode_ignores_process_managed_admin_and_session_secrets(self):
        parent = Path(tempfile.mkdtemp())
        temp = parent / "runtime"
        try:
            config = json.loads((kiosk_run.create_test_root(temp) / "config.json").read_text(encoding="utf-8"))
            with patch.dict(os.environ, {
                "KIOSK_ADMIN_PASSWORD_HASH": hash_password("production-password-value"),
                "KIOSK_SESSION_SECRET": "production-session-value",
            }, clear=False):
                server = kiosk_server.create_server("127.0.0.1", 0, runtime_root=temp, test_mode=True)
                try:
                    handler = kiosk_server.KioskHandler.__new__(kiosk_server.KioskHandler)
                    handler.server = server
                    handler.read_json = lambda: {"password": "test-owner-password-1234"}
                    captured = {}
                    handler.json_response = lambda data, status=200, cookie=None: captured.update({"data": data, "status": status, "cookie": cookie})
                    handler.error_response = lambda status, message: captured.update({"error": (status, message)})
                    kiosk_server.KioskHandler.login(handler)
                    self.assertEqual(captured["status"], 200)
                    token = captured["cookie"].split(";", 1)[0].split("=", 1)[1]
                    self.assertTrue(verify_session(config["session_secret"], token))
                    self.assertFalse(verify_session("production-session-value", token))
                finally:
                    server.server_close()
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_python_runtime_helper_accepts_signed_python_and_path_python(self):
        temp = Path(tempfile.mkdtemp())
        try:
            candidate = Path(sys.executable).resolve()
            helper = (Path(kiosk_server.ROOT) / "tools" / "python_runtime.ps1").resolve()
            command = textwrap.dedent(f'''
                function Get-AuthenticodeSignature {{ [pscustomobject]@{{ Status = 'Valid' }} }}
                . '{helper}'
                $direct = Get-KioskInterpreter @{{ File = '{candidate}'; Args = @() }}
                if ($null -eq $direct -or $direct.File -ne '{candidate}') {{ exit 1 }}
                $path = Find-KioskPython '{temp}'
                if ($null -eq $path -or $path.File -ne '{candidate}') {{ exit 1 }}
            ''')
            env = os.environ.copy()
            env["PATH"] = f"{candidate.parent};C:\\Windows\\System32;C:\\Windows\\System32\\WindowsPowerShell\\v1.0"
            env["LocalAppData"] = str(temp / "LocalAppData")
            env["ProgramFiles"] = str(temp / "ProgramFiles")
            env["ProgramFiles(x86)"] = str(temp / "ProgramFilesX86")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-Command", command], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_python_runtime_helper_finds_standard_installed_python_without_path(self):
        temp = Path(tempfile.mkdtemp())
        try:
            candidate = Path(sys.executable).resolve()
            fake_root = temp / "LocalAppData" / "Programs" / "Python" / "Python313"
            fake_root.mkdir(parents=True)
            standard_candidate = fake_root / "python.exe"
            standard_candidate.write_text("", encoding="ascii")
            blocked_path = temp / "blocked-path"
            blocked_path.mkdir()
            for command_name in ("py.cmd", "python.cmd"):
                (blocked_path / command_name).write_text("@echo off\r\nexit /b 1\r\n", encoding="ascii")
            helper = (Path(kiosk_server.ROOT) / "tools" / "python_runtime.ps1").resolve()
            command = textwrap.dedent(f'''
                . '{helper}'
                function Get-KioskInterpreter {{
                    param([hashtable]$Candidate)
                    if ($Candidate.File -eq '{standard_candidate}') {{ return @{{ File = '{candidate}'; Args = @() }} }}
                    return $null
                }}
                $result = Find-KioskPython '{temp / 'project'}'
                if ($null -eq $result -or $result.File -ne '{candidate}') {{ exit 1 }}
            ''')
            env = os.environ.copy()
            env["LocalAppData"] = str(fake_root.parents[2])
            env["ProgramFiles"] = str(temp / "ProgramFiles")
            env["ProgramFiles(x86)"] = str(temp / "ProgramFilesX86")
            env["PATH"] = f"{blocked_path};C:\\Windows\\System32;C:\\Windows\\System32\\WindowsPowerShell\\v1.0"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-Command", command], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_python_runtime_helper_rejects_codex_and_unsigned_candidates_behaviorally(self):
        temp = Path(tempfile.mkdtemp())
        try:
            marker = temp / "executed.marker"
            codex = temp / ".codex" / "python.exe"
            codex.parent.mkdir(parents=True)
            codex.write_text(f"marker={marker}\r\n", encoding="ascii")
            wrapper = temp / "python.cmd"
            wrapper.write_text(f"@echo off\r\necho executed>\"{marker}\"\r\nexit /b 0\r\n", encoding="ascii")
            helper = (Path(kiosk_server.ROOT) / "tools" / "python_runtime.ps1").resolve()
            command = textwrap.dedent(f'''
                . '{helper}'
                $result = Get-KioskInterpreter @{{ File = '{codex}'; Args = @() }}
                if ($null -ne $result) {{ exit 1 }}
                $wrapperResult = Find-KioskPython '{temp / 'project'}'
                if ($null -ne $wrapperResult) {{ exit 1 }}
            ''')
            env = os.environ.copy()
            env["LocalAppData"] = str(temp / "LocalAppData")
            env["ProgramFiles"] = str(temp / "ProgramFiles")
            env["ProgramFiles(x86)"] = str(temp / "ProgramFilesX86")
            env["PATH"] = f"{temp};C:\\Windows\\System32;C:\\Windows\\System32\\WindowsPowerShell\\v1.0"
            rejected = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-Command", command], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(rejected.returncode, 0, rejected.stderr)
            self.assertFalse(marker.exists())
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    def test_python_runtime_helper_accepts_signed_py_launcher(self):
        if shutil.which("py") is None:
            self.skipTest("signed py.exe is not installed on this Windows host")
        powershell_probe = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "RemoteSigned",
                "-Command",
                "$py=Get-Command py -CommandType Application -ErrorAction SilentlyContinue; if ($null -eq $py) { exit 1 }",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if powershell_probe.returncode != 0:
            self.skipTest("PowerShell cannot resolve the installed py.exe launcher")
        helper = (Path(kiosk_server.ROOT) / "tools" / "python_runtime.ps1").resolve()
        command = textwrap.dedent(f'''
            . '{helper}'
            $py = Get-Command py -CommandType Application -All -ErrorAction SilentlyContinue | Where-Object {{ $_.Source -like '*\\py.exe' }} | Select-Object -First 1
            if ($null -eq $py) {{ exit 2 }}
            $env:PATH = (Split-Path -Parent $py.Source) + [IO.Path]::PathSeparator + $env:PATH
            $result = Get-KioskInterpreter @{{ File = 'py'; Args = @() }}
            if ($null -eq $result -or $result.File -notlike '*\\python.exe') {{ exit 1 }}
        ''')
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-Command", command], capture_output=True, text=True, timeout=10)
        if result.returncode != 0 and not result.stderr.strip():
            self.skipTest("PowerShell child process could not complete the signed py.exe check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_test_mode_never_calls_email(self):
        app = {"config": self.config, "db": self.db, "test_mode": True}
        with patch.dict(os.environ, {
            "M365_TENANT_ID": "synthetic-tenant",
            "M365_CLIENT_ID": "synthetic-client",
            "M365_SENDER_ADDRESS": "alerts@example.com",
        }, clear=False), patch("kiosk_server.server.send_email") as send_email:
            status, detail = kiosk_server.deliver_notification(app, 1, {"email": "host@example.com"}, ("Subject", "plain", "<p>html</p>"))
        self.assertEqual(status, "skipped")
        self.assertIn("test mode", detail.lower())
        send_email.assert_not_called()

    def test_launcher_browser_opening_is_nonfatal_and_not_start_process(self):
        launcher = (Path(kiosk_server.ROOT) / "tools" / "launch.ps1").read_text(encoding="utf-8")
        self.assertIn("if ($client.Connected) { $ready = $true; break }", launcher)
        self.assertNotIn('$client.Connected -and (Test-EliteRfService', launcher)
        self.assertIn('$env:KIOSK_PUBLIC_HOSTNAME = $displayHost', launcher)
        self.assertIn("function Open-Browser", launcher)
        self.assertNotIn("Start-Process", launcher)
        self.assertIn("server is ready", launcher.lower())
        self.assertIn('"${scheme}://${displayHost}:$port/admin"', launcher)
        self.assertIn("System.Net.Sockets.TcpClient", launcher)
        self.assertIn("$client.Connect('127.0.0.1', $port)", launcher)

    def test_launcher_replaces_stale_ip_but_preserves_permanent_hostname(self):
        helper = (Path(kiosk_server.ROOT) / "tools" / "python_runtime.ps1").resolve()
        command = textwrap.dedent(f'''\
            . '{helper}'
            if ((Resolve-KioskDisplayHost '192.168.4.53' '192.168.4.63' $false $false 'TEST-PC.local') -ne '192.168.4.63') {{ exit 1 }}
            if ((Resolve-KioskDisplayHost '192.168.4.53' '192.168.4.63' $false $true 'TEST-PC.local') -ne 'TEST-PC.local') {{ exit 2 }}
            if ((Resolve-KioskDisplayHost 'kiosk.eliterf.company' '192.168.4.63' $false $true 'TEST-PC.local') -ne 'kiosk.eliterf.company') {{ exit 3 }}
            if ((Resolve-KioskDisplayHost '192.168.4.53' '192.168.4.63' $true $true 'TEST-PC.local') -ne '127.0.0.1') {{ exit 4 }}
        ''')
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_batch_launchers_use_safe_policy_and_canonical_modes(self):
        root = Path(kiosk_server.ROOT)
        production = (root / "development" / "START_ELITE_RF_KIOSK.bat").read_text(encoding="utf-8")
        test_mode = (root / "development" / "START_TEST_MODE.bat").read_text(encoding="utf-8")
        for launcher in (production, test_mode):
            self.assertNotIn("ExecutionPolicy Bypass", launcher)
            self.assertIn("ExecutionPolicy RemoteSigned", launcher)
            self.assertIn("tools\\launch.ps1", launcher)
            self.assertIn("if errorlevel 1", launcher)
            self.assertIn("pause", launcher)
        self.assertIn("-Mode Production", production)
        self.assertIn("-Mode Test", test_mode)
        self.assertFalse((root / "run_windows.bat").exists())

    def test_startup_task_uses_safe_policy(self):
        installer = (Path(kiosk_server.ROOT) / "tools" / "install_startup_tasks.ps1").read_text(encoding="utf-8")
        self.assertIn("supervise.py", installer)
        self.assertIn("RestartCount 999", installer)
        self.assertIn("StartWhenAvailable", installer)
        self.assertNotIn("ExecutionPolicy', 'Bypass", installer)

    def test_diagnostic_uses_disposable_root_and_http_smoke_checks(self):
        diagnostic = (Path(kiosk_server.ROOT) / "tools" / "diagnose.ps1").read_text(encoding="utf-8")
        self.assertIn("$tempRoot", diagnostic)
        self.assertIn("/admin", diagnostic)
        self.assertIn("/kiosk", diagnostic)
        self.assertNotIn("Join-Path $root $dir", diagnostic)

    def test_admin_login_succeeds(self):
        status, payload, headers = self.request_json("POST", "/api/admin/login", {"password": "test-admin-password"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("elite_admin=", headers["Set-Cookie"])

    def test_first_run_owner_setup_flow(self):
        self.config["admin_password_hash"] = hash_password("change-me-before-use")
        self.config["session_secret"] = "temporary-bootstrap-secret"

        status, payload, _ = self.request_json("GET", "/api/admin/setup-state")
        self.assertEqual(status, 200)
        self.assertTrue(payload["setup_required"])

        status, payload, _ = self.request_json("POST", "/api/admin/login", {"password": "change-me-before-use"})
        self.assertEqual(status, 409)
        self.assertIn("setup is required", payload["error"].lower())

        status, payload, _ = self.request_json(
            "POST",
            "/api/admin/setup",
            {"password": "owner-password-1234", "confirm_password": "different"},
        )
        self.assertEqual(status, 400)
        self.assertIn("match", payload["error"])

        status, payload, headers = self.request_json(
            "POST",
            "/api/admin/setup",
            {"password": "owner-password-1234", "confirm_password": "owner-password-1234"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["setup_required"])
        current_config = self.server.app["config"]
        self.assertTrue(verify_password("owner-password-1234", current_config["admin_password_hash"]))
        self.assertFalse(verify_password("change-me-before-use", current_config["admin_password_hash"]))
        self.assertNotEqual(current_config["session_secret"], "temporary-bootstrap-secret")

        setup_cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, payload, _ = self.request_json("GET", "/api/admin/health", cookie=setup_cookie)
        self.assertEqual(status, 200)
        self.assertIn("visits", payload)
        self.assertEqual({entry["area"] for entry in payload["storage"]}, {"records", "photos", "backups", "secondary backups", "backup freshness"})
        self.assertTrue(all(entry["status"] in {"ok", "warning", "critical", "unknown", "unconfigured", "unmounted", "missing"} for entry in payload["storage"]))

        status, payload, _ = self.request_json(
            "POST",
            "/api/admin/setup",
            {"password": "owner-password-1234", "confirm_password": "owner-password-1234"},
        )
        self.assertEqual(status, 409)
        self.assertIn("already set", payload["error"].lower())

        status, payload, _ = self.request_json("POST", "/api/admin/login", {"password": "owner-password-1234"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_setup_state_flags_remote_clients_as_host_only(self):
        self.config["admin_password_hash"] = hash_password("change-me-before-use")
        self.config["session_secret"] = "temporary-bootstrap-secret"
        handler = kiosk_server.KioskHandler.__new__(kiosk_server.KioskHandler)
        handler.server = self.server
        handler.client_address = ("192.168.1.50", 4567)
        handler.path = "/api/admin/setup-state"
        captured = {}

        def json_response(data, status=200, cookie=None):
            captured["status"] = status
            captured["data"] = data

        handler.json_response = json_response
        handler.error_response = lambda status, message: None
        kiosk_server.KioskHandler.do_GET(handler)
        self.assertEqual(captured["status"], 200)
        self.assertTrue(captured["data"]["setup_required"])
        self.assertFalse(captured["data"]["setup_allowed"])

    def test_setup_rejects_non_loopback_clients(self):
        self.config["admin_password_hash"] = hash_password("change-me-before-use")
        self.config["session_secret"] = "temporary-bootstrap-secret"
        captured = self.invoke_setup_handler(client_address=("192.168.1.50", 1234))
        self.assertEqual(captured["error"]["status"], 403)
        self.assertIn("host computer", captured["error"]["message"])
        self.assertTrue(verify_password("change-me-before-use", self.config["admin_password_hash"]))
        self.assertEqual(self.config["session_secret"], "temporary-bootstrap-secret")

    def test_setup_rejects_non_loopback_clients_before_password_checks(self):
        self.config["admin_password_hash"] = hash_password("change-me-before-use")
        self.config["session_secret"] = "temporary-bootstrap-secret"
        with patch.dict(os.environ, {"KIOSK_ADMIN_PASSWORD_HASH": hash_password("env-managed-password")}, clear=False):
            captured = self.invoke_setup_handler(client_address=("192.168.1.50", 1234))
        self.assertEqual(captured["error"]["status"], 403)
        self.assertIn("host computer", captured["error"]["message"])
        self.assertTrue(verify_password("change-me-before-use", self.config["admin_password_hash"]))
        self.assertEqual(self.config["session_secret"], "temporary-bootstrap-secret")

    def test_setup_uses_environment_managed_session_secret(self):
        self.config["admin_password_hash"] = hash_password("change-me-before-use")
        self.config["session_secret"] = "temporary-bootstrap-secret"
        with patch.dict(os.environ, {"KIOSK_SESSION_SECRET": "env-managed-session-secret"}, clear=False):
            captured = self.invoke_setup_handler()
        self.assertEqual(captured["json"]["status"], 200)
        self.assertTrue(captured["json"]["data"]["ok"])
        token = captured["json"]["cookie"].split(";", 1)[0].split("=", 1)[1]
        self.assertTrue(verify_session("env-managed-session-secret", token))
        self.assertEqual(self.server.app["config"]["session_secret"], "temporary-bootstrap-secret")

    def test_concurrent_setup_allows_exactly_one_success(self):
        self.config["admin_password_hash"] = hash_password("change-me-before-use")
        self.config["session_secret"] = "temporary-bootstrap-secret"
        results = []
        ready = threading.Barrier(2)

        def worker():
            handler = kiosk_server.KioskHandler.__new__(kiosk_server.KioskHandler)
            handler.server = self.server
            handler.client_address = ("127.0.0.1", 1234)
            handler.read_json = lambda: {"password": "owner-password-1234", "confirm_password": "owner-password-1234"}
            captured = {"json": None, "error": None}

            def json_response(data, status=200, cookie=None):
                captured["json"] = {"status": status, "data": data, "cookie": cookie}

            def error_response(status, message):
                captured["error"] = {"status": status, "message": message}

            handler.json_response = json_response
            handler.error_response = error_response
            ready.wait(timeout=5)
            kiosk_server.KioskHandler.setup_owner_password(handler)
            results.append(captured)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        successes = [result for result in results if result["json"]]
        failures = [result for result in results if result["error"]]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(successes[0]["json"]["status"], 200)
        self.assertEqual(failures[0]["error"]["status"], 409)
        self.assertIn("already set", failures[0]["error"]["message"].lower())

    def test_admin_only_routes_reject_unauthenticated_requests(self):
        status, payload, _ = self.request_json("GET", "/api/admin/visits")
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_visit_csv_export_escapes_spreadsheet_formulas(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "=1+1",
                "last_name": "Visitor",
                "email": "visitor@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        request = Request(
            self.base_url + "/api/admin/visits/export.csv",
            headers={"Cookie": self.login_cookie()},
        )
        with urlopen(request, timeout=5) as response:
            exported = response.read().decode("utf-8-sig")
        self.assertIn("'=1+1", exported)

    def test_visit_pdf_export_is_admin_only_inline_and_excludes_photo_path(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "PDF",
                "last_name": "Visitor",
                "email": "pdf-visitor@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        saved_visit = self.db.list_all_visits()[0]

        unauthenticated = Request(self.base_url + "/api/admin/visits/export.pdf")
        with self.assertRaises(HTTPError) as denied:
            urlopen(unauthenticated, timeout=5)
        self.assertEqual(denied.exception.code, 401)

        request = Request(
            self.base_url + "/api/admin/visits/export.pdf",
            headers={"Cookie": self.login_cookie()},
        )
        with urlopen(request, timeout=5) as response:
            exported = response.read()
            self.assertEqual(response.headers.get_content_type(), "application/pdf")
            self.assertIn("inline", response.headers.get("Content-Disposition", "").lower())
            self.assertNotIn("attachment", response.headers.get("Content-Disposition", "").lower())
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertTrue(exported.startswith(b"%PDF-"))
        self.assertNotIn(saved_visit["photo_filename"].encode(), exported)

    def test_pdf_export_fields_cover_visit_record_without_photo_filename(self):
        from kiosk_server.pdf_export import VISIT_EXPORT_FIELDS

        exported_fields = {key for _label, key in VISIT_EXPORT_FIELDS}
        connection = self.db.connect()
        try:
            database_fields = {row[1] for row in connection.execute("PRAGMA table_info(visits)")}
        finally:
            connection.close()
        database_fields.discard("photo_filename")
        self.assertEqual(exported_fields, database_fields)

    def test_public_employee_list_hides_contact_details(self):
        status, payload, _ = self.request_json("GET", "/api/employees")
        self.assertEqual(status, 200)
        self.assertEqual(payload["employees"], [{"id": self.employee["id"], "name": "Test Host"}])

    def test_valid_checkin_creates_visit(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["photo_saved"])
        self.assertTrue(payload["record_saved"])
        self.assertEqual(payload["notification_status"], "skipped")
        visits = self.db.list_visits()
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits[0]["first_name"], "Jane")
        self.assertEqual(visits[0]["kiosk_id"], "Main Lobby")
        self.assertTrue((self.photos_path / visits[0]["photo_filename"]).exists())

    def test_checkin_rejects_overlong_or_non_string_visitor_text(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        base = {
            "first_name": "Jane",
            "last_name": "Visitor",
            "email": "jane@example.com",
            "company": "Example Co",
            "employee_id": self.employee["id"],
            "photo": photo,
        }
        status, response, _ = self.request_json("POST", "/api/checkins", {**base, "company": "x" * 151})
        self.assertEqual(status, 400)
        self.assertIn("too long", response["error"])
        status, response, _ = self.request_json("POST", "/api/checkins", {**base, "first_name": ["Jane"]})
        self.assertEqual(status, 400)
        self.assertIn("Missing required fields", response["error"])

    def test_checkin_uses_main_lobby_without_client_kiosk_identity(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.db.list_visits()[0]["kiosk_id"], "Main Lobby")

    def test_checkin_ignores_stale_client_kiosk_identity(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Building-2",
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.db.list_visits()[0]["kiosk_id"], "Main Lobby")

    def test_checkin_allows_missing_or_empty_visitor_phone(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()

        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.db.list_visits()[0]["phone"], "")

        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        visits = self.db.list_visits()
        self.assertEqual(visits[-1]["phone"], "")

    def test_checkin_stores_supplied_visitor_phone(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.db.list_visits()[0]["phone"], "555-0101")

    def test_checkin_missing_email_still_fails(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Missing required fields", payload["error"])

    def test_checkin_db_failure_cleans_up_staged_photo(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        with patch.object(self.db, "create_visit", side_effect=RuntimeError("db down")):
            status, payload, _ = self.request_json(
                "POST",
                "/api/checkins",
                {
                    "first_name": "Jane",
                    "last_name": "Visitor",
                    "phone": "555-0101",
                    "email": "jane@example.com",
                    "company": "Example Co",
                    "employee_id": self.employee["id"],
                    "kiosk_id": "Main Lobby",
                    "photo": photo,
                },
            )
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "Could not save check-in")
        self.assertEqual(self.db.list_visits(), [])
        self.assertEqual(list(self.photos_path.iterdir()), [])

    def test_checkin_missing_required_field_fails(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Missing required fields", payload["error"])

    def test_checkin_invalid_employee_fails(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": 99999,
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Employee not found")

    def test_checkin_invalid_photo_fails(self):
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": "not-base64",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "Invalid photo")

    def test_kiosk_form_does_not_require_visitor_phone(self):
        project_root = Path(__file__).resolve().parents[2]
        html = (project_root / "static" / "kiosk.html").read_text(encoding="utf-8")
        script = (project_root / "static" / "kiosk.js").read_text(encoding="utf-8")
        self.assertIn('<label class="field">Phone<input name="phone" type="tel" autocomplete="tel"></label>', html)
        self.assertNotIn('Phone <span class="required-mark" aria-label="required">*</span><input name="phone" type="tel" autocomplete="tel" required>', html)
        self.assertIn('name="first_name" autocomplete="given-name" required', html)
        self.assertIn('name="last_name" autocomplete="family-name" required', html)
        self.assertIn('name="email" type="email" autocomplete="email" required', html)
        self.assertIn('name="company" autocomplete="organization" required', html)
        self.assertIn('Are you a U.S. Citizen?', html)
        self.assertIn('name="us_citizen" value="yes" required', html)
        self.assertIn('name="us_citizen" value="no"', html)
        self.assertIn('name="employee_id" id="employeeSelect" required', html)
        self.assertIn('id="employeePicker" type="button" aria-haspopup="listbox"', html)
        self.assertIn('id="employeeOptions" role="listbox"', html)
        self.assertNotIn("URLSearchParams(location.search)", script)
        self.assertNotIn("payload.kiosk_id", script)

    def test_kiosk_form_explains_invalid_email_and_keeps_native_host_selector_readable(self):
        project_root = Path(__file__).resolve().parents[2]
        html = (project_root / "static" / "kiosk.html").read_text(encoding="utf-8")
        script = (project_root / "static" / "kiosk.js").read_text(encoding="utf-8")
        css = (project_root / "static" / "kiosk.css").read_text(encoding="utf-8")
        self.assertIn('aria-describedby="emailMessage"', html)
        self.assertIn('id="emailMessage" class="field-message" role="alert" aria-live="polite" hidden', html)
        self.assertIn('Email is required.', script)
        self.assertIn('Enter a valid email address, such as name@example.com.', script)
        self.assertIn('emailInput.addEventListener("input"', script)
        self.assertIn('emailInput.addEventListener("blur"', script)
        self.assertIn('if (!validateEmail(true))', script)
        self.assertIn('.host-picker-button', css)
        self.assertIn('background:#fff', css)
        self.assertIn('.host-options', css)
        self.assertIn('background:#fff', css)
        self.assertIn('bottom:calc(100% + 6px)', css)
        self.assertIn('.host-options.opens-down', css)
        self.assertIn('positionEmployeePicker', script)

    def test_android_kiosk_stays_in_landscape_for_photo_flow(self):
        project_root = Path(__file__).resolve().parents[2]
        manifest = (project_root / "development" / "android-kiosk" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn('android:screenOrientation="landscape"', manifest)

    def test_kiosk_completion_can_start_next_checkin_immediately(self):
        project_root = Path(__file__).resolve().parents[2]
        html = (project_root / "static" / "kiosk.html").read_text(encoding="utf-8")
        script = (project_root / "static" / "kiosk.js").read_text(encoding="utf-8")
        self.assertIn('id="stepThree" class="thank-you"', html)
        self.assertIn('id="backToCheckinBtn"', html)
        self.assertIn('>Back to check-in</button>', html)
        self.assertIn('backToCheckinBtn.addEventListener("click", resetKiosk);', script)
        self.assertIn('resetTimer = setTimeout(resetKiosk, 10000);', script)
        self.assertNotIn('setTimeout(resetKiosk, 15000)', script)

    def test_kiosk_photo_flow_has_unobstructed_camera_and_take_photo_action(self):
        project_root = Path(__file__).resolve().parents[2]
        html = (project_root / "static" / "kiosk.html").read_text(encoding="utf-8")
        script = (project_root / "static" / "kiosk.js").read_text(encoding="utf-8")
        self.assertIn("Look at the camera and center your face and shoulders in the frame.", html)
        self.assertIn('class="photo-page photo-step"', html)
        self.assertIn('class="camera-area photo-stage"', html)
        self.assertIn('class="action-bar photo-controls"', html)
        self.assertNotIn('class="photo-logo"', html)
        self.assertNotIn('class="person-outline"', html)
        self.assertNotIn('class="person-outline-corners"', html)
        self.assertNotIn('class="camera-overlay-instruction"', html)
        self.assertIn('<video id="video" class="camera-preview" autoplay muted playsinline', html)
        self.assertIn('id="photoHeading"', html)
        self.assertIn('id="photoInstruction"', html)
        self.assertIn('id="captureBtn"', html)
        self.assertIn(">TAKE PHOTO</button>", html)
        self.assertIn('captureBtn.textContent = "TAKE PHOTO";', script)
        self.assertIn('id="finishBtn"', html)
        self.assertIn(">USE PHOTO</button>", html)
        self.assertIn('window.EliteRfNativeCamera?.capture();', script)
        self.assertIn('window.eliteRfNativeCameraReady = () =>', script)
        self.assertIn('video.srcObject.getVideoTracks().length === 0', script)
        self.assertIn('await video.play();', script)
        self.assertIn('submitCheckin();', script)
        self.assertIn('finishBtn.addEventListener("click", submitCheckin);', script)
        self.assertIn('id="retakeBtn"', html)
        self.assertIn('retakeBtn.addEventListener("click", async () =>', script)
        self.assertIn('photoHeading.textContent = "Is this photo okay?";', script)
        self.assertIn('photoInstruction.textContent = "Make sure your face is clearly visible.";', script)
        self.assertNotIn('photoHeading.textContent = "Photo saved";', script)
        self.assertIn('photoHeading.textContent = "Completing your check-in…";', script)
        self.assertIn('Completing your check-in…\\nPlease wait.', script)
        self.assertIn('function showCapturedPhoto(dataUrl)', script)
        self.assertNotIn("fillTemporaryTestDetails", script)
        self.assertNotIn('test.visitor@example.com', script)
        self.assertNotIn('logoTapCount', script)
        self.assertIn('cameraDebugMode = location.hash === "#camera-debug"', script)
        self.assertIn('captureDebugFrame()', script)
        self.assertIn('saveDebugDataUrl(capturedPhoto)', script)
        main_activity = (project_root / "development" / "android-kiosk" / "app" / "src" / "main" / "java" / "com" / "eliterf" / "kiosk" / "MainActivity.kt").read_text(encoding="utf-8")
        camerax_activity = (project_root / "development" / "android-kiosk" / "app" / "src" / "main" / "java" / "com" / "eliterf" / "kiosk" / "CameraXCaptureActivity.kt").read_text(encoding="utf-8")
        self.assertIn("private const val TARGET_WIDTH = 2560", camerax_activity)
        self.assertIn("private const val TARGET_HEIGHT = 1920", camerax_activity)
        self.assertIn("CameraSelector.DEFAULT_FRONT_CAMERA", camerax_activity)
        self.assertIn("isReversedHorizontal = true", camerax_activity)
        self.assertIn(".setMetadata(metadata)", camerax_activity)
        self.assertIn('text = "ONE LAST STEP"', camerax_activity)
        self.assertIn('text = "Please take your photo"', camerax_activity)
        self.assertIn('text = "Look at the camera and center your face and shoulders in the frame."', camerax_activity)
        self.assertIn('text = "Your photo is used for visitor identification and security."', camerax_activity)
        self.assertIn('text = "Back"', camerax_activity)
        self.assertIn('FrameLayout.LayoutParams(-1, dp(132), Gravity.BOTTOM)', camerax_activity)
        self.assertIn('text = if (debugBuild) "Open camera pixel diagnostic" else "Camera pixel diagnostic unavailable"', main_activity)
        self.assertIn('cameraDiagnosticsRequested = true', main_activity)
        self.assertIn('cameraCalibrationRequested = true', main_activity)
        self.assertGreaterEqual(main_activity.count('KioskPolicy.relaxFullMaintenanceRestrictions(this)'), 2)
        self.assertIn('calibration -> "#camera-calibration"', main_activity)
        self.assertIn('cameraCalibrationActive = false', main_activity)
        self.assertIn('if (!cameraCalibrationActive && System.currentTimeMillis() - lastInteraction > 60_000)', main_activity)
        self.assertIn('onCameraCalibrationFinished()', main_activity)
        self.assertIn('text = "Calibrate camera"', main_activity)
        css = (project_root / "static" / "kiosk.css").read_text(encoding="utf-8")
        self.assertIn("grid-template-rows:70% 30%", css)
        self.assertIn("width:100vw", css)
        self.assertIn("height:100dvh", css)
        self.assertIn(".photo-page .camera-area", css)
        self.assertIn(".action-bar", css)
        self.assertIn("grid-template-columns:minmax(0,1fr) minmax(280px,360px)", css)
        self.assertIn("object-fit:cover", css)
        self.assertIn('.photo-page[data-reviewing="true"] .camera-frame img { object-fit:contain; transform:none; }', css)
        self.assertIn("vertical-align:top", css)
        self.assertIn("body.native-camera-mode", css)
        self.assertIn("background:transparent !important", css)
        native = (project_root / "development" / "android-kiosk" / "app" / "src" / "main" / "java" / "com" / "eliterf" / "kiosk" / "NativeCameraController.kt").read_text(encoding="utf-8")
        self.assertIn('camera_preview_capture.jpg', native)
        self.assertIn('camera_raw_frame.jpg', native)
        self.assertIn('camera_take_photo_capture.jpg', native)
        self.assertIn('camera_uploaded_photo.jpg', native)
        self.assertIn('raw_camera_frame.jpg', native)
        self.assertIn('rendered_preview_frame.jpg', native)
        self.assertIn('camera_source_buffer.jpg', native)
        self.assertIn('take_photo_capture.jpg', native)
        self.assertIn('uploaded_backend_photo.jpg', native)
        self.assertIn('cameraId=', native)
        self.assertIn("val scale = min(target.width() / rotated.width(), target.height() / rotated.height())", native)
        self.assertIn("postScale(scale, scale", native)
        self.assertIn("fitCompleteFrame=true", native)
        self.assertIn("Match the preview to the still stream's aspect", native)
        self.assertIn("val jpegSize = chooseOutputSize(streamMap.getOutputSizes(ImageFormat.JPEG).orEmpty())", native)
        self.assertIn("choosePreviewSize(\n                streamMap.getOutputSizes(SurfaceTexture::class.java).orEmpty(),\n                jpegSize\n            )", native)
        self.assertIn("activityOrientation=", native)
        self.assertIn("addOnLayoutChangeListener", native)
        self.assertIn("setDefaultBufferSize", native)
        self.assertIn("CameraCharacteristics.LENS_FACING_FRONT", native)
        self.assertIn("(sensorOrientation + displayRotationDegrees()) % 360", native)
        self.assertIn("MESWAO_FRONT_PREVIEW_OFFSET = 180", native)
        self.assertIn("webView.setLayerType(View.LAYER_TYPE_SOFTWARE, null)", native)
        self.assertIn("webView.setLayerType(View.LAYER_TYPE_NONE, null)", native)
        self.assertIn("webView.visibility = View.INVISIBLE", native)
        self.assertIn("webView.visibility = View.VISIBLE", native)
        self.assertIn("if (cameraOverlay != null) stopOnMainThread()", native)
        self.assertIn("createNativeControls()", native)
        self.assertIn('text = "Take photo"', native)
        self.assertIn("createCalibrationControls()", native)
        self.assertIn('text = "CAMERA CALIBRATION MODE"', native)
        self.assertIn('ROTATE 90° LEFT', native)
        self.assertIn('ROTATE 90° RIGHT', native)
        self.assertIn('FLIP HORIZONTALLY', native)
        self.assertIn('STRETCH WIDER', native)
        self.assertIn('STRETCH NARROWER', native)
        self.assertIn('horizontalStretch', native)
        self.assertIn('postScale(calibration.horizontalStretch, 1f', native)
        self.assertIn('SAVE AND LOCK', native)
        self.assertIn('CameraCalibration.save(activity, value)', native)
        self.assertIn('applyCalibrationToJpeg', native)
        self.assertIn('manualCalibration=${calibration != null}', native)
        self.assertIn('dp(if (calibrationMode) 420 else 220)', native)
        self.assertIn('window.EliteRfNativeCamera?.start(0, 0, 0, 0, cameraCalibrationMode);', script)
        self.assertIn('window.EliteRfNativeCamera?.launchCapture', script)
        self.assertIn('window.eliteRfNativeCaptureResult', script)
        self.assertIn('window.eliteRfNativeCaptureReady', script)
        self.assertIn('capturedPhotoChunk', script)
        self.assertIn('window.eliteRfNativeCaptureCancelled', script)
        self.assertIn('window.eliteRfNativeCaptureError', script)
        self.assertIn('function showNativeReviewPhoto(dataUrl)', script)
        self.assertNotIn('showNativePhotoStep();', script)
        self.assertIn('The camera is unavailable. Please ask reception for help.', script)
        self.assertIn('returnToDetails();', script)
        self.assertGreaterEqual(script.count('if (capturedPhoto || submitting ||'), 2)
        self.assertIn('finishBtn.disabled = true;', script)
        self.assertIn('let data;', script)
        self.assertIn('stepThree.hidden = false;\n  window.EliteRfNativeCamera?.discardCapturedPhoto?.();', script)
        self.assertIn('captureBtn.disabled = false;', script)
        self.assertIn('window.eliteRfFinishCalibration = finishCameraCalibration;', script)
        self.assertIn('async function finishCameraCalibration()', script)
        self.assertIn('if (!photoPage.hidden && !capturedPhoto) await startCamera();', script)
        self.assertIn('SAVE AND LOCK stops the calibration controller', script)
        self.assertIn("container.addView(overlay, params)", native)
        self.assertIn("previewTransformRotation()", native)
        self.assertIn("jpegRotation=${jpegOrientation()}", native)
        self.assertIn("renderPreviewDiagnostic(sourceBitmap)", native)
        self.assertIn("source.width.toFloat(), source.height.toFloat(),", native)
        self.assertIn("Canvas(rendered)", native)
        self.assertNotIn("MESWAO_FRONT_MOUNT_CORRECTION", native)
        self.assertIn("isOpaque = true", native)
        self.assertIn("val params = FrameLayout.LayoutParams(-1, -1)", native)
        self.assertIn("container.addView(overlay, params)", native)
        self.assertIn("previewReadyNotified", native)
        self.assertNotIn("TextureView(activity).apply {\n            isOpaque = false\n            setBackgroundColor", native)
        self.assertNotIn("ACTION_IMAGE_CAPTURE", native)
        self.assertNotIn("com.mediatek.camera", native)
        self.assertIn('fun launchCapture(payloadJson: String): Boolean', native)
        main_manifest = (project_root / "development" / "android-kiosk" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn('androidx.core.content.FileProvider', main_manifest)
        self.assertIn('${applicationId}.fileprovider', main_manifest)
        activity = (project_root / "development" / "android-kiosk" / "app" / "src" / "main" / "java" / "com" / "eliterf" / "kiosk" / "MainActivity.kt").read_text(encoding="utf-8")
        self.assertIn('MediaStore.ACTION_IMAGE_CAPTURE', activity)
        self.assertIn('MediaStore.EXTRA_OUTPUT', activity)
        self.assertIn('ClipData.newRawUri', activity)
        self.assertIn('FileProvider.getUriForFile', activity)
        self.assertIn('putString("payload", payloadJson)', activity)
        self.assertIn('discardPendingNativeCapture', activity)
        self.assertIn('NATIVE_CAPTURE_REVIEW_PATH', activity)
        self.assertIn('review_path', activity)
        policy = (project_root / "development" / "android-kiosk" / "app" / "src" / "main" / "java" / "com" / "eliterf" / "kiosk" / "admin" / "KioskPolicy.kt").read_text(encoding="utf-8")
        self.assertGreaterEqual(policy.count("dpm.setCameraDisabled(admin, false)"), 2)
        self.assertIn('dpm.setPackagesSuspended(admin, CAMERA_APP_PACKAGES, false)', policy)
        self.assertIn('dpm.setPackagesSuspended(admin, CAMERA_APP_PACKAGES, false)', policy)

    def test_admin_delete_requires_auth_and_removes_live_record_and_photo(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        visit = self.db.list_visits()[0]
        cookie = self.login_cookie()
        path = f"/api/admin/visits/{visit['id']}"
        action_header = {"X-Kiosk-Admin-Action": "delete-visit"}

        status, payload, _ = self.request_json("DELETE", path, extra_headers=action_header)
        self.assertEqual(status, 401)
        self.assertIn("error", payload)
        self.assertEqual(len(self.db.list_visits()), 1)
        self.assertTrue((self.photos_path / visit["photo_filename"]).exists())

        status, payload, _ = self.request_json("DELETE", path, cookie=cookie)
        self.assertEqual(status, 403)
        self.assertEqual(len(self.db.list_visits()), 1)

        status, payload, _ = self.request_json("DELETE", path, cookie=cookie, extra_headers=action_header)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["photo_cleanup_pending"])
        self.assertEqual(self.db.list_visits(), [])
        self.assertFalse((self.photos_path / visit["photo_filename"]).exists())
        with self.db.connect() as conn:
            event = conn.execute(
                "SELECT event_type, visit_id FROM audit_log WHERE event_type = 'visit_deleted'"
            ).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual((event["event_type"], event["visit_id"]), ("visit_deleted", visit["id"]))

        status, payload, _ = self.request_json("DELETE", path, cookie=cookie, extra_headers=action_header)
        self.assertEqual(status, 404)

    def test_pending_photo_delete_is_retried_after_interrupted_cleanup(self):
        photo_name = "pending-delete.jpg"
        photo_path = self.photos_path / photo_name
        photo_path.write_bytes(b"jpeg")
        visit_id = self.db.create_visit({
            "first_name": "Test",
            "last_name": "Visitor",
            "phone": "",
            "email": "test@example.com",
            "company": "Test Co",
            "employee_id": self.employee["id"],
            "employee_name": "Test Host",
            "kiosk_id": "Main Lobby",
            "photo_filename": photo_name,
            "notification_status": "skipped",
            "notification_detail": "test",
        })
        result = self.db.delete_visit(visit_id)
        self.assertEqual(result["photo_filename"], photo_name)
        self.assertTrue(photo_path.exists())
        self.assertEqual(self.db.pending_photo_deletions(), [photo_name])

        remaining = kiosk_server.cleanup_pending_photo_deletions(self.db, self.photos_path)
        self.assertEqual(remaining, 0)
        self.assertFalse(photo_path.exists())
        self.assertEqual(self.db.pending_photo_deletions(), [])

    def test_old_backup_keeps_its_snapshot_until_rotation_and_new_backup_omits_deleted_visit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            photos = root / "storage" / "photos"
            data.mkdir()
            photos.mkdir(parents=True)
            (root / "config.json").write_text("{}\n", encoding="utf-8")
            db = Database(data / "visitor-kiosk.sqlite3")
            employee = db.create_employee({
                "name": "Test Host", "phone": "", "email": "host@example.com",
                "alert_method": "email", "active": 1,
            })
            photo_name = "retained-in-old-backup.jpg"
            (photos / photo_name).write_bytes(b"\xff\xd8test-jpeg")
            visit_id = db.create_visit({
                "first_name": "Test", "last_name": "Visitor", "phone": "",
                "email": "test@example.com", "company": "Test Co",
                "employee_id": employee["id"], "employee_name": employee["name"],
                "kiosk_id": "Main Lobby", "photo_filename": photo_name,
                "notification_status": "skipped", "notification_detail": "test",
            })
            old_backup = create_backup(root, retention_count=10)

            result = db.delete_visit(visit_id)
            kiosk_server.cleanup_pending_photo_deletions(db, photos)
            new_backup = create_backup(root, retention_count=10)

            with zipfile.ZipFile(old_backup) as archive:
                self.assertIn(f"storage/photos/{photo_name}", archive.namelist())
                prior_db = root / "old-backup-db.sqlite3"
                prior_db.write_bytes(archive.read("data/visitor-kiosk.sqlite3"))
            connection = sqlite3.connect(prior_db)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 1)
            finally:
                connection.close()

            with zipfile.ZipFile(new_backup) as archive:
                self.assertNotIn(f"storage/photos/{photo_name}", archive.namelist())
                current_db = root / "new-backup-db.sqlite3"
                current_db.write_bytes(archive.read("data/visitor-kiosk.sqlite3"))
            connection = sqlite3.connect(current_db)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 0)
            finally:
                connection.close()
            self.assertEqual(result["visit_id"], visit_id)

    def test_employee_create_edit_inactivate_via_routes(self):
        cookie = self.login_cookie()
        status, payload, _ = self.request_json(
            "POST",
            "/api/admin/employees",
            {
                "name": "Route Added",
                "phone": "+15555550123",
                "email": "route@example.com",
                "alert_method": "email",
                "active": True,
            },
            cookie=cookie,
        )
        self.assertEqual(status, 201)
        employee = payload["employee"]
        self.assertEqual(employee["name"], "Route Added")

        status, payload, _ = self.request_json(
            "PATCH",
            f"/api/admin/employees/{employee['id']}",
            {
                "name": "Route Updated",
                "phone": "+15555550999",
                "email": "updated@example.com",
                "alert_method": "email",
                "active": False,
            },
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["employee"]["name"], "Route Updated")
        self.assertEqual(payload["employee"]["active"], 0)
        self.assertNotIn({"id": employee["id"], "name": "Route Updated"}, self.db.list_active_employees_public())

    def test_notification_settings_require_authentication(self):
        status, payload, _ = self.request_json("PATCH", "/api/admin/settings", {"notifications_enabled": True, "message_template": "{visitor_name}"})
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_notification_settings_update_with_valid_template(self):
        cookie = self.login_cookie()
        status, payload, _ = self.request_json(
            "PATCH",
            "/api/admin/settings",
            {
                "notifications_enabled": False,
                "message_template": "{visitor_name} from {company} is at {location} for {host_name}.",
            },
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.config["notification_settings"]["enabled"], False)
        self.assertEqual(self.config["notification_settings"]["message_template"], "{visitor_name} from {company} is at {location} for {host_name}.")

    def test_notification_settings_reject_unsupported_placeholders(self):
        cookie = self.login_cookie()
        status, payload, _ = self.request_json(
            "PATCH",
            "/api/admin/settings",
            {
                "notifications_enabled": True,
                "message_template": "{visitor_name} has arrived for {bad_placeholder}.",
            },
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertIn("Use only", payload["error"])

    def test_microsoft_graph_readiness_returns_booleans_only(self):
        cookie = self.login_cookie()
        with patch.dict(os.environ, {
            "M365_TENANT_ID": "tenant-id",
            "M365_CLIENT_ID": "client-id",
            "M365_SENDER_ADDRESS": "alerts@example.com",
            "M365_CERTIFICATE_PATH": "missing.pem",
            "M365_CERTIFICATE_THUMBPRINT": "thumbprint",
        }, clear=False):
            status, payload, _ = self.request_json("GET", "/api/admin/notifications/readiness", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(payload["email"]["tenant_id_configured"])
        self.assertTrue(payload["email"]["client_id_configured"])
        self.assertTrue(payload["email"]["sender_address_configured"])
        self.assertTrue(all(isinstance(value, bool) for value in payload["email"].values()))
        self.assertNotIn("AC1234567890", json.dumps(payload))
        self.assertNotIn("token-value", json.dumps(payload))
        self.assertNotIn("client-id", json.dumps(payload))

    def test_microsoft365_connection_requires_admin_and_https(self):
        status, payload, _ = self.request_json("GET", "/api/admin/notifications/connect")
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])
        cookie = self.login_cookie()
        status, payload, _ = self.request_json("GET", "/api/admin/notifications/connect", cookie=cookie)
        self.assertEqual(status, 400)
        self.assertIn("HTTPS", payload["error"])

    def test_microsoft365_dashboard_setup_validation(self):
        valid = kiosk_microsoft365_setup.validate_app_settings(
            "elite-rf.onmicrosoft.com",
            "12345678-1234-1234-1234-1234567890ab",
            "https://visitors.example.internal/oauth/microsoft/callback",
        )
        self.assertEqual(valid["tenant_id"], "elite-rf.onmicrosoft.com")
        self.assertEqual(valid["client_id"], "12345678-1234-1234-1234-1234567890ab")
        with self.assertRaisesRegex(ValueError, "HTTPS callback"):
            kiosk_microsoft365_setup.validate_app_settings(
                "elite-rf.onmicrosoft.com",
                "12345678-1234-1234-1234-1234567890ab",
                "http://visitors.example.internal/oauth/microsoft/callback",
            )
        with self.assertRaisesRegex(ValueError, r"application \(client\) ID"):
            kiosk_microsoft365_setup.validate_app_settings(
                "elite-rf.onmicrosoft.com", "not-an-app-id",
                "https://visitors.example.internal/oauth/microsoft/callback",
            )

    def test_microsoft365_dashboard_setup_is_admin_only_and_saved_encrypted(self):
        setup = {
            "tenant_id": "elite-rf.onmicrosoft.com",
            "client_id": "12345678-1234-1234-1234-1234567890ab",
            "redirect_uri": "https://visitors.example.internal/oauth/microsoft/callback",
        }
        status, payload, _ = self.request_json("POST", "/api/admin/notifications/setup", setup)
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

        self.config["session_secret"] = "test-session-secret-with-enough-entropy-1234567890"
        cookie = self.login_cookie()
        with patch("kiosk_server.server.load_microsoft365_setup", return_value={}), \
             patch("kiosk_server.server.save_microsoft365_setup") as save:
            status, payload, _ = self.request_json("POST", "/api/admin/notifications/setup", setup, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(save.call_args.args[2], setup)
        self.assertNotIn("private_key", json.dumps(payload))

    def test_microsoft365_dashboard_setup_rejects_http_callback(self):
        self.config["session_secret"] = "test-session-secret-with-enough-entropy-1234567890"
        cookie = self.login_cookie()
        status, payload, _ = self.request_json("POST", "/api/admin/notifications/setup", {
            "tenant_id": "elite-rf.onmicrosoft.com",
            "client_id": "12345678-1234-1234-1234-1234567890ab",
            "redirect_uri": "http://visitors.example.internal/oauth/microsoft/callback",
        }, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertIn("HTTPS callback", payload["error"])

    def test_microsoft365_authorization_callback_is_state_and_cookie_bound(self):
        cookie = self.login_cookie()
        self.server.app["external_https"] = True
        flow = {"auth_uri": "https://login.microsoftonline.com/example/authorize", "state": "state-from-test"}
        with patch("kiosk_server.server.initiate_microsoft365_auth_flow", return_value=flow) as initiate:
            status, headers, _ = self.request_no_redirect(
                "GET", "/api/admin/notifications/connect", headers={"Cookie": cookie},
            )
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], flow["auth_uri"])
        state = initiate.call_args.args[0]
        oauth_cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=None", headers["Set-Cookie"])
        self.assertIn("Secure", headers["Set-Cookie"])
        body = urlencode({"state": state, "code": "synthetic-auth-code"}).encode()
        with patch("kiosk_server.server.complete_microsoft365_auth_flow", return_value="alerts@example.com") as complete:
            status, headers, _ = self.request_no_redirect(
                "POST", "/oauth/microsoft/callback", body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": oauth_cookie},
            )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/admin?m365=connected")
        complete.assert_called_once()

    def test_microsoft365_callback_rejects_missing_state_cookie(self):
        self.server.app["external_https"] = True
        self.server.app["oauth_flows"]["state"] = {
            "flow": {"state": "state"}, "nonce": "expected-nonce", "expires_at": time.time() + 60,
        }
        body = urlencode({"state": "state", "code": "synthetic-auth-code"}).encode()
        with patch("kiosk_server.server.complete_microsoft365_auth_flow") as complete:
            status, headers, _ = self.request_no_redirect(
                "POST", "/oauth/microsoft/callback", body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/admin?m365=error")
        complete.assert_not_called()

    def test_microsoft365_disconnect_removes_saved_connection(self):
        cookie = self.login_cookie()
        cache = self.tempdir / "data" / kiosk_notifications.TOKEN_CACHE_NAME
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"synthetic-encrypted-cache")
        status, payload, _ = self.request_json(
            "POST", "/api/admin/notifications/disconnect", {}, cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(cache.exists())

    def test_test_email_requires_admin_authentication(self):
        status, payload, _ = self.request_json("POST", "/api/admin/notifications/test", {"employee_id": self.employee["id"]})
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_test_email_uses_selected_active_employee(self):
        cookie = self.login_cookie()
        with patch("kiosk_server.server.send_email", return_value=("sent", "Email accepted by Microsoft 365.")) as send_email:
            status, payload, _ = self.request_json("POST", "/api/admin/notifications/test", {"employee_id": self.employee["id"]}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "sent")
        self.assertEqual(payload["detail"], "Email accepted by Microsoft 365.")
        send_email.assert_called_once()
        employee_arg, subject_arg, text_arg, html_arg = send_email.call_args.args
        self.assertEqual(employee_arg["id"], self.employee["id"])
        self.assertEqual(subject_arg, "Visitor waiting at Elite RF")
        self.assertIn("delivery test", text_arg.lower())
        self.assertIn("delivery test", html_arg.lower())

    def test_test_email_rejects_inactive_or_invalid_employees(self):
        cookie = self.login_cookie()
        inactive_status, inactive_payload, _ = self.request_json(
            "POST",
            "/api/admin/employees",
            {
                "name": "Inactive Route",
                "email": "inactive@example.com",
                "active": False,
            },
            cookie=cookie,
        )
        self.assertEqual(inactive_status, 201)
        inactive_employee = inactive_payload["employee"]
        status, payload, _ = self.request_json("POST", "/api/admin/notifications/test", {"employee_id": inactive_employee["id"]}, cookie=cookie)
        self.assertEqual(status, 400)
        self.assertIn("active employee", payload["error"].lower())

        status, payload, _ = self.request_json(
            "POST",
            "/api/admin/employees",
            {
                "name": "Bad Email",
                "phone": "",
                "email": "not-an-email",
                "alert_method": "email",
                "active": True,
            },
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertIn("email", payload["error"].lower())

        status, payload, _ = self.request_json(
            "PATCH",
            f"/api/admin/employees/{self.employee['id']}",
            {
                "name": self.employee["name"],
                "email": self.db.list_employees_admin()[0]["email"],
                "alert_method": "email",
                "active": True,
            },
            cookie=cookie,
        )
        self.assertEqual(status, 200)

    def test_test_mode_rejects_test_email_without_calling_graph(self):
        captured = self.invoke_test_notification_handler(test_mode=True)
        self.assertEqual(captured["json"]["status"], 200)
        self.assertEqual(captured["json"]["data"]["status"], "skipped")
        self.assertIn("test mode", captured["json"]["data"]["detail"].lower())

    def test_email_unconfigured_skips_without_crashing(self):
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["photo_saved"])
        self.assertTrue(payload["record_saved"])
        self.assertEqual(payload["notification_status"], "skipped")
        visit = self.db.list_visits()[0]
        self.assertEqual(visit["notification_status"], "skipped")

    def test_email_unavailable_marks_notification_pending(self):
        self.mock_send_email.return_value = ("pending", "temporary outage")
        photo = base64.b64encode(b"\xff\xd8\xff\xe0jpeg-data").decode()
        status, payload, _ = self.request_json(
            "POST",
            "/api/checkins",
            {
                "first_name": "Jane",
                "last_name": "Visitor",
                "phone": "555-0101",
                "email": "jane@example.com",
                "company": "Example Co",
                "employee_id": self.employee["id"],
                "kiosk_id": "Main Lobby",
                "photo": photo,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["notification_status"], "pending")
        visit = self.db.list_visits()[0]
        self.assertEqual(visit["notification_status"], "pending")


class RestoreBackupToolTests(unittest.TestCase):
    def _make_backup(self, root: Path) -> Path:
        (root / "data").mkdir(parents=True)
        (root / "storage" / "photos").mkdir(parents=True)
        (root / "config.json").write_text('{"version":"backup"}\n', encoding="utf-8")
        Database(root / "data" / "visitor-kiosk.sqlite3")
        (root / "storage" / "photos" / "backup-photo.jpg").write_bytes(b"backup-photo")
        return create_backup(root)

    def _make_live_root(self, root: Path) -> None:
        (root / "data").mkdir(parents=True)
        (root / "storage" / "photos").mkdir(parents=True)
        (root / "backups").mkdir()
        (root / "config.json").write_text('{"version":"live"}\n', encoding="utf-8")
        Database(root / "data" / "visitor-kiosk.sqlite3")
        (root / "data" / "old-data-marker").write_text("old", encoding="utf-8")
        (root / "storage" / "photos" / "live-photo.jpg").write_bytes(b"live-photo")
        (root / "backups" / "keep.txt").write_text("keep", encoding="utf-8")

    def test_restore_defaults_to_verify_only_then_swaps_state_and_preserves_rollback(self):
        restore_tool = load_restore_backup_module()
        source = Path(tempfile.mkdtemp())
        runtime = Path(tempfile.mkdtemp())
        try:
            archive = self._make_backup(source)
            self._make_live_root(runtime)
            live_database = runtime / "data" / "visitor-kiosk.sqlite3"
            original_bytes = live_database.read_bytes()

            self.assertIsNone(restore_tool.restore_backup(archive, runtime))
            self.assertEqual(live_database.read_bytes(), original_bytes)
            self.assertFalse(list(runtime.glob(".restore-rollback-*")))

            rollback = restore_tool.restore_backup(archive, runtime, apply=True)

            self.assertIsNotNone(rollback)
            self.assertEqual(json.loads((runtime / "config.json").read_text())['version'], "backup")
            self.assertTrue((runtime / "storage" / "photos" / "backup-photo.jpg").is_file())
            self.assertFalse((runtime / "storage" / "photos" / "live-photo.jpg").exists())
            self.assertTrue((runtime / "backups" / "keep.txt").is_file())
            self.assertEqual((rollback / "config.json").read_text(encoding="utf-8"), '{"version":"live"}\n')
            self.assertTrue((rollback / "data" / "old-data-marker").is_file())
            self.assertTrue((rollback / "storage" / "photos" / "live-photo.jpg").is_file())
            restored_db = sqlite3.connect(runtime / "data" / "visitor-kiosk.sqlite3")
            try:
                self.assertEqual(restored_db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                restored_db.close()
        finally:
            shutil.rmtree(source, ignore_errors=True)
            shutil.rmtree(runtime, ignore_errors=True)

    def test_restore_failure_rolls_back_previous_live_state(self):
        restore_tool = load_restore_backup_module()
        source = Path(tempfile.mkdtemp())
        runtime = Path(tempfile.mkdtemp())
        try:
            archive = self._make_backup(source)
            self._make_live_root(runtime)
            live_database = runtime / "data" / "visitor-kiosk.sqlite3"
            original_bytes = live_database.read_bytes()
            real_replace = os.replace
            calls = 0

            def fail_once(source_path, destination_path):
                nonlocal calls
                calls += 1
                if calls == 4:
                    raise OSError("simulated disk rename failure")
                return real_replace(source_path, destination_path)

            with patch.object(restore_tool.os, "replace", side_effect=fail_once):
                with self.assertRaisesRegex(OSError, "simulated disk rename failure"):
                    restore_tool.restore_backup(archive, runtime, apply=True)

            self.assertEqual(live_database.read_bytes(), original_bytes)
            self.assertEqual(json.loads((runtime / "config.json").read_text())['version'], "live")
            self.assertTrue((runtime / "storage" / "photos" / "live-photo.jpg").is_file())
            self.assertFalse(list(runtime.glob(".restore-rollback-*")))
        finally:
            shutil.rmtree(source, ignore_errors=True)
            shutil.rmtree(runtime, ignore_errors=True)

    def test_interrupted_restore_leaves_startup_blocking_marker_and_rollback_data(self):
        restore_tool = load_restore_backup_module()
        source = Path(tempfile.mkdtemp())
        runtime = Path(tempfile.mkdtemp())
        try:
            archive = self._make_backup(source)
            self._make_live_root(runtime)
            real_replace = os.replace
            calls = 0

            class SimulatedProcessInterruption(BaseException):
                pass

            def interrupt_after_old_data_is_moved(source_path, destination_path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise SimulatedProcessInterruption()
                return real_replace(source_path, destination_path)

            with patch.object(restore_tool.os, "replace", side_effect=interrupt_after_old_data_is_moved):
                with self.assertRaises(SimulatedProcessInterruption):
                    restore_tool.restore_backup(archive, runtime, apply=True)

            marker = runtime / ".restore-in-progress"
            self.assertTrue(marker.is_file())
            rollback = runtime / marker.read_text(encoding="ascii").strip()
            self.assertTrue((rollback / "data" / "old-data-marker").is_file())
            self.assertFalse((runtime / "data").exists())
        finally:
            shutil.rmtree(source, ignore_errors=True)
            shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()













