"""Ilan aciklamasini yapay zeka ile ozetler ve olmazsa olmaz kosullari cikarir.

Neden gerekli: aciklamalar tum kaynaklarda `strip_html` + `clean_text` ile TEK
SATIRA duzlestiriliyor (normalize.py) - "Requirements:" basligi metin olarak
kalsa bile madde sinirlari kayboluyor. Regex ile guvenilir madde cikarilamiyor;
ustelik ilanlarin bir kismi Almanca/Hollandaca/Fransizca.

Servis Google Gemini (Flash-Lite): bu is (kisa ozet + madde cikarma) icin en
ucuz siniftaki model yetiyor ve ceviriyi de kendisi yapiyor - Almanca ilan
dogrudan Ingilizce ozetleniyor, ayrica translate.py'ye gerek kalmiyor.

Token disiplini bilincli:
  - Yalnizca kullanici ilanin ustune geldiginde cagrilir (tarama sirasinda degil).
  - Sonuc veritabanina yazilir, ayni ilan ikinci kez ozetlenmez (storage.py >
    save_ai_summary).
  - Ayarlardaki `ai.min_score` esiginin altindaki ilan hic gonderilmez.

`google-generativeai` SDK'si KURULMUYOR: tek bir REST cagrisi icin paket eklemek
Docker imajini ve bagimlilik yuzeyini bosuna buyuturdu. httpx zaten var.
"""
from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger("scanner.ai")

UC_NOKTA = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: Ilan aciklamasinin AI'ya gonderilen en fazla karakteri. Girdi token'ini
#: sinirlar; olculdu: 6000 karakterlik ilan ~1.340 token girdi uretiyor.
MAX_KARAKTER = 6000

VARSAYILAN_MODEL = "gemini-3.5-flash-lite"

#: Model basina yaklasik ucret - 1 MILYON token icin USD (girdi, cikti).
#: Kaynak: Google Gemini API fiyat listesi. Google fiyati degistirirse ya da
#: burada olmayan bir model kullanilirsa `VARSAYILAN_FIYAT` devreye girer.
#: Maliyet ekrani (Ayarlar > Yapay zeka > Maliyet) bu tabloyu kullanir.
#:
#: Yalnizca guncel nesil (Gemini 2.5 ve 3.x) tutuluyor; 2.0 ve altindaki
#: modeller listeden cikarildi. 3.x fiyatlari Google resmi liste yayinlayana
#: kadar bir onceki nesle (2.5) gore tahminidir - siralamada YENIDEN ESKIYE.
FIYATLAR: dict[str, tuple[float, float]] = {
    "gemini-3.6-flash": (0.30, 2.50),
    "gemini-3.6-flash-lite": (0.10, 0.40),
    "gemini-3.5-pro": (1.25, 10.00),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-3-pro": (1.25, 10.00),
    "gemini-3-flash": (0.30, 2.50),
    "gemini-3-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}
VARSAYILAN_FIYAT: tuple[float, float] = (0.10, 0.40)


def _model_kok(model: str) -> str:
    """'models/gemini-3.5-flash-lite-preview-06' -> 'gemini-3.5-flash-lite'.

    En uzun eslesen anahtar kazanir: 'gemini-3.5-flash-lite', 'gemini-3.5-flash'
    on ekiyle de baslar, once uzun olan denenmezse yanlis fiyat secilir.
    """
    ad = (model or "").strip().lower().removeprefix("models/")
    for bilinen in sorted(FIYATLAR, key=len, reverse=True):
        if ad == bilinen or ad.startswith(bilinen + "-"):
            return bilinen
    return ad


def fiyat(model: str) -> tuple[float, float]:
    """Modelin (girdi, cikti) 1M token USD ucreti; bilinmiyorsa varsayilan."""
    return FIYATLAR.get(_model_kok(model), VARSAYILAN_FIYAT)


def maliyet_usd(model: str, girdi_token: int, cikti_token: int) -> float:
    """Bir cagrinin USD maliyeti."""
    giris, cikis = fiyat(model)
    return (girdi_token or 0) / 1_000_000 * giris + (cikti_token or 0) / 1_000_000 * cikis

#: Cikti sekli sunucu tarafinda garanti altina aliniyor (responseSchema), boylece
#: serbest metinden madde ayiklamaya calismak gerekmiyor.
SEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "must_haves": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "must_haves"],
}

#: "Always answer in ENGLISH" tek basina YETMIYOR: model ozeti Ingilizce yazip
#: must_haves maddelerini ilanin dilinden ALINTILIYORDU ("Mehrjahrige Erfahrung
#: in ABAP und ABAP OO"). Ceviri sarti her iki alan icin ayri ayri ve "alintilama"
#: yasagiyla birlikte yazilmali.
TALIMAT = (
    "You summarize job listings for a freelance SAP consultant.\n"
    "\n"
    "LANGUAGE RULE - applies to EVERY field you output:\n"
    "Write everything in ENGLISH. Listings are often in German, Dutch, French or "
    "Turkish. TRANSLATE their wording into English - never copy or quote a phrase "
    "in the original language. For example 'Mehrjahrige Erfahrung in ABAP' must "
    "become 'Several years of ABAP experience'. Proper nouns and product names "
    "(SAP, ABAP, S/4HANA, Fiori) stay as they are.\n"
    "\n"
    "- summary: 2-3 sentences in English, what the job is and the working "
    "arrangement.\n"
    "- must_haves: the NON-NEGOTIABLE requirements only, each written in English "
    "(what the listing calls required/must/mandatory/erforderlich/vereist). "
    "Short phrases, max 6. If the listing states none explicitly, infer the hard "
    "technical prerequisites."
)


def ozetle(baslik: str, aciklama: str, *, anahtar: str,
           model: str = VARSAYILAN_MODEL, zaman_asimi: float = 30.0) -> dict | None:
    """Ilani ozetler. Basarisiz olursa None doner - cagiran eski davranisa doner.

    Donen sozluk: {"summary": str, "must_haves": list[str],
                   "prompt_tokens": int, "output_tokens": int}
    Token sayilari maliyet ekrani icin - Gemini `usageMetadata` alanindan gelir,
    eksikse 0 kalir.

    Hicbir hata yukari firlatilmaz: ozet kozmetik bir zenginlestirme, panelin
    calismasi buna bagli degil. Anahtar yanlis, kota dolmus ya da ag kopmus
    olabilir - hepsinde ilan ham haliyle gosterilmeye devam eder.
    """
    aciklama = (aciklama or "").strip()
    if not anahtar or not aciklama:
        return None

    govde = {
        "systemInstruction": {"parts": [{"text": TALIMAT}]},
        "contents": [{"parts": [{
            "text": f"TITLE: {baslik or ''}\n\nDESCRIPTION:\n{aciklama[:MAX_KARAKTER]}"
        }]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SEMA,
            "temperature": 0.2,          # ayni ilan icin tutarli cikti
            "maxOutputTokens": 8000,
        },
    }

    try:
        yanit = httpx.post(UC_NOKTA.format(model=model), params={"key": anahtar},
                           json=govde, timeout=zaman_asimi)
        yanit.raise_for_status()
        veri = yanit.json()
    except Exception as hata:                # noqa: BLE001 - ozet kritik degil
        log.info("ai ozet basarisiz (%s): %s", model, hata)
        return None

    try:
        metin = veri["candidates"][0]["content"]["parts"][0]["text"]
        icerik = json.loads(metin)
    except (KeyError, IndexError, TypeError, ValueError) as hata:
        # Guvenlik filtresi devreye girmis ya da cikti kesilmis olabilir.
        log.info("ai yaniti okunamadi (%s): %s", model, hata)
        return None

    ozet = str(icerik.get("summary") or "").strip()
    kosullar = [str(k).strip() for k in (icerik.get("must_haves") or []) if str(k).strip()]
    if not ozet:
        return None

    kullanim = veri.get("usageMetadata") or {}
    girdi = int(kullanim.get("promptTokenCount") or 0)
    # `thoughtsTokenCount` (varsa) cikti gibi ucretlendirilir.
    cikti = int(kullanim.get("candidatesTokenCount") or 0) + \
        int(kullanim.get("thoughtsTokenCount") or 0)
    return {"summary": ozet, "must_haves": kosullar,
            "prompt_tokens": girdi, "output_tokens": cikti}


def ayarlar(config: dict) -> tuple[bool, str, int, float]:
    """config.yaml > `ai` bolumunu (acik mi, model, esik, zaman asimi) okur."""
    bolum = (config or {}).get("ai") or {}
    return (
        bool(bolum.get("enabled", True)),
        str(bolum.get("model") or VARSAYILAN_MODEL),
        int(bolum.get("min_score", 30) or 0),
        float(bolum.get("timeout", 30) or 30),
    )
