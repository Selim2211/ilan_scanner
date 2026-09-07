"""Bakim: gecici dosyalari ve eskimis kayitlari temizler.

Ayarlar > Bakim ekrani once `survey()` ile "ne kadar yer tutuyor" listesi gosterir,
kullanici onaylayinca `run()` siler. Kullanicinin ilan verisi (aktif ilanlar,
ayarlar, anahtarlar) hicbir gorevde silinmez.
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .config import load_config, resolve_path

log = logging.getLogger(__name__)

#: Bu yastan eski ciktilar/loglar temizlik adayidir
LOG_KEEP_DAYS = 14
EXPORT_KEEP_DAYS = 30
NOTIFICATION_KEEP_DAYS = 30


@dataclass
class Task:
    """Bir temizlik gorevi: ekranda kutucuk olarak gosterilir."""
    key: str
    title: str
    detail: str
    count: int = 0
    bytes: int = 0
    #: Varsayilan olarak isaretli gelsin mi (guvenli olanlar isaretli)
    safe: bool = True

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "detail": self.detail,
                "count": self.count, "bytes": self.bytes, "safe": self.safe,
                "human": human_size(self.bytes), "summary": self.summary}

    @property
    def summary(self) -> str:
        """Kutunun sag ustundeki rozet: ne kadar is var, tek bakista."""
        if not self.count and not self.bytes:
            return "temiz"
        if self.bytes and self.count > 1:
            return f"{self.count} öğe · {human_size(self.bytes)}"
        if self.bytes:
            return human_size(self.bytes)
        return f"{self.count} kayıt"


def human_size(size: int) -> str:
    if size <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _older_than(path: Path, days: int) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) > days * 86400
    except OSError:
        return False


def _pycache_dirs() -> list[Path]:
    """__pycache__ ve .pytest_cache - kaynaktan calisirken birikir, kurulu pakette olmaz."""
    root = paths.bundle_dir()
    found = [d for d in root.rglob("__pycache__") if d.is_dir()]
    for name in (".pytest_cache", ".ruff_cache", ".mypy_cache"):
        cache = root / name
        if cache.is_dir():
            found.append(cache)
    return found


def _old_logs() -> list[Path]:
    return [f for f in paths.log_dir().glob("*.log") if _older_than(f, LOG_KEEP_DAYS)]


def _old_exports(config: dict) -> list[Path]:
    out = resolve_path(config.get("output_dir", "output"))
    if not out.is_dir():
        return []
    files = []
    for pattern in ("*.xlsx", "*.csv"):
        files += [f for f in out.glob(pattern) if _older_than(f, EXPORT_KEEP_DAYS)]
    return files


def _temp_files(config: dict) -> list[Path]:
    """Yarim kalmis atomik yazma dosyalari ve mail onizlemeleri."""
    found: list[Path] = []
    data = paths.data_dir()
    found += [f for f in data.rglob("*.tmp") if f.is_file()]
    out = resolve_path(config.get("output_dir", "output"))
    if out.is_dir():
        found += [f for f in out.glob("mail_onizleme*.html") if f.is_file()]
        found += [f for f in out.glob("*.log") if f.is_file() and _older_than(f, LOG_KEEP_DAYS)]
    return found


def _files_size(files: list[Path]) -> int:
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def survey(config: dict | None = None) -> list[Task]:
    """Neyin ne kadar yer tuttugunu hesaplar; hicbir sey silmez."""
    config = config or load_config()
    from .storage import Storage

    caches = _pycache_dirs()
    logs = _old_logs()
    exports = _old_exports(config)
    temps = _temp_files(config)

    # Sayilar SILINECEK olani gosterir, toplami degil: her gorev kendi run()
    # olcutuyle ayni sorguyu kullanir, yoksa "1467 kayit" yazip 3 tane siliyordu.
    from .auth import Auth

    db_path = resolve_path(config["database"])
    store = Storage(db_path)
    try:
        eski_bildirim = store.count_notifications_before(NOTIFICATION_KEEP_DAYS)
        toplam_bildirim = len(store.notifications(limit=100000))
        kapali = store.count_closed()
        kazanilabilir = store.reclaimable_bytes()
        db_size = store.db_size()
    finally:
        store.close()

    auth = Auth(db_path)
    try:
        dolmus_oturum = auth.expired_session_count()
    finally:
        auth.close()

    return [
        Task("pycache", "Python önbellek klasörleri",
             "__pycache__ ve test önbellekleri — silinince ilk çalıştırmada yeniden oluşur.",
             len(caches), sum(_dir_size(d) for d in caches)),
        Task("temp", "Geçici dosyalar",
             "Yarım kalmış yazma dosyaları (.tmp) ve mail önizlemeleri.",
             len(temps), _files_size(temps)),
        Task("logs", f"{LOG_KEEP_DAYS} günden eski günlükler",
             "Eski çalışma kayıtları; sorun ararken işe yarar, silmek zorunlu değil.",
             len(logs), _files_size(logs)),
        Task("exports", f"{EXPORT_KEEP_DAYS} günden eski Excel çıktıları",
             "Dışa aktardığınız eski dosyalar. Saklamak istediğiniz varsa önce kopyalayın.",
             len(exports), _files_size(exports), safe=False),
        Task("notifications", f"{NOTIFICATION_KEEP_DAYS} günden eski bildirimler",
             f"Kayıtlı {toplam_bildirim} bildirimin {eski_bildirim} tanesi bu kadar eski.",
             eski_bildirim, 0),
        Task("closed", "Kapanmış ilanların hepsini sil",
             f"Kapanmış {kapali} ilan var. Kapanan ilana başvurulamaz, listede yer kaplar. "
             "Aktif ilanlara dokunulmaz.", kapali, 0, safe=False),
        Task("sessions", "Süresi dolmuş oturumlar",
             "Kullanılmayan giriş anahtarları.", dolmus_oturum, 0),
        Task("vacuum", "Veritabanını sıkıştır",
             f"Dosya şu an {human_size(db_size)}. "
             + (f"Silinen kayıtların bıraktığı {human_size(kazanilabilir)} boşluk geri verilebilir."
                if kazanilabilir else "Şu an geri verilecek boşluk yok."),
             1 if kazanilabilir else 0, kazanilabilir),
    ]


def run(selected: list[str], config: dict | None = None) -> list[str]:
    """Secilen gorevleri calistirir; kullaniciya gosterilecek sonuc satirlarini doner."""
    config = config or load_config()
    from .auth import Auth
    from .storage import Storage

    chosen = set(selected)
    report: list[str] = []
    freed = 0

    if "pycache" in chosen:
        silinen = 0
        for folder in _pycache_dirs():
            freed += _dir_size(folder)
            try:
                shutil.rmtree(folder, ignore_errors=True)
                silinen += 1
            except OSError as exc:
                log.warning("%s silinemedi: %s", folder, exc)
        report.append(f"{silinen} önbellek klasörü silindi.")

    for key, files, label in (("temp", _temp_files(config), "geçici dosya"),
                              ("logs", _old_logs(), "eski günlük"),
                              ("exports", _old_exports(config), "eski çıktı")):
        if key not in chosen:
            continue
        silinen = 0
        for f in files:
            try:
                freed += f.stat().st_size
                f.unlink()
                silinen += 1
            except OSError as exc:
                log.warning("%s silinemedi: %s", f, exc)
        report.append(f"{silinen} {label} silindi.")

    db_path = resolve_path(config["database"])
    if {"notifications", "closed", "vacuum"} & chosen:
        store = Storage(db_path)
        try:
            if "notifications" in chosen:
                report.append(f"{store.prune_notifications(NOTIFICATION_KEEP_DAYS)} eski bildirim silindi.")
            if "closed" in chosen:
                report.append(f"{store.prune_closed()} kapanmış ilan silindi.")
            if "vacuum" in chosen:
                kazanc = store.vacuum()
                freed += kazanc
                report.append(f"Veritabanı sıkıştırıldı ({human_size(kazanc)} kazanıldı).")
        finally:
            store.close()

    if "sessions" in chosen:
        auth = Auth(db_path)
        try:
            report.append(f"{auth.purge_sessions()} süresi dolmuş oturum silindi.")
            budanan = auth.prune_login_events(keep_per_user=50)
            if budanan:
                report.append(f"{budanan} eski giriş kaydı silindi.")
        finally:
            auth.close()

    if freed:
        report.append(f"Toplam {human_size(freed)} yer açıldı.")
    return report or ["Seçili görev yok."]
