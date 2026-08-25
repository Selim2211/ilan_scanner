"""freelancermap.com adapter - Avrupa'nin en buyuk IT contract/proje panosu.

Anahtar kelime sayfalari statik HTML:
    https://www.freelancermap.com/projects/<anahtar>       (or. sap-abap, sap, sap-hana)
    https://www.freelancermap.com/projects/remote          (sadece remote projeler)

Kart: div.project-card
    sirket   -> .project-info > div (ilk satir)
    baslik   -> a[data-testid=title]
    etiket   -> a[data-id=project-card-keyword-link]
    sehir    -> [data-testid=city]
    remote % -> [data-testid=remoteInPercent]   ("100% remote")
    tip      -> [data-testid=type]              ("Freelance")
    sure     -> [data-testid=duration]          ("5 months+")
    baslangic-> [data-testid=beginningMonth]    ("9/2026")
    tarih    -> [data-testid=created]           ("19.05.2026")

Site aramasi JS ile calisiyor ve ?page= parametresi yok sayiliyor; bu yuzden
sayfalama yerine BIRDEN COK anahtar kelime sayfasi taranip sonuclar birlestirilir.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from ..models import Project
from ..normalize import (clean_text, detect_contract, detect_work_mode, mode_from_percent,
                         parse_date, parse_percent)
from .base import BaseSource

BASE = "https://www.freelancermap.com"
KEYWORD_PAGE = BASE + "/projects/{keyword}"

DEFAULT_KEYWORDS = ["sap-abap", "sap", "sap-hana", "sap-fiori", "sap-applications", "abap"]


class FreelancermapSource(BaseSource):
    name = "freelancermap"
    title = "freelancermap"
    signup_url = "https://www.freelancermap.com/"
    notes = ("Avrupa contract pazarinin merkezi; anahtar gerekmez. Site aramasi JS ile "
             "calistigi icin anahtar kelime sayfalari taranir.")
    list_options = ("keywords",)

    def fetch(self, query: str) -> list[Project]:
        keywords = self.options.get("keywords") or DEFAULT_KEYWORDS
        if query:
            keywords = [query.strip().lower().replace(" ", "-")]

        projects: list[Project] = []
        for keyword in keywords:
            html = self.client.get(KEYWORD_PAGE.format(keyword=keyword)).text
            projects.extend(self._parse(html))
        return projects

    def _parse(self, html: str) -> list[Project]:
        soup = BeautifulSoup(html, "lxml")
        projects: list[Project] = []
        for card in soup.select("div.project-card"):
            link = card.select_one("a[data-testid=title], a[data-id=project-card-title]")
            if not link or not link.get("href"):
                continue
            href = link["href"]
            url = href if href.startswith("http") else BASE + href
            title = clean_text(link.get_text(" "))

            company = self._company(card)
            city = clean_text(self._text(card, "[data-testid=city]")).rstrip(",")
            remote_text = self._text(card, "[data-testid=remoteInPercent]")
            percent = parse_percent(remote_text)
            engagement = clean_text(self._text(card, "[data-testid=type]"))
            skills = [clean_text(a.get_text(" "))
                      for a in card.select("a[data-id=project-card-keyword-link]")]

            work_mode = mode_from_percent(percent)
            if percent is None:
                work_mode = detect_work_mode(title, remote_text, city)

            projects.append(Project(
                source=self.name,
                source_id=href.strip("/").split("/")[-1],
                url=url,
                title=title,
                company=company,
                location=city,
                country=city.split(",")[-1].strip() if "," in city else "",
                work_mode=work_mode,
                remote_percent=percent,
                engagement=engagement,
                is_contract=detect_contract(engagement, title) if engagement else True,
                duration=clean_text(self._text(card, "[data-testid=duration]")),
                starts_at=clean_text(self._text(card, "[data-testid=beginningMonth]")),
                skills=[s for s in skills if s],
                description=" | ".join(s for s in skills if s),
                posted_at=parse_date(clean_text(self._text(card, "[data-testid=created]"))),
            ))
        return projects

    @staticmethod
    def _text(card, selector: str) -> str:
        found = card.select_one(selector)
        return found.get_text(" ") if found else ""

    @staticmethod
    def _company(card) -> str:
        """Sirket adi baslik div'inden onceki ilk satirda duruyor."""
        info = card.select_one(".project-info")
        if not info:
            return ""
        for div in info.find_all("div", recursive=False):
            text = clean_text(div.get_text(" "))
            if text and not div.select_one("a[data-testid=title]"):
                return text
        return ""
