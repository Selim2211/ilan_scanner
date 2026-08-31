"""Arama profilleri: bir arama icin gereken tum parametreleri isimli paket olarak saklar.

Neden: her yeni arama konusu icin sorgular, zorunlu kelimeler, puan agirliklari ve
liste filtreleri bastan doldurulmak zorundaydi. Profil bunlari bir kez kaydeder,
sonra tek tikla aralarinda gecis yapilir.

Gecis yaparken profil, ayarlar overlay dosyalarina UYGULANIR (config.py'daki
save_overlay ile) - boylece hem tarama hem puanlama profile gore calisir, sadece
liste gorunumu degil.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (deep_merge, keywords_overlay_path, load_base_config, load_base_keywords,
                     load_config, load_keywords, save_overlay)
from .settings import replace_map

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT DEFAULT '',
    payload     TEXT NOT NULL,            -- sorgular, kelimeler, agirliklar, liste filtreleri
    is_active   INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Yoneticinin kullaniciya atadigi profiller. Kullanici yalnizca kendisine
-- atanmis profiller arasinda gecis yapabilir; yoneticiye kayit gerekmez.
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id    INTEGER NOT NULL,
    profile_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, profile_id)
);
"""

#: Profilin tasidigi puanlama alanlari (keywords.yaml tarafi)
KEYWORD_FIELDS = ("must_any", "hard_exclude", "boost", "penalty", "signals",
                  "min_score", "title_multiplier")

#: Bos birakilinca TABAN degeri korunan alanlar.
#: `signals` konuya degil uygulamanin isleyisine ait (uzaktan/proje bazli onceligi).
#: Bos = "formu doldurmadim" demek; silinirse remote_boost 0 olur ve uygulamanin
#: asil mantigi sessizce kapanir. `boost`/`hard_exclude` icin bos = "yok" demektir,
#: onlar degistirilir.
KEEP_WHEN_EMPTY = ("signals",)


def _keyword_value(field: str, value, mevcut):
    """Profil alaninin nihai degeri; degistirilmeyecekse None doner."""
    if value is None:
        return None
    if field in KEEP_WHEN_EMPTY and not value:
        return None
    if isinstance(value, dict):
        # ic ice yapilar (signals.country_boost) formda gosterilmiyor, korunur
        nested = {k: v for k, v in (mevcut or {}).items() if isinstance(v, dict)}
        return {**nested, **value}
    return value

#: Profilin tasidigi liste gorunumu alanlari (panel filtreleri)
VIEW_FIELDS = ("mode", "contract", "budget", "days", "sort", "country", "exclude", "min_score")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_payload() -> dict:
    """Bos profil iskeleti - form bunu doldurur."""
    return {"queries": [], "must_any": [], "hard_exclude": [], "boost": {}, "penalty": {},
            "signals": {}, "min_score": 0, "title_multiplier": 2.0, "view": {},
            # Tam turda arka planda da taransin mi? Kapaliysa profil yalnizca
            # etkinken beslenir.
            "scan_in_background": True}


def settings_for(row: sqlite3.Row) -> tuple[dict, dict]:
    """Profilin (config, keywords) ikilisi - overlay DOSYALARINA DOKUNMADAN.

    `activate()` ayarlari diske yazar; bu ise yalnizca bellekte uretir. Tarama
    sirasinda dosyaya yazmak, kullanici o an profil degistirirse birbirini ezerdi.
    """
    payload = Profiles.payload_of(row)
    config = load_config()
    keywords = load_keywords()
    if payload.get("queries"):
        config = deep_merge(config, {"queries": list(payload["queries"])})

    # Sozlukler BIRLESTIRILMEZ, DEGISTIRILIR - activate() ile ayni anlam. deep_merge
    # burada kullanilamaz: dict + dict'i ozyineli birlestirir ve etkin profilin
    # kelimeleri taranan profile sizardi.
    result = dict(keywords)
    for field in KEYWORD_FIELDS:
        nihai = _keyword_value(field, payload.get(field), keywords.get(field))
        if nihai is not None:
            result[field] = nihai
    return config, result


def _cok_secim(form, alan: str) -> str:
    """Cok secimli form alanini virgulle birlesik metne cevirir."""
    if hasattr(form, "getlist"):
        secili = [str(v).strip() for v in form.getlist(alan) if str(v).strip()]
    else:
        secili = [(form.get(alan) or "").strip()]
    return ",".join(dict.fromkeys(v for v in secili if v))


def capture_current(config: dict | None = None, keywords: dict | None = None) -> dict:
    """Su anki ayarlardan profil govdesi uretir ('mevcut ayarlardan profil olustur')."""
    config = config or load_config()
    keywords = keywords or load_keywords()
    payload = default_payload()
    payload["queries"] = list(config.get("queries") or [])
    for field in KEYWORD_FIELDS:
        if field in keywords:
            payload[field] = keywords[field]
    return payload


class Profiles:
    """search_profiles tablosu. Storage / Auth gibi acilip kapatilir."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- okuma -----------------------------------------------------------
    def all(self) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM search_profiles ORDER BY name COLLATE NOCASE"))

    def get(self, profile_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM search_profiles WHERE id = ?", (profile_id,)).fetchone()

    def by_name(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM search_profiles WHERE name = ? COLLATE NOCASE", (name.strip(),)).fetchone()

    def active(self) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM search_profiles WHERE is_active = 1").fetchone()

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM search_profiles").fetchone()[0])

    @staticmethod
    def payload_of(row: sqlite3.Row | None) -> dict:
        if row is None:
            return default_payload()
        try:
            data = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            return default_payload()
        merged = default_payload()
        merged.update(data)
        return merged

    def scannable(self) -> list[sqlite3.Row]:
        """Tam turda arka planda taranacak profiller.

        Etkin profil, anahtari kapali olsa bile listeye girer: kullanicinin onunde
        duran liste her turda beslenmeli.
        """
        rows = [r for r in self.all()
                if self.payload_of(r).get("scan_in_background", True) or r["is_active"]]
        return [r for r in rows if self.payload_of(r).get("queries")]

    def active_view(self) -> dict:
        """Etkin profilin liste filtreleri; profil yoksa bos sozluk."""
        row = self.active()
        return self.payload_of(row).get("view") or {} if row else {}

    # --- yazma -----------------------------------------------------------
    def create(self, name: str, payload: dict, description: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO search_profiles (name, description, payload, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (name.strip(), description.strip(), json.dumps(payload, ensure_ascii=False),
             _now(), _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def update(self, profile_id: int, name: str, payload: dict, description: str = "") -> None:
        self.conn.execute(
            "UPDATE search_profiles SET name = ?, description = ?, payload = ?, updated_at = ? "
            "WHERE id = ?",
            (name.strip(), description.strip(), json.dumps(payload, ensure_ascii=False),
             _now(), profile_id))
        self.conn.commit()

    def delete(self, profile_id: int) -> None:
        self.conn.execute("DELETE FROM user_profiles WHERE profile_id = ?", (profile_id,))
        self.conn.execute("DELETE FROM search_profiles WHERE id = ?", (profile_id,))
        self.conn.commit()

    # --- kullaniciya atama -----------------------------------------------
    def assigned_ids(self, user_id: int) -> set[int]:
        return {r[0] for r in self.conn.execute(
            "SELECT profile_id FROM user_profiles WHERE user_id = ?", (user_id,))}

    def set_assigned(self, user_id: int, profile_ids: list[int]) -> None:
        self.conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (user_id,))
        self.conn.executemany(
            "INSERT OR IGNORE INTO user_profiles (user_id, profile_id) VALUES (?,?)",
            [(user_id, int(pid)) for pid in profile_ids])
        self.conn.commit()

    def for_user(self, user_id: int, is_admin: bool = False) -> list[sqlite3.Row]:
        """Kullanicinin secebilecegi profiller. Yonetici hepsini gorur."""
        if is_admin:
            return self.all()
        return list(self.conn.execute(
            "SELECT p.* FROM search_profiles p JOIN user_profiles up ON up.profile_id = p.id "
            "WHERE up.user_id = ? ORDER BY p.name COLLATE NOCASE", (user_id,)))

    def can_use(self, user_id: int, profile_id: int, is_admin: bool = False) -> bool:
        if is_admin or profile_id == 0:
            return True
        return profile_id in self.assigned_ids(user_id)

    def activate(self, profile_id: int) -> sqlite3.Row | None:
        """Profili etkin yapar ve ayarlarini overlay dosyalarina yazar.

        Puanlama sozlugu degistigi icin cagiran taraf ardindan `rescore_all`
        calistirmalidir - yoksa liste eski puanlarla gorunur.
        """
        row = self.get(profile_id)
        if row is None:
            return None
        payload = self.payload_of(row)

        if payload.get("queries"):
            save_overlay({"queries": list(payload["queries"])}, base=load_base_config())

        # Bos alanlar da YAZILIR. Atlanirsa onceki profilin degeri yerinde kalir ve
        # profiller birbirine sizar (B profilinde hard_exclude bos olmasina ragmen
        # A'nin 'stajyer' kaydi eleme yapmaya devam ediyordu).
        #
        # Sozlukler birlestirilmez, DEGISTIRILIR: replace_map tabanda olup profilde
        # olmayan anahtara mezar tasi koyar, ic ice yapilari (country_boost) korur.
        # Karsilastirma tabana gore yapilir, mevcut ayara gore degil - replace_keys
        # zaten onceki profilin overlay kaydini siliyor.
        base_keywords = load_base_keywords()
        keyword_patch: dict[str, Any] = {}
        for field in KEYWORD_FIELDS:
            value = payload.get(field)
            if value is None or (field in KEEP_WHEN_EMPTY and not value):
                continue          # bos sinyal listesi tabani silmesin
            if isinstance(value, dict):
                keyword_patch[field] = replace_map(value, base_keywords.get(field))
            else:
                keyword_patch[field] = value
        if keyword_patch:
            save_overlay(keyword_patch, keywords_overlay_path(), base=base_keywords,
                         replace_keys=KEYWORD_FIELDS)

        self.conn.execute("UPDATE search_profiles SET is_active = 0")
        self.conn.execute("UPDATE search_profiles SET is_active = 1 WHERE id = ?", (profile_id,))
        self.conn.commit()
        return self.get(profile_id)

    def deactivate(self) -> None:
        """Profil secimini birakir; ayarlar oldugu gibi kalir."""
        self.conn.execute("UPDATE search_profiles SET is_active = 0")
        self.conn.commit()

    def sync_active(self, config: dict, keywords: dict) -> None:
        """Ayarlar ekranindan elle degisiklik yapilinca etkin profili de gunceller.

        Yoksa profil ile gercek ayarlar birbirinden sessizce ayrilirdi.
        """
        row = self.active()
        if row is None:
            return
        payload = self.payload_of(row)
        payload["queries"] = list(config.get("queries") or [])
        for field in KEYWORD_FIELDS:
            if field in keywords:
                payload[field] = keywords[field]
        self.conn.execute(
            "UPDATE search_profiles SET payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), _now(), row["id"]))
        self.conn.commit()


def validate_name(name: str) -> str:
    """Bos dizge = gecerli, aksi halde hata metni."""
    name = name.strip()
    if len(name) < 2:
        return "Profil adı en az 2 karakter olmalı."
    if len(name) > 48:
        return "Profil adı en fazla 48 karakter olabilir."
    return ""


def payload_from_form(form: Any, parse_lines, parse_weight_lines) -> tuple[dict, str]:
    """Ayarlar formundan profil govdesi cikarir: (payload, hata)."""
    payload = default_payload()
    payload["queries"] = parse_lines(form.get("queries") or "")
    payload["must_any"] = parse_lines(form.get("must_any") or "")
    payload["hard_exclude"] = parse_lines(form.get("hard_exclude") or "")
    if not payload["queries"]:
        return {}, "En az bir arama sorgusu yazın."
    if not payload["must_any"]:
        return {}, "Zorunlu kelimeler boş bırakılamaz — yoksa hiçbir ilan elenmez."
    try:
        payload["boost"] = parse_weight_lines(form.get("boost") or "")
        payload["penalty"] = parse_weight_lines(form.get("penalty") or "")
        payload["signals"] = parse_weight_lines(form.get("signals") or "")
        payload["min_score"] = int(form.get("min_score") or 0)
        payload["title_multiplier"] = float(form.get("title_multiplier") or 2.0)
    except ValueError as exc:
        return {}, str(exc)

    payload["scan_in_background"] = form.get("scan_in_background") == "1"
    payload["view"] = {
        "mode": (form.get("view_mode") or "").strip(),
        "contract": 1 if form.get("view_contract") == "1" else 0,
        "budget": 1 if form.get("view_budget") == "1" else 0,
        "days": int(form.get("view_days") or 0),
        "sort": (form.get("view_sort") or "score").strip(),
        # Ulke kutusu cok secimli: birden fazla kod virgulle tek metinde saklanir,
        # okurken storage.country_values() ayni listeye geri ceviriyor.
        "country": _cok_secim(form, "view_country"),
        "exclude": (form.get("view_exclude") or "").strip(),
        "min_score": payload["min_score"],
    }
    return payload, ""
