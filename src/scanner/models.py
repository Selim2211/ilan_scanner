"""Kanonik proje/ilan sematasi."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

#: calisma sekli - onceligimiz remote, sonra hybrid
REMOTE = "remote"
HYBRID = "hybrid"
ONSITE = "onsite"
UNKNOWN = "unknown"

WORK_MODES = (REMOTE, HYBRID, ONSITE, UNKNOWN)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Project:
    """Bir kaynaktan cekilmis proje / contract ilani."""

    source: str                        # "jooble", "freelancermap", ...
    source_id: str
    url: str
    title: str
    company: str = ""
    location: str = ""
    country: str = ""
    work_mode: str = UNKNOWN           # remote | hybrid | onsite | unknown
    remote_percent: Optional[int] = None
    engagement: str = ""               # Freelance / Contract / Permanent (kaynagin dedigi)
    #: True = arz tarafi: birinin SUNDUGU hizmet ilani (is arayan degil, hizmet satan).
    #: Bizim aradigimiz talep degil; hicbir varsayilan listede gorunmez.
    is_supply: bool = False
    is_contract: Optional[bool] = None  # proje bazli mi (freelance/contract)
    duration: str = ""                 # "5 months+", "6 ay"
    starts_at: str = ""                # "9/2026"
    budget_raw: str = ""               # "1500-12500 USD", "80 EUR/saat"
    currency: str = ""
    description: str = ""
    skills: list[str] = field(default_factory=list)
    posted_at: Optional[datetime] = None
    fetched_at: datetime = field(default_factory=utcnow)
    keywords_hit: list[str] = field(default_factory=list)
    score: int = 0
    fingerprint: str = ""
    also_seen_on: list[str] = field(default_factory=list)

    @property
    def searchable_text(self) -> str:
        return "\n".join([self.title, self.description, " ".join(self.skills),
                          self.engagement, self.location])

    @property
    def is_remote(self) -> bool:
        return self.work_mode == REMOTE

    def to_dict(self) -> dict:
        return asdict(self)
