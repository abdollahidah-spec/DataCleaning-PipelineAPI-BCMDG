"""
Vérifie la cascade Pays (e08_ocd/fields/pays.py) : référentiel pycountry/babel/
geonamescache + alias manuels (chargé une fois au niveau module, données réelles
— léger, pas besoin de mock), matching flou, mots-clés d'adresse, règle NoAs/
OUTLIER/Namibie, et le fallback Claude (mocké — remplace Ollama/qwen2.5 de
l'ancien repo, voir docstring du module).
"""
import pandas as pd

from e08_ocd.fields.pays import treating_pays


def _row(**kwargs) -> dict:
    base = {"RefBanque": "B1", "NumCredoc": "CD1", "Pays": ""}
    base.update(kwargs)
    return base


def _run(rows, cfg=None, warm_start=False):
    df = pd.DataFrame(rows)
    return treating_pays(df, api_id="TEST_API", cfg=cfg or {}, warm_start=warm_start)


def test_exact_country_name_resolves_via_map():
    out = _run([_row(Pays="France")])
    assert out["Pays_Normalisé"].iloc[0] == "FR"
    assert out["Pays_method"].iloc[0] == "MAP"


def test_french_country_name_variant_resolves():
    out = _run([_row(Pays="Côte d'Ivoire")])
    assert out["Pays_Normalisé"].iloc[0] == "CI"


def test_iso2_code_resolves_directly():
    out = _run([_row(Pays="FR")])
    assert out["Pays_Normalisé"].iloc[0] == "FR"
    assert out["Pays_method"].iloc[0] == "MAP"


def test_misspelled_country_resolves_via_fuzzy():
    out = _run([_row(Pays="Germanyy")])
    assert out["Pays_Normalisé"].iloc[0] == "DE"
    assert out["Pays_method"].iloc[0] == "FUZZY"


def test_non_pays_blacklist_is_outlier():
    out = _run([_row(Pays="IMMOBILIER")])
    assert out["Pays_Normalisé"].iloc[0] == "OUTLIER"


def test_digits_only_is_outlier():
    out = _run([_row(Pays="12345")])
    assert out["Pays_Normalisé"].iloc[0] == "OUTLIER"


# ---- Règle NoAs / OUTLIER / Namibie --------------------------------------------------

def test_both_empty_is_noas():
    out = _run([_row(Pays="", NumCredoc="")])
    assert out["Pays_Normalisé"].iloc[0] == "NoAs"
    assert out["Pays_method"].iloc[0] == "NoAs"


def test_na_with_empty_ref_is_noas_not_namibia():
    out = _run([_row(Pays="NA", NumCredoc="")])
    assert out["Pays_Normalisé"].iloc[0] == "NoAs"


def test_na_with_real_ref_is_namibia():
    """Cas d'ambiguïté du champ Pays : 'NA' est À LA FOIS le témoin NA générique
    et le vrai code ISO-2 de la Namibie — désambiguïsé par la présence d'un
    NumCredoc réel."""
    out = _run([_row(Pays="NA", NumCredoc="CD1")])
    assert out["Pays_Normalisé"].iloc[0] == "NA"
    assert out["Pays_method"].iloc[0] == "MAP"


def test_unresolved_value_with_ref_present_is_outlier_not_noas():
    out = _run([_row(Pays="", NumCredoc="CD1")])
    assert out["Pays_Normalisé"].iloc[0] == "OUTLIER"


# ---- Fallback Claude (remplace Ollama) -------------------------------------------------

def test_unresolved_value_goes_through_claude_fallback(monkeypatch, tmp_path):
    import e08_ocd.fields.pays as pays_mod

    monkeypatch.setattr(pays_mod, "_REFERENTIEL_DIR", tmp_path)  # jamais écrire dans le vrai référentiel

    def fake_call(batch, valid_labels, system_prompt, cfg):
        assert batch == ["CONTRAT COMMERCIAL REF 4521"]
        assert "AE" in valid_labels
        return ["AE"]

    monkeypatch.setattr(pays_mod, "call_claude_match_batch", fake_call)

    out = _run([_row(Pays="CONTRAT COMMERCIAL REF 4521")])
    assert out["Pays_Normalisé"].iloc[0] == "AE"
    assert out["Pays_method"].iloc[0] == "CLAUDE"


def test_claude_fallback_technical_failure_not_cached(monkeypatch, tmp_path):
    import e08_ocd.fields.pays as pays_mod

    monkeypatch.setattr(pays_mod, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(pays_mod, "call_claude_match_batch", lambda *a, **k: None)

    out = _run([_row(Pays="CONTRAT COMMERCIAL REF 4521")], warm_start=True)
    assert out["Pays_Normalisé"].iloc[0] == "OUTLIER"
    assert not (tmp_path / "validated_classif_pays_test_api.json").exists()


def test_claude_fallback_results_are_cached(monkeypatch, tmp_path):
    """Contrairement à l'ancien repo (Ollama, jamais caché) — voir docstring module."""
    import e08_ocd.fields.pays as pays_mod

    monkeypatch.setattr(pays_mod, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(pays_mod, "call_claude_match_batch", lambda *a, **k: ["AE"])

    _run([_row(Pays="CONTRAT COMMERCIAL REF 4521")], warm_start=True)
    cache_path = tmp_path / "validated_classif_pays_test_api.json"
    assert cache_path.exists()
    import json
    assert json.loads(cache_path.read_text(encoding="utf-8"))["classif"]["CONTRAT COMMERCIAL REF 4521"] == "AE"


def test_warm_start_cache_hit(monkeypatch, tmp_path):
    import e08_ocd.fields.pays as pays_mod
    import json

    monkeypatch.setattr(pays_mod, "_REFERENTIEL_DIR", tmp_path)
    cache_path = tmp_path / "validated_classif_pays_test_api.json"
    cache_path.write_text(json.dumps({"classif": {"UNUSUAL VALUE": "DE"}}), encoding="utf-8")

    out = _run([_row(Pays="Unusual Value")], warm_start=True)
    assert out["Pays_Normalisé"].iloc[0] == "DE"
    assert out["Pays_method"].iloc[0] == "WARM"
