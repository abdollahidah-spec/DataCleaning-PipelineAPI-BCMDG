"""
shared/email_notifier.py
==========================
Notifications par email (OK/KO) à l'issue de chaque run — solution simple et
gratuite : Gmail SMTP via smtplib (bibliothèque standard), avec un mot de passe
d'application Gmail (nécessite la double authentification sur le compte émetteur).

Variables .env requises :
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_APP_PASSWORD, EMAIL_TO (liste séparée par virgules)
    EMAIL_MAX_ATTACHMENT_MB (optionnel, défaut 15)
"""
from __future__ import annotations

import os
import smtplib
import traceback as _tb
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from shared.quality_report import QualityReport
from shared.state_store import RunState


def _is_configured() -> bool:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_APP_PASSWORD", "EMAIL_TO"]
    return all(os.getenv(k) for k in required)


def _recipients() -> list[str]:
    raw = os.getenv("EMAIL_TO", "")
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def _render_cumulative_html(state: RunState) -> str:
    """Stats globales cumulées depuis le tout premier run (Initial Load)."""
    since = state.first_run_datetime.strftime("%Y-%m-%d") if state.first_run_datetime else "?"
    return f"""
    <h3>Stats globales (depuis le {since})</h3>
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><td>Runs effectués</td><td>{state.cumulative_runs:,}</td></tr>
      <tr><td>Lignes traitées (total)</td><td>{state.cumulative_rows:,}</td></tr>
      <tr><td>Outliers/anomalies détectés (total)</td><td>{state.cumulative_outliers:,}</td></tr>
    </table>
    """


def send_run_notification(
    status: str,
    report: Optional[QualityReport],
    error: Optional[str] = None,
    exc: Optional[BaseException] = None,
    attachments: Optional[list[Path]] = None,
    cumulative: Optional[RunState] = None,
    dry_run: bool = False,
) -> bool:
    """
    Envoie l'email OK/KO de fin de run. Ne lève JAMAIS d'exception — un échec
    d'envoi ne doit pas transformer un run par ailleurs réussi en échec ; il est
    seulement loggé et la fonction retourne False.

    Corps : stats du run en cours, puis (si `cumulative` fourni) un saut de deux
    lignes suivi des stats globales cumulées depuis le premier run.
    """
    api_id = report.api_id if report else "UNKNOWN"
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if status == "OK":
        n_rows = report.n_rows if report else 0
        subject = f"[BCM Data Cleaning][{api_id}][OK] {n_rows} lignes — {date_str}"
        body = (report.to_html_summary() if report else "<p>Run OK (rapport indisponible)</p>")
    else:
        subject = f"[BCM Data Cleaning][{api_id}][KO] Échec du traitement — {date_str}"
        tb_tail = "\n".join(_tb.format_exception(type(exc), exc, exc.__traceback__)[-15:]) if exc else ""
        body = (
            f"<h3>{api_id} — ÉCHEC</h3><p><b>Erreur :</b> {error or 'inconnue'}</p>"
            f"<pre>{tb_tail}</pre>"
            + (report.to_html_summary() if report else "")
        )

    if cumulative is not None:
        body += "<br><br>" + _render_cumulative_html(cumulative)

    max_mb = float(os.getenv("EMAIL_MAX_ATTACHMENT_MB", "15"))
    valid_attachments = []
    skipped_note = ""
    for p in (attachments or []):
        p = Path(p)
        if not p.exists():
            continue
        size_mb = p.stat().st_size / (1024 * 1024)
        if size_mb <= max_mb:
            valid_attachments.append(p)
        else:
            skipped_note += f"<p><i>Pièce jointe {p.name} omise ({size_mb:.1f} MB > {max_mb} MB) — voir SharePoint.</i></p>"
    body += skipped_note

    if dry_run or not _is_configured():
        print(f"[EMAIL][dry-run={dry_run}] To={_recipients()} Subject={subject}")
        print(f"[EMAIL] Attachments: {[str(p) for p in valid_attachments]}")
        return True

    try:
        msg = MIMEMultipart()
        msg["From"] = os.getenv("SMTP_USER")
        msg["To"] = ", ".join(_recipients())
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        for p in valid_attachments:
            with open(p, "rb") as f:
                part = MIMEApplication(f.read(), Name=p.name)
            part["Content-Disposition"] = f'attachment; filename="{p.name}"'
            msg.attach(part)

        host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(os.getenv("SMTP_USER"), os.getenv("SMTP_APP_PASSWORD"))
            server.sendmail(os.getenv("SMTP_USER"), _recipients(), msg.as_string())
        print(f"  [Email] Envoyé -> {_recipients()}")
        return True

    except Exception as e:
        print(f"  [Email] Échec envoi : {e}")
        return False
