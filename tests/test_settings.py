"""Ayarlar ekraninin veri katmani: overlay, .env yazimi, kaynak katalogu, kontrol (probe)."""
import hashlib

import pytest

from scanner import config as config_mod
from scanner import settings
from scanner.pipeline import missing_credentials
from scanner.probe import probe_source
from scanner.sources import REGISTRY
from scanner.sources.base import BaseSource, BlockedError, EnvVar


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """Overlay ve .env'i gecici klasore alir; gercek proje dosyalarina dokunulmaz."""
    monkeypatch.setattr(config_mod.paths, "data_dir", lambda: tmp_path)
    return tmp_path


# --- overlay birlestirme ---------------------------------------------
def test_deep_merge_ic_ice_ve_liste():
    base = {"a": {"x": 1, "y": 2}, "liste": [1, 2, 3], "sabit": "kal"}
    patch = {"a": {"y": 9, "z": 3}, "liste": [7]}
    assert config_mod.deep_merge(base, patch) == {
        "a": {"x": 1, "y": 9, "z": 3}, "liste": [7], "sabit": "kal"}


def test_deep_merge_none_anahtari_siler():
    """Kullanici bir kelimeyi listeden silince saf birlestirme onu geri getirirdi."""
    base = {"boost": {"abap": 6, "fiori": 2}}
    assert config_mod.deep_merge(base, {"boost": {"fiori": None}}) == {"boost": {"abap": 6}}


def test_overlay_config_yamli_ezmez(overlay):
    """En onemli garanti: config.yaml'in aciklamalari korunmali."""
    kaynak = config_mod.config_dir() / "config.yaml"
    once = hashlib.sha256(kaynak.read_bytes()).hexdigest()

    config_mod.save_overlay({"panel": {"page_size": 77}})
    config = config_mod.load_config()

    assert config["panel"]["page_size"] == 77
    assert config["sources"]["jooble"]["queries"]          # taban degerler duruyor
    assert hashlib.sha256(kaynak.read_bytes()).hexdigest() == once


def test_prune_unchanged_tabanla_ayni_olani_atar():
    """Overlay her kaydediste butun listeleri kopyalarsa config.yaml varsayilanlari
    donar: sonraki surumde sorgu listesi iyilestirilse kullaniciya ulasmaz."""
    base = {"sources": {"jooble": {"enabled": True, "queries": ["a", "b"]},
                        "ted": {"enabled": True}}}
    patch = {"sources": {"jooble": {"enabled": True, "queries": ["a"]},
                         "ted": {"enabled": True}}}
    assert config_mod.prune_unchanged(patch, base) == {"sources": {"jooble": {"queries": ["a"]}}}


def test_prune_unchanged_tabanda_olmayani_korur():
    assert config_mod.prune_unchanged({"yeni": 1}, {"eski": 2}) == {"yeni": 1}


def test_save_overlay_base_ile_sadece_farki_yazar(overlay):
    base = {"panel": {"page_size": 20}, "auto_scan": {"enabled": True}}
    config_mod.save_overlay({"panel": {"page_size": 20}, "auto_scan": {"enabled": False}},
                            base=base)
    assert config_mod.load_overlay(config_mod.overlay_path()) == {"auto_scan": {"enabled": False}}


def test_save_overlay_onceki_icerigi_korur(overlay):
    config_mod.save_overlay({"panel": {"page_size": 30}})
    config_mod.save_overlay({"auto_scan": {"enabled": False}})
    data = config_mod.load_overlay(config_mod.overlay_path())
    assert data["panel"]["page_size"] == 30
    assert data["auto_scan"]["enabled"] is False


# --- sifreli anahtar deposu ------------------------------------------
def test_anahtar_diske_duz_metin_yazilmaz(overlay, monkeypatch):
    """Asil istek: anahtar veritabaninda SIFRELI dursun, duz metin gorunmesin."""
    import os

    from scanner import secrets_store

    config_mod.save_env({"JOOBLE_API_KEY": "cok-gizli-anahtar"})

    ham = secrets_store.db_path().read_bytes()
    assert b"cok-gizli-anahtar" not in ham          # dosyada duz metin YOK
    assert secrets_store.load_all()["JOOBLE_API_KEY"] == "cok-gizli-anahtar"
    # setdefault tuzagi: calisan surec yeni anahtari hemen gormeli
    assert os.environ["JOOBLE_API_KEY"] == "cok-gizli-anahtar"


def test_anahtar_degistirilebilir_ve_silinebilir(overlay, monkeypatch):
    """Kullanici "anahtari yarin obur gun degistirecegiz" dedi: akis calismali."""
    import os

    from scanner import secrets_store

    config_mod.save_env({"REED_API_KEY": "birinci"})
    config_mod.save_env({"REED_API_KEY": "ikinci"})
    assert secrets_store.load_all()["REED_API_KEY"] == "ikinci"

    config_mod.save_env({"REED_API_KEY": ""})       # bos deger = sil
    assert "REED_API_KEY" not in secrets_store.load_all()
    assert os.environ.get("REED_API_KEY") is None


def test_env_dosyasindaki_anahtarlar_sifreli_depoya_tasinir(overlay, monkeypatch):
    """Mevcut kurulumlar ve anahtari imaja gomulu Docker paketi bozulmamali."""
    from scanner import secrets_store

    config_mod.env_path().write_text("# anahtarlar\nJOOBLE_API_KEY=dosyadan\n",
                                     encoding="utf-8")
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)

    config_mod._load_dotenv()

    assert secrets_store.load_all()["JOOBLE_API_KEY"] == "dosyadan"


def test_panelden_girilen_anahtar_eski_env_satirini_ezmez(overlay, monkeypatch):
    """Sifreli depo `.env`den ONCE okunmali - yoksa kurulumdan kalma eski
    satir, panelden girilen guncel anahtari geri getirirdi."""
    import os

    config_mod.env_path().write_text("JOOBLE_API_KEY=eski\n", encoding="utf-8")
    config_mod.save_env({"JOOBLE_API_KEY": "panelden-yeni"})
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)

    config_mod._load_dotenv()

    assert os.environ["JOOBLE_API_KEY"] == "panelden-yeni"


def test_gercek_ortam_degiskeni_depoyu_ezer(overlay, monkeypatch):
    """systemd / docker -e ile verilen deger her seyin onunde kalmali."""
    import os

    config_mod.save_env({"REED_API_KEY": "depodan"})
    monkeypatch.setenv("REED_API_KEY", "ortamdan")

    config_mod._load_dotenv()

    assert os.environ["REED_API_KEY"] == "ortamdan"


def test_cozme_anahtari_kaybolursa_panel_ayakta_kalir(overlay, monkeypatch):
    """secret.key silinirse degerler cozulemez ama uygulama COKMEZ."""
    from scanner import secrets_store

    config_mod.save_env({"REED_API_KEY": "gizli"})
    secrets_store.key_path().unlink()               # anahtar kayboldu

    assert secrets_store.load_all() == {}           # sessizce bos doner


# --- maskeleme / temizleme -------------------------------------------
@pytest.mark.parametrize("value", ["", None])
def test_mask_bos_deger(value):
    assert settings.mask(value) == "tanımsız"


@pytest.mark.parametrize("gizli", ["kisa", "abcdef123456", "9441407c-49e5-4519-8f8f"])
def test_mask_hicbir_karakter_sizdirmaz(gizli):
    """Eskiden ilk 2 + son 2 karakter aciktaydi (`ab••••••56`); artik yalnizca yildiz."""
    maskeli = settings.mask(gizli)
    assert set(maskeli) == {"*"}
    assert gizli not in maskeli
    # Uzunluk da bilgi sizdirir: maske her anahtar icin AYNI uzunlukta olmali
    assert maskeli == settings.mask("bambaska-uzunlukta-bir-anahtar")


def test_scrub_anahtari_hata_mesajindan_siler(monkeypatch):
    """Jooble anahtari URL yolunda gidiyor, hata mesaji runs tablosuna dusuyor."""
    monkeypatch.setenv("JOOBLE_API_KEY", "cok-gizli-anahtar")
    mesaj = "FetchError: HTTP 500 - https://jooble.org/api/cok-gizli-anahtar"
    temiz = settings.scrub(mesaj)
    assert "cok-gizli-anahtar" not in temiz
    assert "***" in temiz


# --- kaynak katalogu --------------------------------------------------
def test_env_requirements_coklu_anahtari_bildirir():
    assert [v.name for v in REGISTRY["upwork"].env_requirements()][:2] == \
        ["UPWORK_CLIENT_ID", "UPWORK_CLIENT_SECRET"]
    assert REGISTRY["ted"].env_requirements() == ()

    class Eski(BaseSource):                      # sadece requires_env tanimlayan eski bicim
        name = "eski"
        requires_env = "ESKI_KEY"

    assert [v.name for v in Eski.env_requirements()] == ["ESKI_KEY"]


def test_eksik_ikinci_anahtar_kaynagi_atlatir(monkeypatch):
    """APP_ID var, APP_KEY yokken kaynak calisip 401 aliyordu."""
    monkeypatch.setenv("UPWORK_CLIENT_ID", "var")
    monkeypatch.delenv("UPWORK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("UPWORK_TOKEN", raising=False)
    source = REGISTRY["upwork"](client=None, options={})
    assert missing_credentials(source) == ["UPWORK_CLIENT_SECRET"]


def test_source_catalog_tum_kaynaklari_listeler():
    catalog = settings.source_catalog({"sources": {"jooble": {"enabled": True, "queries": ["x"]}}})
    isimler = {row["name"] for row in catalog}
    assert isimler == set(REGISTRY)
    jooble = next(row for row in catalog if row["name"] == "jooble")
    assert jooble["enabled"] is True
    assert jooble["lists"]["queries"] == ["x"]
    # config'te hic yazmayan kaynak da kapali olarak listeye girer
    assert next(row for row in catalog if row["name"] == "ted")["enabled"] is False


# --- form ayristirma --------------------------------------------------
def test_parse_lines_yorum_ve_bosluk_atlar():
    assert settings.parse_lines("sap\n\n# yorum\n  abap  \n") == ["sap", "abap"]


def test_parse_weight_lines_bicimleri():
    assert settings.parse_weight_lines("abap: 6\nfiori = 2\nrap 3\nnegatif: -4") == \
        {"abap": 6, "fiori": 2, "rap": 3, "negatif": -4}


def test_parse_weight_lines_sayisiz_satirda_hata_verir():
    """Sessizce dusurulurse kullanici agirligi kaydettigini sanip yanlis puanla calisirdi."""
    with pytest.raises(ValueError):
        settings.parse_weight_lines("abap: cok")


# --- kontrol (probe) --------------------------------------------------
class SahteKaynak(BaseSource):
    name = "sahte"
    title = "Sahte"
    sonuc: list = []
    hata: Exception | None = None

    def fetch(self, query):
        if self.hata:
            raise self.hata
        return self.sonuc


@pytest.fixture
def sahte_registry(monkeypatch, tmp_path):
    monkeypatch.setitem(REGISTRY, "sahte", SahteKaynak)
    import scanner.probe as probe_mod
    monkeypatch.setitem(probe_mod.REGISTRY, "sahte", SahteKaynak)
    monkeypatch.chdir(tmp_path)                  # DB yazilirsa burada gorunur
    return tmp_path


def test_probe_sonuc_ve_basliklari_dondurur(sahte_registry, monkeypatch):
    from scanner.models import Project
    ilanlar = [Project(source="sahte", source_id=str(i), url="http://x", title=f"SAP ABAP {i}")
               for i in range(5)]
    monkeypatch.setattr(SahteKaynak, "sonuc", ilanlar)
    monkeypatch.setattr(SahteKaynak, "hata", None)

    sonuc = probe_source("sahte", config={"sources": {}, "queries": ["SAP ABAP"], "http": {}})

    assert sonuc.ok and sonuc.count == 5
    assert sonuc.titles == ["SAP ABAP 0", "SAP ABAP 1", "SAP ABAP 2"]
    assert "5 ilan geldi" in sonuc.message
    # dry-run: kontrol veritabanina dokunmamali
    assert not list(sahte_registry.glob("**/*.db"))


def test_probe_401i_turkce_anlatir_ve_anahtari_maskeler(sahte_registry, monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "gizli-anahtar-123")
    monkeypatch.setattr(SahteKaynak, "hata",
                        BlockedError("HTTP 401 - engellendi: https://x/api/gizli-anahtar-123"))

    sonuc = probe_source("sahte", config={"sources": {}, "queries": ["q"], "http": {}})

    assert not sonuc.ok
    assert "401" in sonuc.message and "Anahtar reddedildi" in sonuc.message
    assert "gizli-anahtar-123" not in sonuc.detail


def test_probe_eksik_anahtarda_aga_cikmaz(monkeypatch):
    class Anahtarli(BaseSource):
        name = "anahtarli"
        env_vars = (EnvVar("YOK_BOYLE_BIR_KEY", "Anahtar"),)

        def fetch(self, query):
            raise AssertionError("anahtar yokken aga cikilmamali")

    monkeypatch.setitem(REGISTRY, "anahtarli", Anahtarli)
    import scanner.probe as probe_mod
    monkeypatch.setitem(probe_mod.REGISTRY, "anahtarli", Anahtarli)
    monkeypatch.delenv("YOK_BOYLE_BIR_KEY", raising=False)

    sonuc = probe_source("anahtarli", config={"sources": {}, "queries": ["q"], "http": {}})
    assert not sonuc.ok
    assert "YOK_BOYLE_BIR_KEY" in sonuc.message


def test_probe_bilinmeyen_kaynak():
    sonuc = probe_source("boyle-bir-kaynak-yok", config={"sources": {}, "http": {}})
    assert not sonuc.ok
    assert "Bilinmeyen kaynak" in sonuc.message


def test_probe_kod_listelerini_kisaltmaz():
    """skill_ids/cpv tek istege siginiyor; kisaltmak aramayi daraltir, hiz kazandirmaz."""
    assert "skill_ids" not in REGISTRY["freelancercom"].probe_trim
    assert "cpv" not in REGISTRY["ted"].probe_trim
    assert "queries" in REGISTRY["reed"].probe_trim
