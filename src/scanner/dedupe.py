"""Ayni ilanin birden cok kaynaktan gelmesini tespit eder."""
from __future__ import annotations

import hashlib
import re

from .models import Project
from .normalize import fold

_NOISE = re.compile(r"[^a-z0-9 ]+")
#: baslikta anlam tasimayan, kaynaktan kaynaga degisen ekler
_FILLER = re.compile(
    r"\b(m/w/d|m/f/d|w/m/d|mwd|remote|freelance|contract|is ilani|ilani|"
    r"urgent|hemen|senior|junior|sr|jr)\b"
)


def _key_part(value: str) -> str:
    value = _FILLER.sub(" ", fold(value))
    return " ".join(_NOISE.sub(" ", value).split())


def fingerprint(project: Project) -> str:
    """Baslik + sirket (sirket yoksa sehir) ayni ise ayni ilan sayilir."""
    title = _key_part(project.title)
    company = _key_part(project.company) or _key_part(project.location)
    raw = f"{title}|{company}" if title else project.url
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def dedupe(projects: list[Project]) -> list[Project]:
    """Ayni fingerprint'e sahip ilanlari tek kayda indirir; kaynaklari birlestirir."""
    seen: dict[str, Project] = {}
    for project in projects:
        project.fingerprint = project.fingerprint or fingerprint(project)
        existing = seen.get(project.fingerprint)
        if existing is None:
            seen[project.fingerprint] = project
            continue
        if project.source != existing.source and project.source not in existing.also_seen_on:
            existing.also_seen_on.append(project.source)
        if len(project.description) > len(existing.description):
            existing.description = project.description
        # calisma sekli bilinmiyorsa diger kaynaktan tamamla
        if existing.work_mode == "unknown" and project.work_mode != "unknown":
            existing.work_mode = project.work_mode
            existing.remote_percent = project.remote_percent
        if existing.is_contract is None and project.is_contract is not None:
            existing.is_contract = project.is_contract
        for field_name in ("company", "location", "country", "budget_raw", "duration",
                           "starts_at", "engagement"):
            if not getattr(existing, field_name) and getattr(project, field_name):
                setattr(existing, field_name, getattr(project, field_name))
        if project.posted_at and (existing.posted_at is None or project.posted_at > existing.posted_at):
            existing.posted_at = project.posted_at
        existing.skills = sorted({*existing.skills, *project.skills})
    return list(seen.values())
