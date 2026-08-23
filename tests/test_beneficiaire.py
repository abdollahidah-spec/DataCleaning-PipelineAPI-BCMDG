"""
Vérifie la cascade Beneficiaire (e08_ocd/fields/beneficiaire.py, E08 = bénéficiaire
ÉTRANGER d'un crédit documentaire import) : référentiel, entreprise publique,
classification locale, fallback Claude + recherche web (mocké, jamais de vrai
appel réseau).
"""
import pandas as pd

from e08_ocd.fields._entity_matching import prepare_public_ent_index
from e08_ocd.fields.beneficiaire import treating_beneficiaire


def _public_index() -> dict:
    df = pd.DataFrame({
        "Short Name": ["SNIM"],
        "Raison social - Public Ent": ["Société Nationale Industrielle et Minière"],
    })
    return prepare_public_ent_index(df)


def _row(**kwargs) -> dict:
    base = {"RefBanque": "B1", "NumCredoc": "CD1", "Beneficiaire": ""}
    base.update(kwargs)
    return base


def _run(rows, ref=None, cfg=None, warm_start=False):
    df = pd.DataFrame(rows)
    return treating_beneficiaire(
        df, ref=ref or {}, public_index=_public_index(),
        api_id="TEST_API", cfg=cfg or {}, warm_start=warm_start,
    )


def test_referentiel_map():
    out = _run([_row(Beneficiaire="ABC")], ref={"ABC": "ABC CONTRACTING"})
    assert out["Beneficiaire_Normalisé"].iloc[0] == "ABC CONTRACTING"
    assert out["Beneficiaire_method"].iloc[0] == "MAP"


def test_public_entity_match():
    out = _run([_row(Beneficiaire="SNIM")])
    assert out["Beneficiaire_Normalisé"].iloc[0] == "SNIM"
    assert out["Beneficiaire_method"].iloc[0] == "PUBLIC_ENT"


def test_particulier_classification():
    out = _run([_row(Beneficiaire="Mohamed Ould Ahmed")])
    assert out["Beneficiaire_method"].iloc[0] == "PARTICULIER"


def test_evident_outlier_short_circuits():
    out = _run([_row(Beneficiaire="STRING")])
    assert out["Beneficiaire_Normalisé"].iloc[0] == "OUTLIER"
    assert out["Beneficiaire_method"].iloc[0] == "OUTLIER"


def test_na_rule_legit_when_ref_also_na():
    out = _run([_row(Beneficiaire="NA", NumCredoc="NA")])
    assert out["Beneficiaire_Normalisé"].iloc[0] == "NA"


def test_na_rule_outlier_when_ref_not_na():
    out = _run([_row(Beneficiaire="NA", NumCredoc="CD1")])
    assert out["Beneficiaire_Normalisé"].iloc[0] == "OUTLIER"


def test_unresolved_value_goes_through_claude_web_fallback(monkeypatch, tmp_path):
    import e08_ocd.fields.beneficiaire as bene_mod

    monkeypatch.setattr(bene_mod, "_REFERENTIEL_DIR", tmp_path)  # jamais écrire dans le vrai référentiel

    def fake_call(batch, cfg):
        assert batch == ["SOME FOREIGN COMPANY XYZ"]
        return ["SOME FOREIGN COMPANY XYZ LTD"]

    monkeypatch.setattr(bene_mod, "call_claude_beneficiaire_web_batch", fake_call)

    out = _run([_row(Beneficiaire="Some Foreign Company XYZ")])
    assert out["Beneficiaire_Normalisé"].iloc[0] == "SOME FOREIGN COMPANY XYZ LTD"
    assert out["Beneficiaire_method"].iloc[0] == "CLAUDE"


def test_claude_web_technical_failure_not_cached(monkeypatch, tmp_path):
    import e08_ocd.fields.beneficiaire as bene_mod

    monkeypatch.setattr(bene_mod, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(bene_mod, "call_claude_beneficiaire_web_batch", lambda *a, **k: None)

    out = _run([_row(Beneficiaire="Truc jamais vu XYZ")], warm_start=True)
    assert out["Beneficiaire_Normalisé"].iloc[0] == "OUTLIER"
    assert not (tmp_path / "validated_classif_beneficiaire_test_api.json").exists()


def test_warm_start_cache_hit(monkeypatch, tmp_path):
    import e08_ocd.fields.beneficiaire as bene_mod
    import json

    monkeypatch.setattr(bene_mod, "_REFERENTIEL_DIR", tmp_path)
    cache_path = tmp_path / "validated_classif_beneficiaire_test_api.json"
    cache_path.write_text(json.dumps({"classif": {"UNKNOWN CO": "KNOWN LEGAL NAME"}}), encoding="utf-8")

    out = _run([_row(Beneficiaire="Unknown Co")], warm_start=True)
    assert out["Beneficiaire_Normalisé"].iloc[0] == "KNOWN LEGAL NAME"
    assert out["Beneficiaire_method"].iloc[0] == "WARM"
