import shutil
from pathlib import Path

import pytest

_REAL_REFERENTIEL_DIR = Path(__file__).parent.parent / "e11_rdcc" / "referentiel"


@pytest.fixture(autouse=True)
def _no_external_calls(monkeypatch):
    """Empêche tout test unitaire d'appeler accidentellement une vraie API Claude
    ou un vrai SMTP/SharePoint — les tests qui en ont besoin monkeypatchent
    explicitement les fonctions concernées."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SHAREPOINT_TENANT_ID", raising=False)


@pytest.fixture
def isolated_referentiel_dir(tmp_path, monkeypatch):
    """Redirige les référentiels ET les caches warm-start (Devise + NomCorrespondant)
    vers un dossier temporaire — aucun test ne doit JAMAIS écrire dans le vrai
    référentiel du repo. Les référentiels de base (lecture seule) sont copiés une
    fois ; le cache warm-start écrit par le test vit uniquement en tmp_path."""
    import e11_rdcc.fields.devise as devise_mod
    import e11_rdcc.fields.nomcorrespondant as nomcorr_mod

    shutil.copy(_REAL_REFERENTIEL_DIR / "devise_referentiel.json", tmp_path / "devise_referentiel.json")
    shutil.copy(_REAL_REFERENTIEL_DIR / "nomcorrespondant_referentiel_E11.json",
                tmp_path / "nomcorrespondant_referentiel_E11.json")
    monkeypatch.setattr(devise_mod, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(nomcorr_mod, "_REFERENTIEL_DIR", tmp_path)
    return tmp_path
