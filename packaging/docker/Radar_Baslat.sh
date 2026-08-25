#!/usr/bin/env bash
# SAP Proje Radari - Linux baslatici.
#
#   ./Radar_Baslat.sh          panel ayaga kalkar, adres yazilir
#
# Yaptigi is sirayla:
#   1. docker var mi / calisiyor mu
#   2. imaj yuklu degilse yanindaki .tar dosyasindan yukler (docker load)
#   3. konteyner yoksa olusturur, duruyorsa baslatir
#   4. saglik kontrolu yesillenene kadar bekler
#   5. masaustu varsa tarayiciyi acar, kisayolu menuye ekler
#
# Ayarlar ayarlar.conf dosyasindan gelir; ortam degiskeni onu ezer:
#   RADAR_PORT=8010 ./Radar_Baslat.sh      baska port
#   RADAR_BIND=0.0.0.0 ./Radar_Baslat.sh   agdaki makinelere ac
set -euo pipefail

KOK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$KOK"

# ayarlar.conf'taki degerler VARSAYILANDIR: dosyayi oldugu gibi source etmek
# komut satirindaki ortam degiskenini (RADAR_PORT=8010 ./Radar_Baslat.sh) ezerdi.
ayar_yukle() {
    [ -f "$1" ] || return 0
    while IFS= read -r satir || [ -n "$satir" ]; do
        satir="${satir%$'\r'}"                     # Windows'ta duzenlenmis olabilir
        case "$satir" in ''|'#'*) continue ;; esac
        case "$satir" in *=*) ;; *) continue ;; esac
        anahtar="${satir%%=*}"; deger="${satir#*=}"
        case "$anahtar" in RADAR_*) ;; *) continue ;; esac   # yalnizca kendi ayarlarimiz
        eval ": \"\${$anahtar:=\$deger}\""              # zaten tanimliysa dokunma
    done < "$1"
}
ayar_yukle ayarlar.conf

IMAJ="${RADAR_IMAJ:-sap-proje-radari:1.4.0}"
KONTEYNER="${RADAR_KONTEYNER:-sap-radar}"
BIRIM="${RADAR_BIRIM:-sap-radar-veri}"
PORT="${RADAR_PORT:-8000}"
BIND="${RADAR_BIND:-127.0.0.1}"
TZ_="${RADAR_TZ:-Europe/Istanbul}"

kirmizi() { printf '\033[31m%s\033[0m\n' "$*"; }
yesil()   { printf '\033[32m%s\033[0m\n' "$*"; }
bilgi()   { printf '  %s\n' "$*"; }

bekle_tus() {
    # Cift tiklayarak acilan pencere hata mesajini gostermeden kapanmasin.
    if [ -t 0 ] && [ "${RADAR_BEKLEME:-1}" = "1" ]; then
        printf '\nKapatmak icin Enter...'; read -r _ || true
    fi
}
trap 'bekle_tus' EXIT

# --- 1. docker -------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    kirmizi "Docker kurulu degil."
    bilgi "Ubuntu'da kurmak icin:"
    bilgi "  sudo apt update && sudo apt install -y docker.io"
    bilgi "  sudo usermod -aG docker \$USER   # sonra oturumu kapatip acin"
    exit 1
fi

# Kullanici docker grubunda degilse her komut sudo ister. Bir kez tespit edip
# butun cagrilara ayni oneki koyuyoruz - yarim kurulum (imaj root'ta, konteyner
# kullanicida) en kotu durum.
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null || [ -t 0 ]; then
        if sudo docker info >/dev/null 2>&1; then
            DOCKER=(sudo docker)
            bilgi "docker grubunda degilsiniz; sudo ile devam ediliyor."
            bilgi "Kalici cozum: sudo usermod -aG docker \$USER  (oturumu yenileyin)"
        else
            kirmizi "Docker servisi calismiyor:  sudo systemctl start docker"
            exit 1
        fi
    else
        kirmizi "Docker'a erisilemiyor (izin yok veya servis kapali)."
        exit 1
    fi
fi

# --- 2. imaj ---------------------------------------------------------------
if ! "${DOCKER[@]}" image inspect "$IMAJ" >/dev/null 2>&1; then
    TAR="${RADAR_TAR:-}"
    [ -z "$TAR" ] && TAR="$(ls -1 sap-radar-*.tar sap-radar-*.tar.gz 2>/dev/null | head -1 || true)"
    if [ -z "$TAR" ] || [ ! -f "$TAR" ]; then
        kirmizi "Imaj yok ($IMAJ) ve yaninda .tar dosyasi bulunamadi."
        bilgi "sap-radar-*.tar dosyasini bu klasore koyun."
        exit 1
    fi
    echo "Imaj yukleniyor: $TAR  (bir kerelik, ~1 dk)"
    if [ "${TAR##*.}" = "gz" ]; then
        gunzip -c "$TAR" | "${DOCKER[@]}" load
    else
        "${DOCKER[@]}" load -i "$TAR"
    fi
fi

# --- 3. konteyner ----------------------------------------------------------
DURUM="$("${DOCKER[@]}" inspect -f '{{.State.Status}}' "$KONTEYNER" 2>/dev/null || true)"

if [ -z "$DURUM" ]; then
    echo "Konteyner olusturuluyor (port $PORT)..."
    "${DOCKER[@]}" volume create "$BIRIM" >/dev/null
    # --restart unless-stopped: makine yeniden baslayinca panel de kalkar.
    # Tek replika bilincli: tarama zamanlayicisi panelin icinde donuyor ve
    # veritabani SQLite - ikinci konteyner ayni dosyaya ikinci tarama yazar.
    if ! "${DOCKER[@]}" run -d \
        --name "$KONTEYNER" \
        --restart unless-stopped \
        -p "${BIND}:${PORT}:8000" \
        -v "${BIRIM}:/veri" \
        -e "TZ=${TZ_}" \
        "$IMAJ" >/dev/null; then
        kirmizi "Konteyner baslatilamadi."
        bilgi "Port $PORT dolu olabilir:  RADAR_PORT=8010 ./Radar_Baslat.sh"
        exit 1
    fi
elif [ "$DURUM" != "running" ]; then
    echo "Konteyner baslatiliyor..."
    "${DOCKER[@]}" start "$KONTEYNER" >/dev/null
else
    echo "Konteyner zaten calisiyor."
fi

# Yayinlanan portu konteynerden okuyoruz: konteyner daha once baska portla
# olusturulmus olabilir, o zaman PORT degiskeni yaniltir.
GERCEK="$("${DOCKER[@]}" port "$KONTEYNER" 8000/tcp 2>/dev/null | head -1 || true)"
if [ -n "$GERCEK" ]; then PORT="${GERCEK##*:}"; fi
ADRES="http://127.0.0.1:${PORT}"

# --- 4. saglik -------------------------------------------------------------
printf 'Panel bekleniyor'
for _ in $(seq 1 60); do
    SAGLIK="$("${DOCKER[@]}" inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}yok{{end}}' "$KONTEYNER" 2>/dev/null || echo yok)"
    case "$SAGLIK" in
        healthy) printf '\n'; break ;;
        unhealthy)
            printf '\n'; kirmizi "Panel yanit vermiyor. Son gunlukler:"
            "${DOCKER[@]}" logs --tail 30 "$KONTEYNER" || true
            exit 1 ;;
    esac
    printf '.'; sleep 2
done

if [ "${SAGLIK:-}" != "healthy" ]; then
    printf '\n'; kirmizi "Panel 2 dakikada acilmadi. Gunlukler:"
    "${DOCKER[@]}" logs --tail 30 "$KONTEYNER" || true
    exit 1
fi

yesil "Hazir:  $ADRES"
bilgi "Ilk giris:  admin / 123456   -> Ayarlar > Hesabim'dan hemen degistirin."
if [ "$BIND" = "0.0.0.0" ]; then
    bilgi "Panel AGA ACIK. Parolayi degistirmeden birakmayin."
fi

# --- 5. masaustu -----------------------------------------------------------
if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
    # Menu kisayolu: bundan sonrasi icin gercek "tek tik".
    KISAYOL="$HOME/.local/share/applications/sap-radar.desktop"
    if [ ! -f "$KISAYOL" ] && [ -f "$KOK/SAP-Radar.desktop" ]; then
        mkdir -p "$(dirname "$KISAYOL")"
        sed "s|@KOK@|$KOK|g" "$KOK/SAP-Radar.desktop" > "$KISAYOL"
        chmod +x "$KISAYOL"
        bilgi "Uygulama menusune 'SAP Proje Radari' kisayolu eklendi."
    fi
    xdg-open "$ADRES" >/dev/null 2>&1 &
    RADAR_BEKLEME=0
fi
