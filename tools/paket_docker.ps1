# SAP Proje Radari - Linux'a teslim edilecek Docker paketini uretir.
#
#   powershell -ExecutionPolicy Bypass -File .\tools\paket_docker.ps1
#
# Sonuc: dist\sap-radar-docker-<surum>.zip  - karsi tarafa gonderilecek TEK dosya.
# Icinde: imaj tar'i (anahtarlar gomulu), baslatma script'leri, KURULUM_LINUX.md
#
# Anahtarlar hakkinda: .env dosyasi derleme sirasinda .env.paket adiyla imaja
# kopyalanir (Dockerfile'daki COPY ... .env.paket*). Yani imaji alan herkes
# anahtarlari okuyabilir - paket bilerek boyle uretiliyor ki alici hicbir sey
# doldurmadan calistirsin. Anahtarsiz surum icin -AnahtarsIz.

param(
    [switch]$AnahtarsIz,     # anahtarlari gomme (alici kendi .env'ini girer)
    [switch]$ZipYok,         # klasoru uret, sikistirma
    [string]$Port = "8000"   # alicinin varsayilan portu (ayarlar.conf'a yazilir)
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path $PSScriptRoot -Parent)

# --- surum: tek kaynak src\scanner\version.py ------------------------------
$surumSatiri = Select-String -Path "src\scanner\version.py" -Pattern '^VERSION\s*=\s*"([^"]+)"'
if (-not $surumSatiri) { throw "version.py icinde VERSION bulunamadi" }
$SURUM = $surumSatiri.Matches[0].Groups[1].Value
$IMAJ  = "sap-proje-radari:$SURUM"
$CIKIS = "dist\sap-radar-docker"

Write-Host "== SAP Proje Radari $SURUM -> Linux paketi" -ForegroundColor Cyan

docker version --format '{{.Server.Version}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker calismiyor (Docker Desktop acik mi?)" }

# --- 1. anahtarlar ---------------------------------------------------------
$tohum = ".env.paket"
if (Test-Path $tohum) { Remove-Item $tohum -Force }

if ($AnahtarsIz) {
    Write-Host "== 1/5  Anahtarlar GOMULMUYOR (-AnahtarsIz)" -ForegroundColor Yellow
} else {
    if (-not (Test-Path ".env")) { throw ".env yok - gomulecek anahtar bulunamadi" }
    Write-Host "== 1/5  Anahtarlar imaja gomuluyor (.env -> $tohum)" -ForegroundColor Cyan
    Copy-Item ".env" $tohum -Force
}

# --- 2. imaj ---------------------------------------------------------------
try {
    Write-Host "== 2/5  Imaj derleniyor: $IMAJ" -ForegroundColor Cyan
    docker build -t $IMAJ .
    if ($LASTEXITCODE -ne 0) { throw "docker build basarisiz" }
} finally {
    # Anahtar kopyasi proje klasorunde UNUTULMASIN: .gitignore'da yok, imaja
    # her derlemede sessizce girerdi.
    if (Test-Path $tohum) { Remove-Item $tohum -Force }
}

# --- 3. dogrulama ----------------------------------------------------------
Write-Host "== 3/5  Imaj dogrulaniyor" -ForegroundColor Cyan
$kontrol = docker run --rm -e RADAR_DATA_DIR=/tmp/veri $IMAJ python -c @"
import os, sys
sys.path.insert(0, '/app/src')
from scanner import config
config._load_dotenv()
print('JOOBLE', 'VAR' if os.environ.get('JOOBLE_API_KEY') else 'YOK')
"@
if ($LASTEXITCODE -ne 0) { throw "Imaj calistirilamadi" }
Write-Host "   $kontrol"
if ($AnahtarsIz) {
    if ($kontrol -notmatch 'YOK') { throw "Anahtarsiz istendi ama imajda anahtar VAR" }
} else {
    if ($kontrol -notmatch 'VAR') { throw "Anahtar gomulmemis - .env icinde JOOBLE_API_KEY dolu mu?" }
}

# --- 4. paket klasoru ------------------------------------------------------
Write-Host "== 4/5  Paket hazirlaniyor: $CIKIS" -ForegroundColor Cyan
if (Test-Path $CIKIS) { Remove-Item -Recurse -Force $CIKIS }
New-Item -ItemType Directory -Path $CIKIS -Force | Out-Null

$tar = Join-Path $CIKIS "sap-radar-$SURUM.tar"
docker save -o $tar $IMAJ
if ($LASTEXITCODE -ne 0) { throw "docker save basarisiz" }

Copy-Item "packaging\docker\Radar_*.sh"        $CIKIS
Copy-Item "packaging\docker\SAP-Radar.desktop" $CIKIS
Copy-Item "packaging\docker\KURULUM_LINUX.md"  (Join-Path $CIKIS "KURULUM.md")

# Script'lerin okudugu ayar dosyasi. Surum burada sabitlenir: alicidaki
# Radar_Baslat.sh dogru imaj etiketini arasin.
$conf = @"
# SAP Proje Radari - baslatma ayarlari. Script'ler bu dosyayi okur.
# Degistirdikten sonra:  ./Radar_Durdur.sh && ./Radar_Baslat.sh

RADAR_IMAJ=$IMAJ
RADAR_TAR=sap-radar-$SURUM.tar
RADAR_KONTEYNER=sap-radar
RADAR_BIRIM=sap-radar-veri

# Panelin yayinlandigi port.
RADAR_PORT=$Port

# 127.0.0.1 = yalnizca bu makine.  0.0.0.0 = agdaki herkes
# (once admin parolasini degistirin!)
RADAR_BIND=127.0.0.1

RADAR_TZ=Europe/Istanbul
"@
# Linux script'i okuyacak: LF ve BOM'suz UTF-8 sart.
[IO.File]::WriteAllText((Join-Path $CIKIS "ayarlar.conf"), ($conf -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding $false))

$boyut = [math]::Round((Get-Item $tar).Length / 1MB)
Write-Host "   imaj: $boyut MB"

# --- 5. zip ----------------------------------------------------------------
if ($ZipYok) {
    Write-Host "`nBitti: $CIKIS\" -ForegroundColor Green
} else {
    Write-Host "== 5/5  Sikistiriliyor" -ForegroundColor Cyan
    $zip = "dist\sap-radar-docker-$SURUM.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path "$CIKIS\*" -DestinationPath $zip
    $zipMB = [math]::Round((Get-Item $zip).Length / 1MB)
    Write-Host "`nBitti: $zip  ($zipMB MB)" -ForegroundColor Green
}

Write-Host ""
Write-Host "Karsi tarafta yapilacak (Ubuntu):" -ForegroundColor Green
Write-Host "  unzip sap-radar-docker-$SURUM.zip -d sap-radar"
Write-Host "  cd sap-radar && chmod +x *.sh && ./Radar_Baslat.sh"
if (-not $AnahtarsIz) {
    Write-Warning "Bu pakette API anahtarlari IMAJIN ICINDE. Guvenmediginiz kimseye vermeyin."
}
