"""Tek kaynagi canli deneyip "veri geliyor mu" sorusunu cevaplar.

Veritabanina DOKUNMAZ: modul `Storage`i hic import etmez, boylece dry-run
yapisal olarak garanti (test bunu dogruluyor).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .config import load_config
from .settings import scrub
from .sources import REGISTRY, BlockedError, FetchError, HttpClient, MappingError, source_class

log = logging.getLogger(__name__)

#: Kontrol beklemesin diye: tek deneme, hiz siniri yok, kisa zaman asimi.
#: Tarama istemcisi 3 deneme + 2**n backoff kullaniyor; bir kontrol 14+ sn surerdi.
PROBE_TIMEOUT = 20.0
PROBE_TITLES = 3


@dataclass
class ProbeResult:
    source: str
    ok: bool = False
    count: int = 0
    titles: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    message: str = ""
    detail: str = ""
    query: str = ""

    def as_dict(self) -> dict:
        return {"source": self.source, "ok": self.ok, "count": self.count,
                "titles": self.titles, "elapsed_ms": self.elapsed_ms,
                "message": self.message, "detail": self.detail, "query": self.query}


def _probe_client(config: dict) -> HttpClient:
    http = config.get("http") or {}
    return HttpClient(
        user_agent=http.get("user_agent", "SAP-Proje-Radari/0.1"),
        timeout=min(float(http.get("timeout_seconds", 30)), PROBE_TIMEOUT),
        rate_limit_seconds=0.0,
        max_retries=1,
    )


def _pick_query(given: str | None, options: dict, config: dict) -> str:
    if given and given.strip():
        return given.strip()
    for candidate in (options.get("queries"), config.get("queries")):
        if candidate:
            first = str(candidate[0]).strip()
            if first:
                return first
    return "SAP ABAP"


def _blocked_message(text: str) -> str:
    if "401" in text:
        return "Anahtar reddedildi (401) - API anahtarini kontrol edin."
    if "403" in text:
        return "Kaynak erisimi engelledi (403) - bot korumasi veya yetkisiz anahtar."
    if "429" in text:
        return "Cok fazla istek (429) - birkac dakika sonra tekrar deneyin."
    return "Kaynak erisimi engelledi."


def probe_source(name: str, query: str | None = None, config: dict | None = None,
                 titles: int = PROBE_TITLES, options: dict | None = None) -> ProbeResult:
    """Kaynagi tek sorgu, tek sayfa ile dener.

    `options` verilirse (Ayarlar'daki "yeni kaynak" formu) kaydetmeden denenir;
    verilmezse kayitli ayarlar kullanilir.
    """
    result = ProbeResult(source=name)
    config = config or load_config()
    options = dict(options if options is not None else
                   (config.get("sources") or {}).get(name) or {})
    options.setdefault("name", name)

    cls = source_class(name, options)
    if cls is None:
        result.message = f"Bilinmeyen kaynak: {name}"
        return result
    options["pages"] = 1
    for key in cls.probe_trim:                   # tek dilim yeter, kontrol hizli olsun
        if options.get(key):
            options[key] = list(options[key])[:1]

    result.query = _pick_query(query, options, config)

    from .pipeline import missing_credentials     # dairesel import olmasin diye burada

    client = _probe_client(config)
    source = cls(client, options)
    missing = missing_credentials(source)
    if missing:
        client.close()
        result.message = f"{', '.join(missing)} tanimli degil - once anahtari kaydedin."
        return result

    started = time.monotonic()
    try:
        projects = source.fetch(result.query)
    except MappingError as exc:
        # Ag sorunu degil, tanim yanlis: mesaj dogrudan gosterilir
        result.message = scrub(str(exc))
    except BlockedError as exc:
        result.message = _blocked_message(str(exc))
        result.detail = scrub(str(exc))
    except FetchError as exc:
        result.message = "Kaynak yanit vermedi."
        result.detail = scrub(str(exc))
    except Exception as exc:                      # noqa: BLE001 - kontrol hicbir sey patlatmasin
        log.warning("kontrol hatasi (%s): %s", name, exc)
        result.message = f"Beklenmeyen hata: {type(exc).__name__}"
        result.detail = scrub(str(exc))
    else:
        result.ok = True
        result.count = len(projects)
        result.titles = [scrub((p.title or "")[:90]) for p in projects[:titles]]
        result.message = (f"{result.count} ilan geldi"
                          if result.count else "Baglanti tamam ama bu sorgu icin sonuc yok.")
    finally:
        client.close()
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result
