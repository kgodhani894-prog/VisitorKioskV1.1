# MESWAO Android 14 Provisioning

This process is intentionally manual and non-destructive. It assumes the tablet is Android 14/API 34 and has no existing Device Owner. Factory reset, if required, must be performed by the owner or IT separately.

## Prepare

1. Back up anything that must be retained, then prepare the tablet without a Google account, secondary user, or work profile.
2. Enable Developer Options and USB debugging.
3. Install the official Android Platform Tools on the provisioning computer, or place the official `platform-tools` folder beside the provisioning script.
4. Put the debug APK at the path used by the script.
5. Maintenance mode uses Android's exact-alarm capability to restore kiosk policy after five minutes. The sideloaded company APK declares this capability directly and still refuses to unlock Settings if Android reports that exact scheduling is unavailable.

## PowerShell

From `development/android-kiosk`, run:

```powershell
.\tools\provision_android.ps1 -ApkPath .\app\build\outputs\apk\debug\app-debug.apk
```

The script requires exactly one attached device, reports its serial/API level, checks for an existing owner, installs the APK, assigns:

```text
com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver
```

It refuses to factory-reset or continue after an assignment failure.

After completing the app's one-time HTTPS/PIN setup, verify true Lock Task Mode without reinstalling or reassigning Device Owner:

```powershell
.\tools\provision_android.ps1 -ApkPath .\app\build\outputs\apk\debug\app-debug.apk -VerifyLockTask
```

After ADB verification and the physical pre-lockdown checks pass, enter the hidden administrator console on the tablet. Select **Activate production lockdown**, re-enter the administrator PIN, and type `LOCKDOWN`. This final step adds Android's debugging restriction and will normally disconnect ADB. It is intentionally separate so verification is not interrupted immediately after Device Owner assignment.

## Bash

```bash
./tools/provision_android.sh ./app/build/outputs/apk/debug/app-debug.apk
```

After provisioning, launch the app, enter the trusted HTTPS kiosk URL, create the unique administrator PIN, save the one-time recovery code, and verify the camera. Reboot and confirm the app returns directly to the kiosk.

After setup, repeat with `./tools/provision_android.sh ./app/build/outputs/apk/debug/app-debug.apk --verify-lock-task` to verify true Lock Task Mode. This verification path only checks the existing installation and Device Owner state.

Then activate production lockdown from the authenticated on-tablet administrator console and complete the remaining manual acceptance checks.

## Updating

Every update must be signed with the same production signing key. See `keystore.properties.example`. Do not uninstall the existing Device Owner app to update it. Use the approved maintenance/update path documented by IT.

## Decommissioning

Follow `DECOMMISSION_ANDROID14.md`. A production Device Owner must not expose an unauthenticated removal action. Android recommends a company-approved factory reset for permanent retirement because non-destructive Device Owner clearing is deprecated testing behavior and may leave policies or data behind.
