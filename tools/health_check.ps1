param([int]$Port = 8080)
$ErrorActionPreference = 'Stop'
try { $r = Invoke-WebRequest -Uri "https://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 5; Write-Host "Health check passed: $($r.StatusCode)"; exit 0 }
catch { Write-Error 'Health check failed. Confirm the production HTTPS URL and certificate.'; exit 1 }
