# Historical Windows Host Pilot Runbook

Archived September 2026. This procedure is not the selected company deployment. Use ../../COMPANY_PILOT_RUNBOOK.md and ../../DEBIAN_APACHE_DEPLOYMENT.md for current rollout steps.


This runbook applies only if IT chooses a Windows backend. Elite RF's selected
company target is a Debian VM behind Apache; use
`DEBIAN_APACHE_DEPLOYMENT.md` for that rollout. Use fictional visitor
information until the pilot has passed.

## 1. Prepare the backend computer

1. Install Python 3.11 or newer from https://www.python.org/downloads/windows/.
2. Select **Add Python to PATH** and **Install for all users** so the supervised
   backend can start before anyone signs in.
3. Before extracting the downloaded ZIP, right-click it, choose **Properties**,
   select **Unblock** if that option is shown, and click **Apply**. If the ZIP
   was already extracted, repeat this for the launcher files.
4. Extract the project and move the `Elite-RF-Visitor-Kiosk` folder to a
   permanent company-approved location on the backend computer. Do not run the
   production system from Downloads, an email attachment, or a temporary folder.
5. Keep the backend computer protected by the company's Windows account,
   antivirus, disk encryption, and backup policies.
6. Double-click `SET UP ELITE RF ON THIS COMPUTER.bat` for the guided readiness
   check. Rerun it after owner setup and company HTTPS configuration to install
   supervised startup, daily backups, and the restricted firewall rule.

## 2. Create the private environment file

On the backend computer, copy `.env.example` to a new file named `.env`.
Enter values provided by the company's authorized administrator:

```text
M365_TENANT_ID=
M365_CLIENT_ID=
M365_SENDER_ADDRESS=
M365_CERTIFICATE_PATH=
M365_CERTIFICATE_THUMBPRINT=
M365_PRIVATE_KEY_PATH=
M365_GRAPH_SCOPE=https://graph.microsoft.com/.default
KIOSK_SESSION_SECRET=
KIOSK_BACKUP_COPY_PATH=
KIOSK_BACKUP_RETENTION=30
```

The backend automatically creates and verifies backups of the database, every
visitor photo, and configuration. Configure `KIOSK_BACKUP_COPY_PATH` so it also
maintains a verified copy on encrypted company-approved storage. Back up `.env`
and private keys separately in the company's encrypted secrets system; they are
intentionally excluded from ordinary backup ZIPs.

`KIOSK_SESSION_SECRET` must be long and randomly generated. Keep `.env` only
on the backend computer. Never email it, upload it, photograph it, paste it
into chat, copy it to a kiosk, or include it in a screenshot. Do not put
Microsoft 365 credentials in `config.json` or any browser file.

## 3. Practice safely first

1. Double-click `START ELITE RF SAFE TEST MODE.bat` in the company package.
2. Confirm the banner says `TEST MODE - NO REAL EMAIL - DISPOSABLE DATA`.
3. Open the local admin page shown by the launcher.
4. Use the test password documented in `README.md`.
5. Practice a fictional check-in and confirm it appears in the disposable admin
   dashboard.
6. Stop the test server window when finished.

Safe test mode uses `127.0.0.1`, disposable data, and no `.env`. It cannot send
email and does not use the normal database, photos, backups, or configuration.

## 4. Complete first-run owner setup

Do this on the backend computer before enabling production TLS:

1. Start `START ELITE RF VISITOR KIOSK.bat` with HTTPS not yet configured.
2. Open `http://127.0.0.1:8080/admin` on the backend computer.
3. Complete the one-time owner-password setup.
4. Use a strong company-controlled password and store it in the company's
   approved password manager.
5. Stop the backend before final HTTPS configuration.

The remote Android tablet cannot complete first-run owner setup.

## 5. Choose the stable network identity

Company IT must create a permanent DNS hostname, such as
`kiosk.eliterf.company`, that always resolves to the backend computer. Use a
reserved/static local IP as well when possible. The tablet should use the DNS
hostname rather than a changing numeric address. Record the hostname before
creating the certificate, and make sure the DNS record is updated whenever the
backend address changes.

## 6. Configure trusted HTTPS

Set these backend-only values in `.env`:

```text
KIOSK_TLS_CERT_PATH=
KIOSK_TLS_KEY_PATH=
KIOSK_PUBLIC_HOSTNAME=
```

The certificate must match the hostname used by the kiosk, and the kiosk
device must trust the certificate chain. The private key stays only on the
backend computer. Never copy it to a kiosk or disable browser certificate
validation. Remote browser camera access normally requires trusted HTTPS.

If only one TLS path is set, a file is missing, or the certificate cannot be
loaded, stop and correct the configuration. Do not work around a certificate
warning.

## 7. Configure the firewall

Company IT should allow only the backend service port on the trusted company
network/private Windows profile. Do not expose the service to the public
internet. Verify that the Android tablet can reach the backend and restrict
admin access to authorized devices where practical.

## 8. Add employees and verify Microsoft 365 email

1. Start the production backend with `START ELITE RF VISITOR KIOSK.bat`.
2. Open the displayed admin URL and sign in.
3. Add or update the real employees.
4. Enter a valid company email address for each active employee.
5. Remember that visitor phone numbers are optional and are not used for alerts.
6. Open Notifications and confirm the Microsoft 365 readiness indicators.
7. Select one explicitly approved active employee and send one protected test
   email.
8. Confirm delivery with that employee. Do not create a visitor record just to
   test email delivery.

## 9. Connect the kiosk

Use the actual configured hostname in URLs like these:

```text
https://COMPANY-HOSTNAME:8080/admin
https://COMPANY-HOSTNAME:8080/kiosk
```

1. Provision the company-owned Android tablet as Device Owner using the
   production-signed `com.eliterf.kiosk` app and company MDM.
2. Set its managed `kiosk_url` to the trusted company HTTPS `/kiosk` URL.
3. Confirm camera permission is granted by policy and is used only on the photo
   step.
4. Confirm there is no certificate warning.
5. Confirm Device Owner, Lock Task, automatic boot, offline retry, power/volume
   restrictions, and administrator maintenance access all pass the Android 14
   acceptance checklist.

## 10. Fictional end-to-end pilot

Use fictional names, companies, emails, and photos only.

1. Submit one fictional visitor from the kiosk.
2. Confirm the record appears in the same admin dashboard.
3. Confirm the photo is viewable by the authenticated admin.
4. Confirm the record identifies the Main Lobby kiosk.
5. Confirm the employee email identifies the Main Lobby kiosk.
6. Confirm the public kiosk shows employee names only, not phone numbers or
   email addresses.
8. Stop and restart the backend.
9. Confirm the record and photo remain after restart.
10. Record any failure before changing configuration.

Pilot result:  Pass / Fail

Kiosk record and photo:       Pass / Fail    Date: __________
Main Lobby in email:          Pass / Fail    Date: __________
Restart persistence:          Pass / Fail    Date: __________
Operator/IT initials: ______________________

## 11. Daily startup and scheduled tasks

1. After owner setup and all company settings are ready, rerun
   `SET UP ELITE RF ON THIS COMPUTER.bat` and approve its Administrator prompt.
   It installs the startup, daily backup, and restricted firewall configuration.

2. After a Windows restart, verify the supervisor starts the backend and the
   kiosk URL works without an operator opening a launcher.
3. Stop the backend process once and verify the supervisor restarts it.
4. Verify the daily backup task has run, the backup passes the restore drill,
   and a verified copy exists at `KIOSK_BACKUP_COPY_PATH`.
   If that path is a network share, company IT must grant the backend computer's
   Windows system account access; do not rely on a user's mapped drive letter.
5. Enroll the tablet in the company's Android Enterprise/MDM system. Publish
   the production-signed app as a private Managed Google Play app, select
   high-priority automatic updates, and set the managed `kiosk_url` to the
   trusted company HTTPS URL.

## 12. Before public use

Company IT should still complete BitLocker, a protected secondary backup
destination, a visitor/photo retention policy, a restricted Windows account,
permanent APK signing, and Android Enterprise/MDM enrollment. Do not use real
visitor information until the pilot checklist passes.

For recovery testing, follow [BACKUP_RESTORE_DRILL.md](BACKUP_RESTORE_DRILL.md).

