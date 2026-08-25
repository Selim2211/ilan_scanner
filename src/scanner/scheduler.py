"""Arka plan tarama dongusu: panel acikken veri kendiliginden tazelenir.

Panel yalnizca veritabanini okuyordu; tarama ise elle ya da gunluk gorevle
calisiyordu. Bu yuzden acik duran panelde saatler once kapanmis ilanlar
gorunuyordu. Bu modul, panelle ayni surecte donen bir is parcaciginda
periyodik `scan` + `verify` calistirir ve durumunu panele acar.

Tek is parcacigi kullanilir: iki tarama ayni anda calisip ayni SQLite
dosyasina yazmaz.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import load_config
from .pipeline import rescore_all, run_scan, verify_active

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SchedulerState:
    """Panelin /api/durum ucundan okudugu anlik durum."""

    enabled: bool = False
    phase: str = "idle"                 # idle | quick | scanning | verifying | sleeping
    interval_minutes: int = 30
    quick_minutes: int = 10
    started_at: datetime | None = None  # calisan turun baslangici
    last_finished: datetime | None = None
    next_run: datetime | None = None
    last_new: int = 0
    last_updated: int = 0
    last_closed: int = 0
    last_fetched: int = 0
    last_verified: int = 0
    total_runs: int = 0
    quick_runs: int = 0
    next_kind: str = "quick"            # sonraki turda hizli mi tam mi
    current_profile: str = ""           # tam turda o an taranan arama profili
    errors: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        def iso(value: datetime | None) -> str:
            return value.isoformat() if value else ""

        remaining = None
        if self.next_run and self.phase in ("sleeping", "idle"):
            remaining = max(0, int((self.next_run - _now()).total_seconds()))
        return {
            "enabled": self.enabled,
            "phase": self.phase,
            "running": self.phase in ("scanning", "verifying", "quick"),
            "interval_minutes": self.interval_minutes,
            "quick_minutes": self.quick_minutes,
            "next_kind": self.next_kind,
            "started_at": iso(self.started_at),
            "last_finished": iso(self.last_finished),
            "next_run": iso(self.next_run),
            "seconds_to_next": remaining,
            "last_new": self.last_new,
            "last_updated": self.last_updated,
            "last_closed": self.last_closed,
            "last_fetched": self.last_fetched,
            "last_verified": self.last_verified,
            "total_runs": self.total_runs,
            "quick_runs": self.quick_runs,
            "current_profile": self.current_profile,
            "errors": self.errors[-6:],
        }


class BackgroundScanner:
    """Iki hizli tempo: her `quick_interval_minutes`'ta hafif tur (yeni ilan avi),
    her `interval_minutes`'ta tam tur (tum kaynaklar + kapanan tespiti + link kontrolu).

    `trigger()` uykuyu boler: paneldeki "Şimdi tara" dugmesi bunu cagirir.
    """

    def __init__(self, interval_minutes: int = 30, verify_limit: int = 40,
                 verify_every: int = 2, run_on_start: bool = True,
                 quick_minutes: int = 10):
        full_minutes = max(5, int(interval_minutes))
        quick = max(2, min(int(quick_minutes), full_minutes))
        self.interval = full_minutes * 60
        self.quick_interval = quick * 60
        #: kac hizli turda bir tam tur yapilir
        self.full_every = max(1, round(full_minutes / quick))
        self.verify_limit = int(verify_limit)
        self.verify_every = max(0, int(verify_every))
        self.run_on_start = run_on_start
        self.state = SchedulerState(enabled=True, interval_minutes=full_minutes,
                                    quick_minutes=quick)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        #: Ayarlardan kapatilinca thread durdurulmaz, sadece duraklatilir. Boylece
        #: paneli masaustu uygulamasi besliyorken (external_scheduler) de calisir.
        self._active = threading.Event()
        self._active.set()
        #: "Şimdi tara" duraklatilmisken de bir tur calistirsin
        self._force = threading.Event()

    # --- ayar degisikligi ----------------------------------------------
    def reconfigure(self, interval_minutes: int | None = None, quick_minutes: int | None = None,
                    verify_limit: int | None = None, verify_every: int | None = None) -> None:
        """Aralik/limit degisikligini yeniden baslatmadan uygular."""
        with self._lock:
            full = max(5, int(interval_minutes if interval_minutes is not None
                              else self.state.interval_minutes))
            quick = max(2, min(int(quick_minutes if quick_minutes is not None
                                   else self.state.quick_minutes), full))
            self.interval = full * 60
            self.quick_interval = quick * 60
            self.full_every = max(1, round(full / quick))
            self.state.interval_minutes = full
            self.state.quick_minutes = quick
            if verify_limit is not None:
                self.verify_limit = int(verify_limit)
            if verify_every is not None:
                self.verify_every = max(0, int(verify_every))
        # uykuyu bol: yeni periyot hemen kurulsun, eski beklemeye takilmasin
        self._wake.set()
        log.info("tarama ayarlari guncellendi: hizli %s dk, tam %s dk",
                 self.state.quick_minutes, self.state.interval_minutes)

    def set_enabled(self, enabled: bool) -> None:
        """Taramayi duraklatir/devam ettirir (thread yasam dongusune dokunmaz)."""
        with self._lock:
            self.state.enabled = bool(enabled)
        if enabled:
            self._active.set()
            self._wake.set()
        else:
            self._active.clear()
        log.info("arka plan taramasi %s", "acildi" if enabled else "duraklatildi")

    # --- yasam dongusu -------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tarama", daemon=True)
        self._thread.start()
        log.info("arka plan taramasi acik: hizli tur %s dk, tam tur %s dk",
                 self.state.quick_minutes, self.state.interval_minutes)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def trigger(self) -> bool:
        """Elle tarama istegi. Tarama zaten calisiyorsa False doner.

        Duraklatilmis olsa da calisir: kullanici dugmeye basmissa taramayi istiyordur.
        """
        if self.state.phase in ("scanning", "verifying", "quick"):
            return False
        self._force.set()
        self._wake.set()
        return True

    def snapshot(self) -> dict:
        with self._lock:
            return self.state.snapshot()

    # --- ic islevler ---------------------------------------------------
    def _loop(self) -> None:
        if not self.run_on_start or not self._active.is_set():
            self._sleep_until_next()
        ticks = 0
        while not self._stop.is_set():
            forced = self._force.is_set()
            self._force.clear()
            if self._active.is_set() or forced:
                # duraklatilmisken elle istenen tarama hep tam tur olsun
                full = forced or (ticks % self.full_every == 0)
                self._run_once(full=full)
                ticks += 1
            if self._stop.is_set():
                break
            self._sleep_until_next(next_full=(ticks % self.full_every == 0))

    def _sleep_until_next(self, next_full: bool = False) -> None:
        paused = not self._active.is_set()
        with self._lock:
            self.state.phase = "paused" if paused else "sleeping"
            self.state.next_kind = "full" if next_full else "quick"
            self.state.next_run = None if paused else _now() + timedelta(seconds=self.quick_interval)
        self._wake.wait(timeout=self.quick_interval)
        self._wake.clear()

    def _apply_result(self, result, etiket: str, profil: str = "") -> None:
        """Tur sonucunu duruma yazar. Cok profilli turda sayilar TOPLANIR."""
        with self._lock:
            self.state.last_fetched += result.fetched
            self.state.last_new += result.new
            self.state.last_updated += result.updated
            self.state.last_closed += result.closed
            self.state.errors += [f"{src}: {err}" for src, err in result.errors]
        log.info("%s tarama%s: %s yeni, %s guncel, %s kapandi", etiket,
                 f" ({profil})" if profil else "", result.new, result.updated, result.closed)

    def _scan_all_profiles(self) -> None:
        """Her arama profilini KENDI ayarlariyla tarar, sonra etkin profile geri doner.

        `projects.score` tek kolon: son tarayan profilin puani kalir. Tur sonunda
        etkin profille yeniden puanlanmazsa kullanici baska profilin puanlarini
        gorurdu. Rescore aga cikmaz, saniyeler surer.
        """
        from .config import load_keywords, resolve_path
        from .profiles import Profiles, settings_for

        with self._lock:                      # sayaclar bu turda sifirdan toplanir
            self.state.last_fetched = self.state.last_new = 0
            self.state.last_updated = self.state.last_closed = 0
            self.state.errors = []

        db = resolve_path(load_config()["database"])
        profiles = Profiles(db)
        try:
            hedefler = profiles.scannable()
        finally:
            profiles.close()

        if not hedefler:                      # profil kurulmamis: bugunku davranis
            self._apply_result(run_scan(quick=False), "tam")
            return

        for row in hedefler:
            with self._lock:
                self.state.current_profile = row["name"]
            try:
                config, keywords = settings_for(row)
                self._apply_result(run_scan(quick=False, config=config, keywords=keywords,
                                            profile_id=int(row["id"])), "tam", row["name"])
            except Exception as exc:  # noqa: BLE001 - bir profil digerlerini durdurmasin
                log.exception("profil taramasi hata verdi: %s", row["name"])
                with self._lock:
                    self.state.errors.append(f"{row['name']}: {type(exc).__name__}: {exc}")

        with self._lock:
            self.state.current_profile = ""
        try:
            rescore_all(load_config(), load_keywords())
        except Exception:  # noqa: BLE001 - puanlar bir sonraki turda duzelir
            log.exception("tur sonu yeniden puanlama basarisiz")

    def _run_once(self, full: bool = True) -> None:
        """Bir tur: hizli turda sadece yeni ilan avi, tam turda kapanan tespiti de var.

        Hizli tur yalnizca ETKIN profili tarar - onunde duran liste taze kalsin,
        maliyet artmasin. Tam tur butun profilleri sirayla tarar; yoksa etkin
        olmayan profilin sorgulari hic calismaz ve o liste sessizce bayatlar.
        """
        with self._lock:
            self.state.phase = "scanning" if full else "quick"
            self.state.started_at = _now()
        try:
            if full:
                self._scan_all_profiles()
            else:
                self._apply_result(run_scan(quick=True), "hizli")
        except Exception as exc:  # noqa: BLE001 - dongu bir hatada olmemeli
            log.exception("arka plan taramasi hata verdi")
            with self._lock:
                self.state.errors = [f"tarama: {type(exc).__name__}: {exc}"]
                self.state.current_profile = ""

        runs = self.state.total_runs + (1 if full else 0)
        if full and self.verify_every and self.verify_limit and runs % self.verify_every == 0:
            with self._lock:
                self.state.phase = "verifying"
            try:
                checked, closed = verify_active(limit=self.verify_limit, min_hours=6)
                with self._lock:
                    self.state.last_verified = checked
                    self.state.last_closed += closed
                log.info("link kontrolu: %s bakildi, %s kapandi", checked, closed)
            except Exception as exc:  # noqa: BLE001
                log.exception("link kontrolu hata verdi")
                with self._lock:
                    self.state.errors.append(f"dogrulama: {type(exc).__name__}: {exc}")

        with self._lock:
            self.state.total_runs = runs
            if not full:
                self.state.quick_runs += 1
            self.state.last_finished = _now()
            self.state.phase = "sleeping"


def from_config(config: dict | None = None, override_minutes: int | None = None,
                enabled: bool | None = None,
                always: bool = False) -> BackgroundScanner | None:
    """config.yaml -> auto_scan ayarlarindan zamanlayici kurar (kapaliysa None).

    `always=True` kapaliyken de nesneyi kurar ama duraklatilmis baslatir: panel
    boylece ayarlardan acilinca yeniden baslatilmaya gerek kalmadan calisir.
    """
    config = config or load_config()
    auto = config.get("auto_scan") or {}
    active = auto.get("enabled", True) if enabled is None else enabled
    if not active and not always:
        return None
    freshness = config.get("freshness") or {}
    scanner = BackgroundScanner(
        interval_minutes=override_minutes or int(auto.get("interval_minutes", 30)),
        quick_minutes=int(auto.get("quick_interval_minutes", 10)),
        verify_limit=int(auto.get("verify_limit", freshness.get("verify_limit", 40))),
        verify_every=int(auto.get("verify_every_n_scans", 2)),
        run_on_start=bool(auto.get("run_on_start", True)),
    )
    if not active:
        scanner.set_enabled(False)
    return scanner
