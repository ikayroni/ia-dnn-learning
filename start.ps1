# Sobe a API com venv na porta 8002 (padrão).
$ErrorActionPreference = "SilentlyContinue"
Set-Location $PSScriptRoot

Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -match 'llm_geraQuestion.*run\.py|uvicorn.*app\.main' } |
    ForEach-Object { taskkill /F /PID $_.ProcessId 2>$null }

foreach ($port in 8000, 8001, 8002) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { taskkill /F /PID $_.OwningProcess 2>$null }
}
Start-Sleep -Seconds 2

$port = 8002
$env:PORT = "$port"
Write-Host "Iniciando em http://localhost:$port  (confira GET /api/build)"
& "$PSScriptRoot\.venv\Scripts\python.exe" run.py
