# Elite RF Visitor Kiosk Handoff Prompt

Copy everything below into a new coding-agent task when handing this project to another engineer or agent.

---

You are taking over the Elite RF Visitor Kiosk project. Continue from the existing code and artifacts. Do not recreate the project, replace it with a scaffold, or assume that prior claims are correct without verifying them.

## Project Location

The source project is:

`C:\Users\kgodh\Downloads\elite-rf-visitor-kiosk-v17\elite-rf-visitor-kiosk`

The backend is the project root. The Android project is:

`C:\Users\kgodh\Downloads\elite-rf-visitor-kiosk-v17\elite-rf-visitor-kiosk\development\android-kiosk`

The project is a Python visitor check-in backend intended to run on Elite RF's Debian Linux VM as a systemd service behind Apache HTTPS, paired with a company-owned MESWAO Android tablet operating as a locked visitor kiosk. Windows launch and setup tools remain for development or a separately approved Windows host.

## Product Goal

The system provides a visitor check-in kiosk for Elite RF. A visitor enters personal and company information, selects an active host, takes a photo, reviews it, submits the check-in, and sees a completion screen. The backend stores the visitor record and photo and sends an email notification to the selected host through Microsoft Graph when production email is configured.

The tablet is intended to run as a dedicated Android Device Owner kiosk. Visitors should not be able to leave the kiosk, access Settings, change the keyboard, change the network, access other apps, or alter kiosk configuration. Administrators authenticate with a PIN or recovery code and can access limited maintenance, administrator, camera diagnostic, and final-lockdown controls.

## Important Safety Rules

- Do not print, expose, copy, or commit values from `.env`, `config.json`, `keystore.properties`, private keys, certificates, databases, visitor records, or photos.
- Never include administrator passwords, PINs, recovery codes, session secrets, Microsoft credentials, signing-key passwords, or visitor information in a handoff, log, screenshot, test output, or final response.
- Treat the existing source folder as an operational development workspace, not as a production deployment package.
- Do not delete the existing `backups` directory or rollback artifacts without explicit authorization and an integrity check.
- Do not run tests that send real email notifications unless the user explicitly authorizes a controlled test.
- Use fictional visitor data for testing.
- Do not install an APK on the tablet unless the user explicitly requests installation and the APK, signing key, rollback state, and device state are understood.
- Do not activate final Android production lockdown during ordinary home testing because it disables USB debugging.
- Preserve the current backend routes, visitor payload, photo payload, email behavior, Device Owner policy, Lock Task policy, and Android permissions unless the task explicitly changes one of them.
- Before any destructive operation, create or verify a rollback copy and state exactly what will be changed.

## Repository Layout

- `run.py`: Backend entry point. Supports production and disposable test mode, optional TLS, host and port selection, startup messages, and browser launch.
- `kiosk_server/server.py`: HTTP server, routing, sessions, admin authentication, employee APIs, check-in submission, notification status, readiness, health, and TLS setup.
- `kiosk_server/db.py`: SQLite schema and database operations for employees, visitor records, retryable photo deletion, audit events, and notification state.
- `kiosk_server/security.py`: Password hashing, session signing and verification, login throttling, and security helpers.
- `kiosk_server/notifications.py`: Microsoft Graph email configuration and sending logic.
- `kiosk_server/backups.py`: SQLite snapshot backup, photo inventory, backup manifest, verification, retention, and secondary-copy handling.
- `static/kiosk.html`: Public visitor form, photo step, review step, completion step, and clear-form dialog.
- `static/kiosk.js`: Visitor form validation, host selection, temporary test autofill, camera bridge calls, native capture/review flow, submission, reset, completion timer, and camera error handling.
- `static/kiosk.css`: Visitor and photo-step layout, responsive styling, host picker, completion screen, and camera controls.
- `static/admin.html`: Authenticated administrator dashboard markup.
- `static/admin.js`: Dashboard authentication, visitor records with confirmed individual deletion, exports/status, employees, notifications, and readiness actions.
- `static/admin.css`: Administrator dashboard styling.
- `config.json`: Current local operational configuration. It contains authentication material and test employee records and must not be shipped directly.
- `.env.example`: Names and placeholders for private production settings.
- `data/`: Local SQLite database. Contains operational visitor data in this workspace.
- `storage/photos/`: Local visitor photos. Contains operational visitor data in this workspace.
- `backups/`: Local verified backup ZIPs. Treat them as sensitive because they contain database, photo, and authentication-related configuration material.
- `tools/build_release.py`: Allowlisted clean deployment package builder.
- `tools/linux/install_debian.sh` and `tools/linux/configure_kiosk_env.py`: Debian installation and protected production-settings setup.
- `tools/linux/*.service`, `*.timer`, and `*.apache.conf`: systemd backend/backup definitions and Apache HTTPS reverse-proxy template.
- `tools/company_setup.ps1`: Windows first-run configuration and readiness checker.
- `tools/launch.ps1`: Production or disposable test launcher.
- `tools/diagnose.ps1`: Disposable installation and backend diagnostic.
- `tools/backup.py`: Manual verified backup tool.
- `tools/install_startup_tasks.ps1`: Windows startup supervisor and daily backup task installer.
- `tools/windows_security.ps1`: Sensitive-file protection and Windows security helpers.
- `tools/create_shortcuts.py`: Machine-specific shortcut generator.
- `docs/`: Security, deployment, pilot, backup, email, and Android acceptance documentation.
- `development/`: Development launchers, tests, Android source, and Android build tooling. Do not copy this directory into the clean company backend package.

## Backend Behavior

The backend can run locally over HTTP for loopback development. Production operation uses Apache HTTPS in front of a backend bound only to `127.0.0.1:8080`. The company needs a stable internal hostname and a trusted matching certificate; public DNS is not required. Remote tablet camera access must not use plain HTTP or an untrusted certificate.

The Debian systemd unit supervises the backend and keeps port 8080 loopback-only. Apache proxies the approved HTTPS virtual host to it. The tablet URL is the company HTTPS `/kiosk` URL, not `127.0.0.1`, because loopback on the tablet refers to the tablet itself. The admin dashboard uses the same origin at `/admin`.

The backend exposes:

- `/kiosk`: Public visitor check-in page.
- `/admin`: Authenticated administrator dashboard.
- `/api/checkins`: Visitor submission endpoint.
- `/api/admin/*`: Authenticated employee, visitor-status/export, notification, and readiness operations. Visitor-record deletion is disabled.
- `/api/system/identity`: Backend identity/readiness probe used by launchers and diagnostics.

The visitor submission saves the photo and record before attempting notification. The frontend requires both `photo_saved` and `record_saved` before showing success. Email status is separate from visitor-record success. A notification failure must not silently erase a successful visitor check-in.

The backend uses SQLite, local photo storage, signed sessions, PBKDF2-HMAC-SHA-256 password hashes, security headers, bounded photo input, safe generated filenames, authenticated employee management, and employee filtering so private employee contact data is not exposed to visitors. An authenticated dashboard action can delete one live visitor row and its photo; a deletion event remains in the audit log, and old backup archives may retain the deleted data until normal rotation.

## Email Behavior

Production host notifications use Microsoft Graph email. Required private settings include the tenant ID, client ID, sender address, certificate path, certificate thumbprint, private-key path or certificate key material, and Graph scope.

The application should be configured with a dedicated sender mailbox and the least privilege appropriate for the approved Microsoft Graph application. Company IT must complete tenant consent and any mailbox restriction required by policy.

Email delivery has not been accepted as production-ready until one explicitly authorized controlled email reaches the correct approved employee mailbox and the dashboard shows the expected status. Never use real email in safe test mode.

## Android Architecture

Android package: `com.eliterf.kiosk`

Android project:

`development/android-kiosk`

Build configuration:

- Compile SDK 34.
- Target SDK 34.
- Minimum SDK 29.
- Android Gradle Plugin 8.5.2.
- Gradle wrapper 8.7.
- Kotlin JVM target 17.
- CameraX 1.3.4.
- Application version currently 1.1.0, version code 2.

Important Android classes:

- `MainActivity.kt`: WebView host, kiosk lifecycle, URL setup, maintenance, administrator controls, Lock Task, recovery, network state, native-camera result handling, and production-lockdown controls.
- `CameraXCaptureActivity.kt`: CameraX front-camera capture activity, landscape layout, native bottom bar, 2560x1920 JPEG output, capture validation, and result handoff.
- `NativeCameraController.kt`: Older/native camera bridge, browser/native camera coordination, diagnostics, calibration, preview artifacts, and bridge methods.
- `KioskPolicy.kt`: Device Owner policy, Lock Task allowlist, restrictions, camera permission policy, input-method policy, status bar/keyguard/security controls, and maintenance restoration.
- `ApprovedInputMethodPolicy.kt`: Gboard-only input-method policy using package `com.google.android.inputmethod.latin` and related Android settings controls.
- `KioskSettings.kt`: Approved kiosk URL and production/debug HTTP policy.
- `KioskUrlPolicy.kt`: URL normalization and approved-origin validation.
- `KioskWebViewClient.kt`: WebView navigation, origin, SSL, camera permission, downloads, mixed-content, and external-navigation enforcement.
- `LockedInputWebView.kt` and `LockedInputPolicy.kt`: Input configuration restrictions and keyboard behavior.
- `MaintenanceController.kt`, `MaintenanceWindow.kt`, and `MaintenanceDecision.kt`: Ten-minute technician mode, 30-minute full maintenance, deadlines, persistence, reboot recovery, and automatic relock.
- `BootReceiver.kt` and `MaintenanceReceiver.kt`: Reboot and maintenance deadline handling.
- `PinAuth.kt`, `PinAuthCore.kt`, and `PinLockout.kt`: Administrator PIN/recovery-code authentication and lockout.
- `RendererRecoveryPolicy.kt`, `KioskFailure.kt`, `KioskReadiness.kt`, `ConnectivityFailureRules.kt`, `PowerGuardRules.kt`, and `ProductionLockdownRules.kt`: Recovery and readiness decision logic.
- `CameraCalibration.kt`: Saved camera calibration data. Treat calibration as a development/administrator tool and decide whether it belongs in the final production build.

## Camera and Photo Flow

The intended visitor flow is:

1. Visitor completes the form.
2. Visitor presses `Next`.
3. The Android app launches the native CameraX activity directly.
4. The native activity displays the camera preview and the bottom instruction/action bar.
5. Visitor presses `TAKE PHOTO`.
6. CameraX saves a 2560x1920 JPEG to an app-private path.
7. The app returns to the WebView review state.
8. Visitor can choose `RETAKE PHOTO` or `USE PHOTO`.
9. Backend confirms both the photo and visitor record.
10. Completion screen appears and resets after the configured 10-second delay or via `Back to check-in`.

The current native camera startup fix uses a texture-backed `PreviewView` and an opaque background to prevent the WebView media/play placeholder from showing during launch. The JavaScript also hides the browser video surface during native capture and review and does not restore it from `stopCamera()`.

The current camera behavior has been physically tested in prior work for landscape orientation, front camera, natural framing, 2560x1920 JPEG output, review, retake, and use-photo flow. Because production APK changes can affect camera behavior, the exact final signed APK still requires direct visual confirmation on the MESWAO tablet. `FLAG_SECURE` prevents ordinary ADB screenshots.

## Tablet Security Model

Normal visitor mode requires:

- Device Owner active.
- True `LOCK_TASK_MODE_LOCKED` state.
- Only the kiosk package allowlisted.
- Persistent Home activity configured.
- Status bar, keyguard, notifications, Settings, Quick Settings, volume, external apps, and unwanted system configuration restricted.
- Gboard remains the only permitted input method.
- Camera permission available only for the approved kiosk flow.
- Screen capture blocked in production windows.
- Automatic relock after maintenance deadlines.
- Reboot recovery to the kiosk.

Android cannot guarantee protection against every hardware forced reboot, OEM recovery key combination, emergency SOS behavior, or physical reset mechanism. These residual risks must be documented and accepted by company IT.

Full tablet maintenance is deliberately time-limited and PIN-protected. It should not be described as permanent unrestricted ownership or as protection against factory reset. Verify that maintenance automatically relocks after expiration and after reboot before production acceptance.

## Current Device Evidence

The known tablet is a MESWAO MES_B3, Android 14, ADB serial `B1256G261340` when connected. The last read-only verification showed:

- Device Owner: `com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver`.
- Lock Task state: `LOCK_TASK_MODE_LOCKED`.
- Foreground activity: `MainActivity`.

The device must be reverified after installing the final production-signed APK. Do not treat a debug APK installation as final company acceptance.

## Current Build and Test State

The Android release build currently assembles successfully with release unit tests and release lint. The release APK was locally signed, but the signing certificate has not yet been proven to be the protected permanent company signing key.

The prior backend test failure described in earlier handoffs has been resolved. The latest full backend run passed 124 tests with 3 skipped; the POSIX config-permission regression test is skipped on this Windows workstation. That is code-level evidence only, not Debian VM, Apache, TLS, email, backup-restore, or physical tablet acceptance.

The focused kiosk-flow contract tests pass and cover confirmed photo/record behavior, review loading, and prevention of the browser video placeholder reappearing.

The existing tablet and backend checks do not replace final acceptance of the exact production artifacts.

## Existing Deployment Artifacts

An older company ZIP was:

`C:\Users\kgodh\Downloads\elite-rf-visitor-kiosk-v17\company-release-audit\Elite-RF-Visitor-Kiosk.zip`

It was created on September 4, 2026 and is stale relative to the current source. Do not present it as the current package. The current Debian package is built outside the source tree at `../release-debian/Elite-RF-Visitor-Kiosk.zip`; rebuild and verify its checksum after any further source changes.

There are also extracted deployment directories under the outer project folder. Treat them as historical artifacts until their contents and hashes are independently verified.

## Release Packaging

Use `tools/build_release.py` to create a clean allowlisted package outside the source folder. The builder intentionally excludes `.env`, live data, photos, backups, tests, Android development files, private keys, keystores, and shortcuts. It creates a sanitized config with placeholder authentication material and embeds operator documentation.

Before shipping a package:

- Confirm the source contains no temporary autofill or camera-debug behavior intended only for home testing.
- Confirm the package contains no `.env`, database, photo, backup, keystore, certificate private key, or signing-key file.
- Confirm the package contains only current source and documentation.
- Record the ZIP SHA-256 hash.
- Scan the ZIP with approved endpoint protection.
- Extract to a clean permanent company folder, not Downloads, OneDrive, an email attachment, a removable drive, or a temporary folder.
- On Debian, run the installer, configure the Apache HTTPS site, and verify the backend, backup timer, disk alerts, and trusted tablet URL before pilot use.

## Company Setup Requirements

Company IT must provide and verify:

- Stable internal hostname or reserved IP, plus a trusted TLS certificate matching the exact address used by the tablet. Public DNS is not required.
- Debian VM with systemd, Apache 2.4, approved package sources, and Python 3.11 or newer.
- Apache HTTPS reverse proxy to loopback-only backend port 8080; firewall exposure limited to approved internal clients on HTTPS.
- Microsoft Graph application and dedicated sender mailbox.
- Strong administrator password stored in the company password manager.
- Strong random `KIOSK_SESSION_SECRET` stored only on the backend computer and approved secret storage.
- Real active employee roster and valid company email addresses.
- Building and kiosk identity used in email templates and operational records.
- Indefinite retention of visitor records and photos, with separately defined retention for verified backup archives.
- Encrypted secondary backup destination on separate approved storage.
- Encrypted VM data storage and backup storage, with recovery keys protected.
- Restricted `eliterfkiosk` service account and systemd service hardening.
- Host endpoint protection and updates maintained under company IT policy.
- systemd supervised startup and daily verified backup timer.
- A company plan for Android system-trust of the certificate chain, permanent APK signing, MDM enrollment, and tablet provisioning.
- Protected permanent Android signing key and key escrow/recovery process.
- Android Enterprise/MDM enrollment and private Managed Google Play distribution plan.

## Required Final Acceptance

Use fictional data until every check passes. Record date, operator, machine, tablet serial, APK version, backend URL, artifact hashes, and pass/fail evidence.

- Fresh backend installation from the newly generated ZIP.
- First-run owner password setup over loopback.
- Trusted HTTPS certificate validation from the tablet.
- No certificate warnings.
- Kiosk URL points to the company HTTPS `/kiosk` endpoint.
- Active employees and notification settings are correct.
- One authorized controlled Microsoft 365 email reaches the correct employee.
- One fictional visitor check-in creates exactly one record and one photo.
- A failed email does not lose the visitor record.
- Retake creates no duplicate visitor record.
- Back navigation returns cleanly to the form.
- Clear form confirmation erases only the current form.
- Completion screen resets after 10 seconds and the manual button works.
- Camera opens only in the photo step and releases after capture, back, reset, error, or admin access.
- Camera preview is landscape, natural-sized, not stretched, not zoomed, and visually correct on the physical tablet.
- Captured JPEG is readable, correctly oriented, and 2560x1920.
- Gboard types normally.
- Alternate keyboards and input-method configuration are blocked.
- Host selector is readable and opaque.
- Invalid email feedback is visible and valid non-Gmail addresses work.
- Device Owner is active.
- True Lock Task is active.
- Home, Recents, notification shade, Settings, external apps, and kiosk escape paths remain blocked.
- Administrator PIN/recovery flow works.
- Five failed administrator attempts lock out for the configured period.
- Technician maintenance expires and relocks.
- Full maintenance expires and relocks.
- Maintenance relocks safely after reboot and invalid/missing deadlines.
- Wi-Fi interruption recovers without exposing the tablet.
- Backend restart recovers without losing kiosk state.
- WebView renderer restart recovers without losing kiosk security.
- Tablet reboot returns to the kiosk.
- USB debugging is disabled only after all ADB-assisted tests are complete.
- Final production lockdown succeeds and is recorded.
- Verified backup is created and copied to separate protected storage.
- Backup restore succeeds in a disposable folder.
- Visitor records/photos remain present, export is correct, and backup rotation does not prune primary visitor data.
- Continuous 72-hour kiosk test passes.

## Recommended Next Work Sequence

1. Preserve the current source and rollback artifacts.
2. Remove the temporary autofill shortcut and decide whether camera calibration/diagnostic controls belong in production.
3. Update the stale backend contract test without changing runtime behavior.
4. Run the complete backend suite and Android debug/release tests, lint, and assembly.
5. Generate a new clean company ZIP.
6. Inspect and hash the ZIP and final release APK.
7. Configure the company backend and trusted HTTPS.
8. Perform the complete pilot and backup restore drill.
9. Provision or update the tablet through the approved Android Enterprise/MDM process.
10. Physically verify the exact final APK on the MESWAO tablet.
11. Activate final production lockdown only after all tests pass.
12. Deliver a final handoff containing artifact hashes, configuration ownership, runbook, rollback procedure, acceptance record, and known residual risks.

## Useful Verification Commands

Run backend tests from the backend project root:

```powershell
& 'C:\Users\kgodh\AppData\Local\Programs\Python\Python313\python.exe' -m unittest -q development.tests.test_app
```

Run focused kiosk contract tests:

```powershell
& 'C:\Users\kgodh\AppData\Local\Programs\Python\Python313\python.exe' -m unittest -q development.tests.test_kiosk_flow_contract
```

Run Android checks from `development/android-kiosk`:

```powershell
.\gradlew.bat :app:testDebugUnitTest :app:testReleaseUnitTest :app:lintRelease :app:assembleRelease
```

Build the clean deployment package from the backend project root:

```powershell
& 'C:\Users\kgodh\AppData\Local\Programs\Python\Python313\python.exe' tools\build_release.py
```

Use official Android Platform Tools for device verification. Confirm the exact device serial before any command that could install, reboot, wipe, or change policy.

## Handoff Completion Standard

Do not call this project company-deployment-ready merely because the backend starts, the debug APK installs, the release APK assembles, or the tablet is currently locked. Company readiness requires the exact final signed artifacts, trusted HTTPS, production configuration, complete green tests, fresh-install proof, physical acceptance, protected backups, email acceptance, final lockdown, and recorded rollback evidence.

---

End of handoff prompt.
