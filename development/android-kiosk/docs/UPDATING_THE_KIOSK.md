# Updating the Android Kiosk

For unattended company updates, enroll the tablet in the company's Android Enterprise/MDM service and publish `com.eliterf.kiosk` as a private Managed Google Play app. Keep every release signed with the same protected production key and increase `versionCode` for each release.

Set the app to high-priority automatic updates and provide the managed `kiosk_url` value as the trusted company HTTPS kiosk URL. The app reads that managed value at startup, resume, and whenever the management service changes it. Invalid or non-HTTPS managed URLs are ignored.

This is a one-time IT setup. Until an MDM tenant and private Play listing exist, APK updates remain a controlled maintenance task; the app does not download or install unsigned code itself.

After every rollout, confirm Device Owner status, Lock Task, HTTPS, camera, check-in, and reboot recovery. Never commit or share the keystore, passwords, PINs, recovery codes, private TLS keys, Microsoft 365 values, or backend `.env`.
