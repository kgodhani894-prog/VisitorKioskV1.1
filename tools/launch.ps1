param([ValidateSet('Production','Test')][string]$Mode = 'Production')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$projectRoot = if ((Split-Path -Leaf $root) -ieq 'app') { Split-Path -Parent $root } else { $root }
Set-Location $root
. (Join-Path $PSScriptRoot 'python_runtime.ps1')
$test = $Mode -eq 'Test'
$hostName = if ($test) { '127.0.0.1' } else { '0.0.0.0' }
$port = 8080

if (-not $test) {
  Import-KioskEnvironment (Join-Path $projectRoot '.env')
}

$tlsCert = if ($test) { $null } else { $env:KIOSK_TLS_CERT_PATH }
$tlsKey = if ($test) { $null } else { $env:KIOSK_TLS_KEY_PATH }
$publicHostname = if ($test) { $null } else { $env:KIOSK_PUBLIC_HOSTNAME }
if (-not $test -and [bool]$tlsCert -ne [bool]$tlsKey) {
  Write-Host 'HTTPS setup is incomplete. Both the certificate and private key must be configured.' -ForegroundColor Red
  Read-Host 'Press Enter to close'
  exit 1
}
if (-not $test -and $tlsCert -and (-not (Test-Path -LiteralPath $tlsCert) -or -not (Test-Path -LiteralPath $tlsKey))) {
  Write-Host 'The configured HTTPS certificate or private key file is missing.' -ForegroundColor Red
  Read-Host 'Press Enter to close'
  exit 1
}
$scheme = if ($tlsCert -and $tlsKey) { 'https' } else { 'http' }
$lanAddress = if ($test) { '127.0.0.1' } else { Get-KioskLanAddress }
$stableLocalHostname = if ($env:COMPUTERNAME) { "$($env:COMPUTERNAME).local" } else { $lanAddress }
$displayHost = Resolve-KioskDisplayHost $publicHostname $lanAddress $test ($scheme -eq 'https') $stableLocalHostname
if (-not $test) {
  # Keep run.py's printed URLs consistent with the host selected by the launcher.
  $env:KIOSK_PUBLIC_HOSTNAME = $displayHost
}
if ($test) {
  $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
  $probe.Start()
  $port = $probe.LocalEndpoint.Port
  $probe.Stop()
}

function Open-Browser([string]$url) {
  try {
    $browserInfo = New-Object System.Diagnostics.ProcessStartInfo
    $browserInfo.FileName = $url
    $browserInfo.UseShellExecute = $true
    [System.Diagnostics.Process]::Start($browserInfo) | Out-Null
  } catch {
    Write-Host "Open this URL manually: $url" -ForegroundColor Yellow
  }
}

function Test-EliteRfService([string]$baseUrl) {
  try {
    $identity = Invoke-RestMethod -Uri "$baseUrl/api/system/identity" -TimeoutSec 2
    return $identity.service -eq 'elite-rf-visitor-kiosk' -and $identity.status -eq 'ok'
  } catch {
    return $false
  }
}

Write-Host ''
if ($test) { Write-Host 'TEST MODE - NO REAL EMAIL - DISPOSABLE DATA' -ForegroundColor Yellow }
Write-Host 'Checking this computer...'
Write-Host "Computer: $env:COMPUTERNAME"
if (-not $test) { Write-Host "Network address: $lanAddress" }
Write-Host "Opening address: ${scheme}://${displayHost}:$port/admin"
Write-Host 'Starting Elite RF Visitor Kiosk...'
Write-Host 'Keep this window open while the system is running.'
$python = Find-KioskPython $root
if ($null -eq $python) {
  Write-Host 'Python 3.11 or newer was not found.' -ForegroundColor Red
  Write-Host 'Install it from https://www.python.org/downloads/windows/'
  Write-Host 'During installation, select Add Python to PATH, then rerun this launcher.'
  Read-Host 'Press Enter to close'
  exit 1
}
Write-Host 'Python runtime: ready'
$httpsStatus = if ($scheme -eq 'https') { 'HTTPS: configured' } else { 'HTTPS: not configured' }
Write-Host $httpsStatus
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
  $existingBaseUrl = "${scheme}://${displayHost}:$port"
  $existingAdminUrl = "${scheme}://${displayHost}:$port/admin"
  if (-not (Test-EliteRfService $existingBaseUrl)) {
    Write-Host "Port $port is being used by another program, not Elite RF." -ForegroundColor Red
    Read-Host 'Close the other program, then press Enter'
    exit 1
  }
  Write-Host "Elite RF is already running on port $port." -ForegroundColor Yellow
  Write-Host "Opening the existing admin dashboard: $existingAdminUrl"
  Open-Browser $existingAdminUrl
  Start-Sleep -Seconds 2
  exit 0
}
$arguments = @('run.py', '--host', $hostName, '--port', $port)
if ($test) { $arguments += '--test-mode' }
$hasTls = -not $test -and $tlsCert -and $tlsKey
if ($hasTls) {
  $arguments += @('--tls-cert', $tlsCert, '--tls-key', $tlsKey)
  if (-not $publicHostname) {
    Write-Host 'HTTPS is active. Set KIOSK_PUBLIC_HOSTNAME to the certificate hostname for the clearest browser and readiness URLs.' -ForegroundColor Yellow
  }
}
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $python.File
$allArguments = @($python.Args) + $arguments
$psi.Arguments = (($allArguments | ForEach-Object { '"' + $_.ToString().Replace('"', '\"') + '"' }) -join ' ')
$psi.WorkingDirectory = $root
$psi.UseShellExecute = $false
$process = [System.Diagnostics.Process]::Start($psi)

try {
  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 250
    $client = $null
    try {
      $client = New-Object System.Net.Sockets.TcpClient
      $client.Connect('127.0.0.1', $port)
      # This is the process we just launched, so a live local listener is the
      # readiness signal. The public hostname may be temporarily stale while a
      # laptop changes networks and must not cause a healthy server to be killed.
      if ($client.Connected) { $ready = $true; break }
    } catch {
    } finally {
      if ($null -ne $client) { $client.Dispose() }
    }
    if ($process.HasExited) { throw "The server stopped during startup (exit code $($process.ExitCode))." }
  }
  if (-not $ready) { throw "The server did not become ready on port $port." }
  Write-Host 'Server is ready.'
  Write-Host "Admin:  ${scheme}://${displayHost}:$port/admin"
  Write-Host "Kiosk:  ${scheme}://${displayHost}:$port/kiosk"
  if (-not $test -and -not $hasTls) { Write-Host 'Warning: remote camera capture on the LAN kiosk requires trusted HTTPS.' -ForegroundColor Yellow }
  Open-Browser "${scheme}://${displayHost}:$port/admin"
  $process.WaitForExit()
} catch {
  Write-Host $_.Exception.Message -ForegroundColor Red
  if (-not $process.HasExited) { $process.Kill() }
  Read-Host 'Press Enter to close'
  exit 1
} finally {
  if (-not $process.HasExited) { $process.Kill() }
  $process.Dispose()
}
