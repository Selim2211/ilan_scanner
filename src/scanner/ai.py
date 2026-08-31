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

    Donen sozluk: {"summary": str, "must_haves": list[str]}

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
    return {"summary": ozet, "must_haves": kosullar}


def ayarlar(config: dict) -> tuple[bool, str, int, float]:
    """config.yaml > `ai` bolumunu (acik mi, model, esik, zaman asimi) okur."""
    bolum = (config or {}).get("ai") or {}
    return (
        bool(bolum.get("enabled", True)),
        str(bolum.get("model") or VARSAYILAN_MODEL),
        int(bolum.get("min_score", 30) or 0),
        float(bolum.get("timeout", 30) or 30),
    )
