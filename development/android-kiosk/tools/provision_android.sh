#!/usr/bin/env bash
set -euo pipefail
APK_PATH="${1:?Usage: provision_android.sh path/to/app-debug.apk}"
VERIFY_LOCK="${2:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$SCRIPT_DIR/platform-tools/adb" ]]; then
  ADB="$SCRIPT_DIR/platform-tools/adb"
elif [[ -x "$SCRIPT_DIR/../platform-tools/adb" ]]; then
  ADB="$SCRIPT_DIR/../platform-tools/adb"
elif command -v adb >/dev/null; then
  ADB="$(command -v adb)"
else
  echo 'Android Platform Tools (adb) are required. Install them or place the official platform-tools folder beside this script.' >&2
  exit 1
fi
[[ -f "$APK_PATH" ]] || { echo "APK not found: $APK_PATH" >&2; exit 1; }
mapfile -t DEVICES < <("$ADB" devices | awk '$2 == "device" { print $1 }')
[[ "${#DEVICES[@]}" -eq 1 ]] || { echo "Exactly one authorized Android device is required." >&2; exit 1; }
SERIAL="${DEVICES[0]}"
API="$("$ADB" -s "$SERIAL" shell getprop ro.build.version.sdk | tr -d '\r')"
echo "Device: $SERIAL"
echo "Android API: $API"
[[ "$API" == "34" ]] || { echo 'This release requires Android 14/API 34.' >&2; exit 1; }
OWNER="$("$ADB" -s "$SERIAL" shell dpm list-owners 2>&1 || true)"
if [[ "$OWNER" == *"Unknown command"* ]]; then
  OWNER="$("$ADB" -s "$SERIAL" shell dpm get-device-owner 2>&1 || true)"
fi
NO_OWNER=false
if [[ "$OWNER" == *"No device owner"* || "$OWNER" == *"no owners"* ]]; then
  NO_OWNER=true
fi
EXPECTED_OWNER='com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver'
if [[ "$VERIFY_LOCK" == "--verify-lock-task" ]]; then
  [[ "$NO_OWNER" == false ]] || { echo 'Lock Task verification requires this app to already be Device Owner.' >&2; exit 1; }
  [[ "$OWNER" == *"$EXPECTED_OWNER"* ]] || { echo "Expected Device Owner $EXPECTED_OWNER was not found." >&2; exit 1; }
  "$ADB" -s "$SERIAL" shell pm path 'com.eliterf.kiosk' | grep -q 'package:' || { echo 'Installed package com.eliterf.kiosk was not found.' >&2; exit 1; }
  "$ADB" -s "$SERIAL" shell am start -n 'com.eliterf.kiosk/.MainActivity' >/dev/null
  sleep 2
  "$ADB" -s "$SERIAL" shell dumpsys activity | grep -Eq 'LOCK_TASK_MODE_LOCKED|mLockTaskModeState=(LOCKED|1)' || { echo 'The app did not report true Lock Task Mode.' >&2; exit 1; }
  "$ADB" -s "$SERIAL" shell dumpsys activity activities | grep -Eq '^[[:space:]]*(topResumedActivity|mResumedActivity|ResumedActivity)[[:space:]]*[:=].*com\.eliterf\.kiosk/\.MainActivity' || { echo 'Elite RF MainActivity is not the resumed foreground activity.' >&2; exit 1; }
  echo 'Existing Device Owner and true Lock Task Mode verified.'
  exit 0
fi
[[ "$NO_OWNER" == true ]] || { echo 'A Device Owner already exists; use --verify-lock-task for verification or have IT approve the update path.' >&2; exit 1; }
"$ADB" -s "$SERIAL" install -r "$APK_PATH"
"$ADB" -s "$SERIAL" shell pm path 'com.eliterf.kiosk' | grep -q 'package:' || { echo 'Installed package com.eliterf.kiosk was not found.' >&2; exit 1; }
"$ADB" -s "$SERIAL" shell dpm set-device-owner 'com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver'
"$ADB" -s "$SERIAL" shell am start -n 'com.eliterf.kiosk/.MainActivity' >/dev/null
echo 'Provisioning completed. Launch the app and complete its one-time setup.'
