"""
shared/quality_report.py
==========================
Rapport de qualité des traitements : statistiques générales + indicateurs de
performance calculés à l'issue de chaque run. Alimente la feuille "Rapport_Qualite"
du classeur de sortie, le corps de l'email de notification, et les logs structurés.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd


@dataclass
class QualityReport:
    api_id: str
    mode: str
    started_at: datetime
    finished_at: datetime
    n_rows: int
    n_outlier_rows: int = 0                                    # lignes non-conformes (catégoriel OU anomalie numérique ERROR)
    per_field_stats: dict = field(default_factory=dict)       # field_name -> stats dict
    taux_conformite_pct: float = 0.0                          # % lignes sans aucun outlier/anomalie
    taux_normalisation_pct: float = 0.0                       # moyenne des taux de normalisation (champs catégoriels)
    taux_outliers_pct: float = 0.0
    outliers_by_refbanque: dict = field(default_factory=dict)
    outliers_by_champ: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def execution_time_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def to_html_summary(self) -> str:
        by_champ = "".join(
            f"<tr><td>{c}</td><td>{n:,}</td><td>{round(100*n/max(self.n_rows,1),2)}%</td></tr>"
            for c, n in sorted(self.outliers_by_champ.items(), key=lambda kv: -kv[1])
        )
        by_banque = "".join(f"<tr><td>{b}</td><td>{n}</td></tr>" for b, n in self.outliers_by_refbanque.items())
        return f"""
        <h3>{self.api_id} — {self.mode}</h3>
        <table border="1" cellpadding="4" cellspacing="0">
          <tr><td>Lignes traitées</td><td>{self.n_rows:,}</td></tr>
          <tr><td>Taux conformité (global, tous champs confondus)</td><td>{self.taux_conformite_pct}%</td></tr>
          <tr><td>Taux normalisation (champs catégoriels)</td><td>{self.taux_normalisation_pct}%</td></tr>
          <tr><td>Taux outliers (global, tous champs confondus)</td><td>{self.taux_outliers_pct}%</td></tr>
          <tr><td>Temps d'exécution</td><td>{self.execution_time_seconds:.1f}s</td></tr>
        </table>
        <h4>Détail par champ / règle (% du total de lignes)</h4>
        <table border="1" cellpadding="4" cellspacing="0">
          <tr><th>Champ / règle</th><th>Lignes</th><th>%</th></tr>
          {by_champ}
        </table>
        <h4>Outliers par RefBanque</h4>
        <table border="1" cellpadding="4" cellspacing="0">{by_banque}</table>
        """

    def to_log_lines(self) -> list[str]:
        lines = [
            f"api_id={self.api_id} mode={self.mode} duration={self.execution_time_seconds:.1f}s "
            f"rows={self.n_rows} conformite={self.taux_conformite_pct}% (global, tous champs confondus) "
            f"normalisation={self.taux_normalisation_pct}% outliers={self.taux_outliers_pct}% (global)",
        ]
        for champ, n in sorted(self.outliers_by_champ.items(), key=lambda kv: -kv[1]):
            pct = round(100 * n / max(self.n_rows, 1), 2)
            lines.append(f"  [{champ}] {n:,} lignes ({pct}% du total)")
        for w in self.warnings:
            lines.append(f"WARNING: {w}")
        for e in self.errors:
            lines.append(f"ERROR: {e}")
        return lines


def compute_quality_report(
    api_id: str,
    mode: str,
    started_at: datetime,
    finished_at: datetime,
    df_final: pd.DataFrame,
    categorical_fields: list,   # list of (field_name, col_out, stats_dict)
    numeric_anomalies_df: Optional[pd.DataFrame],
    ref_banque_col: str = "RefBanque",
    outlier_tag: str = "OUTLIER",
    warnings: Optional[list] = None,
    errors: Optional[list] = None,
) -> QualityReport:
    n_rows = len(df_final)
    per_field_stats = {name: stats for name, _, stats in categorical_fields}

    outliers_by_champ: dict = {}
    is_outlier_row = pd.Series(False, index=df_final.index)
    for name, col_out, _ in categorical_fields:
        if col_out not in df_final.columns:
            continue
        mask = df_final[col_out] == outlier_tag
        outliers_by_champ[name] = int(mask.sum())
        is_outlier_row = is_outlier_row | mask

    # Cohérence numérique : "_NC_row_conforme" (aligné sur l'index d'origine, posé par
    # NumericCoherenceProcessor) est la source de vérité pour incorporer ces anomalies
    # dans la conformité globale — le long-form numeric_anomalies_df ne porte pas
    # l'index d'origine et ne peut pas être OR-é directement sur is_outlier_row.
    if "_NC_row_conforme" in df_final.columns:
        is_outlier_row = is_outlier_row | (~df_final["_NC_row_conforme"].astype(bool))

    if numeric_anomalies_df is not None and not numeric_anomalies_df.empty and "Rule" in numeric_anomalies_df.columns:
        error_only = numeric_anomalies_df[numeric_anomalies_df.get("Severity", "ERROR") == "ERROR"]
        for rule, sub in error_only.groupby("Rule"):
            outliers_by_champ[str(rule)] = int(len(sub))

    outliers_by_refbanque: dict = {}
    if ref_banque_col in df_final.columns:
        counts = df_final.loc[is_outlier_row, ref_banque_col].value_counts()
        outliers_by_refbanque = {str(k): int(v) for k, v in counts.items()}
        if numeric_anomalies_df is not None and not numeric_anomalies_df.empty and ref_banque_col in numeric_anomalies_df.columns:
            error_only = numeric_anomalies_df[numeric_anomalies_df.get("Severity", "ERROR") == "ERROR"]
            for k, v in error_only[ref_banque_col].value_counts().items():
                outliers_by_refbanque[str(k)] = outliers_by_refbanque.get(str(k), 0) + int(v)

    n_conforme = n_rows - int(is_outlier_row.sum())
    taux_conformite = round(100 * n_conforme / max(n_rows, 1), 2) if n_rows else 0.0

    norm_rates = [s.get("taux_normalisation_pct", 0.0) for _, _, s in categorical_fields]
    taux_normalisation = round(sum(norm_rates) / len(norm_rates), 2) if norm_rates else 0.0

    taux_outliers = round(100 * int(is_outlier_row.sum()) / max(n_rows, 1), 2) if n_rows else 0.0

    return QualityReport(
        api_id=api_id,
        mode=mode,
        started_at=started_at,
        finished_at=finished_at,
        n_rows=n_rows,
        n_outlier_rows=int(is_outlier_row.sum()),
        per_field_stats=per_field_stats,
        taux_conformite_pct=taux_conformite,
        taux_normalisation_pct=taux_normalisation,
        taux_outliers_pct=taux_outliers,
        outliers_by_refbanque=outliers_by_refbanque,
        outliers_by_champ=outliers_by_champ,
        warnings=warnings or [],
        errors=errors or [],
    )
