$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$projectRoot = if ((Split-Path -Leaf $root) -ieq 'app') { Split-Path -Parent $root } else { $root }
Set-Location $root
. (Join-Path $PSScriptRoot 'python_runtime.ps1')

Write-Host 'Elite RF Visitor Kiosk diagnostic'
Write-Host "Project: $root"
$python = Find-KioskPython $root
if ($null -eq $python) {
  Write-Host 'Python 3.11 or newer was not found.' -ForegroundColor Red
  exit 1
}
Write-Host 'Python 3.11+: available'

foreach ($file in @('run.py','config.json','static\kiosk.html','static\admin.html','kiosk_server\server.py')) {
  if (-not (Test-Path -LiteralPath (Join-Path $root $file))) { throw "Required file is missing: $file" }
}
Write-Host 'Required files: present'
Write-Host 'Port 8080 available:' (-not [bool](Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue))

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('elite-rf-kiosk-diagnostic-' + [guid]::NewGuid().ToString())
$probeRoot = Join-Path ([IO.Path]::GetTempPath()) ('elite-rf-kiosk-diagnostic-probe-' + [guid]::NewGuid().ToString())
$probeDirs = @('data', 'storage\photos', 'backups')
try {
  New-Item -ItemType Directory -Force -Path $probeRoot | Out-Null
  foreach ($dir in $probeDirs) {
    $path = Join-Path $probeRoot $dir
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    $probe = Join-Path $path '.write-test'
    [IO.File]::WriteAllText($probe, 'ok')
    Remove-Item -LiteralPath $probe -Force
  }
  Write-Host 'Disposable runtime directories: writable'
  Write-Host '.env exists:' (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))
  Write-Host 'No .env contents or secret values were read.'

  $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
  $listener.Start()
  $port = $listener.LocalEndpoint.Port
  $listener.Stop()
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $python.File
  $arguments = @($python.Args) + @('run.py', '--test-mode', '--runtime-root', $tempRoot, '--host', '127.0.0.1', '--port', $port)
  $psi.Arguments = (($arguments | ForEach-Object { '"' + $_.ToString().Replace('"', '\"') + '"' }) -join ' ')
  $psi.WorkingDirectory = $root
  $psi.UseShellExecute = $false
  $process = [System.Diagnostics.Process]::Start($psi)
  try {
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
      Start-Sleep -Milliseconds 250
      try {
        $identity = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/system/identity" -TimeoutSec 1
        if ($identity.service -eq 'elite-rf-visitor-kiosk' -and (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/admin" -TimeoutSec 1).StatusCode -eq 200 -and (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/kiosk" -TimeoutSec 1).StatusCode -eq 200) { $ready = $true; break }
      } catch { }
      if ($process.HasExited) { throw 'Test server stopped during diagnostic startup.' }
    }
    if (-not $ready) { throw 'Test server did not pass the admin/kiosk HTTP smoke check.' }
    Write-Host 'Backend identity, /admin, and /kiosk HTTP checks: passed'
  } finally {
    if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
    $process.Dispose()
  }
} finally {
  Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
