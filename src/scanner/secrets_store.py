"""API anahtarlarinin sifreli deposu.

Anahtarlar duz metin `.env` dosyasi yerine kucuk bir SQLite veritabaninda
(`<veri>/secrets.db`) SIFRELI saklanir. Panelden girilen her anahtar buraya
yazilir; `.env` yalnizca ilk kurulumda tohum (seed) olarak okunur.

NE KORUR, NE KORUMAZ - dogru beklenti icin:
  KORUR   : veritabani dosyasi, yedek ya da ekran goruntusu disari sizarsa
            anahtarlar okunamaz. Docker imajindan cikarilan bir .db de ayni.
  KORUMAZ : sunucuya TAM erisimi olan biri hem sifreli veriyi hem cozme
            anahtarini alabilir. Uygulama anahtarlari kullanabilmek icin
            cozmek zorunda, dolayisiyla cozme anahtari da makinede duruyor.
            Bu, standart "encryption at rest" sinirdir; sihir yok.

Cozme anahtari sirasiyla:
  1. RADAR_SECRET_KEY ortam degiskeni (uretimde tercih edilen - diske yazilmaz)
  2. `<veri>/secret.key` dosyasi; yoksa ilk acilista uretilir (POSIX'te 0600).
Anahtar dosyasi kaybolursa kayitli degerler cozulemez - panelden yeniden
girilir. Sifreleme `cryptography` paketinin Fernet'i (AES-128-CBC + HMAC).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import paths

log = logging.getLogger(__name__)

#: Cozme anahtarini diske yazmadan vermek icin (systemd, docker -e ...)
ANAHTAR_DEGISKENI = "RADAR_SECRET_KEY"

SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    name       TEXT PRIMARY KEY,
    value      BLOB NOT NULL,          -- Fernet ile sifrelenmis deger
    updated_at TEXT NOT NULL
);
"""


def db_path() -> Path:
    """Sifreli anahtar veritabani. `projects.db`den AYRI ve sabit yolda.

    Ayri olmasinin nedeni tavuk-yumurta: `projects.db`nin yeri config'ten
    okunuyor, config'i okumak icin de anahtarlar gerekiyor.
    """
    return paths.data_dir() / "secrets.db"


def key_path() -> Path:
    return paths.data_dir() / "secret.key"


def _anahtar() -> bytes:
    """Cozme anahtari: once ortam degiskeni, sonra dosya (yoksa uretilir)."""
    ortam = os.environ.get(ANAHTAR_DEGISKENI, "").strip()
    if ortam:
        return ortam.encode()

    yol = key_path()
    if yol.exists():
        return yol.read_bytes().strip()

    yol.parent.mkdir(parents=True, exist_ok=True)
    yeni = Fernet.generate_key()
    # Once dar izinle olustur, sonra yaz: aradaki an bile genis izinli kalmasin.
    with open(yol, "wb", opener=lambda p, f: os.open(p, f, 0o600)) as dosya:
        dosya.write(yeni)
    try:
        os.chmod(yol, stat.S_IRUSR | stat.S_IWUSR)      # Windows'ta etkisiz, zararsiz
    except OSError:                                      # noqa: BLE001
        pass
    log.info("sifreleme anahtari uretildi: %s", yol)
    return yeni


def _baglanti() -> sqlite3.Connection:
    yol = db_path()
    yol.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(yol)
    conn.executescript(SCHEMA)
    return conn


def load_all() -> dict[str, str]:
    """Kayitli anahtarlari cozup dondurur. Cozulemeyenler ATLANIR.

    Cozememek beklenen bir durum: secret.key degistirilmis ya da kaybolmus
    olabilir. Panel bu yuzden acilmamali - kullanici anahtari yeniden girsin.
    """
    try:
        fernet = Fernet(_anahtar())
    except Exception as hata:                            # noqa: BLE001 - bozuk anahtar
        log.warning("sifreleme anahtari okunamadi, kayitli anahtarlar atlaniyor: %s", hata)
        return {}

    out: dict[str, str] = {}
    try:
        with _baglanti() as conn:
            satirlar = list(conn.execute("SELECT name, value FROM secrets"))
    except sqlite3.Error as hata:
        log.warning("anahtar deposu okunamadi: %s", hata)
        return {}

    for ad, sifreli in satirlar:
        try:
            out[ad] = fernet.decrypt(sifreli).decode()
        except (InvalidToken, ValueError):
            log.warning("anahtar cozulemedi (secret.key degismis olabilir): %s", ad)
    return out


def save(values: dict[str, str]) -> None:
    """Anahtarlari sifreleyip yazar. Bos deger = anahtari SIL."""
    if not values:
        return
    fernet = Fernet(_anahtar())
    simdi = datetime.now(timezone.utc).isoformat()
    with _baglanti() as conn:
        for ad, deger in values.items():
            deger = (deger or "").strip()
            if deger:
                conn.execute(
                    "INSERT INTO secrets (name, value, updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value, "
                    "updated_at=excluded.updated_at",
                    (ad, fernet.encrypt(deger.encode()), simdi))
            else:
                conn.execute("DELETE FROM secrets WHERE name = ?", (ad,))
        conn.commit()


def names() -> set[str]:
    """Depoda kayitli anahtar adlari (deger cozmeden - hizli kontrol icin)."""
    try:
        with _baglanti() as conn:
            return {r[0] for r in conn.execute("SELECT name FROM secrets")}
    except sqlite3.Error:
        return set()


def seed_from_env_file(degerler: dict[str, str]) -> int:
    """Duz metin .env'den okunan degerleri depoya BIR KEZ tasir.

    Yalnizca depoda HENUZ OLMAYAN anahtarlar yazilir: kullanici panelden yeni
    bir deger girdiyse eski .env satiri onu ezmesin.
    """
    mevcut = names()
    yeni = {k: v for k, v in degerler.items() if v and k not in mevcut}
    if yeni:
        save(yeni)
        log.info("%s anahtar duz metin .env'den sifreli depoya tasindi", len(yeni))
    return len(yeni)
