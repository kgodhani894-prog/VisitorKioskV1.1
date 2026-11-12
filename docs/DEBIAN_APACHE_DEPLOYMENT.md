# Debian VM and Apache deployment

Use this procedure for the Elite RF internal Debian VM. Apache terminates HTTPS and proxies requests to the Python backend on loopback. The backend must not listen on a LAN address or be exposed directly.

## IT prerequisites

- A supported Debian VM with systemd, sudo, the company's approved apt sources, and its approved Python package mirror configured. The installer obtains `python3`, `python3-venv`, `apache2`, `openssl`, and `ca-certificates` from apt if missing, and installs the Python dependency through the VM's configured `pip` index; it does not select or validate a package index. Python 3.11 or newer must be available from the approved Debian sources.
- A stable internal address for the VM: either an internal hostname or a DHCP-reserved IP address. Permanent or public DNS is not required. Apache's TLS certificate must match the exact address used by clients (hostname in a DNS subject alternative name, or IP address in an IP subject alternative name), and its certificate chain must be trusted by the managed Android tablet and admin computers. The current APK trusts Android system CAs only. A public CA works when the device trusts it; if Elite RF uses a private CA that is not in Android's system trust store, IT must provide its root certificate for a reviewed app-specific trust configuration scoped to the kiosk address. A user-installed CA alone is not enough for this APK.
- Persistent VM storage for `/var/lib/elite-rf-visitor-kiosk` and an encrypted secondary backup destination mounted at a stable path.
- A dedicated Microsoft 365 sender mailbox and Entra app registration with delegated Graph `Mail.Send`, an HTTPS web callback registered exactly as `https://<company-internal-hostname>/oauth/microsoft/callback`, and a protected certificate credential. See `MICROSOFT_365_EMAIL_SETUP.md` for the dashboard connection flow. Existing deployments may keep the app-only permission as a fallback.
- Permission for the VM to reach the approved Python package mirror and Microsoft identity/Graph endpoints over outbound HTTPS.

The Apache certificate is used by Apache. Leave `KIOSK_TLS_CERT_PATH` and `KIOSK_TLS_KEY_PATH` empty for the Python service; set `KIOSK_EXTERNAL_HTTPS=true` so the backend marks admin cookies Secure and reports the external HTTPS URL. That setting is accepted only when Python binds to loopback. If permanent DNS is unavailable, IT may use the VM's reserved internal IP as `KIOSK_PUBLIC_HOSTNAME`, Apache's `ServerName`, and the tablet URL, provided the certificate contains that IP as an IP subject alternative name and the tablet trusts its issuer. Without a trusted matching certificate, the production APK cannot safely connect; use a debuggable build only for approved testing, not production operation.

## Install the service

1. Transfer the clean company deployment ZIP to the VM through the company's approved software process, verify its checksum, and extract it to a temporary or staging directory. Do not transfer the development workspace, live database, visitor photos, `.env`, or Android signing material.
2. Confirm the VM is permitted to install packages from the approved Debian mirror and that root's `pip` configuration selects Elite RF's approved Python package mirror with certificate verification enabled. The installer installs missing OS prerequisites and enables Apache modules; it does not upgrade the VM's existing packages or choose an external package source.
3. From the extracted package directory, run:

   ```bash
   sudo bash app/tools/linux/install_debian.sh
   ```

   The installer obtains missing OS prerequisites from the approved apt sources, creates the restricted `eliterfkiosk` service account, application virtual environment, `/opt/elite-rf-visitor-kiosk` code directory, and `/var/lib/elite-rf-visitor-kiosk` runtime directory. It keeps existing runtime configuration when upgrading, enforces the Debian HTTPS-proxy and mounted-backup safety settings, creates or preserves a random session secret, protects the environment file, prompts for the initial admin password if the sanitized default is still active, installs a hardened systemd unit, and starts/restarts the backend. It creates an initial local backup when no secondary destination is configured. The daily timer is enabled only after the configured secondary destination is mounted and writable, and a one-shot backup to it succeeds.

4. Edit `/etc/elite-rf-visitor-kiosk/kiosk.env` as root. Set `KIOSK_PUBLIC_HOSTNAME` and `KIOSK_BACKUP_COPY_PATH` to the approved secondary filesystem's mount point. Keep `KIOSK_REQUIRE_MOUNTED_BACKUP=true`. When that path is configured but is unmounted or not writable, a backup job fails before creating a new local archive; it will not silently fall back to filling the VM's primary filesystem. Keep the settings directory owned by `root:eliterfkiosk` with mode `0750`, and the environment file owned by `root:eliterfkiosk` with mode `0640`. The dashboard-managed delegated mailbox flow does not require `M365_*` values in this file: after Apache's trusted HTTPS address is working, an admin enters the tenant ID, app ID, and exact callback in **Notifications → Prepare Microsoft 365**, then saves the app certificate there. IT must register that certificate and callback in Entra and approve delegated `Mail.Send`. For the older app-only fallback only, configure the `M365_*` environment values and store its certificate/private key in a root-owned, service-readable restricted directory under `/etc/elite-rf-visitor-kiosk/` (for example, `/etc/elite-rf-visitor-kiosk/m365/`); set the private key file to `root:eliterfkiosk` mode `0640`. That Microsoft app credential is separate from Apache's HTTPS server certificate. The systemd unit hides `/home` from the service. Do not put real credentials into this guide or the application `config.json`. Restart the app after changing settings: `sudo systemctl restart elite-rf-visitor-kiosk`.
5. Ensure the secondary backup mount exists, set `KIOSK_BACKUP_COPY_PATH` to its absolute mount point, and grant `eliterfkiosk` write access. Use a simple absolute path without spaces, such as `/mnt/elite-rf-kiosk-backups`. Rerun the installer after configuring it. The installer adds a dedicated systemd drop-in that orders the backup service after the mount and grants only that service write access to the path. It enables the daily timer only if the path is an active mount and writable by the service account. If the mount is missing or permissions are wrong, the timer stays disabled and the installer reports what to correct.
6. Confirm `systemctl list-timers elite-rf-visitor-kiosk-backup.timer` shows the timer, then confirm the backup service succeeds and the archive appears on both filesystems before the pilot.

The backend binds to `127.0.0.1:8080`. Check its state with `systemctl status elite-rf-visitor-kiosk`; read service messages with `journalctl -u elite-rf-visitor-kiosk`. Check daily backups with `systemctl list-timers elite-rf-visitor-kiosk-backup.timer` and `journalctl -u elite-rf-visitor-kiosk-backup.service`.

## Configure Apache

1. Review `/usr/local/share/elite-rf-visitor-kiosk/apache-vhost.example.conf`. Copy it to `/etc/apache2/sites-available/elite-rf-visitor-kiosk.conf`, replacing the example `ServerName`, certificate file, and key file paths with company-approved values. The template allows TLS 1.2 and 1.3, and disables SSLv3, TLS 1.0, and TLS 1.1. Replace the reserved TEST-NET addresses in both `Require ip` rules: use the approved admin subnet for `/admin`, `/api/admin/`, and `/photos/`, and the tablet's DHCP-reserved address for `/api/checkins`. These safe example addresses intentionally deny access until IT supplies the real ranges.
2. Confirm the config retains `RequestHeader unset X-Forwarded-For` before proxying and `ProxyAddHeaders On`. Apache then supplies the actual client address to the backend rather than accepting a client-supplied forwarded address. Keep the path access rules; the app's first-run web setup remains disabled in this deployment, and the installer sets the owner password locally before starting the service.
3. Enable the Apache modules and site, test the configuration, and reload Apache:

   ```bash
   sudo a2enmod proxy proxy_http headers ssl authz_host
   sudo a2ensite elite-rf-visitor-kiosk.conf
   sudo apachectl configtest
   sudo systemctl reload apache2
   ```

4. Restrict inbound HTTPS to the managed kiosk device or dedicated kiosk VLAN and authorized admin subnet. Visitor submission is intentionally unauthenticated, so do not expose `/api/checkins` to every device on the corporate Wi-Fi; reserve the tablet's address or use a managed VLAN and apply matching firewall or Apache path access rules. Restrict `/admin` and `/api/admin/` to authorized admin networks. Keep TCP 8080 loopback-only; do not add a firewall rule exposing it. If HTTP on port 80 is enabled, redirect it to HTTPS and do not use it as the managed tablet URL.
5. From an admin workstation, open `https://<company-hostname>/admin`; from the tablet, set the managed URL to `https://<company-hostname>/kiosk`. Do not proceed with production camera testing while the certificate is untrusted or produces a browser warning.

## Data, logs, and recovery

- SQLite visitor records: `/var/lib/elite-rf-visitor-kiosk/data/visitor-kiosk.sqlite3`.
- Visitor photos: `/var/lib/elite-rf-visitor-kiosk/storage/photos/`.
- Local verified backups: `/var/lib/elite-rf-visitor-kiosk/backups/`.
- Microsoft 365 and session settings: `/etc/elite-rf-visitor-kiosk/kiosk.env`.
- Visitor check-in history: sign in at `/admin`, use the Visits/Logs view, or use its CSV export. Apache access logs under `/var/log/apache2/` show HTTP requests; they are not the visitor record log.

Visitor records and photos remain in the primary application database and photo directory until an administrator deletes a visit from the authenticated dashboard. The database records a non-personal deletion event, and the app retries photo cleanup after interruptions. Existing backup archives may still contain deleted records and photos until normal rolling rotation removes them; restoring a pre-deletion archive can restore those records. The Debian installer sets backup retention to 30 archives. Keep primary data and backups under company encryption, access control, capacity monitoring, and retention controls. Before real use, perform a restore drill into a separate test directory and verify records and photos.

The backup ZIPs are not encrypted by the application. IT must provide encryption at rest for the VM data volume and the secondary backup destination, limit access to the kiosk service/admin recovery group, and test recovery of the encryption keys. `/etc/elite-rf-visitor-kiosk/kiosk.env` and the Microsoft 365 private key are excluded from application backups; keep recovery copies in the company's encrypted secrets vault. If the session secret must be regenerated after a server loss, existing admin sessions are invalidated but visitor data is unaffected.

The admin dashboard checks free space for the records, photo, local backup, and configured secondary backup filesystems every 15 seconds while it is open. It shows a warning at 85% used and a critical alert at 95% used; an unconfigured or unmounted secondary destination is also shown as critical. It also reports the last successful backup operation: warning after 30 hours, critical after 48 hours, and critical if no success has been recorded. A success marker is written only after the backup operation completes. These are operational warnings, not automatic cleanup or a substitute for VM-level monitoring and capacity planning. Increase or extend the underlying VM storage and confirm the filesystem sees the added capacity.

The Debian systemd timer is the only scheduled backup creator; the backend's background loop continues notification retries but skips its own backup schedule. Each backup is a full snapshot of the database and photo directory, not an incremental copy. For planning, if average photo size is `S`, check-ins per day are `N`, and `R` daily archives are retained, the photo portion of one full-backup set is roughly `S × N × R × (R + 1) / 2`, plus the live photo library and database. With 30 daily archives, that multiplier is 465 times one day's incoming photo volume at each backup location. JPEG compression usually provides little additional reduction; measure real pilot photos and size both filesystems with headroom.

## Updating application code

1. Create and verify a new clean deployment ZIP on a trusted build machine.
2. Stage and inspect it on the VM; retain a rollback copy of the current application code.
3. Run the new package's Linux installer. It replaces application code, refreshes the Python virtual environment, and restarts the backend service while preserving `/var/lib/elite-rf-visitor-kiosk` and the protected `/etc` settings.
4. Review `systemctl status` and `journalctl`, then verify the HTTPS admin and kiosk pages and run a controlled check-in/email pilot.

Never copy the current workstation's runtime data into the company VM unless IT explicitly approves a data migration. Begin with clean company state and company-managed accounts.
