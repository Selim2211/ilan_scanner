"""Kodsuz kaynak: RSS/JSON eslemesi, hata mesajlari, kayit defteri cozumlemesi."""
import pytest

from scanner.sources import CustomSource, is_custom, source_class
from scanner.sources.base import MappingError
from scanner.sources.custom import dig

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>ERP Developer (Remote)</title>
    <link>https://ornek.test/is/1</link>
    <description>&lt;p&gt;Freelance ERP projesi&lt;/p&gt;</description>
    <pubDate>Tue, 12 Aug 2026 10:00:00 GMT</pubDate>
    <author>Ornek AS</author>
  </item>
  <item>
    <title>SAP Basis Consultant</title>
    <link>https://ornek.test/is/2</link>
    <description>Onsite pozisyon</description>
  </item>
</channel></rss>"""

JSON_BODY = {
    "meta": {"total": 2},
    "data": {"results": [
        {"baslik": "ERP Mimari", "yol": "https://ornek.test/2",
         "sirket": {"ad": "Acme"}, "yer": "Istanbul", "ucret": "$90/hour",
         "tarih": "2026-08-12", "aciklama": "Remote freelance ERP"},
        {"baslik": "", "yol": "https://ornek.test/3"},          # basliksiz: elenir
    ]},
}


class SahteYanit:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("JSON degil")
        return self._payload


class SahteIstemci:
    """Ag yok: sabit yanit doner, istenen adresleri kaydeder."""

    def __init__(self, response):
        self.response = response
        self.urls: list[str] = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.response

    def close(self):
        pass


def kaynak(options, response):
    client = SahteIstemci(response)
    return CustomSource(client, {"name": "deneme", **options}), client


# --- yol cozumleme ----------------------------------------------------
@pytest.mark.parametrize("path,beklenen", [
    ("data.results", [{"a": 1}]),
    ("data.results[0].a", 1),
    ("meta.total", 5),
    ("yok.olan", None),
    ("data.results[9]", None),
    ("", None),
])
def test_dig(path, beklenen):
    data = {"meta": {"total": 5}, "data": {"results": [{"a": 1}]}}
    assert dig(data, path) == beklenen


# --- RSS --------------------------------------------------------------
def test_rss_varsayilan_eslemeyle_okur():
    """RSS alan adlari standart: kullanicinin esleme yazmasi gerekmemeli."""
    source, _ = kaynak({"kind": "rss", "url": "https://ornek.test/feed"}, SahteYanit(text=RSS))
    projects = source.fetch("erp")

    assert [p.title for p in projects] == ["ERP Developer (Remote)", "SAP Basis Consultant"]
    ilk = projects[0]
    assert ilk.url == "https://ornek.test/is/1"
    assert ilk.company == "Ornek AS"
    assert ilk.description == "Freelance ERP projesi"      # HTML temizlendi
    assert ilk.posted_at is not None
    assert ilk.work_mode == "remote"                       # baslıktan cikarildi
    assert ilk.is_contract is True                         # "freelance" gecti
    assert ilk.source == "deneme"


def test_rss_yerine_html_gelirse_soyler():
    source, _ = kaynak({"kind": "rss", "url": "https://ornek.test/"},
                       SahteYanit(text="<html><body>giris sayfasi</body></html>"))
    with pytest.raises(MappingError, match="HTML"):
        source.fetch("erp")


# --- JSON -------------------------------------------------------------
def test_json_alan_eslemesiyle_okur():
    source, _ = kaynak({
        "kind": "json", "url": "https://ornek.test/api",
        "items_path": "data.results",
        "fields": {"title": "baslik", "url": "yol", "company": "sirket.ad",
                   "location": "yer", "budget_raw": "ucret", "posted_at": "tarih",
                   "description": "aciklama"},
    }, SahteYanit(payload=JSON_BODY))

    projects = source.fetch("erp")
    assert len(projects) == 1                     # basliksiz kayit elendi
    assert projects[0].company == "Acme"          # ic ice alan okundu
    assert projects[0].budget_raw == "$90/hour"


def test_json_yanlis_yolu_soyler():
    source, _ = kaynak({"kind": "json", "url": "https://ornek.test/api",
                        "items_path": "olmayan.yol", "fields": {"title": "t", "url": "u"}},
                       SahteYanit(payload=JSON_BODY))
    with pytest.raises(MappingError, match="Ilan listesi bulunamadi"):
        source.fetch("erp")


def test_json_liste_olmayan_yolu_soyler():
    source, _ = kaynak({"kind": "json", "url": "https://ornek.test/api",
                        "items_path": "meta", "fields": {"title": "t", "url": "u"}},
                       SahteYanit(payload=JSON_BODY))
    with pytest.raises(MappingError, match="ilan listesi degil"):
        source.fetch("erp")


def test_esleme_tutmazsa_mevcut_alanlari_soyler():
    """Sessizce 'sonuc yok' demek kullaniciyi kor birakiyordu."""
    source, _ = kaynak({"kind": "json", "url": "https://ornek.test/api",
                        "items_path": "data.results",
                        "fields": {"title": "yanlis_alan", "url": "yol"}},
                       SahteYanit(payload=JSON_BODY))
    with pytest.raises(MappingError) as hata:
        source.fetch("erp")
    mesaj = str(hata.value)
    assert "baslik+link okunamadi" in mesaj
    assert "baslik" in mesaj          # ornek kayittaki gercek alan adlari listelenmis


# --- adres kaliplari --------------------------------------------------
def test_sorgu_ve_sayfa_yer_tutuculari_doldurulur():
    source, client = kaynak({"kind": "rss", "pages": 3,
                             "url": "https://ornek.test/f?q={sorgu}&p={sayfa}"},
                            SahteYanit(text=RSS))
    source.fetch("erp yazilim")
    assert client.urls == ["https://ornek.test/f?q=erp yazilim&p=1",
                           "https://ornek.test/f?q=erp yazilim&p=2",
                           "https://ornek.test/f?q=erp yazilim&p=3"]


def test_sayfa_yer_tutucusu_yoksa_tek_istek():
    """Sayfa parametresi olmayan adrese 3 kez gitmek ayni veriyi tekrar cekerdi."""
    source, client = kaynak({"kind": "rss", "pages": 5, "url": "https://ornek.test/feed"},
                            SahteYanit(text=RSS))
    source.fetch("erp")
    assert client.urls == ["https://ornek.test/feed"]


def test_bos_adres_soylenir():
    source, _ = kaynak({"kind": "rss", "url": ""}, SahteYanit(text=RSS))
    with pytest.raises(MappingError, match="adresi bos"):
        source.fetch("erp")


def test_bilinmeyen_tur_soylenir():
    source, _ = kaynak({"kind": "html", "url": "https://ornek.test"}, SahteYanit(text=RSS))
    with pytest.raises(MappingError, match="Bilinmeyen kaynak turu"):
        source.fetch("erp")


# --- kayit defteri ----------------------------------------------------
def test_source_class_ozel_kaynagi_bulur():
    assert source_class("jooble") is not None
    assert source_class("yeni-pano", {"kind": "rss"}) is CustomSource
    assert source_class("yeni-pano", {}) is None          # tanimsiz kaynak


def test_is_custom():
    assert is_custom({"kind": "json"}) is True
    assert is_custom({"enabled": True}) is False
    assert is_custom(None) is False


def test_ozel_kaynak_anahtar_istemez():
    from scanner.pipeline import missing_credentials
    source, _ = kaynak({"kind": "rss", "url": "https://ornek.test"}, SahteYanit(text=RSS))
    assert missing_credentials(source) == []


def test_sorguya_duyarsiz_adres_bir_kez_cekilir():
    """Adreste {sorgu} yoksa her sorgu ayni sayfayi cekiyordu: 4 sorgu = 4 istek."""
    source, client = kaynak({"kind": "rss", "url": "https://ornek.test/feed"},
                            SahteYanit(text=RSS))
    ilk = source.fetch("sap")
    tekrar = source.fetch("erp")
    assert len(client.urls) == 1
    assert tekrar == ilk


def test_sorguya_duyarli_adres_her_sorguda_cekilir():
    source, client = kaynak({"kind": "rss", "url": "https://ornek.test/f?q={sorgu}"},
                            SahteYanit(text=RSS))
    source.fetch("sap")
    source.fetch("erp")
    assert client.urls == ["https://ornek.test/f?q=sap", "https://ornek.test/f?q=erp"]
