"""Ayarlar ekraninin veri katmani: kaynak katalogu, anahtar maskeleme, form ayristirma.

Web'e bagli degil - masaustu Ayarlar sekmesi ve CLI de ayni kataloga bakabilsin diye
ayri modul. Ayarlarin nasil saklandigi `config.py` icinde (overlay + .env).
"""
from __future__ import annotations

import os
import re
from typing import Any

from .normalize import fold
from .sources import REGISTRY, CustomSource, is_custom

#: Panelde duzenlenmeyen, sadece gosterilen kaynak secenekleri
_HIDDEN_OPTIONS = {"enabled", "queries", "keywords", "skill_ids", "countries", "locales", "cpv",
                   "kind", "url", "items_path", "fields", "title", "notes", "site_url", "name"}


#: Maskede gosterilen sabit yildiz sayisi. Gercek uzunlugu YANSITMAZ:
#: uzunluk da anahtar hakkinda bilgi sizdirir (hangi servisin anahtari,
#: kac karakter denenecegi...).
MASKE = "*" * 12


def mask(value: str | None) -> str:
    """Anahtari tarayiciya gostermeden once maskeler; tam deger asla donmez.

    Eskiden ilk 2 ve son 2 karakter aciktaydi (`d8••••••11`). Ekran goruntusu
    ya da omuz sorfu ile bu parcalar sizabiliyordu; artik hicbir karakter
    gosterilmiyor - yalnizca "kayitli mi, degil mi" bilgisi.
    """
    value = (value or "").strip()
    return MASKE if value else "tanımsız"


def secret_values() -> list[str]:
    """Ortamda tanimli olan kaynak anahtarlarinin ham degerleri (scrub icin)."""
    names = {var.name for cls in REGISTRY.values() for var in cls.env_requirements() if var.secret}
    values = [os.environ.get(name, "").strip() for name in names]
    return [v for v in values if len(v) >= 6]


def scrub(text: str) -> str:
    """Hata mesajlarindaki anahtarlari temizler.

    Jooble anahtari URL yolunda gidiyor ve hata mesaji URL'yi iceriyor; bu mesaj
    hem `runs` tablosuna hem panele dusuyordu.
    """
    if not text:
        return text
    for value in secret_values():
        text = text.replace(value, "***")
    return text


def anahtar_dosyalari() -> dict[str, Any]:
    """Ayarlar girisinde gosterilen anahtar dosyalarinin yollari.

    Yedek alirken ikisi birlikte alinmali: sifreli depo tek basina ise yaramaz,
    cozme anahtari olmadan acilamaz.
    """
    from . import secrets_store

    return {"secrets_file": str(secrets_store.db_path()),
            "secret_key_file": str(secrets_store.key_path())}


def env_state(config: dict | None = None) -> list[dict[str, Any]]:
    """Tum kaynak anahtarlarinin durumu: tanimli mi, maskesi ne."""
    seen: dict[str, dict[str, Any]] = {}
    for name, cls in REGISTRY.items():
        for var in cls.env_requirements():
            row = seen.setdefault(var.name, {
                "name": var.name, "label": var.label, "required": var.required,
                "secret": var.secret, "sources": [],
                "value": os.environ.get(var.name, "").strip(),
            })
            row["sources"].append(name)
    for row in seen.values():
        row["is_set"] = bool(row["value"])
        row["masked"] = mask(row["value"]) if row["secret"] else (row["value"] or "tanımsız")
        del row["value"]
    return list(seen.values())


def _catalog_row(name: str, cls, options: dict) -> dict[str, Any]:
    env_vars = []
    for var in cls.env_requirements():
        value = os.environ.get(var.name, "").strip()
        env_vars.append({"name": var.name, "label": var.label, "required": var.required,
                         "secret": var.secret, "is_set": bool(value),
                         "masked": mask(value) if var.secret else (value or "tanımsız")})
    missing = [v["name"] for v in env_vars if v["required"] and not v["is_set"]]
    lists = {key: list(options.get(key) or []) for key in cls.list_options}
    custom = is_custom(options)
    return {
        "name": name,
        "title": (options.get("title") if custom else cls.title) or name,
        "signup_url": options.get("site_url", "") if custom else cls.signup_url,
        "notes": options.get("notes", "") if custom else cls.notes,
        "enabled": bool(options.get("enabled")),
        "pages": options.get("pages"),
        "env_vars": env_vars,
        "missing": missing,
        "ready": not missing,
        "lists": lists,
        "list_names": list(cls.list_options),
        "custom": custom,
        "kind": options.get("kind", ""),
        "url": options.get("url", ""),
        "items_path": options.get("items_path", ""),
        "fields": dict(options.get("fields") or {}),
        "extra": {k: v for k, v in options.items()
                  if k not in _HIDDEN_OPTIONS and k not in lists and k != "pages"},
    }


def source_catalog(config: dict) -> list[dict[str, Any]]:
    """Kodda tanimli tum kaynaklar + panelden eklenenler + anahtar durumu.

    config.yaml'da hic yazmayan bir kaynak da (varsayilan kapali olarak) listeye girer.
    """
    configured = config.get("sources") or {}
    catalog = [_catalog_row(name, cls, dict(configured.get(name) or {}))
               for name, cls in REGISTRY.items()]
    catalog += [_catalog_row(name, CustomSource, dict(options or {}))
                for name, options in configured.items()
                if name not in REGISTRY and is_custom(options)]
    catalog.sort(key=lambda row: (not row["enabled"], row["name"]))
    return catalog


def custom_sources(config: dict) -> list[dict[str, Any]]:
    return [row for row in source_catalog(config) if row["custom"]]


def slugify(value: str) -> str:
    """Kaynak adi: kucuk harf, sadece harf/rakam/tire. Kaynak adi DB'ye yaziliyor."""
    folded = fold(value).replace(" ", "-")
    slug = "".join(ch for ch in folded if ch.isalnum() or ch == "-").strip("-")
    return re.sub(r"-{2,}", "-", slug)[:40]


# --- form ayristirma -------------------------------------------------

def parse_lines(text: str) -> list[str]:
    """Textarea -> liste. Bos satir ve `#` yorumu atlanir."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def parse_int_lines(text: str) -> list[int]:
    """Sayisal liste (freelancercom skill_ids gibi). Sayi olmayan satir atlanir."""
    out = []
    for line in parse_lines(text):
        try:
            out.append(int(line))
        except ValueError:
            continue
    return out


_WEIGHT = re.compile(r"^(?P<key>.+?)\s*[:=\s]\s*(?P<value>-?\d+(?:\.\d+)?)$")


def parse_weight_lines(text: str) -> dict[str, float]:
    """`kelime: 6` / `kelime = 6` / `kelime 6` satirlarini sozluge cevirir.

    Sayi tasimayan satir hata verir - sessizce dusurulurse kullanici agirligi
    kaydettigini sanip yanlis puanla calisirdi.
    """
    out: dict[str, float] = {}
    for line in parse_lines(text):
        match = _WEIGHT.match(line)
        if not match:
            raise ValueError(f"Agirlik satiri okunamadi: {line!r} - 'kelime: 6' bicimi bekleniyor")
        value = float(match.group("value"))
        out[match.group("key").strip()] = int(value) if value.is_integer() else value
    return out


def format_weight_lines(mapping: dict[str, Any] | None) -> str:
    """Agirlik sozlugunu textarea'ya yazar.

    Ic ice degerler (signals.country_boost gibi) atlanir: tek satirlik
    'kelime: sayi' bicimine sigmaz, formda gosterilse kaydederken hata verirdi.
    """
    return "\n".join(f"{key}: {value}" for key, value in (mapping or {}).items()
                     if isinstance(value, (int, float)) and not isinstance(value, bool))


def replace_map(new: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any]:
    """Sozlugu birlestirmek yerine DEGISTIRIR: tabanda olup formda olmayan anahtara
    mezar tasi (None) koyar.

    Konu degistiginde (SAP -> ERP) eski kelimeler silinebilsin diye gerekli;
    duz birlestirme onlari geri getiriyordu.
    """
    out: dict[str, Any] = dict(new)
    for key, value in (base or {}).items():
        if key in new:
            continue
        # ic ice yapilar formda gosterilmiyor, silinmemeli
        if isinstance(value, dict):
            continue
        out[key] = None
    return out


def format_lines(values: Any) -> str:
    return "\n".join(str(v) for v in (values or []))
