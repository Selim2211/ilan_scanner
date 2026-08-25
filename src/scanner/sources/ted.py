"""TED adapter - Avrupa Birligi kamu ihale ilanlari (Tenders Electronic Daily).

    POST https://api.ted.europa.eu/v3/notices/search
    govde: {"query": "FT ~ (\\"SAP S/4HANA\\") AND classification-cpv IN (...)", ...}

Anahtar gerekmiyor. GET desteklenmiyor (405), sorgu POST govdesinde gider.

Bu kaynak digerlerinden farkli: is ilani degil, **kamu kurumlarinin actigi
SAP ihaleleri**. Sirket hizmet sattigi icin dogrudan is kalemi; tek fark
basvurunun ihale dosyasiyla yapilmasi. Kayitlar `engagement = "ihale"` ile
isaretlenir, panel bunlara ayri rozet basar.

Sorgu notu: ifadeleri "OR" ile tek sorguda birlestirmek 0 sonuc donduruyor,
her ifade ayri sorgu olarak calistirilir (config -> sources.ted.queries).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import Project
from ..normalize import clean_text, parse_date, strip_html
from .base import BaseSource, FetchError

ENDPOINT = "https://api.ted.europa.eu/v3/notices/search"
NOTICE_URL = "https://ted.europa.eu/en/notice/-/detail/{number}"

#: BT hizmetleri (72*) ve yazilim (48*) - SAP ihaleleri bu iki daldan cikiyor
DEFAULT_CPV = ["72000000", "48000000"]

FIELDS = ["publication-number", "notice-title", "buyer-name", "buyer-country",
          "publication-date", "deadline-receipt-tender-date-lot", "classification-cpv",
          "total-value", "description-lot"]

#: TED 3 harfli ulke kodu kullaniyor
COUNTRY_NAMES = {
    "DEU": "Germany", "AUT": "Austria", "CHE": "Switzerland", "NLD": "Netherlands",
    "BEL": "Belgium", "FRA": "France", "ITA": "Italy", "ESP": "Spain", "POL": "Poland",
    "SWE": "Sweden", "DNK": "Denmark", "FIN": "Finland", "NOR": "Norway", "IRL": "Ireland",
    "PRT": "Portugal", "CZE": "Czechia", "ROU": "Romania", "HUN": "Hungary",
    "GRC": "Greece", "BGR": "Bulgaria", "HRV": "Croatia", "SVK": "Slovakia",
    "SVN": "Slovenia", "EST": "Estonia", "LVA": "Latvia", "LTU": "Lithuania",
    "LUX": "Luxembourg", "TUR": "Turkey",
}


def _text(value, prefer: str = "eng") -> str:
    """TED alanlari cok dilli sozluk donduruyor: {"eng": [...], "deu": [...]}."""
    if value is None:
        return ""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return _text(value[0]) if value else ""
    if isinstance(value, dict):
        chosen = value.get(prefer) or next(iter(value.values()), "")
        return _text(chosen)
    return clean_text(str(value))


class TedSource(BaseSource):
    name = "ted"
    title = "TED (AB ihaleleri)"
    signup_url = "https://ted.europa.eu/"
    notes = ("Is ilani degil, AB kamu ihalesi; anahtar gerekmez. Ifadeler OR ile "
             "birlestirilemedigi icin her ifade ayri sorgu.")
    list_options = ("queries", "cpv")

    def fetch(self, query: str) -> list[Project]:
        cpv = self.options.get("cpv") or DEFAULT_CPV
        days = int(self.options.get("max_days_old", 120))
        limit = int(self.options.get("limit", 50))
        pages = int(self.options.get("pages", 2))
        term = query or "SAP S/4HANA"

        expression = (f'FT ~ ("{term}") '
                      f'AND classification-cpv IN ({" ".join(cpv)}) '
                      f'AND publication-date >= today(-{days})')

        projects: list[Project] = []
        for page in range(1, pages + 1):
            payload = {"query": expression, "limit": limit, "page": page,
                       "scope": "ACTIVE", "fields": FIELDS}
            data = self.client.post(ENDPOINT, json=payload).json()
            if "notices" not in data:
                raise FetchError(f"TED beklenmeyen yanit: {str(data)[:160]}")
            notices = data.get("notices") or []
            if not notices:
                break
            projects.extend(self._to_project(n) for n in notices)
            if len(notices) < limit:
                break
        return projects

    def _to_project(self, notice: dict) -> Project:
        number = clean_text(notice.get("publication-number"))
        title = _text(notice.get("notice-title"))
        # TED basliklari "Ulke - CPV etiketi - gercek baslik" formatinda geliyor
        if title.count(" – ") >= 2:
            title = title.split(" – ", 2)[2]
        description = strip_html(_text(notice.get("description-lot")))[:4000]
        country = clean_text(_text(notice.get("buyer-country")))
        deadline = _text(notice.get("deadline-receipt-tender-date-lot"))

        return Project(
            source=self.name,
            source_id=number or self.make_id(title),
            url=NOTICE_URL.format(number=number) if number else "https://ted.europa.eu/",
            title=title,
            company=_text(notice.get("buyer-name")),
            location=COUNTRY_NAMES.get(country, country),
            country=COUNTRY_NAMES.get(country, country),
            # ihale dosyasi yerinde/uzaktan demiyor; puanlama bunu tahmine birakmasin
            work_mode="unknown",
            engagement="ihale",
            is_contract=True,                      # ihale = proje bazli is, tanimi geregi
            duration=f"son teklif: {deadline[:10]}" if deadline else "",
            budget_raw=self._value(notice),
            description=description,
            skills=[c for c in (notice.get("classification-cpv") or [])[:3]],
            posted_at=parse_date(_text(notice.get("publication-date"))[:10]),
        )

    @staticmethod
    def _value(notice: dict) -> str:
        value = notice.get("total-value")
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            amount = value.get("amount") or value.get("value")
            currency = value.get("currency") or "EUR"
            return f"{float(amount):,.0f} {currency}".replace(",", ".") if amount else ""
        if isinstance(value, (int, float)):
            return f"{float(value):,.0f} EUR".replace(",", ".")
        return clean_text(value) if value else ""

    @staticmethod
    def is_open(deadline: str) -> bool:
        """Teklif suresi gecmis ihaleler icin yardimci (panel filtreleri kullanabilir)."""
        parsed = parse_date(deadline)
        return parsed is None or parsed >= datetime.now(timezone.utc) - timedelta(days=1)
