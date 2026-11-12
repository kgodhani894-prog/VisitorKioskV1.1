ELITE RF VISITOR KIOSK - ANDROID 14

This package installs the Elite RF kiosk APK and provisions a clean company-owned
Android 14 tablet as a dedicated Device Owner kiosk.

IMPORTANT

- Device Owner setup is a one-time administrator procedure. Copying or tapping
  the APK alone cannot grant Android's kiosk privileges.
- Use a clean tablet with no Google account, work profile, secondary user, or
  existing Device Owner.
- The production APK must be signed with Elite RF's permanent signing key.
- Keep that signing key and its passwords in company-approved secure storage.
- Never share the administrator PIN or recovery code with visitors.
- Android can block the ordinary power menu, Home, Back, Recents, volume changes,
  notifications, Quick Settings, Settings, and other apps. No Android app can
  guarantee that an OEM hardware forced-reboot or recovery-button combination is
  disabled.
- In visitor mode, a short power press wakes straight back to the kiosk. Five
  taps in the upper-left Elite RF branding area open the administrator PIN gate.
- The screen stays awake in visitor mode. Camera use is limited to the photo
  step and is released immediately when that step ends or administrator access
  begins.
- The app checks the backend continuously. If the network or server is offline,
  it shows the Elite RF offline screen and retries automatically until trusted
  HTTPS is available again.
- The backend must use an IT-managed hostname or reserved static IP, and the
  HTTPS certificate must match that exact address.

ONE-TIME INSTALLATION

1. Install the official Android Platform Tools on the Windows setup computer,
   or place the official platform-tools folder beside INSTALL_ELITE_RF_KIOSK.ps1.
2. Factory-prepare the tablet as described in PROVISIONING_ANDROID14.md.
3. Enable Developer Options and USB debugging temporarily.
4. Connect exactly one tablet and approve the USB debugging prompt.
5. Right-click PowerShell and open it in this package folder.
6. Run:

   .\INSTALL_ELITE_RF_KIOSK.ps1

7. On the tablet, enter the trusted Elite RF HTTPS /kiosk URL, create the unique
   8-12 digit administrator PIN, and save the one-time recovery code.
8. Verify lockdown:

   .\INSTALL_ELITE_RF_KIOSK.ps1 -VerifyLockTask

9. Complete the ADB-assisted checks in ACCEPTANCE_TEST_ANDROID14.md.
10. Open the hidden administrator console, select Activate Production Lockdown,
    re-enter the PIN, and type LOCKDOWN. This applies the final debugging
    restriction and normally disconnects ADB.
11. Complete the remaining manual checks and reboot test.

The app must be provisioned and physically tested on the MESWAO tablet before
the company treats it as production-ready.
