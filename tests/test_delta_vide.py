"""
Run sans aucune nouvelle donnée (delta vide) — cas le plus fréquent en production :
la tâche planifiée se déclenche, mais rien n'a été ajouté en base depuis le
dernier passage.

Bug réel corrigé ici (E08, 10/09/2026) : le champ Pays reconstruisait sa table de
jointure à partir de listes Python. Sur un delta vide, les listes vides donnaient
des colonnes float64 face aux colonnes texte de la source, et la fusion échouait
avec « You are trying to merge on object and float64 columns for key 'Pays' ».
Le run se terminait en KO, avec envoi d'un email d'échec aux destinataires.
"""
import pandas as pd
import pytest

from shared.config import load_config

_APIS = [
    ("e11_rdcc", "E11_RDCC", "e11_rdcc.pipeline", "E11Pipeline"),
    ("e09_pe", "E09_PE", "e09_pe.pipeline", "E09Pipeline"),
    ("e08_ocd", "E08_OCD", "e08_ocd.pipeline", "E08Pipeline"),
    ("e07_fs", "E07_FS", "e07_fs.pipeline", "E07Pipeline"),
]


def _fixture_vide(paquet: str, tmp_path):
    """Même en-tête que la fixture d'exemple, mais AUCUNE ligne — reproduit ce que
    renvoie SQL Server quand le delta ne ramène rien."""
    source = pd.read_csv(f"tests/fixtures/{paquet}_sample.csv", sep=";", dtype=str,
                          keep_default_na=False)
    vide = tmp_path / f"{paquet}_vide.csv"
    source.head(0).to_csv(vide, sep=";", index=False)
    return vide


@pytest.mark.parametrize("paquet,api_id,module,classe", _APIS)
def test_run_sur_delta_vide_ne_plante_pas(paquet, api_id, module, classe, tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))
    cfg = load_config(f"{paquet}/config/{api_id}.yaml")
    pipeline_cls = getattr(importlib.import_module(module), classe)

    resultat = pipeline_cls(cfg).run(mode="auto",
                                      override_input=str(_fixture_vide(paquet, tmp_path)))

    assert resultat["status"] == "OK", f"{api_id} doit terminer OK sur un delta vide"
    assert resultat["quality"].n_rows == 0
    assert resultat["path"].exists(), "le classeur doit être produit même sans données"


def test_pays_supporte_un_delta_vide(tmp_path, monkeypatch):
    """Test ciblé sur la cause racine : la jointure de Pays doit conserver le type
    des colonnes de jointure, y compris quand il n'y a aucune ligne."""
    from e08_ocd.fields.pays import treating_pays

    monkeypatch.setattr("e08_ocd.fields.pays._REFERENTIEL_DIR", tmp_path)
    vide = pd.DataFrame({
        "Pays": pd.Series([], dtype=object),
        "NumCredoc": pd.Series([], dtype=object),
    })

    out = treating_pays(vide, pays_col="Pays", ref_col="NumCredoc", api_id="E08_OCD")

    assert len(out) == 0
    assert "Pays_Normalisé" in out.columns
