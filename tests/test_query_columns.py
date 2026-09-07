"""
Vérifie la projection de colonnes (remplace `SELECT *`) et sa confrontation au
schéma réel de la table.

Contexte : l'Initial Load d'E09 est resté bloqué des heures en consommant toute
la mémoire — `SELECT *` rapatriait toutes les colonnes de la table d'historique.
La projection réduit le volume ; la confrontation au schéma évite de demander une
colonne absente et signale immédiatement un écart YAML/table.
"""
import pytest

from shared.errors import DataSourceError
from shared.query_columns import (
    primary_field_columns,
    projection_for,
    reconcile_with_table,
    required_columns,
)


def _cfg(**overrides) -> dict:
    cfg = {
        "api_id": "E08_OCD",
        "input": {"table_name": "E8EtatBcmOuvertureCreditDocumentaires", "dt_cr_column": "dtCr"},
        "fields": [
            {"name": "Devise", "type": "categorical",
             "columns": {"field": "Devise", "field_out": "Devise_Normalisée",
                         "ref_transaction": "NumCredoc", "ref_banque": "RefBanque"}},
            {"name": "NomDonneurOrdre", "type": "categorical",
             "columns": {"field": "NomDonneurOrdre", "field_out": "NomDonneurOrdre_Normalisé",
                         "ref_transaction": "NumCredoc", "ref_banque": "RefBanque",
                         "nif_nni": "NifNni"}},
        ],
    }
    cfg.update(overrides)
    return cfg


def test_output_columns_are_never_requested():
    """Régression : `field_out` est produit PAR la pipeline, il n'existe pas dans
    la table — le demander ferait échouer la requête sur "Invalid column name"."""
    cols = required_columns(_cfg())
    assert "Devise_Normalisée" not in cols
    assert "NomDonneurOrdre_Normalisé" not in cols
    assert {"Devise", "NomDonneurOrdre", "NumCredoc", "RefBanque", "NifNni", "dtCr"} == cols


def test_synthetic_columns_are_never_requested():
    """Les colonnes calculées par preprocess() (ex: témoin de non-activité
    globale d'E11) commencent par "_" et n'existent pas en base."""
    cfg = _cfg(fields=[{"name": "Devise", "type": "categorical",
                        "columns": {"field": "Devise", "field_out": "Devise_N",
                                    "ref_transaction": "_E11_GlobalNoActivite",
                                    "ref_banque": "RefBanque"}}])
    assert "_E11_GlobalNoActivite" not in required_columns(cfg)


def test_extra_columns_escape_hatch():
    cfg = _cfg()
    cfg["input"]["extra_columns"] = ["ColonneSupplementaire"]
    assert "ColonneSupplementaire" in required_columns(cfg)


def test_projection_can_be_disabled():
    cfg = _cfg()
    cfg["input"]["select_columns"] = False
    assert projection_for(cfg) is None      # None => SELECT * comme avant


def test_optional_missing_column_is_dropped_not_fatal():
    """Cas réel E08 : `NifNni` est configuré mais absent de la vraie table.
    Il doit être retiré du SELECT, sans faire échouer le run (l'étape de
    matching NIF est simplement sautée)."""
    cfg = _cfg()
    reelles = {"Devise", "NomDonneurOrdre", "NumCredoc", "RefBanque", "dtCr"}

    retenues, absentes = reconcile_with_table(cfg, sorted(required_columns(cfg)), reelles)

    assert absentes == ["NifNni"]
    assert "NifNni" not in retenues
    assert set(retenues) == reelles


def test_missing_primary_column_fails_immediately_with_clear_message():
    """Cas réel E08 : `Produits` (pluriel) alors que la table expose `Produit`.
    Avant, cela produisait un `KeyError: 'Produits'` après plusieurs minutes de
    traitement — désormais l'erreur est levée avant la requête, et nomme le
    champ, la colonne fautive et les colonnes disponibles."""
    cfg = _cfg(fields=[{"name": "Produits", "type": "categorical",
                        "columns": {"field": "Produits", "field_out": "Produits_Normalisé",
                                    "ref_transaction": "NumCredoc", "ref_banque": "RefBanque"}}])
    reelles = {"Produit", "NumCredoc", "RefBanque", "dtCr"}

    with pytest.raises(DataSourceError) as exc:
        reconcile_with_table(cfg, sorted(required_columns(cfg)), reelles)

    msg = str(exc.value)
    assert "Produits" in msg and "Produit'" in msg
    assert "champ 'Produits'" in msg
    assert "columns.field" in msg


def test_primary_field_columns_mapping():
    assert primary_field_columns(_cfg()) == {"Devise": "Devise",
                                              "NomDonneurOrdre": "NomDonneurOrdre"}
