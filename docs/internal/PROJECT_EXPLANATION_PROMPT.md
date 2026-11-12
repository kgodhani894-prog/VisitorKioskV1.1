# Elite RF Visitor Kiosk: Complete Project Explanation Prompt

Use the following project context when reviewing, maintaining, securing, testing, or deploying this system. Treat the current source tree as authoritative and do not invent missing components.

## Mission

Elite RF Visitor Kiosk is a two-part visitor check-in system:

1. A Python backend runs as a systemd service on the company Debian VM, with Apache providing the HTTPS reverse proxy for the visitor kiosk and administrator dashboard.
2. A managed MESWAO Android tablet runs the kiosk APK in Device Owner and Android Lock Task mode.

Visitors enter their name, citizenship response, phone, email, company, and host, then complete the photo step. The backend stores the visitor record and photo and sends an email notification to the selected active employee through Microsoft Graph. Administrators authenticate to review visits, view photos, export records, delete an individual live visit, manage employees, test email readiness, and change the owner password. Existing backup archives can retain deleted visits until normal rotation removes them.

The system must preserve visitor privacy, retain visitor records and photos indefinitely, prevent kiosk escape, keep the tablet recoverable after crashes or reboots, and keep test data completely separate from company data.

## Repository Layout

- `run.py`: backend entry point, test-mode setup, TLS argument handling, launch URL reporting, and server lifecycle.
- `kiosk_server/server.py`: HTTP(S) routes, authentication gates, visitor validation, JPEG storage, notification dispatch, security headers, and background maintenance.
- `kiosk_server/db.py`: SQLite schema and data operations for employees, visitor records, notification state, and audit-related state.
- `kiosk_server/security.py`: PBKDF2-SHA256 password hashes, HMAC-signed administrator sessions, and SQLite-persisted administrator login throttling using keyed client-address fingerprints.
- `kiosk_server/notifications.py`: Microsoft Graph certificate-authenticated email delivery and retry classification.
- `kiosk_server/backups.py`: verified ZIP backups containing configuration, SQLite state, visitor photos, and recovery metadata.
- `static/kiosk.html`, `static/kiosk.css`, `static/kiosk.js`: visitor UI, form validation, host selector, photo-step UI, capture/submit flow, reset behavior, and check-in cooldown behavior.
- `static/admin.html`, `static/admin.css`, `static/admin.js`: administrator login, dashboard, employee management, visitor table, photo viewing, export, status changes, individual visit deletion, and email readiness controls.
- `development/android-kiosk`: Android Device Owner kiosk application for the MESWAO tablet.
- `development/android-kiosk/app/src/main/.../MainActivity.kt`: WebView kiosk shell, maintenance mode, safe home-screen exit, reconnect/recovery behavior, administrator controls, and native capture handoff.
- `development/android-kiosk/app/src/main/.../admin/KioskPolicy.kt`: Device Owner restrictions, Lock Task allow-list, status-bar/keyguard/screen-capture policy, camera permission policy, maintenance relaxation, and recovery re-application.
- `development/android-kiosk/app/src/main/.../admin/ApprovedInputMethodPolicy.kt`: Gboard-only input-method allow-list and managed Gboard restrictions.
- `development/android-kiosk/app/src/main/.../CameraXCaptureActivity.kt`: native CameraX proof-of-concept capture screen, front-camera selection, target rotation, natural output size selection, JPEG output, and capture result handoff.
- `tools/build_release.py`: allow-listed company deployment package builder that excludes live data, backups, tests, secrets, and machine-specific files.
- `tools/linux/configure_kiosk_env.py`: Debian settings reconciler that enforces proxy TLS, first-run setup, mounted-backup, backup-timer, session-secret, and file-permission requirements.
- `tools/linux/install_debian.sh` and `tools/linux/*.service` / `*.timer`: Debian installer and supervised backend/backup services.
- `tools/linux/elite-rf-visitor-kiosk.apache.conf`: Apache HTTPS reverse-proxy template for the loopback-only Python backend.
- `docs/DEBIAN_APACHE_DEPLOYMENT.md`: Debian VM setup, Apache TLS configuration, storage locations, and operations.
- `tools/company_setup.ps1`: Windows readiness checks for Python, dependencies, permanent installation location, secrets, Microsoft 365, trusted HTTPS, backup destination, owner password, startup tasks, and firewall integration.
- `docs/COMPANY_PILOT_RUNBOOK.md`: company setup and pilot acceptance procedure.
- `docs/BACKUP_RESTORE_DRILL.md`: backup verification and recovery procedure.
- `development/tests`: backend unit/integration tests.
- `development/android-kiosk/app/src/test`: Android unit tests for URL policy, maintenance, kiosk recovery, input-method policy, and camera contracts.

## Normal Runtime Flow

1. The Debian VM starts the Python backend as a systemd service. Apache serves the company HTTPS hostname and proxies to the backend at `127.0.0.1:8080`.
2. Production startup loads `/etc/elite-rf-visitor-kiosk/kiosk.env`, uses a private session secret and Microsoft Graph certificate settings, and enables `KIOSK_EXTERNAL_HTTPS=true`. Apache holds the TLS certificate and key; the Python backend remains loopback-only.
3. The tablet is assigned the trusted HTTPS `/kiosk` URL through managed configuration or administrator setup.
4. Android validates the URL and only permits the approved origin and visitor routes. Administrator routes, arbitrary navigation, external intents, certificate errors, and unapproved resources must fail closed.
5. Android applies Device Owner policy, permits only Gboard, disables visitor access to system controls, enters true Lock Task mode, and grants camera access to the kiosk flow.
6. The visitor form loads active host names from `/api/employees` and validates required fields locally and again on the backend.
7. The photo screen uses the native CameraX path on the MESWAO tablet. Capture must preserve the tablet's natural landscape framing and JPEG payload contract.
8. The backend validates the employee, fields, JPEG signature, and photo size, stages the photo, atomically renames it, creates the SQLite record, and dispatches or queues the email notification.
9. The dashboard reads visitor records only after administrator authentication. Photos are served only after the same authentication check. Visit deletion requires an authenticated dashboard action, records a non-personal audit event, and removes the associated live photo with retryable cleanup.
10. A visitor completion screen returns to a clean kiosk form after the configured cooldown, and backend retry processing handles temporary email failures.

## Security Controls That Are Present

- PBKDF2-SHA256 administrator password hashing with a per-password random salt.
- HMAC-signed, expiring administrator session cookies with `HttpOnly` and `SameSite=Strict`.
- Security headers including CSP, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, no-store caching, and MIME sniffing protection.
- Photos require an administrator session at `/photos/...`; visitor-facing routes do not expose photo files.
- Dashboard visitor and employee values are rendered with DOM text nodes rather than raw HTML.
- Visitor photo writes use a temporary file and atomic rename; failed database writes remove staged files.
- SQLite uses foreign keys, a busy timeout, WAL mode, and parameterized SQL.
- Test mode uses a disposable marked runtime root, synthetic employee data, disabled notifications, and loopback-only binding.
- Release packaging is allow-listed and excludes `.env`, live data, photos, backups, tests, keys, archives, and shortcuts.
- Android release network security disables cleartext traffic, rejects invalid TLS, restricts WebView navigation to the approved origin, and keeps the camera activity non-exported.
- Device Owner and Lock Task recovery paths re-apply policy after boot, renderer failure, and native camera return.
- Android policy verifies that Gboard is installed, permitted, active, and not freely controllable before declaring kiosk readiness.

## Current Deployment Audit Findings

Rechecked 2026-10-27 against the Debian/Apache implementation. Passing tests do not replace VM, TLS, backup, or physical-device acceptance.

### Resolved in source: remote HTTP and login guessing

- Production now rejects non-loopback binds unless the backend has direct TLS. The Debian unit binds to `127.0.0.1` with `KIOSK_EXTERNAL_HTTPS=true`, and that flag is accepted only on loopback behind the Apache TLS proxy.
- Admin login locks after five failed attempts for five minutes. The lockout survives service restarts in the SQLite database; client addresses are stored only as HMAC fingerprints. The Apache template strips a client-supplied `X-Forwarded-For` before Apache adds the proxy client address.

### High: Backup archives require encrypted storage and controlled access

- `kiosk_server/backups.py` places the database, photos, and runtime configuration in a verified ZIP; the application does not encrypt that ZIP.
- IT must encrypt the VM data filesystem and secondary backup filesystem, restrict access, protect recovery keys, and test a restore. The Debian unit requires the configured secondary destination to be an active mount before copying, and the admin panel reports its capacity/mount state.

### Improved in source: bounded JSON request parsing

- The backend now requires a decimal `Content-Length`, rejects transfer-encoding, non-JSON bodies, invalid JSON and bodies over 7 MB, and applies a 15-second socket timeout. Apache also applies an 8 MB body limit and a 60-second proxy timeout.
- Regression tests cover missing, malformed and oversized lengths, malformed JSON, transfer-encoding, and a slow body. These checks do not replace deployment testing through the actual Apache configuration.

### Medium: Production depends on company configuration and physical acceptance

- The Debian installer cannot supply the company's hostname, trusted certificate, mailbox authorization, encrypted storage, firewall policy, Android MDM/signing key, or camera/device acceptance. Those are required before production use.

### Medium: Android keyboard controls need device-version verification

- Gboard managed restriction keys are version-sensitive. Verify the exact installed Gboard version and active input method on the MESWAO tablet, and requalify after Gboard updates.

## Items That Must Not Be Changed Accidentally

- Do not change the backend database schema, visitor payload, employee lookup, email provider, or photo filename contract without a migration plan.
- Do not disable Device Owner, Lock Task, WebView origin checks, TLS validation, or crash/reboot recovery.
- Do not install a debug APK for company deployment. Production requires a reviewed, permanently signed release APK and managed provisioning.
- Do not use HTTP from the tablet in company deployment, even if it works on a trusted LAN.
- Do not use real visitor names, photos, or email addresses in automated tests.
- Do not send test email during tests unless the operator explicitly authorizes a live delivery test.
- Do not delete rollback backups until the replacement has passed build, security, restore, and physical tablet acceptance.

## Verification Baseline

Latest completed checks for the current source:

- Backend: 124 tests passed, with 3 skipped. The POSIX config-permission regression test is skipped on this Windows workstation.
- Android: Gradle `test`, `lint`, and `:app:assembleDebug` passed.
- These results prove code-level checks only. They do not prove Debian service startup, Apache configuration, trusted Android TLS, backup confidentiality, physical camera framing, email delivery, firewall correctness, or recovery after a real power loss.

## Required Production Exit Criteria

Do not call the system company-deployment-ready until all of the following are evidenced:

1. Remote HTTP startup is rejected and the tablet uses a trusted company HTTPS hostname.
2. The backend runs as one supervised process from a permanent company path.
3. The owner password is unique, strong, stored in the approved password manager, and the default password is impossible to use.
4. Microsoft Graph certificate authentication sends a controlled test email to an authorized test recipient.
5. Backups are access-controlled, protected or encrypted, copied to approved secondary storage, and restored successfully in a drill.
6. The production APK is signed with the company release key, installed through MDM or approved provisioning, and is not the debug build.
7. Device Owner, Lock Task, Gboard-only input, camera permission, boot recovery, renderer recovery, and maintenance relock are verified on the actual MESWAO tablet.
8. The camera preview and captured JPEG are landscape, natural-sized, not stretched, not unexpectedly cropped, and successfully submit exactly one visitor record.
9. Admin login throttling, session expiry, photo access, export, permanent-record protection, and audit behavior are manually accepted.
10. Firewall rules expose only the required private/domain LAN path, and the server is not internet-facing.
11. Retention, deletion, privacy, incident response, password recovery, backup recovery, and ownership responsibilities are documented for the company.

## Safe Review Instructions

When asked to work on this project:

1. Inspect the current source and deployment state before proposing changes.
2. Separate source behavior, generated artifacts, backups, test data, and live tablet state.
3. Never print or paste `.env`, private keys, password hashes, session secrets, visitor records, or visitor photos.
4. Make a timestamped rollback backup before modifying backend or Android behavior.
5. Make the smallest scoped change, add a regression test, run backend tests, run Android tests/lint/build when Android code changes, and report any manual tablet gap.
6. If a finding concerns production security, prefer a fail-closed behavior and an explicit migration/rollout step over a silent workaround.
7. Report confirmed findings first, with absolute file paths and line numbers, then assumptions, residual risks, and changes made.
