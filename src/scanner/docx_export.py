"""Tek ilani (ya da birkacini) formatli bir Word (.docx) dosyasina cikarir.

`python-docx` KURULMUYOR: bir ilani disari aktarmak icin paket eklemek Docker
imajini ve bagimlilik yuzeyini bosuna buyuturdu - docx zaten bir ZIP + birkac
XML. Burasi minimal ama gecerli bir WordprocessingML uretir (Word, LibreOffice
ve Google Docs acar).

Cikti kategorize: baslik, kunye tablosu (skor/calisma sekli/firma/butce...),
yapay zeka ozeti, olmazsa olmaz kosullar, eslesen kelimeler ve tam aciklama.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

MODE_LABEL = {"remote": "Uzaktan", "hybrid": "Hibrit", "onsite": "Yerinde", "unknown": "Belirsiz"}
STATUS_LABEL = {
    "new": "İşaretsiz", "shortlist": "Takipte", "applied": "Başvuruldu",
    "rejected": "Olumsuz", "won_pending": "Olumlu — henüz başlamadı",
    "won_active": "Olumlu — üzerinde çalışılıyor", "won_done": "Olumlu — bitti",
}

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '</Types>'
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '</Relationships>'
)
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
    '<w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
    '<w:pPr><w:spacing w:before="0" w:after="120"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="40"/><w:color w:val="1F3864"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
    '<w:pPr><w:spacing w:before="280" w:after="100"/><w:outlineLvl w:val="0"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="26"/><w:color w:val="2E74B5"/></w:rPr></w:style>'
    '</w:styles>'
)


def _t(text: str) -> str:
    """Bosluklari koruyan bir <w:t>."""
    return f'<w:t xml:space="preserve">{escape(text or "")}</w:t>'


def _para(text: str = "", *, style: str = "", bold: bool = False, color: str = "",
          size: int = 0) -> str:
    ppr = f'<w:pStyle w:val="{style}"/>' if style else ""
    rpr_bits = ""
    if bold:
        rpr_bits += "<w:b/>"
    if size:
        rpr_bits += f'<w:sz w:val="{size}"/>'
    if color:
        rpr_bits += f'<w:color w:val="{color}"/>'
    rpr = f"<w:rPr>{rpr_bits}</w:rPr>" if rpr_bits else ""
    body = f"<w:r>{rpr}{_t(text)}</w:r>" if text else ""
    return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{body}</w:p>"


def _bullet(text: str) -> str:
    # numbering.xml eklemeden: asili girinti + elle madde imi.
    return (
        '<w:p><w:pPr><w:ind w:left="360" w:hanging="360"/>'
        '<w:spacing w:after="40"/></w:pPr>'
        '<w:r><w:t xml:space="preserve">• </w:t></w:r>'
        f'<w:r>{_t(text)}</w:r></w:p>'
    )


def _kunye_table(rows: list[tuple[str, str]]) -> str:
    trs = []
    for label, value in rows:
        if not value:
            continue
        trs.append(
            "<w:tr>"
            '<w:tc><w:tcPr><w:tcW w:w="2600" w:type="dxa"/>'
            '<w:shd w:val="clear" w:fill="EEF3F8"/></w:tcPr>'
            f'<w:p><w:r><w:rPr><w:b/></w:rPr>{_t(label)}</w:r></w:p></w:tc>'
            '<w:tc><w:tcPr><w:tcW w:w="6400" w:type="dxa"/></w:tcPr>'
            f'<w:p><w:r>{_t(value)}</w:r></w:p></w:tc>'
            "</w:tr>"
        )
    if not trs:
        return ""
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/>'
        '<w:tblBorders>'
        + "".join(f'<w:{e} w:val="single" w:sz="4" w:color="C7D0DA"/>'
                  for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + "</w:tblBorders></w:tblPr>"
        '<w:tblGrid><w:gridCol w:w="2600"/><w:gridCol w:w="6400"/></w:tblGrid>'
        + "".join(trs) + "</w:tbl>"
    )


def _json_list(value) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        return [str(value)]


def _fmt_date(value) -> str:
    return str(value)[:10] if value else ""


def _project_body(row: dict) -> list[str]:
    parts: list[str] = [_para(row.get("title") or "(başlıksız ilan)", style="Title")]

    mark = row.get("mark") or row.get("status") or ""
    contract = row.get("is_contract")
    kunye = [
        ("Skor", str(row.get("score")) if row.get("score") is not None else ""),
        ("Güncellik", "Yayında" if row.get("is_active") else "İlan kapandı"),
        ("Çalışma şekli", MODE_LABEL.get(row.get("work_mode") or "unknown", "Belirsiz")),
        ("Sözleşme", "" if contract is None else ("Proje bazlı / Contract" if contract else "Kadrolu")),
        ("Firma", row.get("company") or ""),
        ("Konum", row.get("location") or row.get("country") or ""),
        ("Süre", row.get("duration") or ""),
        ("Başlangıç", row.get("starts_at") or ""),
        ("Bütçe / Ücret", row.get("budget_raw") or ""),
        ("İlan tarihi", _fmt_date(row.get("posted_at")) or _fmt_date(row.get("first_seen_at"))),
        ("Kaynak", row.get("source") or ""),
        ("Durum", STATUS_LABEL.get(mark, "") if mark else ""),
        ("Bağlantı", row.get("url") or ""),
    ]
    parts.append(_para("Künye", style="Heading1"))
    parts.append(_kunye_table(kunye))

    ozet = (row.get("ai_summary") or row.get("summary_en") or "").strip()
    if ozet:
        parts.append(_para("Özet", style="Heading1"))
        for satir in (ozet.splitlines() or [ozet]):
            parts.append(_para(satir))

    kosullar = _json_list(row.get("ai_must_haves"))
    if kosullar:
        parts.append(_para("Olmazsa olmaz koşullar", style="Heading1"))
        parts.extend(_bullet(k) for k in kosullar)

    hits = _json_list(row.get("keywords_hit"))
    if hits:
        parts.append(_para("Eşleşen kelimeler", style="Heading1"))
        parts.append(_para(", ".join(hits)))

    aciklama = (row.get("description") or "").strip()
    if aciklama:
        parts.append(_para("İlan açıklaması", style="Heading1"))
        bloklar = [b.strip() for b in aciklama.replace("\r\n", "\n").split("\n") if b.strip()]
        for blok in (bloklar or [aciklama]):
            parts.append(_para(blok))

    parts.append(_para(
        f"İlan Tarayıcı ile {datetime.now(timezone.utc).astimezone().strftime('%d.%m.%Y %H:%M')} "
        "tarihinde dışa aktarıldı.", color="8A94A6", size=16))
    return parts


def build_docx(rows) -> bytes:
    """Bir veya birkac ilani tek bir .docx'e yazar; ilanlar arasinda sayfa sonu."""
    rows = list(rows)
    govde: list[str] = []
    for i, row in enumerate(rows):
        if i:
            govde.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        govde.extend(_project_body(row))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(govde) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
        '</w:body></w:document>'
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        zf.writestr("word/styles.xml", _STYLES)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()
