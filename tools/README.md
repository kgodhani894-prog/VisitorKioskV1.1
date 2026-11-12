# Project tools

These scripts support packaging and operating the backend. The production release builder uses explicit file allow-lists; files in this development tools folder are not copied wholesale.

- `build_release.py` — assembles the clean company release package.
- `linux/` — Debian installer, systemd service and backup timer units, and Apache site template.
- `company_setup.ps1`, `install_startup_tasks.ps1`, `windows_security.ps1` — Windows host setup and supervised startup helpers.
- `launch.ps1`, `supervise.py`, `stop_backend.ps1`, `health_check.ps1` — start, monitor, stop, and check the backend.
- `backup.py` — create and verify backend backups.
- `restore_backup.py` — Debian restore tool; verifies by default and requires a stopped service plus `--apply` to replace live runtime data.
- `restore_backup.ps1` — Windows disposable restore extractor; verifies first and refuses to overwrite an existing folder.
- `diagnose.ps1`, `python_runtime.ps1`, `set_admin_password.py` — Windows diagnostics and setup helpers.
- `create_shortcuts.py` — generate Windows launch shortcuts.

Android installation and provisioning tools are kept with the tablet project in `development/android-kiosk/tools/`.
