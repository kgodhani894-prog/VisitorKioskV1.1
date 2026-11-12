from __future__ import annotations

import getpass
import argparse
import json
import os
import stat
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kiosk_server.security import hash_password

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Set the kiosk administrator password.")
parser.add_argument("--config", type=Path, default=ROOT / "config.json", help="Configuration file to update.")
args = parser.parse_args()
config_path = args.config.expanduser().resolve()

password = getpass.getpass("New admin password: ")
confirm = getpass.getpass("Confirm admin password: ")
if password != confirm:
    raise SystemExit("Passwords did not match.")
if len(password) < 10:
    raise SystemExit("Use at least 10 characters.")

config = json.loads(config_path.read_text(encoding="utf-8"))
config["admin_password_hash"] = hash_password(password)
temporary = config_path.with_suffix(config_path.suffix + ".tmp")
temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
if os.name == "posix":
    os.chmod(temporary, stat.S_IMODE(config_path.stat().st_mode))
os.replace(temporary, config_path)
print("Admin password updated.")
