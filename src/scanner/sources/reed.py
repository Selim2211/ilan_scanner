"""Reed.co.uk adapter - Birlesik Krallik'in en buyuk is panosu (ucretsiz anahtar).

    GET https://www.reed.co.uk/api/1.0/search?keywords=SAP ABAP&resultsToTake=100
    Kimlik: HTTP Basic - kullanici adi = API anahtari, parola bos.

Anahtar: https://www.reed.co.uk/developers (ucretsiz) -> .env icine REED_API_KEY.

Neden degerli: UK contract pazari SAP icin Avrupa'nin en derin ikinci pazari ve
Reed ilanin "contract" mi "permanent" mi oldugunu, gunluk ucret araligiyla
birlikte veriyor.
"""
from __future__ import annotations

import os

from ..models import Project
from ..normalize import clean_text, detect_contract, detect_work_mode, parse_date, strip_html
from .base import BaseSource, EnvVar, FetchError

ENDPOINT = "https://www.reed.co.uk/api/1.0/search"


class ReedSource(BaseSource):
    name = "reed"
    requires_env = "REED_API_KEY"
    title = "Reed.co.uk"
    signup_url = "https://www.reed.co.uk/developers"
    notes = "UK contract pazari. Gunluk ucret araligi + contract/temp bayraklari."
    env_vars = (EnvVar("REED_API_KEY", "API anahtari"),)

    def fetch(self, query: str) -> list[Project]:
        key = os.environ.get("REED_API_KEY", "").strip()
        if not key:
            raise FetchError(
                "REED_API_KEY yok. Ucretsiz anahtar: https://www.reed.co.uk/developers "
                "-> .env dosyasina ekleyin."
            )

        take = int(self.options.get("results_per_page", 100))
        pages = int(self.options.get("pages", 2))

        projects: list[Project] = []
        for page in range(pages):
            params = {
                "keywords": query or "SAP ABAP",
                "resultsToTake": take,
                "resultsToSkip": page * take,
            }
            if self.options.get("contract_only"):
                params["contract"] = "true"
            batch = self.client.get(ENDPOINT, params=params, auth=(key, "")).json()
            results = batch.get("results") or []
            if not results:
                break
            projects.extend(self._to_project(item) for item in results)
            if len(results) < take:
                break
        return projects

    def _to_project(self, item: dict) -> Project:
        title = clean_text(item.get("jobTitle"))
        description = strip_html(item.get("jobDescription"))
        location = clean_text(item.get("locationName"))
        engagement = self._engagement(item)

        return Project(
            source=self.name,
            source_id=str(item.get("jobId") or self.make_id(item.get("jobUrl", ""), title)),
            url=item.get("jobUrl", ""),
            title=title,
            company=clean_text(item.get("employerName")),
            location=location,
            country="United Kingdom",
            work_mode=detect_work_mode(title, location, description, engagement),
            engagement=engagement,
            # Reed sozlesme tipini bayrak olarak veriyor; bayrak yoksa metinden bak
            is_contract=True if item.get("contractType") or item.get("contract")
            else detect_contract(title, description, engagement),
            budget_raw=self._salary(item),
            currency=clean_text(item.get("currency")),
            description=description,
            posted_at=parse_date(item.get("date") or item.get("datePosted")),
        )

    @staticmethod
    def _engagement(item: dict) -> str:
        parts = []
        if item.get("contractType") or item.get("contract"):
            parts.append("Contract")
        if item.get("temp"):
            parts.append("Temporary")
        if item.get("partTime"):
            parts.append("Part time")
        elif item.get("fullTime"):
            parts.append("Full time")
        return " · ".join(parts)

    @staticmethod
    def _salary(item: dict) -> str:
        low, high = item.get("minimumSalary"), item.get("maximumSalary")
        currency = clean_text(item.get("currency")) or "GBP"
        if not low and not high:
            return ""
        if low and high and float(low) != float(high):
            return f"{float(low):,.0f}-{float(high):,.0f} {currency}".replace(",", ".")
        return f"{float(low or high):,.0f} {currency}".replace(",", ".")
