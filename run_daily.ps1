# Gunluk aday taramasi + Excel + mail. Windows Task Scheduler bu dosyayi cagirir.
#
# Zamanlanmis gorev olusturmak icin (gunde bir kez 08:30):
#   schtasks /Create /TN "SAP Proje Radari" /TR "powershell -ExecutionPolicy Bypass -File `"$PWD\run_daily.ps1`"" /SC DAILY /ST 08:30

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path $PSScriptRoot "output\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$log = Join-Path $logDir ("run_" + (Get-Date -Format "yyyyMMdd") + ".log")

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') tarama basliyor ===" | Out-File -Append -Encoding utf8 $log

py -m scanner scan            *>&1 | Tee-Object -Append -FilePath $log
py -m scanner verify          *>&1 | Tee-Object -Append -FilePath $log   # kapanan ilanlari isaretle
py -m scanner export --xlsx   *>&1 | Tee-Object -Append -FilePath $log

# mail.enabled config/config.yaml icinde true ise gonderir
$mailEnabled = (Select-String -Path "config\config.yaml" -Pattern "^\s*enabled:\s*true" -Context 1,0 |
                Where-Object { $_.Context.PreContext -match "mail:" })
if ($mailEnabled) {
    py -m scanner mail *>&1 | Tee-Object -Append -FilePath $log
} else {
    "mail kapali (config/config.yaml -> mail.enabled)" | Out-File -Append -Encoding utf8 $log
}

"=== bitti ===" | Out-File -Append -Encoding utf8 $log
