# Elite RF Visitor Kiosk Company Pilot Runbook

This runbook is for the selected company setup: the Python backend on the Debian VM, Apache providing HTTPS, and the managed Android tablet opening the trusted `/kiosk` page. For host installation details, follow [DEBIAN_APACHE_DEPLOYMENT.md](DEBIAN_APACHE_DEPLOYMENT.md). Use fictional visitor information until the pilot is approved.

## Before the pilot

1. **Install and secure the Debian service.** Complete the deployment guide. Confirm the backend service starts after reboot and listens only on `127.0.0.1:8080`.
2. **Verify Apache and network access.** Use the company-approved internal hostname or reserved IP and a trusted certificate matching that exact address. Replace the template TEST-NET values with the approved admin network and tablet address. Confirm the tablet can reach check-in, an ordinary network device cannot submit check-ins, and admin/photo paths are limited to approved admin devices. Do not expose backend port 8080.
3. **Set up the admin owner and email.** Set a strong owner password locally. In **Notifications → Prepare Microsoft 365**, enter the Entra tenant/app IDs and exact HTTPS callback, then generate and download the public app certificate. IT registers that certificate and callback on the Entra app and approves delegated `Mail.Send`. Then an admin selects **Connect Microsoft 365 account**, signs in to the dedicated sender mailbox, and sends one approved test message to a controlled company recipient.
4. **Verify storage and backups.** Confirm the data volume and secondary backup destination are encrypted and access-controlled, the backup timer is active, the latest backup succeeded, and the restore drill in [BACKUP_RESTORE_DRILL.md](BACKUP_RESTORE_DRILL.md) succeeds in a separate test location. Confirm the dashboard reports storage and backup status.
5. **Provision and inspect the tablet.** Install the approved production-signed kiosk app through company MDM, set its managed URL to the trusted HTTPS `/kiosk` address, and follow the tablet [physical acceptance procedure](../development/android-kiosk/docs/ACCEPTANCE_TEST_ANDROID14.md) and record results on its [acceptance checklist](../development/android-kiosk/docs/MESWAO_ACCEPTANCE_CHECKLIST.md). Verify Device Owner, Lock Task, camera capture, recovery after network loss/reboot, and administrator maintenance access.

## Fictional end-to-end check

Use a fictional visitor and an approved test host. This check creates a record and photo in the configured pilot system and sends the host notification.

- Submit one fictional check-in on the tablet.
- Confirm the record and photo appear in the authenticated admin dashboard.
- Confirm the selected host receives the email with the correct visitor, location, and time.
- Confirm public kiosk pages show only the intended employee names, not private employee contact details.
- Restart the backend and confirm the record and photo remain.
- Confirm the visitor storage and backup indicators remain healthy.

Visitor records and photos stay in the live system until an administrator deletes a visit in the dashboard. Existing backups can retain deleted visits until they expire through normal rotation; restoring an older backup can bring those visits back.

## Pilot sign-off

| Check | Result | Date / initials |
|---|---|---|
| Trusted HTTPS certificate and stable internal address | Pass / Fail | |
| Apache paths and network restrictions | Pass / Fail | |
| Backend bound to loopback; service starts after reboot | Pass / Fail | |
| Approved Microsoft 365 test email delivered | Pass / Fail | |
| Encrypted secondary backup and restore drill | Pass / Fail | |
| Tablet physical acceptance checklist | Pass / Fail | |
| Fictional check-in, photo, email, and restart persistence | Pass / Fail | |

Do not switch to real visitor information until the company has approved the pilot and all required checks pass. For ongoing recovery, use [BACKUP_RESTORE_DRILL.md](BACKUP_RESTORE_DRILL.md); for logs and retention, use [LOG_RETENTION_AND_RECOVERY.md](LOG_RETENTION_AND_RECOVERY.md).
