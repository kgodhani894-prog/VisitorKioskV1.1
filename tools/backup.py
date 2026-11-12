from __future__ import annotations

import argparse
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kiosk_server.backups import create_backup, record_backup_success
from kiosk_server.server import load_local_env

PROJECT_ROOT = ROOT.parent if ROOT.name.lower() == "app" else ROOT
load_local_env(PROJECT_ROOT)
parser = argparse.ArgumentParser(description="Create and verify a visitor kiosk backup.")
parser.add_argument("--root", type=Path, default=ROOT, help="Runtime data root containing config.json, data/, and storage/.")
args = parser.parse_args()
copy_value = os.environ.get("KIOSK_BACKUP_COPY_PATH", "").strip()
retention = max(1, int(os.environ.get("KIOSK_BACKUP_RETENTION", "30")))
root = args.root.expanduser().resolve()
backup = create_backup(root, retention_count=retention, copy_destination=Path(copy_value).expanduser() if copy_value else None)
record_backup_success(root)
print(backup)
