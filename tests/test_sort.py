"""Detayli siralama ve tarih araligi filtresi."""
from datetime import datetime, timedelta, timezone

import pytest

from scanner.dedupe import fingerprint
from scanner.models import HYBRID, ONSITE, REMOTE, Project
from scanner.normalize import budget_daily, infer_period, parse_budget
from scanner.storage import Storage


def project(title, company="ACME", **kwargs):
    p = Project(source="test", source_id=title, url=f"http://x/{title}", title=title,
                company=company, **kwargs)
    p.fingerprint = fingerprint(p)
    return p


# --- butce ayristirma -------------------------------------------------
@pytest.mark.parametrize("raw,tutar,periyot", [
    ("$100-110/saat", 110.0, "hour"),
    ("$100–110/hour", 110.0, "hour"),          # en dash
    ("€600/Tag", 600.0, "day"),
    ("1.200 TL/gun", 1200.0, "day"),           # binlik nokta
    ("$1,200/day", 1200.0, "day"),             # binlik virgul
    ("60000 EUR/year", 60000.0, "year"),
    ("5k", 5000.0, ""),
    ("Negotiable", None, ""),
    ("", None, ""),
])
def test_parse_budget(raw, tutar, periyot):
    amount, period, _ = parse_budget(raw)
    assert amount == tutar
    assert period == periyot


def test_parse_budget_aralikta_ust_ucu_alir():
    assert parse_budget("$60 - $130 per hour")[0] == 130.0


def test_budget_daily_periyotlari_ayni_olcege_getirir():
    assert budget_daily(100, "hour", "USD") == 800.0
    assert budget_daily(600, "day", "USD") == 600.0
    assert budget_daily(26000, "year", "USD") == 100.0
    # periyot bilinmiyorsa goturu ucret: siralanabilir degil
    assert budget_daily(5000, "", "USD") is None


def test_infer_period_maasi_yillik_sayar_ihaleyi_saymaz():
    """En yaygin bicim '$100k - $150k' ve periyot yazmiyor; disarida birakmak
    siralamayi ise yaramaz hale getirirdi."""
    assert infer_period(150000, "", "") == "year"
    assert infer_period(150000, "", "ihale") == ""      # ihale toplami maas degil
    assert infer_period(5_000_000, "", "") == ""        # proje toplami, maas degil
    assert infer_period(110, "", "") == ""              # saatlik ucret olabilir
    assert infer_period(150000, "hour", "") == "hour"   # yazan periyot korunur


# --- siralama ---------------------------------------------------------
def test_her_siralama_fingerprint_ile_biter():
    """Esitlikte SQLite satir sirasi kararsiz: sayfalamada ilan tekrarlanirdi."""
    for name, clause in Storage.ORDERS.items():
        assert clause.strip().endswith("fingerprint"), name


def test_bilinmeyen_siralama_skora_duser(tmp_path):
    store = Storage(tmp_path / "t.db")
    store.upsert([project("SAP ABAP bir")])
    assert store.query(order="'; DROP TABLE projects; --") == store.query(order="score")
    store.close()


def test_butceye_gore_siralama(tmp_path):
    store = Storage(tmp_path / "t.db")
    store.upsert([
        project("Ucuz", budget_raw="$50 per hour"),        # 400/gun
        project("Pahali", budget_raw="$150 per hour"),     # 1200/gun
        project("Butcesiz"),
        project("Goturu", budget_raw="1500 USD fixed"),    # periyot yok -> NULL
    ])
    inen = [r["title"] for r in store.query(order="budget", limit=10)]
    assert inen[:2] == ["Pahali", "Ucuz"]
    assert set(inen[2:]) == {"Butcesiz", "Goturu"}         # bilinmeyen butce sonda

    cikan = [r["title"] for r in store.query(order="budget_asc", limit=10)]
    assert cikan[:2] == ["Ucuz", "Pahali"]
    assert set(cikan[2:]) == {"Butcesiz", "Goturu"}        # iki yonde de sonda
    store.close()


def test_calisma_sekli_onceligi(tmp_path):
    store = Storage(tmp_path / "t.db")
    store.upsert([
        project("Yerinde", work_mode=ONSITE),
        project("Uzak", work_mode=REMOTE),
        project("Belirsiz"),
        project("Hibrit", work_mode=HYBRID),
    ])
    assert [r["title"] for r in store.query(order="mode", limit=10)] == \
        ["Uzak", "Hibrit", "Belirsiz", "Yerinde"]
    store.close()


def test_uzaktan_projede_proje_bazli_uste_gelir(tmp_path):
    store = Storage(tmp_path / "t.db")
    store.upsert([
        project("Kadrolu", work_mode=REMOTE, is_contract=False),
        project("Proje bazli", work_mode=REMOTE, is_contract=True),
    ])
    assert [r["title"] for r in store.query(order="mode", limit=5)][0] == "Proje bazli"
    store.close()


def test_artan_azalan_siralama(tmp_path):
    store = Storage(tmp_path / "t.db")
    yeni = datetime.now(timezone.utc)
    store.upsert([
        project("Eski", posted_at=yeni - timedelta(days=10)),
        project("Yeni", posted_at=yeni),
    ])
    assert [r["title"] for r in store.query(order="date")][0] == "Yeni"
    assert [r["title"] for r in store.query(order="date_asc")][0] == "Eski"
    store.close()


# --- tarih araligi ----------------------------------------------------
@pytest.fixture
def gunluk_store(tmp_path):
    store = Storage(tmp_path / "t.db")
    bugun = datetime.now(timezone.utc).replace(hour=13, minute=30)
    store.upsert([
        project("Bugun", posted_at=bugun),
        project("Dun", posted_at=bugun - timedelta(days=1)),
        project("Gecen hafta", posted_at=bugun - timedelta(days=7)),
    ])
    yield store, bugun.date()
    store.close()


def test_tarih_araligi_tek_gun(gunluk_store):
    """from == to o gunun tamamini kapsamali (mikro saniyeli damgalar dahil)."""
    store, bugun = gunluk_store
    gun = bugun.isoformat()
    assert [r["title"] for r in store.query(date_from=gun, date_to=gun)] == ["Bugun"]
    assert store.count(date_from=gun, date_to=gun) == 1


def test_tarih_araligi_iki_ucu_de_dahil(gunluk_store):
    store, bugun = gunluk_store
    baslangic = (bugun - timedelta(days=1)).isoformat()
    assert store.count(date_from=baslangic, date_to=bugun.isoformat()) == 2


def test_tarih_araligi_tek_tarafli(gunluk_store):
    store, bugun = gunluk_store
    assert store.count(date_to=(bugun - timedelta(days=2)).isoformat()) == 1
    assert store.count(date_from=(bugun - timedelta(days=2)).isoformat()) == 2


def test_gecersiz_tarih_metni_filtreyi_dusurur(gunluk_store):
    store, _ = gunluk_store
    assert store.count(date_from="dun") == store.count()
    assert store.count(date_to="19/08/2026") == store.count()


def test_tarih_alani_sisteme_dusme(gunluk_store):
    """Ilan tarihi eski olsa da sisteme bugun dustuyse 'seen' onu bulmali."""
    store, bugun = gunluk_store
    gun = bugun.isoformat()
    assert store.count(date_from=gun, date_to=gun, date_field="seen") == 3
    assert store.count(date_from=gun, date_to=gun, date_field="posted") == 1


def test_count_ve_query_ayni_filtreyi_uygular(gunluk_store):
    store, bugun = gunluk_store
    filtre = {"date_from": (bugun - timedelta(days=1)).isoformat(), "date_to": bugun.isoformat()}
    assert store.count(**filtre) == len(store.query(limit=100, **filtre))
