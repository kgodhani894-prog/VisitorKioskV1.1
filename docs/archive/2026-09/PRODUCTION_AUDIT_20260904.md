# Production Readiness Audit

Historical snapshot dated 2026-09-04. It describes the source and tablet state
at that time; for the current Debian/Apache implementation, use
`../../DEBIAN_APACHE_DEPLOYMENT.md` and `../../internal/PROJECT_EXPLANATION_PROMPT.md`.

Date: 2026-09-04
Classification: Not ready for company deployment

## Verified

- Package: `com.eliterf.kiosk`.
- Device Owner receiver: `com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver`.
- Live tablet: Device Owner active and true Lock Task active.
- Installed working APK SHA-256: `6D775D390C3A1110B08C051C12F52E5B8546C4293B8A5F588455E48CD387C80E`.
- Android project: API 34 target, API 29 minimum, AGP 8.5.2, CameraX 1.3.4.
- CameraX behavior is frozen and previously physically verified: landscape front camera, upright natural preview, 2560x1920 JPEG, review, retake, and use-photo flow.
- Backend includes input validation, session signing, security headers, bounded photo input, safe filenames, fixed kiosk identity, and an authenticated delete-confirmation flow.

## Production blockers

- The verified artifact is debug-signed (`1.1.0-debug`), not a permanent company-signed release APK.
- Company hostname, trusted TLS certificate, and private key are not supplied.
- Production session secret, administrator password, Microsoft 365 application settings, employee roster, kiosk identity, retention decision, and protected backup destination are not supplied.
- A fresh company Windows installation, firewall, startup task, DNS, certificate chain, backup restore, and end-to-end production submission have not been proven here.
- The source workspace contains local operational configuration/data and must not be shipped directly.

## Security and reliability gaps

- Direct `run.py` can support development HTTP; company operation must use the production launcher after TLS configuration.
- Hardware-forced shutdown, recovery-mode reset, unplugging, and OEM-specific behavior cannot be guaranteed by ordinary Android software.
- Wi-Fi interruption, backend restart, camera/WebView recovery, reboot recovery, long-duration stability, exactly-one-record, and exactly-one-email still require controlled-pilot verification.

## Required company values

Approved hostname/IP, trusted TLS certificate/key, strong session secret, administrator password, Microsoft 365 tenant/client/sender/certificate settings, employee names/emails/status, building and kiosk identity, data/photo/backup locations, retention period, tablet kiosk URL, administrator PIN/recovery code, and permanent release signing key.

## Deliberately unchanged

CameraX, visitor flow, backend routes, database behavior, email behavior, kiosk policy, Device Owner, Lock Task, permissions, and tablet state were not changed for this audit.

