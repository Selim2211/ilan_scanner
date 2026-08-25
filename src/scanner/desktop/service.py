"""Ajan servisi: tarama dongusu + web paneli tek surecte.

Arayuz (launcher.py) yalnizca bu sinifi kullanir; Tkinter'dan bagimsizdir,
bu yuzden test edilebilir ve ileride baska bir arayuze de baglanabilir.
"""
from __future__ import annotations

import logging
import socket
import threading
import webbrowser
from typing import Any

from ..config import load_config
from ..scheduler import BackgroundScanner, from_config

log = logging.getLogger(__name__)


def find_free_port(preferred: int = 8000, tries: int = 20) -> int:
    """Panel portu. Patronun makinesinde 8000 dolu olabilir."""
    for port in range(preferred, preferred + tries):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


class RadarService:
    """Tarama zamanlayicisi + uvicorn panelini birlikte baslatip durdurur."""

    def __init__(self, port: int | None = None):
        self.config = load_config()
        self.port = port or find_free_port(int((self.config.get("panel") or {})
                                               .get("port", 8000)))
        self.scanner: BackgroundScanner | None = None
        self._server: Any = None
        self._server_thread: threading.Thread | None = None

    # --- yasam dongusu -------------------------------------------------
    @property
    def running(self) -> bool:
        return self.scanner is not None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        if self.running:
            return
        # zamanlayici once kurulur: panel bunu disaridan alip kendi durumu gibi
        # gosterir, yoksa ust bardaki "canli tarama" gostergesi hep kapali kalirdi.
        self.scanner = from_config(self.config, enabled=True)
        self._start_panel()
        if self.scanner:
            self.scanner.start()
        log.info("ajan basladi - panel %s", self.url)

    def stop(self) -> None:
        if self.scanner:
            self.scanner.stop()
            self.scanner = None
        if self._server is not None:
            self._server.should_exit = True
            if self._server_thread:
                self._server_thread.join(timeout=8)
            self._server = None
            self._server_thread = None
        log.info("ajan durduruldu")

    # --- panel ---------------------------------------------------------
    def _start_panel(self) -> None:
        import uvicorn

        from ..web.app import create_app

        # external_scheduler: taramayi bu sinif yurutuyor, panele kendi zamanlayicisini
        # kurdurmak yerine bizimkini geciyoruz - ust bardaki durum gostergesi dogru calissin.
        app = create_app(external_scheduler=self.scanner)
        # log_config=None: pythonw'da (--windowed pakette de) sys.stdout yok, uvicorn'un
        # varsayilan log ayari StreamHandler(stream=sys.stdout) kurmaya calisip cokuyordu.
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                log_level="warning", access_log=False, log_config=None)
        self._server = uvicorn.Server(config)
        self._server_thread = threading.Thread(target=self._server.run, name="panel",
                                               daemon=True)
        self._server_thread.start()

    def open_panel(self) -> None:
        webbrowser.open(self.url)

    # --- eylemler ------------------------------------------------------
    def scan_now(self) -> bool:
        """Bekleyen turu one alir. Tarama zaten calisiyorsa False."""
        return bool(self.scanner and self.scanner.trigger())

    def snapshot(self) -> dict:
        if not self.scanner:
            return {"enabled": False, "phase": "off", "running": False,
                    "seconds_to_next": None, "next_kind": "", "last_new": 0,
                    "last_updated": 0, "last_closed": 0, "total_runs": 0, "errors": []}
        return self.scanner.snapshot()

    def stats(self) -> dict:
        from ..config import resolve_path
        from ..storage import Storage

        store = Storage(resolve_path(self.config["database"]))
        try:
            return store.stats()
        finally:
            store.close()

    def export_excel(self):
        """Panelde gorunen ilanlari Excel'e yazar; olusan dosyanin yolunu doner."""
        from ..config import load_keywords, resolve_path
        from ..export import default_name, to_xlsx
        from ..storage import Storage

        store = Storage(resolve_path(self.config["database"]))
        try:
            rows = store.query(min_score=int(load_keywords().get("min_score", 0)),
                               order="score", limit=1000)
        finally:
            store.close()
        path = resolve_path(self.config.get("output_dir", "output")) / \
            default_name("sap_projeleri", "xlsx")
        to_xlsx(rows, path)
        return path
