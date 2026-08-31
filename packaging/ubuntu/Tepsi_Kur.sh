#!/usr/bin/env bash
# SAP Proje Radari - Ubuntu tepsi kurulumu.
#
#   ./Tepsi_Kur.sh            bagimliliklari kurar, tepsiyi baslatir, menuye ekler
#   ./Tepsi_Kur.sh --kaldir   tepsiyi ve otomatik baslatmayi kaldirir
#
# Tepsi HOST'ta calisir, konteyneri yonetir. Konteynere dokunmaz; kaldirsan da
# panel calismaya devam eder.
set -euo pipefail

KOK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$KOK"

UYGULAMA="$HOME/.local/share/applications/sap-radar-tepsi.desktop"
OTOMATIK="$HOME/.config/autostart/sap-radar-tepsi.desktop"

kirmizi() { printf '\033[31m%s\033[0m\n' "$*"; }
yesil()   { printf '\033[32m%s\033[0m\n' "$*"; }
bilgi()   { printf '  %s\n' "$*"; }

# --- kaldirma --------------------------------------------------------------
if [ "${1:-}" = "--kaldir" ]; then
    pkill -f "radar_tepsi.py" 2>/dev/null || true
    rm -f "$UYGULAMA" "$OTOMATIK"
    yesil "Tepsi kaldirildi. Konteyner etkilenmedi (docker compose ps ile gorursunuz)."
    exit 0
fi

# --- masaustu var mi -------------------------------------------------------
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    kirmizi "Masaustu oturumu yok - tepsi yalnizca ekranli Ubuntu'da calisir."
    bilgi "Sunucuda paneli su sekilde yonetin:"
    bilgi "  docker compose up -d      /  docker compose stop"
    exit 1
fi

# --- bagimliliklar ---------------------------------------------------------
# Hepsi apt'den; pip kullanilmiyor ki 24.04'un korumali ortamiyla catismasin.
EKSIK=()
python3 -c "import gi" 2>/dev/null || EKSIK+=(python3-gi)
[ -e /usr/lib/*/girepository-1.0/Gtk-3.0.typelib ] 2>/dev/null || EKSIK+=(gir1.2-gtk-3.0)
if ! ls /usr/lib/*/girepository-1.0/AyatanaAppIndicator3-0.1.typelib >/dev/null 2>&1 \
   && ! ls /usr/lib/*/girepository-1.0/AppIndicator3-0.1.typelib >/dev/null 2>&1; then
    EKSIK+=(gir1.2-ayatanaappindicator3-0.1)
fi
command -v notify-send >/dev/null 2>&1 || EKSIK+=(libnotify-bin)
command -v xdg-open    >/dev/null 2>&1 || EKSIK+=(xdg-utils)

if [ ${#EKSIK[@]} -gt 0 ]; then
    echo "Eksik paketler kuruluyor: ${EKSIK[*]}"
    sudo apt update
    sudo apt install -y "${EKSIK[@]}"
fi

# --- docker erisimi --------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    kirmizi "Docker kurulu degil:"
    bilgi "sudo apt install -y docker.io docker-compose-plugin"
    bilgi "sudo usermod -aG docker \$USER   # sonra oturumu kapatip acin"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    kirmizi "Docker'a erisilemiyor - tepsi komutlari calismaz."
    bilgi "sudo usermod -aG docker \$USER   sonra oturumu kapatip acin"
    bilgi "(sudo ile calistirmak tepsiyi root'un oturumuna baglar, onerilmez)"
    exit 1
fi

chmod +x radar_tepsi.py

# --- kendi kendini sinama --------------------------------------------------
echo ""
python3 radar_tepsi.py --kontrol
echo ""

# --- menu + otomatik baslatma girisleri ------------------------------------
mkdir -p "$(dirname "$UYGULAMA")" "$(dirname "$OTOMATIK")"
cat > "$UYGULAMA" <<GIRIS
[Desktop Entry]
Type=Application
Name=SAP Proje Radarı
Comment=İlan tarayıcı tepsi ikonu (Docker konteynerini yönetir)
Exec=python3 "$KOK/radar_tepsi.py"
Path=$KOK
Icon=system-search
Terminal=false
Categories=Office;Utility;
StartupNotify=false
GIRIS
chmod +x "$UYGULAMA"

# Oturum acilisinda kendiliginden: Windows'taki "baslangicta calistir" karsiligi.
# 8 saniye gecikme - masaustu tepsi alanini olusturmadan baslarsa ikon dusuyor.
sed 's|^Exec=.*|Exec=bash -c "sleep 8; exec python3 \\"'"$KOK"'/radar_tepsi.py\\""|' \
    "$UYGULAMA" > "$OTOMATIK"
chmod +x "$OTOMATIK"

# --- calistir --------------------------------------------------------------
pkill -f "radar_tepsi.py" 2>/dev/null || true
nohup python3 "$KOK/radar_tepsi.py" >/dev/null 2>&1 &
sleep 2

if pgrep -f "radar_tepsi.py" >/dev/null; then
    yesil "Tepsi calisiyor - saat yaninda ikonu goreceksiniz."
else
    kirmizi "Tepsi baslatilamadi. Elle calistirip hatayi gorun:"
    bilgi "python3 $KOK/radar_tepsi.py"
    exit 1
fi

bilgi "Menu: Paneli Aç · Başlat · Durdur · Yeniden Başlat · Günlükler · Hakkında"
bilgi "Oturum acilisinda kendiliginden baslar."
bilgi "Kaldirmak icin: ./Tepsi_Kur.sh --kaldir"
