"""freelancer.com aktif proje adapter'i (resmi genel API, anahtar gerekmiyor).

    GET /api/projects/0.1/projects/active/?jobs[]=<yetenek_id>&limit=&offset=

Yetenek kodlari (/api/projects/0.1/jobs/ listesinden):
    1733 ABAP · 1734 ABAP Web Dynpro · 1784 ABAP List Viewer (ALV)
    267 SAP · 1438 SAP 4 Hana · 1489 SAP HANA · 1532 SAP PI · 1533 SAP CPI

Burasi kucuk/orta butceli uzaktan isler icin uygun; kurumsal contract isleri
freelancermap tarafinda.
"""
from __future__ import annotations

from ..models import REMOTE, Project
from ..normalize import clean_text, detect_work_mode, parse_date, strip_html
from .base import BaseSource

ENDPOINT = "https://www.freelancer.com/api/projects/0.1/projects/active/"
PROJECT_URL = "https://www.freelancer.com/projects/{seo_url}"

DEFAULT_SKILL_IDS = [1733, 1734, 1784, 267, 1438, 1489, 1532, 1533]


class FreelancerComSource(BaseSource):
    name = "freelancercom"
    title = "freelancer.com"
    signup_url = "https://www.freelancer.com/api/docs/"
    notes = "Genel API, anahtar gerekmez. Yetenek kodlariyla aranir (ABAP: 1733/1734/1784)."
    list_options = ("skill_ids",)

    def fetch(self, query: str) -> list[Project]:
        limit = int(self.options.get("limit", 50))
        pages = int(self.options.get("pages", 2))
        skills = self.options.get("skill_ids") or DEFAULT_SKILL_IDS

        projects: list[Project] = []
        for offset in range(0, pages * limit, limit):
            params: list[tuple[str, str]] = [
                ("limit", str(limit)), ("offset", str(offset)),
                ("job_details", "true"), ("full_description", "true"),
                ("location_details", "true"),
            ]
            for skill in skills:
                params.append(("jobs[]", str(skill)))
            if query:
                params.append(("query", query))

            result = self.client.get(ENDPOINT, params=params).json().get("result") or {}
            batch = result.get("projects") or []
            if not batch:
                break
            projects.extend(self._to_project(item) for item in batch)
            if len(batch) < limit:
                break
        return projects

    def _to_project(self, item: dict) -> Project:
        title = clean_text(item.get("title"))
        description = strip_html(item.get("description") or item.get("preview_description"))
        skills = [clean_text(j.get("name")) for j in (item.get("jobs") or []) if j.get("name")]
        budget = item.get("budget") or {}
        currency = (item.get("currency") or {}).get("code", "")
        minimum, maximum = budget.get("minimum"), budget.get("maximum")
        if minimum and maximum:
            budget_raw = f"{minimum:.0f}-{maximum:.0f} {currency}"
        elif minimum:
            budget_raw = f"{minimum:.0f}+ {currency}"
        else:
            budget_raw = ""
        if item.get("type") == "hourly":
            budget_raw = (budget_raw + "/saat").strip()

        seo = item.get("seo_url") or ""
        return Project(
            source=self.name,
            source_id=str(item.get("id")),
            url=PROJECT_URL.format(seo_url=seo) if seo else "https://www.freelancer.com/",
            title=title,
            company="",                     # ilan sahibi anonim
            location="",
            work_mode=detect_work_mode(title, description) if description else REMOTE,
            engagement="Freelance",
            is_contract=True,               # platformun tamami proje bazli
            budget_raw=budget_raw,
            currency=currency,
            description=description,
            skills=skills,
            posted_at=parse_date(item.get("time_submitted") or item.get("submitdate")),
        )
