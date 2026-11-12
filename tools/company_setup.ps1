param(
  [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot),
  [switch]$CheckOnly,
  [switch]$SkipDiagnostic,
  [switch]$InstallSystemIntegration
)

$ErrorActionPreference = 'Stop'
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$DeploymentRoot = if ((Split-Path -Leaf $ProjectPath) -ieq 'app') { Split-Path -Parent $ProjectPath } else { $ProjectPath }
$EnvironmentPath = Join-Path $DeploymentRoot '.env'
$EnvironmentExample = Join-Path $DeploymentRoot '.env.example'
. (Join-Path $PSScriptRoot 'python_runtime.ps1')
. (Join-Path $PSScriptRoot 'windows_security.ps1')

function Write-SetupCheck([string]$Name, [bool]$Passed, [string]$Detail) {
  $label = if ($Passed) { 'READY' } else { 'NEEDS ATTENTION' }
  $color = if ($Passed) { 'Green' } else { 'Yellow' }
  Write-Host ("[{0}] {1}: {2}" -f $label, $Name, $Detail) -ForegroundColor $color
}

function Get-EnvironmentMap([string]$Path) {
  $values = @{}
  if (-not (Test-Path -LiteralPath $Path)) { return $values }
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
    $parts = $trimmed.Split('=', 2)
    $values[$parts[0].Trim()] = $parts[1].Trim()
  }
  return $values
}

function Set-EnvironmentValue([string]$Path, [string]$Name, [string]$Value) {
  $lines = [Collections.Generic.List[string]]::new()
  $updated = $false
  if (Test-Path -LiteralPath $Path) {
    foreach ($line in Get-Content -LiteralPath $Path) {
      if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=')) {
        $lines.Add("$Name=$Value")
        $updated = $true
      } else {
        $lines.Add($line)
      }
    }
  }
  if (-not $updated) { $lines.Add("$Name=$Value") }
  $temporary = "$Path.tmp"
  [IO.File]::WriteAllLines($temporary, $lines, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function New-SessionSecret {
  $bytes = New-Object byte[] 48
  $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
  return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Test-OwnerConfigured([hashtable]$Values, [hashtable]$Python) {
  if ($Values['KIOSK_ADMIN_PASSWORD_HASH']) { return $true }
  $config = Join-Path $ProjectPath 'config.json'
  if (-not (Test-Path -LiteralPath $config)) { return $false }
  $code = "import json,sys; from kiosk_server.security import verify_password; c=json.load(open(sys.argv[1],encoding='utf-8')); raise SystemExit(1 if verify_password('change-me-before-use',c.get('admin_password_hash','')) else 0)"
  & $Python.File @($Python.Args) -c $code $config 2>$null
  return $LASTEXITCODE -eq 0
}

function Test-IsAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host ''
Write-Host 'Elite RF Company Setup and Readiness Check' -ForegroundColor Cyan
Write-Host 'No secret values will be displayed.'
Write-Host "Installation folder: $DeploymentRoot"
Write-Host ''

$python = Find-KioskPython $ProjectPath
if ($null -eq $python) {
  Write-SetupCheck 'Python 3.11+' $false 'Install official Python and select Add Python to PATH.'
  Write-Host 'Official installer: https://www.python.org/downloads/windows/'
  if (-not $CheckOnly) {
    try { Start-Process 'https://www.python.org/downloads/windows/' } catch { }
  }
  exit 1
}
Write-SetupCheck 'Python 3.11+' $true 'A trusted supported interpreter was found.'
$pythonForAllUsers = -not $env:LOCALAPPDATA -or $python.File -notmatch ('^' + [regex]::Escape($env:LOCALAPPDATA))
Write-SetupCheck 'System-wide Python' $pythonForAllUsers $(if ($pythonForAllUsers) { 'Python is available before a user signs in.' } else { 'Reinstall official Python using Install for all users before final company setup.' })

$requirementsPath = Join-Path $ProjectPath 'requirements.txt'
$requirementsCode = 'import cryptography, msal, reportlab'
try { & $python.File @($python.Args) -c $requirementsCode 2>$null } catch { }
$requirementsReady = $LASTEXITCODE -eq 0
if (-not $requirementsReady -and -not $CheckOnly -and (Test-Path -LiteralPath $requirementsPath)) {
  Write-Host 'One or more application dependencies are missing. Installing the pinned requirements...' -ForegroundColor Yellow
  & $python.File @($python.Args) -m pip install --requirement $requirementsPath
  $requirementsReady = $LASTEXITCODE -eq 0
}
Write-SetupCheck 'Application requirements' ([bool]$requirementsReady) $(if ($requirementsReady) { 'Microsoft Graph email and PDF export dependencies are installed.' } else { 'Install the package requirements before final setup.' })
if (-not $requirementsReady -and -not $CheckOnly) { exit 1 }

$temporaryLocation = $DeploymentRoot -match '(?i)[\\/](Downloads|Temp|Temporary Internet Files)([\\/]|$)'
Write-SetupCheck 'Permanent installation folder' (-not $temporaryLocation) $(if ($temporaryLocation) { 'Move the extracted folder to a permanent company-approved location.' } else { 'Folder location is suitable for a permanent installation.' })

$requiredFiles = @('run.py', 'config.json', 'kiosk_server\server.py', 'static\kiosk.html', 'tools\diagnose.ps1', 'tools\install_startup_tasks.ps1')
$filesReady = -not ($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $ProjectPath $_)) })
Write-SetupCheck 'Application files' $filesReady $(if ($filesReady) { 'Required backend files are present.' } else { 'The deployment is incomplete; extract a fresh company package.' })
if (-not $filesReady) { exit 1 }

if (-not (Test-Path -LiteralPath $EnvironmentPath)) {
  Write-SetupCheck 'Private company settings' $false '.env has not been created yet.'
  if ($CheckOnly) { exit 1 }
  if (-not (Test-Path -LiteralPath $EnvironmentExample)) { throw '.env.example is missing.' }
  Copy-Item -LiteralPath $EnvironmentExample -Destination $EnvironmentPath
  Set-EnvironmentValue $EnvironmentPath 'KIOSK_SESSION_SECRET' (New-SessionSecret)
  Write-Host 'Created .env and generated a private random session secret.' -ForegroundColor Green
  Write-Host 'Enter the Microsoft 365, HTTPS, hostname, and protected backup settings in Notepad.'
  Start-Process notepad.exe -ArgumentList @($EnvironmentPath) -Wait
}

$values = Get-EnvironmentMap $EnvironmentPath
$sessionReady = $values['KIOSK_SESSION_SECRET'] -and $values['KIOSK_SESSION_SECRET'].Length -ge 32 -and $values['KIOSK_SESSION_SECRET'] -ne 'replace-with-a-long-random-secret'
$m365KeyPath = if ($values['M365_PRIVATE_KEY_PATH']) { $values['M365_PRIVATE_KEY_PATH'] } else { $values['M365_CERTIFICATE_PATH'] }
$m365Ready = $values['M365_TENANT_ID'] -and $values['M365_CLIENT_ID'] -and $values['M365_SENDER_ADDRESS'] -and $values['M365_CERTIFICATE_PATH'] -and $values['M365_CERTIFICATE_THUMBPRINT'] -and (Test-Path -LiteralPath $values['M365_CERTIFICATE_PATH']) -and (Test-Path -LiteralPath $m365KeyPath)
$tlsValuesReady = $values['KIOSK_TLS_CERT_PATH'] -and $values['KIOSK_TLS_KEY_PATH'] -and $values['KIOSK_PUBLIC_HOSTNAME']
$tlsFilesReady = $tlsValuesReady -and (Test-Path -LiteralPath $values['KIOSK_TLS_CERT_PATH']) -and (Test-Path -LiteralPath $values['KIOSK_TLS_KEY_PATH'])
$backupReady = $values['KIOSK_BACKUP_COPY_PATH'] -and (Test-Path -LiteralPath $values['KIOSK_BACKUP_COPY_PATH'])

if ($tlsFilesReady) {
  $tlsCode = "import ssl,sys; c=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); c.load_cert_chain(sys.argv[1],sys.argv[2])"
  & $python.File @($python.Args) -c $tlsCode $values['KIOSK_TLS_CERT_PATH'] $values['KIOSK_TLS_KEY_PATH'] 2>$null
  $tlsFilesReady = $LASTEXITCODE -eq 0
}

Write-SetupCheck 'Session security' ([bool]$sessionReady) $(if ($sessionReady) { 'A private session secret is configured.' } else { 'Generate a long random KIOSK_SESSION_SECRET.' })
Write-SetupCheck 'Microsoft 365 email settings' ([bool]$m365Ready) $(if ($m365Ready) { 'Microsoft 365 application email settings are present.' } else { 'Add the tenant, client, sender, certificate, and certificate thumbprint settings to .env.' })
Write-SetupCheck 'Trusted HTTPS' ([bool]$tlsFilesReady) $(if ($tlsFilesReady) { 'Hostname, certificate, and private key are configured.' } else { 'Add the company hostname and existing certificate/key paths.' })
Write-SetupCheck 'Protected secondary backup' ([bool]$backupReady) $(if ($backupReady) { 'The configured secondary destination exists.' } else { 'Set KIOSK_BACKUP_COPY_PATH to existing encrypted company-approved storage.' })

$ownerReady = Test-OwnerConfigured $values $python
Write-SetupCheck 'Owner password' $ownerReady $(if ($ownerReady) { 'The default owner password is no longer active.' } else { 'Complete first-run owner setup locally before installing automatic startup.' })

if (-not $SkipDiagnostic) {
  & powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File (Join-Path $PSScriptRoot 'diagnose.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'The disposable backend diagnostic failed.' }
  Write-SetupCheck 'Backend diagnostic' $true 'Disposable admin and kiosk checks passed.'
}

$startupTask = Get-ScheduledTask -TaskName 'Elite RF Visitor Kiosk' -ErrorAction SilentlyContinue
$backupTask = Get-ScheduledTask -TaskName 'Elite RF Visitor Kiosk Backup' -ErrorAction SilentlyContinue
$firewallRule = Get-NetFirewallRule -DisplayName 'Elite RF Visitor Kiosk (Company LAN)' -ErrorAction SilentlyContinue
Write-SetupCheck 'Automatic startup' ([bool]$startupTask) $(if ($startupTask) { 'The supervised startup task is installed.' } else { 'Windows startup task is not installed yet.' })
Write-SetupCheck 'Daily backup task' ([bool]$backupTask) $(if ($backupTask) { 'The verified daily backup task is installed.' } else { 'Daily backup task is not installed yet.' })
Write-SetupCheck 'Company-network firewall' ([bool]$firewallRule) $(if ($firewallRule) { 'The private/domain LAN rule is installed.' } else { 'The restricted inbound rule is not installed yet.' })

$settingsReady = $sessionReady -and $m365Ready -and $tlsFilesReady -and $backupReady -and $ownerReady -and $pythonForAllUsers -and (-not $temporaryLocation)
if ($CheckOnly) {
  if ($settingsReady -and $startupTask -and $backupTask -and $firewallRule) { exit 0 }
  exit 1
}

if (-not $settingsReady) {
  Write-Host ''
  Write-Host 'Company setup is not ready to finalize. Correct the items above, then run this file again.' -ForegroundColor Yellow
  Write-Host 'Company IT must supply the permanent DNS hostname, trusted certificate, firewall approval, Microsoft 365 app/mailbox, and protected backup destination.'
  exit 1
}

if (-not $InstallSystemIntegration) {
  $answer = Read-Host 'Install supervised startup, daily backups, and the private/domain LAN firewall rule now? [Y/N]'
  if ($answer -notmatch '^(?i)y(es)?$') {
    Write-Host 'Readiness checks finished. System integration was not changed.'
    exit 0
  }
}

if (-not (Test-IsAdministrator)) {
  Write-Host 'Windows will request Administrator approval for the one-time system setup.'
  $arguments = @('-NoProfile', '-ExecutionPolicy', 'RemoteSigned', '-File', "`"$PSCommandPath`"", '-ProjectPath', "`"$ProjectPath`"", '-SkipDiagnostic', '-InstallSystemIntegration')
  Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments -Wait
  exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot 'install_startup_tasks.ps1') -ProjectPath $ProjectPath
if (-not (Get-NetFirewallRule -DisplayName 'Elite RF Visitor Kiosk (Company LAN)' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName 'Elite RF Visitor Kiosk (Company LAN)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 -Profile Domain,Private -RemoteAddress LocalSubnet | Out-Null
}
Protect-EliteRfSensitiveFiles -ProjectPath $ProjectPath -EnvironmentPath $EnvironmentPath -TlsKeyPath $values['KIOSK_TLS_KEY_PATH']

Write-Host ''
Write-Host 'COMPANY COMPUTER SETUP COMPLETE' -ForegroundColor Green
if ((Split-Path -Leaf $ProjectPath) -ieq 'app') {
  & attrib.exe +H $ProjectPath 2>$null
}
& attrib.exe +H $EnvironmentPath 2>$null
& attrib.exe +H $EnvironmentExample 2>$null
Write-Host 'Restart Windows, verify the backend starts automatically, then complete the company pilot checklist.'
Write-Host 'Private settings and runtime data are restricted to this operator, Administrators, and Windows SYSTEM; the launchers remain visible.'
