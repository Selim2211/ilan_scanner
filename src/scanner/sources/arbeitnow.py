"""Arbeitnow acik is panosu API'si - Avrupa agirlikli remote ilanlar.

    GET https://www.arbeitnow.com/api/job-board-api?page=1

Anahtar gerekmiyor, filtre parametresi de yok: tum sayfa cekilip yerelde
anahtar kelimeye gore suzuluyor. SAP hacmi dusuk ama bedava ve remote etiketi
guvenilir (`remote: true` alani var).
"""
from __future__ import annotations

from ..models import REMOTE, Project
from ..normalize import clean_text, detect_contract, detect_work_mode, parse_date, strip_html
from .base import BaseSource

ENDPOINT = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowSource(BaseSource):
    name = "arbeitnow"
    title = "Arbeitnow"
    signup_url = "https://www.arbeitnow.com/api/job-board-api"
    notes = ("Ucretsiz acik API (Almanya/AB). Arama parametresi yok: her sorgu ayni "
             "sayfalari cekiyor, fazla sorgu 429 verir. Iki sorgu yeterli.")

    def fetch(self, query: str) -> list[Project]:
        pages = int(self.options.get("pages", 3))
        needle = (query or "sap").lower()

        projects: list[Project] = []
        for page in range(1, pages + 1):
            batch = self.client.get(ENDPOINT, params={"page": page}).json().get("data") or []
            if not batch:
                break
            for item in batch:
                haystack = " ".join([
                    str(item.get("title", "")), str(item.get("description", "")),
                    " ".join(item.get("tags") or []), " ".join(item.get("job_types") or []),
                ]).lower()
                if needle in haystack:
                    projects.append(self._to_project(item))
        return projects

    def _to_project(self, item: dict) -> Project:
        title = clean_text(item.get("title"))
        description = strip_html(item.get("description"))[:4000]
        location = clean_text(item.get("location"))
        tags = [clean_text(t) for t in (item.get("tags") or [])]
        job_types = " ".join(item.get("job_types") or [])

        return Project(
            source=self.name,
            source_id=str(item.get("slug") or self.make_id(item.get("url", ""), title)),
            url=item.get("url", ""),
            title=title,
            company=clean_text(item.get("company_name")),
            location=location,
            country="Germany" if "germany" in location.lower() else "",
            work_mode=REMOTE if item.get("remote") else detect_work_mode(title, location, description),
            engagement=job_types,
            is_contract=detect_contract(title, job_types, description),
            description=description,
            skills=tags,
            posted_at=parse_date(item.get("created_at")),
        )
