# Historical handoff (September 2026)

This document describes an earlier Windows-host deployment and is not the
current company installation procedure. Elite RF's current target is Debian
behind Apache; use `../../DEBIAN_APACHE_DEPLOYMENT.md` and `LOG_RETENTION_AND_RECOVERY.md`.

# Company Handoff

1. Extract the clean ZIP to a permanent restricted Windows folder.
2. Install Python 3.11 or newer and run `SET UP ELITE RF ON THIS COMPUTER.bat` as administrator.
3. Enter company values only through setup. Never copy the development database, photos, `.env`, keystore, or backups.
4. Install a trusted certificate whose hostname exactly matches the kiosk URL.
5. Configure restricted private/domain LAN firewall access and supervised startup/daily backups.
6. Start with `START ELITE RF VISITOR KIOSK.bat`; do not use safe test mode for company traffic.

The included APK is the verified debug compatibility artifact. Company deployment requires a permanent company-signed APK and the same key for all future updates. Provision Device Owner only on an approved tablet, verify `com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver`, verify true Lock Task, configure the HTTPS kiosk URL, and complete the MESWAO checklist.

Visitor records follow the documented retention policy. Test restore only into a separate folder. Rollback means stop the backend, restore the last verified package, reinstall only an APK signed by the same permanent key, and reverify Device Owner, Lock Task, kiosk URL, and camera flow.

Current status recorded in September 2026: **Not ready for company deployment** until signing, HTTPS, company configuration, backups, and physical acceptance are complete. See `PRODUCTION_AUDIT_20260904.md`.

