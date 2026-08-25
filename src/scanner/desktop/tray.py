"""Sistem tepsisi uygulamasi: Baslat / Durdur / Paneli Ac / Hakkinda / Cikis.

    py -m scanner tray

Pencere yok; program gorev cubugunun sag alt kosesindeki simge alaninda durur.
Ikon rengi durumu gosterir (turkuaz = calisiyor, gri = duruk).

Is bolumu: pystray kendi dongusunu ayri bir is parcaciginda dondurur, Tkinter ise
yalnizca ANA is parcacigindan cagrilabilir. Bu yuzden gizli bir Tk koku ana
is parcaciginda mainloop'ta bekler; tepsi menusu pencere acacagi zaman isi
`root.after(0, ...)` ile ona devreder.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from datetime import datetime

from .. import paths
from ..version import APP_NAME, CHANGELOG, VERSION, about_lines
from .service import RadarService

log = logging.getLogger(__name__)

COLORS = {"bg": "#0f1620", "surface": "#18222f", "line": "#26344a",
          "ink": "#e9f0f7", "ink2": "#a7b6c8", "ink3": "#7488a0",
          "signal": "#39d8c8", "stopped": "#7488a0"}


def _icon_image(color: str):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=COLORS["bg"])
    draw.ellipse((13, 13, 51, 51), outline=color, width=5)
    draw.ellipse((27, 27, 37, 37), fill=color)
    return image


class TrayApp:
    """Tepsi ikonu + gizli Tk koku (pencereler icin)."""

    def __init__(self, service: RadarService):
        self.service = service
        self._busy = False
        self._about: tk.Toplevel | None = None

        self.root = tk.Tk()
        self.root.withdraw()                 # ana pencere yok, sadece dialog tasiyicisi
        self._set_icon(self.root)
        self.icon = self._build_icon()

    # --- tepsi menusu ----------------------------------------------------
    def _build_icon(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Başlat", self._on_start, enabled=lambda i: not self._can_stop()),
            pystray.MenuItem("Durdur", self._on_stop, enabled=lambda i: self._can_stop()),
            pystray.MenuItem("Paneli Aç", self._on_open, enabled=lambda i: self._can_stop(),
                             default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Hakkında", self._on_about),
            pystray.MenuItem("Çıkış", self._on_quit),
        )
        return pystray.Icon("ilan-tarayici", _icon_image(COLORS["stopped"]), APP_NAME, menu)

    def _can_stop(self) -> bool:
        """Menu ogesi tiklanabilir mi: gecis surerken ikisi de kapali."""
        return self.service.running and not self._busy

    def _refresh(self) -> None:
        running = self.service.running
        self.icon.icon = _icon_image(COLORS["signal"] if running else COLORS["stopped"])
        durum = "çalışıyor" if running else "duruk"
        self.icon.title = f"{APP_NAME} {VERSION} — {durum}"
        self.icon.update_menu()

    # --- eylemler --------------------------------------------------------
    def _on_start(self, icon=None, item=None) -> None:
        if self._busy or self.service.running:
            return
        self._run_async(self._start_and_open, "başladı")

    def _start_and_open(self) -> None:
        self.service.start()
        self.service.open_panel()   # hangi portta calistigi kullaniciya sorulmaz

    def _on_stop(self, icon=None, item=None) -> None:
        if self._busy or not self.service.running:
            return
        self._run_async(self.service.stop, "durduruldu")

    def _on_open(self, icon=None, item=None) -> None:
        if self.service.running:
            self.service.open_panel()

    def _run_async(self, action, done_word: str) -> None:
        self._busy = True
        self._refresh()

        def worker():
            try:
                action()
            except Exception:  # noqa: BLE001 - pencere yok, hata sessizce kaybolmasin
                log.exception("islem basarisiz")
                self._notify(f"İşlem başarısız oldu. Günlük: {paths.log_dir()}")
            else:
                self._notify(f"{APP_NAME} {done_word}.")
            self._busy = False
            self._refresh()

        threading.Thread(target=worker, name="tepsi-eylem", daemon=True).start()

    def _notify(self, text: str) -> None:
        try:
            self.icon.notify(text, APP_NAME)
        except Exception:  # noqa: BLE001 - bildirim destegi yoksa sessiz gec
            log.debug("tepsi bildirimi gosterilemedi")

    def _on_quit(self, icon=None, item=None) -> None:
        self.service.stop()
        self.icon.stop()
        self.root.after(0, self.root.quit)

    # --- Hakkinda penceresi ----------------------------------------------
    def _on_about(self, icon=None, item=None) -> None:
        # pystray kendi is parcacigindan cagiriyor; Tkinter ana is parcacigina devredilir
        self.root.after(0, self._show_about)

    def _set_icon(self, window) -> None:
        path = paths.resource("packaging/radar.ico")
        if path.exists():
            try:
                window.iconbitmap(str(path))
            except tk.TclError:
                pass

    def _show_about(self) -> None:
        if self._about is not None and self._about.winfo_exists():
            self._about.deiconify()
            self._about.lift()
            self._about.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._about = win
        win.title(f"{APP_NAME} hakkında")
        win.configure(bg=COLORS["bg"])
        win.geometry("560x520")
        win.minsize(460, 380)
        self._set_icon(win)
        win.protocol("WM_DELETE_WINDOW", self._close_about)

        head = tk.Frame(win, bg=COLORS["bg"])
        head.pack(fill="x", padx=22, pady=(20, 6))
        tk.Label(head, text=APP_NAME, bg=COLORS["bg"], fg=COLORS["ink"],
                 font=("Segoe UI Semibold", 16)).pack(anchor="w")
        tk.Label(head, text=f"sürüm {VERSION}  ·  uzaktan / proje bazlı SAP işleri",
                 bg=COLORS["bg"], fg=COLORS["signal"], font=("Segoe UI", 10)).pack(anchor="w")

        # --- teknik bilgiler
        info = tk.Frame(win, bg=COLORS["surface"])
        info.pack(fill="x", padx=22, pady=(14, 8))
        for satir, (label, value) in enumerate(about_lines()):
            tk.Label(info, text=label, bg=COLORS["surface"], fg=COLORS["ink3"],
                     font=("Segoe UI", 9)).grid(row=satir, column=0, sticky="w", padx=(12, 14), pady=3)
            tk.Label(info, text=value, bg=COLORS["surface"], fg=COLORS["ink2"],
                     font=("Consolas", 9), anchor="w", justify="left",
                     wraplength=340).grid(row=satir, column=1, sticky="w", padx=(0, 12), pady=3)

        # --- surum notlari
        tk.Label(win, text="Sürüm notları", bg=COLORS["bg"], fg=COLORS["ink3"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=22, pady=(8, 4))
        box = tk.Frame(win, bg=COLORS["surface"])
        box.pack(fill="both", expand=True, padx=22)
        text = tk.Text(box, bg=COLORS["surface"], fg=COLORS["ink2"], relief="flat",
                       font=("Segoe UI", 9), padx=12, pady=10, wrap="word", height=8)
        scroll = tk.Scrollbar(box, command=text.yview, bg=COLORS["line"])
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)

        text.tag_configure("head", foreground=COLORS["signal"], font=("Segoe UI Semibold", 10),
                           spacing1=8, spacing3=3)
        for surum, tarih, maddeler in CHANGELOG:
            text.insert("end", f"{surum} — {tarih}\n", "head")
            for madde in maddeler:
                text.insert("end", f"   •  {madde}\n")
        text.configure(state="disabled")

        tk.Button(win, text="Kapat", command=self._close_about, bg=COLORS["signal"],
                  fg="#04211f", activebackground="#5ee6d8", relief="flat",
                  font=("Segoe UI Semibold", 10), width=14, cursor="hand2"
                  ).pack(pady=16)

        win.lift()
        win.focus_force()

    def _close_about(self) -> None:
        if self._about is not None:
            self._about.destroy()
            self._about = None

    # --- yasam dongusu ---------------------------------------------------
    def run(self) -> None:
        threading.Thread(target=self.icon.run, name="tepsi", daemon=True).start()
        self.root.mainloop()


def main(port: int | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    _file_logging()
    TrayApp(RadarService(port=port)).run()
    return 0


def _file_logging() -> None:
    handler = logging.FileHandler(
        paths.log_dir() / f"radar_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
