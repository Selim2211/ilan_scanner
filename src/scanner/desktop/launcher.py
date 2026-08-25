"""SAP Proje Radari - masaustu baslatma penceresi (Tkinter + tepsi ikonu).

Patronun bilgisayarinda cift tikla acilir: tarama arka planda baslar, panel
tarayicida acilir. Pencere kapatilinca program tepside calismaya devam eder.

    py -m scanner gui
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from .. import paths
from .service import RadarService

log = logging.getLogger(__name__)

APP_TITLE = "İlan Tarayıcı"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SAPProjeRadari"

# panelle ayni renk dili: turkuaz = sinyal, kehribar = uyari, mercan = hata
COLORS = {
    "bg": "#0f1620", "surface": "#18222f", "line": "#26344a",
    "ink": "#e9f0f7", "ink2": "#a7b6c8", "ink3": "#7488a0",
    "signal": "#39d8c8", "amber": "#f0a93c", "coral": "#ff6b6b",
}

PHASE_TEXT = {
    "quick": ("Yeni ilan taraması sürüyor…", "signal"),
    "scanning": ("Tam tarama: tüm kaynaklar…", "signal"),
    "verifying": ("İlan linkleri doğrulanıyor…", "signal"),
    "sleeping": ("Çalışıyor", "signal"),
    "idle": ("Hazır", "ink3"),
    "off": ("Durduruldu", "ink3"),
}


class LogPipe(logging.Handler):
    """Log kayitlarini arayuze tasiyan kuyruk (Tkinter baska is parcacigindan cagrilmaz)."""

    def __init__(self, sink: queue.Queue):
        super().__init__(level=logging.INFO)
        self.sink = sink
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait(self.format(record))
        except queue.Full:
            pass


def autostart_enabled() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    """Windows acilisinda otomatik baslatma (HKCU - yonetici izni gerekmez)."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = f'"{sys.executable}"'
            if not paths.is_frozen():
                command = f'"{sys.executable}" -m scanner gui'
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


class LauncherWindow:
    """Ana pencere: durum, dugmeler, canli log, ayarlar."""

    def __init__(self, service: RadarService, autostart_scan: bool = True):
        self.service = service
        self.logs: queue.Queue[str] = queue.Queue(maxsize=2000)
        self.tray = None
        self._closing = False
        self._open_panel_on_start = True
        self._busy = False              # Başlat/Durdur gecisi surerken durum donguysu ustune yazmasin

        root = logging.getLogger()
        root.setLevel(logging.INFO)          # yoksa INFO kayitlari arayuze hic gelmez
        root.addHandler(LogPipe(self.logs))
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("760x560")
        self.root.minsize(640, 480)
        self.root.configure(bg=COLORS["bg"])
        self._set_icon()
        self._build_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        log.info("%s hazır. Veri klasörü: %s", APP_TITLE, paths.data_dir())
        self._pump_logs()
        self._refresh_status()
        if autostart_scan:
            self.root.after(400, self.start_service)

    # --- gorunum -------------------------------------------------------
    def _set_icon(self) -> None:
        icon = paths.resource("packaging/radar.ico")
        if icon.exists():
            try:
                self.root.iconbitmap(str(icon))
            except tk.TclError:
                pass

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=COLORS["bg"], foreground=COLORS["ink"],
                        fieldbackground=COLORS["surface"], borderwidth=0)
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Card.TFrame", background=COLORS["surface"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["ink"],
                        font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=COLORS["surface"], foreground=COLORS["ink"])
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 15))
        style.configure("Muted.TLabel", foreground=COLORS["ink3"], font=("Segoe UI", 9))
        style.configure("Stat.TLabel", background=COLORS["surface"],
                        foreground=COLORS["signal"], font=("Consolas", 17, "bold"))
        style.configure("StatLabel.TLabel", background=COLORS["surface"],
                        foreground=COLORS["ink3"], font=("Segoe UI", 8))
        style.configure("TButton", background=COLORS["surface"], foreground=COLORS["ink"],
                        font=("Segoe UI", 10), padding=(14, 9))
        style.map("TButton", background=[("active", COLORS["line"])])
        style.configure("Primary.TButton", background=COLORS["signal"], foreground="#04211f",
                        font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("active", "#5ee6d8")])
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0, tabmargins=(0, 4, 0, 0))
        # clam temasi secili olmayan sekmeyi acik renkte boyuyor; elle karartiyoruz
        style.configure("TNotebook.Tab", background=COLORS["line"], foreground=COLORS["ink3"],
                        padding=(18, 9), font=("Segoe UI", 10), borderwidth=0, lightcolor=COLORS["line"],
                        darkcolor=COLORS["line"], bordercolor=COLORS["line"])
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["surface"]), ("active", COLORS["surface"])],
                  lightcolor=[("selected", COLORS["surface"])],
                  foreground=[("selected", COLORS["signal"]), ("active", COLORS["ink"])])
        style.configure("TScrollbar", background=COLORS["line"], troughcolor=COLORS["surface"],
                        borderwidth=0, arrowcolor=COLORS["ink3"])
        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["ink2"])
        style.map("TCheckbutton", background=[("active", COLORS["bg"])])

    def _build_ui(self) -> None:
        head = ttk.Frame(self.root, padding=(18, 16, 18, 6))
        head.pack(fill="x")
        ttk.Label(head, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(head, text="uzaktan · proje bazlı SAP işleri", style="Muted.TLabel").pack(anchor="w")

        # durum satiri
        status = ttk.Frame(self.root, padding=(18, 8))
        status.pack(fill="x")
        self.dot = tk.Canvas(status, width=13, height=13, bg=COLORS["bg"], highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 11, 11, fill=COLORS["ink3"], outline="")
        self.dot.pack(side="left", padx=(0, 9))
        self.status_label = ttk.Label(status, text="Hazır", font=("Segoe UI Semibold", 11))
        self.status_label.pack(side="left")
        self.next_label = ttk.Label(status, text="", style="Muted.TLabel")
        self.next_label.pack(side="left", padx=(10, 0))

        # dugmeler
        buttons = ttk.Frame(self.root, padding=(18, 6))
        buttons.pack(fill="x")
        self.toggle_btn = ttk.Button(buttons, text="Başlat", style="Primary.TButton",
                                     command=self.toggle_service)
        self.toggle_btn.pack(side="left")
        ttk.Button(buttons, text="Paneli Aç", command=self.service.open_panel).pack(side="left", padx=6)
        self.scan_btn = ttk.Button(buttons, text="Şimdi Tara", command=self.scan_now)
        self.scan_btn.pack(side="left")

        # sayilar
        stats = ttk.Frame(self.root, padding=(18, 10))
        stats.pack(fill="x")
        self.stat_vars: dict[str, tk.StringVar] = {}
        for key, label in [("active", "aktif ilan"), ("remote", "uzaktan"),
                           ("contract", "proje bazlı"), ("new_24h", "son 24 saat"),
                           ("closed", "kapandı")]:
            card = ttk.Frame(stats, style="Card.TFrame", padding=(14, 9))
            card.pack(side="left", padx=(0, 8))
            var = tk.StringVar(value="–")
            self.stat_vars[key] = var
            ttk.Label(card, textvariable=var, style="Stat.TLabel").pack(anchor="w")
            ttk.Label(card, text=label.upper(), style="StatLabel.TLabel").pack(anchor="w")

        # sekmeler
        tabs = ttk.Notebook(self.root)
        tabs.pack(fill="both", expand=True, padx=18, pady=(6, 16))
        tabs.add(self._build_log_tab(tabs), text="Kayıtlar")
        tabs.add(self._build_settings_tab(tabs), text="Ayarlar")

    def _build_log_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=1)
        self.log_box = tk.Text(frame, bg=COLORS["surface"], fg=COLORS["ink2"],
                               insertbackground=COLORS["ink"], relief="flat", wrap="none",
                               font=("Consolas", 9), padx=12, pady=10, state="disabled")
        scroll = ttk.Scrollbar(frame, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True)
        return frame

    def _build_settings_tab(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=16)

        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(frame, text="Windows açılışında otomatik başlat",
                        variable=self.autostart_var,
                        command=self._toggle_autostart).pack(anchor="w", pady=(0, 14))

        ttk.Label(frame, text="Tarama aralıkları", style="Muted.TLabel").pack(anchor="w")
        auto = self.service.config.get("auto_scan") or {}
        ttk.Label(frame, text=f"Yeni ilan taraması: {auto.get('quick_interval_minutes', 8)} dakikada bir · "
                              f"Tam tarama: {auto.get('interval_minutes', 30)} dakikada bir"
                  ).pack(anchor="w", pady=(2, 14))

        ttk.Label(frame, text="Kaynaklar", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text=self._source_summary(), wraplength=650,
                  justify="left").pack(anchor="w", pady=(2, 14))

        ttk.Label(frame, text="Veri klasörü", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text=str(paths.data_dir())).pack(anchor="w", pady=(2, 6))
        ttk.Button(frame, text="Klasörü Aç", command=self._open_data_dir).pack(anchor="w")
        return frame

    def _source_summary(self) -> str:
        """Aktif kaynaklarin anahtar durumu (panelde /ayarlar ile ayni katalog)."""
        from ..pipeline import active_sources
        from ..settings import source_catalog

        durum = {row["name"]: row for row in source_catalog(self.service.config)}
        parts = []
        for name in active_sources(self.service.config):
            row = durum.get(name)
            if row is None or row["ready"]:
                parts.append(f"{name} ✓")
            else:
                parts.append(f"{name} — {', '.join(row['missing'])} yok")
        return " · ".join(parts)

    # --- eylemler ------------------------------------------------------
    def toggle_service(self) -> None:
        if self.service.running:
            self.stop_service()
        else:
            self.start_service()

    def stop_service(self) -> None:
        self._busy = True
        self.toggle_btn.configure(text="Durduruluyor…", state="disabled")

        def worker():
            try:
                self.service.stop()
            except Exception:  # noqa: BLE001 - ayni sebep: --windowed pakette konsol yok
                log.exception("ajan durdurulamadi")
            self.root.after(0, self._on_stop_ok)

        threading.Thread(target=worker, name="ajan-durdur", daemon=True).start()

    def _on_stop_ok(self) -> None:
        self._busy = False
        self.toggle_btn.configure(text="Başlat", state="normal")

    def start_service(self, open_panel: bool = True) -> None:
        if self.service.running:
            return

        self.toggle_btn.configure(text="Başlıyor…", state="disabled")

        def worker():
            try:
                self.service.start()
            except Exception as exc:  # noqa: BLE001 - --windowed pakette konsol yok,
                # yakalanmazsa hata sessizce kaybolur ve dugme hicbir sey yapmamis gibi durur.
                log.exception("ajan baslatilamadi")
                self.root.after(0, lambda: self._on_start_failed(exc))
                return
            if open_panel and self._open_panel_on_start:
                self._open_panel_on_start = False
                self.service.open_panel()      # ilk baslatmada panel kendiliginden acilir
            self.root.after(0, lambda: self.toggle_btn.configure(state="normal"))

        threading.Thread(target=worker, name="ajan-baslat", daemon=True).start()

    def _on_start_failed(self, exc: Exception) -> None:
        self.toggle_btn.configure(text="Başlat", state="normal")
        detail = ("Ajan başlatılamadı:\n\n" + str(exc) + "\n\nAyrıntı: "
                  + str(paths.log_dir()) + " klasöründeki günlükte.")
        messagebox.showerror(APP_TITLE, detail)

    def scan_now(self) -> None:
        if not self.service.running:
            messagebox.showinfo(APP_TITLE, "Önce 'Başlat' deyin.")
            return
        if not self.service.scan_now():
            messagebox.showinfo(APP_TITLE, "Tarama zaten sürüyor.")

    def _toggle_autostart(self) -> None:
        try:
            set_autostart(self.autostart_var.get())
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Otomatik başlatma ayarlanamadı: {exc}")
            self.autostart_var.set(autostart_enabled())

    def _open_data_dir(self) -> None:
        import os

        os.startfile(str(paths.data_dir()))  # noqa: S606 - Windows klasor acma

    # --- dongu ---------------------------------------------------------
    def _pump_logs(self) -> None:
        satirlar = []
        while True:
            try:
                satirlar.append(self.logs.get_nowait())
            except queue.Empty:
                break
        if satirlar:
            self.log_box.configure(state="normal")
            self.log_box.insert("end", "\n".join(satirlar) + "\n")
            # bellek sismesin: son 500 satir yeter
            if int(self.log_box.index("end-1c").split(".")[0]) > 500:
                self.log_box.delete("1.0", "100.0")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(700, self._pump_logs)

    def _refresh_status(self) -> None:
        snap = self.service.snapshot()
        phase = snap.get("phase", "off") if self.service.running else "off"
        text, color = PHASE_TEXT.get(phase, ("Çalışıyor", "signal"))
        self.status_label.configure(text=text)
        self.dot.itemconfigure(self.dot_id, fill=COLORS[color])
        if not self._busy:              # Başlıyor.../Durduruluyor... yazisinin ustune yazma
            self.toggle_btn.configure(text="Durdur" if self.service.running else "Başlat")

        detay = ""
        if self.service.running and phase == "sleeping":
            saniye = snap.get("seconds_to_next")
            tur = "tam tarama" if snap.get("next_kind") == "full" else "yeni ilan taraması"
            if saniye is not None:
                detay = f"sonraki {tur}: {saniye // 60} dk {saniye % 60:02d} sn"
        if snap.get("last_finished"):
            son = str(snap["last_finished"])[11:16]
            detay += f"   ·   son tur {son}: {snap.get('last_new', 0)} yeni, " \
                     f"{snap.get('last_updated', 0)} güncel"
        if snap.get("errors"):
            detay += f"   ·   ⚠ {snap['errors'][-1][:60]}"
        self.next_label.configure(text=detay)

        try:
            stats = self.service.stats()
            for key, var in self.stat_vars.items():
                var.set(str(stats.get(key, 0)))
        except Exception:  # noqa: BLE001 - veritabani kilitliyse bir sonraki turda
            pass

        self.root.after(1000, self._refresh_status)

    # --- tepsi ---------------------------------------------------------
    def hide_to_tray(self) -> None:
        """Pencere kapatilinca program tepside calismaya devam eder."""
        if self.tray is None and not self._start_tray():
            if messagebox.askyesno(APP_TITLE, "Program tamamen kapatılsın mı?\n"
                                              "(Hayır derseniz pencere açık kalır)"):
                self.quit()
            return
        self.root.withdraw()

    def _start_tray(self) -> bool:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            log.warning("pystray/Pillow yok, tepsi ikonu devre disi")
            return False

        image = Image.new("RGB", (64, 64), COLORS["bg"])
        draw = ImageDraw.Draw(image)
        draw.ellipse((8, 8, 56, 56), outline=COLORS["signal"], width=5)
        draw.ellipse((26, 26, 38, 38), fill=COLORS["signal"])

        menu = pystray.Menu(
            pystray.MenuItem("Göster", lambda: self.root.after(0, self.show)),
            pystray.MenuItem("Paneli Aç", lambda: self.service.open_panel()),
            pystray.MenuItem("Şimdi Tara", lambda: self.root.after(0, self.scan_now)),
            pystray.MenuItem("Çıkış", lambda: self.root.after(0, self.quit)),
        )
        self.tray = pystray.Icon("sap-radar", image, APP_TITLE, menu)
        threading.Thread(target=self.tray.run, name="tepsi", daemon=True).start()
        return True

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self.tray:
            self.tray.stop()
        self.service.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main(port: int | None = None, minimized: bool = False) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    _file_logging()
    window = LauncherWindow(RadarService(port=port))
    if minimized:
        window.root.after(600, window.hide_to_tray)
    window.run()
    return 0


def _file_logging() -> None:
    """Paketlenmis programda konsol yok; loglar dosyaya da yazilir."""
    handler = logging.FileHandler(
        paths.log_dir() / f"radar_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
