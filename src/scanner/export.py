"""Excel / CSV cikti."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MODE_LABEL = {"remote": "UZAKTAN", "hybrid": "Hibrit", "onsite": "Yerinde", "unknown": "?"}

COLUMNS = [
    ("Skor", "score", 8),
    ("Calisma", "work_mode", 12),
    ("Sozlesme", "is_contract", 12),
    ("Baslik", "title", 45),
    ("Firma", "company", 26),
    ("Konum", "location", 20),
    ("Sure", "duration", 14),
    ("Baslangic", "starts_at", 12),
    ("Butce/Ucret", "budget_raw", 18),
    ("Ilan tarihi", "posted_at", 14),
    ("Kaynak", "source", 14),
    ("Eslesen", "keywords_hit", 30),
    ("Link", "url", 55),
]

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
REMOTE_FILL = PatternFill("solid", fgColor="C6EFCE")
HYBRID_FILL = PatternFill("solid", fgColor="FFEB9C")


def _json_list(value) -> str:
    if not value:
        return ""
    try:
        return ", ".join(json.loads(value))
    except (json.JSONDecodeError, TypeError):
        return str(value)


def _value(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if key == "work_mode":
        return MODE_LABEL.get(value or "unknown", "?")
    if key == "is_contract":
        return "" if value is None else ("Proje/Contract" if value else "Kadrolu")
    if key in ("skills", "keywords_hit"):
        return _json_list(value)
    if key == "posted_at" and value:
        return str(value)[:10]
    return "" if value is None else str(value)


def to_xlsx(rows: Sequence[sqlite3.Row], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "SAP Projeleri"

    for col, (header, _key, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        ws.column_dimensions[get_column_letter(col)].width = width

    for r, row in enumerate(rows, start=2):
        for c, (_header, key, _w) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=r, column=c, value=_value(row, key))
            cell.alignment = Alignment(vertical="top", wrap_text=key in ("title", "company"))
            if key == "url" and row["url"]:
                cell.hyperlink = row["url"]
                cell.font = Font(color="0563C1", underline="single")
        fill = REMOTE_FILL if row["work_mode"] == "remote" else (
            HYBRID_FILL if row["work_mode"] == "hybrid" else None)
        if fill:
            ws.cell(row=r, column=2).fill = fill

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(ws.max_row, 1)}"
    wb.save(path)
    return path


def to_csv(rows: Sequence[sqlite3.Row], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([header for header, _k, _w in COLUMNS])
        for row in rows:
            writer.writerow([_value(row, key) for _h, key, _w in COLUMNS])
    return path


def default_name(prefix: str, extension: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M')}.{extension}"
