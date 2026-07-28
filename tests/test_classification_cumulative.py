"""
Vérifie que le classeur de classification (source Power BI, chemin stable) reste
CUMULATIF entre deux runs en mode incrémental — un label vu et corrigé lors d'un
run précédent ne doit jamais disparaître simplement parce que le delta du run
suivant ne le contient plus (voir shared/field_processor.py::CategoricalFieldProcessor
et e11_rdcc/fields/{devise,nomcorrespondant}.py::build_full_classification_*).
"""
import pandas as pd

from e11_rdcc.fields.devise import build_devise_processor, save_warm_start_devise
from e11_rdcc.fields.nomcorrespondant import build_nomcorrespondant_processor, save_warm_start_nomcorrespondant


def _nomcorrespondant_field_cfg(tmp_path) -> dict:
    return {
        "name": "NomCorrespondant",
        "columns": {
            "field": "NomCorrespondant",
            "field_out": "NomCorrespondant_Normalisé",
            "ref_transaction": "ReferenceTransaction",
            "ref_banque": "RefBanque",
        },
        "outlier_tag": "OUTLIER",
        "referentiel_path": str(tmp_path / "nomcorrespondant_referentiel_E11.json"),
        "llm": {"batch_size": 20},
    }


def _devise_field_cfg(tmp_path) -> dict:
    return {
        "name": "Devise",
        "columns": {
            "field": "Devise",
            "field_out": "Devise_Normalisée",
            "ref_transaction": "NomCorrespondant",
            "ref_banque": "RefBanque",
        },
        "outlier_tag": "OUTLIER",
        "referentiel_path": str(tmp_path / "devise_referentiel.json"),
    }


def test_nomcorrespondant_classification_keeps_label_absent_from_current_delta(isolated_referentiel_dir):
    # "Semaine 1" : une correction manuelle a déjà été appliquée et mise en cache.
    save_warm_start_nomcorrespondant("E11_RDCC", {"BANQUE SEMAINE 1": "REAL BANK SA"}, verbose=False)

    # "Semaine 2" : le delta de CE run ne contient PLUS du tout cette banque.
    processor = build_nomcorrespondant_processor(_nomcorrespondant_field_cfg(isolated_referentiel_dir))
    df_week2 = pd.DataFrame({
        "RefBanque": ["BANK01"],
        "NomCorrespondant": ["ATTIJARIWAFA BANK MAROC"],
        "ReferenceTransaction": ["REF001"],
    })
    result = processor.process(df_week2, api_id="E11_RDCC")

    labels = dict(zip(result.classification_df["NomCorrespondant"],
                       result.classification_df["NomCorrespondant_Normalisé"]))
    assert labels.get("BANQUE SEMAINE 1") == "REAL BANK SA"
    assert labels.get("ATTIJARIWAFA BANK MAROC") == "ATTIJARIWAFA BANK"


def test_devise_classification_keeps_correction_absent_from_current_delta(isolated_referentiel_dir):
    save_warm_start_devise("E11_RDCC", {"XYZ": "EUR"}, verbose=False)

    processor = build_devise_processor(_devise_field_cfg(isolated_referentiel_dir))
    df_week2 = pd.DataFrame({
        "RefBanque": ["BANK01"],
        "Devise": ["USD"],
        "NomCorrespondant": ["ATTIJARIWAFA BANK MAROC"],
    })
    result = processor.process(df_week2, api_id="E11_RDCC")

    labels = dict(zip(result.classification_df["Devise"], result.classification_df["Devise_Normalisée"]))
    assert labels.get("XYZ") == "EUR"          # correction de la semaine precedente, absente du delta actuel
    assert labels.get("USD") == "USD"           # entree referentiel statique, toujours presente


def test_classification_cache_correction_overrides_stale_referentiel_entry(isolated_referentiel_dir):
    """Une correction manuelle doit primer sur une entree statique du referentiel."""
    save_warm_start_nomcorrespondant("E11_RDCC", {"AFREXIM BANK": "AFREXIMBANK CORRIGE"}, verbose=False)

    processor = build_nomcorrespondant_processor(_nomcorrespondant_field_cfg(isolated_referentiel_dir))
    df = pd.DataFrame({"RefBanque": ["BANK01"], "NomCorrespondant": ["X"], "ReferenceTransaction": ["R"]})
    result = processor.process(df, api_id="E11_RDCC")

    labels = dict(zip(result.classification_df["NomCorrespondant"],
                       result.classification_df["NomCorrespondant_Normalisé"]))
    assert labels.get("AFREXIM BANK") == "AFREXIMBANK CORRIGE"
