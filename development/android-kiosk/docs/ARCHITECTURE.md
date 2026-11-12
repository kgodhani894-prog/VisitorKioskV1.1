# Elite RF Android Kiosk Architecture

The APK is a Device Policy Controller (DPC) shell around the existing Windows-hosted kiosk website. It does not duplicate SQLite, photos, employee data, email delivery, or the admin dashboard.

- Package: `com.eliterf.kiosk`
- Entry activity: `com.eliterf.kiosk.MainActivity`
- Device admin receiver: `com.eliterf.kiosk.admin.EliteDeviceAdminReceiver`
- Website: the exact HTTPS `/kiosk` URL entered during one-time setup
- Backend: the existing Python server and its `/api/employees` and `/api/checkins` routes

The app refuses visitor mode until Android reports that this package is the Device Owner and the server URL/PIN setup is complete. It applies Lock Task policies, disables screen capture, disables cleartext traffic, restricts WebView navigation to the approved kiosk origin, and blocks `/admin` and `/api/admin/*`.

Visitor data is sent to the backend and is not queued or stored as an application record on the tablet. WebView session state is cleared after inactivity or through the administrator console.

No production hostname, certificate, signing key, PIN, recovery code, or backend secret is embedded in the project.
