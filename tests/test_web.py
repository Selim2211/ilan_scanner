"""Panel rotalari: ayarlar kaydi, filtre parametrelerinin sayfalamada korunmasi."""
import pytest
from fastapi.testclient import TestClient

from scanner import config as config_mod
from scanner.scheduler import BackgroundScanner


@pytest.fixture
def anon_client(tmp_path, monkeypatch):
    """Oturum acmamis istemci - giris duvarini sinayan testler icin."""
    monkeypatch.setattr(config_mod.paths, "data_dir", lambda: tmp_path)
    from scanner.web.app import create_app
    with TestClient(create_app(auto_scan=False)) as c:
        yield c


@pytest.fixture
def client(anon_client):
    """Panel gecici bir veri klasorunde calisir: gercek DB ve overlay'e dokunulmaz.

    Panel artik giris istiyor; ilk acilista kurulan yonetici hesabiyla oturum acilir,
    boylece rota testleri yetki duvarina takilmadan asil isi sinar.
    """
    anon_client.post("/giris", data={"username": "admin", "password": "123456", "next": "/"},
                     follow_redirects=False)
    yield anon_client


def test_ayarlar_sayfasi_kaynaklari_listeler(client):
    response = client.get("/ayarlar")
    assert response.status_code == 200
    assert "Ayarlar" in response.text
    assert "jooble" in response.text
    assert "Veriyi kontrol et" in response.text


def test_tarama_ayari_overlaye_yazilir(client, tmp_path):
    response = client.post("/ayarlar/tarama", data={
        "auto_enabled": "1", "interval_minutes": "25", "quick_interval_minutes": "7",
        "verify_limit": "50", "verify_every_n_scans": "1", "page_size": "35",
    }, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/ayarlar?")
    data = config_mod.load_overlay(config_mod.overlay_path())
    assert data["auto_scan"]["interval_minutes"] == 25
    assert data["panel"]["page_size"] == 35


def test_tarama_araligi_alt_sinirin_altina_inmez(client):
    client.post("/ayarlar/tarama", data={"interval_minutes": "1", "quick_interval_minutes": "1"},
                follow_redirects=False)
    data = config_mod.load_overlay(config_mod.overlay_path())
    assert data["auto_scan"]["interval_minutes"] == 5
    assert data["auto_scan"]["quick_interval_minutes"] == 2


TEMEL_ARAMA = {"queries": "SAP ABAP", "must_any": "abap\nsap"}


def test_ne_ariyoruz_bozuk_agirlikta_hata_dondurur(client):
    response = client.post("/ayarlar/ne-ariyoruz", data={**TEMEL_ARAMA, "boost": "abap: cok"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert "hata=" in response.headers["location"]


def test_ne_ariyoruz_bos_zorunlu_kelimeyi_reddeder(client):
    """must_any bosalirsa hicbir ilan elenmez ve liste alakasiz ilanla dolar."""
    response = client.post("/ayarlar/ne-ariyoruz", data={"queries": "ERP", "must_any": ""},
                           follow_redirects=False)
    assert "hata=" in response.headers["location"]
    assert not config_mod.keywords_overlay_path().exists()


def test_ne_ariyoruz_konuyu_degistirir(client):
    """SAP -> ERP: sorgular ve zorunlu kelimeler tek formdan degisir."""
    response = client.post("/ayarlar/ne-ariyoruz", data={
        "queries": "ERP yazilim\nERP developer",
        "must_any": "erp",
        "boost": "erp: 8",
        "min_score": "5",
    }, follow_redirects=False)
    assert response.status_code == 303
    assert "ok=" in response.headers["location"]

    assert config_mod.load_config()["queries"] == ["ERP yazilim", "ERP developer"]
    keywords = config_mod.load_keywords()
    assert keywords["must_any"] == ["erp"]
    assert keywords["boost"] == {"erp": 8}


def test_ne_ariyoruz_ic_ice_sinyali_korur(client):
    """signals.country_boost formda gosterilemiyor; kaydederken silinmemeli."""
    onceki = config_mod.load_keywords()["signals"]["country_boost"]
    client.post("/ayarlar/ne-ariyoruz", data={
        **TEMEL_ARAMA, "signals": "remote_boost: 20\ncontract_boost: 8"},
        follow_redirects=False)

    signals = config_mod.load_keywords()["signals"]
    assert signals["remote_boost"] == 20
    assert signals["country_boost"] == onceki      # dokunulmadi
    assert "hybrid_boost" not in signals           # formdan silinen gitti


def test_ne_ariyoruz_ekrandan_okunani_geri_yazmak_bozmaz(client):
    """Sayfayi acip hicbir sey degistirmeden kaydetmek ayarlari bozmamali."""
    from scanner import settings as settings_mod

    once = config_mod.load_keywords()
    client.post("/ayarlar/ne-ariyoruz", data={
        "queries": "\n".join(config_mod.load_config()["queries"]),
        "must_any": settings_mod.format_lines(once["must_any"]),
        "hard_exclude": settings_mod.format_lines(once["hard_exclude"]),
        "boost": settings_mod.format_weight_lines(once["boost"]),
        "penalty": settings_mod.format_weight_lines(once["penalty"]),
        "signals": settings_mod.format_weight_lines(once["signals"]),
        "min_score": str(once["min_score"]),
        "title_multiplier": str(once["title_multiplier"]),
    }, follow_redirects=False)

    assert config_mod.load_keywords() == once


# --- kodsuz yeni kaynak -----------------------------------------------
YENI_KAYNAK = {
    "title": "Deneme Panosu", "kind": "json",
    "url": "https://ornek.test/api?q={sorgu}",
    "items_path": "data", "pages": "1",
    "field__title": "baslik", "field__url": "link", "enabled": "1",
}


def test_yeni_kaynak_eklenir_ve_taramaya_girer(client):
    response = client.post("/ayarlar/kaynak-ekle", data=YENI_KAYNAK, follow_redirects=False)
    assert response.status_code == 303
    assert "ok=" in response.headers["location"]

    config = config_mod.load_config()
    kaynak = config["sources"]["deneme-panosu"]
    assert kaynak["kind"] == "json"
    assert kaynak["fields"] == {"title": "baslik", "url": "link"}

    # kodsuz kaynak gercekten tarama listesine giriyor mu
    from scanner.pipeline import active_sources
    assert "deneme-panosu" in active_sources(config)


def test_yeni_kaynak_gecersiz_adresi_reddeder(client):
    response = client.post("/ayarlar/kaynak-ekle",
                           data={**YENI_KAYNAK, "url": "ornek.test/api"},
                           follow_redirects=False)
    assert "hata=" in response.headers["location"]
    assert "deneme-panosu" not in (config_mod.load_config().get("sources") or {})


def test_yeni_kaynak_json_alan_eslemesi_ister(client):
    response = client.post("/ayarlar/kaynak-ekle",
                           data={**YENI_KAYNAK, "field__title": "", "field__url": ""},
                           follow_redirects=False)
    assert "hata=" in response.headers["location"]


def test_yeni_kaynak_kodda_tanimli_adi_kullanamaz(client):
    response = client.post("/ayarlar/kaynak-ekle", data={**YENI_KAYNAK, "title": "jooble"},
                           follow_redirects=False)
    assert "hata=" in response.headers["location"]


def test_ozel_kaynak_silinir(client):
    client.post("/ayarlar/kaynak-ekle", data=YENI_KAYNAK, follow_redirects=False)
    response = client.post("/ayarlar/kaynak-sil", data={"name": "deneme-panosu"},
                           follow_redirects=False)
    assert "ok=" in response.headers["location"]
    assert "deneme-panosu" not in (config_mod.load_config().get("sources") or {})


def test_kodda_tanimli_kaynak_silinemez(client):
    response = client.post("/ayarlar/kaynak-sil", data={"name": "jooble"},
                           follow_redirects=False)
    assert "hata=" in response.headers["location"]
    assert "jooble" in config_mod.load_config()["sources"]


def test_anahtar_kaydi_bos_alani_yok_sayar(client):
    response = client.post("/ayarlar/anahtarlar", data={"env__JOOBLE_API_KEY": ""},
                           follow_redirects=False)
    assert response.status_code == 303
    assert not config_mod.env_path().exists()      # bos form dosya olusturmamali


def test_sayfalama_baglantisi_yeni_filtreleri_tasir(client, tmp_path):
    """base_query'ye eklenmezse tarih araligi 2. sayfada sessizce kaybolurdu."""
    from datetime import datetime, timedelta, timezone

    from scanner.dedupe import fingerprint
    from scanner.models import Project
    from scanner.storage import Storage

    # sayfalama baglantisi ancak birden fazla sayfa varken cikar
    client.post("/ayarlar/tarama", data={"page_size": "5"}, follow_redirects=False)
    store = Storage(tmp_path / "data" / "projects.db")
    bugun = datetime.now(timezone.utc)
    ilanlar = []
    for i in range(12):
        p = Project(source="test", source_id=str(i), url=f"http://x/{i}",
                    title=f"SAP ABAP {i}", company=f"F{i}",
                    posted_at=bugun - timedelta(days=3))
        p.fingerprint = fingerprint(p)
        ilanlar.append(p)
    store.upsert(ilanlar)
    store.close()

    # sabit tarih yazmak testi takvime bagliyordu: first_seen_at hep "bugun"
    baslangic = (bugun - timedelta(days=7)).date().isoformat()
    bitis = bugun.date().isoformat()
    response = client.get(f"/?date_from={baslangic}&date_to={bitis}"
                          "&sort=budget&date_field=seen&min_score=0")
    assert response.status_code == 200
    for parca in (f"date_from={baslangic}", f"date_to={bitis}", "sort=budget", "date_field=seen"):
        assert parca in response.text, parca


def test_tarih_araligi_gun_filtresini_ezer(client):
    """Ikisi ayni ekseni filtreliyor; aralik seciliyse o kazanir."""
    response = client.get("/?days=7&date_from=2026-08-01")
    assert response.status_code == 200
    # "Yayin tarihi" secimi sifirlanmis olmali (7 secili kalmamali)
    assert '<option value="7" selected>' not in response.text
    assert 'value="2026-08-01"' in response.text


# --- zamanlayici ------------------------------------------------------
def test_reconfigure_thread_baslatmadan_uygular():
    scanner = BackgroundScanner(interval_minutes=30, quick_minutes=10)
    scanner.reconfigure(interval_minutes=60, quick_minutes=15, verify_limit=99)

    assert scanner.state.interval_minutes == 60
    assert scanner.state.quick_minutes == 15
    assert scanner.interval == 3600
    assert scanner.full_every == 4
    assert scanner.verify_limit == 99


def test_reconfigure_alt_sinirlari_zorlar():
    scanner = BackgroundScanner(interval_minutes=30, quick_minutes=10)
    scanner.reconfigure(interval_minutes=1, quick_minutes=1)
    assert scanner.state.interval_minutes == 5
    assert scanner.state.quick_minutes == 2


def test_duraklatma_thread_durdurmaz():
    """Masaustu uygulamasi zamanlayiciyi kendi yonetiyor; panel onu durdurmamali."""
    scanner = BackgroundScanner()
    scanner.set_enabled(False)
    assert scanner.state.enabled is False
    assert scanner._active.is_set() is False

    # "Şimdi tara" duraklatilmisken de bir tur istemeli
    assert scanner.trigger() is True
    assert scanner._force.is_set() is True

    scanner.set_enabled(True)
    assert scanner.state.enabled is True
    assert scanner._active.is_set() is True


# --- temizlik turu (Listeyi kontrol et) -----------------------------------

def test_temizlik_yetkisiz_kullaniciya_kapali(anon_client):
    assert anon_client.post("/api/temizlik", json={}, follow_redirects=False).status_code in (302, 303, 403)


def test_temizlik_baslar_ve_durumda_gorunur(client):
    response = client.post("/api/temizlik", json={"min_score": "0"})
    assert response.status_code == 200
    assert response.json()["ok"] is True

    durum = client.get("/api/durum").json()
    assert "sweep" in durum
    assert durum["sweep"]["phase"] in ("running", "done")


def test_temizlik_ikinci_istek_reddedilir(client, monkeypatch):
    """Ayni anda iki tur ag trafigini ikiye katlar; ikincisi 409 almali."""
    import scanner.web.app as app_mod

    # Tur bitmesin diye kontrol askida kalsin
    engel = __import__("threading").Event()

    def bekle(self, store, client_, rows, workers, profile_id):
        engel.wait(timeout=5)

    monkeypatch.setattr(app_mod.LinkSweeper, "_sweep", bekle)
    try:
        assert client.post("/api/temizlik", json={}).status_code == 200
        ikinci = client.post("/api/temizlik", json={})
        assert ikinci.status_code == 409
        assert "sürüyor" in ikinci.json()["message"]
    finally:
        engel.set()
