"""Kod yazmadan panelden eklenen kaynaklar: RSS/Atom akisi ya da JSON API.

Tanim `config["sources"][ad]` altinda durur ve Ayarlar ekranindan yazilir:

    kind: rss | json          hangi bicim
    url: "https://site/feed?q={sorgu}&page={sayfa}"
    items_path: "data.results"        (json) ilan listesinin bulundugu yol
    fields: {title: "...", url: "..."} (json) hangi alan neye karsilik geliyor

`{sorgu}` ve `{sayfa}` yer tutuculari URL'de gecerse doldurulur; `{sayfa}` yoksa
tek istek atilir. Alan yollari noktali yazilir (`company.name`), liste icindeki
ilk ogeye `[0]` ile ulasilir (`locations[0].name`).
"""
from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from ..models import Project
from ..normalize import (clean_text, detect_contract, detect_work_mode, parse_date,
                         parse_percent, strip_html)
from .base import BaseSource, FetchError, MappingError

#: Ayarlar ekraninda eşlenebilen alanlar: (anahtar, etiket, zorunlu mu)
FIELD_SPEC = (
    ("title", "Baslik", True),
    ("url", "Ilan linki", True),
    ("company", "Firma", False),
    ("location", "Konum", False),
    ("country", "Ulke", False),
    ("description", "Aciklama", False),
    ("posted_at", "Ilan tarihi", False),
    ("budget_raw", "Butce / ucret", False),
    ("duration", "Sure", False),
    ("engagement", "Calisma tipi", False),
    ("source_id", "Ilan kimligi", False),
)

#: RSS'te alan adlari sabit oldugu icin varsayilan eslemeler hazir gelir.
#: Virgul = "sirayla dene": RSS `pubDate` derken Atom `published` diyor.
RSS_DEFAULTS = {
    "title": "title",
    "url": "link",
    "description": "description,summary,content,content:encoded",
    "posted_at": "pubDate,published,updated,date",
    "company": "author,creator,dc:creator,source",
}

KINDS = ("rss", "json")


def dig(data: Any, path: str) -> Any:
    """'company.name' / 'locations[0].name' yolunu izler; bulamazsa None."""
    if not path:
        return None
    current = data
    for part in path.replace("[", ".[").split("."):
        part = part.strip()
        if not part or current is None:
            continue
        if part.startswith("[") and part.endswith("]"):
            index = part[1:-1]
            if not isinstance(current, list) or not index.lstrip("-").isdigit():
                return None
            try:
                current = current[int(index)]
            except IndexError:
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _find_tag(item: Any, name: str):
    """RSS etiketini buyuk/kucuk harf ve ad alani farkini yok sayarak bulur.

    bs4'un xml cozumleyicisi harfe duyarli: `pubDate` ararken `pubdate` yazmak
    bulamiyordu. `dc:creator` gibi adlar ad alani onekiyle de yazilabilsin diye
    onek ayrica denenir.
    """
    if not name:
        return None
    wanted = name.lower()
    bare = wanted.split(":")[-1]
    for tag in item.find_all(True, recursive=True):
        tag_name = (tag.name or "").lower()
        full = f"{tag.prefix}:{tag_name}".lower() if tag.prefix else tag_name
        if wanted in (tag_name, full) or bare == tag_name:
            return tag
    return None


def _text(value: Any) -> str:
    """Eslesen alan liste ya da sozluk cikarsa da okunabilir metne cevrilir."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if v is not None)
    if isinstance(value, dict):
        for key in ("name", "title", "label", "value", "text"):
            if key in value:
                return _text(value[key])
        return ""
    return str(value)


class CustomSource(BaseSource):
    """Panelden tanimlanan kaynak. `options` tanimin kendisidir."""

    name = "custom"
    title = "Ozel kaynak"
    list_options = ("queries",)
    probe_trim = ("queries",)

    def __init__(self, client, options: dict[str, Any] | None = None):
        super().__init__(client, options)
        self.name = str(self.options.get("name") or "custom")
        self.title = str(self.options.get("title") or self.name)
        #: Adreste {sorgu} yoksa her sorgu ayni sayfayi cekiyor; ilk sonuc saklanir
        self._cache: list[Project] | None = None

    @property
    def _query_aware(self) -> bool:
        template = str(self.options.get("url") or "")
        return "{sorgu}" in template or "{query}" in template

    # --- istek --------------------------------------------------------
    def _urls(self, query: str) -> list[str]:
        template = str(self.options.get("url") or "").strip()
        if not template:
            raise MappingError("Kaynak adresi bos")
        pages = max(1, int(self.options.get("pages", 1) or 1))
        if "{sayfa}" not in template and "{page}" not in template:
            pages = 1
        start = int(self.options.get("first_page", 1) or 1)
        urls = []
        for page in range(start, start + pages):
            url = (template.replace("{sorgu}", query).replace("{query}", query)
                          .replace("{sayfa}", str(page)).replace("{page}", str(page)))
            urls.append(url)
        return urls

    def fetch(self, query: str) -> list[Project]:
        kind = str(self.options.get("kind") or "rss").lower()
        if kind not in KINDS:
            raise MappingError(f"Bilinmeyen kaynak turu: {kind}")
        if self._cache is not None and not self._query_aware:
            # adres sorguya duyarsiz: ayni sayfayi her sorgu icin tekrar cekmeyelim
            return self._cache
        projects: list[Project] = []
        seen: set[str] = set()
        found_items = 0
        for url in self._urls(query):
            response = self.client.get(url)
            items = self._items_rss(response.text) if kind == "rss" else self._items_json(response)
            if not items:
                break
            found_items += len(items)
            for item in items:
                project = self._to_project(item, kind)
                if project and project.url not in seen:
                    seen.add(project.url)
                    projects.append(project)

        if found_items and not projects:
            # Ilan var ama hicbiri okunamadi: sessizce "sonuc yok" demek yerine
            # kullaniciya baslik/link eslemesini duzeltmesi gerektigi soylenir.
            mapping = self._mapping(kind)
            raise MappingError(
                f"{found_items} kayit bulundu ama hicbirinden baslik+link okunamadi. "
                f"Baslik alani: '{mapping.get('title', '-')}', link alani: '{mapping.get('url', '-')}'. "
                f"Ornek kayittaki alanlar: {self._sample_keys(items[0], kind)}")
        if not self._query_aware:
            self._cache = projects
        return projects

    def _sample_keys(self, item: Any, kind: str) -> str:
        """Hata mesajinda "hangi alanlar var" ipucu."""
        if kind == "json" and isinstance(item, dict):
            return ", ".join(list(item)[:12]) or "-"
        if kind == "rss":
            return ", ".join(sorted({c.name for c in item.find_all(recursive=False)})[:12]) or "-"
        return "-"

    def _items_json(self, response) -> list:
        try:
            payload = response.json()
        except ValueError as exc:
            raise FetchError(f"Yanit JSON degil: {exc}") from exc
        path = str(self.options.get("items_path") or "").strip()
        items = dig(payload, path) if path else payload
        if items is None:
            raise MappingError(f"Ilan listesi bulunamadi (yol: {path or '-'})")
        if isinstance(items, dict):
            # {"1": {...}, "2": {...}} gibi anahtarli koleksiyonlar listeye cevrilir;
            # tek bir nesneye isaret edildiyse bu bir esleme hatasidir, gizlenmemeli
            values = list(items.values())
            if values and all(isinstance(v, dict) for v in values):
                return values
        if not isinstance(items, list):
            raise MappingError(
                f"'{path or 'kok'}' bir ilan listesi degil. "
                f"Buradaki alanlar: {', '.join(list(items)[:12]) if isinstance(items, dict) else '-'}")
        return items

    def _items_rss(self, text: str) -> list:
        soup = BeautifulSoup(text, "xml")
        items = soup.find_all("item") or soup.find_all("entry")
        if not items and "<html" in text[:400].lower():
            raise MappingError("Adres RSS degil, HTML sayfa dondu")
        return items

    # --- eslestirme ---------------------------------------------------
    def _mapping(self, kind: str) -> dict[str, str]:
        fields = {k: str(v).strip() for k, v in (self.options.get("fields") or {}).items() if v}
        if kind == "rss":
            return {**RSS_DEFAULTS, **fields}
        return fields

    def _value(self, item: Any, kind: str, mapping: dict[str, str], key: str) -> str:
        path = mapping.get(key, "")
        if not path:
            return ""
        if kind == "json":
            return _text(dig(item, path))
        # RSS: virgulle ayrilmis etiket adlari sirayla denenir
        for candidate in path.split(","):
            tag = _find_tag(item, candidate.strip())
            if tag is None:
                continue
            text = (tag.text or "").strip()
            # Atom'da <link href="..."/> metin tasimaz
            if not text and key == "url":
                text = tag.get("href", "").strip()
            if text:
                return text
        return ""

    def _to_project(self, item: Any, kind: str) -> Project | None:
        mapping = self._mapping(kind)
        title = clean_text(self._value(item, kind, mapping, "title"))
        url = clean_text(self._value(item, kind, mapping, "url"))
        if not title or not url:
            return None            # baslik ya da link yoksa ilan ise yaramaz

        description = strip_html(self._value(item, kind, mapping, "description"))[:4000]
        location = clean_text(self._value(item, kind, mapping, "location"))
        engagement = clean_text(self._value(item, kind, mapping, "engagement"))
        source_id = clean_text(self._value(item, kind, mapping, "source_id"))

        return Project(
            source=self.name,
            source_id=source_id or self.make_id(url, title),
            url=url,
            title=title,
            company=clean_text(self._value(item, kind, mapping, "company")),
            location=location,
            country=clean_text(self._value(item, kind, mapping, "country")),
            work_mode=detect_work_mode(title, location, description),
            remote_percent=parse_percent(f"{title} {description}"),
            engagement=engagement,
            is_contract=detect_contract(title, engagement, description),
            duration=clean_text(self._value(item, kind, mapping, "duration")),
            budget_raw=clean_text(self._value(item, kind, mapping, "budget_raw")),
            description=description,
            posted_at=parse_date(self._value(item, kind, mapping, "posted_at")),
        )
