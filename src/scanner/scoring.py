"""Proje skorlama: anahtar kelimeler + calisma sekli / sozlesme sinyalleri.

Onculuk sirasi: remote contract SAP projesi > hybrid > onsite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .models import HYBRID, ONSITE, REMOTE, Project
from .normalize import fold


@lru_cache(maxsize=512)
def _pattern(keyword: str) -> re.Pattern[str]:
    """Kelime siniri ile eslesme ("rap" -> "raporlama" icinde saymasin)."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])")


def contains(text: str, keyword: str) -> bool:
    return bool(_pattern(keyword).search(text))


@dataclass
class ScoreResult:
    score: int
    hits: list[str]
    rejected_reason: str = ""

    @property
    def accepted(self) -> bool:
        return not self.rejected_reason


class Scorer:
    def __init__(self, keywords: dict[str, Any]):
        self.title_multiplier = float(keywords.get("title_multiplier", 2.0))
        self.must_any = [fold(k) for k in keywords.get("must_any") or []]
        self.boost = {fold(k): int(v) for k, v in (keywords.get("boost") or {}).items()}
        self.penalty = {fold(k): int(v) for k, v in (keywords.get("penalty") or {}).items()}
        self.hard_exclude = [fold(k) for k in keywords.get("hard_exclude") or []]
        self.min_score = int(keywords.get("min_score", 0))

        signals = keywords.get("signals") or {}
        self.remote_boost = int(signals.get("remote_boost", 0))
        self.hybrid_boost = int(signals.get("hybrid_boost", 0))
        self.onsite_penalty = int(signals.get("onsite_penalty", 0))
        self.contract_boost = int(signals.get("contract_boost", 0))
        self.permanent_penalty = int(signals.get("permanent_penalty", 0))
        self.country_boost = {fold(k): int(v) for k, v in (signals.get("country_boost") or {}).items()}
        self.fresh_boost = int(signals.get("fresh_boost", 0))
        self.fresh_days = int(signals.get("fresh_days", 7))

    def score(self, project: Project) -> ScoreResult:
        title = fold(project.title)
        body = fold(project.searchable_text)

        if self.must_any and not any(contains(body, k) for k in self.must_any):
            return ScoreResult(0, [], "must_any esleme yok")
        for bad in self.hard_exclude:
            if contains(body, bad):
                return ScoreResult(0, [], f"hard_exclude: {bad}")

        total = 0.0
        hits: list[str] = []
        for keyword in self.must_any:
            if contains(body, keyword):
                hits.append(keyword)
        for keyword, weight in self.boost.items():
            if contains(body, keyword):
                total += weight * (self.title_multiplier if contains(title, keyword) else 1.0)
                hits.append(keyword)
        for keyword, weight in self.penalty.items():
            if contains(body, keyword):
                total -= weight
                hits.append("-" + keyword)

        total += self._signal_score(project, hits)
        return ScoreResult(int(round(total)), sorted(set(hits)))

    def _signal_score(self, project: Project, hits: list[str]) -> float:
        """Asil is burada: remote + contract olan ilan one cikar."""
        total = 0.0

        if project.work_mode == REMOTE:
            total += self.remote_boost
            hits.append("+remote")
        elif project.work_mode == HYBRID:
            total += self.hybrid_boost
            hits.append("+hybrid")
        elif project.work_mode == ONSITE:
            total -= self.onsite_penalty
            hits.append("-onsite")

        if project.is_contract is True:
            total += self.contract_boost
            hits.append("+contract")
        elif project.is_contract is False:
            total -= self.permanent_penalty
            hits.append("-kadrolu")

        country = fold(project.country or project.location)
        for key, weight in self.country_boost.items():
            if country and key in country:
                total += weight
                hits.append(f"+{key}")
                break

        if self.fresh_boost and project.posted_at:
            from datetime import datetime, timezone
            age_days = (datetime.now(timezone.utc) - project.posted_at).days
            if age_days <= self.fresh_days:
                total += self.fresh_boost
                hits.append("+yeni")

        return total

    def apply(self, projects: list[Project]) -> tuple[list[Project], list[tuple[Project, str]]]:
        """Skorlar; (kabul edilenler, (proje, red_sebebi) listesi) doner."""
        kept: list[Project] = []
        dropped: list[tuple[Project, str]] = []
        for project in projects:
            result = self.score(project)
            project.score = result.score
            project.keywords_hit = result.hits
            if result.accepted:
                kept.append(project)
            else:
                dropped.append((project, result.rejected_reason))
        kept.sort(key=lambda p: (-p.score, p.posted_at is None,
                                 -(p.posted_at.timestamp() if p.posted_at else 0)))
        return kept, dropped
