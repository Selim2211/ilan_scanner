#!/usr/bin/env bash
# Paneli durdurur. Veri silinmez - birim (sap-radar-veri) oldugu gibi kalir,
# Radar_Baslat.sh ile kaldiginiz yerden devam eder.
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

"${DOCKER[@]}" stop "$KONTEYNER" >/dev/null 2>&1 \
    && printf '\033[32m%s\033[0m\n' "Durduruldu." \
    || echo "Zaten calismiyor."
