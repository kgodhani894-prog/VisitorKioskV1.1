# Elite RF Visitor Kiosk Backend

This is a portable local-first visitor check-in backend for Elite RF. The
company should operate the clean deployment ZIP produced by
`tools/build_release.py`, not the development workspace.

Development files are grouped under `development/`. Run backend tests from this
project root with `python -m unittest -q development.tests.test_app`. The Android
workspace is in `development/android-kiosk`.

## Project layout

- `kiosk_server/` — Python request handling, records, authentication, email, and backups.
- `static/` — kiosk and admin web pages, styles, and browser code.
- `development/` — Android tablet app, development launchers, and backend tests.
- `docs/` — Debian deployment, company operations, and recovery guides; see `docs/README.md`.
- `tools/` — release builder and host setup/operation scripts; see `tools/README.md`.
- `data/` — live SQLite visitor records.
- `storage/photos/` — live visitor photos.
- `backups/` — automatic application backup archives.

The deployed service uses its own configured data paths. These source-workspace directories are not included in the clean release package.

The application can run on Windows or on the company's Debian VM behind Apache. The clean deployment ZIP includes a Debian `systemd` service and Apache reverse-proxy procedure.

## What it does

- Hosts the visitor kiosk page.
- Hosts the protected admin dashboard.
- Stores visitor records in SQLite.
- Stores visitor photos locally.
- Retains visitor rows and photos until an administrator deletes a visit from the dashboard; deleted data can remain in older backups until normal backup rotation removes those archives.
- Keeps employee phone/email private from visitors.
- Can send Microsoft 365 email alerts through Microsoft Graph.
- Lets an administrator connect a dedicated Microsoft 365 sender mailbox, check its status, reconnect or disconnect it, and send a test email to an active employee. The Debian host keeps the existing app-only certificate setup as a fallback.
- Can still save visitor check-ins even if Microsoft 365 is not configured yet.
- Saves a check-in before attempting email; temporary failures retry with bounded
  backoff and stop after eight attempts for administrator review.
- Creates and verifies automatic backups. The Debian installer retains the
  newest 30 archives and requires its configured secondary destination to be
  an active mount.
- Includes a supervised Windows startup task that restarts the backend after an
  unexpected exit.
- Includes a Debian systemd service, daily backup timer, and Apache reverse-proxy template.
- Lets a logged-in admin manage employees and notification preferences from the dashboard.
- Refreshes visitor arrivals and employee data automatically while the dashboard is open.
- Refreshes the public kiosk host list automatically after employee changes.

For the company pilot, follow [docs/COMPANY_PILOT_RUNBOOK.md](docs/COMPANY_PILOT_RUNBOOK.md). Browse the [documentation index](docs/README.md) for the other current guides.
For a safe backup verification, follow [docs/BACKUP_RESTORE_DRILL.md](docs/BACKUP_RESTORE_DRILL.md).

## First-time setup

For Elite RF's Debian VM, use [the Debian and Apache deployment guide](docs/DEBIAN_APACHE_DEPLOYMENT.md). The Windows setup steps below apply only to Windows hosts.

1. Extract the clean deployment ZIP. Keep the extracted folder in a permanent location on the
   company Windows computer. The ZIP intentionally excludes `.env`, live data,
   photos, backups, tests, and internal development files.
2. Open that folder.
3. If Windows says the downloaded files are blocked, right-click the ZIP before extracting, choose **Properties**, select **Unblock**, and click **Apply**. If the folder was already extracted, right-click each launcher, choose **Properties**, select **Unblock**, and click **Apply**.
4. Install Python 3.11 or newer from https://www.python.org/downloads/windows/.
   Select **Add Python to PATH** and **Install for all users** so supervised
   startup can run before a user signs in.
5. Scan the ZIP with Microsoft Defender. Right-click the ZIP, choose
   **Properties**, select **Unblock** if it is shown, and click **Apply** before
   extracting. Extract a fresh copy after unblocking. If Defender reports a
     named threat, do not unblock or run the files; contact company IT and verify
     the download source.
6. Double-click `SET UP ELITE RF ON THIS COMPUTER.bat`. It creates the private
   settings file when needed, generates a random session secret, checks company
   readiness without displaying secrets, runs the disposable diagnostic, and
   can install supervised startup, daily backups, and a local-subnet firewall
   rule after owner setup is complete.

## Normal daily startup

1. Double-click `START ELITE RF VISITOR KIOSK.lnk` in the source project, or
   `START ELITE RF VISITOR KIOSK.bat` in the clean company package.

Keep the black server window open while testing or using the kiosk. It now
stays open and displays a clear error if Python is missing or port 8080 is in
use.

If Smart App Control blocks downloaded batch or PowerShell files, use the
approved installed Python interpreter directly. From a trusted local project
folder, run:

```powershell
python tools/create_shortcuts.py --python "C:\Path\To\Approved\python.exe"
```

This creates local, machine-specific shortcuts targeting the installed Python
executable. Do not copy the `.lnk` files to another computer; regenerate them
after moving the project or changing Python. A genuinely portable downloadable
one-click package requires legitimate trusted code signing.

You can also open a terminal in this folder and run:

```bash
python run.py
```

Then open:

```text
http://127.0.0.1:8080/kiosk
http://127.0.0.1:8080/admin
```

Starter admin password:

```text
change-me-before-use
```

On the very first run, open `/admin` and use the one-time owner setup card to
set the initial password. When `KIOSK_SESSION_SECRET` is not set, that flow
also rotates the configuration-managed session secret so the temporary
bootstrap state cannot linger. If the environment already manages the session
secret, the returned cookie uses that effective secret instead.

For a real company pilot with the remote kiosk and camera access, set
`KIOSK_TLS_CERT_PATH` and `KIOSK_TLS_KEY_PATH` to a trusted certificate and
matching private key for the hostname the kiosk will use. Also set
`KIOSK_PUBLIC_HOSTNAME` to that hostname so the launcher prints the correct
HTTPS URLs. The certificate must match that hostname and must be trusted by
the kiosk device.

If TLS is not configured, the app still runs over HTTP for local development,
but remote camera capture on the LAN kiosk requires trusted HTTPS.

Before enabling HTTPS for the company pilot, finish the first owner-password
setup over `http://127.0.0.1:8080/admin` on the host computer.

If you need to reset the password offline later, use the protected
`tools/set_admin_password.py` utility while preparing the company's computer.

## Safe testing

Double-click `START ELITE RF SAFE TEST MODE.lnk` in source, or `START ELITE RF SAFE TEST
MODE.bat` in the company package, to start a loopback-only test server with
disposable data. It displays `TEST MODE - NO REAL EMAIL - DISPOSABLE DATA`, never
loads `.env`, never sends email, and never writes the normal database, photos,
backups, or `config.json`. Test mode selects an available local port and opens
its local admin page automatically.

The disposable test administrator password is `test-owner-password-1234`.

The Notifications tab lets an admin connect the dedicated Microsoft 365 sender
mailbox over HTTPS and send a test email to an active employee. Safe test mode
never connects to Microsoft or sends a real email.

To check installation and launch prerequisites without reading secrets:

```powershell
.\tools\diagnose.ps1
```

The direct Python safe-test fallback is:

```powershell
python run.py --test-mode --host 127.0.0.1 --port 0 --open-admin --pause-on-error
```

## Managing employees

After logging into the admin dashboard, open the **Employees** tab. From there
an admin can add an employee, edit their name/email, inactivate
them from the public kiosk host list, permanently remove them, or reactivate a
previously inactivated employee. Destructive actions ask for confirmation.

Open the **Notifications** tab to enable or disable visitor alerts and edit the
email template. The supported placeholders are `{visitor_name}`, `{company}`,
`{location}`, and `{host_name}`. Microsoft 365 credentials remain in the host
computer's protected environment/certificate store and are never displayed in
the dashboard.

Visitor phone numbers are optional during check-in. Active employees require a
valid company email address for alerts.

## How the company kiosk connects

For the company Debian VM, use Apache as the HTTPS reverse proxy and keep the
Python backend on `127.0.0.1:8080`. The tablet URL is
`https://<approved-internal-hostname>/kiosk`; the admin dashboard is
`https://<approved-internal-hostname>/admin`. A permanent public DNS name is not
required, but the internal hostname must match a TLS certificate trusted by the
managed Android tablet and admin computers. See
`docs/DEBIAN_APACHE_DEPLOYMENT.md` for setup.

Have IT reserve the host address in DHCP and point the internal hostname to
that reservation. This keeps the URL stable across restarts. Restrict access to
the approved Wi-Fi subnet or managed devices, block guest/WAN access, and keep
backend port 8080 private. The host server must stay running and connected for
both pages to load.

Production mode refuses to bind the backend to a non-loopback interface without
TLS. HTTP is available only for loopback development or disposable test mode.
The production Android app rejects HTTP, even on a trusted company Wi-Fi. It
must be signed with Elite RF's permanent release key and enrolled through MDM;
the home debug APK and test certificate are not production deployment artifacts.

## Microsoft 365 email setup

The admin dashboard's **Notifications** tab has a Microsoft 365 setup wizard.
Enter the tenant and application IDs and exact HTTPS callback supplied by IT;
upload an IT-approved certificate (preferred for production), or generate a
pilot certificate if company policy allows. The dashboard keeps the private
key encrypted on the host. Give IT the public certificate so IT can register
it on the Entra app, add the callback, and approve delegated Microsoft Graph `Mail.Send`.
Then press **Connect Microsoft 365 account**, sign in to the sender mailbox,
and send a test email. IT must create or approve the Entra app and its permission;
the kiosk cannot create those resources inside the company's Microsoft tenant.

For the full sequence and security details, see
[`docs/MICROSOFT_365_EMAIL_SETUP.md`](docs/MICROSOFT_365_EMAIL_SETUP.md).
The encrypted Microsoft app settings and sign-in cache stay under the runtime
`data/` folder and are not included in visitor backups. Safe test mode disables
Microsoft sign-in and real email.

Existing deployments may continue using the certificate-based app-only
fallback. Its host-only values are `M365_TENANT_ID`, `M365_CLIENT_ID`,
`M365_SENDER_ADDRESS`, `M365_CERTIFICATE_PATH`, `M365_CERTIFICATE_THUMBPRINT`,
`M365_PRIVATE_KEY_PATH`, and `M365_GRAPH_SCOPE=https://graph.microsoft.com/.default`.
Never place real values in source control.

For a Windows host using direct backend TLS, configure these certificate values:

```text
KIOSK_TLS_CERT_PATH
KIOSK_TLS_KEY_PATH
KIOSK_PUBLIC_HOSTNAME
```

For the company's Debian VM behind Apache, Apache owns the HTTPS certificate;
leave the backend `KIOSK_TLS_CERT_PATH` and `KIOSK_TLS_KEY_PATH` empty and use
`KIOSK_EXTERNAL_HTTPS=true` with the internal hostname in `KIOSK_PUBLIC_HOSTNAME`.

Optional unattended-backup values (Debian deployments must use an active,
separately managed mount point):

```text
KIOSK_BACKUP_COPY_PATH=D:\Elite-RF-Backups
KIOSK_BACKUP_RETENTION=30
KIOSK_REQUIRE_MOUNTED_BACKUP=false
```

Then add real employee company email addresses in the admin dashboard.

Microsoft Graph credentials and certificates remain on the host only. `.env` is
loaded only by the Python backend and is not part of the portable ZIP package.

Until Microsoft 365 is configured, the app saves check-ins but marks email as skipped or failed.

## Where data is stored

```text
data/visitor-kiosk.sqlite3     SQLite database
storage/photos/                Visitor photos
backups/                       Backup zip files
```

## Backups

Run:

```bash
python tools/backup.py
```

This creates a verified ZIP containing a consistent database snapshot, every
stored visitor photo, `config.json`, and a self-checking recovery inventory.
The ZIP may contain authentication material from `config.json`, so treat it as
sensitive. It deliberately excludes `.env`, TLS private keys, and signing keys;
keep those separately in company-approved encrypted secret storage.

Use [docs/BACKUP_RESTORE_DRILL.md](docs/BACKUP_RESTORE_DRILL.md) to verify a
backup in a disposable folder before attempting any real recovery.

The running backend creates a backup immediately when no verified backup
exists, then whenever the newest verified backup is older than 24 hours. It
keeps the newest 30 by default and repairs a missing or damaged secondary copy.
For recovery
after a power outage, install the two Windows tasks once on the host PC from an
Administrator PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process RemoteSigned
.\tools\install_startup_tasks.ps1
```

The first task supervises the kiosk after Windows starts and restarts it after
an unexpected exit. The second creates a verified daily 2:00 AM backup and runs
after a missed schedule when Windows becomes available. Set
`KIOSK_BACKUP_COPY_PATH` to encrypted external or company-approved storage; a
backup on the same computer cannot protect against theft or total disk failure.

## Android management

For unattended tablet updates, publish the production-signed
`com.eliterf.kiosk` APK as a private Managed Google Play app through the
company's Android Enterprise/MDM service. Set it to high-priority automatic
updates and supply the managed `kiosk_url` value. The app accepts only a valid
trusted HTTPS kiosk URL and otherwise keeps its last valid setting. This
requires one-time company IT enrollment; the app intentionally does not
download or install code on its own.

## Windows-host alternative production checklist

These host-specific steps apply only if IT approves Windows instead of the
company's selected Debian VM. For Debian and Apache, follow
`docs/DEBIAN_APACHE_DEPLOYMENT.md`.

- Change admin password.
- Set a long random `KIOSK_SESSION_SECRET`.
- Set `KIOSK_TLS_CERT_PATH` and `KIOSK_TLS_KEY_PATH` to a real trusted certificate and key.
- Add real employees and company email addresses.
- Configure Microsoft 365 email and send one authorized real test alert.
- Use HTTPS for real kiosk camera access.
- Restrict Windows Firewall to the company network.
- Enable BitLocker on the host computer.
- Set up Windows Task Scheduler to start the app on boot.
- Set up scheduled daily backups.
- Keep visitor records and photos indefinitely; backup archives use a separate rolling retention policy.

## Windows security warnings

The launchers use PowerShell's process-scoped `RemoteSigned` policy and do not
use `ExecutionPolicy Bypass`. Windows may still mark files downloaded from the
internet with Mark-of-the-Web. To inspect that metadata without removing it,
run this in PowerShell:

```powershell
Get-Item -LiteralPath '.\START ELITE RF VISITOR KIOSK.lnk' -Stream Zone.Identifier -ErrorAction SilentlyContinue
```

Only unblock a ZIP or extracted copy after verifying that it came from the
approved project source and Microsoft Defender reports no named threat. Do not
disable Defender, create exclusions, or ignore a named malware detection. If a
trusted distribution certificate is available, Authenticode signing is the
preferred long-term distribution improvement.




