# Decommissioning the Android kiosk

Device Owner removal is a company-IT operation. Android's ADB `remove-active-admin` command applies only to applications built as `testOnly`; the production Elite RF APK must not be test-only. Android also documents programmatic `clearDeviceOwnerApp()` as deprecated, testing-only behavior and recommends a factory reset so device-owner data and policies are not left behind.

1. Confirm that visitor records and photos are safely stored on the Windows backend. The Android app is only the kiosk shell.
2. Enter the hidden administrator console and use the authenticated five-minute maintenance window.
3. Record the tablet serial number and confirm with company IT that the device is approved for retirement or repurposing.
4. Company IT should factory-reset the tablet using the manufacturer's approved Android 14 procedure. Do not add an unprotected “remove Device Owner” button to the production kiosk.
5. After reset, confirm `com.eliterf.kiosk` is absent and the Elite RF app is no longer Device Owner before assigning the tablet to another user.

If the display is damaged or Settings cannot be reached, use the MESWAO manufacturer's approved recovery procedure. Hardware recovery behavior is outside the APK and must be tested on the actual tablet.
