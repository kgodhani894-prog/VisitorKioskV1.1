function Protect-EliteRfPath([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $resolved = (Resolve-Path -LiteralPath $Path).Path
  $item = Get-Item -LiteralPath $resolved -Force
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $targets = [Collections.Generic.List[string]]::new()
  $targets.Add($resolved)
  if ($item.PSIsContainer) {
    $descendants = @(Get-ChildItem -LiteralPath $resolved -Force -Recurse -ErrorAction Stop)
    if ($descendants | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
      throw 'A protected data directory contains a reparse point; Windows permissions were not changed.'
    }
    foreach ($descendant in $descendants) { $targets.Add($descendant.FullName) }
  }
  foreach ($target in $targets) {
    $targetItem = Get-Item -LiteralPath $target -Force
    $suffix = if ($targetItem.PSIsContainer) { '(OI)(CI)F' } else { 'F' }
    & icacls.exe $target /inheritance:r /grant:r "*$currentSid`:$suffix" '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw 'Windows could not apply the required private-file permissions.'
    }
  }
}

function Protect-EliteRfSensitiveFiles(
  [string]$ProjectPath,
  [string]$EnvironmentPath,
  [string]$TlsKeyPath = ''
) {
  $paths = [Collections.Generic.List[string]]::new()
  $paths.Add($EnvironmentPath)
  $paths.Add((Join-Path $ProjectPath 'config.json'))
  foreach ($name in @('data', 'storage', 'backups')) {
    $paths.Add((Join-Path $ProjectPath $name))
  }
  if ($TlsKeyPath) { $paths.Add($TlsKeyPath) }
  foreach ($path in $paths) {
    Protect-EliteRfPath $path
  }
}
