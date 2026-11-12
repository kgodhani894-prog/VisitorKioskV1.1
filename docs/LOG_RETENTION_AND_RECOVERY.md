# Visitor Record Retention and Recovery

Visitor records and their photos remain in the live application until an
administrator deletes an individual visit from the authenticated dashboard.
Deletion removes the database row and its photo from the live system and records
a non-personal deletion event in the audit log. The application retries photo
cleanup after interruptions and reports any pending cleanup in dashboard health.
Existing backups may retain a deleted visit until normal backup rotation removes
those archives; a restored pre-deletion backup can therefore restore that visit.
Administrators can also export the complete current history. Exports contain
sensitive personal information and must be handled as controlled records.

## Backup requirements

Backups have a separate rolling retention count; the Debian installer sets it
to 30 archives. The primary visitor records and photos are not pruned when old
backup archives rotate. Backup ZIPs are not encrypted by the application, so IT
must use encrypted VM storage and an encrypted, access-controlled secondary
mount. Set `KIOSK_REQUIRE_MOUNTED_BACKUP=true` and configure the secondary path
as the mount point itself. The application refuses to copy to an unmounted
destination and reports an unconfigured or unmounted destination in the admin
health panel.

Production acceptance requires IT to verify:

1. The secondary backup mount is configured, mounted at boot, and writable by
   the kiosk service account.
2. Local backup creation, archive verification, secondary copy failures, and
   server capacity are monitored.
3. A restore drill succeeds on a separate replacement or disposable system.
4. Encryption keys can be recovered through the company's approved process.
5. TLS, account permissions, export handling, capacity growth, and company
   retention controls are documented.

No software can guarantee that records survive every physical or storage
failure. Visitor rows remain until an administrator deletes them through the
authenticated dashboard; backups are monitored and recovery is regularly tested.
