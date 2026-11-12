$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot 'provision_android.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) ('elite-rf-provision-test-' + [guid]::NewGuid().ToString('N'))
$mockBin = Join-Path $root 'bin'
New-Item -ItemType Directory -Path $mockBin -Force | Out-Null
$apk = Join-Path $root 'test.apk'
Set-Content -LiteralPath $apk -Value 'synthetic' -Encoding Ascii

@'
@echo off
set "args=%*"
if /i "%args%" == "devices" (
  echo List of devices attached
  echo TEST123	device
  exit /b 0
)
echo %args% | findstr /c:"getprop ro.build.version.sdk" >nul && (echo 34 & exit /b 0)
echo %args% | findstr /c:"getprop ro.build.version.release" >nul && (echo 14 & exit /b 0)
echo %args% | findstr /c:"dpm list-owners" >nul && (
  if /i "%MOCK_ADB_SCENARIO%" == "wrong-owner" (echo User 0: admin=other.package/.OtherReceiver,DeviceOwner) else (echo User 0: admin=com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver,DeviceOwner)
  exit /b 0
)
echo %args% | findstr /c:"pm path" >nul && (echo package:/data/app/com.eliterf.kiosk/base.apk & exit /b 0)
echo %args% | findstr /c:"dumpsys activity activities" >nul && (
  if /i "%MOCK_ADB_SCENARIO%" == "wrong-foreground" (echo   topResumedActivity=ActivityRecord{ other.package/.OtherActivity }) else if /i "%MOCK_ADB_SCENARIO%" == "history-false-positive" (
    echo   mResumedActivity: ActivityRecord{ other.package/.OtherActivity }
    echo       ActivityRecord{ com.eliterf.kiosk/.MainActivity }
  ) else (echo   topResumedActivity=ActivityRecord{ com.eliterf.kiosk/.MainActivity })
  exit /b 0
)
echo %args% | findstr /c:"dumpsys activity" >nul && (echo mLockTaskModeState=LOCKED & exit /b 0)
exit /b 0
'@ | Set-Content -LiteralPath (Join-Path $mockBin 'adb.cmd') -Encoding Ascii

function Invoke-Scenario([string]$name, [bool]$shouldPass, [string]$expectedError = '') {
    $oldPath = $env:Path
    $oldScenario = $env:MOCK_ADB_SCENARIO
    try {
        $env:Path = "$mockBin;$oldPath"
        $env:MOCK_ADB_SCENARIO = $name
        $passed = $true
        $output = ''
        try {
            $output = & powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File $script -ApkPath $apk -VerifyLockTask 2>&1 | Out-String
            $passed = $LASTEXITCODE -eq 0
        } catch {
            $output = $_ | Out-String
            $passed = $false
        }
        if ($passed -ne $shouldPass) { throw "Scenario '$name' returned an unexpected result." }
        if (-not $shouldPass -and $output -notmatch [regex]::Escape($expectedError)) { throw "Scenario '$name' did not report the expected error." }
    } finally {
        $env:Path = $oldPath
        if ($null -eq $oldScenario) { Remove-Item Env:MOCK_ADB_SCENARIO -ErrorAction SilentlyContinue } else { $env:MOCK_ADB_SCENARIO = $oldScenario }
    }
}

try {
    Invoke-Scenario 'wrong-owner' $false 'Expected Device Owner com.eliterf.kiosk/.admin.EliteDeviceAdminReceiver was not found.'
    Invoke-Scenario 'wrong-foreground' $false 'Elite RF MainActivity is not the resumed foreground activity.'
    Invoke-Scenario 'history-false-positive' $false 'Elite RF MainActivity is not the resumed foreground activity.'
    Invoke-Scenario 'valid' $true
    Write-Host 'Controlled provisioning verification passed.'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
