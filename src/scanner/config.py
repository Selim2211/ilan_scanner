"""config.yaml / keywords.yaml yukleme + .env.

Panelden yapilan ayar degisiklikleri `config.yaml`'a YAZILMAZ: o dosyanin yorumlari
her kaynagin neden oyle ayarlandigini belgeliyor, PyYAML round-trip'i hepsini siler.
Bunun yerine yaninda bir overlay dosyasi (`settings.local.yaml`) tutulur ve yukleme
sirasinda taban uzerine derin birlestirilir.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from . import paths

log = logging.getLogger(__name__)

#: Salt okunur kaynaklarin koku (paketlenmis exe'de pakete isaret eder)
ROOT = paths.bundle_dir()
CONFIG_DIR = ROOT / "config"

OVERLAY_HEADER = (
    "# Bu dosya panelin Ayarlar ekrani tarafindan uretilir.\n"
    "# config.yaml ezilmez; buradaki degerler onun uzerine uygulanir.\n"
    "# Elle de duzenlenebilir. Bir anahtari varsayilana dondurmek icin satiri silin.\n"
)


def config_dir() -> Path:
    """Salt okunur ayar dosyalarinin klasoru (test icin monkeypatch'lenebilir)."""
    return paths.bundle_dir() / "config"


def overlay_dir() -> Path:
    """Yazilabilir ayar klasoru: gelistirmede proje koku, kuruluda %LOCALAPPDATA%."""
    path = paths.data_dir() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def overlay_path() -> Path:
    return overlay_dir() / "settings.local.yaml"


def keywords_overlay_path() -> Path:
    return overlay_dir() / "keywords.local.yaml"


def deep_merge(base: Any, patch: Any) -> Any:
    """`patch`i `base` uzerine uygular.

    dict + dict  -> ozyineli birlestir
    patch None   -> anahtari SIL (mezar tasi; kullanici bir kelimeyi listeden
                    silince saf birlestirme onu tabandan geri getirirdi)
    diger        -> tamamen degistir (liste ve skalerler)
    """
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return patch
    out = dict(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def prune_unchanged(patch: Any, base: Any) -> Any:
    """Tabanla ayni olan degerleri overlay'den eler.

    Yoksa panel her kaydediste butun listeleri overlay'e kopyalar ve config.yaml'daki
    varsayilanlar donar: sonraki surumde sorgu listesi iyilestirilse kullaniciya ulasmaz.
    """
    if not isinstance(patch, dict) or not isinstance(base, dict):
        return patch
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in base:
            out[key] = value
            continue
        if isinstance(value, dict) and isinstance(base[key], dict):
            trimmed = prune_unchanged(value, base[key])
            if trimmed:
                out[key] = trimmed
        elif value != base[key]:
            out[key] = value
    return out


def load_base_config(path: Path | None = None) -> dict[str, Any]:
    """Overlay uygulanmamis config.yaml (fark almak icin)."""
    path = path or config_dir() / "config.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_base_keywords(path: Path | None = None) -> dict[str, Any]:
    path = path or config_dir() / "keywords.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_overlay(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_overlay(patch: dict[str, Any], path: Path | None = None,
                 base: dict[str, Any] | None = None,
                 replace_keys: tuple[str, ...] = ()) -> Path:
    """Overlay'i mevcut icerikle birlestirip atomik yazar.

    `base` verilirse tabanla ayni olan degerler yazilmaz; overlay sadece kullanicinin
    gercekten degistirdiklerini tutar.

    `replace_keys` icindeki anahtarlar mevcut overlay ile BIRLESTIRILMEZ, sifirdan
    yazilir. Birlestirmede `deep_merge` yalnizca overlay'de zaten bulunan anahtarin
    mezar tasini uygulayabiliyor; digerleri sessizce dusuyor ve taban degeri geri
    geliyordu. Arama profili gecisi bunu kullanir: profil sozlugu tumuyle degismeli.
    """
    path = path or overlay_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_overlay(path)
    for key in replace_keys:
        existing.pop(key, None)
    merged = deep_merge(existing, patch)
    if base is not None:
        merged = prune_unchanged(merged, base)
    body = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(OVERLAY_HEADER + body, encoding="utf-8")
    os.replace(tmp, path)
    return path


def env_path() -> Path:
    """Anahtarlarin yazildigi .env: `_load_dotenv`in ilk okudugu dosya."""
    return paths.data_dir() / ".env"


def save_env(values: dict[str, str]) -> None:
    """Anahtarlari SIFRELI depoya yazar ve calisan surecin ortamini tazeler.

    Eskiden duz metin `.env` dosyasina yazilirdi; artik anahtarlar sifreli
    saklaniyor (secrets_store) ve `.env` yalnizca ilk kurulumda tohum olarak
    OKUNUR, yazilmaz. Ortam tazelenmezse yeni anahtar ancak yeniden baslatinca
    gorulurdu. Bos deger = anahtari sil.
    """
    from . import secrets_store

    secrets_store.save(values)
    for key, value in values.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def _load_dotenv(force: bool = False) -> None:
    """Ortam degiskenlerini yukler: once .env dosyalari, sonra pakete gomulu anahtarlar.

    Kurulu programda .env dosyasi olmayabilir; derleme sirasinda uretilen
    `_embedded_env` modulu devreye girer. Dosyada bulunan deger gomuluyu ezer.

    `force=True` dosyadaki degeri surecte zaten tanimli olanin uzerine yazar;
    varsayilan `False` gercek ortam degiskenlerinin (systemd, CI) onceligini korur.

    Oncelik: gercek ortam degiskeni > SIFRELI depo > .env dosyalari > gomulu.
    Sifreli depo dosyalardan ONCE okunuyor: panelden girilen guncel anahtari,
    kurulumdan kalma eski bir `.env` satiri ezmesin.
    """
    from . import secrets_store

    for key, value in secrets_store.load_all().items():
        if not value:
            continue
        if force:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)

    dosyadan: dict[str, str] = {}
    for env_file in (paths.data_dir() / ".env", ROOT / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            dosyadan.setdefault(key, value)
            if force:
                os.environ[key] = value
            else:
                os.environ.setdefault(key, value)

    # Duz metinden okunanlar sifreli depoya BIR KEZ tasinir: mevcut kurulumlar
    # ve anahtari imaja gomulu Docker paketi panelde yeniden anahtar girmek
    # zorunda kalmasin. Depoda zaten olan anahtar EZILMEZ (bkz. seed_from_env_file).
    if dosyadan:
        try:
            secrets_store.seed_from_env_file(dosyadan)
        except Exception as hata:            # noqa: BLE001 - tohumlama kritik degil
            log.warning("anahtarlar sifreli depoya tasinamadi: %s", hata)

    try:
        from ._embedded_env import EMBEDDED_ENV     # derleme sirasinda uretilir
    except ImportError:
        return
    for key, value in EMBEDDED_ENV.items():
        if value:
            os.environ.setdefault(key, value)


def load_config(path: Path | None = None) -> dict[str, Any]:
    _load_dotenv()
    path = path or config_dir() / "config.yaml"
    base = yaml.safe_load(path.read_text(encoding="utf-8"))
    return deep_merge(base, load_overlay(overlay_path()))


def load_keywords(path: Path | None = None) -> dict[str, Any]:
    path = path or config_dir() / "keywords.yaml"
    base = yaml.safe_load(path.read_text(encoding="utf-8"))
    return deep_merge(base, load_overlay(keywords_overlay_path()))


def resolve_path(value: str) -> Path:
    """Yazilacak dosya yolu: veritabani, ciktilar, loglar.

    Gelistirmede proje klasorune, kurulu programda %LOCALAPPDATA% altina duser
    (Program Files yazilabilir degil).
    """
    p = Path(value)
    if p.is_absolute():
        return p
    target = paths.data_dir() / p
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
