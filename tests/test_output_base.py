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


def test_output_base_empty_uses_local_dir_in_repo(monkeypatch, tmp_path_factory):
    monkeypatch.delenv("OUTPUT_BASE", raising=False)

    cfg = load_config("e11_rdcc/config/E11_RDCC.yaml")
    try:
        result = E11Pipeline(cfg).run(mode="auto", override_input="tests/fixtures/e11_rdcc_sample.csv")
        assert result["status"] == "OK"
        assert result["path"].resolve().is_relative_to((REPO_ROOT / "e11_rdcc" / "outputs").resolve())
    finally:
        for f in (REPO_ROOT / "e11_rdcc" / "outputs").glob("E11_RDCC_*"):
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
