"""Yapay zeka ozetleyici (Gemini).

Aga cikilmaz: `httpx.post` yamanir. Testin korudugu sey ozet kalitesi degil -
cevresindeki sozlesme: ne gonderiliyor, bozuk cevapta ne oluyor, hata panele
sizyor mu. `ozetle` HICBIR durumda istisna firlatmamali; ozet kozmetik bir
zenginlestirme, panelin calismasi ona bagli degil.
"""
from __future__ import annotations

import json

import pytest

from scanner import ai as ai_modulu
from scanner.ai import MAX_KARAKTER, ayarlar, ozetle

ANAHTAR = "test-anahtari"


class SahteYanit:
    def __init__(self, govde, kod: int = 200):
        self._govde = govde
        self.status_code = kod

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._govde


def yamala(monkeypatch, govde, kod: int = 200) -> list[dict]:
    """`httpx.post`u degistirir ve gonderilen istekleri toplar."""
    istekler: list[dict] = []

    def sahte_post(url, params=None, json=None, timeout=None):
        istekler.append({"url": url, "params": params or {}, "govde": json or {},
                         "timeout": timeout})
        return SahteYanit(govde, kod)

    monkeypatch.setattr(ai_modulu.httpx, "post", sahte_post)
    return istekler


def gemini_yaniti(icerik: dict) -> dict:
    """Gemini'nin gercek yanit sekli: metin JSON string olarak gomulu gelir."""
    return {"candidates": [{"content": {"parts": [
        {"text": json.dumps(icerik, ensure_ascii=False)}]}}]}


BASARILI = gemini_yaniti({
    "summary": "Freelance SAP ABAP role, fully remote, 6 month contract.",
    "must_haves": ["5+ years ABAP", "S/4HANA experience", "Fluent English"],
})


def test_basarili_ozet(monkeypatch):
    yamala(monkeypatch, BASARILI)
    sonuc = ozetle("SAP ABAP Developer", "Uzun ilan aciklamasi", anahtar=ANAHTAR)
    assert sonuc["summary"].startswith("Freelance SAP ABAP")
    assert sonuc["must_haves"] == ["5+ years ABAP", "S/4HANA experience", "Fluent English"]


def test_istek_sekli(monkeypatch):
    """Yapilandirilmis cikti sunucu tarafinda garanti altina alinmali."""
    istekler = yamala(monkeypatch, BASARILI)
    ozetle("Baslik", "Aciklama", anahtar=ANAHTAR, model="test-model")

    istek = istekler[0]
    assert "test-model:generateContent" in istek["url"]
    assert istek["params"]["key"] == ANAHTAR       # anahtar URL'de degil params'ta
    ayar = istek["govde"]["generationConfig"]
    assert ayar["responseMimeType"] == "application/json"
    assert ayar["responseSchema"]["required"] == ["summary", "must_haves"]


def test_anahtarsiz_istek_atilmaz(monkeypatch):
    istekler = yamala(monkeypatch, BASARILI)
    assert ozetle("Baslik", "Aciklama", anahtar="") is None
    assert istekler == []


def test_bos_aciklama_istek_atilmaz(monkeypatch):
    istekler = yamala(monkeypatch, BASARILI)
    assert ozetle("Baslik", "", anahtar=ANAHTAR) is None
    assert ozetle("Baslik", "   ", anahtar=ANAHTAR) is None
    assert istekler == []


def test_uzun_aciklama_kirpilir(monkeypatch):
    """Girdi token'i sinirli kalsin: cok uzun ilan oldugu gibi gonderilmemeli."""
    istekler = yamala(monkeypatch, BASARILI)
    ozetle("Baslik", "x" * (MAX_KARAKTER * 3), anahtar=ANAHTAR)

    gonderilen = istekler[0]["govde"]["contents"][0]["parts"][0]["text"]
    assert len(gonderilen) < MAX_KARAKTER + 200      # baslik + etiketler payi


@pytest.mark.parametrize("govde, kod", [
    ({}, 500),                                            # sunucu hatasi
    ({"error": {"message": "API key not valid"}}, 400),   # yanlis anahtar
    ({}, 429),                                            # kota doldu
])
def test_http_hatasi_none_doner(monkeypatch, govde, kod):
    yamala(monkeypatch, govde, kod)
    assert ozetle("Baslik", "Aciklama", anahtar=ANAHTAR) is None


def test_ag_hatasi_yutulur(monkeypatch):
    def patlayan_post(url, params=None, json=None, timeout=None):
        raise RuntimeError("baglanti koptu")

    monkeypatch.setattr(ai_modulu.httpx, "post", patlayan_post)
    assert ozetle("Baslik", "Aciklama", anahtar=ANAHTAR) is None


@pytest.mark.parametrize("govde", [
    {},                                                        # candidates yok
    {"candidates": []},                                        # bos liste
    {"candidates": [{"finishReason": "SAFETY"}]},              # guvenlik filtresi
    {"candidates": [{"content": {"parts": [{"text": "{bozuk"}]}}]},   # gecersiz JSON
])
def test_bozuk_yanit_none_doner(monkeypatch, govde):
    yamala(monkeypatch, govde)
    assert ozetle("Baslik", "Aciklama", anahtar=ANAHTAR) is None


def test_ozet_bossa_none_doner(monkeypatch):
    """Yalnizca kosul listesi gelmis, ozet yok: gosterecek bir sey yok."""
    yamala(monkeypatch, gemini_yaniti({"summary": "  ", "must_haves": ["a"]}))
    assert ozetle("Baslik", "Aciklama", anahtar=ANAHTAR) is None


def test_kosullar_temizlenir(monkeypatch):
    """Bos/bosluk maddeler elenmeli, madde yoksa liste bos kalmali - hata degil."""
    yamala(monkeypatch, gemini_yaniti({"summary": "Ozet", "must_haves": ["a", "", "  ", "b"]}))
    assert ozetle("Baslik", "Aciklama", anahtar=ANAHTAR)["must_haves"] == ["a", "b"]

    yamala(monkeypatch, gemini_yaniti({"summary": "Ozet", "must_haves": []}))
    sonuc = ozetle("Baslik", "Aciklama", anahtar=ANAHTAR)
    assert sonuc["summary"] == "Ozet"
    assert sonuc["must_haves"] == []


def test_ayarlar_okuma():
    acik, model, esik, zaman = ayarlar({"ai": {"enabled": False, "model": "m",
                                               "min_score": 45, "timeout": 12}})
    assert (acik, model, esik, zaman) == (False, "m", 45, 12.0)


def test_ayarlar_varsayilanlari():
    """Config'te `ai` bolumu hic yoksa panel yine calismali."""
    acik, model, esik, zaman = ayarlar({})
    assert acik is True and model == ai_modulu.VARSAYILAN_MODEL
    assert esik == 30 and zaman == 30.0

def test_talimat_her_alan_icin_ingilizce_sart_kosar():
    """Regresyon: ozet Ingilizce geliyordu ama must_haves ilanin dilinden
    ALINTILANIYORDU ("Mehrjahrige Erfahrung in ABAP und ABAP OO"). Talimat
    ceviriyi acikca istemeli ve alintilamayi yasaklamali."""
    from scanner.ai import TALIMAT

    metin = TALIMAT.lower()
    assert "translate" in metin                       # cevir demeli
    assert "never copy" in metin or "not copy" in metin   # alintilamayi yasaklamali
    # dil kurali must_haves icin de acikca tekrarlanmali
    assert metin.count("english") >= 3
