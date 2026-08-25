# SAP Proje Radari - surekli calisan ajan.
#
# Panelden bagimsiz calisir: tarayici kapaliyken de veri akmaya devam eder.
# Panel de acilir (http://127.0.0.1:8000) ama taramayi panel degil bu surec yurutur.
#
# Elle baslatmak:
#   powershell -ExecutionPolicy Bypass -File .\run_agent.ps1
# Windows acilisinda otomatik baslatmak icin:
#   .\install_agent.ps1

param(
    [int]$Port = 8000,
    [switch]$NoPanel          # sadece tarama dongusu, web paneli acma
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path $PSScriptRoot "output\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("agent_" + (Get-Date -Format "yyyyMMdd") + ".log")

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ajan basliyor ===" | Out-File -Append -Encoding utf8 $log

if ($NoPanel) {
    py -m scanner watch *>&1 | Tee-Object -Append -FilePath $log
} else {
    py -m scanner serve --port $Port *>&1 | Tee-Object -Append -FilePath $log
}
