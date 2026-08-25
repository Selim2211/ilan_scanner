"""Jooble REST API adapter - global is/proje agregatoru.

    POST https://jooble.org/api/<KEY>   govde: {"keywords": "...", "location": "...", "page": "1"}

Not: elimizdeki anahtar GLOBAL indekse bagli (jooble.org). Bolge alt alan adlari
(tr.jooble.org vb.) ayni anahtarla 403 doner - o indeksler icin ayri anahtar gerekir.
Remote proje avinda global indeks zaten dogru yer.
"""
from __future__ import annotations

import logging
import os

from typing import Any, Iterable

from ..models import Project
from ..normalize import (clean_text, detect_contract, detect_work_mode, parse_date, strip_html)
from .base import BaseSource, EnvVar, FetchError

log = logging.getLogger(__name__)

#: Dogrulama sorgusunda tek istekte kac sonuc istenir (API destekliyor).
VERIFY_PAGE_SIZE = "100"


class JoobleSource(BaseSource):
    """Jooble'in KURESEL (jooble.org) indeksi - pratikte ABD ilanlari.

    Her ulke alan adi AYRI anahtar ister: jooble.org anahtari uk.jooble.org'da
    403 doner (2026-08-24'te uc anahtarla dogrulandi). Bu yuzden her bolge ayri
    bir kaynak sinifidir: kendi anahtari, kendi sorgulari, panelde kendi karti,
    ilanlarda kendi `source` etiketi. Anahtari degistirmek icin Ayarlar >
    Anahtarlar yeterli, koda dokunulmaz.
    """

    name = "jooble"
    #: bos = jooble.org; "uk" -> uk.jooble.org gibi
    region = ""
    requires_env = "JOOBLE_API_KEY"
    title = "Jooble (ABD / global)"
    signup_url = "https://jooble.org/api/about"
    notes = ("Kuresel indeks, agirlikla ABD ilanlari. Anahtar bu alan adina bagli; "
             "uk/de.jooble.org ayni anahtarla 403 verir - onlar icin ayri kaynaklar var. "
             "Ilan sayfalari Cloudflare yuzunden acilamaz; kapanma tespiti link yerine "
             "API sorgusuyla yapilir.")
    env_vars = (EnvVar("JOOBLE_API_KEY", "API anahtari (jooble.org)"),)
    can_verify = True

    def _endpoint(self) -> str:
        key = os.environ.get(self.requires_env, "").strip()
        if not key:
            raise FetchError(f"{self.requires_env} tanimli degil (.env dosyasina ekleyin)")
        # config'teki `region` sinif varsayilanini ezebilir (elle deneme icin)
        region = str(self.options.get("region", self.region) or "").strip()
        host = f"{region}.jooble.org" if region else "jooble.org"
        return f"https://{host}/api/{key}"

    def fetch(self, query: str) -> list[Project]:
        endpoint = self._endpoint()
        location = self.options.get("location", "")
        pages = int(self.options.get("pages", 3))

        projects: list[Project] = []
        for page in range(1, pages + 1):
            payload = {"keywords": query, "location": location, "page": str(page)}
            batch = self.client.post(endpoint, json=payload).json().get("jobs") or []
            if not batch:
                break
            projects.extend(self._to_project(item) for item in batch)
        return projects

    def verify_alive(self, rows: Iterable[Any]) -> dict[str, bool | None]:
        """Ilan hala indekste mi: basligiyla aranir, PARMAK IZI ile eslestirilir.

        `id` ile eslestirme YAPILMAZ: olculdu, Jooble ayni ilana sorgudan sorguya
        farkli id veriyor (ayni baslik + ayni firma, baska id). Bizim parmak izimiz
        baslik+firmadan uretildigi icin kararli olan tek anahtar o.

        Bulunamamak tek basina "kapandi" demek degildir (baslik araması ilani her
        zaman getirmiyor); karar veren, ust uste kac tur bulunamadigidir.
        """
        from ..dedupe import fingerprint

        sonuc: dict[str, bool | None] = {}
        try:
            endpoint = self._endpoint()
        except FetchError as exc:
            log.warning("jooble dogrulamasi atlandi: %s", exc)
            return sonuc

        for row in rows:
            fp = row["fingerprint"]
            title = clean_text(row["title"])
            if not title:
                sonuc[fp] = None
                continue
            try:
                payload = {"keywords": title, "location": "", "page": "1",
                           "ResultOnPage": VERIFY_PAGE_SIZE}
                jobs = self.client.post(endpoint, json=payload).json().get("jobs") or []
            except Exception as exc:  # noqa: BLE001 - kota/engel: karar verilemedi
                log.debug("jooble dogrulama istegi basarisiz (%s): %s", title[:40], exc)
                sonuc[fp] = None
                continue
            sonuc[fp] = fp in {fingerprint(self._to_project(item)) for item in jobs}
        return sonuc

    def _to_project(self, item: dict) -> Project:
        title = clean_text(item.get("title"))
        description = strip_html(item.get("snippet"))
        location = clean_text(item.get("location"))
        employment = clean_text(item.get("type"))
        url = item.get("link", "")

        return Project(
            source=self.name,
            source_id=str(item.get("id") or self.make_id(url, title)),
            url=url,
            title=title,
            company=clean_text(item.get("company")) or clean_text(item.get("source")),
            location=location,
            country=location.split(",")[-1].strip() if "," in location else location,
            work_mode=detect_work_mode(title, location, description, employment),
            engagement=employment,
            is_contract=detect_contract(title, description, employment),
            budget_raw=clean_text(item.get("salary")),
            description=description,
            posted_at=parse_date(item.get("updated")),
        )


class JoobleUKSource(JoobleSource):
    """uk.jooble.org - Ingiltere indeksi.

    Olcum (2026-08-24): "SAP ABAP contract" -> donen 100 ilanin 100'u gercekten
    SAP/ABAP. Firmalar Next Ventures, RED, Vivid Resourcing gibi SAP contract
    ajanslari. Kuresel indekse gore cok daha temiz.
    """

    name = "jooble-uk"
    region = "uk"
    requires_env = "JOOBLE_API_KEY_UK"
    title = "Jooble UK"
    signup_url = "https://uk.jooble.org/api/about"
    notes = ("Ingiltere indeksi - ayri anahtar ister. SAP contract ajanslarinin "
             "yogun oldugu pazar; isabet orani kuresel indeksten yuksek.")
    env_vars = (EnvVar("JOOBLE_API_KEY_UK", "API anahtari (uk.jooble.org)"),)


class JoobleDESource(JoobleSource):
    """de.jooble.org - Almanya indeksi.

    AB SAP freelance hacminin merkezi: "SAP ABAP" icin 65.043 sonuc (ABD
    indeksinden bile fazla). Almanca sorgular ancak burada anlamli - kuresel
    indekste "SAP Berater freiberuflich" ABD ilanlari donduruyordu.
    """

    name = "jooble-de"
    region = "de"
    requires_env = "JOOBLE_API_KEY_DE"
    title = "Jooble Almanya"
    signup_url = "https://de.jooble.org/api/about"
    notes = ("Almanya indeksi - ayri anahtar ister. AB SAP freelance pazarinin "
             "merkezi; Almanca sorgular yalnizca burada calisir.")
    env_vars = (EnvVar("JOOBLE_API_KEY_DE", "API anahtari (de.jooble.org)"),)
