"""En basit arayuz: tek buton, baslat/durdur. Baska hicbir sey yok.

    py -m scanner mini
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

from .. import paths
from .service import RadarService

log = logging.getLogger(__name__)

APP_TITLE = "İlan Tarayıcı"
COLORS = {"bg": "#0f1620", "ink": "#e9f0f7", "ink3": "#7488a0",
          "signal": "#39d8c8", "coral": "#ff6b6b"}


class MiniWindow:
    def __init__(self, service: RadarService):
        self.service = service
        self._busy = False

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("300x180")
        self.root.resizable(False, False)
        self.root.configure(bg=COLORS["bg"])
        self._set_icon()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        tk.Label(self.root, text=APP_TITLE, bg=COLORS["bg"], fg=COLORS["ink"],
                 font=("Segoe UI Semibold", 13)).pack(pady=(20, 4))
        self.status_label = tk.Label(self.root, text="Duruyor", bg=COLORS["bg"],
                                     fg=COLORS["ink3"], font=("Segoe UI", 10))
        self.status_label.pack(pady=(0, 16))

        self.toggle_btn = tk.Button(self.root, text="Başlat", command=self.toggle,
                                    bg=COLORS["signal"], fg="#04211f", activebackground="#5ee6d8",
                                    font=("Segoe UI Semibold", 12), relief="flat",
                                    width=16, height=2, cursor="hand2")
        self.toggle_btn.pack()

        self._refresh()

    def _set_icon(self) -> None:
        icon = paths.resource("packaging/radar.ico")
        if icon.exists():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def toggle(self) -> None:
        if self._busy:
            return
        if self.service.running:
            self._run_async(self.service.stop, "Durduruluyor…")
        else:
            self._run_async(self._start_and_open, "Başlıyor…")

    def _start_and_open(self) -> None:
        self.service.start()
        self.service.open_panel()  # patron hangi portta calistigini bilmek istemiyor

    def _run_async(self, action, busy_text: str) -> None:
        self._busy = True
        self.toggle_btn.configure(state="disabled", text=busy_text)

        def worker():
            try:
                action()
            except Exception as exc:  # noqa: BLE001 - --windowed pakette konsol yok
                log.exception("islem basarisiz")
                self.root.after(0, lambda: self._on_error(exc))
            self._busy = False
            self.root.after(0, self._refresh)

        threading.Thread(target=worker, name="mini-eylem", daemon=True).start()

    def _on_error(self, exc: Exception) -> None:
        messagebox.showerror(APP_TITLE, f"İşlem başarısız:\n\n{exc}")

    def _refresh(self) -> None:
        running = self.service.running
        self.status_label.configure(text="Çalışıyor" if running else "Duruyor",
                                    fg=COLORS["signal"] if running else COLORS["ink3"])
        if not self._busy:
            self.toggle_btn.configure(state="normal", text="Durdur" if running else "Başlat")
        self.root.after(1000, self._refresh)

    def quit(self) -> None:
        self.service.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(port: int | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    _file_logging()
    MiniWindow(RadarService(port=port)).run()
    return 0


def _file_logging() -> None:
    handler = logging.FileHandler(
        paths.log_dir() / f"radar_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
