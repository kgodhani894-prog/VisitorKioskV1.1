# Elite RF Visitor Kiosk Android App

This Kotlin Android 14 Device Owner/DPC shell connects to the Elite RF company backend. The planned company host is a Debian Linux VM running the Python service behind Apache HTTPS. The Android app does not replace the backend or store visitor records locally.

## Build

Install Android Studio or the Android command-line tools with JDK 17, then run from this folder:

```text
gradle test
gradle lint
gradle assembleDebug
```

The debug APK is at `app/build/outputs/apk/debug/app-debug.apk`. It is signed with the Android debug certificate and is for tablet compatibility/provisioning proof only. A permanent company release keystore is still required for production updates.

To create the single-file tablet deployment bundle after building, run:

```powershell
.\tools\build_android_deployment.ps1
```

This creates `Elite-RF-Android-Kiosk-Deployment.zip` with the signed release APK, installer, verifier, acceptance checklist, and decommission instructions. Use `-ApkPath` only when deliberately building a separate test package; do not deploy the debug-signed APK as the long-term company release.

## First setup

Provision the tablet as Device Owner using `docs/PROVISIONING_ANDROID14.md`, launch the app, enter the trusted HTTPS `/kiosk` URL, and create the unique 8-12 digit administrator PIN. Save the one-time recovery code.

The app rejects HTTP and certificate errors, and blocks `/admin` plus all admin API routes. The production server must have a trusted certificate for the exact hostname entered during setup.

## Automatic connection

The tablet opens the approved kiosk URL automatically after boot. It checks the backend while the kiosk is open and, if the network or backend is unavailable, shows a branded offline screen and retries every few seconds. When trusted HTTPS becomes reachable again, the app creates a fresh browser session and returns to the check-in form without visitor action.

Use an IT-managed hostname or reserved static IP for the backend computer. The HTTPS certificate must include that exact hostname or IP. A changing address cannot be repaired by the tablet retry loop.

Run every physical check in `docs/ACCEPTANCE_TEST_ANDROID14.md` on the MESWAO tablet. Source tests cannot prove OEM behavior such as volume keys, notification suppression, camera firmware compatibility, or 72-hour recovery.
