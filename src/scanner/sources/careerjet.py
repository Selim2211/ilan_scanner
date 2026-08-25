"""Careerjet adapter - 90 ulkede is ilani agregatoru (ucretsiz ortak kimligi).

    GET http://public.api.careerjet.net/search
        ?keywords=SAP ABAP&locale_code=en_GB&affid=..&user_ip=..&user_agent=..&pagesize=99

Kimlik: https://www.careerjet.com/partners/api/ -> ucretsiz `affid`
(.env icine CAREERJET_AFFID).

API `user_ip` ve `user_agent` alanlarini zorunlu tutuyor (son kullanicinin
bilgisi bekleniyor); sunucu tarafinda calistigimiz icin kendi degerlerimizi
gonderiyoruz.
"""
from __future__ import annotations

import os

from ..models import Project
from ..normalize import clean_text, detect_contract, detect_work_mode, parse_date, strip_html
from .base import BaseSource, EnvVar, FetchError

ENDPOINT = "http://public.api.careerjet.net/search"

#: locale_code -> ulke adi. Config'ten degistirilebilir.
LOCALE_COUNTRIES = {
    "en_GB": "United Kingdom", "de_DE": "Germany", "de_AT": "Austria",
    "de_CH": "Switzerland", "nl_NL": "Netherlands", "en_US": "United States",
    "fr_FR": "France", "es_ES": "Spain", "it_IT": "Italy", "pl_PL": "Poland",
    "tr_TR": "Turkey", "en_IE": "Ireland", "sv_SE": "Sweden",
}

DEFAULT_LOCALES = ["en_GB", "de_DE", "de_AT", "de_CH", "nl_NL", "en_US", "tr_TR"]


class CareerjetSource(BaseSource):
    name = "careerjet"
    requires_env = "CAREERJET_AFFID"
    title = "Careerjet"
    signup_url = "https://www.careerjet.com/partners/api/"
    notes = "90 ulke agregatoru; locale listesi taranir."
    env_vars = (EnvVar("CAREERJET_AFFID", "Ortak kimligi (affid)"),)
    list_options = ("queries", "locales")
    probe_trim = ("queries", "locales")          # her locale ayri istek

    def fetch(self, query: str) -> list[Project]:
        affid = os.environ.get("CAREERJET_AFFID", "").strip()
        if not affid:
            raise FetchError(
                "CAREERJET_AFFID yok. Ucretsiz kimlik: "
                "https://www.careerjet.com/partners/api/ -> .env dosyasina ekleyin."
            )

        locales = self.options.get("locales") or DEFAULT_LOCALES
        pages = int(self.options.get("pages", 2))
        page_size = int(self.options.get("page_size", 99))
        contract_only = bool(self.options.get("contract_only", False))

        projects: list[Project] = []
        for locale in locales:
            for page in range(1, pages + 1):
                params = {
                    "keywords": query or "SAP ABAP",
                    "locale_code": locale,
                    "affid": affid,
                    "pagesize": page_size,
                    "page": page,
                    "user_ip": self.options.get("user_ip", "127.0.0.1"),
                    "user_agent": self.options.get("user_agent", "SAP-Proje-Radari/1.0"),
                    "sort": "date",
                }
                if contract_only:
                    params["contracttype"] = "c"     # c = contract
                data = self.client.get(ENDPOINT, params=params).json()
                if data.get("type") == "ERROR":
                    raise FetchError(f"Careerjet hatasi: {data.get('error')}")
                jobs = data.get("jobs") or []
                if not jobs:
                    break
                projects.extend(self._to_project(job, locale) for job in jobs)
                if len(jobs) < page_size:
                    break
        return projects

    def _to_project(self, job: dict, locale: str) -> Project:
        title = clean_text(job.get("title"))
        description = strip_html(job.get("description"))
        location = clean_text(job.get("locations"))
        salary = clean_text(job.get("salary"))

        return Project(
            source=self.name,
            source_id=self.make_id(job.get("url", ""), title),
            url=job.get("url", ""),
            title=title,
            company=clean_text(job.get("company")),
            location=location,
            country=LOCALE_COUNTRIES.get(locale, locale),
            work_mode=detect_work_mode(title, location, description),
            is_contract=detect_contract(title, description),
            budget_raw=salary,
            description=description,
            posted_at=parse_date(job.get("date")),
        )
