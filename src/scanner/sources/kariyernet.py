"""kariyer.net adapter - Turkiye pazari (hibrit/yerinde isler icin).

    https://www.kariyer.net/is-ilanlari?kw=<sorgu>&cp=<sayfa>

Kart: div.job-list-card-item; sehir/tarih/calisma sekli kart attribute'larinda.
Uyari: bot korumasi var - ~35 istekten sonra IP bazli 403, yasak ~10 dakika.
Bu yuzden `pages` dusuk tutulur ve detay sayfasi acilmaz.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..models import Project
from ..normalize import clean_text, detect_contract, detect_work_mode, parse_date
from .base import BaseSource

log = logging.getLogger(__name__)

BASE = "https://www.kariyer.net"
SEARCH = BASE + "/is-ilanlari?kw={query}&cp={page}"


class KariyerNetSource(BaseSource):
    name = "kariyernet"
    title = "kariyer.net"
    signup_url = "https://www.kariyer.net/"
    notes = ("Turkiye pazari (cogu yerinde/kadrolu). Bot korumasi var: ~35 istekte IP bazli "
             "403, yasak ~10 dk. 'pages' dusuk tutulmali.")

    def fetch(self, query: str) -> list[Project]:
        pages = int(self.options.get("pages", 2))
        projects: list[Project] = []
        for page in range(1, pages + 1):
            url = SEARCH.format(query=quote(query or "SAP ABAP"), page=page)
            try:
                html = self.client.get(url).text
            except Exception:
                if projects:
                    log.warning("%s: sayfa %s alinamadi, %s ilanla devam", self.name, page, len(projects))
                    break
                raise
            batch = self._parse(html)
            if not batch:
                break
            projects.extend(batch)
        return projects

    def _parse(self, html: str) -> list[Project]:
        soup = BeautifulSoup(html, "lxml")
        projects: list[Project] = []
        for card in soup.select("div.job-list-card-item"):
            link = card.select_one("a.k-ad-card, a[data-test=ad-card-item]")
            if not link or not link.get("href"):
                continue
            href = link["href"]
            title = self._text(card, "[data-test=ad-card-title]")
            if not title:
                continue

            work_model = self._text(card, "[data-test=work-model]") or clean_text(card.get("workmodeltext", ""))
            work_type = self._text(card, "[data-test=text]") or clean_text(card.get("worktypetext", ""))
            location = self._text(card, "[data-test=location]") or clean_text(card.get("cityname", ""))
            posted_raw = clean_text(card.get("time", ""))

            projects.append(Project(
                source=self.name,
                source_id=clean_text(card.get("jobcode", "")) or self.make_id(href),
                url=href if href.startswith("http") else BASE + href,
                title=title,
                company=self._text(card, "[data-test=subtitle]"),
                location=location,
                country="Turkey",
                work_mode=detect_work_mode(work_model, title, location),
                engagement=" / ".join(p for p in (work_type, work_model) if p),
                is_contract=detect_contract(work_type, title),
                description=" ".join(p for p in (work_type, work_model) if p),
                posted_at=parse_date(posted_raw + " once" if posted_raw else None),
            ))
        return projects

    @staticmethod
    def _text(node, selector: str) -> str:
        found = node.select_one(selector)
        return clean_text(found.get_text(" ")) if found else ""
