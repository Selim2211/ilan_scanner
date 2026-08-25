"""Kaynak adapter arayuzu + ortak HTTP istemcisi."""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from ..models import Project

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Kaynak erisilemedi / beklenmeyen yanit."""


class BlockedError(FetchError):
    """Kaynak bizi engelledi (403/429). O tarama boyunca kaynak atlanir."""


class MappingError(FetchError):
    """Panelden girilen kaynak tanimi yanlis (adres, yol ya da alan eslemesi).

    Ayri tutuluyor cunku bu bir ag sorunu degil: kullaniciya "kaynak yanit vermedi"
    demek yerine formda neyi duzeltmesi gerektigi soylenir.
    """


class HttpClient:
    """Rate limit + retry uygulayan ince httpx sarmalayicisi."""

    def __init__(self, user_agent: str, timeout: float = 25.0,
                 rate_limit_seconds: float = 2.0, max_retries: int = 3):
        self.rate_limit = rate_limit_seconds
        self.max_retries = max_retries
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
        )

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.monotonic()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._wait()
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    return response
                if response.status_code in (401, 403, 429):
                    # Bot korumasi devrede: tekrar denemek yasagi uzatiyor.
                    raise BlockedError(f"HTTP {response.status_code} - engellendi: {url}")
                if response.status_code in (500, 502, 503, 504):
                    last_error = FetchError(f"HTTP {response.status_code} - {url}")
                else:
                    raise FetchError(f"HTTP {response.status_code} - {url}")
            backoff = 2 ** attempt
            log.warning("istek basarisiz (%s/%s), %ss bekleniyor: %s", attempt, self.max_retries, backoff, last_error)
            time.sleep(backoff)
        raise FetchError(str(last_error))

    def probe(self, url: str) -> httpx.Response | None:
        """Tek deneme, retry yok, hata firlatmaz: durum kodu CAGIRANA lazim.

        `request()` 404'te FetchError, 403'te BlockedError firlatiyor. Ilan linki
        kontrolu bu yuzden durum kodunu hic goremiyor, her 404 "karar verilemedi"
        sayiliyordu; kapanan ilan da hicbir zaman kapatilmiyordu. Burada yanit
        neyse o donulur, ag hatasinda None.

        Rate limit uygulanmaz: cagiran (temizlik turu) hizini kendi yonetir.
        """
        try:
            return self._client.request("GET", url)
        except httpx.HTTPError as exc:
            log.debug("%s acilamadi: %s", url, exc)
            return None

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._client.close()


@dataclass(frozen=True)
class EnvVar:
    """Kaynagin ihtiyac duydugu bir ortam degiskeni (panelde duzenlenebilir)."""

    name: str
    label: str
    required: bool = True
    secret: bool = True


class BaseSource:
    """Her kaynak bunu miras alir ve fetch() doldurur."""

    name: str = "base"
    #: kaynak calismak icin ortam degiskeni gerektiriyorsa adi (tek degiskenli eski yol)
    requires_env: str | None = None
    #: panelde gosterilecek ad, kayit adresi ve kisa not
    title: str = ""
    signup_url: str = ""
    notes: str = ""
    #: kaynak birden fazla anahtar istiyorsa hepsi burada (ornek: upwork)
    env_vars: tuple[EnvVar, ...] = ()
    #: config'te panelden duzenlenebilen liste alanlari
    list_options: tuple[str, ...] = ("queries",)
    #: kontrol (probe) sirasinda kisaltilacak listeler: sadece HER ELEMANI AYRI
    #: ISTEK olanlar. Tek istege sigan kod listeleri (skill_ids, cpv) kisaltilirsa
    #: arama daralir ve kontrol "sonuc yok" der - hiz da kazandirmaz.
    probe_trim: tuple[str, ...] = ("queries", "keywords")

    @classmethod
    def env_requirements(cls) -> tuple[EnvVar, ...]:
        """Kaynagin tum ortam degiskenleri. `requires_env` tek degisken tutuyordu;
        upwork iki tane istiyor, bu yuzden `env_vars` onceliklidir."""
        if cls.env_vars:
            return cls.env_vars
        if cls.requires_env:
            return (EnvVar(cls.requires_env, cls.requires_env),)
        return ()

    def __init__(self, client: HttpClient, options: dict[str, Any] | None = None):
        self.client = client
        self.options = options or {}

    #: Kaynak, ilanin hala yayinda olup olmadigini KENDI yolundan soyleyebiliyor mu?
    #: Jooble'in ilan sayfalari Cloudflare yuzunden her kosulda 403 doner; link
    #: kontrolu oradan bilgi alamaz ama API acik.
    can_verify: bool = False

    def fetch(self, query: str) -> list[Project]:
        raise NotImplementedError

    def verify_alive(self, rows: Iterable[Any]) -> dict[str, bool | None]:
        """{fingerprint: yayinda_mi} - True / False / None (bilinmiyor).

        `rows` veritabani satirlaridir (fingerprint, title, company... alanlari).
        Varsayilan bostur: kaynak konusamiyorsa karar link kontrolune kalir.
        """
        return {}

    # --- yardimcilar --------------------------------------------------
    def make_id(self, *parts: str) -> str:
        raw = "|".join(p for p in parts if p)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def safe_fetch(self, query: str) -> tuple[list[Project], str]:
        """(projeler, hata_mesaji) doner. Bir kaynak patlarsa tarama durmaz."""
        try:
            return self.fetch(query), ""
        except Exception as exc:  # noqa: BLE001 - kaynak izolasyonu bilincli
            log.error("%s kaynagi hata verdi (%s): %s", self.name, query, exc)
            return [], f"{type(exc).__name__}: {exc}"


def limit(items: Iterable, count: int) -> list:
    out = []
    for item in items:
        out.append(item)
        if len(out) >= count:
            break
    return out
