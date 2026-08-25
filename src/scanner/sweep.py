"""Temizlik turu: listedeki ilanlarin linkini acip kapananlari kapatir.

Zamanlayicinin dogrulamasi (`pipeline.verify_active`) sirayla ve tur basina
sinirli ilerliyor; buyuk listede bir ilana sira gunler sonra geliyor. Bu modul
kullanicinin O AN gordugu filtreye uyan ilanlari tek seferde, paralel olarak
kontrol eder.

Kaynak atlanmaz: linki dogrulanamayan kaynaklarda (Jooble bot duvari) sonuc
"dogrulanamadi" olarak raporlanir - kapatilmaz, sorunlu da isaretlenmez.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import load_config, resolve_path
from .pipeline import (_inspect_link, active_profile_id, api_verify_settings, build_client,
                       verifier_for, verify_with_source)
from .sources import HttpClient
from .storage import Storage

log = logging.getLogger(__name__)

#: Varsayilanlar; config.yaml > freshness altindan ezilir
SWEEP_MAX = 500
SWEEP_WORKERS = 8
SWEEP_TIMEOUT = 15.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def sweep_settings(config: dict) -> tuple[int, int, float]:
    """(en fazla ilan, es zamanli istek, tek link zaman asimi)"""
    freshness = config.get("freshness") or {}
    return (max(1, int(freshness.get("sweep_max", SWEEP_MAX))),
            max(1, int(freshness.get("sweep_workers", SWEEP_WORKERS))),
            float(freshness.get("sweep_timeout", SWEEP_TIMEOUT)))


def sweep_client(config: dict) -> HttpClient:
    """Kontrol istemcisi: hiz siniri ve retry yok (probe.py'daki mantik).

    Tarama istemcisi istek basina 2 sn bekliyor ve 3 kez deniyor; 200 ilan
    yarim saat surerdi. Paralellik `sweep_workers` ile siniri korur.
    """
    http = config.get("http") or {}
    _, _, timeout = sweep_settings(config)
    return HttpClient(
        user_agent=http.get("user_agent", "SAP-Proje-Radari/0.1"),
        timeout=timeout,
        rate_limit_seconds=0.0,
        max_retries=1,
    )


@dataclass
class SweepState:
    """Panelin /api/durum ucundan okudugu anlik durum."""

    phase: str = "idle"                  # idle | running | done | cancelled
    total: int = 0
    checked: int = 0
    closed: int = 0
    flagged: int = 0
    alive: int = 0
    unverified: int = 0                  # 403/ag hatasi: ilan hakkinda bilgi yok
    api_checked: int = 0                 # linki acilamayip kaynak API'sine sorulan ilan
    started_at: datetime | None = None
    finished_at: datetime | None = None
    by: str = ""                         # turu baslatan kullanici
    errors: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        def iso(value: datetime | None) -> str:
            return value.isoformat() if value else ""

        return {
            "phase": self.phase,
            "running": self.phase == "running",
            "total": self.total,
            "checked": self.checked,
            "closed": self.closed,
            "flagged": self.flagged,
            "alive": self.alive,
            "unverified": self.unverified,
            "api_checked": self.api_checked,
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "by": self.by,
            "errors": self.errors[-6:],
        }


class LinkSweeper:
    """Elle tetiklenen temizlik turunu arka planda kosturur (ayni anda bir tane)."""

    def __init__(self, db_path, config: dict | None = None):
        self.db_path = db_path
        self._config = config
        self.state = SweepState()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    # --- dis arayuz ----------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return self.state.snapshot()

    def start(self, filters: dict, by: str = "", config: dict | None = None) -> bool:
        """Turu baslatir. Zaten calisiyorsa False doner."""
        with self._lock:
            if self.state.phase == "running":
                return False
            self.state = SweepState(phase="running", started_at=_now(), by=by)
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, name="temizlik", daemon=True,
                                        args=(dict(filters), config or self._config))
        self._thread.start()
        return True

    def cancel(self) -> bool:
        """Suren turu durdurur; calisan yoksa False."""
        with self._lock:
            if self.state.phase != "running":
                return False
        self._cancel.set()
        return True

    def join(self, timeout: float | None = None) -> None:
        """Suren turun bitmesini bekler (iptal etmez)."""
        if self._thread:
            self._thread.join(timeout=timeout)

    def stop(self, timeout: float = 5.0) -> None:
        self._cancel.set()
        self.join(timeout=timeout)

    # --- ic islevler ---------------------------------------------------
    def _run(self, filters: dict, config: dict | None) -> None:
        config = config or load_config()
        limit, workers, _ = sweep_settings(config)
        store = Storage(self.db_path)
        client = sweep_client(config)
        try:
            rows = store.query(**filters, limit=limit, offset=0)
            with self._lock:
                self.state.total = len(rows)
            log.info("temizlik turu: %s ilan kontrol edilecek", len(rows))
            self._sweep(store, client, rows, workers, active_profile_id(store), config)
        except Exception as exc:  # noqa: BLE001 - tur cokerse panel takili kalmasin
            log.exception("temizlik turu basarisiz")
            with self._lock:
                self.state.errors.append(str(exc))
        finally:
            client.close()
            store.close()
            with self._lock:
                if self.state.phase == "running":
                    self.state.phase = "cancelled" if self._cancel.is_set() else "done"
                self.state.finished_at = _now()
                durum = self.state
            log.info("temizlik turu bitti: %s kontrol, %s kapandi, %s sorunlu, "
                     "%s API ile soruldu, %s dogrulanamadi", durum.checked, durum.closed,
                     durum.flagged, durum.api_checked, durum.unverified)

    def _sweep(self, store: Storage, client: HttpClient, rows: list, workers: int,
               profile_id: int, config: dict) -> None:
        """Linkleri paralel acar, SONUCU TEK THREAD'DE yazar.

        SQLite tek yazar ister; kontrol I/O bekledigi icin paralellik zaten
        oradan kazanc sagliyor.
        """
        kapananlar: list[dict] = []
        dogrulananlar: list[str] = []

        def kontrol(row):
            # Havuz tum isleri bir kerede kuyruga aliyor; iptal edilince siradaki
            # linklere hic GIDILMESIN diye kontrol burada.
            if self._cancel.is_set():
                return None
            return _inspect_link(client, row["url"])

        engellenenler: list = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for row, verdict in zip(rows, pool.map(kontrol, rows)):
                if verdict is None:
                    continue
                if verdict.closed is None and verdict.blocked:
                    engellenenler.append(row)
                self._apply(store, row, verdict, kapananlar, dogrulananlar)
        if dogrulananlar:
            store.mark_verified(dogrulananlar)

        kapananlar += self._api_dogrula(store, engellenenler, config)
        if kapananlar:
            store.add_notifications(kapananlar, profile_id=profile_id)

    def _api_dogrula(self, store: Storage, rows: list, config: dict) -> list[dict]:
        """Linki acilamayan ilanlari kaynagin API'sine sorar (Jooble).

        Link kontrolunden AYRI ve SERI kosar: API kotasi paralel istekle zorlanmasin.
        """
        if not rows or self._cancel.is_set():
            return []
        limit, threshold = api_verify_settings(config)
        client = build_client(config)                 # hiz sinirli normal istemci
        kapananlar: list[dict] = []
        try:
            for source in sorted({r["source"] for r in rows}):
                checker = verifier_for(source, config, client)
                if checker is None:
                    continue
                hedef = [r for r in rows if r["source"] == source][:limit]
                kapatilan, yayinda, bilinmiyor = verify_with_source(
                    store, checker, hedef, threshold)
                with self._lock:
                    self.state.api_checked += len(hedef)
                    self.state.closed += len(kapatilan)
                    self.state.alive += yayinda
                    # API de cevap veremediyse ilan yine "dogrulanamadi" sayilir
                    self.state.unverified -= (len(hedef) - bilinmiyor)
                for row in kapatilan:
                    kapananlar.append({
                        "kind": "closed", "fingerprint": row["fingerprint"],
                        "title": row["title"], "detail": f"{source} API'sinde artık yok",
                        "url": row["url"], "source": row["source"],
                        "score": row["score"] or 0, "work_mode": row["work_mode"] or ""})
        finally:
            client.close()
        return kapananlar

    def _apply(self, store: Storage, row, verdict, kapananlar: list[dict],
               dogrulananlar: list[str]) -> None:
        fingerprint = row["fingerprint"]
        with self._lock:
            self.state.checked += 1

        if verdict.closed is None:
            # 403 ya da ag hatasi: ilan hakkinda hicbir sey ogrenemedik.
            # `verified_at` YAZILMAZ - yoksa ilan "bakildi" sayilip otomatik
            # dogrulama sirasinin sonuna atilirdi.
            with self._lock:
                self.state.unverified += 1
            return

        dogrulananlar.append(fingerprint)
        if verdict.closed:
            store.close_project(fingerprint)
            kapananlar.append({"kind": "closed", "fingerprint": fingerprint,
                               "title": row["title"], "detail": "link kontrolü: ilan kapanmış",
                               "url": row["url"], "source": row["source"],
                               "score": row["score"] or 0, "work_mode": row["work_mode"] or ""})
            with self._lock:
                self.state.closed += 1
            log.info("kapandi (temizlik turu, HTTP %s): %s", verdict.status, row["title"])
            return

        # Yayinda. Elle konmus isaret ezilmesin: yalnizca otomatik isaretler guncellenir.
        elle = (row["flagged_by"] or "") not in ("", "otomatik")
        if verdict.problem and not elle:
            store.set_flag(fingerprint, verdict.problem)
            with self._lock:
                self.state.flagged += 1
        elif not verdict.problem and not elle and (row["quality_flag"] or ""):
            store.clear_flag(fingerprint)
        with self._lock:
            self.state.alive += 1


def run_sweep(filters: dict | None = None, limit: int | None = None,
              config: dict | None = None) -> SweepState:
    """CLI/test icin senkron tur: baslatir ve bitmesini bekler."""
    config = config or load_config()
    sweeper = LinkSweeper(resolve_path(config["database"]), config)
    filters = dict(filters or {})
    if limit:
        config = {**config, "freshness": {**(config.get("freshness") or {}), "sweep_max": limit}}
    sweeper.start(filters, by="cli", config=config)
    sweeper.join()                          # iptal etmeden bitmesini bekle
    return sweeper.state
