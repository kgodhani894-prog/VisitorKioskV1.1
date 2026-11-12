# Development workspace

Development-only code, tools, and checks live here. The company release is assembled separately by the root-level release builder.

- `android-kiosk/` — Android tablet app, build setup, provisioning/install scripts, tablet-specific documentation, and preserved reference artifacts. Start at `android-kiosk/README.md`.
- `tests/` — backend and packaging test suite.
- `START_ELITE_RF_KIOSK.bat` and `START_TEST_MODE.bat` — Windows development launchers for the backend; these do not install or configure the Android tablet.

Runtime visitor records, photos, and backups are stored outside this development folder in the project-level `data/`, `storage/`, and `backups/` directories.
