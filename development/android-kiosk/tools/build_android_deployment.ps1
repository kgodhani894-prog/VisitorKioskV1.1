param(
    [string]$ApkPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'app\build\outputs\apk\release\app-release.apk'),
    [string]$OutputPath = (Join-Path (Split-Path $PSScriptRoot -Parent) 'Elite-RF-Android-Kiosk-Deployment.zip')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$resolvedApk = Resolve-Path -LiteralPath $ApkPath -ErrorAction Stop
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("elite-rf-android-deployment-" + [guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $stagingRoot 'Elite-RF-Android-Kiosk'

try {
    New-Item -ItemType Directory -Path $packageRoot | Out-Null
    Copy-Item -LiteralPath $resolvedApk.Path -Destination (Join-Path $packageRoot 'Elite-RF-Visitor-Kiosk.apk')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'tools\provision_android.ps1') -Destination (Join-Path $packageRoot 'INSTALL_ELITE_RF_KIOSK.ps1')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'tools\provision_android.sh') -Destination (Join-Path $packageRoot 'install_elite_rf_kiosk.sh')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'DEPLOYMENT_README.txt') -Destination $packageRoot
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\PROVISIONING_ANDROID14.md') -Destination $packageRoot
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\ACCEPTANCE_TEST_ANDROID14.md') -Destination $packageRoot
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\DECOMMISSION_ANDROID14.md') -Destination $packageRoot

    $installer = Join-Path $packageRoot 'INSTALL_ELITE_RF_KIOSK.ps1'
    $installerText = Get-Content -Raw -LiteralPath $installer
    $installerText = $installerText -replace 'param\(\[Parameter\(Mandatory=\$true\)\]\[string\]\$ApkPath,', 'param([string]$ApkPath = (Join-Path $PSScriptRoot ''Elite-RF-Visitor-Kiosk.apk''),'
    Set-Content -LiteralPath $installer -Value $installerText -Encoding UTF8

    $outputDirectory = Split-Path $resolvedOutput -Parent
    if (-not (Test-Path -LiteralPath $outputDirectory)) {
        New-Item -ItemType Directory -Path $outputDirectory | Out-Null
    }
    $temporaryZip = Join-Path $stagingRoot 'deployment.zip'
    Compress-Archive -LiteralPath $packageRoot -DestinationPath $temporaryZip -CompressionLevel Optimal
    Move-Item -LiteralPath $temporaryZip -Destination $resolvedOutput -Force
    Write-Host "Created: $resolvedOutput"
} finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
