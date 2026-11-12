from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kiosk_server.backups import BACKUP_MANIFEST, _sync_directory, verify_backup


DEFAULT_RUNTIME_ROOT = Path("/var/lib/elite-rf-visitor-kiosk")
SERVICE_NAME = "elite-rf-visitor-kiosk"
BACKUP_SERVICE_NAME = "elite-rf-visitor-kiosk-backup.service"
BACKUP_TIMER_NAME = "elite-rf-visitor-kiosk-backup.timer"
SERVICE_USER = "eliterfkiosk"
SERVICE_GROUP = "eliterfkiosk"
RESTORE_PATHS = ("data", "storage/photos", "config.json")


def _validate_archive_layout(archive_path: Path) -> list[str]:
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        files = [name for name in names if name != BACKUP_MANIFEST and name != "RELEASE_MANIFEST.txt"]
        for name in files:
            if name not in {"config.json", "data/visitor-kiosk.sqlite3"} and not name.startswith("storage/photos/"):
                raise ValueError("Backup contains a file outside the supported runtime data paths.")
            info = archive.getinfo(name)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Backup contains a symbolic link; restore was cancelled.")
        if "config.json" not in files or "data/visitor-kiosk.sqlite3" not in files:
            raise ValueError("Backup is missing required runtime data.")
        return files


def _set_service_permissions(path: Path, uid: int | None, gid: int | None) -> None:
    for current in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if uid is not None and gid is not None:
            os.chown(current, uid, gid, follow_symlinks=False)
        os.chmod(current, 0o750 if current.is_dir() else 0o640, follow_symlinks=False)
    if uid is not None and gid is not None:
        os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, 0o750 if path.is_dir() else 0o640, follow_symlinks=False)


def restore_backup(
    archive_path: Path,
    runtime_root: Path,
    *,
    apply: bool = False,
    service_uid: int | None = None,
    service_gid: int | None = None,
) -> Path | None:
    """Verify or transactionally restore a backup, preserving prior state for rollback."""
    archive_path = Path(archive_path).expanduser().resolve(strict=True)
    raw_runtime_root = Path(runtime_root).expanduser()
    if raw_runtime_root.is_symlink():
        raise ValueError("Refusing to restore through a symbolic link.")
    runtime_root = raw_runtime_root.resolve(strict=True)
    if not archive_path.is_file() or not runtime_root.is_dir():
        raise ValueError("The backup archive and existing runtime directory are required.")
    if runtime_root == Path(runtime_root.anchor) or runtime_root.is_symlink():
        raise ValueError("Refusing to restore into a filesystem root or symbolic link.")
    marker = runtime_root / ".restore-in-progress"
    if marker.exists() or marker.is_symlink():
        raise RuntimeError("An earlier restore is unfinished; keep the service stopped and contact IT.")

    verify_backup(archive_path)
    files = _validate_archive_layout(archive_path)
    if not apply:
        return None

    for relative in RESTORE_PATHS:
        destination = runtime_root / relative
        if destination.is_symlink():
            raise ValueError(f"Refusing to replace symbolic link at {relative}.")
    photos_parent = runtime_root / "storage"
    if photos_parent.is_symlink():
        raise ValueError("Refusing to restore through a symbolic link.")

    with ZipFile(archive_path) as archive:
        archive_bytes = sum(archive.getinfo(name).file_size for name in files)
    reserve_bytes = 64 * 1024 * 1024
    if shutil.disk_usage(runtime_root).free < archive_bytes + reserve_bytes:
        raise OSError("Not enough free space to stage the restore safely; live data was not changed.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rollback_root = runtime_root / f".restore-rollback-{stamp}-{uuid.uuid4().hex[:8]}"
    rollback_root.mkdir(mode=0o700)
    staging_root: Path | None = None
    moved_old: list[str] = []
    installed: list[str] = []
    marker_created = False
    try:
        with tempfile.TemporaryDirectory(prefix=".restore-stage-", dir=runtime_root) as temporary:
            staging_root = Path(temporary)
            (staging_root / "data").mkdir()
            (staging_root / "storage" / "photos").mkdir(parents=True)
            with ZipFile(archive_path) as archive:
                for name in files:
                    destination = staging_root / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as source, destination.open("xb") as target:
                        shutil.copyfileobj(source, target)
            if service_uid is not None and service_gid is not None:
                _set_service_permissions(staging_root / "data", service_uid, service_gid)
                _set_service_permissions(staging_root / "storage" / "photos", service_uid, service_gid)
                config_path = staging_root / "config.json"
                os.chown(config_path, service_uid, service_gid)
                os.chmod(config_path, 0o640)

            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            marker_created = True
            try:
                os.write(descriptor, (rollback_root.name + "\n").encode("ascii"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _sync_directory(runtime_root)

            for relative in RESTORE_PATHS:
                live_path = runtime_root / relative
                staged_path = staging_root / relative
                previous_path = rollback_root / relative
                live_path.parent.mkdir(parents=True, exist_ok=True)
                previous_path.parent.mkdir(parents=True, exist_ok=True)
                if live_path.exists():
                    os.replace(live_path, previous_path)
                    moved_old.append(relative)
                    _sync_directory(live_path.parent)
                    _sync_directory(previous_path.parent)
                    if previous_path.parent.parent != previous_path.parent:
                        _sync_directory(previous_path.parent.parent)
                os.replace(staged_path, live_path)
                installed.append(relative)
                _sync_directory(staged_path.parent)
                _sync_directory(live_path.parent)
            marker.unlink()
            _sync_directory(runtime_root)
    except Exception:
        if marker_created and not marker.exists():
            try:
                marker.write_text(rollback_root.name + "\n", encoding="ascii")
                os.chmod(marker, 0o600)
                _sync_directory(runtime_root)
            except Exception:
                pass
        rollback_error: Exception | None = None
        for relative in reversed(installed):
            live_path = runtime_root / relative
            try:
                if live_path.exists():
                    failed_path = rollback_root / "failed-new" / relative
                    failed_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(live_path, failed_path)
                    _sync_directory(live_path.parent)
                    _sync_directory(failed_path.parent)
            except Exception as exc:
                rollback_error = exc
        for relative in reversed(moved_old):
            previous_path = rollback_root / relative
            try:
                if previous_path.exists():
                    os.replace(previous_path, runtime_root / relative)
                    _sync_directory((runtime_root / relative).parent)
                    _sync_directory(previous_path.parent)
            except Exception as exc:
                rollback_error = exc
        if rollback_error is None:
            shutil.rmtree(rollback_root, ignore_errors=True)
            if marker_created:
                marker.unlink(missing_ok=True)
                _sync_directory(runtime_root)
        else:
            raise RuntimeError(
                f"Restore failed and automatic rollback was incomplete; keep the service stopped, preserve {rollback_root}, and contact IT."
            ) from rollback_error
        raise
    return rollback_root


def _require_service_stopped() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise PermissionError("Apply mode must be run as root on the Debian server.")
    if shutil.which("systemctl") is None:
        raise RuntimeError("Could not verify the kiosk service state; systemctl is required.")
    for unit in (SERVICE_NAME, BACKUP_SERVICE_NAME, BACKUP_TIMER_NAME):
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            raise RuntimeError(f"Stop {unit} before applying a restore.")
        if result.returncode != 3:
            raise RuntimeError(f"Could not confirm that {unit} is stopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify or restore a verified Elite RF kiosk backup.")
    parser.add_argument("archive", type=Path, help="Path to the completed backup ZIP")
    parser.add_argument("--root", type=Path, default=DEFAULT_RUNTIME_ROOT, help="Existing kiosk runtime root")
    parser.add_argument("--apply", action="store_true", help="Replace live data after verification")
    args = parser.parse_args()
    try:
        if args.apply:
            if args.root.expanduser().resolve() != DEFAULT_RUNTIME_ROOT:
                raise ValueError("Apply mode is restricted to /var/lib/elite-rf-visitor-kiosk.")
            _require_service_stopped()
            import grp
            import pwd

            uid = pwd.getpwnam(SERVICE_USER).pw_uid
            gid = grp.getgrnam(SERVICE_GROUP).gr_gid
        else:
            uid = gid = None
        rollback = restore_backup(
            args.archive,
            args.root,
            apply=args.apply,
            service_uid=uid,
            service_gid=gid,
        )
        if rollback is None:
            print("Backup verified. No live data was changed. Add --apply to restore after stopping the service.")
        else:
            print(f"Restore completed. Previous live data is preserved at {rollback}.")
            print("Review service health and visitor records before manually removing that rollback folder.")
        return 0
    except Exception as exc:
        print(f"Restore refused or failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
