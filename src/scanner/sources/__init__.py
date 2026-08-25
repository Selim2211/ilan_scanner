"""Kaynak kayit defteri."""
from __future__ import annotations

from .arbeitnow import ArbeitnowSource
from .base import BaseSource, BlockedError, EnvVar, FetchError, HttpClient, MappingError
from .careerjet import CareerjetSource
from .custom import CustomSource
from .freelancercom import FreelancerComSource
from .freelancermap import FreelancermapSource
from .jooble import JoobleDESource, JoobleSource, JoobleUKSource
from .kariyernet import KariyerNetSource
from .reed import ReedSource
from .ted import TedSource
from .upwork import UpworkSource

REGISTRY: dict[str, type[BaseSource]] = {
    JoobleSource.name: JoobleSource,
    JoobleUKSource.name: JoobleUKSource,
    JoobleDESource.name: JoobleDESource,
    FreelancermapSource.name: FreelancermapSource,
    FreelancerComSource.name: FreelancerComSource,
    UpworkSource.name: UpworkSource,
    ArbeitnowSource.name: ArbeitnowSource,
    KariyerNetSource.name: KariyerNetSource,
    ReedSource.name: ReedSource,
    CareerjetSource.name: CareerjetSource,
    TedSource.name: TedSource,
}


def is_custom(options: dict | None) -> bool:
    """Panelden eklenmis kaynak mi? (kodda karsiligi yok, tanimi config'te)"""
    return bool((options or {}).get("kind"))


def source_class(name: str, options: dict | None = None) -> type[BaseSource] | None:
    """Ad + ayara bakip kaynak sinifini secer.

    Kodda tanimli kaynaklar REGISTRY'den gelir; panelden eklenenler `kind` alani
    tasidigi icin CustomSource ile calisir.
    """
    if name in REGISTRY:
        return REGISTRY[name]
    return CustomSource if is_custom(options) else None


__all__ = ["REGISTRY", "BaseSource", "BlockedError", "EnvVar", "FetchError", "HttpClient",
           "MappingError", "CustomSource", "is_custom", "source_class",
           "JoobleSource", "JoobleUKSource", "JoobleDESource", "FreelancermapSource", "FreelancerComSource", "UpworkSource",
           "ArbeitnowSource", "KariyerNetSource", "ReedSource",
           "CareerjetSource", "TedSource"]
