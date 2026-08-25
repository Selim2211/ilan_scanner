# SAP Proje Radari - kurulum paketi uretir.
#
#   powershell -ExecutionPolicy Bypass -File .\build.ps1
#
# Sonuc: dist\SAPProjeRadari-Setup.exe  (patronun makinesinde calistirilacak dosya)
#
# Gereksinimler:
#   py -m pip install -r requirements.txt pyinstaller
#   Inno Setup 6  (https://jrsoftware.org/isdl.php)  - yoksa sadece klasor uretilir

param(
    [switch]$SkipKeys,      # anahtarlari gomme (test derlemesi)
    [switch]$NoInstaller    # sadece exe klasoru uret
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "== 1/4  Anahtarlar gomuluyor" -ForegroundColor Cyan
if ($SkipKeys) {
    py tools\embed_keys.py --clear
} else {
    py tools\embed_keys.py
}

Write-Host "== 2/4  Testler" -ForegroundColor Cyan
$env:PYTHONPATH = "src"
py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Testler basarisiz, derleme durduruldu" }

Write-Host "== 3/4  PyInstaller" -ForegroundColor Cyan
if (Test-Path "dist\SAP Proje Radari") { Remove-Item -Recurse -Force "dist\SAP Proje Radari" }
py -m PyInstaller packaging\radar.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller basarisiz" }

if ($NoInstaller) {
    Write-Host "`nBitti: dist\SAP Proje Radari\  (tasinabilir klasor)" -ForegroundColor Green
    return
}

Write-Host "== 4/4  Inno Setup" -ForegroundColor Cyan
$iscc = @(
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Warning "Inno Setup bulunamadi (https://jrsoftware.org/isdl.php)."
    Write-Host "Exe klasoru hazir: dist\SAP Proje Radari\" -ForegroundColor Green
    return
}

& $iscc packaging\radar.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup basarisiz" }

Write-Host "`nBitti: dist\SAPProjeRadari-Setup.exe" -ForegroundColor Green
Write-Host "Patronun makinesinde bu dosyayi calistirmasi yeterli." -ForegroundColor Green
