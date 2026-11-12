from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
DEFAULT_OUTPUT_ROOT = SOURCE_ROOT.parent / "release"
PACKAGE_NAME = "Elite-RF-Visitor-Kiosk"
MAX_PACKAGE_FILES = 43
MAX_PACKAGE_BYTES = 400_000
FORBIDDEN_NAMES = {
    ".env",
    ".git",
    ".codex",
    ".agents",
    "tests",
    "__pycache__",
    "backups",
    "data",
    "storage",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db", ".zip", ".lnk"}

FILES = (
    "INSTALL_INSTRUCTIONS.txt",
    "requirements.txt",
    ".env.example",
    "SET_UP_ELITE_RF.bat",
    "development/START_ELITE_RF_KIOSK.bat",
    "development/START_TEST_MODE.bat",
)
PACKAGE_ROOT_NAMES = {
    "SET_UP_ELITE_RF.bat": "SET UP ELITE RF ON THIS COMPUTER.bat",
    "development/START_ELITE_RF_KIOSK.bat": "START ELITE RF VISITOR KIOSK.bat",
    "development/START_TEST_MODE.bat": "START ELITE RF SAFE TEST MODE.bat",
}
APP_FILES = ("run.py",)
KIOSK_SERVER_FILES = (
    "__init__.py",
    "backups.py",
    "db.py",
    "microsoft365_setup.py",
    "notifications.py",
    "pdf_export.py",
    "security.py",
    "server.py",
    "storage_lock.py",
)
STATIC_FILES = (
    "admin.css",
    "admin.html",
    "admin.js",
    "elite-rf-logo.png",
    "kiosk.css",
    "kiosk.html",
    "kiosk.js",
)
TOOLS = (
    "backup.py",
    "company_setup.ps1",
    "create_shortcuts.py",
    "diagnose.ps1",
    "install_startup_tasks.ps1",
    "launch.ps1",
    "python_runtime.ps1",
    "set_admin_password.py",
    "supervise.py",
    "restore_backup.py",
    "windows_security.ps1",
)
LINUX_TOOLS = (
    "configure_kiosk_env.py",
    "install_debian.sh",
    "elite-rf-visitor-kiosk.service",
    "elite-rf-visitor-kiosk-backup.service",
    "elite-rf-visitor-kiosk-backup.timer",
    "elite-rf-visitor-kiosk.apache.conf",
)
OPERATOR_DOCS = (
    "COMPANY_PILOT_RUNBOOK.md",
    "BACKUP_RESTORE_DRILL.md",
    "MICROSOFT_365_EMAIL_SETUP.md",
    "DEBIAN_APACHE_DEPLOYMENT.md",
    "LOG_RETENTION_AND_RECOVERY.md",
)


def _safe_release_root(source_root: Path, output_root: Path) -> Path:
    release_root = output_root.resolve()
    if release_root == source_root or source_root in release_root.parents:
        raise ValueError("Release output must be outside the application source folder.")
    return release_root


def _copy_file(source_root: Path, destination: Path, relative: str) -> None:
    source = source_root / relative
    if not source.is_file():
        raise FileNotFoundError(f"Required release file is missing: {relative}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_allowlisted_files(source_root: Path, destination: Path, directory: str, files: tuple[str, ...]) -> None:
    for filename in files:
        relative = f"{directory}/{filename}"
        _copy_file(source_root, destination / relative, relative)


def _is_forbidden(relative: Path) -> bool:
    lowered = {part.lower() for part in relative.parts}
    return bool(lowered & FORBIDDEN_NAMES) or relative.suffix.lower() in FORBIDDEN_SUFFIXES


def _sanitized_config() -> dict:
    from kiosk_server.security import hash_password

    return {
        "company_name": "Elite RF",
        "database_path": "data/visitor-kiosk.sqlite3",
        "photo_storage_path": "storage/photos",
        "admin_password_hash": hash_password("change-me-before-use"),
        "session_secret": "replace-this-with-a-long-random-secret",
        "automatic_backup_hours": 24,
        "cookie_secure": False,
        "max_photo_bytes": 5_242_880,
        "notification_settings": {
            "enabled": True,
            "provider": "microsoft_graph_email",
            "message_template": "{visitor_name} from {company} is waiting for you at {location}.",
        },
        "employees": [],
    }


def _write_operator_guide(source_root: Path, destination: Path) -> None:
    install = (source_root / "INSTALL_INSTRUCTIONS.txt").read_text(encoding="utf-8")
    install = install.replace(
        "For the complete company pilot procedure, see `docs\\COMPANY_PILOT_RUNBOOK.md`.\n",
        "",
    ).replace(
        "For the safe backup verification procedure, see `docs\\BACKUP_RESTORE_DRILL.md`.\n",
        "",
    )
    install = install.replace("python tools\\", "python app\\tools\\")
    install = install.replace(".\\tools\\", ".\\app\\tools\\")
    install = install.replace("python run.py", "python app\\run.py")
    sections = [install.rstrip()]
    for document in OPERATOR_DOCS:
        title = document.removesuffix(".md").replace("_", " ")
        text = (source_root / "docs" / document).read_text(encoding="utf-8").strip()
        sections.append(f"\n\n===== {title} =====\n\n{text}")
    (destination / "INSTALL_INSTRUCTIONS.txt").write_text("".join(sections) + "\n", encoding="utf-8")


def _write_manifest(destination: Path) -> None:
    manifest = """Elite RF Visitor Kiosk Company Deployment Package

Purpose: clean company runtime package. Elite RF's selected backend host is Debian Linux behind Apache.
Python: 3.11 or newer is required.
Debian setup: see the Debian and Apache procedure in INSTALL_INSTRUCTIONS.txt.
Windows-host setup and startup files are retained only for an IT-approved Windows alternative.
Safe testing: run START ELITE RF SAFE TEST MODE.bat; it uses disposable data and never sends email.

Included operator document:
- INSTALL_INSTRUCTIONS.txt

Intentionally excluded:
- .env and all secret values
- live databases, photos, backups, and logs
- machine-specific shortcuts
- development tests and internal workflow/review files

After moving the package, regenerate shortcuts with:
python app\\tools\\create_shortcuts.py --python "C:\\Path\\To\\Approved\\python.exe"

Run the diagnostic with:
powershell -ExecutionPolicy RemoteSigned -File .\\app\\tools\\diagnose.ps1
"""
    (destination / "RELEASE_MANIFEST.txt").write_text(manifest, encoding="utf-8")


def _validate_package(destination: Path, source_root: Path) -> None:
    packaged_files = [path for path in destination.rglob("*") if path.is_file()]
    if len(packaged_files) > MAX_PACKAGE_FILES:
        raise ValueError("Release package exceeds the approved file-count budget.")
    if sum(path.stat().st_size for path in packaged_files) > MAX_PACKAGE_BYTES:
        raise ValueError("Release package exceeds the approved size budget.")
    for path in destination.rglob("*"):
        relative = path.relative_to(destination)
        if _is_forbidden(relative):
            raise ValueError(f"Forbidden path entered release package: {relative}")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise ValueError(f"Forbidden file entered release package: {relative}")
        if path.is_file():
            content = path.read_bytes()
            if b"-----BEGIN " in content and b"PRIVATE KEY-----" in content:
                raise ValueError(f"Private-key material detected in release file: {relative}")
            if str(source_root.resolve()).lower().encode() in content.lower():
                raise ValueError(f"Developer path detected in release file: {relative}")
            if path.name.lower() == ".env.example":
                _validate_example_environment(path)
    config = json.loads((destination / "app" / "config.json").read_text(encoding="utf-8"))
    if config.get("employees") or config.get("session_secret") == "":
        raise ValueError("Release configuration contains operational data or an empty session secret.")


def _validate_example_environment(path: Path) -> None:
    allowed_placeholder = "replace-with-a-long-random-secret"
    sensitive_keys = {
        "KIOSK_ADMIN_PASSWORD_HASH",
        "KIOSK_SESSION_SECRET",
        "KIOSK_TLS_CERT_PATH",
        "KIOSK_TLS_KEY_PATH",
        "KIOSK_PUBLIC_HOSTNAME",
        "M365_TENANT_ID",
        "M365_CLIENT_ID",
        "M365_SENDER_ADDRESS",
        "M365_CERTIFICATE_PATH",
        "M365_CERTIFICATE_THUMBPRINT",
        "M365_PRIVATE_KEY_PATH",
        "M365_GRAPH_SCOPE",
        "M365_REDIRECT_URI",
    }
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in sensitive_keys:
            value = value.strip()
            if key.strip() == "KIOSK_SESSION_SECRET" and value == allowed_placeholder:
                continue
            if key.strip() == "M365_GRAPH_SCOPE" and value == "https://graph.microsoft.com/.default":
                continue
            if key.strip() == "M365_REDIRECT_URI" and value == "https://kiosk.example.internal/oauth/microsoft/callback":
                continue
            if value:
                raise ValueError(f"Populated sensitive value detected in release file: {path.name}")


def _cleanup_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _restore_backup(backup: Path | None, target: Path) -> None:
    if backup is None or not backup.exists():
        return
    _cleanup_path(target)
    backup.rename(target)


def build_release(source_root: Path = SOURCE_ROOT, output_root: Path = DEFAULT_OUTPUT_ROOT) -> tuple[Path, Path]:
    source_root = source_root.resolve()
    release_root = _safe_release_root(source_root, output_root)
    archive = release_root / f"{PACKAGE_NAME}.zip"
    release_root.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{PACKAGE_NAME.lower()}-staging-", dir=str(release_root)))
    staging_destination = staging_root / PACKAGE_NAME
    staging_destination.mkdir(parents=True, exist_ok=True)
    app_destination = staging_destination / "app"
    app_destination.mkdir(parents=True, exist_ok=True)
    staging_archive = staging_root / f"{PACKAGE_NAME}.zip"
    legacy_destination = release_root / PACKAGE_NAME
    backup_destination: Path | None = None
    backup_archive: Path | None = None
    destination_backed_up = False
    archive_backed_up = False
    archive_replaced = False
    try:
        _copy_file(source_root, staging_destination / ".env.example", ".env.example")
        for relative in FILES:
            if relative == ".env.example":
                continue
            target_name = PACKAGE_ROOT_NAMES.get(relative, relative)
            _copy_file(source_root, staging_destination / target_name, relative)
        for relative in APP_FILES:
            _copy_file(source_root, app_destination / relative, relative)
        _copy_file(source_root, app_destination / "requirements.txt", "requirements.txt")
        _copy_allowlisted_files(source_root, app_destination, "kiosk_server", KIOSK_SERVER_FILES)
        _copy_allowlisted_files(source_root, app_destination, "static", STATIC_FILES)
        for tool in TOOLS:
            _copy_file(source_root, app_destination / "tools" / tool, f"tools/{tool}")
        for tool in LINUX_TOOLS:
            _copy_file(source_root, app_destination / "tools" / "linux" / tool, f"tools/linux/{tool}")

        (app_destination / "config.json").write_text(json.dumps(_sanitized_config(), indent=2) + "\n", encoding="utf-8")
        _write_operator_guide(source_root, staging_destination)
        _write_manifest(app_destination)
        _validate_package(staging_destination, source_root)

        with ZipFile(staging_archive, "w", ZIP_DEFLATED) as zip_file:
            for path in staging_destination.rglob("*"):
                if path.is_file():
                    zip_file.write(path, path.relative_to(staging_destination).as_posix())

        try:
            if legacy_destination.exists():
                backup_destination = legacy_destination.parent / f".{PACKAGE_NAME}.backup-{uuid.uuid4().hex}"
                legacy_destination.rename(backup_destination)
                destination_backed_up = True
            if archive.exists():
                backup_archive = archive.parent / f".{PACKAGE_NAME}.backup-{uuid.uuid4().hex}.zip"
                archive.rename(backup_archive)
                archive_backed_up = True
            staging_archive.replace(archive)
            archive_replaced = True
        except Exception:
            if archive_replaced:
                _cleanup_path(archive)
            if destination_backed_up:
                _restore_backup(backup_destination, legacy_destination)
            if archive_backed_up:
                _restore_backup(backup_archive, archive)
            raise
        else:
            if backup_destination:
                _cleanup_path(backup_destination)
            if backup_archive:
                _cleanup_path(backup_archive)
        return release_root, archive
    finally:
        _cleanup_path(staging_root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the clean Elite RF company deployment package.")
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    destination, archive = build_release(args.source_root, args.output_root)
    print(f"Release folder: {destination}")
    print(f"Release ZIP: {archive}")
    print(f"Built at: {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
