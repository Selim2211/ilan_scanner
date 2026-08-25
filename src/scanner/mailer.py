"""SMTP ile proje raporu."""
from __future__ import annotations

import os
import smtplib
import sqlite3
from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Sequence

MODE_BADGE = {
    "remote": ("UZAKTAN", "#1a7f37"),
    "hybrid": ("HIBRIT", "#9a6700"),
    "onsite": ("Yerinde", "#6b7280"),
    "unknown": ("?", "#6b7280"),
}


def render_html(rows: Sequence[sqlite3.Row], title: str) -> str:
    remote_count = sum(1 for r in rows if r["work_mode"] == "remote")
    parts = [
        "<html><body style=\"font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222\">",
        f"<h2 style=\"color:#1F4E79\">{escape(title)}</h2>",
        f"<p>{len(rows)} proje - <b>{remote_count} uzaktan</b>.</p>",
        "<table cellpadding='6' cellspacing='0' border='0' style=\"border-collapse:collapse;width:100%\">",
        "<tr style=\"background:#1F4E79;color:#fff;text-align:left\">"
        "<th>Skor</th><th>Calisma</th><th>Proje</th><th>Firma</th>"
        "<th>Sure</th><th>Butce</th><th>Kaynak</th></tr>",
    ]
    for i, row in enumerate(rows):
        bg = "#f6f8fa" if i % 2 else "#ffffff"
        label, color = MODE_BADGE.get(row["work_mode"] or "unknown", MODE_BADGE["unknown"])
        contract = "" if row["is_contract"] is None else (
            " · proje bazlı" if row["is_contract"] else " · kadrolu")
        summary = (row["description"] or "")[:150]
        parts.append(
            f"<tr style=\"background:{bg};border-bottom:1px solid #e1e4e8\">"
            f"<td align='center'><b>{row['score']}</b></td>"
            f"<td><b style=\"color:{color}\">{label}</b>"
            f"<span style=\"color:#888;font-size:11px\">{escape(contract)}</span></td>"
            f"<td><a href=\"{escape(row['url'] or '')}\" style=\"color:#0563C1;text-decoration:none\">"
            f"{escape(row['title'] or '')}</a>"
            + (f"<div style=\"color:#666;font-size:12px\">{escape(summary)}</div>" if summary else "")
            + f"</td><td>{escape(row['company'] or '')}</td>"
            f"<td>{escape(row['duration'] or '')}</td>"
            f"<td>{escape(row['budget_raw'] or '')}</td>"
            f"<td>{escape(row['source'] or '')}</td></tr>"
        )
    parts.append("</table><p style=\"color:#888;font-size:12px\">"
                 "SAP Proje Radari tarafindan otomatik olusturuldu.</p></body></html>")
    return "\n".join(parts)


def build_message(rows: Sequence[sqlite3.Row], subject_template: str,
                  attachment: Path | None = None) -> EmailMessage:
    subject = subject_template.format(date=date.today().isoformat(), count=len(rows))
    sender = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER", "")
    recipients = [a.strip() for a in os.environ.get("MAIL_TO", "").split(",") if a.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(f"{len(rows)} SAP projesi bulundu. HTML goruntuleyici gerekiyor.")
    msg.add_alternative(render_html(rows, subject), subtype="html")

    if attachment and attachment.exists():
        msg.add_attachment(
            attachment.read_bytes(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=attachment.name,
        )
    return msg


def send(msg: EmailMessage) -> None:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if not (host and user and password and msg["To"]):
        raise RuntimeError("SMTP ayarlari eksik: .env icinde SMTP_HOST/SMTP_USER/SMTP_PASSWORD/MAIL_TO doldurun")

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
