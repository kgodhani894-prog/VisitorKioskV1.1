param(
  [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot)
)

# Run this once in an Administrator PowerShell window on the computer that will host the kiosk.
. (Join-Path $PSScriptRoot 'python_runtime.ps1')

function Convert-KioskArgumentList([object[]]$Arguments) {
  return ($Arguments | ForEach-Object { '"' + $_.ToString().Replace('"', '\"') + '"' }) -join ' '
}

$supervisorFile = Join-Path $ProjectPath 'tools\supervise.py'
$backupFile = Join-Path $ProjectPath 'tools\backup.py'

$python = Find-KioskPython $ProjectPath
if ($null -eq $python) {
  throw 'Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/windows/ and select Add Python to PATH, then rerun this script.'
}

$startupArguments = @($python.Args) + @($supervisorFile, '--host', '0.0.0.0', '--port', '8080')
$startupAction = New-ScheduledTaskAction -Execute $python.File -Argument (Convert-KioskArgumentList $startupArguments) -WorkingDirectory $ProjectPath
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$startupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$systemPrincipal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'Elite RF Visitor Kiosk' -Action $startupAction -Trigger $startupTrigger -Settings $startupSettings -Principal $systemPrincipal -Force | Out-Null

$backupArguments = @($python.Args) + @($backupFile)
$backupAction = New-ScheduledTaskAction -Execute $python.File -Argument (Convert-KioskArgumentList $backupArguments) -WorkingDirectory $ProjectPath
$backupTrigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
$backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'Elite RF Visitor Kiosk Backup' -Action $backupAction -Trigger $backupTrigger -Settings $backupSettings -Principal $systemPrincipal -Force | Out-Null

Write-Host 'Installed supervised startup and verified daily backup tasks.'
