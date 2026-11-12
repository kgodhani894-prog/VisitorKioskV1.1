from __future__ import annotations

import os
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .storage_lock import storage_operation_lock


BACKUP_PREFIX = "elite-rf-kiosk-backup-"
BACKUP_MANIFEST = "BACKUP_MANIFEST.json"
FORBIDDEN_BACKUP_NAMES = {".env", "keystore.properties"}
FORBIDDEN_BACKUP_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"}


def _sync_file(path: Path) -> None:
    """Flush file contents to stable storage before publishing a backup."""
    with Path(path).open("r+b") as file_handle:
        os.fsync(file_handle.fileno())


def _sync_directory(path: Path) -> None:
    """Flush directory-entry changes on POSIX hosts when supported."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_backup(path: Path) -> None:
    """Verify ZIP structure, CRCs, and the SQLite snapshot before accepting it."""
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            if archive.testzip() is not None:
                raise ValueError("Backup ZIP contains damaged data.")
            for name in names:
                item = Path(name)
                if item.is_absolute() or ".." in item.parts:
                    raise ValueError("Backup ZIP contains an unsafe path.")
                if item.name.lower() in FORBIDDEN_BACKUP_NAMES or item.suffix.lower() in FORBIDDEN_BACKUP_SUFFIXES:
                    raise ValueError("Backup ZIP contains secret-key material.")
            if len(names) != len(set(names)):
                raise ValueError("Backup ZIP contains duplicate file entries.")
            if BACKUP_MANIFEST not in names:
                raise ValueError("Backup ZIP is missing its recovery manifest.")
            manifest = json.loads(archive.read(BACKUP_MANIFEST).decode("utf-8"))
            expected_files = set(manifest.get("files", []))
            actual_files = set(names) - {BACKUP_MANIFEST}
            if expected_files != actual_files:
                raise ValueError("Backup ZIP contents do not match its recovery manifest.")
            if "config.json" not in actual_files or "data/visitor-kiosk.sqlite3" not in actual_files:
                raise ValueError("Backup ZIP is missing required kiosk state.")
            photo_count = len([name for name in actual_files if name.startswith("storage/photos/")])
            if int(manifest.get("photo_count", -1)) != photo_count:
                raise ValueError("Backup ZIP photo inventory is incomplete.")
            if "data/visitor-kiosk.sqlite3" in names:
                with tempfile.TemporaryDirectory(prefix="elite-rf-backup-verify-") as temp_dir:
                    database_path = Path(temp_dir) / "visitor-kiosk.sqlite3"
                    database_path.write_bytes(archive.read("data/visitor-kiosk.sqlite3"))
                    with closing(sqlite3.connect(database_path)) as database:
                        result = database.execute("PRAGMA quick_check").fetchone()
                        foreign_key_errors = database.execute("PRAGMA foreign_key_check").fetchall()
                        photo_filenames = [
                            str(row[0]) for row in database.execute("SELECT photo_filename FROM visits")
                        ]
                    if not result or str(result[0]).lower() != "ok":
                        raise ValueError("Backup database integrity check failed.")
                    if foreign_key_errors:
                        raise ValueError("Backup database contains foreign-key violations.")
                    for filename in photo_filenames:
                        if (
                            not filename
                            or filename in {".", ".."}
                            or "/" in filename
                            or "\\" in filename
                            or Path(filename).name != filename
                        ):
                            raise ValueError("Backup database contains an unsafe visitor photo filename.")
                        if f"storage/photos/{filename}" not in actual_files:
                            raise ValueError("Backup is missing a photo referenced by a visitor record.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Backup verification failed.") from exc


def latest_verified_backup(backups: Path) -> Path | None:
    for candidate in sorted(backups.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True):
        try:
            verify_backup(candidate)
        except ValueError:
            continue
        return candidate
    return None


def backup_is_due(root: Path, interval_seconds: int, now: float | None = None) -> bool:
    backups = root / "backups"
    latest = latest_verified_backup(backups) if backups.exists() else None
    if latest is None:
        return True
    current = datetime.now().timestamp() if now is None else now
    return current - latest.stat().st_mtime >= interval_seconds


def _apply_retention(directory: Path, keep: int) -> None:
    if keep < 1 or not directory.exists():
        return
    candidates = sorted(directory.glob(f"{BACKUP_PREFIX}*.zip"), reverse=True)
    removed = False
    for old_backup in candidates[keep:]:
        try:
            old_backup.unlink()
            removed = True
        except FileNotFoundError:
            continue
    if removed:
        _sync_directory(directory)


def _mounted_backup_required() -> bool:
    return os.environ.get("KIOSK_REQUIRE_MOUNTED_BACKUP", "").strip().lower() in {"1", "true", "yes", "on"}


def _require_mounted_destination(destination: Path) -> None:
    required = _mounted_backup_required()
    if required and os.name == "posix" and not destination.is_mount():
        raise OSError("The configured secondary backup destination is not a mounted filesystem.")


def copy_verified_backup(source: Path, destination: Path, *, retention_count: int = 0) -> Path:
    _require_mounted_destination(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    temporary = destination / f".{source.name}.tmp"
    try:
        shutil.copy2(source, temporary)
        _sync_file(temporary)
        verify_backup(temporary)
        os.replace(temporary, target)
        _sync_directory(destination)
    finally:
        temporary.unlink(missing_ok=True)
    _apply_retention(destination, retention_count)
    return target


def ensure_secondary_backup(source: Path, destination: Path, *, retention_count: int = 0) -> Path:
    """Keep one verified secondary copy, replacing a missing or damaged copy."""
    _require_mounted_destination(destination)
    target = destination / source.name
    if target.exists():
        try:
            verify_backup(target)
        except ValueError:
            pass
        else:
            return target
    return copy_verified_backup(source, destination, retention_count=retention_count)


def record_backup_success(root: Path) -> None:
    """Atomically record that the complete backup operation succeeded."""
    directory = Path(root) / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ".last-successful-backup"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            prefix=".last-successful-backup-",
            dir=directory,
            delete=False,
        ) as marker_file:
            temporary = Path(marker_file.name)
            marker_file.write(datetime.now(timezone.utc).isoformat() + "\n")
            marker_file.flush()
            os.fsync(marker_file.fileno())
        os.replace(temporary, marker)
        _sync_directory(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def backup_freshness(root: Path, *, now: float | None = None) -> tuple[str, int | None]:
    """Return the age/status of the last completed backup without opening archives."""
    marker = Path(root) / "backups" / ".last-successful-backup"
    try:
        modified = marker.stat().st_mtime
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unknown", None
    current = datetime.now(timezone.utc).timestamp() if now is None else now
    age = max(0, int(current - modified))
    status = "critical" if age >= 48 * 60 * 60 else "warning" if age >= 30 * 60 * 60 else "ok"
    return status, age


def create_backup(
    root: Path,
    *,
    retention_count: int = 0,
    copy_destination: Path | None = None,
) -> Path:
    """Create, verify, retain, and optionally copy an atomic kiosk backup."""
    if copy_destination is not None:
        copy_destination = Path(copy_destination)
        _require_mounted_destination(copy_destination)
        if _mounted_backup_required() and os.name == "posix":
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=".elite-rf-backup-write-check-",
                    dir=copy_destination,
                    delete=True,
                ):
                    pass
            except OSError as exc:
                raise OSError("The configured secondary backup destination is not writable by the kiosk service.") from exc

    backups = root / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = backups / f"{BACKUP_PREFIX}{stamp}.zip"
    temporary_zip = backups / f".{target.name}.tmp"

    try:
        with tempfile.TemporaryDirectory(prefix=".elite-rf-backup-stage-", dir=backups) as temp_dir:
            stage = Path(temp_dir)
            snapshot = stage / "visitor-kiosk.sqlite3"
            config_snapshot = stage / "config.json"
            photo_snapshot = stage / "storage" / "photos"
            source = root / "data" / "visitor-kiosk.sqlite3"
            config = root / "config.json"
            if not config.is_file() or not source.is_file():
                raise ValueError("Kiosk configuration or database is missing; backup was not created.")
            with storage_operation_lock(root):
                shutil.copy2(config, config_snapshot)
                with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(snapshot)) as snapshot_db:
                    source_db.backup(snapshot_db)
                with closing(sqlite3.connect(snapshot)) as snapshot_db:
                    has_deletion_queue = snapshot_db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        ("pending_photo_deletions",),
                    ).fetchone()
                    pending_deletions = set()
                    if has_deletion_queue:
                        pending_deletions = {
                            str(row[0])
                            for row in snapshot_db.execute(
                                "SELECT photo_filename FROM pending_photo_deletions"
                            )
                        }
                photos = root / "storage" / "photos"
                if photos.exists():
                    for item in photos.rglob("*"):
                        if not item.is_file() or item.is_symlink():
                            continue
                        if item.name in pending_deletions:
                            continue
                        relative = item.relative_to(photos)
                        staged_photo = photo_snapshot / relative
                        staged_photo.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            os.link(item, staged_photo)
                        except OSError:
                            shutil.copy2(item, staged_photo)

            with ZipFile(temporary_zip, "w", ZIP_DEFLATED) as archive:
                archived_files: list[str] = []
                archive.write(config_snapshot, "config.json")
                archived_files.append("config.json")
                archive.write(snapshot, "data/visitor-kiosk.sqlite3")
                archived_files.append("data/visitor-kiosk.sqlite3")
                if photo_snapshot.exists():
                    for item in photo_snapshot.rglob("*"):
                        if item.is_file():
                            relative = (Path("storage") / "photos" / item.relative_to(photo_snapshot)).as_posix()
                            archive.write(item, relative)
                            archived_files.append(relative)
                release_manifest = root / "RELEASE_MANIFEST.txt"
                if release_manifest.is_file():
                    archive.write(release_manifest, "RELEASE_MANIFEST.txt")
                    archived_files.append("RELEASE_MANIFEST.txt")
                manifest = {
                    "format_version": 1,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "files": sorted(archived_files),
                    "photo_count": len([name for name in archived_files if name.startswith("storage/photos/")]),
                    "secrets_included": True,
                    "encrypted": False,
                }
                archive.writestr(BACKUP_MANIFEST, json.dumps(manifest, indent=2) + "\n")
        _sync_file(temporary_zip)
        verify_backup(temporary_zip)
        os.replace(temporary_zip, target)
        _sync_directory(backups)
        verify_backup(target)
        _apply_retention(backups, retention_count)
        if copy_destination is not None:
            copied = copy_verified_backup(target, Path(copy_destination), retention_count=retention_count)
            verify_backup(copied)
        return target
    finally:
        temporary_zip.unlink(missing_ok=True)
