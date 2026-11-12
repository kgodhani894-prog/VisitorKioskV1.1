param([Parameter(Mandatory=$true)][string]$BackupArchive, [Parameter(Mandatory=$true)][string]$RestoreRoot)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $BackupArchive -PathType Leaf)) { throw 'Backup archive was not found.' }
$archivePath = (Resolve-Path -LiteralPath $BackupArchive).Path
$restorePath = [System.IO.Path]::GetFullPath($RestoreRoot)
if (Test-Path -LiteralPath $restorePath) { throw 'Choose a new, empty restore folder; existing files will not be overwritten.' }
$python = Get-Command python -ErrorAction Stop
$appRoot = Split-Path -Parent $PSScriptRoot
$oldPythonPath = $env:PYTHONPATH
$createdRestoreRoot = $false
try {
    $env:PYTHONPATH = $appRoot
    & $python.Source -c 'import sys; from pathlib import Path; from kiosk_server.backups import verify_backup; verify_backup(Path(sys.argv[1]))' $archivePath
    if ($LASTEXITCODE -ne 0) { throw 'Backup verification failed; nothing was extracted.' }
    New-Item -ItemType Directory -Path $restorePath | Out-Null
    $createdRestoreRoot = $true
    Expand-Archive -LiteralPath $archivePath -DestinationPath $restorePath -ErrorAction Stop
} catch {
    if ($createdRestoreRoot -and (Test-Path -LiteralPath $restorePath)) {
        Remove-Item -LiteralPath $restorePath -Recurse -Force -ErrorAction SilentlyContinue
    }
    throw
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
Write-Host "Backup extracted to $RestoreRoot. Review it before replacing production data."
