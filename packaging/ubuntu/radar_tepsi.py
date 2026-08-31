#!/usr/bin/env python3
"""SAP Proje Radari - Ubuntu gorev tepsisi (Docker surumu).

Windows'taki tepsi ikonunun Linux karsiligi. Onemli fark: uygulamayi KENDI
icinde calistirmaz, konteyneri yonetir.

Neden boyle: tepsi ikonu ekran sunucusu (X11/Wayland) ve masaustu oturumunun
D-Bus veri yolunu ister; konteyner ikisinden de yalitilmistir. Ikonu konteynere
sokmak icin host'un X11 soketini iceri baglamak gerekirdi - yalitim delinir.
Bu yuzden tepsi host'ta durur, panel konteynerde; ikisi `docker compose`
komutlariyla konusur.

Bagimliliklar apt'den gelir, pip gerekmez:
    python3-gi  gir1.2-gtk-3.0  gir1.2-ayatanaappindicator3-0.1

Kendi kendini sinama (masaustu gerekmez):
    python3 radar_tepsi.py --kontrol
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

PROJE = Path(__file__).resolve().parent
COMPOSE = PROJE / "docker-compose.yml"
SERVIS = "radar"
SURUM = "1.4.0"

# Tema ikonlari kullaniliyor - pakete ikon dosyasi koymamak icin. Ubuntu'nun
# Yaru temasinda ucu de mevcut.
IKON = {
    "calisiyor": "emblem-default",
    "durdu": "media-playback-stop",
    "hasta": "dialog-warning",
    "yok": "media-playback-stop",
}

DURUM_METNI = {
    "calisiyor": "Çalışıyor",
    "durdu": "Durdu",
    "hasta": "Yanıt vermiyor",
    "yok": "Kurulu değil",
}


# --------------------------------------------------------------------------
# Docker katmani - Gtk'dan bagimsiz, --kontrol modunda da kullaniliyor
# --------------------------------------------------------------------------
def _compose(*argv: str) -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE), *argv]


def _calistir(argv: list[str], zaman_asimi: int = 120) -> tuple[int, str]:
    try:
        sonuc = subprocess.run(argv, capture_output=True, text=True, timeout=zaman_asimi)
        return sonuc.returncode, (sonuc.stdout + sonuc.stderr).strip()
    except FileNotFoundError:
        return 127, "docker bulunamadi"
    except subprocess.TimeoutExpired:
        return 124, "komut zaman asimina ugradi"


def durum() -> tuple[str, str | None]:
    """(durum, adres) dondurur. Adres yalnizca panel yayindayken dolu."""
    # -a sart: `ps -q` yalnizca CALISAN konteyneri listeler, durdurulmus olani
    # atlar - o zaman tepsi "Durdu" yerine "Kurulu degil" gosterirdi.
    kod, kimlik = _calistir(_compose("ps", "-aq", SERVIS), 30)
    if kod != 0 or not kimlik.strip():
        return "yok", None

    kimlik = kimlik.splitlines()[0].strip()
    bicim = (
        "{{.State.Status}}|"
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}|"
        "{{range $p, $c := .NetworkSettings.Ports}}{{range $c}}{{.HostIp}}:{{.HostPort}} {{end}}{{end}}"
    )
    kod, cikti = _calistir(["docker", "inspect", "-f", bicim, kimlik], 30)
    if kod != 0:
        return "yok", None

    parcalar = cikti.split("|")
    if len(parcalar) < 3:
        return "yok", None
    kosu, saglik, portlar = parcalar[0], parcalar[1], parcalar[2]

    adres = None
    yayin = portlar.split()
    if yayin:
        makine, _, port = yayin[0].rpartition(":")
        # 0.0.0.0 tarayiciya yazilmaz; ayni makineden bakiliyor
        makine = "127.0.0.1" if makine in ("0.0.0.0", "::", "") else makine
        adres = f"http://{makine}:{port}"

    if kosu != "running":
        return "durdu", None
    if saglik == "unhealthy":
        return "hasta", adres
    return "calisiyor", adres


def baslat() -> tuple[bool, str]:
    kod, cikti = _calistir(_compose("up", "-d"), 600)
    return kod == 0, cikti


def durdur() -> tuple[bool, str]:
    kod, cikti = _calistir(_compose("stop"), 120)
    return kod == 0, cikti


def yeniden() -> tuple[bool, str]:
    kod, cikti = _calistir(_compose("restart"), 300)
    return kod == 0, cikti


def bildir(baslik: str, mesaj: str) -> None:
    if shutil.which("notify-send"):
        subprocess.Popen(
            ["notify-send", "-a", "SAP Proje Radarı", baslik, mesaj],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def gunluk_ac() -> None:
    """Gunlukleri yeni bir terminal penceresinde acar."""
    komut = "cd " + str(PROJE) + " && docker compose logs -f --tail 50"
    adaylar = (
        ("gnome-terminal", ["gnome-terminal", "--", "bash", "-c", komut]),
        ("konsole", ["konsole", "-e", "bash", "-c", komut]),
        ("tilix", ["tilix", "-e", "bash", "-c", komut]),
        ("xfce4-terminal", ["xfce4-terminal", "-x", "bash", "-c", komut]),
        ("x-terminal-emulator", ["x-terminal-emulator", "-e", "bash", "-c", komut]),
    )
    for terminal, argv in adaylar:
        if shutil.which(terminal):
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    bildir("Terminal bulunamadı", "Günlükler için: docker compose logs -f")


def indicator_yukle():
    """Ayatana (Ubuntu 22.04+) yoksa eski AppIndicator3'e duser."""
    import gi

    for ad, surum in (("AyatanaAppIndicator3", "0.1"), ("AppIndicator3", "0.1")):
        try:
            gi.require_version(ad, surum)
            return ad
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Kendi kendini sinama - masaustu olmadan da calisir
# --------------------------------------------------------------------------
def kontrol() -> int:
    sorun = 0

    print("proje klasoru  : " + str(PROJE))
    print("compose dosyasi: " + ("VAR" if COMPOSE.exists() else "YOK"))
    if not COMPOSE.exists():
        sorun = 1

    try:
        import gi  # noqa: F401

        print("python3-gi     : VAR")
    except ImportError:
        print("python3-gi     : YOK  -> sudo apt install python3-gi")
        return 1

    import gi

    try:
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401

        print("Gtk 3.0        : VAR")
    except (ValueError, ImportError):
        print("Gtk 3.0        : YOK  -> sudo apt install gir1.2-gtk-3.0")
        sorun = 1

    arkayuz = indicator_yukle()
    if arkayuz:
        print("gosterge       : VAR (" + arkayuz + ")")
    else:
        print("gosterge       : YOK  -> sudo apt install gir1.2-ayatanaappindicator3-0.1")
        sorun = 1

    if shutil.which("docker"):
        print("docker         : VAR")
        kod, _ = _calistir(["docker", "info"], 30)
        print("docker erisimi : " + ("VAR" if kod == 0 else "YOK (grup/servis?)"))
        if kod != 0:
            sorun = 1
        else:
            hal, adres = durum()
            print("konteyner      : " + DURUM_METNI[hal] + (" (" + adres + ")" if adres else ""))
    else:
        print("docker         : YOK  -> sudo apt install docker.io")
        sorun = 1

    print("")
    print("sonuc: " + ("HAZIR" if sorun == 0 else "EKSIK VAR"))
    return sorun


# --------------------------------------------------------------------------
# Tepsi
# --------------------------------------------------------------------------
class Tepsi:
    def __init__(self) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk

        self.Gtk = Gtk
        self.GLib = GLib
        self.hal = "yok"
        self.adres: str | None = None
        self.mesgul = False

        arkayuz = indicator_yukle()
        if arkayuz is None:
            print(
                "Gosterge kutuphanesi yok:\n"
                "  sudo apt install gir1.2-ayatanaappindicator3-0.1",
                file=sys.stderr,
            )
            raise SystemExit(1)

        modul = __import__("gi.repository", fromlist=[arkayuz])
        AppIndicator = getattr(modul, arkayuz)

        self.gosterge = AppIndicator.Indicator.new(
            "sap-proje-radari",
            IKON["yok"],
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.gosterge.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.gosterge.set_title("SAP Proje Radarı")
        self.menu_kur()
        self.yenile()
        # 5 saniyede bir durum: konteyner disaridan durdurulursa ikon da dussun
        GLib.timeout_add_seconds(5, self.yenile)

    def menu_kur(self) -> None:
        Gtk = self.Gtk
        self.menu = Gtk.Menu()
        self.ogeler = {}

        satirlar = (
            ("durum", "Durum"),
            ("ayrac1", None),
            ("panel", "Paneli Aç"),
            ("baslat", "Başlat"),
            ("durdur", "Durdur"),
            ("yeniden", "Yeniden Başlat"),
            ("ayrac2", None),
            ("gunluk", "Günlükler"),
            ("hakkinda", "Hakkında"),
            ("ayrac3", None),
            ("cikis", "Çıkış"),
        )
        for anahtar, etiket in satirlar:
            if etiket is None:
                oge = Gtk.SeparatorMenuItem()
            else:
                oge = Gtk.MenuItem(label=etiket)
                if anahtar == "durum":
                    oge.set_sensitive(False)
                else:
                    oge.connect("activate", getattr(self, "tik_" + anahtar))
            self.ogeler[anahtar] = oge
            self.menu.append(oge)

        self.menu.show_all()
        self.gosterge.set_menu(self.menu)

    def yenile(self) -> bool:
        if self.mesgul:
            return True
        self.hal, self.adres = durum()
        self.gosterge.set_icon_full(IKON[self.hal], "SAP Proje Radarı")
        self.ogeler["durum"].set_label("Durum: " + DURUM_METNI[self.hal])

        calisir = self.hal in ("calisiyor", "hasta")
        self.ogeler["panel"].set_sensitive(bool(self.adres))
        self.ogeler["baslat"].set_sensitive(not calisir)
        self.ogeler["durdur"].set_sensitive(calisir)
        self.ogeler["yeniden"].set_sensitive(calisir)
        return True

    def _arkaplan(self, is_adi: str, islev) -> None:
        """Uzun docker komutlari menuyu dondurmasin diye ayri is parcaciginda."""
        self.mesgul = True
        self.ogeler["durum"].set_label("Durum: " + is_adi + "...")

        def calis():
            oldu, cikti = islev()
            self.GLib.idle_add(self._bitti, is_adi, oldu, cikti)

        threading.Thread(target=calis, daemon=True).start()

    def _bitti(self, is_adi: str, oldu: bool, cikti: str) -> bool:
        self.mesgul = False
        if oldu:
            bildir("SAP Proje Radarı", is_adi + " tamam")
        else:
            bildir(is_adi + " başarısız", cikti.splitlines()[-1] if cikti else "")
        self.yenile()
        return False

    def tik_panel(self, *_):
        if self.adres:
            webbrowser.open(self.adres)

    def tik_baslat(self, *_):
        self._arkaplan("Başlatılıyor", baslat)

    def tik_durdur(self, *_):
        self._arkaplan("Durduruluyor", durdur)

    def tik_yeniden(self, *_):
        self._arkaplan("Yeniden başlatılıyor", yeniden)

    def tik_gunluk(self, *_):
        gunluk_ac()

    def tik_hakkinda(self, *_):
        Gtk = self.Gtk
        kutu = Gtk.MessageDialog(
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="SAP Proje Radarı",
        )
        kutu.format_secondary_text(
            "Sürüm " + SURUM + "\n"
            "Durum: " + DURUM_METNI[self.hal] + "\n"
            "Panel: " + (self.adres or "-") + "\n"
            "Proje: " + str(PROJE) + "\n\n"
            "Uygulama Docker konteynerinde çalışır; bu tepsi onu yönetir."
        )
        kutu.run()
        kutu.destroy()

    def tik_cikis(self, *_):
        # Yalnizca tepsi kapanir - konteyner calismaya devam eder.
        self.Gtk.main_quit()

    def calistir(self) -> None:
        self.Gtk.main()


def main() -> int:
    if "--kontrol" in sys.argv:
        return kontrol()

    if not COMPOSE.exists():
        print("docker-compose.yml bulunamadi: " + str(COMPOSE), file=sys.stderr)
        return 1

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print(
            "Masaustu oturumu yok (DISPLAY/WAYLAND_DISPLAY tanimsiz).\n"
            "Sunucuda tepsi calismaz; paneli su komutla yonetin:\n"
            "  docker compose up -d",
            file=sys.stderr,
        )
        return 1

    Tepsi().calistir()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
