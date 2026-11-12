# Android Kiosk Security Model

## Device control

Normal visitor mode requires Device Owner status and `isLockTaskPermitted()`. The DPC allowlists only this package, uses `LOCK_TASK_FEATURE_NONE`, sets the app as the persistent Home activity, disables the status bar/keyguard/screen capture, and applies Android user restrictions for reset, safe boot, users, volume, USB/media, unknown apps, debugging, Wi-Fi, and Bluetooth configuration.

Android cannot prevent a hardware power button, unplugging, a recessed reset switch, or OEM recovery combinations. Reboot recovery is provided by the boot receiver and persistent Home policy.

## WebView

Only HTTPS is accepted. The approved origin is derived from the administrator-entered URL. Same-origin kiosk pages, static resources, employee loading, and check-in submission are allowed. Admin/authentication routes, HTTP, custom schemes, downloads, popups, mixed content, file/content access, and cross-origin navigation are blocked. SSL errors are cancelled rather than bypassed.

The camera grant is restricted to video capture from the exact approved origin. Microphone, geolocation, downloads, and file access are denied. Production windows use `FLAG_SECURE`.

The display is held awake while visitor mode is in the foreground. Camera capture starts only when the visitor enters the photo step and is released after capture, back, reset, page exit, renderer replacement, or administrator entry.

## Administrator access

Five taps in the upper-left branding area open an in-app PIN gate. After authentication, the administrator can enter a five-minute maintenance exit or open administrator controls. The initial PIN is 8-12 digits, stored only as PBKDF2-HMAC-SHA-256 material with a random salt, and locks after five failures for five minutes. A random recovery code is displayed once and only its hash is retained. There is no default production PIN.

The administrator console never displays backend secrets or visitor data. The Android app has no Microsoft 365 credentials and no unsafe JavaScript bridge.

## Open limitation

The repository does not contain an approved private CA certificate. The app therefore uses Android system trust only. If the company hostname uses a private CA, IT must provide the approved public CA and the Android release process must add it only for that exact domain before deployment.

The app never enables WebView debugging, including in builds used for compatibility testing, and maintenance remains time-limited with exact automatic policy restoration. An ordinary power press wakes straight back to visitor mode; Android still reserves hardware forced-reboot and Emergency SOS behavior for the operating system.
