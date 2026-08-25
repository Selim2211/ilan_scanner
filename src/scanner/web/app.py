"""Yerel web paneli (FastAPI + Jinja2): liste, filtreler, sayfalama, bildirimler.

Panel arka planda tarama yapan zamanlayiciyi da calistirir (config -> auto_scan),
boylece acik duran panelde veri surekli tazelenir.

Erisim kullanici girisiyle korunur (auth.py). Her rota, kullanicinin ilgili
yetkisini `_require` ile dogrular; sablonlar da ayni yetkiye bakip dugmeyi hic
gostermez - yetkisiz kullanici tiklayamayacagi bir dugme gormesin.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import maintenance as maint_mod
from .. import settings as settings_mod
from ..auth import (COOKIE_NAME, DEFAULT_PERMISSIONS, PERMISSION_GROUPS, PERMISSIONS, Auth,
                    User, validate_password, validate_username)
from ..config import (env_path, keywords_overlay_path, load_base_config, load_base_keywords,
                      load_config, load_keywords, overlay_path, resolve_path, save_env,
                      save_overlay)
from ..pipeline import rescore_all
from ..probe import probe_source
from ..sweep import LinkSweeper
from ..profiles import Profiles, capture_current, payload_from_form, validate_name
from ..scheduler import from_config as scheduler_from_config
from ..sources import REGISTRY, is_custom
from ..sources.custom import FIELD_SPEC
from ..sources.custom import KINDS as CUSTOM_KINDS
from ..storage import Storage
from ..version import APP_NAME, VERSION

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
#: Ilan uzerindeki isaretler. "Kaldir/gizle" kaldirildi: begenilmeyen ilan
#: icin "Kapandi" dugmesi kullaniliyor, ayri bir gizleme mekanizmasi yok.
STATUSES = {"new", "shortlist", "applied"}

#: Giris gerektirmeyen yollar. Baska her sey oturum ister.
PUBLIC_PATHS = ("/giris", "/static", "/favicon.ico")

#: Kontrol dugmesine arka arkaya basilinca ayni anda 10 istemci acilmasin
_PROBE_LOCK = threading.Semaphore(1)


def list_filters(min_score: int = 0, source: str = "", q: str = "", exclude: str = "",
                 mode: str = "", contract: int = 0, budget: int = 0, country: str = "",
                 days: int = 0, status: str = "", closed: int = 0, supply: int = 0,
                 date_from: str = "", date_to: str = "", date_field: str = "",
                 flag: str = "", profile_id: int = 0) -> dict:
    """Adres cubugu parametrelerini `Storage.query` filtrelerine cevirir.

    Liste ve temizlik turu ayni yardimciyi kullanir: "ekranda gordugun ilanlar
    kontrol edilir" sozunun tutmasi buna bagli, iki yerde ayri kurulan filtre
    zamanla birbirinden ayrilirdi.
    """
    return dict(
        min_score=min_score, source=source or None, search=q or None, exclude=exclude or None,
        work_mode=mode or None, contract_only=bool(contract), has_budget=bool(budget),
        country=country or None, max_age_days=int(days) or None,
        only_new=(status == "new"), shortlist=(status == "shortlist"),
        status=status if status in ("applied",) else None,
        closed_only=bool(closed), include_supply=bool(supply),
        date_from=date_from or None, date_to=date_to or None,
        date_field=date_field or "posted",
        flag=flag if flag in ("problem", "clean") else None,
        profile_id=profile_id,
    )


def _list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return []


def since(value: str | None) -> str:
    """ISO zaman damgasini "3 saat once" gibi kisa bir ifadeye cevirir."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - moment).total_seconds()
    if seconds < 0:
        return "az önce"
    minutes = seconds / 60
    if minutes < 1:
        return "az önce"
    if minutes < 60:
        return f"{int(minutes)} dk önce"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} sa önce"
    days = hours / 24
    if days < 30:
        return f"{int(days)} gün önce"
    months = days / 30
    if months < 12:
        return f"{int(months)} ay önce"
    return f"{int(months / 12)} yıl önce"


def age_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - moment).total_seconds() // 86400))


TEMPLATES.env.filters["since"] = since
TEMPLATES.env.filters["age_days"] = age_days


def page_window(current: int, total_pages: int, span: int = 2) -> list[int]:
    """Aktif sayfanin etrafindaki sayfa numaralari."""
    if total_pages <= 1:
        return [1]
    start = max(1, current - span)
    end = min(total_pages, current + span)
    return list(range(start, end + 1))


class PermissionDenied(Exception):
    """Yetkisiz istek; rota bunu firlatir, ust katman kullaniciya mesaj gosterir."""

    def __init__(self, permission: str):
        super().__init__(permission)
        self.permission = permission


def create_app(auto_scan: bool | None = None, interval_minutes: int | None = None,
               external_scheduler: object | None = None) -> FastAPI:
    """`external_scheduler` verilirse panel kendi zamanlayıcısını başlatmaz, sadece onun
    durumunu okur. Masaüstü uygulaması (desktop/service.py) taramayı kendi nesnesinde
    yürütüyor; bu olmadan panelin üst bardaki canlı durumu hep 'kapalı' görünürdü."""
    config = load_config()
    db_path = resolve_path(config["database"])
    # Ayarlar ekranindan degisen degerler yeniden baslatmadan gecerli olsun diye
    # config baslangicta yakalanmaz, bu tutucudan okunur.
    state: dict = {"config": config, "keywords": load_keywords()}

    def reload_config() -> dict:
        state["config"] = load_config()
        state["keywords"] = load_keywords()
        return state["config"]

    def page_size() -> int:
        return int((state["config"].get("panel") or {}).get("page_size", 20))

    def default_min_score() -> int:
        return int(state["keywords"].get("min_score", 0))

    def suspect_after() -> int:
        """Kac tur ust uste gorunmeyen ilan "supheli" sayilir.

        Rozet bu esikle ayni olmali: arayuz 1'den itibaren rozet basinca aktif
        listenin dortte ucu "kontrol ediliyor" gorunuyordu, oysa dogrulama
        yalnizca bu esigi gecenlere bakiyor.
        """
        return int((state["config"].get("freshness") or {}).get("suspect_after_missing_scans", 2))

    def hit_weights() -> dict[str, int]:
        """`keywords_hit` isaretlerini agirliklariyla eslestirir: 'abap' -> 6.

        scoring.py hit'leri su bicimde yaziyor: duz kelime = zorunlu/boost eslesmesi,
        '-kelime' = ceza, '+remote'/'+contract'/'-onsite'/'+yeni' = sinyal. Panel
        "bu ilan neden bu puani aldi" dokumunu bu haritayla yaziyor.
        """
        keywords = state["keywords"]
        signals = keywords.get("signals") or {}
        agirlik: dict[str, int] = {}
        for kelime, deger in (keywords.get("boost") or {}).items():
            agirlik[str(kelime)] = int(deger)
        for kelime, deger in (keywords.get("penalty") or {}).items():
            agirlik["-" + str(kelime)] = -int(deger)
        for isaret, anahtar in (("+remote", "remote_boost"), ("+hybrid", "hybrid_boost"),
                                ("+contract", "contract_boost"), ("+yeni", "fresh_boost")):
            if signals.get(anahtar):
                agirlik[isaret] = int(signals[anahtar])
        for isaret, anahtar in (("-onsite", "onsite_penalty"), ("-kadrolu", "permanent_penalty")):
            if signals.get(anahtar):
                agirlik[isaret] = -int(signals[anahtar])
        for ulke, deger in (signals.get("country_boost") or {}).items():
            agirlik["+" + str(ulke)] = int(deger)
        return agirlik

    def currency_rates() -> dict | None:
        return (state["config"].get("panel") or {}).get("currency_rates")

    def open_store() -> Storage:
        return Storage(db_path, rates=currency_rates())

    def open_auth() -> Auth:
        return Auth(db_path)

    def open_profiles() -> Profiles:
        return Profiles(db_path)

    # ilk acilista yonetici hesabi: admin / 123456
    starter = open_auth()
    try:
        if starter.ensure_admin():
            import logging
            logging.getLogger(__name__).info(
                "ilk yonetici olusturuldu - kullanici: admin, sifre: 123456")
    finally:
        starter.close()

    # Elle baslatilan temizlik turu (Listeyi kontrol et). Panel omru boyunca tek
    # ornek: ayni anda iki tur ag trafigini ikiye katlar ve ayni ilana iki kez gider.
    sweeper = LinkSweeper(db_path, config)

    if external_scheduler is not None:
        scheduler = external_scheduler
        owns_scheduler = False
    else:
        # kapaliyken de kurulur ama duraklatilmis baslar: Ayarlar'dan acilinca
        # paneli yeniden baslatmak gerekmesin
        scheduler = scheduler_from_config(config, override_minutes=interval_minutes,
                                          enabled=auto_scan, always=True)
        owns_scheduler = True

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if scheduler and owns_scheduler:
            scheduler.start()
        yield
        if scheduler and owns_scheduler:
            scheduler.stop()
        sweeper.stop()

    app = FastAPI(title=APP_NAME, lifespan=lifespan)
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # --- oturum kontrolu ---------------------------------------------
    @app.middleware("http")
    async def attach_user(request: Request, call_next):
        """Her istekte oturumu cozer; korumali yolda oturum yoksa girise gonderir."""
        path = request.url.path
        # Statik dosya icin oturum cozmeye gerek yok: her css/js istegi bir SQLite
        # baglantisi acardi ve sayfa basina onlarca istek geliyor.
        if path.startswith("/static"):
            request.state.user = None
            request.state.profile_id = 0
            request.state.profile_name = ""
            return await call_next(request)

        auth = open_auth()
        try:
            request.state.user = auth.session_user(request.cookies.get(COOKIE_NAME))
        finally:
            auth.close()

        # Etkin profil GENELDIR: tarama ve puanlama ayarlarini o belirliyor. Kullaniciya
        # atama, hangi profillere gecebilecegini sinirlar - kendi kopyasini vermez.
        profiles = open_profiles()
        try:
            active = profiles.active()
            request.state.profile_id = int(active["id"]) if active else 0
            request.state.profile_name = active["name"] if active else ""
        finally:
            profiles.close()

        if request.state.user is None and not path.startswith(PUBLIC_PATHS):
            hedef = request.url.path
            if request.url.query:
                hedef += "?" + request.url.query
            return RedirectResponse(f"/giris?{urlencode({'next': hedef})}", status_code=303)
        return await call_next(request)

    @app.exception_handler(PermissionDenied)
    async def permission_denied(request: Request, exc: PermissionDenied):
        return TEMPLATES.TemplateResponse(
            request, "denied.html",
            {**base_context(request), "permission": exc.permission, "active_tab": ""},
            status_code=403)

    def user_of(request: Request) -> User:
        return request.state.user

    def profile_of(request: Request) -> int:
        """Etkin arama profilinin kimligi; secili degilse 0."""
        return int(getattr(request.state, "profile_id", 0) or 0)

    def _require(request: Request, permission: str) -> User:
        """Yetkiyi dogrular; yoksa PermissionDenied firlatir."""
        user = user_of(request)
        if user is None or not user.can(permission):
            raise PermissionDenied(permission)
        return user

    def asset_version() -> str:
        """CSS/JS adresine eklenen damga: dosya degisince tarayici yenisini ceker.

        Damgasiz haliyle tarayici eski app.css/app.js'i onbellekten veriyordu ve
        arayuz degisiklikleri ancak Ctrl+F5 ile goruluyordu.
        """
        try:
            son = max((BASE_DIR / "static" / ad).stat().st_mtime
                      for ad in ("app.css", "app.js", "settings.js"))
            return f"{VERSION}-{int(son)}"
        except OSError:
            return VERSION

    def base_context(request: Request) -> dict:
        """Her sayfada bulunan ortak degerler: kullanici, yetki yardimcisi, etkin profil."""
        user = getattr(request.state, "user", None)
        return {
            "user": user,
            "can": (lambda perm: bool(user and user.can(perm))),
            "app_name": APP_NAME,
            "app_version": VERSION,
            "asset_v": asset_version(),
            "active_profile": getattr(request.state, "profile_name", ""),
            "active_profile_id": profile_of(request),
        }

    def scan_state() -> dict:
        if not scheduler:
            return {"enabled": False, "phase": "off", "running": False, "errors": [],
                    "interval_minutes": 0, "seconds_to_next": None, "last_finished": "",
                    "last_new": 0, "last_updated": 0, "last_closed": 0, "total_runs": 0}
        return scheduler.snapshot()

    # --- giris / cikis -------------------------------------------------
    @app.get("/giris", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/", hata: str = ""):
        if getattr(request.state, "user", None) is not None:
            return RedirectResponse(next or "/", status_code=303)
        return TEMPLATES.TemplateResponse(
            request, "login.html",
            {"next": next or "/", "error": hata, "app_name": APP_NAME,
             "app_version": VERSION, "user": None})

    @app.post("/giris")
    def login_submit(username: str = Form(""), password: str = Form(""),
                     next: str = Form("/")):
        auth = open_auth()
        try:
            result = auth.login(username, password)
        finally:
            auth.close()
        if result is None:
            return RedirectResponse(
                "/giris?" + urlencode({"next": next or "/",
                                       "hata": "Kullanıcı adı ya da şifre hatalı."}),
                status_code=303)
        token, _user = result
        response = RedirectResponse(next or "/", status_code=303)
        # yerel http panel: secure bayragi yok, ama tarayici scripti okuyamasin
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
        return response

    @app.post("/cikis")
    def logout(request: Request):
        auth = open_auth()
        try:
            auth.logout(request.cookies.get(COOKIE_NAME))
        finally:
            auth.close()
        response = RedirectResponse("/giris", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    # --- ilan listesi ---------------------------------------------------
    @app.get("/")
    def index(request: Request, q: str = "", exclude: str | None = None, source: str = "",
              mode: str | None = None, country: str | None = None, min_score: int | None = None,
              contract: int | None = None, budget: int | None = None, days: int | None = None,
              status: str = "", sort: str | None = None, closed: int = 0, supply: int = 0,
              page: int = 1, date_from: str = "", date_to: str = "", date_field: str = "",
              flag: str = ""):
        user = _require(request, "view_list")
        pid = profile_of(request)
        store = open_store()
        profiles = open_profiles()
        try:
            view = profiles.active_view()
            # ust bardaki secici: kullanici yalnizca kendisine atanan profilleri gorur
            switch_profiles = profiles.for_user(user.id, user.is_admin)
        finally:
            profiles.close()

        # Etkin profil, kullanicinin ACIKCA vermedigi filtreler icin varsayilani belirler.
        # Adres cubugunda deger varsa (None degilse) kullanici kazanir.
        def pick(value, key, fallback):
            if value is not None:
                return value
            return view.get(key, fallback) if view else fallback

        mode = pick(mode, "mode", "") or ""
        country = pick(country, "country", "") or ""
        exclude = pick(exclude, "exclude", "") or ""
        sort = pick(sort, "sort", "score") or "score"
        contract = int(pick(contract, "contract", 0) or 0)
        budget = int(pick(budget, "budget", 0) or 0)
        days = int(pick(days, "days", 0) or 0)
        score = default_min_score() if min_score is None else min_score

        size = page_size()
        page = max(1, page)
        # tarih araligi ile "son N gun" ayni ekseni filtreliyor: aralik seciliyse o kazanir
        if date_from or date_to:
            days = 0

        filters = list_filters(
            min_score=score, source=source, q=q, exclude=exclude, mode=mode, contract=contract,
            budget=budget, country=country, days=days, status=status, closed=closed,
            supply=supply, date_from=date_from, date_to=date_to, date_field=date_field,
            flag=flag, profile_id=pid)

        total = store.count(**filters)
        total_pages = max(1, ceil(total / size))
        page = min(page, total_pages)
        rows = store.query(**filters, order=sort, limit=size, offset=(page - 1) * size)

        query_params = {k: v for k, v in {
            "q": q, "exclude": exclude, "source": source, "mode": mode, "country": country,
            "min_score": score, "contract": contract or "", "budget": budget or "",
            "days": days or "", "status": status, "sort": sort if sort != "score" else "",
            "closed": closed or "", "supply": supply or "", "flag": flag,
            "date_from": date_from, "date_to": date_to,
            "date_field": date_field if date_field == "seen" else "",
        }.items() if v not in ("", None)}

        context = {
            **base_context(request),
            "rows": rows,
            "as_list": _list,
            "sources": store.sources(),
            "countries": store.countries(),
            "switch_profiles": switch_profiles,
            "stats": store.stats(pid),
            "unread": store.unread_count(pid),
            "scan": scan_state(),
            "newest_seen": store.newest_seen_at(),
            "filters": {"q": q, "exclude": exclude, "source": source, "mode": mode,
                        "country": country, "min_score": score, "contract": contract,
                        "budget": budget, "days": days, "status": status, "sort": sort,
                        "closed": closed, "supply": supply, "date_from": date_from,
                        "date_to": date_to, "date_field": date_field or "posted",
                        "flag": flag},
            "suspect_after": suspect_after(),
            "hit_weights": hit_weights(),
            "title_multiplier": float(state["keywords"].get("title_multiplier", 2.0)),
            "page": page, "total": total, "total_pages": total_pages, "page_size": size,
            "pages": page_window(page, total_pages),
            "base_query": urlencode(query_params),
            "shown_from": 0 if total == 0 else (page - 1) * size + 1,
            "shown_to": min(page * size, total),
            "active_tab": "list",
        }
        store.close()
        return TEMPLATES.TemplateResponse(request, "index.html", context)

    @app.get("/bildirimler")
    def notifications(request: Request, unread: int = 0):
        _require(request, "view_notifications")
        pid = profile_of(request)
        store = open_store()
        context = {
            **base_context(request),
            # her profil kendi bildirim akisini gorur
            "items": store.notifications(unread_only=bool(unread), limit=150, profile_id=pid),
            "unread": store.unread_count(pid),
            "stats": store.stats(pid),
            "scan": scan_state(),
            "newest_seen": store.newest_seen_at(),
            "only_unread": bool(unread),
            "active_tab": "notifications",
        }
        store.close()
        return TEMPLATES.TemplateResponse(request, "notifications.html", context)

    @app.get("/api/unread")
    def unread_count(request: Request):
        """Panel arka planda bunu yoklayip rozeti gunceller."""
        store = open_store()
        count = store.unread_count(profile_of(request))
        store.close()
        return JSONResponse({"unread": count})

    @app.get("/api/durum")
    def status_feed(request: Request, since_ts: str = ""):
        """Canli durum: tarama fazi, sayimlar ve `since_ts`den beri gelen yeni ilan sayisi."""
        pid = profile_of(request)
        store = open_store()
        payload = {
            "scan": scan_state(),
            "sweep": sweeper.snapshot(),
            "stats": store.stats(pid),
            "unread": store.unread_count(pid),
            "newest_seen": store.newest_seen_at(),
            "new_since": store.count_since(since_ts) if since_ts else 0,
        }
        store.close()
        return JSONResponse(payload)

    @app.post("/tara")
    def trigger_scan(request: Request, back: str = Form("/")):
        _require(request, "trigger_scan")
        if scheduler:
            scheduler.trigger()
        return RedirectResponse(back or "/", status_code=303)

    @app.post("/api/tara")
    def trigger_scan_api(request: Request):
        _require(request, "trigger_scan")
        if not scheduler:
            return JSONResponse({"ok": False, "reason": "arka plan taramasi kapali"}, status_code=409)
        started = scheduler.trigger()
        return JSONResponse({"ok": started, "scan": scheduler.snapshot()})

    @app.post("/api/temizlik")
    def start_sweep(request: Request, payload: dict | None = None):
        """Filtredeki ilanlarin linkini acip kapananlari kapatir (arka planda).

        Gövde, adres cubugundaki filtre parametreleridir: kullanici ekranda neyi
        goruyorsa o kontrol edilir. Liste ile ayni `list_filters` yardimcisindan
        gecer.
        """
        _require(request, "sweep_links")
        payload = payload or {}
        pid = profile_of(request)
        filters = list_filters(
            min_score=int(payload.get("min_score") or 0),
            source=str(payload.get("source") or ""), q=str(payload.get("q") or ""),
            exclude=str(payload.get("exclude") or ""), mode=str(payload.get("mode") or ""),
            contract=int(payload.get("contract") or 0), budget=int(payload.get("budget") or 0),
            country=str(payload.get("country") or ""), days=int(payload.get("days") or 0),
            status=str(payload.get("status") or ""),
            supply=int(payload.get("supply") or 0),
            date_from=str(payload.get("date_from") or ""),
            date_to=str(payload.get("date_to") or ""),
            date_field=str(payload.get("date_field") or ""),
            flag=str(payload.get("flag") or ""), profile_id=pid)
        # `closed` ALINMAZ: kapanmis ilan zaten kapanmis, tekrar kontrol bos trafik.

        user = request.state.user
        if not sweeper.start(filters, by=user.username if user else "", config=state["config"]):
            return JSONResponse({"ok": False, "message": "Bir kontrol zaten sürüyor.",
                                 "sweep": sweeper.snapshot()}, status_code=409)
        return JSONResponse({"ok": True, "sweep": sweeper.snapshot()})

    @app.post("/api/temizlik/iptal")
    def cancel_sweep(request: Request):
        _require(request, "sweep_links")
        durdu = sweeper.cancel()
        return JSONResponse({"ok": durdu, "sweep": sweeper.snapshot()})

    @app.post("/status")
    def set_status(request: Request, fingerprint: str = Form(...), status: str = Form(...),
                   back: str = Form("/")):
        _require(request, "mark_status")
        if status in STATUSES:
            store = open_store()
            store.set_status(fingerprint, status, profile_id=profile_of(request))
            store.close()
        return RedirectResponse(back or "/", status_code=303)

    @app.post("/kapandi")
    def report_closed(request: Request, fingerprint: str = Form(...), back: str = Form("/")):
        """Kullanıcı bir ilanın artık aktif olmadığını bildirir.

        Bazı kaynakların (Jooble) ilan linkleri bot korumasından ötürü otomatik
        doğrulanamıyor - bu, o boşluğu kapatan elle bildirim.
        """
        _require(request, "report_closed")
        store = open_store()
        store.report_closed(fingerprint, profile_id=profile_of(request))
        store.close()
        return RedirectResponse(back or "/", status_code=303)

    @app.post("/isaret")
    def toggle_flag(request: Request, fingerprint: str = Form(...), reason: str = Form(""),
                    back: str = Form("/")):
        """Sorunlu ilan isaretini koyar/kaldirir (sari halka).

        Ilan yayinda ama acilmiyorsa (bolge kisitli, giris duvari) kapatmak yanlis
        olur; isaret listede goze carpar ve filtrelenebilir.
        """
        user = _require(request, "flag_projects")
        store = open_store()
        try:
            row = store.get_by_fingerprint(fingerprint)
            if row is not None:
                if (row["quality_flag"] or "") == "problem":
                    store.clear_flag(fingerprint)
                else:
                    store.set_flag(fingerprint, reason.strip() or "elle işaretlendi",
                                   by=user.username)
        finally:
            store.close()
        return RedirectResponse(back or "/", status_code=303)

    @app.post("/bildirimler/okundu")
    def mark_read(request: Request, notification_id: int | None = Form(None),
                  back: str = Form("/bildirimler")):
        _require(request, "view_notifications")
        store = open_store()
        store.mark_notifications_read(notification_id, profile_id=profile_of(request))
        store.close()
        return RedirectResponse(back or "/bildirimler", status_code=303)

    @app.post("/bildirimler/sil")
    def delete_notification(request: Request, notification_id: int | None = Form(None),
                            hepsi: int = Form(0), back: str = Form("/bildirimler")):
        _require(request, "delete_notifications")
        store = open_store()
        try:
            if hepsi:
                # yalnizca etkin profilin akisi silinir, diger profilinki durur
                store.delete_all_notifications(profile_id=profile_of(request))
            elif notification_id is not None:
                store.delete_notification(notification_id)
        finally:
            store.close()
        return RedirectResponse(back or "/bildirimler", status_code=303)

    # --- Ayarlar -----------------------------------------------------
    def settings_redirect(section: str, ok: str = "", error: str = "") -> RedirectResponse:
        """Kaydettikten sonra bolume geri doner; bildirim query ile tasinir
        (session middleware yok, mevcut /status desenindeki gibi)."""
        params = urlencode({k: v for k, v in {"ok": ok, "hata": error}.items() if v})
        return RedirectResponse(f"/ayarlar?{params}#{section}", status_code=303)

    def settings_context(request: Request, ok: str = "", error: str = "") -> dict:
        config = state["config"]
        keywords = state["keywords"]
        store = open_store()
        auth = open_auth()
        profiles = open_profiles()
        user = user_of(request)
        pid = profile_of(request)
        try:
            profile_rows = profiles.all()
            context = {
                **base_context(request),
                "request": request,
                "catalog": settings_mod.source_catalog(config),
                "env_rows": settings_mod.env_state(config),
                "auto": config.get("auto_scan") or {},
                "panel": config.get("panel") or {},
                "notify": config.get("notifications") or {},
                "keywords": keywords,
                "signals": keywords.get("signals") or {},
                "queries": config.get("queries") or [],
                "field_spec": FIELD_SPEC,
                "custom_kinds": CUSTOM_KINDS,
                "fmt_lines": settings_mod.format_lines,
                "fmt_weights": settings_mod.format_weight_lines,
                "overlay_file": str(overlay_path()),
                "keywords_file": str(keywords_overlay_path()),
                "env_file": str(env_path()),
                "scan": scan_state(),
                "stats": store.stats(pid),
                "unread": store.unread_count(pid),
                "newest_seen": store.newest_seen_at(),
                "profiles": profile_rows,
                "profile_payload": Profiles.payload_of,
                "maintenance_tasks": [t.as_dict() for t in maint_mod.survey(config)]
                                     if user.can("maintenance") else [],
                "active_tab": "settings",
                "ok": ok,
                "error": error,
            }
        finally:
            store.close()
            auth.close()
            profiles.close()
        return context

    @app.get("/ayarlar")
    def settings_page(request: Request, ok: str = "", hata: str = ""):
        _require(request, "view_settings")
        reload_config()
        return TEMPLATES.TemplateResponse(request, "settings.html",
                                          settings_context(request, ok, hata))

    @app.post("/ayarlar/kaynaklar")
    async def save_sources(request: Request):
        """Kaynak listesi: acik/kapali, sayfa sayisi ve liste alanlari (sorgular vb.)."""
        _require(request, "edit_sources")
        form = await request.form()
        patch: dict = {"sources": {}}
        for row in settings_mod.source_catalog(state["config"]):
            name = row["name"]
            entry: dict = {"enabled": form.get(f"enabled__{name}") == "1"}
            pages = (form.get(f"pages__{name}") or "").strip()
            if pages.isdigit():
                entry["pages"] = int(pages)
            for key in row["list_names"]:
                if f"{key}__{name}" not in form:
                    continue
                text = form.get(f"{key}__{name}") or ""
                entry[key] = (settings_mod.parse_int_lines(text) if key == "skill_ids"
                              else settings_mod.parse_lines(text))
            patch["sources"][name] = entry
        save_overlay(patch, base=load_base_config())
        reload_config()
        return settings_redirect("kaynaklar", ok="Kaynak ayarları kaydedildi.")

    def _custom_definition(form) -> tuple[str, dict, str]:
        """Formdan ozel kaynak tanimi cikarir: (ad, tanim, hata)."""
        title = (form.get("title") or "").strip()
        name = settings_mod.slugify(form.get("name") or title)
        if not name:
            return "", {}, "Kaynağa bir ad verin."
        if name in REGISTRY:
            return "", {}, f"'{name}' kodda tanımlı bir kaynağın adı, başka bir ad seçin."

        kind = (form.get("kind") or "rss").strip().lower()
        if kind not in CUSTOM_KINDS:
            return "", {}, "Kaynak türü RSS ya da JSON olmalı."
        url = (form.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return "", {}, "Adres http:// ya da https:// ile başlamalı."

        fields = {key: (form.get(f"field__{key}") or "").strip() for key, _, _ in FIELD_SPEC}
        fields = {k: v for k, v in fields.items() if v}
        if kind == "json" and not (fields.get("title") and fields.get("url")):
            return "", {}, "JSON kaynakta başlık ve ilan linki alanları zorunlu."

        pages = (form.get("pages") or "1").strip()
        definition = {
            "kind": kind,
            "enabled": form.get("enabled") == "1",
            "title": title or name,
            "site_url": (form.get("site_url") or "").strip(),
            "notes": (form.get("notes") or "").strip(),
            "url": url,
            "items_path": (form.get("items_path") or "").strip(),
            "fields": fields,
            "pages": int(pages) if pages.isdigit() else 1,
            "queries": settings_mod.parse_lines(form.get("queries") or ""),
            "name": name,
        }
        return name, definition, ""

    def _definition_from_json(data: dict) -> tuple[dict | None, str]:
        """JSON gövdesini form biçimine çevirip aynı doğrulamadan geçirir."""
        flat = {k: ("" if v is None else v) for k, v in data.items()
                if k not in ("fields", "queries", "enabled")}
        for key, value in (data.get("fields") or {}).items():
            flat[f"field__{key}"] = value or ""
        queries = data.get("queries") or []
        flat["queries"] = "\n".join(queries) if isinstance(queries, list) else str(queries)
        flat["enabled"] = "1"
        _, definition, error = _custom_definition(flat)
        return (None, error) if error else (definition, "")

    @app.post("/ayarlar/kaynak-ekle")
    async def save_custom_source(request: Request):
        """Kodsuz kaynak ekleme/duzenleme (RSS ya da JSON + alan eslemesi)."""
        _require(request, "edit_sources")
        form = await request.form()
        name, definition, error = _custom_definition(form)
        if error:
            return settings_redirect("yeni-kaynak", error=error)
        save_overlay({"sources": {name: definition}}, base=load_base_config())
        reload_config()
        return settings_redirect("kaynaklar", ok=f"'{definition['title']}' kaydedildi.")

    @app.post("/ayarlar/kaynak-sil")
    async def delete_custom_source(request: Request):
        _require(request, "edit_sources")
        form = await request.form()
        name = (form.get("name") or "").strip()
        options = (state["config"].get("sources") or {}).get(name) or {}
        if not is_custom(options):
            return settings_redirect("kaynaklar",
                                     error="Yalnızca panelden eklenen kaynaklar silinebilir.")
        # None = mezar tasi: overlay'de "bu anahtari sil" demek
        save_overlay({"sources": {name: None}})
        reload_config()
        return settings_redirect("kaynaklar", ok=f"'{name}' silindi.")

    @app.post("/ayarlar/ne-ariyoruz")
    async def save_search(request: Request):
        """Aranan kelimeler ve puan agirliklari tek yerde; kaydedince yeniden puanlanir."""
        _require(request, "edit_search")
        form = await request.form()
        keywords = state["keywords"]
        try:
            # Sozlukler BIRLESTIRILMEZ, degistirilir: konu SAP'tan ERP'ye donunce
            # formdan silinen kelimeler duz birlestirmede geri geliyordu.
            keyword_patch = {
                "min_score": int((form.get("min_score") or keywords.get("min_score", 0))),
                "title_multiplier": float(form.get("title_multiplier")
                                          or keywords.get("title_multiplier", 2.0)),
                "must_any": settings_mod.parse_lines(form.get("must_any") or ""),
                "hard_exclude": settings_mod.parse_lines(form.get("hard_exclude") or ""),
                "boost": settings_mod.replace_map(
                    settings_mod.parse_weight_lines(form.get("boost") or ""),
                    keywords.get("boost")),
                "penalty": settings_mod.replace_map(
                    settings_mod.parse_weight_lines(form.get("penalty") or ""),
                    keywords.get("penalty")),
                "signals": settings_mod.replace_map(
                    settings_mod.parse_weight_lines(form.get("signals") or ""),
                    keywords.get("signals")),
            }
        except ValueError as exc:
            return settings_redirect("ne-ariyoruz", error=str(exc))

        if not keyword_patch["must_any"]:
            return settings_redirect(
                "ne-ariyoruz",
                error="Zorunlu kelimeler boş bırakılamaz - hiçbir ilan elenmez ve liste dolar.")

        queries = settings_mod.parse_lines(form.get("queries") or "")
        if not queries:
            return settings_redirect("ne-ariyoruz",
                                     error="En az bir arama sorgusu yazın.")

        save_overlay({"queries": queries}, base=load_base_config())
        save_overlay(keyword_patch, keywords_overlay_path(), base=load_base_keywords())
        reload_config()
        # etkin profil varsa onu da guncelle, yoksa profil ile gercek ayar ayrisir
        profiles = open_profiles()
        try:
            profiles.sync_active(state["config"], state["keywords"])
        finally:
            profiles.close()
        try:
            changed = rescore_all(state["config"], state["keywords"])
        except sqlite3.OperationalError:
            return settings_redirect(
                "ne-ariyoruz", error="Kaydedildi ama tarama sürerken puanlar güncellenemedi; "
                                     "birazdan tekrar deneyin.")
        return settings_redirect("ne-ariyoruz",
                                 ok=f"Kaydedildi, {changed} ilan yeniden puanlandı.")

    @app.post("/ayarlar/anahtarlar")
    async def save_keys(request: Request):
        """Bos birakilan alan degistirmez; 'sil' kutusu isaretliyse anahtar kaldirilir."""
        _require(request, "edit_keys")
        form = await request.form()
        values: dict[str, str] = {}
        for row in settings_mod.env_state(state["config"]):
            name = row["name"]
            if form.get(f"clear__{name}") == "1":
                values[name] = ""
            else:
                typed = (form.get(f"env__{name}") or "").strip()
                if typed:
                    values[name] = typed
        if not values:
            return settings_redirect("anahtarlar", ok="Değişiklik yok.")
        save_env(values)
        # Upwork uygulama bilgisi degisince eski token gecersiz: silinmezse
        # kaynak eski token'la calisip yaniltici sonuc verir.
        if {"UPWORK_CLIENT_ID", "UPWORK_CLIENT_SECRET"} & set(values):
            from ..sources import upwork_auth
            try:
                upwork_auth.token_path().unlink(missing_ok=True)
            except Exception:                     # noqa: BLE001 - token yoksa sorun degil
                pass
        return settings_redirect("anahtarlar", ok=f"{len(values)} anahtar güncellendi.")

    @app.post("/ayarlar/tarama")
    async def save_scan_settings(request: Request):
        _require(request, "edit_scan")
        form = await request.form()

        def as_int(field: str, fallback: int) -> int:
            raw = (form.get(field) or "").strip()
            try:
                return int(raw)
            except ValueError:
                return fallback

        auto = state["config"].get("auto_scan") or {}
        panel = state["config"].get("panel") or {}
        enabled = form.get("auto_enabled") == "1"
        patch = {
            "auto_scan": {
                "enabled": enabled,
                "interval_minutes": max(5, as_int("interval_minutes",
                                                  int(auto.get("interval_minutes", 30)))),
                "quick_interval_minutes": max(2, as_int("quick_interval_minutes",
                                                        int(auto.get("quick_interval_minutes", 8)))),
                "verify_limit": as_int("verify_limit", int(auto.get("verify_limit", 40))),
                "verify_every_n_scans": as_int("verify_every_n_scans",
                                               int(auto.get("verify_every_n_scans", 1))),
            },
            "panel": {"page_size": max(5, as_int("page_size", int(panel.get("page_size", 20))))},
        }
        save_overlay(patch, base=load_base_config())
        config = reload_config()
        if scheduler:
            auto = config.get("auto_scan") or {}
            scheduler.reconfigure(interval_minutes=auto.get("interval_minutes"),
                                  quick_minutes=auto.get("quick_interval_minutes"),
                                  verify_limit=auto.get("verify_limit"),
                                  verify_every=auto.get("verify_every_n_scans"))
            scheduler.set_enabled(enabled)
        return settings_redirect("tarama", ok="Tarama ayarları kaydedildi.")


    # --- bakim ----------------------------------------------------------
    @app.post("/ayarlar/bakim")
    async def run_maintenance(request: Request):
        _require(request, "maintenance")
        form = await request.form()
        selected = [key for key in form.getlist("gorev") if key]
        if not selected:
            return settings_redirect("bakim", error="Hiçbir görev seçilmedi.")
        try:
            report = maint_mod.run(selected, state["config"])
        except Exception as exc:  # noqa: BLE001 - bakim paneli devirmesin
            return settings_redirect("bakim", error=f"Temizlik yarıda kaldı: {exc}")
        return settings_redirect("bakim", ok=" ".join(report))

    # --- arama profilleri ----------------------------------------------
    @app.post("/ayarlar/profil-ekle")
    async def add_profile(request: Request):
        _require(request, "manage_profiles")
        form = await request.form()
        name = (form.get("name") or "").strip()
        error = validate_name(name)
        if error:
            return settings_redirect("profiller", error=error)
        payload, error = payload_from_form(form, settings_mod.parse_lines,
                                           settings_mod.parse_weight_lines)
        if error:
            return settings_redirect("profiller", error=error)

        profiles = open_profiles()
        try:
            profile_id = int(form.get("profile_id") or 0)
            if profiles.by_name(name) and not profile_id:
                return settings_redirect("profiller", error=f"'{name}' adında bir profil zaten var.")
            if profile_id:
                profiles.update(profile_id, name, payload, (form.get("description") or ""))
                mesaj = f"'{name}' güncellendi."
            else:
                profiles.create(name, payload, (form.get("description") or ""))
                mesaj = f"'{name}' profili eklendi."
        finally:
            profiles.close()
        return settings_redirect("profiller", ok=mesaj)

    @app.post("/ayarlar/profil-mevcuttan")
    async def profile_from_current(request: Request):
        """Su anki ayarlari isimlendirip profil olarak saklar - en hizli yol."""
        _require(request, "manage_profiles")
        form = await request.form()
        name = (form.get("name") or "").strip()
        error = validate_name(name)
        if error:
            return settings_redirect("profiller", error=error)
        profiles = open_profiles()
        try:
            if profiles.by_name(name):
                return settings_redirect("profiller", error=f"'{name}' adında bir profil zaten var.")
            profiles.create(name, capture_current(state["config"], state["keywords"]),
                            "mevcut ayarlardan oluşturuldu")
        finally:
            profiles.close()
        return settings_redirect("profiller", ok=f"'{name}' mevcut ayarlardan oluşturuldu.")

    @app.post("/ayarlar/profil-sil")
    async def remove_profile(request: Request):
        _require(request, "manage_profiles")
        form = await request.form()
        profiles = open_profiles()
        try:
            profiles.delete(int(form.get("profile_id") or 0))
        finally:
            profiles.close()
        return settings_redirect("profiller", ok="Profil silindi.")

    @app.post("/profil-sec")
    async def switch_profile(request: Request):
        """Profiller arasi gecis: ayarlar uygulanir ve ilanlar yeniden puanlanir."""
        user = _require(request, "view_list")
        form = await request.form()
        profile_id = int(form.get("profile_id") or 0)
        back = (form.get("back") or "/").strip() or "/"

        profiles = open_profiles()
        try:
            # kullanici yalnizca kendisine atanan profile gecebilir
            if not profiles.can_use(user.id, profile_id, user.is_admin):
                raise PermissionDenied("manage_profiles")
            if profile_id == 0:
                profiles.deactivate()
                return RedirectResponse(back, status_code=303)
            row = profiles.activate(profile_id)
        finally:
            profiles.close()
        if row is None:
            return RedirectResponse(back, status_code=303)

        reload_config()
        try:
            rescore_all(state["config"], state["keywords"])
        except sqlite3.OperationalError:
            # tarama sürerken kilit olabilir: profil yine de etkin, puanlar bir sonraki turda
            pass
        return RedirectResponse("/", status_code=303)

    # --- hesap ----------------------------------------------------------
    @app.post("/hesap/sifre")
    async def change_own_password(request: Request):
        """Kullanici kendi sifresini degistirir; mevcut sifresini bilmek zorunda."""
        form = await request.form()
        user = user_of(request)
        current = form.get("current_password") or ""
        new = form.get("new_password") or ""
        repeat = form.get("repeat_password") or ""

        if new != repeat:
            return settings_redirect("hesap", error="Yeni şifreler birbirini tutmuyor.")
        error = validate_password(new)
        if error:
            return settings_redirect("hesap", error=error)

        auth = open_auth()
        try:
            if auth.login(user.username, current) is None:
                return settings_redirect("hesap", error="Mevcut şifreniz hatalı.")
            auth.set_password(user.id, new)
        finally:
            auth.close()
        # set_password acik oturumlari dusurur: kullanici yeniden giris yapar
        response = RedirectResponse("/giris", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    # --- admin paneli (ayri sayfa, yalnizca yetkili) --------------------
    def admin_redirect(ok: str = "", error: str = "") -> RedirectResponse:
        params = urlencode({k: v for k, v in {"ok": ok, "hata": error}.items() if v})
        return RedirectResponse(f"/admin?{params}", status_code=303)

    @app.get("/admin")
    def admin_page(request: Request, ok: str = "", hata: str = ""):
        """Kullanici yonetimi. Ayarlar'dan ayri: yetkisi olmayan varligini bile gormez."""
        _require(request, "manage_users")
        store = open_store()
        auth = open_auth()
        profiles = open_profiles()
        pid = profile_of(request)
        try:
            user_rows = auth.users()
            context = {
                **base_context(request),
                "users": user_rows,
                "permission_groups": PERMISSION_GROUPS,
                "profiles": profiles.all(),
                # her kullanicinin hangi profillere erisebildigi (kutular isaretli gelsin)
                "assigned_profiles": {u.id: profiles.assigned_ids(u.id) for u in user_rows},
                "scan": scan_state(),
                "stats": store.stats(pid),
                "unread": store.unread_count(pid),
                "newest_seen": store.newest_seen_at(),
                "active_tab": "admin",
                "ok": ok,
                "error": hata,
            }
        finally:
            store.close()
            auth.close()
            profiles.close()
        return TEMPLATES.TemplateResponse(request, "admin.html", context)

    @app.post("/ayarlar/kullanici-ekle")
    async def add_user(request: Request):
        _require(request, "manage_users")
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""

        error = validate_username(username) or validate_password(password)
        if error:
            return admin_redirect(error=error)

        auth = open_auth()
        try:
            if auth.by_username(username):
                return admin_redirect(error=f"'{username}' zaten kayıtlı.")
            auth.create_user(username, password,
                             is_admin=form.get("is_admin") == "1",
                             full_name=(form.get("full_name") or ""),
                             permissions=list(DEFAULT_PERMISSIONS))
        finally:
            auth.close()
        return admin_redirect(ok=f"'{username}' oluşturuldu.")

    @app.post("/ayarlar/kullanici-yetki")
    async def save_user_permissions(request: Request):
        _require(request, "manage_users")
        form = await request.form()
        user_id = int(form.get("user_id") or 0)
        granted = [key for key in form.getlist("perm") if key in PERMISSIONS]
        is_admin = form.get("is_admin") == "1"

        auth = open_auth()
        try:
            target = auth.get_user(user_id)
            if target is None:
                return admin_redirect(error="Kullanıcı bulunamadı.")
            is_active = form.get("is_active") == "1"
            # son yoneticiyi kilitleyip paneli yonetilemez birakmayalim
            if target.is_admin and (not is_admin or not is_active) and auth.admin_count() <= 1:
                return admin_redirect(error="Son yönetici hesabının yetkisi kaldırılamaz — önce başka bir yönetici açın.")
            auth.set_profile(user_id, (form.get("full_name") or ""), is_admin, is_active)
            auth.set_permissions(user_id, granted)
        finally:
            auth.close()

        # atanan arama profilleri: kullanici yalnizca bunlar arasinda gecis yapabilir
        profiles = open_profiles()
        try:
            secili = [int(x) for x in form.getlist("profil") if str(x).isdigit()]
            profiles.set_assigned(user_id, secili)
        finally:
            profiles.close()
        return admin_redirect(ok="Kullanıcı güncellendi.")

    @app.post("/ayarlar/kullanici-sifre")
    async def reset_user_password(request: Request):
        _require(request, "manage_users")
        form = await request.form()
        user_id = int(form.get("user_id") or 0)
        password = form.get("password") or ""
        error = validate_password(password)
        if error:
            return admin_redirect(error=error)
        auth = open_auth()
        try:
            if auth.get_user(user_id) is None:
                return admin_redirect(error="Kullanıcı bulunamadı.")
            auth.set_password(user_id, password)
        finally:
            auth.close()
        return admin_redirect(ok="Şifre değiştirildi, kullanıcı yeniden giriş yapmalı.")

    @app.post("/ayarlar/kullanici-sil")
    async def remove_user(request: Request):
        _require(request, "manage_users")
        form = await request.form()
        user_id = int(form.get("user_id") or 0)
        if user_id == user_of(request).id:
            return admin_redirect(error="Kendi hesabınızı silemezsiniz.")
        auth = open_auth()
        try:
            target = auth.get_user(user_id)
            if target is None:
                return admin_redirect(error="Kullanıcı bulunamadı.")
            if target.is_admin and auth.admin_count() <= 1:
                return admin_redirect(error="Son yönetici hesabı silinemez.")
            auth.delete_user(user_id)
        finally:
            auth.close()
        # profil atamalari da gitsin: ayni id yeniden kullanilirsa yeni kullanici
        # eskisinin profillerini devralirdi
        profiles = open_profiles()
        try:
            profiles.set_assigned(user_id, [])
        finally:
            profiles.close()
        return admin_redirect(ok="Kullanıcı silindi.")

    @app.post("/api/kaynak-kontrol")
    def check_source(request: Request, payload: dict):
        """Kaynagi canli dener. Veritabanina yazmaz (bkz. probe.py).

        `async def` degil: httpx senkron calisiyor, sync fonksiyon threadpool'a
        gider ve event loop'u kilitlemez.
        """
        _require(request, "edit_sources")
        name = str(payload.get("source") or "").strip()
        query = str(payload.get("query") or "").strip() or None

        # "Yeni kaynak" formundan gelen tanim kaydedilmeden denenir; kullanicinin
        # calismayan bir tanimi kaydetmesi gerekmesin.
        options = None
        if isinstance(payload.get("definition"), dict):
            options, error = _definition_from_json(payload["definition"])
            if error:
                return JSONResponse({"ok": False, "message": error})
            name = options["name"]

        if not _PROBE_LOCK.acquire(blocking=False):
            return JSONResponse({"ok": False, "message": "Bir kontrol zaten sürüyor."},
                                status_code=409)
        try:
            result = probe_source(name, query, state["config"], options=options)
        finally:
            _PROBE_LOCK.release()
        data = result.as_dict()
        scan = scan_state()
        if scan.get("phase") in ("scanning", "quick", "verifying"):
            data["warning"] = "Tarama sürüyor, sonuç yanıltıcı olabilir."
        return JSONResponse(data)

    return app


app = create_app()
