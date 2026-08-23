"""
Vérifie que validate_config() lève des messages d'erreur PRÉCIS (clé exacte,
champ concerné) plutôt qu'un KeyError/TypeError brut — demande explicite du
Business Analyst : "Vérifier le comportement en cas de fichier de config
invalide/incomplet (message d'erreur clair)".
"""
import pytest

from shared.config_validate import validate_config
from shared.errors import ConfigError


def _valid_cfg(tmp_path) -> dict:
    ref_path = tmp_path / "ref.json"
    ref_path.write_text("{}", encoding="utf-8")
    return {
        "api_id": "E11_RDCC",
        "input": {"table_name": "SomeTable"},
        "output": {"local_dir": "out/", "classification_path": "PowerBI/x.xlsx"},
        "fields": [
            {
                "name": "NomCorrespondant",
                "type": "categorical",
                "columns": {
                    "field": "NomCorrespondant", "field_out": "NomCorrespondant_Normalisé",
                    "ref_transaction": "_E11_GlobalNoActivite", "ref_banque": "RefBanque",
                },
                "referentiel_path": str(ref_path),
            },
            {
                "name": "SoldesRDCC",
                "type": "numeric_coherence",
                "columns": {
                    "solde_debut": "SoldeDebutJournee", "mvts_debiteurs": "TotalMvtsDebiteursJournee",
                    "mvts_crediteurs": "TotalMvtsCrediteurs", "solde_fin": "SoldeFinJournee",
                    "date_fin": "DateFinJournee", "dt_cr": "dtCr", "ref_banque": "RefBanque",
                    "nom_correspondant": "NomCorrespondant", "devise": "Devise", "num_compte": "NumCompte",
                },
            },
        ],
    }


def test_valid_config_does_not_raise(tmp_path):
    validate_config(_valid_cfg(tmp_path))


def test_not_a_dict_raises():
    with pytest.raises(ConfigError, match="objet valide"):
        validate_config(["not", "a", "dict"])


@pytest.mark.parametrize("missing_key", ["api_id", "input", "fields", "output"])
def test_missing_top_level_key_names_the_key(tmp_path, missing_key):
    cfg = _valid_cfg(tmp_path)
    del cfg[missing_key]
    with pytest.raises(ConfigError, match=missing_key):
        validate_config(cfg)


def test_input_missing_table_name(tmp_path):
    cfg = _valid_cfg(tmp_path)
    cfg["input"] = {}
    with pytest.raises(ConfigError, match="table_name"):
        validate_config(cfg)


def test_output_missing_classification_path(tmp_path):
    cfg = _valid_cfg(tmp_path)
    del cfg["output"]["classification_path"]
    with pytest.raises(ConfigError, match="classification_path"):
        validate_config(cfg)


def test_fields_empty_list_raises(tmp_path):
    cfg = _valid_cfg(tmp_path)
    cfg["fields"] = []
    with pytest.raises(ConfigError, match="fields"):
        validate_config(cfg)


def test_field_missing_name_raises(tmp_path):
    cfg = _valid_cfg(tmp_path)
    del cfg["fields"][0]["name"]
    with pytest.raises(ConfigError, match="name"):
        validate_config(cfg)


def test_field_invalid_type_names_the_field_and_value(tmp_path):
    cfg = _valid_cfg(tmp_path)
    cfg["fields"][0]["type"] = "bogus_type"
    with pytest.raises(ConfigError, match="NomCorrespondant") as exc_info:
        validate_config(cfg)
    assert "bogus_type" in str(exc_info.value)


def test_categorical_field_missing_columns_lists_them(tmp_path):
    cfg = _valid_cfg(tmp_path)
    del cfg["fields"][0]["columns"]["ref_transaction"]
    with pytest.raises(ConfigError, match="ref_transaction"):
        validate_config(cfg)


def test_numeric_field_missing_columns_lists_them(tmp_path):
    cfg = _valid_cfg(tmp_path)
    del cfg["fields"][1]["columns"]["solde_fin"]
    with pytest.raises(ConfigError, match="solde_fin"):
        validate_config(cfg)


def test_categorical_field_missing_referentiel_path(tmp_path):
    cfg = _valid_cfg(tmp_path)
    del cfg["fields"][0]["referentiel_path"]
    with pytest.raises(ConfigError, match="referentiel_path"):
        validate_config(cfg)


def test_categorical_field_referentiel_path_not_found(tmp_path):
    cfg = _valid_cfg(tmp_path)
    cfg["fields"][0]["referentiel_path"] = str(tmp_path / "does_not_exist.json")
    with pytest.raises(ConfigError, match="introuvable"):
        validate_config(cfg)


def test_error_message_includes_source_file_when_given(tmp_path):
    cfg = _valid_cfg(tmp_path)
    del cfg["api_id"]
    with pytest.raises(ConfigError, match="mon_config.yaml"):
        validate_config(cfg, source="mon_config.yaml")
