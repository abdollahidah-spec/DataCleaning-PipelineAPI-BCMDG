"""
Vérifie l'historique des corrections manuelles (shared/corrections_history.py) :
un fichier par API, jamais partagé, alimenté par `apply_corrections` et affiché
en lecture seule dans l'onglet "Instructions" du classeur.

Exigences métier explicites couvertes ici :
  - l'onglet Instructions est VIDE au tout premier run (personne n'a encore rien
    corrigé) — il ne doit pas afficher des lignes que personne n'a saisies ;
  - chaque API a son PROPRE historique, jamais mélangé avec celui d'une autre ;
  - l'historique s'enrichit au fur et à mesure, pour la traçabilité.
"""
import pytest

from shared.corrections_history import (
    HISTORY_COLS,
    append_corrections,
    history_path,
    load_history_df,
)


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    """Redirige la racine des historiques vers tmp_path — aucun test ne doit
    écrire dans les vrais dossiers referentiel/ du repo."""
    import shared.corrections_history as mod

    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
    return tmp_path


def test_history_is_empty_on_very_first_run(isolated_history):
    """Exigence explicite : au premier run, l'onglet Instructions est vide."""
    df = load_history_df("E11_RDCC")
    assert df.empty
    assert list(df.columns) == HISTORY_COLS


def test_history_accumulates_across_successive_corrections(isolated_history):
    assert append_corrections("E11_RDCC", {"Devise": {"XYZ": "EUR"}}) == 1
    assert append_corrections("E11_RDCC", {"NomCorrespondant": {"BANQUE X": "BANQUE X SA"}}) == 1

    df = load_history_df("E11_RDCC")
    assert len(df) == 2
    assert set(df["Champ"]) == {"Devise", "NomCorrespondant"}
    assert (df["Date"] != "").all()


def test_reapplying_identical_correction_does_not_duplicate(isolated_history):
    """Relancer apply_corrections sur le même fichier ne doit pas gonfler
    l'historique de doublons identiques."""
    append_corrections("E11_RDCC", {"Devise": {"XYZ": "EUR"}})
    added = append_corrections("E11_RDCC", {"Devise": {"XYZ": "EUR"}})

    assert added == 0
    assert len(load_history_df("E11_RDCC")) == 1


def test_changing_an_existing_label_is_logged_as_new_entry(isolated_history):
    """Corriger différemment une valeur déjà corrigée DOIT laisser une trace :
    c'est précisément l'objet de la traçabilité."""
    append_corrections("E11_RDCC", {"Devise": {"XYZ": "EUR"}})
    added = append_corrections("E11_RDCC", {"Devise": {"XYZ": "USD"}})

    assert added == 1
    df = load_history_df("E11_RDCC")
    assert list(df["Label_Attendu"]) == ["EUR", "USD"]


def test_each_api_has_its_own_separate_history(isolated_history):
    """Exigence explicite : jamais d'historique partagé entre APIs."""
    append_corrections("E11_RDCC", {"Devise": {"XYZ": "EUR"}})
    append_corrections("E08_OCD", {"Pays": {"FARANCE": "FR"}})

    e11 = load_history_df("E11_RDCC")
    e08 = load_history_df("E08_OCD")

    assert len(e11) == 1 and len(e08) == 1
    assert e11.iloc[0]["Champ"] == "Devise"
    assert e08.iloc[0]["Champ"] == "Pays"
    assert history_path("E11_RDCC") != history_path("E08_OCD")
    assert load_history_df("E09_PE").empty     # jamais corrigée -> toujours vide


def test_corrupted_history_file_never_breaks_a_run(isolated_history):
    """Un historique illisible est un problème d'affichage, jamais un motif
    d'échec du traitement."""
    path = history_path("E11_RDCC")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ pas du JSON", encoding="utf-8")

    assert load_history_df("E11_RDCC").empty
