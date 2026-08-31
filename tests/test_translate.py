"""Yabanci dildeki ilan aciklamasinin Ingilizce'ye cevrilmesi.

Ag'a cikilmaz: MyMemory yaniti `httpx.get` yamanarak taklit edilir. Testin
korudugu sey ceviri kalitesi degil, cevresindeki kararlar - hangi metin
cevrilir, ne zaman istek ATILMAZ, servis bozuk cevap verirse ne olur.
"""
from __future__ import annotations

import pytest

from scanner import translate as ceviri_modulu
from scanner.translate import MAX_BYTES, _kirp, detect_language, translate

ALMANCA = ("Wir suchen einen erfahrenen SAP ABAP Entwickler fuer ein Projekt auf "
           "Projektbasis. Die Taetigkeit erfolgt ueberwiegend im Homeoffice und "
           "Kenntnisse in S/4HANA sind erforderlich.")
INGILIZCE = ("We are looking for an experienced SAP ABAP developer for a remote "
             "contract role. You will work with our team and should have strong "
             "knowledge of S/4HANA.")
HOLLANDACA = ("Wij zoeken een ervaren SAP ABAP ontwikkelaar voor een opdracht. De "
              "werkzaamheden zijn grotendeels thuiswerken en kennis van S/4HANA is "
              "een pre voor deze functie.")


class SahteYanit:
    def __init__(self, govde: dict, kod: int = 200):
        self._govde = govde
        self.status_code = kod

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._govde


def yamala(monkeypatch, govde: dict, kod: int = 200) -> list[dict]:
    """`httpx.get`i degistirir ve gonderilen parametreleri toplar."""
    istekler: list[dict] = []

    def sahte_get(url, params=None, timeout=None):
        istekler.append({"url": url, "params": params or {}})
        return SahteYanit(govde, kod)

    monkeypatch.setattr(ceviri_modulu.httpx, "get", sahte_get)
    return istekler


def basarili(metin: str) -> dict:
    return {"responseStatus": 200, "responseData": {"translatedText": metin}}


@pytest.mark.parametrize("metin, beklenen", [
    (ALMANCA, "de"),
    (INGILIZCE, "en"),
    (HOLLANDACA, "nl"),
    ("SAP ABAP", "en"),          # cok kisa: sayim guvenilmez, "en" varsayilir
    ("", "en"),
    (None, "en"),
])
def test_dil_tespiti(metin, beklenen):
    assert detect_language(metin) == beklenen


def test_ingilizce_metin_icin_istek_atilmaz(monkeypatch):
    """Zaten hedef dildeki ilan kotayi harcamamali."""
    istekler = yamala(monkeypatch, basarili("bir sey"))
    assert translate(INGILIZCE) is None
    assert istekler == []


def test_almanca_metin_cevrilir(monkeypatch):
    istekler = yamala(monkeypatch, basarili("We are looking for an SAP ABAP developer."))
    sonuc = translate(ALMANCA)
    assert sonuc == "We are looking for an SAP ABAP developer."
    assert istekler[0]["params"]["langpair"] == "de|en"


def test_eposta_verilmezse_parametreye_eklenmez(monkeypatch):
    """Adres yalnizca kullanici config'e yazdiysa servise gider."""
    istekler = yamala(monkeypatch, basarili("çeviri"))
    translate(ALMANCA)
    assert "de" not in istekler[0]["params"]

    istekler.clear()
    translate(ALMANCA, email="kisi@ornek.com")
    assert istekler[0]["params"]["de"] == "kisi@ornek.com"


def test_kota_dolunca_none_doner(monkeypatch):
    """MyMemory kota asiminda HTTP 200 + govdede 429 gonderiyor."""
    yamala(monkeypatch, {"responseStatus": 429, "responseDetails": "LIMIT REACHED",
                         "responseData": {"translatedText": "YOU USED ALL AVAILABLE FREE..."}})
    assert translate(ALMANCA) is None


def test_ag_hatasi_yutulur(monkeypatch):
    def patlayan_get(url, params=None, timeout=None):
        raise RuntimeError("baglanti koptu")

    monkeypatch.setattr(ceviri_modulu.httpx, "get", patlayan_get)
    assert translate(ALMANCA) is None          # cagiran orijinali gosterir


def test_ceviri_kaynakla_ayniysa_yok_sayilir(monkeypatch):
    """Servis bazen metni oldugu gibi geri veriyor; bu ceviri sayilmaz."""
    yamala(monkeypatch, basarili(ALMANCA))
    assert translate(ALMANCA) is None


def test_bos_metin(monkeypatch):
    istekler = yamala(monkeypatch, basarili("x"))
    assert translate("") is None
    assert translate("   ") is None
    assert istekler == []


@pytest.mark.parametrize("metin", [
    "Wort " * 300,                    # duz ascii
    "Tätigkeit über Grüße " * 50,     # cok baytli karakterler
    "a" * MAX_BYTES,                  # tam sinirda
    "a" * (MAX_BYTES + 1),            # bir bayt tasan
])
def test_kirpma_bayt_sinirini_asmaz(metin):
    """MAX_BYTES asilirsa MyMemory 414 doner - uc nokta dahil sigmali."""
    assert len(_kirp(metin).encode("utf-8")) <= MAX_BYTES


def test_kirpma_kelime_ortasindan_bolmez():
    kesik = _kirp("kelime " * 200)
    assert kesik.endswith("…")
    assert "kelim…" not in kesik      # yarim kelime birakmamali


def test_uzun_metin_kirpilmis_gonderilir(monkeypatch):
    istekler = yamala(monkeypatch, basarili("translated"))
    translate("Wir suchen und der die das " * 100)
    gonderilen = istekler[0]["params"]["q"]
    assert len(gonderilen.encode("utf-8")) <= MAX_BYTES
