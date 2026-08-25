"""SQLite depolama: projeler, calistirma gecmisi, bildirimler."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .models import Project
from .normalize import budget_daily, fold, infer_period, parse_budget, parse_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    fingerprint     TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT,
    location        TEXT,
    country         TEXT,
    work_mode       TEXT DEFAULT 'unknown',
    remote_percent  INTEGER,
    engagement      TEXT,
    is_contract     INTEGER,
    duration        TEXT,
    starts_at       TEXT,
    budget_raw      TEXT,
    budget_amount   REAL,                   -- butce metninden cikarilan tutar (orijinal birim)
    budget_daily    REAL,                   -- karsilastirilabilir gunluk tahmin; sabit ucrette NULL
    currency        TEXT,
    description     TEXT,
    skills          TEXT,
    posted_at       TEXT,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    keywords_hit    TEXT,
    score           INTEGER DEFAULT 0,
    also_seen_on    TEXT,
    search_text     TEXT,
    status          TEXT DEFAULT 'new',
    note            TEXT,
    notified_at     TEXT,
    is_supply       INTEGER DEFAULT 0,      -- 1 = arz tarafi (hizmet ilani)
    is_active       INTEGER DEFAULT 1,      -- 0 = ilan kapanmis
    missing_streak  INTEGER DEFAULT 0,      -- ust uste kac taramada kaynak listesinde yok
    last_missing_at TEXT,                   -- listeden ilk dustugu an
    api_miss_streak INTEGER DEFAULT 0,      -- kaynak API'sinde ust uste kac kez bulunamadi
    closed_at       TEXT,
    verified_at     TEXT,                   -- linki en son ne zaman acilip kontrol edildi
    quality_flag    TEXT,                   -- '' / NULL = temiz, 'problem' = ilanda sikinti var
    flag_reason     TEXT,                   -- neden sorunlu: bolge kisitli, giris gerekiyor...
    flagged_at      TEXT,
    flagged_by      TEXT                    -- kullanici adi ya da 'otomatik'
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    source      TEXT NOT NULL,
    query       TEXT,
    fetched     INTEGER DEFAULT 0,
    inserted    INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'ok',
    error       TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,            -- new | closed | error
    fingerprint TEXT,
    title       TEXT NOT NULL,
    detail      TEXT,
    url         TEXT,
    source      TEXT,
    score       INTEGER DEFAULT 0,
    work_mode   TEXT,
    read_at     TEXT,
    profile_id  INTEGER DEFAULT 0         -- hangi arama profilinin turunda dustu
);

-- Kullanicinin ilan uzerindeki isaretleri ARAMA PROFILINE gore tutulur: ayni ilan
-- bir profilde takipte, digerinde gizli olabilir. profile_id 0 = profil secili degil.
CREATE TABLE IF NOT EXISTS profile_status (
    profile_id  INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    status      TEXT NOT NULL,            -- shortlist | applied | ignored
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (profile_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

#: Indeksler ayri tutuluyor: eski bir veritabaninda kolon eksikse once migration
#: calismali, yoksa "no such column" hatasi aliniyor.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_pr_score ON projects(score DESC);
CREATE INDEX IF NOT EXISTS idx_pr_mode ON projects(work_mode);
CREATE INDEX IF NOT EXISTS idx_pr_active ON projects(is_active);
CREATE INDEX IF NOT EXISTS idx_pr_seen ON projects(first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_pr_verified ON projects(verified_at);
CREATE INDEX IF NOT EXISTS idx_pr_posted ON projects(posted_at);
CREATE INDEX IF NOT EXISTS idx_pr_budget ON projects(budget_daily DESC);
CREATE INDEX IF NOT EXISTS idx_pr_flag ON projects(quality_flag);
CREATE INDEX IF NOT EXISTS idx_pr_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_nt_created ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nt_unread ON notifications(read_at);
CREATE INDEX IF NOT EXISTS idx_nt_profile ON notifications(profile_id);
CREATE INDEX IF NOT EXISTS idx_ps_lookup ON profile_status(profile_id, status);
"""

#: status: new | shortlist (takipte) | applied (basvuruldu) | mailed | ignored

MIGRATIONS = {
    "is_supply": "ALTER TABLE projects ADD COLUMN is_supply INTEGER DEFAULT 0",
    "is_active": "ALTER TABLE projects ADD COLUMN is_active INTEGER DEFAULT 1",
    "missing_streak": "ALTER TABLE projects ADD COLUMN missing_streak INTEGER DEFAULT 0",
    "closed_at": "ALTER TABLE projects ADD COLUMN closed_at TEXT",
    "verified_at": "ALTER TABLE projects ADD COLUMN verified_at TEXT",
    "last_missing_at": "ALTER TABLE projects ADD COLUMN last_missing_at TEXT",
    "budget_amount": "ALTER TABLE projects ADD COLUMN budget_amount REAL",
    "budget_daily": "ALTER TABLE projects ADD COLUMN budget_daily REAL",
    "quality_flag": "ALTER TABLE projects ADD COLUMN quality_flag TEXT",
    "flag_reason": "ALTER TABLE projects ADD COLUMN flag_reason TEXT",
    "flagged_at": "ALTER TABLE projects ADD COLUMN flagged_at TEXT",
    "flagged_by": "ALTER TABLE projects ADD COLUMN flagged_by TEXT",
    "api_miss_streak": "ALTER TABLE projects ADD COLUMN api_miss_streak INTEGER DEFAULT 0",
}


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: Siralama/filtrede kullanilan ortak ifadeler
_DATE_EXPR = "COALESCE(posted_at, first_seen_at)"
_MODE_EXPR = ("CASE work_mode WHEN 'remote' THEN 0 WHEN 'hybrid' THEN 1 "
              "WHEN 'onsite' THEN 3 ELSE 2 END")


def _day_start(value: str | None) -> Optional[str]:
    """'2026-08-19' -> gunun basi. Gecersiz metin filtreyi sessizce dusurur."""
    if not value:
        return None
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return day.replace(tzinfo=timezone.utc).isoformat()


def _day_after(value: str | None) -> Optional[str]:
    """Ust sinir icin ertesi gunun basi (aralik ust ucu HARIC karsilastirilir)."""
    start = _day_start(value)
    if start is None:
        return None
    return (datetime.fromisoformat(start) + timedelta(days=1)).isoformat()


def _budget_columns(budget_raw: str | None, currency: str | None,
                    rates: dict[str, float] | None = None,
                    engagement: str | None = None) -> tuple[Optional[float], Optional[float]]:
    """(budget_amount, budget_daily) - siralanabilir butce kolonlari."""
    amount, period, code = parse_budget(budget_raw, currency or "")
    period = infer_period(amount, period, engagement or "")
    return amount, budget_daily(amount, period, code, rates)


def _search_text(project: Project) -> str:
    """Aramada kullanilan sadelestirilmis metin (SQLite lower() Turkce harfi cevirmiyor)."""
    return fold(" ".join([project.title, project.company, project.location, project.country,
                          project.engagement, project.description, " ".join(project.skills)]))


class Storage:
    """projects = ilanlar, runs = calistirma gecmisi, notifications = bildirimler."""

    def __init__(self, path: Path, rates: dict[str, float] | None = None):
        self.path = Path(path)
        self.rates = rates or {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        # Arka plan taramasi yazarken panel de yazabiliyor; kilit hemen hata vermesin.
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        added = self._migrate()
        self.conn.executescript(INDEXES)
        if "budget_daily" in added:
            self._backfill_budget()
        self._migrate_marks()
        self.conn.commit()

    def _migrate(self) -> set[str]:
        """Eski veritabanlarina sonradan eklenen kolonlari tamamlar; eklenenleri doner."""
        existing = {row[1] for row in self.conn.execute("PRAGMA table_info(projects)")}
        added = set()
        for column, statement in MIGRATIONS.items():
            if column not in existing:
                self.conn.execute(statement)
                added.add(column)

        notif = {row[1] for row in self.conn.execute("PRAGMA table_info(notifications)")}
        if "profile_id" not in notif:
            self.conn.execute("ALTER TABLE notifications ADD COLUMN profile_id INTEGER DEFAULT 0")
        return added

    def _migrate_marks(self) -> None:
        """Profil oncesi konan isaretleri (takip/basvuru/gizli) profil tablosuna tasir.

        Bir kez calisir. Isaretler HEM profilsiz gorunume HEM de kayitli her profile
        kopyalanir: kullanici profil actiginda eski takip listesini kaybetmesin.
        """
        done = self.conn.execute("SELECT value FROM meta WHERE key = 'marks_migrated'").fetchone()
        if done:
            return
        rows = list(self.conn.execute(
            "SELECT fingerprint, status FROM projects "
            "WHERE status IN ('shortlist', 'applied', 'ignored')"))
        hedefler = [0]
        try:
            hedefler += [r[0] for r in self.conn.execute("SELECT id FROM search_profiles")]
        except sqlite3.OperationalError:
            pass                      # profil tablosu henuz yok: yalnizca profilsiz gorunum
        now = _now()
        self.conn.executemany(
            "INSERT OR IGNORE INTO profile_status (profile_id, fingerprint, status, updated_at) "
            "VALUES (?,?,?,?)",
            [(pid, fp, status, now) for pid in hedefler for fp, status in rows])
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('marks_migrated', ?)",
                          (now,))
        self.conn.commit()

    def _backfill_budget(self) -> int:
        """Kolon yeni eklendiginde eski satirlarin butcesini bir kez cozer."""
        rows = list(self.conn.execute(
            "SELECT fingerprint, budget_raw, currency, engagement FROM projects "
            "WHERE COALESCE(budget_raw, '') != ''"))
        updates = []
        for row in rows:
            amount, daily = _budget_columns(row["budget_raw"], row["currency"],
                                            self.rates, row["engagement"])
            if amount is not None:
                updates.append((amount, daily, row["fingerprint"]))
        self.conn.executemany(
            "UPDATE projects SET budget_amount=?, budget_daily=? WHERE fingerprint=?", updates)
        self.conn.commit()
        return len(updates)

    def close(self) -> None:
        self.conn.close()

    # --- yazma -------------------------------------------------------
    def upsert(self, projects: Iterable[Project]) -> tuple[int, int, list[Project]]:
        """(yeni, guncellenen, yeni_eklenenler) doner."""
        new_items: list[Project] = []
        updated = 0
        now = _now()
        with closing(self.conn.cursor()) as cur:
            for project in projects:
                # butce metni guncellemede degisebiliyor, iki dalda da hesaplanir
                amount, daily = _budget_columns(project.budget_raw, project.currency,
                                                self.rates, project.engagement)
                cur.execute("SELECT is_active FROM projects WHERE fingerprint = ?",
                            (project.fingerprint,))
                row = cur.fetchone()
                if row is not None:
                    cur.execute(
                        "UPDATE projects SET last_seen_at=?, score=?, keywords_hit=?, also_seen_on=?, "
                        "search_text=?, skills=?, work_mode=?, remote_percent=?, engagement=?, "
                        "is_contract=?, duration=?, starts_at=?, budget_raw=?, budget_amount=?, "
                        "budget_daily=?, posted_at=?, "
                        "is_active=1, missing_streak=0, last_missing_at=NULL, closed_at=NULL, "
                        "description = CASE WHEN length(?) > length(COALESCE(description, '')) "
                        "THEN ? ELSE description END WHERE fingerprint = ?",
                        (now, project.score, json.dumps(project.keywords_hit, ensure_ascii=False),
                         json.dumps(project.also_seen_on, ensure_ascii=False), _search_text(project),
                         json.dumps(project.skills, ensure_ascii=False), project.work_mode,
                         project.remote_percent, project.engagement,
                         None if project.is_contract is None else int(project.is_contract),
                         project.duration, project.starts_at, project.budget_raw, amount, daily,
                         _iso(project.posted_at), project.description, project.description,
                         project.fingerprint),
                    )
                    updated += 1
                else:
                    cur.execute(
                        "INSERT INTO projects (fingerprint, source, source_id, url, title, company, "
                        "location, country, work_mode, remote_percent, engagement, is_contract, "
                        "duration, starts_at, budget_raw, budget_amount, budget_daily, currency, "
                        "description, skills, posted_at, "
                        "first_seen_at, last_seen_at, keywords_hit, score, also_seen_on, search_text, "
                        "status, is_supply, is_active, missing_streak) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,1,0)",
                        (project.fingerprint, project.source, project.source_id, project.url,
                         project.title, project.company, project.location, project.country,
                         project.work_mode, project.remote_percent, project.engagement,
                         None if project.is_contract is None else int(project.is_contract),
                         project.duration, project.starts_at, project.budget_raw, amount, daily,
                         project.currency,
                         project.description, json.dumps(project.skills, ensure_ascii=False),
                         _iso(project.posted_at), now, now,
                         json.dumps(project.keywords_hit, ensure_ascii=False), project.score,
                         json.dumps(project.also_seen_on, ensure_ascii=False), _search_text(project),
                         int(project.is_supply)),
                    )
                    new_items.append(project)
        self.conn.commit()
        return len(new_items), updated, new_items

    def mark_missing(self, source: str, seen_fingerprints: Iterable[str]) -> None:
        """Bu taramada kaynakta gorulmeyen ilanlarin sayacini artirir.

        KAPATMAZ. Kaynaklar sayfalanmis bir pencere donduruyor: yeni ilan gelince
        eski ilan pencereden dusuyor ama yayinda kaliyor. Yokluk = "supheli",
        kapatma karari link kontrolune birakilir (bkz. suspects / close_project).
        """
        seen = list(seen_fingerprints)
        placeholders = ",".join("?" * len(seen)) if seen else "''"
        self.conn.execute(
            f"UPDATE projects SET missing_streak = missing_streak + 1, last_missing_at = ? "
            f"WHERE source = ? AND is_active = 1 AND fingerprint NOT IN ({placeholders})",
            [_now(), source] + seen)
        self.conn.commit()

    def suspects(self, source: str | None = None, min_streak: int = 2,
                 limit: int = 60) -> list[sqlite3.Row]:
        """Kaynak listesinden dusmus, kapanip kapanmadigi link kontrolu bekleyen ilanlar."""
        sql = ("SELECT * FROM projects WHERE is_active = 1 AND missing_streak >= ?"
               + (" AND source = ?" if source else "")
               + " ORDER BY missing_streak DESC, score DESC LIMIT ?")
        params: list = [min_streak] + ([source] if source else []) + [limit]
        return list(self.conn.execute(sql, params))

    def bump_api_miss(self, fingerprints: Iterable[str]) -> dict[str, int]:
        """Kaynak API'sinde bulunamayan ilanlarin sayacini artirir; yeni degerleri doner.

        `missing_streak`ten ayri tutuluyor: o "genel aramada gorunmedi" demek,
        bu ise "ilanin KENDI basligiyla soruldu, indekste yok" demek - daha guclu
        bir kanit ve kendi esigi var.
        """
        fps = list(fingerprints)
        if not fps:
            return {}
        placeholders = ",".join("?" * len(fps))
        self.conn.execute(
            f"UPDATE projects SET api_miss_streak = COALESCE(api_miss_streak, 0) + 1 "
            f"WHERE fingerprint IN ({placeholders})", fps)
        self.conn.commit()
        rows = self.conn.execute(
            f"SELECT fingerprint, api_miss_streak FROM projects "
            f"WHERE fingerprint IN ({placeholders})", fps)
        return {row["fingerprint"]: row["api_miss_streak"] or 0 for row in rows}

    def clear_api_miss(self, fingerprints: Iterable[str]) -> None:
        """API'de gorulen ilan yayindadir: sayac sifirlanir."""
        self.conn.executemany("UPDATE projects SET api_miss_streak = 0 WHERE fingerprint = ?",
                              [(fp,) for fp in fingerprints])
        self.conn.commit()

    def clear_missing(self, fingerprints: Iterable[str]) -> None:
        """Linki hala acilan ilan yayindadir: supheli sayaci sifirlanir."""
        self.conn.executemany(
            "UPDATE projects SET missing_streak = 0, last_missing_at = NULL, verified_at = ? "
            "WHERE fingerprint = ?", [(_now(), fp) for fp in fingerprints])
        self.conn.commit()

    def close_stale(self, source: str, min_streak: int) -> list[sqlite3.Row]:
        """Link kontrolu yapilamayan kaynaklarda son care: uzun sure gorulmeyeni kapat."""
        rows = list(self.conn.execute(
            "SELECT * FROM projects WHERE source = ? AND is_active = 1 AND missing_streak >= ?",
            (source, min_streak)))
        if rows:
            self.conn.execute(
                "UPDATE projects SET is_active = 0, closed_at = ? "
                "WHERE source = ? AND is_active = 1 AND missing_streak >= ?",
                (_now(), source, min_streak))
            self.conn.commit()
        return rows

    def verify_candidates(self, limit: int = 40, min_hours: int = 12,
                          skip_sources: Iterable[str] = ()) -> list[sqlite3.Row]:
        """Kontrol sirasi bekleyen aktif ilanlar.

        Once KAYIP olanlar gelir (missing_streak yuksek): olme ihtimali en yuksek
        ilan en once bakilsin. Ayni streak icinde hic kontrol edilmemisler one
        gecer, boylece liste sirayla da taranir.

        Onceki sıralama yalnizca verified_at'e bakiyordu; 6-10 turdur kayip olan
        152 ilan sıranın sonunda kalip aylarca "aktif" gorunuyordu.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_hours)).isoformat()
        skip = list(skip_sources)
        sql = ("SELECT * FROM projects WHERE is_active = 1 AND COALESCE(is_supply, 0) = 0 "
               "AND (verified_at IS NULL OR verified_at < ?)")
        params: list = [cutoff]
        if skip:
            sql += " AND source NOT IN (" + ",".join("?" * len(skip)) + ")"
            params += skip
        sql += (" ORDER BY missing_streak DESC, verified_at IS NOT NULL, verified_at, "
                "score DESC LIMIT ?")
        params.append(limit)
        return list(self.conn.execute(sql, params))

    def mark_verified(self, fingerprints: Iterable[str]) -> None:
        now = _now()
        self.conn.executemany("UPDATE projects SET verified_at=? WHERE fingerprint=?",
                              [(now, fp) for fp in fingerprints])
        self.conn.commit()

    def close_project(self, fingerprint: str) -> None:
        self.conn.execute(
            "UPDATE projects SET is_active = 0, closed_at = ? WHERE fingerprint = ?",
            (_now(), fingerprint))
        self.conn.commit()

    def get_by_fingerprint(self, fingerprint: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM projects WHERE fingerprint = ?", (fingerprint,)).fetchone()

    def report_closed(self, fingerprint: str, profile_id: int = 0) -> sqlite3.Row | None:
        """Kullanici 'artik aktif degil' derse hemen kapatir.

        Bazi kaynaklarin (ornegin Jooble) ilan linkleri bot korumasi yuzunden
        otomatik kontrol edilemiyor (sabit 403 - tarayici basliklariyla bile).
        Bu, otomatik dogrulamanin ulasamadigi durumlar icin elle kaci.
        """
        row = self.get_by_fingerprint(fingerprint)
        if row is None or row["is_active"] == 0:
            return None
        self.close_project(fingerprint)
        self.add_notification("closed", row["title"], "kullanıcı bildirdi: artık aktif değil",
                              row["url"], row["source"], row["score"] or 0,
                              row["work_mode"] or "", fingerprint, profile_id=profile_id)
        return row

    def start_run(self, source: str, query: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, source, query) VALUES (?,?,?)", (_now(), source, query))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, fetched: int, inserted: int,
                   status: str = "ok", error: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, fetched=?, inserted=?, status=?, error=? WHERE id=?",
            (_now(), fetched, inserted, status, error, run_id))
        self.conn.commit()

    def mark_notified(self, fingerprints: Iterable[str]) -> None:
        now = _now()
        self.conn.executemany(
            "UPDATE projects SET status='mailed', notified_at=? WHERE fingerprint=? AND status='new'",
            [(now, fp) for fp in fingerprints])
        self.conn.commit()

    def set_status(self, fingerprint: str, status: str, profile_id: int = 0) -> None:
        """Isaret yalnizca verilen profilde gecerlidir; 'new' isareti kaldirir."""
        if status == "new":
            self.conn.execute(
                "DELETE FROM profile_status WHERE profile_id=? AND fingerprint=?",
                (int(profile_id), fingerprint))
        else:
            self.conn.execute(
                "INSERT INTO profile_status (profile_id, fingerprint, status, updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(profile_id, fingerprint) "
                "DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at",
                (int(profile_id), fingerprint, status, _now()))
        self.conn.commit()

    def status_of(self, fingerprint: str, profile_id: int = 0) -> str:
        row = self.conn.execute(
            "SELECT status FROM profile_status WHERE profile_id=? AND fingerprint=?",
            (int(profile_id), fingerprint)).fetchone()
        return row[0] if row else "new"

    def update_scores(self, scores: Iterable[tuple[str, int, list[str]]]) -> int:
        rows = [(score, json.dumps(hits, ensure_ascii=False), fp) for fp, score, hits in scores]
        self.conn.executemany(
            "UPDATE projects SET score = ?, keywords_hit = ? WHERE fingerprint = ?", rows)
        self.conn.commit()
        return len(rows)

    # --- bildirimler --------------------------------------------------
    def add_notification(self, kind: str, title: str, detail: str = "", url: str = "",
                         source: str = "", score: int = 0, work_mode: str = "",
                         fingerprint: str = "", profile_id: int = 0) -> None:
        self.conn.execute(
            "INSERT INTO notifications (created_at, kind, fingerprint, title, detail, url, "
            "source, score, work_mode, profile_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_now(), kind, fingerprint, title, detail, url, source, score, work_mode,
             int(profile_id)))
        self.conn.commit()

    def add_notifications(self, items: Iterable[dict], profile_id: int = 0) -> int:
        rows = [(_now(), i.get("kind", "new"), i.get("fingerprint", ""), i.get("title", ""),
                 i.get("detail", ""), i.get("url", ""), i.get("source", ""), int(i.get("score", 0)),
                 i.get("work_mode", ""), int(profile_id)) for i in items]
        self.conn.executemany(
            "INSERT INTO notifications (created_at, kind, fingerprint, title, detail, url, "
            "source, score, work_mode, profile_id) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def notifications(self, unread_only: bool = False, limit: int = 100,
                      profile_id: int | None = None) -> list[sqlite3.Row]:
        """`profile_id` verilirse yalnizca o profilin turunda dusen bildirimler."""
        sql = "SELECT * FROM notifications"
        where, params = [], []
        if unread_only:
            where.append("read_at IS NULL")
        if profile_id is not None:
            where.append("COALESCE(profile_id, 0) = ?")
            params.append(int(profile_id))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        return list(self.conn.execute(sql, params + [limit]))

    def unread_count(self, profile_id: int | None = None) -> int:
        sql = "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
        params: list = []
        if profile_id is not None:
            sql += " AND COALESCE(profile_id, 0) = ?"
            params.append(int(profile_id))
        return int(self.conn.execute(sql, params).fetchone()[0])

    def mark_notifications_read(self, notification_id: int | None = None,
                                profile_id: int | None = None) -> None:
        if notification_id is not None:
            self.conn.execute("UPDATE notifications SET read_at=? WHERE id=?",
                              (_now(), notification_id))
        else:
            sql = "UPDATE notifications SET read_at=? WHERE read_at IS NULL"
            params: list = [_now()]
            if profile_id is not None:
                sql += " AND COALESCE(profile_id, 0) = ?"
                params.append(int(profile_id))
            self.conn.execute(sql, params)
        self.conn.commit()

    def count_notifications_before(self, keep_days: int = 30) -> int:
        """Bakim ekrani: SILINECEK bildirim sayisi (prune_notifications ile ayni olcut)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE created_at < ?", (cutoff,)).fetchone()[0])

    def prune_notifications(self, keep_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        cur = self.conn.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount

    def delete_notification(self, notification_id: int) -> int:
        cur = self.conn.execute("DELETE FROM notifications WHERE id = ?", (notification_id,))
        self.conn.commit()
        return cur.rowcount

    def delete_all_notifications(self, unread_only: bool = False,
                                 profile_id: int | None = None) -> int:
        sql = "DELETE FROM notifications"
        where, params = [], []
        if unread_only:
            where.append("read_at IS NULL")
        if profile_id is not None:
            where.append("COALESCE(profile_id, 0) = ?")
            params.append(int(profile_id))
        if where:
            sql += " WHERE " + " AND ".join(where)
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount

    # --- sorunlu ilan isaretleri --------------------------------------
    def set_flag(self, fingerprint: str, reason: str, by: str = "otomatik") -> None:
        """Ilan acildiginda sorun cikiyor: bolge kisitli, giris istiyor, bos sayfa...

        Kapatmaz - ilan hala yayinda olabilir, sadece bu kullanicidan erisilemiyor.
        """
        self.conn.execute(
            "UPDATE projects SET quality_flag = 'problem', flag_reason = ?, flagged_at = ?, "
            "flagged_by = ? WHERE fingerprint = ?", (reason, _now(), by, fingerprint))
        self.conn.commit()

    def clear_flag(self, fingerprint: str) -> None:
        self.conn.execute(
            "UPDATE projects SET quality_flag = NULL, flag_reason = NULL, flagged_at = NULL, "
            "flagged_by = NULL WHERE fingerprint = ?", (fingerprint,))
        self.conn.commit()

    def delete_projects(self, fingerprints: Iterable[str]) -> int:
        """Ilanlari veritabanindan tamamen siler. Bildirimleri de birlikte gider."""
        batch = list(fingerprints)
        if not batch:
            return 0
        silinen = 0
        for start in range(0, len(batch), 500):
            chunk = batch[start:start + 500]
            marks = ",".join("?" * len(chunk))
            self.conn.execute(f"DELETE FROM notifications WHERE fingerprint IN ({marks})", chunk)
            self.conn.execute(f"DELETE FROM profile_status WHERE fingerprint IN ({marks})", chunk)
            cur = self.conn.execute(f"DELETE FROM projects WHERE fingerprint IN ({marks})", chunk)
            silinen += cur.rowcount
        self.conn.commit()
        return silinen

    # --- bakim ---------------------------------------------------------
    def vacuum(self) -> int:
        """Silinen satirlarin biraktigi bosluk geri verilir; kazanilan bayt doner."""
        before = self.path.stat().st_size if self.path.exists() else 0
        self.conn.execute("VACUUM")
        self.conn.commit()
        after = self.path.stat().st_size if self.path.exists() else 0
        return max(0, before - after)

    def count_closed(self) -> int:
        """Bakim ekrani: silinecek kapanmis ilan sayisi (prune_closed ile ayni olcut)."""
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM projects WHERE is_active = 0").fetchone()[0])

    def prune_closed(self) -> int:
        """TUM kapanmis ilanlari siler - yas sinirina bakilmaz.

        Kapanmis ilanla isimiz yok: basvurulamaz, listede yer kaplar. Yasa gore
        (60 gun) budama vardi ama arada kalan aylarca duruyordu. Aktif ilanlara
        dokunulmaz.
        """
        rows = [r[0] for r in self.conn.execute(
            "SELECT fingerprint FROM projects WHERE is_active = 0")]
        return self.delete_projects(rows)

    def reclaimable_bytes(self) -> int:
        """VACUUM'un geri verebilecegi bosluk: serbest sayfa sayisi x sayfa boyutu."""
        free = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
        page = self.conn.execute("PRAGMA page_size").fetchone()[0]
        return int(free) * int(page)

    def db_size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    # --- okuma -------------------------------------------------------
    @staticmethod
    def _status_expr(profile_id: int) -> str:
        """Ilanin BU PROFILDEKI durumu; isaret konmamissa 'new'.

        profile_id int()'e zorlanir - ifade SQL metnine gomuluyor, parametre olamaz
        (ayni ifade hem WHERE hem SELECT icinde birden cok kez geciyor).
        """
        return ("COALESCE((SELECT ps.status FROM profile_status ps "
                f"WHERE ps.fingerprint = projects.fingerprint AND ps.profile_id = {int(profile_id)})"
                ", 'new')")

    def _where(self, min_score: int = 0, only_new: bool = False, source: str | None = None,
               search: str | None = None, work_mode: str | None = None, contract_only: bool = False,
               shortlist: bool = False, country: str | None = None, include_closed: bool = False,
               closed_only: bool = False, max_age_days: int | None = None,
               has_budget: bool = False, exclude: str | None = None,
               status: str | None = None, include_supply: bool = False,
               date_from: str | None = None, date_to: str | None = None,
               date_field: str = "posted", flag: str | None = None,
               profile_id: int = 0) -> tuple[str, list]:
        # Isaretler profile gore: ayni ilan bir profilde takipte olabilir, digerinde olmayabilir.
        mark = self._status_expr(profile_id)
        sql = " WHERE score >= ?"
        params: list = [min_score]
        if flag == "problem":
            sql += " AND COALESCE(quality_flag, '') = 'problem'"
        elif flag == "clean":
            sql += " AND COALESCE(quality_flag, '') != 'problem'"
        if not include_supply:
            # arz tarafi (hizmet satan ilanlar) varsayilan listede gorunmez
            sql += " AND COALESCE(is_supply, 0) = 0"
        if closed_only:
            sql += " AND is_active = 0"
        elif not include_closed:
            sql += " AND is_active = 1"
        if only_new:
            sql += f" AND {mark} = 'new'"
        if shortlist:
            sql += f" AND {mark} = 'shortlist'"
        if status:
            sql += f" AND {mark} = ?"
            params.append(status)
        if source:
            sql += " AND source = ?"
            params.append(source)
        if work_mode:
            if work_mode == "remote_or_hybrid":
                sql += " AND work_mode IN ('remote','hybrid')"
            else:
                sql += " AND work_mode = ?"
                params.append(work_mode)
        if contract_only:
            sql += " AND is_contract = 1"
        if has_budget:
            sql += " AND COALESCE(budget_raw, '') != ''"
        if country:
            sql += " AND (lower(country) LIKE ? OR lower(location) LIKE ?)"
            params += ["%" + country.lower() + "%"] * 2
        if max_age_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            sql += " AND COALESCE(posted_at, first_seen_at) >= ?"
            params.append(cutoff)
        # Tarih araligi: "sisteme dusme" secilirse first_seen_at'e bakilir.
        # Tarihler sabit +00:00 ekiyle saklandigi icin metin karsilastirmasi kronolojiktir.
        date_col = "first_seen_at" if date_field == "seen" else "COALESCE(posted_at, first_seen_at)"
        start = _day_start(date_from)
        if start:
            sql += f" AND {date_col} >= ?"
            params.append(start)
        end = _day_after(date_to)
        if end:
            # ust sinir HARIC: mikro saniyeli damgalar T23:59:59 ile karsilastirilinca eleniyordu
            sql += f" AND {date_col} < ?"
            params.append(end)
        if search:
            sql += " AND search_text LIKE ?"
            params.append("%" + fold(search) + "%")
        if exclude:
            for word in [w for w in fold(exclude).replace(",", " ").split() if w]:
                sql += " AND search_text NOT LIKE ?"
                params.append("%" + word + "%")
        return sql, params

    #: `fingerprint` her sirada son anahtar: esitlikte SQLite satir sirasi kararsiz,
    #: LIMIT/OFFSET sayfalamasinda ayni ilan iki sayfada cikabiliyordu.
    ORDERS = {
        "score": f"score DESC, {_DATE_EXPR} DESC, fingerprint",
        "score_asc": f"score ASC, {_DATE_EXPR} DESC, fingerprint",
        "date": f"{_DATE_EXPR} DESC, score DESC, fingerprint",
        "date_asc": f"{_DATE_EXPR} ASC, score DESC, fingerprint",
        "new": "first_seen_at DESC, score DESC, fingerprint",
        "company": "company COLLATE NOCASE, score DESC, fingerprint",
        # butcesi bilinmeyen ilan iki yonde de sona gider
        "budget": "budget_daily IS NULL, budget_daily DESC, score DESC, fingerprint",
        "budget_asc": "budget_daily IS NULL, budget_daily ASC, score DESC, fingerprint",
        "mode": f"{_MODE_EXPR}, COALESCE(is_contract, 0) DESC, score DESC, fingerprint",
    }

    def query(self, limit: int = 20, offset: int = 0, order: str = "score", **filters) -> list[sqlite3.Row]:
        where, params = self._where(**filters)
        # `mark` = ilanin bu profildeki isareti. projects.status'u ezmek yerine ayri
        # ad veriliyor: sqlite3.Row ayni adli iki kolonda ilkini dondururdu.
        mark = self._status_expr(filters.get("profile_id", 0))
        sql = (f"SELECT projects.*, {mark} AS mark FROM projects" + where +
               " ORDER BY " + self.ORDERS.get(order, self.ORDERS["score"]) + " LIMIT ? OFFSET ?")
        return list(self.conn.execute(sql, params + [limit, offset]))

    def count(self, **filters) -> int:
        where, params = self._where(**filters)
        return int(self.conn.execute("SELECT COUNT(*) FROM projects" + where, params).fetchone()[0])

    def all_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM projects"))

    def project_from_row(self, row: sqlite3.Row) -> Project:
        """DB satirini yeniden puanlanabilir Project'e cevirir."""
        try:
            skills = json.loads(row["skills"] or "[]")
        except json.JSONDecodeError:
            skills = []
        return Project(
            source=row["source"], source_id=row["source_id"] or "", url=row["url"],
            title=row["title"], company=row["company"] or "", location=row["location"] or "",
            country=row["country"] or "", work_mode=row["work_mode"] or "unknown",
            remote_percent=row["remote_percent"], engagement=row["engagement"] or "",
            is_contract=None if row["is_contract"] is None else bool(row["is_contract"]),
            duration=row["duration"] or "", budget_raw=row["budget_raw"] or "",
            description=row["description"] or "", skills=skills,
            posted_at=parse_date(row["posted_at"]),
        )

    def refresh_budgets(self) -> int:
        """Butce kolonlarini bastan hesaplar (parser ya da kur degisince)."""
        return self._backfill_budget()

    def active_fingerprints(self, source: str) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT fingerprint FROM projects WHERE source = ? AND is_active = 1", (source,))}

    def load_descriptions(self, fingerprints: Iterable[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        batch = list(fingerprints)
        for start in range(0, len(batch), 500):
            chunk = batch[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT fingerprint, description FROM projects "
                f"WHERE fingerprint IN ({placeholders})", chunk)
            out.update({r[0]: r[1] or "" for r in rows})
        return out

    def sources(self) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT source FROM projects ORDER BY source")]

    def countries(self) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT country, COUNT(*) c FROM projects WHERE COALESCE(country,'') != '' "
            "GROUP BY country ORDER BY c DESC LIMIT 25")]

    def stats(self, profile_id: int = 0) -> dict:
        mark = self._status_expr(profile_id)
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(is_active=1) AS aktif, SUM(is_active=0) AS kapali, "
            f"SUM(is_active=1 AND {mark}='new') AS yeni, SUM({mark}='shortlist') AS takip, "
            f"SUM({mark}='applied') AS basvuru, "
            "SUM(is_active=1 AND work_mode='remote') AS remote, "
            "SUM(is_active=1 AND work_mode='hybrid') AS hybrid, "
            "SUM(is_active=1 AND is_contract=1) AS contract, "
            "SUM(COALESCE(is_supply,0)=1) AS arz, MAX(last_seen_at) AS son, "
            "SUM(is_active=1 AND first_seen_at >= :gun) AS yeni24, "
            "SUM(is_active=1 AND COALESCE(posted_at, first_seen_at) >= :hafta) AS taze7, "
            "SUM(is_active=1 AND COALESCE(verified_at, '') = '') AS dogrulanmamis, "
            "SUM(is_active=1 AND missing_streak > 0) AS supheli, "
            "SUM(is_active=1 AND COALESCE(quality_flag,'') = 'problem') AS sorunlu "
            "FROM projects",
            {"gun": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
             "hafta": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()}
        ).fetchone()
        return {"total": row["total"] or 0, "active": row["aktif"] or 0, "closed": row["kapali"] or 0,
                "new": row["yeni"] or 0, "shortlist": row["takip"] or 0, "applied": row["basvuru"] or 0,
                "remote": row["remote"] or 0, "hybrid": row["hybrid"] or 0,
                "contract": row["contract"] or 0, "supply": row["arz"] or 0,
                "last_seen": row["son"], "new_24h": row["yeni24"] or 0,
                "fresh_7d": row["taze7"] or 0, "unverified": row["dogrulanmamis"] or 0,
                "suspect": row["supheli"] or 0, "problem": row["sorunlu"] or 0}

    def newest_seen_at(self) -> str:
        """En son eklenen ilanin zaman damgasi; panel bunu yoklayip taze veriyi haber verir."""
        row = self.conn.execute(
            "SELECT MAX(first_seen_at) FROM projects WHERE is_active = 1").fetchone()
        return row[0] or ""

    def count_since(self, since: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM projects WHERE is_active = 1 AND first_seen_at > ?",
            (since,)).fetchone()[0])

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))
