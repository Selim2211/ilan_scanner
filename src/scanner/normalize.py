"""HTML temizleme, tarih/calisma sekli/butce ayristirma."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .models import HYBRID, ONSITE, REMOTE, UNKNOWN

_WS = re.compile(r"\s+")

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})

#: Almanca/Hollandaca/Fransizca karsiliklar da burada: jooble-de ve careerjet
#: (de_DE, nl_NL, fr_FR) o dillerde ilan donduruyor. Eksikken Almanca bir
#: "Fernarbeit" ilani onsite sayilip remote_boost'u (12 puan, en buyuk etken)
#: alamiyor ve min_score esigini gecemiyordu.
REMOTE_HINTS = ("remote", "uzaktan", "home office", "home-office", "homeoffice",
                "work from home", "evden", "telecommute", "anywhere", "100% remote",
                "fully remote", "remote-first",
                "fernarbeit", "mobiles arbeiten", "ortsunabhangig", "ortsunabhängig",
                "remote arbeit", "vollstandig remote", "thuiswerken", "op afstand",
                "teletravail", "télétravail", "a distance", "à distance")
HYBRID_HINTS = ("hybrid", "hibrit", "partially remote", "kismen uzaktan", "2 days onsite",
                "flexible office", "remote/onsite", "onsite/remote",
                "hybrides arbeiten", "teilweise remote", "teilweise vor ort",
                "hybride werken", "hybride")
ONSITE_HINTS = ("onsite", "on-site", "is yerinde", "ofisten", "vor ort", "in office",
                "office-based", "relocation required")

CONTRACT_HINTS = ("freelance", "freelancer", "contract", "contractor", "c2c", "corp to corp",
                  "b2b", "proje bazli", "sozlesmeli", "interim", "consultant", "danisman",
                  "temporary", "gig", "self-employed", "selbstandig",
                  "freiberuflich", "freiberufler", "werkvertrag", "auf projektbasis",
                  "projektarbeit", "zelfstandig", "opdracht", "independant", "indépendant")
PERMANENT_HINTS = ("permanent", "full-time employee", "festanstellung", "kadrolu",
                   "tam zamanli calisan", "unbefristet")

_PERCENT = re.compile(r"(\d{1,3})\s*%")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T].*)?$")

_REL_UNITS = {
    "saniye": "seconds", "dakika": "minutes", "saat": "hours",
    "gun": "days", "hafta": "weeks", "ay": "months", "yil": "years",
    "second": "seconds", "minute": "minutes", "hour": "hours",
    "day": "days", "week": "weeks", "month": "months", "year": "years",
}
_REL_RE = re.compile(r"(\d+)\s*(saniye|dakika|saat|gun|hafta|ay|yil|second|minute|hour|day|week|month|year)s?")


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    if "<" in value and ">" in value:
        value = BeautifulSoup(value, "lxml").get_text(" ")
    return clean_text(value)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", str(value)).replace("\xa0", " ")
    return _WS.sub(" ", value).strip()


def fold(value: str | None) -> str:
    """Kucuk harf + Turkce karakter sadelestirme. Keyword eslemesi icin."""
    if not value:
        return ""
    return clean_text(value).translate(_TR_MAP).lower()


def detect_work_mode(*parts: str) -> str:
    """Metinden remote / hybrid / onsite cikarir.

    Sira onemli: "hybrid remote" gecen ilan hybrid'dir, remote degil.
    """
    text = fold(" ".join(p for p in parts if p))
    if not text:
        return UNKNOWN
    if any(h in text for h in HYBRID_HINTS):
        return HYBRID
    if any(h in text for h in REMOTE_HINTS):
        return REMOTE
    if any(h in text for h in ONSITE_HINTS):
        return ONSITE
    return UNKNOWN


def mode_from_percent(percent: int | None) -> str:
    """freelancermap gibi kaynaklar remote oranini yuzde veriyor."""
    if percent is None:
        return UNKNOWN
    if percent >= 80:
        return REMOTE
    if percent >= 20:
        return HYBRID
    return ONSITE


def parse_percent(value: str | None) -> Optional[int]:
    match = _PERCENT.search(value or "")
    return int(match.group(1)) if match else None


def detect_contract(*parts: str) -> Optional[bool]:
    """Proje bazli / contract mi, kadrolu mu? Bilinmiyorsa None."""
    text = fold(" ".join(p for p in parts if p))
    if not text:
        return None
    if any(h in text for h in CONTRACT_HINTS):
        return True
    if any(h in text for h in PERMANENT_HINTS):
        return False
    return None


def parse_date(value: str | int | float | None, now: datetime | None = None) -> Optional[datetime]:
    """ISO tarih, epoch ms/s, "19.05.2026" veya "3 gun once" gibi ifadeleri cozer."""
    if value in (None, ""):
        return None
    now = now or datetime.now(timezone.utc)

    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)

    text = fold(value)
    if not text:
        return None
    if text in ("bugun", "today", "az once", "yeni", "new"):
        return now
    if text in ("dun", "yesterday"):
        return now - timedelta(days=1)

    match = _REL_RE.search(text)
    if match and ("once" in text or "ago" in text or "evvel" in text):
        amount = int(match.group(1))
        unit = _REL_UNITS[match.group(2)]
        if unit == "months":
            return now - timedelta(days=30 * amount)
        if unit == "years":
            return now - timedelta(days=365 * amount)
        return now - timedelta(**{unit: amount})

    parsed = _parse_iso(value)
    if parsed is None:
        try:
            parsed = dateparser.parse(value, dayfirst=True, fuzzy=True)
        except (ValueError, OverflowError, TypeError):
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > now + timedelta(days=1):
        # Gun/ay yer degistirmis olabilir (10/08 -> 08/10). Ay-once okunusu gecmisteyse onu al.
        swapped = _swap_day_month(parsed)
        if swapped is not None and swapped <= now + timedelta(days=1):
            return swapped
    return parsed


def _parse_iso(value: str) -> Optional[datetime]:
    """ISO 8601 metinleri dayfirst kurallarina birakmadan cozer.

    dateutil'e dayfirst=True ile gitmek "2026-08-10" tarihini 8 Ekim yapiyordu;
    ilan gelecek tarihli gorunup taze sayiliyordu.
    """
    text = str(value).strip().replace("Z", "+00:00")
    if not _ISO_RE.match(text):
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _swap_day_month(value: datetime) -> Optional[datetime]:
    try:
        return value.replace(month=value.day, day=value.month)
    except ValueError:
        return None


# --- butce ------------------------------------------------------------
#: Kaynaklar butceyi serbest metin veriyor: "$100-110/saat", "€600/Tag",
#: "1.200 TL/gun", "5k". Siralayabilmek icin sayiya cevrilir.

_CURRENCY_SIGNS = {"$": "USD", "€": "EUR", "£": "GBP", "₺": "TRY"}
_CURRENCY_WORDS = {"usd": "USD", "eur": "EUR", "gbp": "GBP", "try": "TRY", "tl": "TRY",
                   "chf": "CHF", "pln": "PLN", "aud": "AUD", "cad": "CAD", "sek": "SEK"}

#: periyot -> gunluk carpan. Sabit ucret (fixed) karsilastirilabilir degil: None.
_PERIOD_HINTS = (
    ("hour", "hour"), ("saat", "hour"), ("/hr", "hour"), ("/h", "hour"), ("stunde", "hour"),
    ("day", "day"), ("gun", "day"), ("tag", "day"), ("/d", "day"), ("daily", "day"),
    ("week", "week"), ("hafta", "week"), ("woche", "week"),
    ("month", "month"), ("ay", "month"), ("monat", "month"), ("mo.", "month"),
    ("year", "year"), ("yil", "year"), ("jahr", "year"), ("annum", "year"),
)
_PERIOD_TO_DAILY = {"hour": 8.0, "day": 1.0, "week": 1 / 5, "month": 1 / 22, "year": 1 / 260}

#: Kaba USD kurlari - siralama icin yeterli, muhasebe icin degil.
#: `panel.currency_rates` ile ezilebilir (Ayarlar ekraninda duzenlenir).
DEFAULT_RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CHF": 1.12, "TRY": 0.03,
                 "PLN": 0.25, "SEK": 0.095, "AUD": 0.66, "CAD": 0.73, "INR": 0.012}

#: Periyot yazmayan ama bu araliga dusen tutar yillik maas sayilir ("$100k - $150k"
#: Jooble'da en yaygin bicim). Ust sinir ihale toplamlarini disarida birakiyor.
_ASSUMED_YEARLY = (20_000.0, 1_000_000.0)

_NUMBER = re.compile(r"\d[\d.,]*\s*[kK]?")


def _to_number(text: str) -> Optional[float]:
    """'1.200', '1,200', '1200.50', '5k' -> float."""
    raw = text.strip()
    multiplier = 1000.0 if raw[-1:] in ("k", "K") else 1.0
    raw = raw.rstrip("kK").strip()
    if not raw:
        return None
    # Son ayrac ondalik mi binlik mi: ardindan tam 3 hane geliyorsa binliktir.
    last = max(raw.rfind("."), raw.rfind(","))
    if last == -1:
        digits = raw
    elif len(raw) - last - 1 == 3:
        digits = raw.replace(".", "").replace(",", "")
    else:
        digits = raw[:last].replace(".", "").replace(",", "") + "." + raw[last + 1:]
    try:
        return float(digits) * multiplier
    except ValueError:
        return None


def parse_budget(raw: str | None, currency: str = "") -> tuple[Optional[float], str, str]:
    """(miktar, periyot, para_birimi) doner; cozulemezse (None, '', '').

    Aralikta ust uc alinir ("$100-110/saat" -> 110): ilanin tavani hangi isin
    daha degerli oldugunu daha iyi anlatiyor.
    """
    text = _WS.sub(" ", str(raw or "")).strip()
    if not text:
        return None, "", ""

    folded = fold(text)
    found = _CURRENCY_WORDS.get((currency or "").strip().lower(), (currency or "").strip().upper())
    for sign, code in _CURRENCY_SIGNS.items():
        if sign in text:
            found = code
            break
    else:
        for word, code in _CURRENCY_WORDS.items():
            if re.search(rf"\b{word}\b", folded):
                found = code
                break

    period = ""
    for hint, name in _PERIOD_HINTS:
        if hint in folded:
            period = name
            break

    numbers = [n for n in (_to_number(m.group()) for m in _NUMBER.finditer(text)) if n]
    if not numbers:
        return None, period, found
    return max(numbers), period, found


def infer_period(amount: Optional[float], period: str, engagement: str = "") -> str:
    """Periyot yazmayan maaslari yillik sayar.

    En yaygin butce bicimi "$100k - $150k" ve periyot yazmiyor; bunlari disarida
    birakmak siralamayi ise yaramaz hale getirirdi. Ihale (TED) toplamlari maas
    degil, o yuzden hep sabit birakilir.
    """
    if period or amount is None or engagement == "ihale":
        return period
    low, high = _ASSUMED_YEARLY
    return "year" if low <= amount <= high else ""


def budget_daily(amount: Optional[float], period: str, currency: str,
                 rates: dict[str, float] | None = None) -> Optional[float]:
    """Karsilastirilabilir gunluk tutar (USD yaklasik).

    Periyot bilinmiyorsa sabit ucret sayilir ve None doner: saatlik 110 ile
    goturu 5000'i ayni kolonda siralamak yaniltici olurdu.
    """
    factor = _PERIOD_TO_DAILY.get(period)
    if amount is None or factor is None:
        return None
    rate = (rates or DEFAULT_RATES).get((currency or "USD").upper(), 1.0)
    return round(amount * factor * rate, 2)
