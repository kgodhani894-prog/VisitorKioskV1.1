# Elite RF Backup and Restore Drill

This drill verifies a backup without overwriting the live kiosk. Use synthetic
or already-approved test data. Do not perform the first restore in the live
project folder.

## Create a backup

1. On Debian, start the installed one-shot backup service. It creates a local
   verified archive and copies it to the configured secondary mount when that
   destination is available:

   ```bash
   sudo systemctl start elite-rf-visitor-kiosk-backup.service
   sudo journalctl -u elite-rf-visitor-kiosk-backup.service --no-pager -n 30
   ```

   The Debian archive is saved under
   `/var/lib/elite-rf-visitor-kiosk/backups/`.

2. For the Windows-host alternative, run `python tools/backup.py` from the
   application folder in PowerShell. It saves the archive in `backups/`.

3. Confirm the backup service completed and a new verified ZIP appears in the
   expected backup directory. Do not treat a file beginning with `.` or ending
   in `.tmp` as a completed backup.

The ZIP contains `config.json`, a consistent SQLite snapshot at
`data/visitor-kiosk.sqlite3`, every file under `storage/photos/`, and a
`BACKUP_MANIFEST.json` inventory. It also includes `RELEASE_MANIFEST.txt` when
that file is present. The backend checks the ZIP, inventory, photo count, SQLite
integrity and foreign keys, and confirms every visitor record's photo is present
before accepting or copying the backup.

The ordinary backup ZIP deliberately excludes `.env`, Debian's
`/etc/elite-rf-visitor-kiosk/kiosk.env`, TLS private keys, and signing keys
because the ZIP is not encrypted. The Debian settings file contains the
session-signing secret and Microsoft 365 configuration paths. Store a protected
recovery copy of required settings and private keys in the company's encrypted
secrets vault. A replacement session secret invalidates existing admin
sessions but does not affect visitor records. Treat the backup ZIP and the
separately protected recovery material as sensitive.

## Protect backups

Backups contain visitor records, photos, and possibly authentication material
from `config.json`, so they must have restricted access.
A backup on the same computer does not protect against theft, fire, ransomware,
or total disk failure. Set `KIOSK_BACKUP_COPY_PATH` to encrypted external
storage or a company-approved protected destination. The backend verifies that
secondary copy and automatically replaces it if it is missing or damaged.
Protect `.env` or Debian's `/etc/elite-rf-visitor-kiosk/kiosk.env` separately
under the company's secret-management policy; do not place it in an ordinary
or shared backup folder.

## Disposable restore drill

1. Stop any disposable test server being used for this drill.
2. Create a new clearly named restore folder outside the production project,
   such as `EliteRF-Restore-Check-YYYYMMDD`.
3. Copy one completed backup ZIP into that folder. Do not move or rename the
   original production backup.
4. Keep the extracted archive private because it contains visitor data and
   authentication material. On Debian, set the archive path and use a new
   restore directory:

   ```bash
   ARCHIVE=/path/to/copied-backup.zip
   RESTORE_DIR=/var/tmp/elite-rf-restore-check-YYYYMMDD
   if [ -e "$RESTORE_DIR" ]; then echo "Choose a new restore directory" >&2; exit 1; fi
   sudo install -d -m 0700 "$RESTORE_DIR/extracted"
   cd /opt/elite-rf-visitor-kiosk/app
   sudo ./../venv/bin/python -c 'from pathlib import Path; from kiosk_server.backups import verify_backup; verify_backup(Path("'"$ARCHIVE"'")); print("Backup verified.")'
   sudo ./../venv/bin/python -m zipfile -e "$ARCHIVE" "$RESTORE_DIR/extracted"
   ```

   On Windows, use Explorer's **Extract All**, or PowerShell:

   ```powershell
   Expand-Archive -LiteralPath .\elite-rf-kiosk-backup-YYYYMMDD-HHMMSS.zip -DestinationPath .\extracted
   ```

5. Verify that `BACKUP_MANIFEST.json` and `data/visitor-kiosk.sqlite3` exist.
6. Verify that `storage/photos/` exists and contains the expected image files
   when the backup included photos.
7. On Debian, run a read-only SQLite integrity check:

   ```bash
   sudo ./../venv/bin/python -c 'import sqlite3; db=sqlite3.connect("file:'"$RESTORE_DIR"'/extracted/data/visitor-kiosk.sqlite3?mode=ro", uri=True); print(db.execute("PRAGMA quick_check").fetchone()[0]); db.close()'
   ```

   Confirm it prints `ok`. Use an approved SQLite viewer or have IT confirm
   the expected visitor records and that each referenced `photo_filename` has
   a matching image under `storage/photos/`.
8. Do not start the normal production launcher against the extracted folder.
9. Remove the disposable restore folder after the result is recorded.

## Production restore on Debian

Use a planned maintenance window and an archive that passed the verification
above. Confirm the server has enough free space for the archive's uncompressed
contents plus at least 64 MiB for staging. A restore from an older archive can
reintroduce visits that were deleted after that archive was made.

1. Stop the app, backup service, and backup timer; confirm all three are inactive:

   ```bash
   sudo systemctl stop elite-rf-visitor-kiosk
   sudo systemctl stop elite-rf-visitor-kiosk-backup.timer
   sudo systemctl stop elite-rf-visitor-kiosk-backup.service
   systemctl is-active elite-rf-visitor-kiosk
   systemctl is-active elite-rf-visitor-kiosk-backup.service
   systemctl is-active elite-rf-visitor-kiosk-backup.timer
   ```

2. Run the verifier-only command. It checks the archive, database, foreign
   keys, photo references, supported paths, and available space without
   changing the live data:

   ```bash
   sudo /opt/elite-rf-visitor-kiosk/venv/bin/python \
     /opt/elite-rf-visitor-kiosk/app/tools/restore_backup.py \
     /path/to/verified-backup.zip
   ```

3. If verification succeeds and the selected archive is approved, apply it:

   ```bash
   sudo /opt/elite-rf-visitor-kiosk/venv/bin/python \
     /opt/elite-rf-visitor-kiosk/app/tools/restore_backup.py \
     /path/to/verified-backup.zip --apply
   ```

   Apply mode is restricted to `/var/lib/elite-rf-visitor-kiosk`, requires
   root, and refuses to run while the app or backup units are active. The
   existing database, photos, and configuration are preserved in the printed
   `.restore-rollback-*` folder. Do not remove that folder until the restored
   service and visitor records have been checked.

4. Start the service and check its status and logs:

   ```bash
   sudo systemctl start elite-rf-visitor-kiosk
   sudo systemctl status elite-rf-visitor-kiosk --no-pager
   sudo journalctl -u elite-rf-visitor-kiosk --no-pager -n 50
   ```

   Confirm the admin dashboard opens, records are present, and visitor photos
   load. Keep the rollback folder until the recovery is accepted, then remove
   that exact folder under the company's data-cleanup procedure.

If power or the restore process is interrupted during replacement, the
`.restore-in-progress` marker prevents both the backend and backup service from
starting against potentially mixed files. Keep the service stopped and have IT
inspect the named rollback folder and complete recovery before removing the
marker. Do not delete the marker to force startup.

## Verify that production was untouched

After the drill, confirm that these production paths were not overwritten or
deleted. On Debian, the live paths are under `/var/lib/elite-rf-visitor-kiosk/`
and the separate settings are under `/etc/elite-rf-visitor-kiosk/`:

```text
data/
storage/photos/
backups/
config.json
.env
```

The original ZIP must still exist and its timestamp must be unchanged.

## Production restore warning

Never overwrite `data/`, `storage/photos/`, `config.json`, or `.env` while the
production backend is running. On Debian, never overwrite the live data under
`/var/lib/elite-rf-visitor-kiosk/` or `/etc/elite-rf-visitor-kiosk/` during this
verification drill. Never make the first restore attempt directly in the live
project. A real replacement requires a maintenance window, a fresh pre-restore
backup, verified source archives, and an authorized operator. Restoring
configuration or secrets requires additional care. Stop and contact company IT
if any step is unclear.

## Recovery result

Backup file checked: ______________________________
Database found and readable:  Pass / Fail
Expected records found:       Pass / Fail
Referenced photos found:      Pass / Fail
Production paths unchanged:   Pass / Fail
Restore folder removed:       Pass / Fail

Operator/IT initials: ______________________    Date: __________

