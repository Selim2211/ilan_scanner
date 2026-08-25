"""Cok profilli tarama: her profil KENDI sorgu ve kelimeleriyle taranmali.

Aga cikilmaz: kaynak kaydi sahte bir sinifla degistirilir.
"""
import pytest

from scanner import config as config_mod
from scanner import pipeline
from scanner.models import Project
from scanner.profiles import Profiles, default_payload, settings_for
from scanner.storage import Storage

#: Sahte kaynagin dondurdugu ilanlar - sorgu metnine gore secilir
KATALOG = {
    "sap abap": [("sap-1", "Senior SAP ABAP Developer", "SAP ABAP remote contract")],
    "mes": [("mes-1", "MES Consultant", "manufacturing execution system rollout")],
    "wms": [("wms-1", "WMS Lead", "warehouse management system project")],
}


class SahteKaynak:
    """pipeline'in bekledigi arayuz: safe_fetch(query) -> (ilanlar, hata)."""

    name = "sahte"
    probe_trim: tuple = ()

    def __init__(self, client, options):
        self.options = options

    def safe_fetch(self, query):
        cagrilar.append(query)
        bulunan = []
        for slug, baslik, aciklama in KATALOG.get(query.lower(), []):
            bulunan.append(Project(
                source="sahte", source_id=slug, url=f"http://ornek/{slug}", title=baslik,
                company="Firma", location="Warsaw", country="PL", work_mode="remote",
                is_contract=True, description=aciklama, skills=[], fingerprint=f"fp-{slug}"))
        return bulunan, ""


class SahteIstemci:
    """pipeline yalnizca close() cagiriyor; link kontrolu bu testlerde tetiklenmiyor."""

    def close(self):
        pass


cagrilar: list[str] = []


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    """(db_yolu, profiller) - gecici veri klasoru, sahte kaynak, ag yok."""
    monkeypatch.setattr(config_mod.paths, "data_dir", lambda: tmp_path)
    cagrilar.clear()

    monkeypatch.setattr(pipeline, "active_sources", lambda cfg, only=None: {"sahte": {}})
    monkeypatch.setattr(pipeline, "source_class", lambda name, options: SahteKaynak)
    monkeypatch.setattr(pipeline, "missing_credentials", lambda source: [])
    # config'deki quick_sources listesi sahte kaynagi elerdi
    monkeypatch.setattr(pipeline, "_quick_sources", lambda cfg, sources: sources)
    monkeypatch.setattr(pipeline, "build_client", lambda cfg: SahteIstemci())

    from scanner.config import load_config, resolve_path
    db = resolve_path(load_config()["database"])

    profiles = Profiles(db)
    sap = default_payload()
    sap.update(queries=["sap abap"], must_any=["abap"], boost={"abap": 6}, min_score=0)
    ucuncu = default_payload()
    ucuncu.update(queries=["mes", "wms"], must_any=["mes", "warehouse management"],
                  boost={"mes": 7}, min_score=0)
    sap_id = profiles.create("SAP", sap)
    ucuncu_id = profiles.create("3RD", ucuncu)
    profiles.activate(sap_id)
    yield db, profiles, sap_id, ucuncu_id
    profiles.close()


def test_settings_for_overlay_dosyalarina_yazmaz(ortam):
    """Tarama sirasinda diske yazmak, kullanici profil degistirirse cakisirdi."""
    from scanner.config import keywords_overlay_path, overlay_path

    _, profiles, _, ucuncu_id = ortam
    onceki = [(f.exists(), f.stat().st_mtime_ns if f.exists() else 0)
              for f in (overlay_path(), keywords_overlay_path())]
    settings_for(profiles.get(ucuncu_id))
    sonraki = [(f.exists(), f.stat().st_mtime_ns if f.exists() else 0)
               for f in (overlay_path(), keywords_overlay_path())]
    assert onceki == sonraki


def test_settings_for_profilleri_birbirinden_ayirir(ortam):
    _, profiles, sap_id, ucuncu_id = ortam
    sap_cfg, sap_kw = settings_for(profiles.get(sap_id))
    ucuncu_cfg, ucuncu_kw = settings_for(profiles.get(ucuncu_id))

    assert sap_cfg["queries"] == ["sap abap"]
    assert ucuncu_cfg["queries"] == ["mes", "wms"]
    assert sap_kw["boost"] == {"abap": 6}
    assert ucuncu_kw["boost"] == {"mes": 7}          # SAP'in kelimesi sizmadi
    assert "country_boost" in ucuncu_kw["signals"]   # ic ice sinyal korundu


def test_tam_tur_butun_profillerin_sorgularini_calistirir(ortam):
    """Asil hata buydu: etkin olmayan profilin sorgulari hic calismiyordu."""
    db, profiles, _, _ = ortam
    from scanner.scheduler import BackgroundScanner

    BackgroundScanner()._scan_all_profiles()

    assert set(cagrilar) == {"sap abap", "mes", "wms"}
    store = Storage(db)
    try:
        basliklar = {r["title"] for r in store.query(limit=50, min_score=-999)}
    finally:
        store.close()
    # her ilan KENDI profilinin must_any'sinden gecerek girdi
    assert "Senior SAP ABAP Developer" in basliklar
    assert "MES Consultant" in basliklar
    assert "WMS Lead" in basliklar


def test_tur_sonunda_etkin_profilin_puanlari_gecerli(ortam):
    """Son tarayan profilin puani kalmamali; kullanici etkin profili goruyor."""
    db, profiles, sap_id, _ = ortam
    from scanner.scheduler import BackgroundScanner

    BackgroundScanner()._scan_all_profiles()

    store = Storage(db)
    try:
        sap_ilani = store.get_by_fingerprint("fp-sap-1")
        mes_ilani = store.get_by_fingerprint("fp-mes-1")
    finally:
        store.close()
    # etkin profil SAP: abap gecen ilan puan alir, MES ilani must_any'den kalir
    assert sap_ilani["score"] > 0
    assert mes_ilani["score"] == 0


def test_arka_plan_anahtari_kapali_profil_taranmaz(ortam):
    db, profiles, sap_id, ucuncu_id = ortam
    payload = Profiles.payload_of(profiles.get(ucuncu_id))
    payload["scan_in_background"] = False
    profiles.update(ucuncu_id, "3RD", payload)

    from scanner.scheduler import BackgroundScanner
    BackgroundScanner()._scan_all_profiles()

    assert "sap abap" in cagrilar
    assert "mes" not in cagrilar


def test_etkin_profil_anahtari_kapali_olsa_da_taranir(ortam):
    """Onunde duran liste her turda beslenmeli."""
    db, profiles, sap_id, _ = ortam
    payload = Profiles.payload_of(profiles.get(sap_id))
    payload["scan_in_background"] = False
    profiles.update(sap_id, "SAP", payload)
    profiles.activate(sap_id)

    assert "SAP" in [r["name"] for r in profiles.scannable()]


def test_bildirimler_tarayan_profile_yazilir(ortam):
    """Cok profilli turda 3RD taramasinin bildirimi SAP akisina dusmemeli."""
    db, profiles, sap_id, ucuncu_id = ortam
    from scanner.scheduler import BackgroundScanner

    BackgroundScanner()._scan_all_profiles()

    store = Storage(db)
    try:
        sap_akisi = {n["title"] for n in store.notifications(profile_id=sap_id, limit=99)}
        ucuncu_akisi = {n["title"] for n in store.notifications(profile_id=ucuncu_id, limit=99)}
    finally:
        store.close()
    assert "Senior SAP ABAP Developer" in sap_akisi
    assert "MES Consultant" in ucuncu_akisi
    assert "MES Consultant" not in sap_akisi


def test_hizli_tur_yalnizca_etkin_profili_tarar(ortam):
    """Maliyet kontrolu: 8 dakikalik tur butun profilleri tarayamaz."""
    db, profiles, _, _ = ortam
    from scanner.scheduler import BackgroundScanner

    BackgroundScanner()._run_once(full=False)
    assert "sap abap" in cagrilar
    assert "mes" not in cagrilar


def test_profil_yokken_bugunku_davranis_surer(tmp_path, monkeypatch):
    """Profil kurmamis kurulumda tarama eskisi gibi tek turda calismali."""
    monkeypatch.setattr(config_mod.paths, "data_dir", lambda: tmp_path)
    cagrilar.clear()
    monkeypatch.setattr(pipeline, "active_sources", lambda cfg, only=None: {"sahte": {}})
    monkeypatch.setattr(pipeline, "source_class", lambda name, options: SahteKaynak)
    monkeypatch.setattr(pipeline, "missing_credentials", lambda source: [])
    # config'deki quick_sources listesi sahte kaynagi elerdi
    monkeypatch.setattr(pipeline, "_quick_sources", lambda cfg, sources: sources)
    monkeypatch.setattr(pipeline, "build_client", lambda cfg: SahteIstemci())

    from scanner.scheduler import BackgroundScanner
    BackgroundScanner()._scan_all_profiles()
    assert cagrilar            # config.yaml'daki sorgularla calisti


def test_bos_sinyal_listesi_uzaktan_onceligini_silmez(ortam):
    """Formda Sinyaller bos birakilirsa remote_boost 0 olup uygulamanin asil
    mantigi sessizce kapaniyordu."""
    _, profiles, _, ucuncu_id = ortam
    _, kw = settings_for(profiles.get(ucuncu_id))       # profilin signals'i bos
    assert kw["signals"]["remote_boost"] > 0
    assert kw["signals"]["contract_boost"] > 0


def test_profil_kendi_sinyalini_verirse_o_gecerli(ortam):
    _, profiles, _, ucuncu_id = ortam
    payload = Profiles.payload_of(profiles.get(ucuncu_id))
    payload["signals"] = {"remote_boost": 3}
    profiles.update(ucuncu_id, "3RD", payload)

    _, kw = settings_for(profiles.get(ucuncu_id))
    assert kw["signals"]["remote_boost"] == 3
    assert "country_boost" in kw["signals"]             # ic ice yapi yine korundu


# --- bayat ilanlarin kapanmasi ---------------------------------------------
def _ilan(store, slug, streak, source="sahte"):
    store.upsert([Project(source=source, source_id=slug, url=f"http://ornek/{slug}",
                          title=slug, description="abap", skills=[], score=10,
                          fingerprint=f"fp-{slug}")])
    store.conn.execute("UPDATE projects SET missing_streak = ? WHERE fingerprint = ?",
                       (streak, f"fp-{slug}"))
    store.conn.commit()


def test_dogrulama_sirasi_en_cok_kayip_olandan_baslar(tmp_path, monkeypatch):
    """Onceki sıralama yalnizca verified_at'e bakiyordu; 6-10 turdur kayip ilanlar
    sıranın sonunda kalip aylarca 'aktif' gorunuyordu."""
    monkeypatch.setattr(config_mod.paths, "data_dir", lambda: tmp_path)
    from scanner.config import load_config, resolve_path

    store = Storage(resolve_path(load_config()["database"]))
    try:
        _ilan(store, "taze", 0)
        _ilan(store, "cok-kayip", 9)
        _ilan(store, "az-kayip", 2)
        sira = [r["title"] for r in store.verify_candidates(limit=10, min_hours=0)]
    finally:
        store.close()
    assert sira[0] == "cok-kayip"
    assert sira.index("az-kayip") < sira.index("taze")


def test_uzun_suredir_kayip_ilan_son_care_kapatilir(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod.paths, "data_dir", lambda: tmp_path)
    from scanner.config import load_config, resolve_path

    store = Storage(resolve_path(load_config()["database"]))
    try:
        _ilan(store, "olu", 10)
        _ilan(store, "yasiyor", 3)
        kapananlar = store.close_stale("sahte", 10)      # config'deki esik
        assert {r["title"] for r in kapananlar} == {"olu"}
        assert store.get_by_fingerprint("fp-olu")["is_active"] == 0
        assert store.get_by_fingerprint("fp-yasiyor")["is_active"] == 1
    finally:
        store.close()
