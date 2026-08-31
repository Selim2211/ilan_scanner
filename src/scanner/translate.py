"""Yabanci dildeki ilan aciklamasini Ingilizce'ye cevirir.

Jooble-DE ve Careerjet (de_DE, nl_NL, fr_FR...) Almanca/Hollandaca ilan
donduruyor; panelde ustune gelince cikan kutu bunlari Ingilizce gostersin diye.

Servis MyMemory: anahtar istemiyor, kayit istemiyor - kurulum adimi eklemeden
calisan tek secenek. Bedeli kota: anonim IP basina gunluk ~5000 kelime. Bu
yuzden ceviri YALNIZCA kullanici ilanin ustune geldiginde yapilir ve sonuc
veritabanina yazilir (storage.summary_en); ayni ilan iki kez cevrilmez.

Dil tespiti kucuk bir stopword sayimi - `langdetect` gibi bir bagimlilik
eklemeye degmez, burada ayirt edilmesi gereken birkac Avrupa dili var ve metin
zaten ilan aciklamasi (uzun ve duz yazi).
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("scanner.translate")

#: MyMemory tek istekte en fazla 500 BAYT metin alir; ustu 414 doner.
#: Kutuda zaten kisa ozet gosteriliyor, 480'de kesmek guvenli sinir.
MAX_BYTES = 480

TIMEOUT = 8.0

#: Dil basina ayirt edici kelimeler. Yalnizca o dile ozgu, kisa ve sik gecenler
#: secildi; "is/in/die" gibi birden fazla dilde bulunanlar bilerek disarida.
STOPWORDS: dict[str, set[str]] = {
    "de": {"und", "der", "die", "das", "für", "mit", "sie", "wir", "sind", "eine",
           "einen", "nicht", "auch", "bei", "von", "sich", "werden", "haben",
           "unser", "ihre", "als", "aus", "durch", "kenntnisse", "erfahrung",
           "aufgaben", "wird", "über", "sowie", "zum", "zur", "beim"},
    "nl": {"een", "het", "van", "voor", "met", "zijn", "wij", "onze", "niet",
           "worden", "wordt", "bij", "aan", "ook", "naar", "kennis", "ervaring",
           "werkzaamheden", "binnen", "over", "deze"},
    "fr": {"pour", "avec", "vous", "nous", "les", "des", "une", "dans", "sur",
           "est", "sont", "votre", "notre", "aux", "par", "ses", "expérience",
           "compétences", "mission", "sera"},
    "es": {"para", "con", "los", "las", "una", "por", "que", "del", "sus",
           "experiencia", "conocimientos", "empresa", "trabajo", "sobre"},
    "it": {"per", "con", "una", "del", "della", "sono", "nel", "gli", "esperienza",
           "conoscenza", "azienda", "lavoro", "sarà"},
    "tr": {"ve", "ile", "için", "olan", "bir", "bu", "olarak", "veya", "deneyim",
           "bilgisi", "gerekli", "tercihen", "yapabilecek", "sahip"},
    "en": {"the", "and", "for", "with", "you", "our", "are", "will", "your",
           "this", "that", "have", "experience", "knowledge", "skills",
           "team", "role", "should", "must", "who", "work"},
}


def detect_language(text: str | None) -> str:
    """Metnin dilini tahmin eder; ayirt edemezse "en" doner.

    "en" varsayilan cunku ceviri yalnizca dil Ingilizce DEGILSE yapiliyor:
    emin olamadigimizda cevirmemek, yanlis dil ciftiyle kota harcamaktan iyi.
    """
    if not text:
        return "en"

    kelimeler = [k.strip(".,;:!?()[]{}\"'-/") for k in text.lower().split()]
    kelimeler = [k for k in kelimeler if k]
    if len(kelimeler) < 8:                    # cok kisa metinde sayim guvenilmez
        return "en"

    havuz = set(kelimeler)
    puanlar = {dil: len(havuz & sozcukler) for dil, sozcukler in STOPWORDS.items()}
    en_iyi = max(puanlar, key=lambda dil: puanlar[dil])

    # En az iki isabet ve Ingilizce'yi gecmis olmali: tek kelimelik tesaduf
    # ("der" bir sirket adinda da gecebilir) ceviri baslatmasin.
    if puanlar[en_iyi] < 2 or puanlar[en_iyi] <= puanlar["en"]:
        return "en"
    return en_iyi


def _kirp(text: str, limit: int = MAX_BYTES) -> str:
    """Metni UTF-8 bayt sinirinda, kelime ortasindan bolmeden keser.

    Sonuc "…" dahil `limit`i asmaz: uc nokta 3 bayt tuttugu icin kesme payi
    onceden dusuluyor, yoksa tam sinirdaki metin sinirin uzerine tasiyordu.
    """
    ham = text.strip()
    if len(ham.encode("utf-8")) <= limit:
        return ham
    pay = limit - len("…".encode("utf-8"))
    kesik = ham.encode("utf-8")[:pay].decode("utf-8", errors="ignore")
    bosluk = kesik.rfind(" ")
    return (kesik[:bosluk] if bosluk > pay // 2 else kesik).rstrip() + "…"


def translate(text: str | None, source: str = "", target: str = "en",
              email: str = "") -> str | None:
    """Metni hedef dile cevirir. Ceviremezse None doner (cagiran orijinali gosterir).

    `email` doluysa MyMemory gunluk kotayi ~5000 kelimeden 50000'e cikariyor.
    Bos birakilabilir; adres istege bagli, Ayarlar > Panel'den girilir.
    """
    if not text or not text.strip():
        return None

    kaynak = source or detect_language(text)
    if kaynak == target:
        return None                            # zaten hedef dilde, istek atma

    parametreler = {"q": _kirp(text), "langpair": f"{kaynak}|{target}"}
    if email:
        parametreler["de"] = email

    try:
        cevap = httpx.get("https://api.mymemory.translated.net/get",
                          params=parametreler, timeout=TIMEOUT)
        cevap.raise_for_status()
        veri = cevap.json()
    except Exception as hata:                  # noqa: BLE001 - ceviri kritik degil
        log.info("ceviri basarisiz (%s -> %s): %s", kaynak, target, hata)
        return None

    # responseStatus govdede geliyor: kota dolunca HTTP 200 + 429 gonderiyor.
    durum = veri.get("responseStatus")
    if str(durum) != "200":
        log.info("ceviri reddedildi (%s): %s", durum, veri.get("responseDetails"))
        return None

    sonuc = ((veri.get("responseData") or {}).get("translatedText") or "").strip()
    if not sonuc or sonuc.upper() == _kirp(text).upper():
        return None                            # ceviri yok sayilir
    return sonuc
