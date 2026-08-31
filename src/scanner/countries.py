"""Ham lokasyon metnini kanonik ulke koduna (ISO2) cevirir.

Kaynak adaptorleri `country` kolonuna ne bulursa yaziyor: jooble virgul yoksa
tum location'i ulke sayiyor ("London", "Remote", "Berlin"), virgul varsa son
parcayi ("TX", "CA"); careerjet locale diline gore "Deutschland" VE "Germany"
uretiyor. Bu yuzden panelde ulke filtresi calismiyordu - burasi o dagiligi tek
bir ISO2 koduna indirger, filtre de tam eslesme yapar.
"""
from __future__ import annotations

import re
import unicodedata

from .normalize import fold

#: ISO2 -> (Turkce ad, bolge). Bolge yalnizca listeyi gruplamak icin.
COUNTRIES: dict[str, tuple[str, str]] = {
    # --- Avrupa ---
    "DE": ("Almanya", "Avrupa"),
    "AT": ("Avusturya", "Avrupa"),
    "CH": ("İsviçre", "Avrupa"),
    "NL": ("Hollanda", "Avrupa"),
    "BE": ("Belçika", "Avrupa"),
    "LU": ("Lüksemburg", "Avrupa"),
    "GB": ("Birleşik Krallık", "Avrupa"),
    "IE": ("İrlanda", "Avrupa"),
    "FR": ("Fransa", "Avrupa"),
    "ES": ("İspanya", "Avrupa"),
    "PT": ("Portekiz", "Avrupa"),
    "IT": ("İtalya", "Avrupa"),
    "GR": ("Yunanistan", "Avrupa"),
    "DK": ("Danimarka", "Avrupa"),
    "SE": ("İsveç", "Avrupa"),
    "NO": ("Norveç", "Avrupa"),
    "FI": ("Finlandiya", "Avrupa"),
    "IS": ("İzlanda", "Avrupa"),
    "PL": ("Polonya", "Avrupa"),
    "CZ": ("Çekya", "Avrupa"),
    "SK": ("Slovakya", "Avrupa"),
    "HU": ("Macaristan", "Avrupa"),
    "RO": ("Romanya", "Avrupa"),
    "BG": ("Bulgaristan", "Avrupa"),
    "HR": ("Hırvatistan", "Avrupa"),
    "SI": ("Slovenya", "Avrupa"),
    "RS": ("Sırbistan", "Avrupa"),
    "BA": ("Bosna-Hersek", "Avrupa"),
    "MK": ("Kuzey Makedonya", "Avrupa"),
    "AL": ("Arnavutluk", "Avrupa"),
    "ME": ("Karadağ", "Avrupa"),
    "EE": ("Estonya", "Avrupa"),
    "LV": ("Letonya", "Avrupa"),
    "LT": ("Litvanya", "Avrupa"),
    "UA": ("Ukrayna", "Avrupa"),
    "MD": ("Moldova", "Avrupa"),
    "BY": ("Belarus", "Avrupa"),
    "RU": ("Rusya", "Avrupa"),
    "MT": ("Malta", "Avrupa"),
    "CY": ("Kıbrıs", "Avrupa"),
    # --- Kuzey Amerika ---
    "US": ("Amerika Birleşik Devletleri", "Kuzey Amerika"),
    "CA": ("Kanada", "Kuzey Amerika"),
    "MX": ("Meksika", "Kuzey Amerika"),
    "CR": ("Kosta Rika", "Kuzey Amerika"),
    "PA": ("Panama", "Kuzey Amerika"),
    # --- Guney Amerika ---
    "BR": ("Brezilya", "Güney Amerika"),
    "AR": ("Arjantin", "Güney Amerika"),
    "CL": ("Şili", "Güney Amerika"),
    "CO": ("Kolombiya", "Güney Amerika"),
    "PE": ("Peru", "Güney Amerika"),
    "UY": ("Uruguay", "Güney Amerika"),
    "EC": ("Ekvador", "Güney Amerika"),
    # --- Orta Dogu ---
    "TR": ("Türkiye", "Orta Doğu"),
    "AE": ("Birleşik Arap Emirlikleri", "Orta Doğu"),
    "SA": ("Suudi Arabistan", "Orta Doğu"),
    "QA": ("Katar", "Orta Doğu"),
    "KW": ("Kuveyt", "Orta Doğu"),
    "BH": ("Bahreyn", "Orta Doğu"),
    "OM": ("Umman", "Orta Doğu"),
    "IL": ("İsrail", "Orta Doğu"),
    "JO": ("Ürdün", "Orta Doğu"),
    "LB": ("Lübnan", "Orta Doğu"),
    "IQ": ("Irak", "Orta Doğu"),
    "IR": ("İran", "Orta Doğu"),
    # --- Asya ---
    "IN": ("Hindistan", "Asya"),
    "PK": ("Pakistan", "Asya"),
    "BD": ("Bangladeş", "Asya"),
    "LK": ("Sri Lanka", "Asya"),
    "CN": ("Çin", "Asya"),
    "HK": ("Hong Kong", "Asya"),
    "TW": ("Tayvan", "Asya"),
    "JP": ("Japonya", "Asya"),
    "KR": ("Güney Kore", "Asya"),
    "SG": ("Singapur", "Asya"),
    "MY": ("Malezya", "Asya"),
    "TH": ("Tayland", "Asya"),
    "VN": ("Vietnam", "Asya"),
    "ID": ("Endonezya", "Asya"),
    "PH": ("Filipinler", "Asya"),
    "KZ": ("Kazakistan", "Asya"),
    "AZ": ("Azerbaycan", "Asya"),
    "GE": ("Gürcistan", "Asya"),
    "AM": ("Ermenistan", "Asya"),
    # --- Afrika ---
    "EG": ("Mısır", "Afrika"),
    "MA": ("Fas", "Afrika"),
    "TN": ("Tunus", "Afrika"),
    "DZ": ("Cezayir", "Afrika"),
    "ZA": ("Güney Afrika", "Afrika"),
    "NG": ("Nijerya", "Afrika"),
    "KE": ("Kenya", "Afrika"),
    "GH": ("Gana", "Afrika"),
    "ET": ("Etiyopya", "Afrika"),
    # --- Okyanusya ---
    "AU": ("Avustralya", "Okyanusya"),
    "NZ": ("Yeni Zelanda", "Okyanusya"),
}

#: Ek adlar: Ingilizce, yerel dil, ISO3. Anahtarlar _key() ile sadelestirilir.
_NAMES: dict[str, tuple[str, ...]] = {
    "DE": ("germany", "deutschland", "allemagne", "duitsland", "germania", "alemania", "deu", "brd"),
    "AT": ("austria", "osterreich", "autriche", "oostenrijk", "aut"),
    "CH": ("switzerland", "schweiz", "suisse", "svizzera", "zwitserland", "che"),
    "NL": ("netherlands", "the netherlands", "nederland", "niederlande", "holland", "pays bas", "nld"),
    "BE": ("belgium", "belgie", "belgien", "belgique", "bel"),
    "LU": ("luxembourg", "luxemburg", "lux"),
    "GB": ("united kingdom", "uk", "great britain", "britain", "england", "scotland",
           "wales", "northern ireland", "grossbritannien", "vereinigtes konigreich",
           "royaume uni", "greater london", "gbr"),
    "IE": ("ireland", "republic of ireland", "irland", "eire", "irl"),
    "FR": ("france", "frankreich", "frankrijk", "francia", "fra"),
    "ES": ("spain", "espana", "spanien", "espagne", "esp"),
    "PT": ("portugal", "prt"),
    "IT": ("italy", "italia", "italien", "italie", "ita"),
    "GR": ("greece", "hellas", "griechenland", "grece", "grc"),
    "DK": ("denmark", "danmark", "danemark", "dnk"),
    "SE": ("sweden", "sverige", "schweden", "suede", "swe"),
    "NO": ("norway", "norge", "norwegen", "norvege", "nor"),
    "FI": ("finland", "suomi", "finnland", "finlande", "fin"),
    "IS": ("iceland", "island", "isl"),
    "PL": ("poland", "polska", "polen", "pologne", "pol"),
    "CZ": ("czechia", "czech republic", "cesko", "ceska republika", "tschechien", "cze"),
    "SK": ("slovakia", "slovensko", "slowakei", "svk"),
    "HU": ("hungary", "magyarorszag", "ungarn", "hongrie", "hun"),
    "RO": ("romania", "rumanien", "roumanie", "rou"),
    "BG": ("bulgaria", "bulgarien", "bulgarie", "bgr"),
    "HR": ("croatia", "hrvatska", "kroatien", "hrv"),
    "SI": ("slovenia", "slovenija", "slowenien", "svn"),
    "RS": ("serbia", "srbija", "serbien", "srb"),
    "BA": ("bosnia and herzegovina", "bosnia", "bosna i hercegovina", "bih"),
    "MK": ("north macedonia", "macedonia", "makedonija", "mkd"),
    "AL": ("albania", "shqiperia", "albanien", "alb"),
    "ME": ("montenegro", "crna gora", "mne"),
    "EE": ("estonia", "eesti", "estland", "est"),
    "LV": ("latvia", "latvija", "lettland", "lva"),
    "LT": ("lithuania", "lietuva", "litauen", "ltu"),
    "UA": ("ukraine", "ukrayina", "ukr"),
    "MD": ("moldova", "republic of moldova", "mda"),
    "BY": ("belarus", "weissrussland", "blr"),
    "RU": ("russia", "russian federation", "russland", "rossiya", "rus"),
    "MT": ("malta", "mlt"),
    "CY": ("cyprus", "kibris", "zypern", "cyp"),
    "US": ("united states", "united states of america", "usa", "america",
           "vereinigte staaten", "etats unis", "us"),
    "CA": ("canada", "kanada", "can"),
    "MX": ("mexico", "mexiko", "mex"),
    "CR": ("costa rica", "cri"),
    "PA": ("panama", "pan"),
    "BR": ("brazil", "brasil", "brasilien", "bra"),
    "AR": ("argentina", "argentinien", "arg"),
    "CL": ("chile", "chl"),
    "CO": ("colombia", "kolumbien", "col"),
    "PE": ("peru", "per"),
    "UY": ("uruguay", "ury"),
    "EC": ("ecuador", "ecu"),
    "TR": ("turkey", "turkiye", "turkei", "turquie", "tur"),
    "AE": ("united arab emirates", "uae", "emirates", "dubai", "abu dhabi", "are"),
    "SA": ("saudi arabia", "ksa", "riyadh", "sau"),
    "QA": ("qatar", "doha", "qat"),
    "KW": ("kuwait", "kwt"),
    "BH": ("bahrain", "bhr"),
    "OM": ("oman", "omn"),
    "IL": ("israel", "isr"),
    "JO": ("jordan", "jor"),
    "LB": ("lebanon", "lbn"),
    "IQ": ("iraq", "irq"),
    "IR": ("iran", "irn"),
    "IN": ("india", "indien", "ind"),
    "PK": ("pakistan", "pak"),
    "BD": ("bangladesh", "bgd"),
    "LK": ("sri lanka", "lka"),
    "CN": ("china", "prc", "chn"),
    "HK": ("hong kong", "hkg"),
    "TW": ("taiwan", "twn"),
    "JP": ("japan", "nippon", "jpn"),
    "KR": ("south korea", "korea", "republic of korea", "kor"),
    "SG": ("singapore", "singapur", "sgp"),
    "MY": ("malaysia", "mys"),
    "TH": ("thailand", "tha"),
    "VN": ("vietnam", "viet nam", "vnm"),
    "ID": ("indonesia", "idn"),
    "PH": ("philippines", "phl"),
    "KZ": ("kazakhstan", "kaz"),
    "AZ": ("azerbaijan", "azerbaycan", "aze"),
    "GE": ("georgia", "sakartvelo", "geo"),
    "AM": ("armenia", "arm"),
    "EG": ("egypt", "agypten", "egy"),
    "MA": ("morocco", "maroc", "mar"),
    "TN": ("tunisia", "tunisie", "tun"),
    "DZ": ("algeria", "algerie", "dza"),
    "ZA": ("south africa", "sudafrika", "zaf"),
    "NG": ("nigeria", "nga"),
    "KE": ("kenya", "ken"),
    "GH": ("ghana", "gha"),
    "ET": ("ethiopia", "eth"),
    "AU": ("australia", "australien", "aus"),
    "NZ": ("new zealand", "nzl"),
}

#: ABD eyalet kisaltmalari. Jooble ABD ilanlarinda "Austin, TX" -> country="TX".
_US_STATES = (
    "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh "
    "nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc"
).split()
_US_STATE_NAMES = (
    "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia "
    "hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts "
    "michigan minnesota mississippi missouri montana nebraska nevada ohio oklahoma oregon "
    "pennsylvania tennessee texas utah vermont virginia washington wisconsin wyoming"
).split() + ["new hampshire", "new jersey", "new mexico", "new york", "north carolina",
             "north dakota", "rhode island", "south carolina", "south dakota", "west virginia",
             "district of columbia"]

#: Kanada eyalet kisaltmalari (ON = Ontario, ISO2 degil).
_CA_PROVINCES = "ab bc mb nb nl ns nt nu on pe qc sk yt".split()
_CA_PROVINCE_NAMES = ["ontario", "quebec", "british columbia", "alberta", "manitoba",
                      "saskatchewan", "nova scotia", "new brunswick", "newfoundland"]

#: Kaynaklarin ulke kolonuna sikca yazdigi buyuk sehirler.
_CITIES: dict[str, tuple[str, ...]] = {
    "DE": ("berlin", "munchen", "munich", "hamburg", "koln", "cologne", "frankfurt",
           "frankfurt am main", "stuttgart", "dusseldorf", "dortmund", "essen", "leipzig",
           "bremen", "dresden", "hannover", "nurnberg", "nuremberg", "mannheim", "karlsruhe",
           "walldorf", "ulm", "bonn", "augsburg", "wiesbaden", "bielefeld", "munster",
           "bayern", "bavaria", "hessen", "nordrhein westfalen", "baden wurttemberg"),
    "AT": ("wien", "vienna", "graz", "linz", "salzburg", "innsbruck"),
    "CH": ("zurich", "genf", "geneve", "geneva", "basel", "bern", "lausanne", "zug"),
    "NL": ("amsterdam", "rotterdam", "utrecht", "den haag", "the hague", "eindhoven"),
    "BE": ("brussels", "brussel", "bruxelles", "antwerp", "antwerpen", "gent"),
    "GB": ("london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh", "bristol",
           "liverpool", "sheffield", "cardiff", "belfast", "reading", "milton keynes"),
    "IE": ("dublin", "cork", "galway"),
    "FR": ("paris", "lyon", "marseille", "toulouse", "lille", "nantes", "bordeaux",
           "ile de france"),
    "ES": ("madrid", "barcelona", "valencia", "sevilla", "malaga", "bilbao"),
    "PT": ("lisbon", "lisboa", "porto"),
    "IT": ("milano", "milan", "roma", "rome", "torino", "turin", "bologna", "napoli"),
    "PL": ("warsaw", "warszawa", "krakow", "wroclaw", "poznan", "gdansk", "katowice"),
    "CZ": ("prague", "praha", "brno", "ostrava"),
    "HU": ("budapest", "debrecen"),
    "RO": ("bucharest", "bucuresti", "cluj", "cluj napoca", "timisoara", "iasi"),
    "SE": ("stockholm", "gothenburg", "goteborg", "malmo"),
    "DK": ("copenhagen", "kobenhavn", "aarhus"),
    "NO": ("oslo", "bergen"),
    "FI": ("helsinki", "espoo", "tampere"),
    "TR": ("istanbul", "ankara", "izmir", "bursa", "antalya", "kocaeli", "adana"),
    "IN": ("bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "chennai", "gurgaon",
           "gurugram", "noida", "delhi", "new delhi", "kolkata"),
    "US": ("new york city", "nyc", "san francisco", "chicago", "boston", "atlanta",
           "austin", "seattle", "dallas", "houston", "denver", "philadelphia", "phoenix",
           "los angeles", "san diego", "san jose", "miami", "minneapolis", "charlotte",
           "newtown square", "bay area"),
    "CA": ("toronto", "montreal", "vancouver", "ottawa", "calgary", "mississauga"),
    "AU": ("sydney", "melbourne", "brisbane", "perth", "canberra"),
    "AE": ("sharjah",),
    "JP": ("tokyo", "osaka"),
    "BR": ("sao paulo", "rio de janeiro"),
    "MX": ("mexico city", "guadalajara", "monterrey"),
    "ZA": ("johannesburg", "cape town", "pretoria"),
}

#: Ulke DEGIL: bunlar gorulunce arama location'a duser, kod uretilmez.
NOT_A_COUNTRY = {
    "remote", "uzaktan", "anywhere", "worldwide", "global", "hybrid", "hibrit", "onsite",
    "home office", "homeoffice", "home based", "work from home", "evden", "fernarbeit",
    "europe", "avrupa", "europa", "eu", "eea", "emea", "apac", "latam", "dach", "benelux",
    "nordics", "middle east", "asia", "africa", "america", "americas", "international",
    "bilinmiyor", "unknown", "n a", "na", "tbd", "various", "multiple locations",
    "remote work", "fully remote", "belirsiz", "diger", "other",
}

#: Kaynagin kendi pazari. SON CARE olarak kullanilir: metinden hicbir sey
#: cikmadiginda (tanimadigimiz kucuk sehir - Darmstadt, Oldenburg, Aberdeen...)
#: ilanin hangi ulke sitesinden geldigi en iyi tahmin. "Remote"/"Homeoffice"
#: gibi ACIKCA ulke olmayan degerlerde devreye GIRMEZ - onlar belirsiz kalir,
#: yoksa uzaktan ilanlar sessizce Almanya'ya yazilirdi.
SOURCE_COUNTRY = {
    "jooble-de": "DE",
    "jooble-uk": "GB",
    "arbeitnow": "DE",
    "reed": "GB",
    "kariyernet": "TR",
}

#: Cozucu surumu. Alias/sehir tablosu genisleyince ARTIRILIR: storage bunu
#: gorup daha once cozulememis satirlari bir kez daha dener.
RESOLVER_REV = 2

#: "Ulke belirsiz" kovasinin filtre jetonu.
NO_COUNTRY = "__none__"

#: Bolgelerin listede gorunme sirasi.
REGION_ORDER = ("Avrupa", "Kuzey Amerika", "Orta Doğu", "Asya", "Güney Amerika",
                "Afrika", "Okyanusya")

_SPLIT = re.compile(r"[,/|;()\[\]]+|\s+-\s+")
_JUNK = re.compile(r"[^a-z0-9 ]+")


def _key(value: str) -> str:
    """fold() + kalan aksanlari duserek eslesme anahtari uretir."""
    folded = unicodedata.normalize("NFKD", fold(value))
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _JUNK.sub(" ", plain).strip()


def _build_aliases() -> dict[str, str]:
    """Tek seferlik alias tablosu.

    Oncelik ONEMLI: once ISO2/ISO3/adlar/sehirler, SONRA eyalet kisaltmalari
    yazilir - boylece ciplak "CA" Kanada degil California (US) olur. Hicbir
    kaynak ulke kolonuna ciplak ISO2 yazmiyor (ted 3 harfli koddan tam ada
    ceviriyor, careerjet/upwork zaten tam ad veriyor), ama jooble ABD
    ilanlarinda "Austin, TX" -> "TX" uretiyor; ciplak iki harfin eyalet olma
    ihtimali cok daha yuksek.
    """
    out: dict[str, str] = {}
    for code, (ad, _bolge) in COUNTRIES.items():
        out[_key(code)] = code
        out[_key(ad)] = code
    for code, adlar in _NAMES.items():
        for ad in adlar:
            out[_key(ad)] = code
    for code, sehirler in _CITIES.items():
        for sehir in sehirler:
            out.setdefault(_key(sehir), code)
    for kisaltma in _CA_PROVINCES:
        out[kisaltma] = "CA"
    for ad in _CA_PROVINCE_NAMES:
        out[_key(ad)] = "CA"
    for kisaltma in _US_STATES:            # eyalet kisaltmasi ISO2'yi EZER
        out[kisaltma] = "US"
    for ad in _US_STATE_NAMES:
        out.setdefault(_key(ad), "US")
    for kelime in NOT_A_COUNTRY:
        out.pop(_key(kelime), None)
    return out


ALIASES = _build_aliases()
_NOT = {_key(k) for k in NOT_A_COUNTRY}


def _from_words(parca: str) -> str:
    """Parca icindeki kelimelerden ulke arar: "Berlin Office" -> DE.

    Yalnizca 3+ harfli kelimeler denenir: iki harfliler eyalet kisaltmasi
    tablosunda oldugu icin, serbest metindeki "in"/"or" gibi kelimeler yanlislikla
    Indiana/Oregon'a baglanirdi.
    """
    kelimeler = parca.split()
    for boy in (2, 1):                      # once "new york", sonra tek kelime
        for i in range(len(kelimeler) - boy + 1):
            grup = " ".join(kelimeler[i:i + boy])
            if len(grup) < 3 or grup in _NOT:
                continue
            if boy == 1 and len(grup) < 3:
                continue
            if grup in ALIASES:
                return ALIASES[grup]
    return ""


def _from_text(raw: str) -> str:
    if not raw:
        return ""
    key = _key(raw)
    if not key or key in _NOT:
        return ""
    if key in ALIASES:
        return ALIASES[key]
    # "Austin, TX" / "Frankfurt am Main, Hessen, Germany": sondan basa bakilir,
    # ulke bilgisi metnin sonunda olur. Bolme HAM metinde yapilir - _key()
    # noktalama isaretlerini bosluga cevirdigi icin virgul orada kayboluyor.
    parcalar = [p for p in (_key(p) for p in _SPLIT.split(raw)) if p]
    for parca in reversed(parcalar):
        if parca in _NOT:
            continue
        if parca in ALIASES:
            return ALIASES[parca]
    for parca in reversed(parcalar):
        code = _from_words(parca)
        if code:
            return code
    return ""


def _explicitly_not_country(raw: str) -> bool:
    """Metin "Remote"/"Homeoffice" gibi bilerek ulke belirtmiyor mu?"""
    if not raw:
        return False
    key = _key(raw)
    if key in _NOT:
        return True
    parcalar = [p for p in (_key(p) for p in _SPLIT.split(raw)) if p]
    return bool(parcalar) and all(p in _NOT for p in parcalar)


def resolve_country(country: str | None = "", location: str | None = "",
                    source: str | None = "") -> str:
    """Ham `country`/`location` metninden ISO2 kodu; cozulemezse "" doner.

    Metinden bir sey cikmazsa kaynagin pazarina duser (bkz. SOURCE_COUNTRY) -
    ama metin acikca "Remote" diyorsa dusmez, o ilan belirsiz kalir.
    """
    for raw in (country or "", location or ""):
        code = _from_text(raw)
        if code:
            return code
    if _explicitly_not_country(country) or _explicitly_not_country(location):
        return ""
    return SOURCE_COUNTRY.get((source or "").strip().lower(), "")


def country_name(code: str) -> str:
    """ISO2 -> Turkce ad. Bilinmeyen kod kendisi olarak doner."""
    if code == NO_COUNTRY:
        return "Ülke belirsiz"
    return COUNTRIES.get((code or "").upper(), ((code or ""), ""))[0]


def country_region(code: str) -> str:
    return COUNTRIES.get((code or "").upper(), ("", "Diğer"))[1] or "Diğer"


def is_code(value: str) -> bool:
    """Filtre degeri kanonik bir ulke kodu mu (yoksa eski serbest metin mi)."""
    return (value or "").upper() in COUNTRIES


def group_by_region(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """[{code, name, count}] -> bolgeye gore gruplu, REGION_ORDER sirasinda.

    "Ülke belirsiz" kovasi her zaman en sonda kendi grubunda durur.
    """
    gruplar: dict[str, list[dict]] = {}
    kuyruk: list[dict] = []
    for item in items:
        if item["code"] == NO_COUNTRY:
            kuyruk.append(item)
            continue
        gruplar.setdefault(country_region(item["code"]), []).append(item)
    sirali = [b for b in REGION_ORDER if b in gruplar]
    sirali += sorted(b for b in gruplar if b not in REGION_ORDER)
    out = [(bolge, gruplar[bolge]) for bolge in sirali]
    if kuyruk:
        out.append(("Diğer", kuyruk))
    return out
