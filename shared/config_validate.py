"""
shared/config_validate.py
============================
Validation du YAML d'une API AVANT toute exécution — un message d'erreur
PRÉCIS (clé exacte manquante, champ concerné, valeur trouvée) plutôt qu'un
KeyError/TypeError brut surgissant en plein milieu du chargement ou du
traitement. Appelée dès la construction du pipeline (BaseApiPipeline.__init__),
donc un YAML cassé échoue immédiatement, avant tout accès réseau/DB.
"""
from __future__ import annotations

from pathlib import Path

from shared.errors import ConfigError

_TOP_LEVEL_REQUIRED = ["api_id", "input", "fields", "output"]
_INPUT_REQUIRED = ["table_name"]
_OUTPUT_REQUIRED = ["local_dir", "classification_path"]
_CATEGORICAL_COLUMNS_REQUIRED = ["field", "field_out", "ref_transaction", "ref_banque"]
# "numeric_coherence" : moteur E11 (cohérence J/J+1, gabarit "sans activité"...).
_NUMERIC_COHERENCE_COLUMNS_REQUIRED = [
    "solde_debut", "mvts_debiteurs", "mvts_crediteurs", "solde_fin", "date_fin",
    "dt_cr", "ref_banque", "nom_correspondant", "devise", "num_compte",
]
# "numeric_validation" : règles ligne-à-ligne simples, sans cohérence inter-lignes
# (ex: E09 — montant > 0, date d'échéance > dtCr). Colonnes propres à chaque API,
# donc pas de schéma fixe partagé avec numeric_coherence — la liste ci-dessous est
# celle d'E09_PE (e09_pe/fields/echeances.py::EcheancesConfig) ; une future API
# utilisant ce même type de moteur devra ajuster cette liste si ses colonnes diffèrent.
_NUMERIC_VALIDATION_COLUMNS_REQUIRED = [
    "montant_echeance", "date_echeance", "dt_cr", "ref_banque", "num_credoc",
]

_TYPE_COLUMNS_REQUIRED = {
    "categorical": _CATEGORICAL_COLUMNS_REQUIRED,
    "numeric_coherence": _NUMERIC_COHERENCE_COLUMNS_REQUIRED,
    "numeric_validation": _NUMERIC_VALIDATION_COLUMNS_REQUIRED,
}


def validate_config(cfg: dict, source: str = "") -> None:
    """Lève ConfigError avec un message précis (clé + emplacement) à la première anomalie trouvée."""
    where = f" (fichier : {source})" if source else ""

    if not isinstance(cfg, dict):
        raise ConfigError(f"Le YAML{where} ne contient pas un objet valide (type trouvé : {type(cfg).__name__}).")

    for key in _TOP_LEVEL_REQUIRED:
        if key not in cfg:
            raise ConfigError(f"Clé obligatoire manquante à la racine du YAML{where} : '{key}'.")

    if not isinstance(cfg.get("api_id"), str) or not cfg["api_id"].strip():
        raise ConfigError(f"'api_id'{where} doit être une chaîne non vide (trouvé : {cfg.get('api_id')!r}).")

    input_cfg = cfg.get("input")
    if not isinstance(input_cfg, dict):
        raise ConfigError(f"'input'{where} doit être un bloc de configuration, trouvé : {type(input_cfg).__name__}.")
    for key in _INPUT_REQUIRED:
        if not input_cfg.get(key):
            raise ConfigError(f"Clé obligatoire manquante ou vide dans 'input'{where} : '{key}'.")

    output_cfg = cfg.get("output")
    if not isinstance(output_cfg, dict):
        raise ConfigError(f"'output'{where} doit être un bloc de configuration, trouvé : {type(output_cfg).__name__}.")
    for key in _OUTPUT_REQUIRED:
        if not output_cfg.get(key):
            raise ConfigError(f"Clé obligatoire manquante ou vide dans 'output'{where} : '{key}'.")

    fields = cfg.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ConfigError(f"'fields'{where} doit être une liste non vide de champs à traiter "
                           f"(trouvé : {type(fields).__name__ if fields is not None else 'absent'}).")

    for i, field_cfg in enumerate(fields):
        if not isinstance(field_cfg, dict):
            raise ConfigError(f"fields[{i}]{where} doit être un bloc de configuration, "
                               f"trouvé : {type(field_cfg).__name__}.")
        name = field_cfg.get("name")
        if not name:
            raise ConfigError(f"fields[{i}]{where} : clé 'name' manquante ou vide.")
        label = f"le champ '{name}' (fields[{i}]){where}"

        ftype = field_cfg.get("type")
        if ftype not in _TYPE_COLUMNS_REQUIRED:
            raise ConfigError(
                f"Type invalide pour {label} : {ftype!r} — attendu l'un de {sorted(_TYPE_COLUMNS_REQUIRED)}."
            )

        columns = field_cfg.get("columns")
        if not isinstance(columns, dict):
            raise ConfigError(f"Bloc 'columns' manquant ou invalide pour {label}.")

        required_cols = _TYPE_COLUMNS_REQUIRED[ftype]
        missing_cols = [c for c in required_cols if not columns.get(c)]
        if missing_cols:
            raise ConfigError(
                f"Colonne(s) manquante(s) ou vide(s) dans 'columns' pour {label} : {missing_cols} "
                f"(clés présentes : {sorted(columns.keys())})."
            )

        if ftype == "categorical":
            ref_path = field_cfg.get("referentiel_path")
            if not ref_path:
                raise ConfigError(f"'referentiel_path' manquant pour {label}.")
            if not Path(ref_path).exists():
                raise ConfigError(
                    f"Référentiel introuvable pour {label} : '{ref_path}' n'existe pas sur le disque "
                    f"(chemin résolu depuis le répertoire courant — vérifie que la commande est bien "
                    f"lancée depuis la racine du repo)."
                )
