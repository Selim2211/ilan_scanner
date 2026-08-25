# Ajani Windows oturum acilisina baglar: bilgisayar acildiginda tarama kendiliginden basler.
#
#   powershell -ExecutionPolicy Bypass -File .\install_agent.ps1          # kur
#   powershell -ExecutionPolicy Bypass -File .\install_agent.ps1 -Remove  # kaldir
#
# Gorev penceresiz calisir; panel http://127.0.0.1:8000 adresinde durur.
# Durumu gormek icin:  schtasks /Query /TN "SAP Proje Radari Ajan" /V /FO LIST

param(
    [string]$TaskName = "SAP Proje Radari Ajan",
    [int]$Port = 8000,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($Remove) {
    schtasks /Delete /TN $TaskName /F
    "Gorev kaldirildi: $TaskName"
    return
}

$script = Join-Path $PSScriptRoot "run_agent.ps1"
if (-not (Test-Path $script)) { throw "run_agent.ps1 bulunamadi: $script" }

# -WindowStyle Hidden: konsol penceresi acilmaz, ajan arka planda kalir
$action  = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Port $Port"

schtasks /Create /TN $TaskName /TR $action /SC ONLOGON /RL LIMITED /F
if ($LASTEXITCODE -ne 0) { throw "Gorev olusturulamadi (cikis $LASTEXITCODE)" }

"Kuruldu: $TaskName"
"Simdi baslatmak icin:  schtasks /Run /TN `"$TaskName`""
"Durdurmak icin      :  schtasks /End /TN `"$TaskName`""
"Kaldirmak icin      :  .\install_agent.ps1 -Remove"
