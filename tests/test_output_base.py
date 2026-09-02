"""
Vérifie que OUTPUT_BASE (.env) redirige bien le stockage local des livrables
vers un dossier externe, sans dépendre de SharePoint (voir README.md).
"""
from pathlib import Path

from shared.config import load_config
from e11_rdcc.pipeline import E11Pipeline

REPO_ROOT = Path(__file__).parent.parent


def test_output_base_redirects_local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))

    cfg = load_config("e11_rdcc/config/E11_RDCC.yaml")
    result = E11Pipeline(cfg).run(mode="auto", override_input="tests/fixtures/e11_rdcc_sample.csv")

    assert result["status"] == "OK"
    assert result["path"].resolve().is_relative_to(tmp_path.resolve())
    assert result["path"].exists()


def test_output_base_uses_uppercase_api_id_with_no_outputs_subfolder(tmp_path, monkeypatch):
    """OUTPUT_BASE défini : {OUTPUT_BASE}/{API_ID EN MAJUSCULES}/, sans niveau
    "outputs/" intermédiaire — un dossier par API nommé d'après son api_id, pour
    que les 3 API lancées en parallèle n'entrent jamais en collision (retour
    explicite : "supprimer le niveau output et le nom de l'API en majuscules")."""
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))

    cfg = load_config("e11_rdcc/config/E11_RDCC.yaml")
    result = E11Pipeline(cfg).run(mode="auto", override_input="tests/fixtures/e11_rdcc_sample.csv")

    assert result["path"].parent == tmp_path / "E11_RDCC"


def test_pdf_report_goes_to_dedicated_rapport_subfolder(tmp_path, monkeypatch):
    """Le PDF (daté, s'accumule à chaque run) doit être séparé du classeur de
    classification (stable, branché BI, jamais daté/déplacé) — retour explicite :
    sous-dossier "Rapport" dédié, jamais le même dossier que le fichier Excel."""
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path))

    cfg = load_config("e11_rdcc/config/E11_RDCC.yaml")
    result = E11Pipeline(cfg).run(mode="auto", override_input="tests/fixtures/e11_rdcc_sample.csv")

    assert result["path"].parent == tmp_path / "E11_RDCC"  # classeur : PAS dans Rapport/
    assert result["pdf_paths"], "aucun PDF généré, le test ne vérifie rien"
    for pdf_path in result["pdf_paths"]:
        assert pdf_path.parent == tmp_path / "E11_RDCC" / "Rapport"


def test_output_base_empty_uses_local_dir_in_repo(monkeypatch, tmp_path_factory):
    """OUTPUT_BASE non défini DU TOUT (ni process, ni .env) -> dossier du repo.

    Il ne suffit pas de retirer la variable du process : le pipeline recharge
    .env, qui la redéfinirait aussitôt sur un poste où elle est renseignée. On
    neutralise donc aussi ce rechargement, pour tester le vrai cas "aucune
    configuration OUTPUT_BASE nulle part".
    """
    import dotenv

    monkeypatch.delenv("OUTPUT_BASE", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)

    cfg = load_config("e11_rdcc/config/E11_RDCC.yaml")
    try:
        result = E11Pipeline(cfg).run(mode="auto", override_input="tests/fixtures/e11_rdcc_sample.csv")
        assert result["status"] == "OK"
        assert result["path"].resolve().is_relative_to((REPO_ROOT / "e11_rdcc" / "outputs").resolve())
    finally:
        for f in (REPO_ROOT / "e11_rdcc" / "outputs").glob("E11_RDCC_*"):
            f.unlink(missing_ok=True)
        for f in (REPO_ROOT / "e11_rdcc" / "outputs" / "Rapport").glob("Rapport_Qualite_Outliers_E11_RDCC_*"):
            f.unlink(missing_ok=True)


def test_output_base_set_but_empty_string_uses_local_dir_in_repo(monkeypatch):
    """Même bug que STATE_DIR (voir test_state_store.py) : une variable présente
    mais vide dans .env ne doit jamais être confondue avec un chemin "" (= CWD)."""
    monkeypatch.setenv("OUTPUT_BASE", "")

    cfg = load_config("e11_rdcc/config/E11_RDCC.yaml")
    try:
        result = E11Pipeline(cfg).run(mode="auto", override_input="tests/fixtures/e11_rdcc_sample.csv")
        assert result["status"] == "OK"
        assert result["path"].resolve().is_relative_to((REPO_ROOT / "e11_rdcc" / "outputs").resolve())
    finally:
        for f in (REPO_ROOT / "e11_rdcc" / "outputs").glob("E11_RDCC_*"):
            f.unlink(missing_ok=True)
        for f in (REPO_ROOT / "e11_rdcc" / "outputs" / "Rapport").glob("Rapport_Qualite_Outliers_E11_RDCC_*"):
            f.unlink(missing_ok=True)
