$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -match 'run\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host 'Elite RF backend stop requested.'
