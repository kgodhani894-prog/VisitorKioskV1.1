from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

if os.name == "posix":
    import grp
else:
    grp = None


ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
REQUIRED_VALUES = {
    "KIOSK_EXTERNAL_HTTPS": "true",
    "KIOSK_DISABLE_REMOTE_OWNER_SETUP": "true",
    "KIOSK_REQUIRE_MOUNTED_BACKUP": "true",
    "KIOSK_BACKUP_TIMER_MANAGED": "true",
}


def _plain_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def reconcile_environment(path: Path, service_group: str | None = None) -> None:
    """Enforce Debian safety flags while preserving operator-supplied settings."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError("The kiosk environment file does not exist.")
    original_lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in original_lines:
        match = ASSIGNMENT.match(line.strip())
        if match:
            values.setdefault(match.group(1), match.group(2))

    session_secret = _plain_value(values.get("KIOSK_SESSION_SECRET", ""))
    if not session_secret or session_secret == "replace-with-a-long-random-secret":
        values["KIOSK_SESSION_SECRET"] = secrets.token_urlsafe(48)
    for key, value in REQUIRED_VALUES.items():
        values[key] = value

    retention = _plain_value(values.get("KIOSK_BACKUP_RETENTION", ""))
    if not retention.isdecimal() or int(retention) < 1:
        retention = "30"
    values["KIOSK_BACKUP_RETENTION"] = retention

    controlled_keys = set(REQUIRED_VALUES) | {"KIOSK_SESSION_SECRET", "KIOSK_BACKUP_RETENTION"}
    output: list[str] = []
    for line in original_lines:
        match = ASSIGNMENT.match(line.strip())
        if match and match.group(1) in controlled_keys:
            continue
        output.append(line)
    output.extend(f"{key}={values[key]}" for key in sorted(controlled_keys))

    descriptor, temporary_name = tempfile.mkstemp(prefix=".kiosk.env.", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            assert grp is not None
            group_id = grp.getgrnam(service_group).gr_gid if service_group else path.stat().st_gid
            os.chown(temporary, 0, group_id)
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Secure Debian kiosk service settings.")
    parser.add_argument("environment_file", type=Path)
    parser.add_argument("--group", required=True, help="Service group that may read the environment file.")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Run this environment setup as root on Debian.")
    reconcile_environment(args.environment_file, args.group)


if __name__ == "__main__":
    main()
