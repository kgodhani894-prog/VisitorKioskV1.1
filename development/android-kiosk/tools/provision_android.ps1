param([Parameter(Mandatory=$true)][string]$ApkPath, [switch]$VerifyLockTask)
$ErrorActionPreference = 'Stop'

function Find-Adb {
    $candidates = @(
        (Join-Path $PSScriptRoot 'platform-tools\adb.exe'),
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'platform-tools\adb.exe'),
        $(if ($env:ANDROID_HOME) { Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe' }),
        $(if ($env:ANDROID_SDK_ROOT) { Join-Path $env:ANDROID_SDK_ROOT 'platform-tools\adb.exe' }),
        $(if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe' })
    ) | Where-Object { $_ }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $command = Get-Command adb -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw 'Android Platform Tools (adb) are required. Install them or place the official platform-tools folder beside this script.'
}

$adb = Find-Adb
if (-not (Test-Path -LiteralPath $ApkPath -PathType Leaf)) { throw "APK not found: $ApkPath" }
$devices = @(& $adb devices | Select-String "`tdevice$")
if ($devices.Count -ne 1) { throw "Exactly one authorized Android device is required; found $($devices.Count)." }
$serial = ($devices[0].Line -split "`t")[0]
$api = (& $adb -s $serial shell getprop ro.build.version.sdk).Trim()
$version = (& $adb -s $serial shell getprop ro.build.version.release).Trim()
Write-Host "Device: $serial"
Write-Host "Android: $version (API $api)"
if ([int]$api -ne 34) { throw 'This release requires Android 14/API 34.' }
$owner = (& $adb -s $serial shell dpm list-owners 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $owner -match 'Unknown command') {
    $owner = (& $adb -s $serial shell dpm get-device-owner 2>&1 | Out-String)
}
$noOwner = $owner -match '(?im)^\s*no owners\s*$|No device owner'
$expectedOwner = 'com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver'
if ($VerifyLockTask) {
    if ($noOwner) { throw 'Lock Task verification requires this app to already be Device Owner.' }
    if ($owner -notmatch [regex]::Escape($expectedOwner)) { throw "Expected Device Owner $expectedOwner was not found." }
    $installed = & $adb -s $serial shell pm path 'com.eliterf.kiosk' 2>&1 | Out-String
    if ($installed -notmatch 'package:') { throw 'Installed APK package com.eliterf.kiosk was not found.' }
    & $adb -s $serial shell am start -n 'com.eliterf.kiosk/.MainActivity' | Out-Null
    Start-Sleep -Seconds 2
    $lockState = & $adb -s $serial shell dumpsys activity 2>&1 | Out-String
    if ($lockState -notmatch 'LOCK_TASK_MODE_LOCKED|mLockTaskModeState=(LOCKED|1)') { throw 'The app did not report true Lock Task Mode.' }
    $foregroundLine = & $adb -s $serial shell dumpsys activity activities 2>&1 | Select-String '^\s*(topResumedActivity|mResumedActivity|ResumedActivity)\s*[:=].*com\.eliterf\.kiosk/\.MainActivity'
    if (-not $foregroundLine) { throw 'Elite RF MainActivity is not the resumed foreground activity.' }
    Write-Host 'Existing Device Owner and true Lock Task Mode verified.'
    exit 0
}
if (-not $noOwner) { throw 'A Device Owner already exists; use -VerifyLockTask for verification or have IT approve the update path.' }
& $adb -s $serial install -r $ApkPath
if ($LASTEXITCODE -ne 0) { throw 'APK installation failed.' }
$installed = & $adb -s $serial shell pm path 'com.eliterf.kiosk' 2>&1 | Out-String
if ($installed -notmatch 'package:') { throw 'Installed APK package com.eliterf.kiosk was not found.' }
& $adb -s $serial shell dpm set-device-owner 'com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver'
if ($LASTEXITCODE -ne 0) { throw 'Device Owner assignment failed. No destructive recovery was attempted.' }
& $adb -s $serial shell am start -n 'com.eliterf.kiosk/.MainActivity' | Out-Null
Write-Host 'Provisioning completed. Launch the app and complete its one-time setup.'
