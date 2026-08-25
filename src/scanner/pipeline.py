"""Tarama akisi: kaynaklar -> dedupe -> puanlama -> DB -> kapanan ilan tespiti -> bildirim."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import load_config, load_keywords, resolve_path
from .dedupe import dedupe
from .models import Project
from .scoring import Scorer
from .settings import scrub
from .sources import REGISTRY, BlockedError, HttpClient, source_class
from .storage import Storage

log = logging.getLogger(__name__)

#: Ilan sayfasinda bunlardan biri geciyorsa ilan kapanmistir.
CLOSED_MARKERS = ("no longer available", "no longer accepting", "position has been filled",
                  "ilan yayindan kaldirilmis", "bu ilan yayinda degil", "yayindan kaldirildi",
                  "project is no longer", "job expired", "vacancy is closed")

#: Ilan yayinda ama ACILMIYOR: sayfa yerine bolge engeli / giris duvari / bot kontrolu
#: cikiyor. Kapatmiyoruz (baskasi icin acilabilir), sari halka ile isaretliyoruz.
PROBLEM_MARKERS = (
    ("bölge kısıtlı", ("not available in your region", "not available in your country",
                       "job is not available in your", "not accepting applications from",
                       "nicht in ihrer region", "bolgenizde yayinda degil")),
    ("giriş gerekiyor", ("sign in to view", "log in to view", "login to view",
                         "members only", "please log in to see", "create an account to view")),
    ("bot koruması", ("verify you are human", "are you a robot", "checking your browser",
                      "enable javascript to continue", "unusual traffic from your")),
)


@dataclass
class ScanResult:
    fetched: int = 0
    kept: int = 0
    new: int = 0
    updated: int = 0
    closed: int = 0
    projects: list[Project] = field(default_factory=list)
    new_projects: list[Project] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)   # (kaynak, hata)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (baslik, sebep)


def build_client(config: dict) -> HttpClient:
    http = config.get("http", {})
    return HttpClient(
        user_agent=http.get("user_agent", "SAP-Proje-Radari/0.1"),
        timeout=float(http.get("timeout_seconds", 30)),
        rate_limit_seconds=float(http.get("rate_limit_seconds", 2.0)),
        max_retries=int(http.get("max_retries", 3)),
    )


def active_sources(config: dict, only: list[str] | None = None) -> dict[str, dict]:
    """config.yaml'daki aktif kaynaklar; --source ile daraltilabilir."""
    result: dict[str, dict] = {}
    for name, options in (config.get("sources") or {}).items():
        options = options or {}
        if source_class(name, options) is None:
            log.warning("bilinmeyen kaynak: %s", name)
            continue
        if only:
            if name in only:
                result[name] = options
            continue
        if options.get("enabled"):
            result[name] = options
    return result


def missing_credentials(source) -> list[str]:
    """Kaynagin eksik olan zorunlu ortam degiskenleri.

    `requires_env` tek degisken tutuyordu; bazi kaynaklar iki anahtar istiyor ve biri
    eksikken kaynak calisip 401 aliyordu. Artik hepsi kontrol edilir.
    """
    import os

    required = [var.name for var in source.env_requirements() if var.required]
    if not required:
        return []
    # upwork: hazir bir access token varsa uygulama anahtarlari gerekmez
    if "UPWORK_CLIENT_ID" in required and os.environ.get("UPWORK_TOKEN", "").strip():
        return []
    return [name for name in required if not os.environ.get(name, "").strip()]


def _missing_credentials(source) -> bool:
    return bool(missing_credentials(source))


def _quick_sources(config: dict, sources: dict[str, dict]) -> dict[str, dict]:
    """Hizli tur icin kaynaklari daralt ve sayfa/sorgu sayisini kis.

    Amac: yeni ilani dakikalar icinde yakalamak. Derin sayfalama ve yavas
    kaynaklar tam turda taranir.
    """
    auto = config.get("auto_scan") or {}
    wanted = auto.get("quick_sources") or ["jooble", "freelancercom"]
    pages = int(auto.get("quick_pages", 1))
    max_queries = int(auto.get("quick_max_queries", 3))
    quick: dict[str, dict] = {}
    for name in wanted:
        if name not in sources:
            continue
        options = dict(sources[name])
        options["pages"] = pages
        if options.get("queries"):
            options["queries"] = list(options["queries"])[:max_queries]
        if options.get("keywords"):
            options["keywords"] = list(options["keywords"])[:max_queries]
        quick[name] = options
    return quick


def run_scan(only_sources: list[str] | None = None, dry_run: bool = False,
             queries: list[str] | None = None, quick: bool = False,
             config: dict | None = None, keywords: dict | None = None,
             profile_id: int = 0) -> ScanResult:
    """Tam tarama; `quick=True` ise hizli tur.

    Hizli tur yalnizca hizli kaynaklarin ilk sayfasini okur ve **kayip tespiti
    yapmaz**: kapsam kismi oldugu icin gorulmeyen ilan "kayip" sayilamaz.

    `config`/`keywords` verilirse diskten okunmaz. Zamanlayici bunu kullanarak
    her arama profilini kendi ayarlariyla tarar: yoksa yalnizca ETKIN profilin
    sorgulari calisir, digerinin ilanlari hic toplanmazdi.
    `profile_id` bildirimleri hangi profile ait oldugunu isaretler.
    """
    config = config or load_config()
    scorer = Scorer(keywords if keywords is not None else load_keywords())
    queries = queries or config.get("queries") or ["SAP ABAP"]
    sources = active_sources(config, only_sources)
    if quick:
        sources = _quick_sources(config, sources)
    if not sources:
        raise SystemExit("Aktif kaynak yok. config/config.yaml icinde enabled: true yapin.")

    freshness = config.get("freshness") or {}
    suspect_after = int(freshness.get("suspect_after_missing_scans", 2))
    close_unverifiable_after = int(freshness.get("close_unverifiable_after_missing_scans", 6))
    skip_sources = set(freshness.get("verify_skip_sources") or [])
    suspect_check_limit = int(freshness.get("suspect_check_limit", 40))
    # 0 = kapali. Linki dogrulanabilen kaynaklarda son care kapatma esigi.
    close_after = int(freshness.get("close_after_missing_scans", 0))
    api_verify_limit, api_miss_threshold = api_verify_settings(config)
    notify_cfg = config.get("notifications") or {}
    notify_min_score = int(notify_cfg.get("min_score", 0))
    notify_keep_days = int(notify_cfg.get("keep_days", 30))

    client = build_client(config)
    storage = None if dry_run else Storage(resolve_path(config["database"]))
    result = ScanResult()
    collected: list[Project] = []
    healthy_sources: set[str] = set()

    try:
        for name, options in sources.items():
            source = source_class(name, options)(client, options)
            missing = missing_credentials(source)
            if missing:
                # Kimlik bilgisi girilmemis kaynak her turda hata uretmesin
                log.info("%s atlandi: %s tanimli degil", name, ", ".join(missing))
                continue
            source_queries = options.get("queries") or queries
            source_ok = True
            for query in dict.fromkeys(source_queries):
                run_id = storage.start_run(name, query) if storage else None
                found, error = source.safe_fetch(query)
                # Jooble anahtari URL yolunda gidiyor; hata mesaji URL'yi iceriyor ve
                # bu mesaj hem runs tablosuna hem panele dusuyor.
                error = scrub(error)
                log.info("%s <- '%s': %s ilan", name, query, len(found))
                collected.extend(found)
                if error:
                    source_ok = False
                    result.errors.append((name, error))
                if storage and run_id is not None:
                    storage.finish_run(run_id, len(found), 0,
                                       status="error" if error else "ok", error=error)
                if error.startswith(BlockedError.__name__):
                    log.warning("%s engelledi, kalan sorgular atlaniyor", name)
                    break
            if source_ok:
                healthy_sources.add(name)
        result.fetched = len(collected)

        unique = dedupe(collected)
        _restore_descriptions(unique, storage)
        kept, dropped = scorer.apply(unique)
        kept = _filter_old(kept, int(config.get("scan", {}).get("max_age_days", 0)))
        result.projects = kept
        result.kept = len(kept)
        result.dropped = [(project.title, reason) for project, reason in dropped]

        if storage:
            result.new, result.updated, new_items = storage.upsert(kept)
            result.new_projects = new_items
            closed_rows = [] if quick else _resolve_missing(
                storage, client, kept, healthy_sources, suspect_after,
                close_unverifiable_after, skip_sources, suspect_check_limit,
                close_after=close_after, config=config,
                api_verify_limit=api_verify_limit, api_miss_threshold=api_miss_threshold)
            result.closed = len(closed_rows)
            _notify(storage, new_items, closed_rows, notify_min_score, profile_id)
            storage.prune_notifications(notify_keep_days)
    finally:
        client.close()
        if storage:
            storage.close()
    return result


def rescore_all(config: dict | None = None, keywords: dict | None = None) -> int:
    """Kayitli ilanlari aga cikmadan yeniden puanlar; butce kolonlarini da tazeler.

    keywords.yaml degistiginde (CLI: `scanner rescore`, panel: Ayarlar > Puanlama)
    tum satirlar yeni agirliklarla hesaplanir.
    """
    config = config or load_config()
    scorer = Scorer(keywords or load_keywords())
    rates = (config.get("panel") or {}).get("currency_rates")
    storage = Storage(resolve_path(config["database"]), rates=rates)
    try:
        updates = []
        for row in storage.all_rows():
            result = scorer.score(storage.project_from_row(row))
            updates.append((row["fingerprint"], result.score, result.hits))
        changed = storage.update_scores(updates)
        storage.refresh_budgets()
        return changed
    finally:
        storage.close()


def _resolve_missing(storage: Storage, client: HttpClient, kept: list[Project],
                     healthy_sources: set[str], suspect_after: int,
                     close_unverifiable_after: int, skip_sources: set[str],
                     check_limit: int, close_after: int = 0, config: dict | None = None,
                     api_verify_limit: int = 60, api_miss_threshold: int = 2) -> list:
    """Kaynak listesinden dusen ilanlarin gercekten kapanip kapanmadigini belirler.

    Kaynaklar sayfalanmis pencere donduruyor: yeni ilan gelince eski ilan pencereden
    duser ama yayinda kalir. Bu yuzden yokluk tek basina kapatmaz:

    1. Gorulmeyen ilanin sayaci artar (supheli).
    2. Sayac esigi asinca ilanin LINKI acilir: 404/410 ya da "kapandi" metni varsa kapatilir,
       sayfa hala aciliyorsa sayac sifirlanir (yanlis alarm).
    3. Link kontrolu yapilamayan kaynaklarda (bot duvari) ancak cok uzun sure
       gorulmeyen ilan kapatilir.
    4. Son care (`close_after`): linki dogrulanabilen kaynakta bile cok uzun sure
       ust uste gorunmeyen ilan kapatilir. Dogrulama tur basina sinirli oldugu icin
       (check_limit) buyuk listelerde sıra bazi ilanlara hic gelmiyordu: 6-10 turdur
       kayip 152 ilan "aktif" gorunmeye devam ediyordu.

    Sadece SORUNSUZ calisan kaynaklar icin yapilir: site coktu diye ilan kapanmaz.
    """
    closed: list = []
    for source in healthy_sources:
        seen = [p.fingerprint for p in kept
                if p.source == source or source in (p.also_seen_on or [])]
        storage.mark_missing(source, seen)

        if source in skip_sources:
            # Linki acilamiyor. Kaynak kendi API'sinden konusabiliyorsa once ONA
            # sorulur; yalnizca o da yoksa uzun sureli yokluga bakilir.
            checker = verifier_for(source, config, client) if config else None
            if checker is not None:
                suspects = storage.suspects(source, min_streak=suspect_after,
                                            limit=api_verify_limit)
                rows, yayinda, bilinmiyor = verify_with_source(storage, checker, suspects,
                                                               api_miss_threshold)
                closed.extend(rows)
                log.info("%s API dogrulamasi: %s yayinda, %s kapandi, %s bilinmiyor",
                         source, yayinda, len(rows), bilinmiyor)
                continue
            rows = storage.close_stale(source, close_unverifiable_after)
            closed.extend(rows)
            for row in rows:
                log.info("kapandi (uzun suredir listede yok): %s", row["title"])
            continue

        alive: list[str] = []
        for row in storage.suspects(source, min_streak=suspect_after, limit=check_limit):
            verdict = _check_link(client, row["url"])
            if verdict is True:
                storage.close_project(row["fingerprint"])
                closed.append(row)
                log.info("kapandi (link dogrulandi): %s", row["title"])
            elif verdict is False:
                alive.append(row["fingerprint"])
        if alive:
            # yayinda: pencereden dustugu icin kaybolmus gorunuyordu
            storage.clear_missing(alive)
            log.info("%s: %s ilan listeden dusmus ama yayinda", source, len(alive))

        if close_after:
            # sıra kendisine hic gelmeyen, cok uzun suredir kayip ilanlar
            rows = storage.close_stale(source, close_after)
            closed.extend(rows)
            for row in rows:
                log.info("kapandi (%s turdur listede yok): %s",
                         row["missing_streak"], row["title"])
    return closed


@dataclass
class LinkVerdict:
    """Bir ilan linkinin kontrol sonucu."""

    #: True = kapanmis, False = yayinda, None = karar verilemedi
    closed: bool | None = None
    #: bos degilse ilan yayinda ama sayfa acilmiyor (bolge engeli gibi)
    problem: str = ""
    status: int | None = None
    #: kaynak bizi engelledi (403/429): ilan hakkinda hicbir sey ogrenemedik
    blocked: bool = False

    def __iter__(self):
        """Eski (kapanmis_mi, sorun) ikilisi gibi cozulebilsin."""
        return iter((self.closed, self.problem))


def api_verify_settings(config: dict) -> tuple[int, int]:
    """(tur basina dogrulanacak ilan, kapatma icin gereken ardisik kacirma)"""
    freshness = config.get("freshness") or {}
    return (max(1, int(freshness.get("api_verify_limit", 60))),
            max(1, int(freshness.get("api_verify_miss_threshold", 2))))


def verifier_for(name: str, config: dict, client: HttpClient):
    """Kaynak kendi ilanlarini dogrulayabiliyorsa ornegini doner, yoksa None.

    Jooble'in ilan sayfalari 403 doner; kapanma karari link yerine kaynagin
    API'sinden alinir (bkz. JoobleSource.verify_alive).
    """
    options = (config.get("sources") or {}).get(name) or {}
    cls = source_class(name, options)
    if cls is None or not getattr(cls, "can_verify", False):
        return None
    source = cls(client, options)
    if missing_credentials(source):
        log.info("%s dogrulamasi atlandi: anahtar tanimli degil", name)
        return None
    return source


def verify_with_source(storage: Storage, source, rows: list, miss_threshold: int) -> tuple[list, int, int]:
    """Satirlari kaynagin API'sine sorar ve sonuca gore DB'yi gunceller.

    (kapatilanlar, yayinda_olanlar, karar_verilemeyenler) doner.

    Bulunamamak TEK BASINA kapatmaz: baslik aramasi ilani her zaman getirmiyor
    (olcumde ~%13 kaciriyor). Ust uste `miss_threshold` kez bulunamayan kapanir.
    """
    if not rows:
        return [], 0, 0
    sonuc = source.verify_alive(rows)
    yayinda = [r["fingerprint"] for r in rows if sonuc.get(r["fingerprint"]) is True]
    yok = [r["fingerprint"] for r in rows if sonuc.get(r["fingerprint"]) is False]
    bilinmiyor = sum(1 for r in rows if sonuc.get(r["fingerprint"]) is None)

    if yayinda:
        storage.clear_missing(yayinda)      # verified_at'i da tazeler
        storage.clear_api_miss(yayinda)
    sayaclar = storage.bump_api_miss(yok)
    kapatilan = []
    for row in rows:
        fp = row["fingerprint"]
        if sonuc.get(fp) is False and sayaclar.get(fp, 0) >= miss_threshold:
            storage.close_project(fp)
            kapatilan.append(row)
            log.info("kapandi (%s API'sinde %s turdur yok): %s",
                     source.name, sayaclar[fp], row["title"])
    return kapatilan, len(yayinda), bilinmiyor


def _inspect_link(client: HttpClient, url: str) -> LinkVerdict:
    """Ilan linkini acar ve ne oldugunu soyler.

    `client.request()` KULLANILMAZ: o 404'te FetchError, 403'te BlockedError
    firlatiyor ve durum kodu buraya hic ulasmiyordu - silinmis ilan (404)
    "karar verilemedi" sayilip sonsuza dek aktif kaliyordu. `probe()` yaniti
    oldugu gibi verir.
    """
    response = client.probe(url)
    if response is None:
        return LinkVerdict()                       # ag hatasi: kapali sayilmaz
    code = response.status_code
    if code in (404, 410):
        return LinkVerdict(closed=True, status=code)
    if code in (401, 403, 429):
        # Bot duvari / giris duvari: bizim erisimimizle ilgili, ilan yayinda olabilir.
        return LinkVerdict(status=code, blocked=True)
    if code >= 500:
        return LinkVerdict(status=code)            # site gecici olarak cokmus
    body = response.text.lower()
    if any(marker in body for marker in CLOSED_MARKERS):
        return LinkVerdict(closed=True, status=code)
    for reason, markers in PROBLEM_MARKERS:
        if any(marker in body for marker in markers):
            return LinkVerdict(closed=False, problem=reason, status=code)
    return LinkVerdict(closed=False, status=code)


def _check_link(client: HttpClient, url: str) -> bool | None:
    """Sadece kapanma karari (eski cagiranlar icin ince sarmalayici)."""
    return _inspect_link(client, url).closed


def active_profile_id(storage: Storage) -> int:
    """Tarama hangi arama profilinin ayarlariyla kosuyorsa onun kimligi (yoksa 0).

    Bildirimler bununla etiketlenir: her profil kendi akisini gorur.
    """
    from .profiles import Profiles

    try:
        profiles = Profiles(storage.path)
    except Exception:  # noqa: BLE001 - profil tablosu yoksa bildirim yine yazilsin
        return 0
    try:
        row = profiles.active()
        return int(row["id"]) if row else 0
    finally:
        profiles.close()


def _notify(storage: Storage, new_items: list[Project], closed_rows: list, min_score: int,
            profile_id: int | None = None) -> None:
    """Yeni ve kapanan ilanlari bildirim akisina yazar.

    `profile_id` verilmezse etkin profile yazilir. Cok profilli turda mutlaka
    verilmeli: aksi halde 3RD Party taramasinin bildirimleri SAP akisina duserdi.
    """
    payload = []
    for project in new_items:
        if project.score < min_score:
            continue
        detail = " · ".join(x for x in [project.company, project.location,
                                        project.budget_raw, project.duration] if x)
        payload.append({"kind": "new", "fingerprint": project.fingerprint, "title": project.title,
                        "detail": detail, "url": project.url, "source": project.source,
                        "score": project.score, "work_mode": project.work_mode})
    for row in closed_rows:
        payload.append({"kind": "closed", "fingerprint": row["fingerprint"], "title": row["title"],
                        "detail": "ilan kaynaktan kalkti", "url": row["url"],
                        "source": row["source"], "score": row["score"] or 0,
                        "work_mode": row["work_mode"] or ""})
    if payload:
        hedef = active_profile_id(storage) if profile_id is None else profile_id
        storage.add_notifications(payload, profile_id=hedef)


def _restore_descriptions(projects: list[Project], storage: Storage | None) -> None:
    """Kaynak bu sefer aciklama vermediyse DB'dekini geri koyar.

    Aksi halde ilan sadece basliga gore yeniden skorlanip puani duserdi.
    """
    if storage is None:
        return
    stored = storage.load_descriptions(p.fingerprint for p in projects)
    for project in projects:
        if not project.description:
            project.description = stored.get(project.fingerprint, "")


def _filter_old(projects: list[Project], max_age_days: int) -> list[Project]:
    """Cok eski ilanlar rapora girmesin; tarihi bilinmeyen elenmez."""
    if max_age_days <= 0:
        return projects
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return [p for p in projects if p.posted_at is None or p.posted_at >= cutoff]


def verify_active(limit: int = 50, dry_run: bool = False, min_hours: int = 12) -> tuple[int, int]:
    """Aktif ilanlarin linklerini kontrol eder; 404/410 veya "kapandi" metni olanlari kapatir.

    En uzun suredir kontrol edilmemis ilanlardan baslar, boylece liste sirayla
    tazelenir. (kontrol_edilen, kapatilan) doner.
    """
    config = load_config()
    freshness = config.get("freshness") or {}
    skip_sources = freshness.get("verify_skip_sources") or []
    storage = Storage(resolve_path(config["database"]))
    client = build_client(config)
    checked = closed = flagged = 0
    verified: list[str] = []
    try:
        rows = storage.verify_candidates(limit=limit, min_hours=min_hours,
                                         skip_sources=skip_sources)
        for row in rows:
            checked += 1
            # Denenen her ilan "bakildi" sayilir: erisilemeyen link kapali degildir ama
            # isaretlenmezse sira ilerlemez ve her turda ayni ilanlara takilirdik.
            verified.append(row["fingerprint"])
            verdict = _inspect_link(client, row["url"])
            kapandi, sorun = verdict.closed, verdict.problem
            if not dry_run:
                # Elle konmus isaret ezilmesin: yalnizca otomatik isaretler guncellenir.
                elle = (row["flagged_by"] or "") not in ("", "otomatik")
                if sorun and not elle:
                    storage.set_flag(row["fingerprint"], sorun)
                    flagged += 1
                elif not sorun and not elle and (row["quality_flag"] or ""):
                    storage.clear_flag(row["fingerprint"])
            if kapandi:
                closed += 1
                log.info("kapanmis ilan: %s", row["title"])
                if not dry_run:
                    storage.close_project(row["fingerprint"])
                    storage.add_notification("closed", row["title"], "link kapali", row["url"],
                                             row["source"], row["score"] or 0,
                                             row["work_mode"] or "", row["fingerprint"],
                                             profile_id=active_profile_id(storage))
        if verified and not dry_run:
            storage.mark_verified(verified)
        if flagged:
            log.info("%s ilan sorunlu isaretlendi (bolge/giris/bot engeli)", flagged)
    finally:
        client.close()
        storage.close()
    return checked, closed
