"""
shared/email_notifier.py
==========================
Notifications par email (OK/KO) à l'issue de chaque run — solution simple et
gratuite : Gmail SMTP via smtplib (bibliothèque standard), avec un mot de passe
d'application Gmail (nécessite la double authentification sur le compte émetteur).

Corps du mail OK : gabarit EXACT validé par le Business Analyst (texte brut, pas
HTML — les séparateurs "====" et le format "indicateurs" ne sont lisibles qu'en
texte simple), indicateurs du run en cours (delta en mode incremental), rappel
du rapport PDF joint (Rapport_Qualite_Outliers, voir shared/report_templates.py
et le module reports.py de chaque API, ex: e11_rdcc/reports.py, e09_pe/reports.py).

Corps du mail KO : ne contient JAMAIS le texte brut de l'exception ni de
traceback (demande explicite du Business Analyst) — uniquement la `category`
de l'exception (shared/errors.py, ex: "Fichier de configuration invalide ou
incomplet"), un message court qui oriente sans exposer de détail technique. Le
détail technique complet reste dans les logs (shared/logging_conf.py).

Variables .env requises :
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_APP_PASSWORD, EMAIL_TO (liste séparée par virgules)
    EMAIL_MAX_ATTACHMENT_MB (optionnel, défaut 15)
"""
from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from shared.quality_report import QualityReport, format_duration_mmss

_DEFAULT_ERROR_CATEGORY = "Erreur inattendue lors du traitement"


def _is_configured() -> bool:
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_APP_PASSWORD", "EMAIL_TO"]
    return all(os.getenv(k) for k in required)


def _recipients() -> list[str]:
    raw = os.getenv("EMAIL_TO", "")
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def _fmt_pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def _fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _mode_label(mode: str) -> str:
    return "Incremental Load – delta" if mode == "incremental" else "Initial Load – historique complet"


def _new_values_counters(report: QualityReport) -> tuple[int, int, int]:
    """(nouvelles valeurs distinctes, nouvelles normalisées, nouveaux outliers) — voir
    shared/field_processor.py::_categorical_stats pour la distinction "nouveau ce run"
    (method CLAUDE/OUTLIER) vs "déjà connu" (référentiel/cache). Les nouveaux outliers
    additionnent le catégoriel (valeurs distinctes) et le numérique (lignes ERROR),
    seule unité disponible côté numérique.

    `report.outliers_by_champ` mélange des clés catégorielles (noms de champs, ex:
    "Devise") et des clés de règles non-catégorielles (ex: "ARITHMETIC" pour E11,
    "AMOUNT_POSITIVE" pour E09) — ces dernières n'ont pas de liste fixe partagée
    entre APIs (decision H), donc on les identifie GÉNÉRIQUEMENT : toute clé de
    outliers_by_champ absente de per_field_stats (qui ne contient que les champs
    catégoriels) est par élimination une règle numérique/non-catégorielle."""
    n_new_distinct = sum(s.get("n_new_distinct", 0) for s in report.per_field_stats.values())
    n_new_normalized = sum(s.get("n_new_normalized", 0) for s in report.per_field_stats.values())
    n_new_categorical_outliers = sum(s.get("n_new_outliers", 0) for s in report.per_field_stats.values())
    categorical_keys = set(report.per_field_stats.keys())
    n_new_numeric_errors = sum(
        n for rule, n in report.outliers_by_champ.items() if rule not in categorical_keys
    )
    return n_new_distinct, n_new_normalized, n_new_categorical_outliers + n_new_numeric_errors


def _build_ok_subject(endpoint_label: str, date_str: str) -> str:
    return f"Pipeline {endpoint_label} : Rapport de qualité et rapport des outliers – {date_str}"


def _build_ok_body(report: QualityReport, endpoint_label: str, date_str: str, pdf_paths: list) -> str:
    is_delta = report.mode == "incremental"
    suf = " (delta)" if is_delta else ""
    header = "INDICATEURS DE L'EXÉCUTION (DELTA)" if is_delta else "INDICATEURS DE L'EXÉCUTION"
    n_new_distinct, n_new_normalized, n_new_outliers = _new_values_counters(report)

    if pdf_paths:
        pdf_lines = "\n".join(p.name for p in pdf_paths)
        detail_paragraph = (
            "Le détail complet – statistiques globales cumulées, répartition des outliers par champ "
            "traité et par RefBanque – est disponible dans les rapports joints :\n"
            f"{pdf_lines}\n\n"
        )
    else:
        detail_paragraph = ""

    return (
        "Bonjour,\n\n"
        f"Veuillez trouver ci-dessous les indicateurs clés de l'exécution (mode {_mode_label(report.mode)}) "
        f"de la pipeline de nettoyage/normalisation de l'endpoint {endpoint_label} du {date_str}, ainsi que "
        "le rapport de qualité et le rapport des outliers complets en pièce jointe (PDF) pour une vision globale.\n\n"
        "============================================\n"
        f"{header}\n"
        "============================================\n\n"
        f"Nombre de lignes traitées{suf} : {_fmt_int(report.n_rows)}\n"
        f"Nombre de nouvelles valeurs distinctes détectées : {_fmt_int(n_new_distinct)}\n"
        f"Nombre de nouvelles valeurs normalisées : {_fmt_int(n_new_normalized)}\n"
        f"Nombre de nouveaux outliers : {_fmt_int(n_new_outliers)}\n"
        f"Taux de données conformes{suf} : {_fmt_pct(report.taux_conformite_pct)}\n"
        f"Temps d'exécution{suf} : {format_duration_mmss(report.execution_time_seconds)}\n\n"
        "============================================\n\n"
        f"{detail_paragraph}"
        "Merci de bien vouloir consulter le rapport des outliers et de procéder à la validation métier "
        "des valeurs listées.\n\n"
        "Restant à disposition pour toute question ou précision complémentaire.\n\n"
        "Cordialement,\n"
    )


def _build_ko_subject(endpoint_label: str, date_str: str) -> str:
    return f"Pipeline {endpoint_label} : Échec du traitement – {date_str}"


def _build_ko_body(endpoint_label: str, date_str: str, exc: Optional[BaseException]) -> str:
    # Jamais str(exc) ni de traceback ici (demande explicite du Business Analyst) —
    # uniquement `category`, un libellé court et non technique (shared/errors.py).
    category = getattr(exc, "category", None) or _DEFAULT_ERROR_CATEGORY
    return (
        "Bonjour,\n\n"
        f"Le traitement de la pipeline {endpoint_label} du {date_str} a échoué.\n\n"
        f"Nature du problème : {category}.\n\n"
        "Merci de consulter les journaux d'exécution (logs) pour le détail technique complet, "
        "ou de contacter l'équipe technique si le problème persiste.\n\n"
        "Cordialement,\n"
    )


def send_run_notification(
    status: str,
    report: Optional[QualityReport],
    error: Optional[str] = None,
    exc: Optional[BaseException] = None,
    attachments: Optional[list] = None,
    pdf_report_paths: Optional[list] = None,
    endpoint_label: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    """
    Envoie l'email OK/KO de fin de run. Ne lève JAMAIS d'exception — un échec
    d'envoi ne doit pas transformer un run par ailleurs réussi en échec ; il est
    seulement loggé et la fonction retourne False.

    `pdf_report_paths` : les 2 PDF (Rapport_Qualite/Rapport_Outliers) — utilisés
    UNIQUEMENT pour lister leurs noms dans le corps (le paramètre `attachments`
    reste la liste complète, y compris le classeur Excel de classification, pour
    la pièce jointe MIME elle-même).
    """
    api_id = report.api_id if report else "UNKNOWN"
    endpoint_label = endpoint_label or api_id
    run_dt = (report.started_at if report else None) or datetime.now()
    date_str = run_dt.strftime("%d/%m/%Y")
    pdf_paths = [Path(p) for p in (pdf_report_paths or [])]

    if status == "OK" and report is not None:
        subject = _build_ok_subject(endpoint_label, date_str)
        body = _build_ok_body(report, endpoint_label, date_str, pdf_paths)
    else:
        subject = _build_ko_subject(endpoint_label, date_str)
        body = _build_ko_body(endpoint_label, date_str, exc)

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
            skipped_note += f"\n(Pièce jointe {p.name} omise : {size_mb:.1f} MB > {max_mb} MB.)"
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
        msg.attach(MIMEText(body, "plain", "utf-8"))

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
