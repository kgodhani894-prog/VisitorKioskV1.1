function Test-KioskInterpreterPath([string]$Path, [string[]]$AllowedNames) {
  try {
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    if ([IO.Path]::GetExtension($resolved) -ine '.exe' -or [IO.Path]::GetFileName($resolved) -notin $AllowedNames) {
      return $null
    }
    if ($resolved -match '(?i)(^|[\\/])\.codex([\\/]|$)') {
      return $null
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $resolved
    if ($signature.Status.ToString() -ne 'Valid') {
      return $null
    }
    return $resolved
  } catch {
    return $null
  }
}

function Resolve-KioskCandidate([hashtable]$Candidate) {
  if ($Candidate.File -in @('py', 'python')) {
    $commands = @(Get-Command -Name $Candidate.File -CommandType Application -All -ErrorAction SilentlyContinue)
    foreach ($command in $commands) {
      $path = Test-KioskInterpreterPath $command.Source @($Candidate.File + '.exe')
      if ($null -ne $path) {
        return $path
      }
    }
    return $null
  }
  return Test-KioskInterpreterPath $Candidate.File @('python.exe')
}

function Get-KioskInterpreter([hashtable]$Candidate) {
  try {
    $candidatePath = Resolve-KioskCandidate $Candidate
    if ($null -eq $candidatePath) {
      return $null
    }
    $candidateArgs = @($Candidate.Args)
    $output = @(& $candidatePath @candidateArgs -c 'import sys; print(sys.executable); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>$null)
    if ($LASTEXITCODE -ne 0 -or $output.Count -eq 0) {
      return $null
    }
    $reportedPath = ([string]($output | Select-Object -Last 1)).Trim()
    if (-not $reportedPath) {
      return $null
    }
    $resolved = Test-KioskInterpreterPath $reportedPath @('python.exe')
    if ($null -eq $resolved) {
      return $null
    }
    return @{ File = $resolved; Args = @() }
  } catch {
    return $null
  }
}

function Import-KioskEnvironment([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }
  foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
      continue
    }
    $parts = $trimmed.Split('=', 2)
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if ($name -and [string]::IsNullOrEmpty([System.Environment]::GetEnvironmentVariable($name))) {
      Set-Item -LiteralPath "Env:$name" -Value $value
    }
  }
}

function Test-KioskIpv4Literal([string]$Value) {
  if (-not $Value) { return $false }
  $address = $null
  return [System.Net.IPAddress]::TryParse($Value, [ref]$address) -and
    $address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork
}

function Get-KioskLanAddress {
  $socket = $null
  try {
    # Connecting a UDP socket selects the active route without sending traffic.
    $socket = New-Object System.Net.Sockets.Socket(
      [System.Net.Sockets.AddressFamily]::InterNetwork,
      [System.Net.Sockets.SocketType]::Dgram,
      [System.Net.Sockets.ProtocolType]::Udp
    )
    $socket.Connect('8.8.8.8', 65530)
    $address = $socket.LocalEndPoint.Address.IPAddressToString
    if ($address -and $address -notlike '127.*' -and $address -notlike '169.254.*') {
      return $address
    }
  } catch {
  } finally {
    if ($null -ne $socket) { $socket.Dispose() }
  }

  try {
    $address = [System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
      Where-Object {
        $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
        $_.IPAddressToString -notlike '127.*' -and
        $_.IPAddressToString -notlike '169.254.*'
      } |
      Select-Object -First 1
    if ($null -ne $address) { return $address.IPAddressToString }
  } catch {
  }
  return '127.0.0.1'
}

function Resolve-KioskDisplayHost(
  [string]$ConfiguredHost,
  [string]$LanAddress,
  [bool]$TestMode,
  [bool]$UseTls,
  [string]$StableLocalHostname
) {
  if ($TestMode) { return '127.0.0.1' }
  $detectedHost = if ($LanAddress) { $LanAddress } else { '127.0.0.1' }
  if ($ConfiguredHost -and -not (Test-KioskIpv4Literal $ConfiguredHost)) {
    return $ConfiguredHost
  }

  # HTTPS certificates identify hostnames, not whichever numeric address DHCP
  # assigned today. Prefer the stable local computer name for home/LAN TLS.
  if ($UseTls -and $StableLocalHostname) {
    return $StableLocalHostname
  }
  return $detectedHost
}

function Find-KioskPython([string]$Root) {
  $candidates = @(
    @{ File = (Join-Path $Root '.runtime\python.exe'); Args = @() },
    @{ File = (Join-Path $Root '.venv\Scripts\python.exe'); Args = @() },
    @{ File = 'py'; Args = @() },
    @{ File = 'python'; Args = @() }
  )

  # Support standard Python installer locations when PATH was not enabled,
  # without embedding a developer-specific path or scanning unrelated folders.
  $installedPythonPaths = @()
  if ($env:LocalAppData) {
    $pythonRoot = Join-Path $env:LocalAppData 'Programs\Python'
    $installedPythonPaths += Get-ChildItem -LiteralPath $pythonRoot -Directory -Filter 'Python*' -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'python.exe' }
  }
  foreach ($programFiles in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($programFiles) {
      $installedPythonPaths += Get-ChildItem -LiteralPath $programFiles -Directory -Filter 'Python*' -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName 'python.exe' }
    }
  }
  foreach ($path in ($installedPythonPaths | Sort-Object -Unique)) {
    $candidates += @{ File = $path; Args = @() }
  }

  foreach ($candidate in $candidates) {
    $interpreter = Get-KioskInterpreter $candidate
    if ($null -ne $interpreter) { return $interpreter }
  }
  return $null
}
