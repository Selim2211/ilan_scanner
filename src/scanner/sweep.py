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
from .pipeline import (_inspect_link, active_profile_id, api_miss_close_streak,
                       api_verify_settings, build_client, verifier_for, verify_with_source)
from .sources import HttpClient
from .storage import Storage

log = logging.getLogger(__name__)

#: Varsayilanlar; config.yaml > freshness altindan ezilir
SWEEP_MAX = 500
SWEEP_WORKERS = 8
SWEEP_TIMEOUT = 15.0

#: API dogrulamasi kac ilanlik parcalar halinde islenir. Sayaclar ve iptal
#: kontrolu parca sonlarinda calisir; kucuk tutmak ilerleme cubugunu akici,
#: Durdur dugmesini tepkili yapar (ilan basina ~1.8 sn -> parca ~35 sn).
API_PARCA = 20


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

    def start(self, filters: dict, by: str = "", config: dict | None = None,
              full: bool = False) -> bool:
        """Turu baslatir. Zaten calisiyorsa False doner.

        `full=True`: panel dugmesi "Tumunu kontrol et" - `sweep_max` sinirini
        yok sayar, o an aktif olan HER ilani kontrol eder (bkz. _run). Zamanlanmis
        turlar ve CLI `sweep` komutu full=False ile eskisi gibi `sweep_max`'e
        uyar - onlar sik sik calisir, tek turda hepsine bakmaya gerek yok.
        """
        with self._lock:
            if self.state.phase == "running":
                return False
            self.state = SweepState(phase="running", started_at=_now(), by=by)
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run, name="temizlik", daemon=True,
                                        args=(dict(filters), config or self._config, full))
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
    def _run(self, filters: dict, config: dict | None, full: bool = False) -> None:
        config = config or load_config()
        limit, workers, _ = sweep_settings(config)
        store = Storage(self.db_path)
        client = sweep_client(config)
        try:
            if full:
                # "Tumunu kontrol et" EKRANDAKI filtreyi tarar (bkz. app.py
                # start_sweep). Kullanici ne filtreledigini goruyor ve onay
                # penceresinde ayni sayiyi okuyor; sweep_max burada sonucu eksik
                # birakan bir sinir olurdu.
                limit = max(limit, store.count(**filters))
            # `stale` sirasi: en uzun suredir kontrol edilmemis ilan basa gelir.
            # Tur basina `limit` ilan bakildigi icin varsayilan puan sirasiyla
            # her tur ayni ilanlar taranir, kuyruktakilere hic sira gelmezdi.
            rows = store.query(**filters, limit=limit, offset=0, order="stale")
            with self._lock:
                self.state.total = len(rows)
            log.info("temizlik turu: %s ilan kontrol edilecek", len(rows))
            self._sweep(store, client, rows, workers, active_profile_id(store), config,
                        full=full)
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
               profile_id: int, config: dict, full: bool = False) -> None:
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

        # Linki HER ZAMAN 403 donen kaynaklarin (Jooble - Cloudflare, bkz.
        # PROJECT.md "bilinen sinir") satirlari link kontrolune HIC gonderilmez:
        # 1329 istek atilip sifir bilgi aliniyordu. Bu satirlar dogrudan API
        # dogrulamasina duser. Yan fayda: "dogrulanamadi" sayaci artik gercekten
        # karar verilemeyenleri gosteriyor (once 1316 gibi sisik cikiyordu).
        atlanacak = set((config.get("freshness") or {}).get("verify_skip_sources") or [])
        link_bakilacak = [r for r in rows if r["source"] not in atlanacak]
        engellenenler: list = [r for r in rows if r["source"] in atlanacak]
        with self._lock:
            self.state.checked += len(engellenenler)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for row, verdict in zip(link_bakilacak, pool.map(kontrol, link_bakilacak)):
                if verdict is None:
                    continue
                if verdict.closed is None and verdict.blocked:
                    engellenenler.append(row)
                self._apply(store, row, verdict, kapananlar, dogrulananlar)
        if dogrulananlar:
            store.mark_verified(dogrulananlar)

        kapananlar += self._api_dogrula(store, engellenenler, config, full=full)
        if kapananlar:
            store.add_notifications(kapananlar, profile_id=profile_id)

    def _api_dogrula(self, store: Storage, rows: list, config: dict,
                     full: bool = False) -> list[dict]:
        """Linki acilamayan ilanlari kaynagin API'sine sorar (Jooble).

        Link kontrolunden AYRI ve SERI kosar: API kotasi paralel istekle zorlanmasin.
        """
        if not rows or self._cancel.is_set():
            return []
        # manual=True: elle baslatilan tur cok daha fazla ilana bakar (kullanici
        # bilerek basti, ilerlemeyi goruyor ve Durdur'a basabiliyor).
        limit, threshold = api_verify_settings(config, manual=True)
        kayip_esigi = api_miss_close_streak(config)
        freshness = config.get("freshness") or {}
        suspect_after = int(freshness.get("suspect_after_missing_scans", 2))
        close_immediately = bool(freshness.get("close_suspects_immediately", False))
        client = build_client(config)                 # hiz sinirli normal istemci
        kapananlar: list[dict] = []
        try:
            for source in sorted({r["source"] for r in rows}):
                kaynak_rows = [r for r in rows if r["source"] == source]
                if close_immediately:
                    # "KONTROL EDILIYOR" rozetindeki (missing_streak >= suspect_after)
                    # ilanlar API'ye hic sorulmadan kapatilir - bkz. pipeline.py
                    # _resolve_missing'deki ayni bayrak, gerekce orada.
                    hazir = [r for r in kaynak_rows
                            if int(r["missing_streak"] or 0) >= suspect_after]
                    hazir_fp = {r["fingerprint"] for r in hazir}
                    for row in hazir:
                        store.close_project(row["fingerprint"],
                                            "şüpheli, doğrudan kapatıldı")
                        kapananlar.append({
                            "kind": "closed", "fingerprint": row["fingerprint"],
                            "title": row["title"], "detail": "şüpheli, doğrudan kapatıldı",
                            "url": row["url"], "source": row["source"],
                            "score": row["score"] or 0, "work_mode": row["work_mode"] or ""})
                    if hazir:
                        with self._lock:
                            self.state.closed += len(hazir)
                    kaynak_rows = [r for r in kaynak_rows if r["fingerprint"] not in hazir_fp]

                checker = verifier_for(source, config, client)
                if checker is None:
                    continue
                # Elle baslatilan turda kaynak basina sinir UYGULANMAZ: kume
                # zaten kullanicinin ekranda filtreledigi kadar, kirpmak
                # "kontrol ettim" deyip yarisina bakmak olurdu.
                hedef = kaynak_rows if full else kaynak_rows[:limit]
                # PARCALI islenir. Tek seferde 150 ilan sorulunca ilan basina
                # ~1.8 sn'den ~4.5 dakika boyunca ne ilerleme cubugu kipirdiyor
                # ne de Durdur dugmesi ise yariyordu - kullanici turun takildigini
                # saniyordu. Parca sonlarinda hem sayaclar guncelleniyor hem
                # iptal bayragina bakiliyor.
                for bas in range(0, len(hedef), API_PARCA):
                    if self._cancel.is_set():
                        break
                    parca = hedef[bas:bas + API_PARCA]
                    kapatilan, yayinda, bilinmiyor = verify_with_source(
                        store, checker, parca, threshold, kayip_esigi=kayip_esigi)
                    with self._lock:
                        self.state.api_checked += len(parca)
                        self.state.closed += len(kapatilan)
                        self.state.alive += yayinda
                        # API de cevap veremediyse ilan yine "dogrulanamadi" sayilir
                        self.state.unverified -= (len(parca) - bilinmiyor)
                    for row in kapatilan:
                        kapananlar.append({
                            "kind": "closed", "fingerprint": row["fingerprint"],
                            "title": row["title"], "detail": f"{source} API'sinde artık yok",
                            "url": row["url"], "source": row["source"],
                            "score": row["score"] or 0, "work_mode": row["work_mode"] or ""})
                if self._cancel.is_set():
                    break
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
            store.close_project(fingerprint, "link kontrolü: ilan kapanmış")
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
