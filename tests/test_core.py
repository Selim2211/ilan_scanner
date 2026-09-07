from datetime import datetime, timedelta, timezone

from scanner.dedupe import dedupe, fingerprint
from scanner.models import HYBRID, ONSITE, REMOTE, UNKNOWN, Project
from scanner.normalize import (detect_contract, detect_work_mode, fold, mode_from_percent,
                               parse_date, parse_percent, strip_html)
from scanner.scoring import Scorer

KEYWORDS = {
    "title_multiplier": 2.0,
    "must_any": ["abap", "sap"],
    "boost": {"abap": 6, "fiori": 2, "freelance": 6},
    "penalty": {"intern": 5},
    "hard_exclude": ["stajyer"],
    "signals": {
        "remote_boost": 12, "hybrid_boost": 5, "onsite_penalty": 4,
        "contract_boost": 8, "permanent_penalty": 3,
        "fresh_boost": 3, "fresh_days": 7,
        "country_boost": {"turkey": 2},
    },
    "min_score": 10,
}


def project(title="SAP ABAP Developer", description="", work_mode=UNKNOWN,
            is_contract=None, company="ACME", **kwargs):
    return Project(source="test", source_id="1", url="http://x/1", title=title,
                   description=description, work_mode=work_mode, is_contract=is_contract,
                   company=company, **kwargs)


# --- normalize -------------------------------------------------------
def test_fold_ve_strip_html():
    assert fold("SAP ABAP Danışmanı") == "sap abap danismani"
    assert strip_html("<p>SAP <b>ABAP</b></p>") == "SAP ABAP"


def test_calisma_sekli_tespiti():
    assert detect_work_mode("SAP ABAP Developer", "Remote") == REMOTE
    assert detect_work_mode("ABAP Consultant", "uzaktan calisma") == REMOTE
    assert detect_work_mode("SAP Developer", "Hybrid - 2 days office") == HYBRID
    assert detect_work_mode("SAP Developer", "Onsite in Munich") == ONSITE
    assert detect_work_mode("SAP Developer", "") == UNKNOWN


def test_hybrid_remote_ifadesi_hybrid_sayilir():
    """'hybrid remote' gecen ilan tam uzaktan degildir."""
    assert detect_work_mode("SAP ABAP", "hybrid remote, 3 days per week") == HYBRID


def test_remote_yuzdesi():
    assert parse_percent("100% remote") == 100
    assert mode_from_percent(100) == REMOTE
    assert mode_from_percent(50) == HYBRID
    assert mode_from_percent(0) == ONSITE
    assert mode_from_percent(None) == UNKNOWN


def test_sozlesme_tespiti():
    assert detect_contract("SAP ABAP Freelance Consultant") is True
    assert detect_contract("Contract role, C2C ok") is True
    assert detect_contract("Permanent position, Festanstellung") is False
    assert detect_contract("SAP ABAP Developer") is None


def test_parse_date():
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    assert parse_date("3 gun once", now) == now - timedelta(days=3)
    assert parse_date("19.05.2026").month == 5
    assert parse_date(None) is None


# --- scoring ---------------------------------------------------------
def test_sap_gecmiyorsa_elenir():
    result = Scorer(KEYWORDS).score(project(title="Java Developer", description="spring boot"))
    assert not result.accepted


def test_remote_contract_en_yuksek_puani_alir():
    scorer = Scorer(KEYWORDS)
    remote_contract = scorer.score(project(work_mode=REMOTE, is_contract=True))
    onsite_permanent = scorer.score(project(work_mode=ONSITE, is_contract=False))
    hybrid = scorer.score(project(work_mode=HYBRID, is_contract=True))

    assert remote_contract.score > hybrid.score > onsite_permanent.score
    assert remote_contract.score - hybrid.score == 12 - 5
    assert "+remote" in remote_contract.hits and "+contract" in remote_contract.hits


def test_yerinde_kadrolu_puan_kaybeder():
    scorer = Scorer(KEYWORDS)
    base = scorer.score(project(work_mode=UNKNOWN))
    worst = scorer.score(project(work_mode=ONSITE, is_contract=False))
    assert base.score - worst.score == 4 + 3


def test_taze_ilan_bonusu():
    scorer = Scorer(KEYWORDS)
    fresh = scorer.score(project(posted_at=datetime.now(timezone.utc) - timedelta(days=2)))
    old = scorer.score(project(posted_at=datetime.now(timezone.utc) - timedelta(days=90)))
    assert fresh.score - old.score == 3


def test_baslikta_gecen_kelime_iki_kat():
    scorer = Scorer(KEYWORDS)
    in_title = scorer.score(project(title="Freelance SAP ABAP", description="x"))
    in_body = scorer.score(project(title="SAP ABAP Developer", description="freelance"))
    assert in_title.score - in_body.score == 6


def test_apply_remote_projeleri_one_alir():
    scorer = Scorer(KEYWORDS)
    kept, dropped = scorer.apply([
        project(title="SAP ABAP onsite", work_mode=ONSITE),
        project(title="SAP ABAP remote", work_mode=REMOTE, is_contract=True),
        project(title="Frontend Developer"),
    ])
    assert kept[0].title == "SAP ABAP remote"
    assert len(dropped) == 1


# --- dedupe ----------------------------------------------------------
def test_ayni_ilan_iki_kaynakta_birlesir():
    a = project(title="SAP ABAP Developer (m/w/d)", work_mode=UNKNOWN)
    a.source = "jooble"
    b = project(title="SAP ABAP Developer", work_mode=REMOTE, is_contract=True,
                description="uzun aciklama")
    b.source = "freelancermap"
    merged = dedupe([a, b])

    assert len(merged) == 1
    assert merged[0].also_seen_on == ["freelancermap"]
    assert merged[0].work_mode == REMOTE          # bilinmeyen taraf diger kaynaktan doldu
    assert merged[0].is_contract is True
    assert merged[0].description == "uzun aciklama"


def test_farkli_firma_ayri_ilan():
    a = project(company="ACME")
    b = project(company="Baska Firma")
    assert len(dedupe([a, b])) == 2


def test_fingerprint_gurultu_kelimelerine_dayanikli():
    assert fingerprint(project(title="Senior SAP ABAP Developer (remote)")) == \
           fingerprint(project(title="SAP ABAP Developer"))


# --- ulke cozumlemesi ------------------------------------------------
def test_ulke_kodu_cozumleme():
    """Kaynaklarin ulke kolonuna yazdigi her sey tek bir ISO2 koduna inmeli."""
    from scanner.countries import resolve_country

    assert resolve_country("Germany") == "DE"
    assert resolve_country("Deutschland") == "DE"       # careerjet yerel dilde donuyor
    assert resolve_country("München") == "DE"           # freelancermap sehir yaziyor
    assert resolve_country("Greater London") == "GB"
    assert resolve_country("Turkey") == "TR"
    assert resolve_country("TX", "Austin, TX") == "US"  # jooble eyalet kisaltmasi
    assert resolve_country("ON", "Toronto, ON") == "CA"
    assert resolve_country("", "New York, NY") == "US"  # ulke bos, location cozuyor


def test_ulke_olmayan_degerler_kod_uretmez():
    from scanner.countries import resolve_country

    assert resolve_country("Remote") == ""
    assert resolve_country("Anywhere", "Europe") == ""
    assert resolve_country("Casablanca") == ""          # tanimadigimiz sehir zorlanmaz


# --- storage ---------------------------------------------------------
def test_ulke_filtresi_tam_eslesir(tmp_path):
    """Eski LIKE filtresi 'CA' deyince Casablanca'yi getirip Deutschland'i kaciriyordu."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    kayitlar = [
        ("Berlin ABAP", "Germany", "Berlin, Germany"),
        ("Köln ABAP", "Deutschland", "Köln"),
        ("Toronto ABAP", "", "Toronto, ON"),
        ("Casablanca ABAP", "Casablanca", "Casablanca"),
        ("Remote ABAP", "Remote", "Remote"),
    ]
    items = []
    for baslik, ulke, konum in kayitlar:
        p = project(title=baslik, company=baslik, country=ulke, location=konum)
        p.fingerprint = fingerprint(p)
        items.append(p)
    store.upsert(items)

    assert store.count(country=["DE"]) == 2            # Germany + Deutschland birlikte
    assert store.count(country=["CA"]) == 1            # Kanada; Casablanca DEGIL
    assert store.count(country=["DE", "CA"]) == 3
    assert store.count(country="__none__") == 2        # Casablanca + Remote
    assert store.count(country="Germany") == 1         # eski serbest metin hala calisir
    store.close()


def test_ulke_facetleri(tmp_path):
    """Kutudaki liste: kanonik ad + sayi, cozulemeyenler sonda tek kovada."""
    from scanner.countries import NO_COUNTRY
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    items = []
    for baslik, ulke in [("A", "Germany"), ("B", "Deutschland"), ("C", "Remote")]:
        p = project(title=f"SAP {baslik}", company=baslik, country=ulke)
        p.fingerprint = fingerprint(p)
        items.append(p)
    store.upsert(items)

    facets = store.country_facets()
    assert facets[0] == {"code": "DE", "name": "Almanya", "count": 2}
    assert facets[-1]["code"] == NO_COUNTRY and facets[-1]["count"] == 1
    store.close()


def test_ulke_kodu_geri_doldurma(tmp_path):
    """Kolon sonradan eklendiginde eski satirlar da kodlanmali."""
    import sqlite3

    from scanner.storage import Storage

    yol = tmp_path / "t.db"
    store = Storage(yol)
    p = project(title="Eski SAP ABAP", country="Deutschland", location="Köln")
    p.fingerprint = fingerprint(p)
    store.upsert([p])
    store.close()

    # kolonu elle sifirla: migration oncesi veritabanini taklit eder
    conn = sqlite3.connect(yol)
    conn.execute("UPDATE projects SET country_code = NULL")
    conn.commit()
    conn.close()

    store = Storage(yol)
    assert store.count(country=["DE"]) == 1
    store.close()



def test_sayfalama_ve_sayim(tmp_path):
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    items = []
    for i in range(25):
        p = project(title=f"SAP ABAP Projesi {i}", company=f"Firma {i}", work_mode=REMOTE)
        p.score = 20
        p.fingerprint = fingerprint(p)
        items.append(p)
    store.upsert(items)

    assert store.count() == 25
    assert len(store.query(limit=20, offset=0)) == 20
    assert len(store.query(limit=20, offset=20)) == 5
    assert store.count(work_mode="onsite") == 0
    store.close()


def test_filtreler(tmp_path):
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    remote = project(title="Remote SAP", work_mode=REMOTE, is_contract=True)
    onsite = project(title="Onsite SAP", company="X", work_mode=ONSITE, is_contract=False)
    for p in (remote, onsite):
        p.fingerprint = fingerprint(p)
    store.upsert([remote, onsite])

    assert store.count(work_mode="remote") == 1
    assert store.count(contract_only=True) == 1
    assert store.count(search="onsite") == 1
    store.set_status(remote.fingerprint, "shortlist")
    assert store.count(shortlist=True) == 1
    store.close()


def test_listeden_dusen_ilan_hemen_kapanmaz(tmp_path):
    """Kaynak sayfalanmis pencere donduruyor: yokluk tek basina kapatmaz, supheli yapar."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    kalan = project(title="Kalan SAP ABAP")
    giden = project(title="Giden SAP ABAP", company="B")
    for p in (kalan, giden):
        p.source = "jooble"
        p.fingerprint = fingerprint(p)
    store.upsert([kalan, giden])

    store.mark_missing("jooble", [kalan.fingerprint])
    store.mark_missing("jooble", [kalan.fingerprint])

    assert store.count() == 2                                   # ikisi de listede kaldi
    assert store.stats()["suspect"] == 1
    suspects = store.suspects("jooble", min_streak=2)
    assert [r["title"] for r in suspects] == ["Giden SAP ABAP"]
    store.close()


def test_yayinda_olan_supheli_temizlenir(tmp_path):
    """Linki acilan ilan yayindadir: supheli sayaci sifirlanir."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="Yayindaki SAP")
    p.source = "freelancermap"
    p.fingerprint = fingerprint(p)
    store.upsert([p])
    store.mark_missing("freelancermap", [])
    assert store.stats()["suspect"] == 1

    store.clear_missing([p.fingerprint])
    assert store.stats()["suspect"] == 0
    assert store.count() == 1
    store.close()


def test_dogrulanamayan_kaynakta_uzun_yokluk_kapatir(tmp_path):
    """Bot duvarli kaynakta link kontrolu yok: son care yuksek esik."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="Kaybolan SAP")
    p.source = "jooble"
    p.fingerprint = fingerprint(p)
    store.upsert([p])
    for _ in range(5):
        store.mark_missing("jooble", [])

    assert store.close_stale("jooble", min_streak=6) == []      # esik dolmadi
    store.mark_missing("jooble", [])
    closed = store.close_stale("jooble", min_streak=6)
    assert len(closed) == 1 and store.count() == 0
    store.close()


def test_kapanan_ilan_kaynakta_gorulse_de_geri_acilmaz(tmp_path):
    """Kapatma KALICI. Eskiden bu test tam tersini pinliyordu.

    Jooble kapanmis ilani gunlerce indeksinde tutuyor; "kaynak hala gosteriyor"
    ile "ilan acik" ayni sey degil. Eski davranista upsert her taramada
    is_active=1 yazip kapatmayi siliyordu: olculdu, 199 ilan "kapandi" bildirimi
    almasina ragmen listede duruyordu, 24 Agustos'ta kapatilan ilan 31 Agustos'ta
    hala aktifti. Kullanicinin gordugu "no longer available ilanlar listeden
    dusmuyor" sikayetinin tam kaynagi buydu.
    """
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="Geri Donen SAP")
    p.source = "jooble"
    p.fingerprint = fingerprint(p)
    store.upsert([p])
    store.mark_missing("jooble", [])
    store.close_stale("jooble", min_streak=1)
    assert store.count() == 0

    store.upsert([p])                      # kaynak indeksinde yine goruldu
    assert store.count() == 0               # ... ama kapali kaliyor
    assert store.count(closed_only=True) == 1
    # Kapali satirin son gorulme zamani yine de tazelenir (veri bayatlamasin)
    assert store.get_by_fingerprint(p.fingerprint)["closed_at"]
    store.close()


def test_elle_bildirilen_kapanma_taramaya_direnir(tmp_path):
    """Kullanicinin kendi karari en guclu sinyal: hicbir tarama onu ezemez."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="Kullanici Kapatti SAP")
    p.source = "jooble"
    p.fingerprint = fingerprint(p)
    store.upsert([p])
    store.report_closed(p.fingerprint)
    assert store.count() == 0

    store.upsert([p])
    assert store.count() == 0
    row = store.get_by_fingerprint(p.fingerprint)
    assert row["is_active"] == 0
    assert "kullanıcı bildirdi" in (row["closed_reason"] or "")
    store.close()


def test_api_sayaci_sifirlanmaz_azalir(tmp_path):
    """Salinan ilan birikebilsin: bulundu -> 0 degil, -1.

    Jooble olmekte olan ilani indeksine alip cikariyor (olculdu: 20:40'ta var,
    20:49'da yok). Sifirlama sayaci 1->0->1->0 salindiriyor, esige hic
    ulasilmiyordu.
    """
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="Salinan SAP")
    p.fingerprint = fingerprint(p)
    store.upsert([p])
    fp = p.fingerprint

    assert store.bump_api_miss([fp])[fp] == 1
    assert store.bump_api_miss([fp])[fp] == 2
    store.decay_api_miss([fp])
    assert store.get_by_fingerprint(fp)["api_miss_streak"] == 1   # sifir DEGIL
    store.decay_api_miss([fp])
    store.decay_api_miss([fp])
    assert store.get_by_fingerprint(fp)["api_miss_streak"] == 0   # altina inmez
    store.close()


def test_supheli_ilan_bayrak_acikken_dogrudan_kapatilir(tmp_path):
    """close_suspects_immediately: link kontrolu hicbir zaman calismayan kaynakta
    (Jooble) supheli olan ilan API'ye sorulmadan dogrudan kapatilir."""
    from scanner.pipeline import _resolve_missing
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="SAP ABAP Projesi")
    p.source = "jooble"
    p.fingerprint = fingerprint(p)
    store.upsert([p])

    closed = _resolve_missing(store, client=None, kept=[], healthy_sources={"jooble"},
                              suspect_after=1, close_unverifiable_after=6,
                              skip_sources={"jooble"}, check_limit=10,
                              close_suspects_immediately=True)
    assert len(closed) == 1
    assert store.count() == 0
    store.close()


def test_supheli_ilan_bayrak_kapaliyken_hemen_kapanmaz(tmp_path):
    """Varsayilan (bayrak kapali) davranis korunur: tek turda kapanmaz."""
    from scanner.pipeline import _resolve_missing
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="SAP ABAP Projesi")
    p.source = "jooble"
    p.fingerprint = fingerprint(p)
    store.upsert([p])

    closed = _resolve_missing(store, client=None, kept=[], healthy_sources={"jooble"},
                              suspect_after=1, close_unverifiable_after=6,
                              skip_sources={"jooble"}, check_limit=10)
    assert closed == []
    assert store.count() == 1
    store.close()


def test_hatali_kaynak_ilanlari_kapatmaz(tmp_path):
    """Kaynak hata verdiyse ilanlari kayip saymayiz - site coktu diye ilan kapanmaz."""
    from scanner.pipeline import _resolve_missing
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="SAP ABAP Projesi")
    p.source = "jooble"
    p.fingerprint = fingerprint(p)
    store.upsert([p])

    args = dict(suspect_after=1, close_unverifiable_after=1,
                skip_sources={"jooble"}, check_limit=10)
    _resolve_missing(store, client=None, kept=[], healthy_sources=set(), **args)
    assert store.count() == 1              # jooble saglikli listede yok -> dokunulmadi
    assert store.stats()["suspect"] == 0   # sayac bile artmadi

    _resolve_missing(store, client=None, kept=[], healthy_sources={"jooble"}, **args)
    assert store.count() == 0
    store.close()


def test_link_aciliyorsa_ilan_kapanmaz(tmp_path):
    """Yanlis kapatmanin onundeki asil koruma: sayfa 200 donuyorsa ilan yayindadir."""
    from scanner.pipeline import _resolve_missing
    from scanner.storage import Storage

    class SahteYanit:
        status_code = 200
        text = "SAP ABAP projesi - basvuru acik"

    class SahteIstemci:
        # gercek istemcide de kontrol probe() ile yapilir: request() 404'te hata
        # firlatiyor ve durum kodu cagirana ulasmiyor
        def probe(self, url):
            return SahteYanit()

    store = Storage(tmp_path / "t.db")
    p = project(title="Pencereden Dusen SAP")
    p.source = "freelancermap"
    p.fingerprint = fingerprint(p)
    store.upsert([p])

    for _ in range(3):
        _resolve_missing(store, SahteIstemci(), kept=[], healthy_sources={"freelancermap"},
                         suspect_after=1, close_unverifiable_after=2,
                         skip_sources=set(), check_limit=10)

    assert store.count() == 1
    assert store.stats()["suspect"] == 0   # her turda yayinda oldugu dogrulandi
    store.close()


def test_kapanmis_link_ilani_kapatir(tmp_path):
    from scanner.pipeline import _resolve_missing
    from scanner.storage import Storage

    class SahteYanit:
        status_code = 404
        text = ""

    class SahteIstemci:
        # gercek istemcide de kontrol probe() ile yapilir: request() 404'te hata
        # firlatiyor ve durum kodu cagirana ulasmiyor
        def probe(self, url):
            return SahteYanit()

    store = Storage(tmp_path / "t.db")
    p = project(title="Gercekten Kapanan SAP")
    p.source = "freelancermap"
    p.fingerprint = fingerprint(p)
    store.upsert([p])

    closed = _resolve_missing(store, SahteIstemci(), kept=[], healthy_sources={"freelancermap"},
                              suspect_after=1, close_unverifiable_after=9,
                              skip_sources=set(), check_limit=10)
    assert len(closed) == 1
    assert store.count() == 0 and store.count(closed_only=True) == 1
    store.close()


# --- bildirimler -----------------------------------------------------
def test_bildirim_akisi(tmp_path):
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    store.add_notification("new", "Yeni SAP projesi", "ACME · Remote", "http://x", "jooble", 30, "remote")
    store.add_notification("closed", "Kapanan proje", url="http://y", source="jooble")

    assert store.unread_count() == 2
    assert len(store.notifications(unread_only=True)) == 2
    store.mark_notifications_read()
    assert store.unread_count() == 0
    assert len(store.notifications()) == 2
    store.close()


def test_bildirim_esigi_dusuk_puanli_ilani_atlar(tmp_path):
    from scanner.pipeline import _notify
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    guclu = project(title="Remote SAP ABAP")
    guclu.score = 40
    zayif = project(title="Zayif SAP", company="Z")
    zayif.score = 5
    _notify(store, [guclu, zayif], closed_rows=[], min_score=15)

    items = store.notifications()
    assert len(items) == 1 and items[0]["title"] == "Remote SAP ABAP"
    store.close()


# --- genisletilmis filtreler -----------------------------------------
def test_filtreler_tarih_butce_haric(tmp_path):
    from datetime import datetime, timedelta, timezone

    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    taze = project(title="Taze SAP ABAP", company="A", posted_at=datetime.now(timezone.utc))
    taze.budget_raw = "100 EUR/saat"
    eski = project(title="Eski SAP ABAP", company="B",
                   posted_at=datetime.now(timezone.utc) - timedelta(days=40))
    junior = project(title="Junior SAP ABAP", company="C")
    for p in (taze, eski, junior):
        p.fingerprint = fingerprint(p)
    store.upsert([taze, eski, junior])

    # tarihi bilinmeyen ilan (junior) sisteme dusme tarihiyle sayilir, elenmez
    assert store.count(max_age_days=7) == 2
    assert store.count(max_age_days=7, exclude="junior") == 1
    assert store.count(has_budget=True) == 1
    assert store.count(exclude="junior") == 2
    assert [r["title"] for r in store.query(order="company", limit=1)] == ["Taze SAP ABAP"]
    store.close()


def test_arz_ilanlari_varsayilan_listede_gizlidir(tmp_path):
    """Hizmet satan ilanlar (arz) talep listesini kirletmemeli."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    talep = project(title="SAP ABAP Remote Contract", company="ACME")
    arz = project(title="I will do sap abap development", company="abapguru")
    arz.is_supply = True
    for p in (talep, arz):
        p.fingerprint = fingerprint(p)
    store.upsert([talep, arz])

    assert store.count() == 1
    assert store.count(include_supply=True) == 2
    assert store.stats()["supply"] == 1
    store.close()


# --- tazelik: tarih ayristirma, dogrulama sirasi, zamanlayici -----------
def test_iso_tarih_gun_ay_yer_degistirmez():
    """Jooble ISO tarih veriyor; dayfirst kurali "2026-08-10"u 8 Ekim yapiyordu."""
    assert parse_date("2026-08-10").month == 8
    assert parse_date("2026-08-10T03:52:43.167Z").day == 10
    # gun-once formatlari bozulmadi
    assert parse_date("19.05.2026").month == 5


def test_gelecek_tarih_geri_cekilir():
    """Ay/gun ters okunmus tarih gelecege dusuyorsa duzeltilmis hali kullanilir."""
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    parsed = parse_date("08/10/2026", now=now)      # gun-once okunusu gelecekte kaliyor
    assert parsed == datetime(2026, 8, 10, tzinfo=timezone.utc)
    # iki okunus da gelecekteyse dokunulmaz
    assert parse_date("10/12/2026", now=now).month == 12


def test_dogrulama_sirasi_ilerler(tmp_path):
    """Kontrol edilen ilan sona atilir; ayni ilk N ilana takilip kalinmaz."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    items = []
    for i in range(5):
        p = project(title=f"SAP ABAP {i}", company=f"F{i}", work_mode=REMOTE)
        p.score = 20
        p.fingerprint = fingerprint(p)
        items.append(p)
    store.upsert(items)

    first = store.verify_candidates(limit=2, min_hours=0)
    store.mark_verified([r["fingerprint"] for r in first])
    second = store.verify_candidates(limit=2, min_hours=0)
    assert {r["fingerprint"] for r in first} & {r["fingerprint"] for r in second} == set()

    # engelli kaynaklar listeden cikarilir
    assert store.verify_candidates(limit=5, min_hours=0, skip_sources=["test"]) == []
    store.close()


def test_tazelik_istatistikleri(tmp_path):
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    taze = project(title="Taze SAP ABAP", work_mode=REMOTE,
                   posted_at=datetime.now(timezone.utc) - timedelta(days=2))
    eski = project(title="Eski SAP ABAP", company="Y",
                   posted_at=datetime.now(timezone.utc) - timedelta(days=90))
    for p in (taze, eski):
        p.fingerprint = fingerprint(p)
    store.upsert([taze, eski])

    stats = store.stats()
    assert stats["new_24h"] == 2          # ikisi de bu taramada sisteme dustu
    assert stats["fresh_7d"] == 1         # sadece biri son 7 gunde yayinlanmis
    assert stats["unverified"] == 2
    assert store.count_since(store.newest_seen_at()) == 0
    store.close()


def test_zamanlayici_durumu():
    """Panel /api/durum'dan bu alanlari okuyor."""
    from scanner.scheduler import BackgroundScanner, from_config

    scanner = BackgroundScanner(interval_minutes=1, verify_limit=0, run_on_start=False)
    snap = scanner.snapshot()
    assert snap["enabled"] is True
    assert snap["interval_minutes"] == 5          # 5 dakikanin altina inilmez
    assert snap["phase"] == "idle" and snap["running"] is False
    assert from_config({"auto_scan": {"enabled": False}}) is None


def test_gecen_sure_metni():
    from scanner.web.app import since

    now = datetime.now(timezone.utc)
    assert since((now - timedelta(minutes=5)).isoformat()) == "5 dk önce"
    assert since((now - timedelta(hours=3)).isoformat()) == "3 sa önce"
    assert since((now - timedelta(days=4)).isoformat()) == "4 gün önce"
    assert since("") == ""


def test_artik_aktif_degil_bildirimi_ilani_kapatir(tmp_path):
    """Panelde 'Artık Aktif Değil' düğmesi: Jooble linkleri bot korumasından ötürü
    otomatik doğrulanamıyor, bu yüzden elle bildirim gerekiyor."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    p = project(title="Kapanmış SAP ABAP")
    p.fingerprint = fingerprint(p)
    store.upsert([p])

    row = store.report_closed(p.fingerprint)
    assert row is not None and row["title"] == "Kapanmış SAP ABAP"
    assert store.count() == 0                      # varsayilan listede artik yok
    assert store.count(closed_only=True) == 1
    assert store.unread_count() == 1                # kapanma bildirimi uretildi

    # ikinci kez bildirilirse ikinci bildirim uretilmez
    assert store.report_closed(p.fingerprint) is None
    assert store.unread_count() == 1
    store.close()

def test_karo_sayimlari_listeyle_ayni_kumeyi_sayar(tmp_path):
    """Karodaki sayi, tiklaninca acilan listeyle BIREBIR ayni olmali.

    Regresyon: `stats()` min skor esigini yok sayiyordu; panel "489 uzaktan"
    yazip tiklayinca 484 ilan gosteriyordu. Kullanici haklı olarak sayilara
    guvenmiyordu.
    """
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    yuksek = project(title="Yuksek SAP ABAP", work_mode=REMOTE)
    dusuk = project(title="Dusuk SAP ABAP", company="Y", work_mode=REMOTE)
    for p, skor in ((yuksek, 40), (dusuk, 5)):
        p.fingerprint = fingerprint(p)
        p.score = skor
    store.upsert([yuksek, dusuk])

    # Esik uygulanmadan: ikisi de sayilir (CLI/tepsi bu davranisi kullaniyor)
    assert store.stats()["remote"] == 2

    # Esik uygulaninca karo ile liste ayni sayiyi vermeli
    karo = store.stats(min_score=10, include_supply=False)["remote"]
    liste = store.count(min_score=10, work_mode="remote")
    assert karo == liste == 1
    store.close()


def test_karo_aktif_sayisi_ham_kalir(tmp_path):
    """"Tumunu kontrol et" skor esigine bakmadan tariyor; sayi onunla ortusmeli."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    for baslik, skor in (("A SAP ABAP", 40), ("B SAP ABAP", 1)):
        p = project(title=baslik, company=baslik)
        p.fingerprint = fingerprint(p)
        p.score = skor
        store.upsert([p])

    assert store.stats(min_score=10)["active"] == 2      # esikten etkilenmez
    store.close()


def test_gun_filtresi_secilen_tarih_alanina_bakar(tmp_path):
    """"son 24 saatte SISTEME DUSEN" ile "YAYINLANAN" farkli kumeler.

    24 saat karosu birincisini sayiyor; `days` filtresi `date_field=seen`
    verildiginde first_seen_at'e bakmazsa karo ile liste tutmuyordu.
    """
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    eski_yayin = project(title="Eski yayin SAP ABAP",
                         posted_at=datetime.now(timezone.utc) - timedelta(days=60))
    eski_yayin.fingerprint = fingerprint(eski_yayin)
    store.upsert([eski_yayin])          # bugun sisteme dustu, 60 gun once yayinlandi

    # yayin tarihine gore: 1 gunluk pencerede YOK
    assert store.count(max_age_days=1, date_field="posted") == 0
    # sisteme dusme tarihine gore: VAR
    assert store.count(max_age_days=1, date_field="seen") == 1
    # karo da bunu saymali
    assert store.stats()["new_24h"] == 1
    store.close()


# --- İyi haberler + dışa aktar için depo yardımcıları -------------------

def test_good_news_esik_isaret_ve_sira(tmp_path):
    """good_news: verilen andan sonra düşen, aktif, yüksek puanlı, işaretsiz
    ilanlar - puana göre, işaretliler ve düşük puanlılar hariç."""
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    kesim = "2026-09-01T00:00:00+00:00"

    def ekle(slug, score, **kw):
        p = project(title=slug, company=slug, work_mode=REMOTE, **kw)
        p.score = score
        p.fingerprint = "fp-" + slug
        store.upsert([p])
        return p.fingerprint

    yeni_yuksek = ekle("yeni-yuksek", 80)
    ekle("yeni-dusuk", 20)                                   # eşik altı
    kapali = ekle("kapali", 90)
    store.close_project(kapali)                              # aktif değil
    isaretli = ekle("isaretli", 85)
    store.set_status(isaretli, "shortlist")                  # işaretli

    # kesimden ÖNCE düşmüş bir ilan (first_seen elle geriye çekilir)
    eski = ekle("eski", 95)
    store.conn.execute("UPDATE projects SET first_seen_at = ? WHERE fingerprint = ?",
                       ("2026-08-01T00:00:00+00:00", eski))
    store.conn.commit()

    haberler = store.good_news(kesim, min_score=50, profile_id=0, limit=12)
    assert [r["fingerprint"] for r in haberler] == [yeni_yuksek]

    # kesim boşsa hiçbir şey dönmez
    assert store.good_news("", min_score=0) == []
    store.close()


def test_good_news_limit_ve_puan_sirasi(tmp_path):
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    for i, sk in enumerate([55, 90, 70, 60, 88]):
        p = project(title=f"ilan{i}", company=f"F{i}", work_mode=REMOTE)
        p.score = sk
        p.fingerprint = f"fp{i}"
        store.upsert([p])

    ilk3 = store.good_news("2026-01-01T00:00:00+00:00", min_score=50, limit=3)
    assert [r["score"] for r in ilk3] == [90, 88, 70]        # puana göre, limit uygulanmış
    store.close()


def test_rows_by_fingerprints_sira_tekil_bos(tmp_path):
    from scanner.storage import Storage

    store = Storage(tmp_path / "t.db")
    for i, sk in enumerate([40, 75, 60]):
        p = project(title=f"r{i}", company=f"F{i}")
        p.score = sk
        p.fingerprint = f"r{i}"
        store.upsert([p])

    rows = store.rows_by_fingerprints(["r0", "r2", "r1", "r0", "yok"], profile_id=0)
    assert [r["fingerprint"] for r in rows] == ["r1", "r2", "r0"]   # puan desc, tekilleşmiş
    assert store.rows_by_fingerprints([], profile_id=0) == []
    store.close()
