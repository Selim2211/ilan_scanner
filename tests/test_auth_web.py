"""Giris, yetkiler, arama profilleri, gizlenen ilanlar ve sorunlu isaretler."""
import pytest
from fastapi.testclient import TestClient

from scanner import config as config_mod
from scanner.auth import Auth
from scanner.models import Project
from scanner.profiles import Profiles
from scanner.storage import Storage


def _project(slug: str, score: int, **extra) -> Project:
    return Project(source="jooble", source_id=slug, url=f"http://ornek/{slug}",
                   title=slug, company="Firma", location="Warsaw", country="PL",
                   description="ABAP ilani", skills=["SAP"], score=score,
                   fingerprint=f"fp-{slug}", **extra)


def _var(slug: str, html: str) -> bool:
    """Ilan sayfada listelenmis mi? Baslik metni <span> ile bitisik render edildigi
    icin duz ad aramasi yaniltici - ilan linki kesin isaret."""
    return f'http://ornek/{slug}"' in html


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """(istemci, db_yolu) - panel gecici klasorde, ornek ilanlarla."""
    monkeypatch.setattr(config_mod.paths, "data_dir", lambda: tmp_path)
    from scanner.config import load_config, resolve_path
    from scanner.web.app import create_app

    db = resolve_path(load_config()["database"])
    store = Storage(db)
    store.upsert([_project("alfa", 40, work_mode="remote", is_contract=True),
                  _project("beta", 30, work_mode="onsite"),
                  _project("gama", 20, work_mode="remote")])
    store.close()

    with TestClient(create_app(auto_scan=False)) as client:
        yield client, db


@pytest.fixture
def admin(panel):
    client, db = panel
    client.post("/giris", data={"username": "admin", "password": "123456", "next": "/"},
                follow_redirects=False)
    return client, db


# --- giris ---------------------------------------------------------------
def test_oturumsuz_istek_girise_yonlenir(panel):
    client, _ = panel
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert "/giris" in response.headers["location"]


def test_ilk_yonetici_kendiliginden_kurulur(panel):
    _, db = panel
    auth = Auth(db)
    try:
        assert auth.by_username("admin") is not None
        assert auth.admin_count() == 1
    finally:
        auth.close()


def test_yanlis_sifre_oturum_acmaz(panel):
    client, _ = panel
    response = client.post("/giris", data={"username": "admin", "password": "yanlis"},
                           follow_redirects=False)
    assert "radar_oturum" not in response.headers.get("set-cookie", "")
    assert "hata" in response.headers["location"]


def test_girisden_sonra_hedef_sayfaya_donulur(panel):
    client, _ = panel
    response = client.post("/giris", data={"username": "admin", "password": "123456",
                                           "next": "/bildirimler"}, follow_redirects=False)
    assert response.headers["location"] == "/bildirimler"


def test_cikis_oturumu_dusurur(admin):
    client, _ = admin
    client.post("/cikis", follow_redirects=False)
    assert client.get("/", follow_redirects=False).status_code == 303


# --- yetkiler ------------------------------------------------------------
def test_yetkisiz_kullanici_ayarlara_giremez(admin):
    client, db = admin
    auth = Auth(db)
    try:
        auth.create_user("dar", "parola123", permissions=["view_list"])
    finally:
        auth.close()

    dar = TestClient(client.app)
    dar.post("/giris", data={"username": "dar", "password": "parola123"})
    assert dar.get("/").status_code == 200
    assert dar.get("/ayarlar").status_code == 403


def test_yetkisiz_kullanici_ilan_isaretleyemez(admin):
    """Sadece okuma yetkisi olan kullanici isaret koyamaz, ilan kapatamaz."""
    client, db = admin
    auth = Auth(db)
    try:
        auth.create_user("dar", "parola123", permissions=["view_list"])
    finally:
        auth.close()

    dar = TestClient(client.app)
    dar.post("/giris", data={"username": "dar", "password": "parola123"})
    assert dar.get("/").status_code == 200
    assert dar.post("/status", data={"fingerprint": "fp-alfa", "status": "shortlist"}).status_code == 403
    assert dar.post("/kapandi", data={"fingerprint": "fp-alfa"}).status_code == 403


def test_yetkisiz_dugmeler_sablonda_hic_gorunmez(admin):
    client, db = admin
    auth = Auth(db)
    try:
        auth.create_user("dar", "parola123", permissions=["view_list"])
    finally:
        auth.close()

    dar = TestClient(client.app)
    dar.post("/giris", data={"username": "dar", "password": "parola123"})
    sayfa = dar.get("/").text
    assert 'href="/ayarlar"' not in sayfa


def test_son_yonetici_yetkisi_kaldirilamaz(admin):
    client, db = admin
    auth = Auth(db)
    try:
        admin_id = auth.by_username("admin")["id"]
    finally:
        auth.close()
    response = client.post("/ayarlar/kullanici-yetki",
                           data={"user_id": admin_id, "is_active": "1"},
                           follow_redirects=False)
    assert "hata" in response.headers["location"]

    auth = Auth(db)
    try:
        assert auth.get_user(admin_id).is_admin
    finally:
        auth.close()


def test_kullanici_kendini_silemez(admin):
    client, db = admin
    auth = Auth(db)
    try:
        admin_id = auth.by_username("admin")["id"]
    finally:
        auth.close()
    response = client.post("/ayarlar/kullanici-sil", data={"user_id": admin_id},
                           follow_redirects=False)
    assert "hata" in response.headers["location"]


def test_sifre_degisince_acik_oturumlar_duser(admin):
    client, db = admin
    auth = Auth(db)
    try:
        user_id = auth.create_user("selim", "parola123")
    finally:
        auth.close()

    selim = TestClient(client.app)
    selim.post("/giris", data={"username": "selim", "password": "parola123"})
    assert selim.get("/").status_code == 200

    client.post("/ayarlar/kullanici-sifre", data={"user_id": user_id, "password": "yenisifre1"},
                follow_redirects=False)
    assert selim.get("/", follow_redirects=False).status_code == 303


# --- sorunlu ilan isareti ------------------------------------------------
def test_isaret_koyulur_ve_kaldirilir(admin):
    client, db = admin
    client.post("/isaret", data={"fingerprint": "fp-alfa", "reason": "bölge kısıtlı"},
                follow_redirects=False)
    store = Storage(db)
    try:
        row = store.get_by_fingerprint("fp-alfa")
        assert row["quality_flag"] == "problem"
        assert row["flag_reason"] == "bölge kısıtlı"
        assert row["flagged_by"] == "admin"
    finally:
        store.close()

    client.post("/isaret", data={"fingerprint": "fp-alfa"}, follow_redirects=False)
    store = Storage(db)
    try:
        assert not (store.get_by_fingerprint("fp-alfa")["quality_flag"] or "")
    finally:
        store.close()


def test_sorunlu_filtresi_yalnizca_isaretlileri_getirir(admin):
    client, db = admin
    store = Storage(db)
    try:
        store.set_flag("fp-beta", "giriş gerekiyor")
    finally:
        store.close()

    sorunlu = client.get("/?flag=problem").text
    assert _var("beta", sorunlu) and not _var("alfa", sorunlu)
    temiz = client.get("/?flag=clean").text
    assert _var("alfa", temiz) and not _var("beta", temiz)


def test_isaretli_ilan_listede_sari_gosterilir(admin):
    client, db = admin
    store = Storage(db)
    try:
        store.set_flag("fp-alfa", "bölge kısıtlı")
    finally:
        store.close()
    sayfa = client.get("/").text
    assert "flagged" in sayfa
    assert "bölge kısıtlı" in sayfa


# --- bildirim silme ------------------------------------------------------
def test_bildirim_tek_tek_ve_toplu_silinir(admin):
    client, db = admin
    store = Storage(db)
    try:
        store.add_notification("new", "birinci")
        store.add_notification("new", "ikinci")
        ilk = store.notifications()[0]["id"]
    finally:
        store.close()

    client.post("/bildirimler/sil", data={"notification_id": ilk}, follow_redirects=False)
    store = Storage(db)
    try:
        assert len(store.notifications()) == 1
    finally:
        store.close()

    client.post("/bildirimler/sil", data={"hepsi": "1"}, follow_redirects=False)
    store = Storage(db)
    try:
        assert store.notifications() == []
    finally:
        store.close()


# --- arama profilleri ----------------------------------------------------
def test_profil_eklenir_ve_etkin_yapilir(admin):
    client, db = admin
    client.post("/ayarlar/profil-ekle", data={
        "name": "Salesforce", "queries": "salesforce remote", "must_any": "salesforce",
        "boost": "apex: 5", "min_score": "8", "title_multiplier": "2.0",
        "view_mode": "remote", "view_sort": "date"}, follow_redirects=False)

    profiles = Profiles(db)
    try:
        row = profiles.by_name("Salesforce")
        assert row is not None
        client.post("/profil-sec", data={"profile_id": row["id"]}, follow_redirects=False)
        assert profiles.active()["name"] == "Salesforce"
    finally:
        profiles.close()
    assert "Salesforce" in client.get("/").text


def test_profil_gecisi_ayarlari_uygular(admin):
    client, db = admin
    client.post("/ayarlar/profil-ekle", data={
        "name": "ERP", "queries": "erp danismani", "must_any": "erp",
        "min_score": "5", "title_multiplier": "2.0"}, follow_redirects=False)
    profiles = Profiles(db)
    try:
        profile_id = profiles.by_name("ERP")["id"]
    finally:
        profiles.close()
    client.post("/profil-sec", data={"profile_id": profile_id}, follow_redirects=False)

    from scanner.config import load_config, load_keywords
    assert load_config()["queries"] == ["erp danismani"]
    assert load_keywords()["must_any"] == ["erp"]


def test_etkin_profil_liste_filtresini_belirler(admin):
    """Profil 'uzaktan' diyorsa liste varsayilan olarak uzaktan ilanlari gosterir."""
    client, db = admin
    client.post("/ayarlar/profil-ekle", data={
        # min_score negatif: sinav konusu GORUNUM filtresi (mode), puanlama degil.
        # onsite ilan artik onsite_penalty aliyor; esik 0 olsaydi puandan elenir,
        # mode filtresinin isini yaptigini gormezdik.
        "name": "Sadece Uzaktan", "queries": "sap", "must_any": "abap",
        "min_score": "-10", "title_multiplier": "2.0", "view_mode": "remote"},
        follow_redirects=False)
    profiles = Profiles(db)
    try:
        profile_id = profiles.by_name("Sadece Uzaktan")["id"]
    finally:
        profiles.close()
    client.post("/profil-sec", data={"profile_id": profile_id}, follow_redirects=False)

    sayfa = client.get("/").text
    assert _var("alfa", sayfa)                # remote
    assert not _var("beta", sayfa)            # onsite - profil filtresiyle elendi
    # adres cubugundaki secim profili ezer
    assert _var("beta", client.get("/?mode=onsite").text)


def test_profil_birakilinca_filtre_kalkar(admin):
    client, db = admin
    client.post("/ayarlar/profil-ekle", data={
        # min_score negatif: sinav konusu GORUNUM filtresi (mode), puanlama degil.
        # onsite ilan artik onsite_penalty aliyor; esik 0 olsaydi puandan elenir,
        # mode filtresinin isini yaptigini gormezdik.
        "name": "Sadece Uzaktan", "queries": "sap", "must_any": "abap",
        "min_score": "-10", "title_multiplier": "2.0", "view_mode": "remote"},
        follow_redirects=False)
    profiles = Profiles(db)
    try:
        profile_id = profiles.by_name("Sadece Uzaktan")["id"]
    finally:
        profiles.close()
    client.post("/profil-sec", data={"profile_id": profile_id}, follow_redirects=False)
    client.post("/profil-sec", data={"profile_id": 0}, follow_redirects=False)
    assert _var("beta", client.get("/").text)


def test_bos_sorgulu_profil_reddedilir(admin):
    client, _ = admin
    response = client.post("/ayarlar/profil-ekle",
                           data={"name": "Bos", "queries": "", "must_any": "x"},
                           follow_redirects=False)
    assert "hata" in response.headers["location"]


def test_ayni_isimli_profil_iki_kez_eklenmez(admin):
    client, _ = admin
    veri = {"name": "Tekil", "queries": "sap", "must_any": "abap",
            "min_score": "0", "title_multiplier": "2.0"}
    client.post("/ayarlar/profil-ekle", data=veri, follow_redirects=False)
    response = client.post("/ayarlar/profil-ekle", data=veri, follow_redirects=False)
    assert "hata" in response.headers["location"]


# --- bakim ---------------------------------------------------------------
def test_bakim_secili_gorevi_calistirir(admin):
    client, _ = admin
    response = client.post("/ayarlar/bakim", data={"gorev": ["sessions", "temp"]},
                           follow_redirects=False)
    assert "ok" in response.headers["location"]


def test_bakim_bos_secimi_reddeder(admin):
    client, _ = admin
    response = client.post("/ayarlar/bakim", data={}, follow_redirects=False)
    assert "hata" in response.headers["location"]


def test_bakim_aktif_ilanlara_dokunmaz(admin):
    client, db = admin
    client.post("/ayarlar/bakim", data={"gorev": ["notifications", "closed", "vacuum"]},
                follow_redirects=False)
    store = Storage(db)
    try:
        assert store.stats()["active"] == 3
    finally:
        store.close()


# --- profil basina bildirim / favori / atama --------------------------------
def _iki_profil(client, db):
    """(A, B) profil kimlikleri - ikisi de ayni ilanlari kapsar."""
    ortak = {"queries": "sap", "must_any": "abap", "min_score": "0", "title_multiplier": "2.0"}
    client.post("/ayarlar/profil-ekle", data={"name": "PA", **ortak}, follow_redirects=False)
    client.post("/ayarlar/profil-ekle", data={"name": "PB", **ortak}, follow_redirects=False)
    profiles = Profiles(db)
    try:
        return profiles.by_name("PA")["id"], profiles.by_name("PB")["id"]
    finally:
        profiles.close()


def test_favoriler_profile_gore_ayrisir(admin):
    client, db = admin
    pa, pb = _iki_profil(client, db)

    client.post("/profil-sec", data={"profile_id": pa}, follow_redirects=False)
    client.post("/status", data={"fingerprint": "fp-alfa", "status": "shortlist"},
                follow_redirects=False)
    assert _var("alfa", client.get("/?status=shortlist").text)

    # B profilinde ayni ilan takipte OLMAMALI
    client.post("/profil-sec", data={"profile_id": pb}, follow_redirects=False)
    assert not _var("alfa", client.get("/?status=shortlist").text)

    client.post("/profil-sec", data={"profile_id": pa}, follow_redirects=False)
    assert _var("alfa", client.get("/?status=shortlist").text)


def test_basvuru_isareti_profile_gore_ayrisir(admin):
    client, db = admin
    pa, pb = _iki_profil(client, db)

    client.post("/profil-sec", data={"profile_id": pa}, follow_redirects=False)
    client.post("/status", data={"fingerprint": "fp-alfa", "status": "applied"},
                follow_redirects=False)
    assert _var("alfa", client.get("/?status=applied").text)

    client.post("/profil-sec", data={"profile_id": pb}, follow_redirects=False)
    assert not _var("alfa", client.get("/?status=applied").text)   # B'de isaretli degil
    store = Storage(db)
    try:
        assert store.status_of("fp-alfa", pa) == "applied"
        assert store.status_of("fp-alfa", pb) == "new"
    finally:
        store.close()


def test_bildirimler_profile_gore_ayrisir(admin):
    client, db = admin
    pa, pb = _iki_profil(client, db)
    store = Storage(db)
    try:
        store.add_notification("new", "A-ilani", profile_id=pa)
        store.add_notification("new", "B-ilani", profile_id=pb)
    finally:
        store.close()

    client.post("/profil-sec", data={"profile_id": pa}, follow_redirects=False)
    sayfa = client.get("/bildirimler").text
    assert "A-ilani" in sayfa and "B-ilani" not in sayfa

    client.post("/profil-sec", data={"profile_id": pb}, follow_redirects=False)
    sayfa = client.get("/bildirimler").text
    assert "B-ilani" in sayfa and "A-ilani" not in sayfa


def test_bildirim_toplu_silme_diger_profili_etkilemez(admin):
    client, db = admin
    pa, pb = _iki_profil(client, db)
    store = Storage(db)
    try:
        store.add_notification("new", "A-ilani", profile_id=pa)
        store.add_notification("new", "B-ilani", profile_id=pb)
    finally:
        store.close()

    client.post("/profil-sec", data={"profile_id": pa}, follow_redirects=False)
    client.post("/bildirimler/sil", data={"hepsi": "1"}, follow_redirects=False)
    store = Storage(db)
    try:
        assert store.notifications(profile_id=pa) == []
        assert len(store.notifications(profile_id=pb)) == 1
    finally:
        store.close()


def test_kullanici_yalnizca_atanan_profili_gorur(admin):
    client, db = admin
    pa, pb = _iki_profil(client, db)
    auth = Auth(db)
    try:
        uid = auth.create_user("dar", "parola123", permissions=["view_list"])
    finally:
        auth.close()
    client.post("/ayarlar/kullanici-yetki",
                data={"user_id": uid, "is_active": "1", "perm": ["view_list"],
                      "profil": [str(pa)]}, follow_redirects=False)

    profiles = Profiles(db)
    try:
        assert profiles.assigned_ids(uid) == {pa}
    finally:
        profiles.close()

    dar = TestClient(client.app)
    dar.post("/giris", data={"username": "dar", "password": "parola123"})
    sayfa = dar.get("/").text
    assert ">PA<" in sayfa and ">PB<" not in sayfa       # secicide yalnizca atanan


def test_atanmamis_profile_gecis_engellenir(admin):
    client, db = admin
    pa, pb = _iki_profil(client, db)
    auth = Auth(db)
    try:
        uid = auth.create_user("dar", "parola123", permissions=["view_list"])
    finally:
        auth.close()
    client.post("/ayarlar/kullanici-yetki",
                data={"user_id": uid, "is_active": "1", "perm": ["view_list"],
                      "profil": [str(pa)]}, follow_redirects=False)

    dar = TestClient(client.app)
    dar.post("/giris", data={"username": "dar", "password": "parola123"})
    assert dar.post("/profil-sec", data={"profile_id": pa}).status_code == 200
    assert dar.post("/profil-sec", data={"profile_id": pb}).status_code == 403


def test_profil_gecisi_parametreleri_tamamen_degistirir(admin):
    """Bos alan onceki profilden sizmamali - profiller birbirinden bagimsiz."""
    client, db = admin
    client.post("/ayarlar/profil-ekle", data={
        "name": "Dolu", "queries": "sap", "must_any": "abap", "hard_exclude": "stajyer",
        "boost": "abap: 6", "penalty": "intern: 5", "min_score": "10",
        "title_multiplier": "2.0"}, follow_redirects=False)
    client.post("/ayarlar/profil-ekle", data={
        "name": "Bos", "queries": "mes", "must_any": "mes", "hard_exclude": "",
        "boost": "mes: 7", "penalty": "", "min_score": "12",
        "title_multiplier": "2.0"}, follow_redirects=False)

    profiles = Profiles(db)
    try:
        dolu, bos = profiles.by_name("Dolu")["id"], profiles.by_name("Bos")["id"]
    finally:
        profiles.close()

    from scanner.config import load_keywords
    client.post("/profil-sec", data={"profile_id": dolu}, follow_redirects=False)
    k = load_keywords()
    assert k["hard_exclude"] == ["stajyer"] and k["boost"] == {"abap": 6}

    client.post("/profil-sec", data={"profile_id": bos}, follow_redirects=False)
    k = load_keywords()
    assert k["hard_exclude"] == []          # onceki profilin degeri sizmadi
    assert k["boost"] == {"mes": 7}
    assert k["penalty"] == {}
    assert "country_boost" in k["signals"]  # ic ice sinyal korundu


# --- admin paneli ayri sayfa -----------------------------------------------
def test_admin_paneli_ayri_sayfada(admin):
    client, _ = admin
    sayfa = client.get("/admin").text
    assert "Kullanıcı yönetimi" in sayfa
    assert "Yeni kullanıcı oluştur" in sayfa
    # ayarlar sayfasinda artik kullanici yonetimi yok
    ayarlar = client.get("/ayarlar").text
    assert "Yeni kullanıcı oluştur" not in ayarlar
    assert "Kayıtlı kullanıcılar" not in ayarlar


def test_admin_sekmesi_yalnizca_yetkiliye_gorunur(admin):
    client, db = admin
    assert 'href="/admin"' in client.get("/").text        # yonetici gorur

    auth = Auth(db)
    try:
        auth.create_user("dar", "parola123", permissions=["view_list", "view_settings"])
    finally:
        auth.close()
    dar = TestClient(client.app)
    dar.post("/giris", data={"username": "dar", "password": "parola123"})
    sayfa = dar.get("/").text
    assert 'href="/admin"' not in sayfa                   # varligini bile gormez
    assert dar.get("/admin").status_code == 403           # dogrudan adres de kapali


def test_kullanici_islemleri_admin_sayfasina_doner(admin):
    client, _ = admin
    response = client.post("/ayarlar/kullanici-ekle",
                           data={"username": "yenikisi", "password": "parola123"},
                           follow_redirects=False)
    assert response.headers["location"].startswith("/admin")


# --- bakim sayilari ---------------------------------------------------------
def test_bakim_sayilari_silinecek_kadari_gosterir(panel):
    """Rozetteki sayi TOPLAMI degil, o gorevin gercekten silecegi kadari gostermeli."""
    from datetime import datetime, timedelta, timezone

    from scanner import maintenance as maint
    from scanner.config import load_config

    _, db = panel
    store = Storage(db)
    try:
        eski = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        yeni = datetime.now(timezone.utc).isoformat()
        # 1 eski + 3 yeni bildirim
        store.conn.execute("INSERT INTO notifications (created_at, kind, title) VALUES (?,?,?)",
                           (eski, "new", "cok eski"))
        for i in range(3):
            store.conn.execute("INSERT INTO notifications (created_at, kind, title) VALUES (?,?,?)",
                               (yeni, "new", f"taze {i}"))
        # 1 eski kapanmis ilan, 1 yeni kapanmis
        store.conn.execute("UPDATE projects SET is_active=0, closed_at=? WHERE fingerprint='fp-beta'",
                           (eski,))
        store.conn.execute("UPDATE projects SET is_active=0, closed_at=? WHERE fingerprint='fp-gama'",
                           (yeni,))
        store.conn.commit()
        assert len(store.notifications(limit=999)) == 4
    finally:
        store.close()

    gorevler = {t.key: t for t in maint.survey(load_config())}
    # toplam 4 bildirim var ama yalnizca 1 tanesi esikten eski
    assert gorevler["notifications"].count == 1
    # kapanan ilanlarda yas siniri YOK: hepsi silinir (basvurulamaz, yer kaplar)
    assert gorevler["closed"].count == 2

    maint.run(["notifications", "closed"], load_config())
    store = Storage(db)
    try:
        assert len(store.notifications(limit=999)) == 3       # taze olanlar durdu
        assert store.get_by_fingerprint("fp-gama") is None    # yeni kapanan da silindi
        assert store.get_by_fingerprint("fp-beta") is None
        assert store.get_by_fingerprint("fp-alfa") is not None  # AKTIF ilana dokunulmadi
    finally:
        store.close()

    # temizlikten sonra sayilar sifirlanmali
    sonra = {t.key: t for t in maint.survey(load_config())}
    assert sonra["notifications"].count == 0
    assert sonra["closed"].count == 0
    assert sonra["notifications"].summary == "temiz"


def test_bakim_dolmus_oturumu_sayar(panel):
    from datetime import datetime, timedelta, timezone

    from scanner import maintenance as maint
    from scanner.config import load_config

    _, db = panel
    auth = Auth(db)
    try:
        gecmis = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        auth.conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            ("olu", 1, gecmis, gecmis))
        auth.conn.commit()
        assert auth.expired_session_count() == 1
    finally:
        auth.close()

    assert {t.key: t.count for t in maint.survey(load_config())}["sessions"] == 1
    maint.run(["sessions"], load_config())
    assert {t.key: t.count for t in maint.survey(load_config())}["sessions"] == 0


# --- puanlama seffafligi ----------------------------------------------------
def test_puan_dokumu_agirliklariyla_gosterilir(admin):
    """keywords_hit veritabaninda duruyordu ama hicbir ekranda gorunmuyordu."""
    client, db = admin
    store = Storage(db)
    try:
        store.update_scores([("fp-alfa", 30, ["abap", "+remote", "-onsite"])])
    finally:
        store.close()

    sayfa = client.get("/").text
    assert "Neden 30 puan?" in sayfa
    assert "abap" in sayfa and "+remote" in sayfa
    # agirliklar keywords.yaml'dan okunup rozete yaziliyor
    assert "+12" in sayfa          # remote_boost
    assert "-4" in sayfa           # onsite_penalty


def test_ayni_ilan_baska_kaynakta_da_gosterilir(admin):
    client, db = admin
    store = Storage(db)
    try:
        store.conn.execute(
            "UPDATE projects SET also_seen_on = ? WHERE fingerprint = 'fp-alfa'",
            ('["reed", "careerjet"]',))
        store.conn.commit()
    finally:
        store.close()
    sayfa = client.get("/").text
    assert "ayrıca:" in sayfa
    assert "reed, careerjet" in sayfa


# --- supheli rozeti esigi ---------------------------------------------------
def test_supheli_rozeti_esigin_altinda_gosterilmez(admin):
    """Arayuz 1'den itibaren rozet basinca aktif listenin dortte ucu
    'kontrol ediliyor' gorunuyordu; sistem esigi ise 2."""
    client, db = admin
    store = Storage(db)
    try:
        store.conn.execute("UPDATE projects SET missing_streak = 1 WHERE fingerprint = 'fp-alfa'")
        store.conn.commit()
    finally:
        store.close()
    assert "KONTROL EDİLİYOR" not in client.get("/").text

    store = Storage(db)
    try:
        store.conn.execute("UPDATE projects SET missing_streak = 2 WHERE fingerprint = 'fp-alfa'")
        store.conn.commit()
    finally:
        store.close()
    assert "KONTROL EDİLİYOR" in client.get("/").text
