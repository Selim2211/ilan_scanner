#!/usr/bin/env bash
# Yeni surum kurar: yanindaki .tar dosyasini yukler, konteyneri yeni imajla
# yeniden olusturur. VERI KORUNUR - her sey birimde (sap-radar-veri), imajda
# degil; konteyneri silmek veriyi silmez.
#
#   ./Radar_Guncelle.sh                yanindaki en yeni sap-radar-*.tar
#   ./Radar_Guncelle.sh yeni.tar       belirli dosya
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
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
KONTEYNER="${RADAR_KONTEYNER:-sap-radar}"

DOCKER=(docker)
docker info >/dev/null 2>&1 || DOCKER=(sudo docker)

TAR="${1:-}"
if [ -z "$TAR" ]; then
    TAR="$(ls -1t sap-radar-*.tar sap-radar-*.tar.gz 2>/dev/null | head -1 || true)"
fi
if [ -z "$TAR" ] || [ ! -f "$TAR" ]; then
    printf '\033[31m%s\033[0m\n' "Yuklenecek .tar dosyasi bulunamadi."
    exit 1
fi

echo "Yeni imaj yukleniyor: $TAR"
if [ "${TAR##*.}" = "gz" ]; then
    gunzip -c "$TAR" | "${DOCKER[@]}" load
else
    "${DOCKER[@]}" load -i "$TAR"
fi

# Calisan konteynerin portu korunsun: kullanici RADAR_PORT=8010 ile kurmus
# olabilir, guncelleme onu sessizce 8000'e dondurmesin.
ESKI_PORT="$("${DOCKER[@]}" port "$KONTEYNER" 8000/tcp 2>/dev/null | head -1 || true)"

echo "Eski konteyner kaldiriliyor (veri birimde kaliyor)..."
"${DOCKER[@]}" rm -f "$KONTEYNER" >/dev/null 2>&1 || true

if [ -n "$ESKI_PORT" ]; then
    export RADAR_PORT="${ESKI_PORT##*:}"
    ADRES_ONEK="${ESKI_PORT%:*}"
    export RADAR_BIND="${ADRES_ONEK:-127.0.0.1}"
fi

RADAR_BEKLEME=0 ./Radar_Baslat.sh
