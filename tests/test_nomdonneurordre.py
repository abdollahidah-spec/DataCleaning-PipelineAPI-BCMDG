"""
Vérifie la cascade NomDonneurOrdre (e08_ocd/fields/nomdonneurordre.py) : chaque
étape de résolution (MAP, NIF exact, entreprise publique, classification locale,
matching DGI déterministe/flou, arbitrage Claude) sur un référentiel/index
synthétique — jamais la vraie base DGI (52k lignes, trop lent pour un test
unitaire ; déjà vérifiée contre les vraies données manuellement).
"""
import pandas as pd
import pytest

from e08_ocd.fields._entity_matching import prepare_dgi_index, prepare_public_ent_index
from e08_ocd.fields.nomdonneurordre import treating_nomdonneurordre


def _dgi_index() -> dict:
    df = pd.DataFrame({
        "RAISON_SOCIALE": ["SOCIETE EXEMPLE DE TEST SARL", "SOCIETE EXEMPLE VOISINE SARL"],
        "NIF": ["01371954", "01371955"],
        "FORME_JURIDIQUE": ["SARL", "SARL"],
    })
    return prepare_dgi_index(df)


def _public_index() -> dict:
    df = pd.DataFrame({
        "Short Name": ["SNIM"],
        "Raison social - Public Ent": ["Société Nationale Industrielle et Minière"],
    })
    return prepare_public_ent_index(df)


def _row(**kwargs) -> dict:
    base = {"RefBanque": "B1", "NumCredoc": "CD1", "NomDonneurOrdre": "", "NifNni": ""}
    base.update(kwargs)
    return base


def _run(rows, ref=None, cfg=None, warm_start=False, **kwargs):
    df = pd.DataFrame(rows)
    return treating_nomdonneurordre(
        df, ref=ref or {}, dgi_index=_dgi_index(), public_index=_public_index(),
        api_id="TEST_API", cfg=cfg or {}, warm_start=warm_start, **kwargs,
    )


def test_referentiel_map_takes_priority():
    out = _run([_row(NomDonneurOrdre="ABC")], ref={"ABC": "ABC LEGAL NAME"})
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "ABC LEGAL NAME"
    assert out["NomDonneurOrdre_method"].iloc[0] == "MAP"


def test_nif_exact_match():
    out = _run([_row(NomDonneurOrdre="SOC EXEMPLE TEST", NifNni="01371954")])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "SOCIETE EXEMPLE DE TEST SARL"
    assert out["NomDonneurOrdre_method"].iloc[0] == "NIF_EXACT"


def test_invalid_nif_does_not_short_circuit():
    """NIF invalide (7 chiffres) -> pas de match NIF, continue la cascade normale."""
    out = _run([_row(NomDonneurOrdre="Mohamed Ould Ahmed", NifNni="1234567")])
    assert out["NomDonneurOrdre_method"].iloc[0] == "PARTICULIER"


def test_public_entity_match():
    out = _run([_row(NomDonneurOrdre="SNIM")])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "SNIM"
    assert out["NomDonneurOrdre_method"].iloc[0] == "PUBLIC_ENT"


def test_particulier_classification():
    out = _run([_row(NomDonneurOrdre="Mohamed Ould Ahmed")])
    assert out["NomDonneurOrdre_method"].iloc[0] == "PARTICULIER"


def test_dgi_exact_norm_match():
    """Libellé qui, une fois hyper-normalisé, correspond exactement à une raison
    sociale DGI — sans passer par le score flou."""
    out = _run([_row(NomDonneurOrdre="Societe Exemple de Test SARL.")])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "SOCIETE EXEMPLE DE TEST SARL"
    assert out["NomDonneurOrdre_method"].iloc[0] == "DGI_EXACT_NORM"


def test_dgi_no_match_below_arbitrage_min():
    out = _run([_row(NomDonneurOrdre="ENTREPRISE TOTALEMENT SANS RAPPORT ZZZ")])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "OUTLIER"
    assert out["NomDonneurOrdre_method"].iloc[0] == "DGI_NO_MATCH"


def test_na_rule_legit_when_ref_also_na():
    out = _run([_row(NomDonneurOrdre="NA", NumCredoc="NA")])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "NA"


def test_na_rule_outlier_when_ref_not_na():
    out = _run([_row(NomDonneurOrdre="NA", NumCredoc="CD1")])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "OUTLIER"


def test_evident_outlier_short_circuits():
    out = _run([_row(NomDonneurOrdre="STRING")])
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "OUTLIER"
    assert out["NomDonneurOrdre_method"].iloc[0] == "OUTLIER"


def _ambiguous_dgi_index() -> dict:
    """Deux candidats à score rapidfuzz IDENTIQUE (gap=0 < strong_gap=8) contre la
    requête ci-dessous -> ni match fort ni no-match, tombe dans la zone d'arbitrage."""
    df = pd.DataFrame({
        "RAISON_SOCIALE": ["MOHAMED TRADING COMPANY SARL", "MOHAMED TRADING GROUP SARL"],
        "NIF": ["01371954", "01371955"],
        "FORME_JURIDIQUE": ["SARL", "SARL"],
    })
    return prepare_dgi_index(df)


def test_claude_arbitrage_ambiguous_candidate(monkeypatch, tmp_path):
    """Deux raisons sociales DGI à score identique (ambigu, ni fort ni faible) ->
    arbitrage Claude, mocké ici (jamais de vrai appel réseau)."""
    import e08_ocd.fields.nomdonneurordre as ndo_mod

    monkeypatch.setattr(ndo_mod, "_REFERENTIEL_DIR", tmp_path)  # jamais écrire dans le vrai référentiel

    def fake_arbitrage(items, cfg):
        assert len(items) == 1
        assert len(items[0]["candidates"]) >= 2
        return [1]  # choisit le 1er candidat

    monkeypatch.setattr(ndo_mod, "call_claude_dgi_arbitrage_batch", fake_arbitrage)

    df = pd.DataFrame([_row(NomDonneurOrdre="Mohamed Trading Sarl")])
    out = treating_nomdonneurordre(
        df, ref={}, dgi_index=_ambiguous_dgi_index(), public_index=_public_index(),
        api_id="TEST_API", cfg={}, warm_start=False,
    )
    assert out["NomDonneurOrdre_method"].iloc[0] == "DGI_CLAUDE_ARBITRAGE"
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] != "OUTLIER"


def test_claude_arbitrage_technical_failure_not_cached(monkeypatch, tmp_path):
    import e08_ocd.fields.nomdonneurordre as ndo_mod

    monkeypatch.setattr(ndo_mod, "_REFERENTIEL_DIR", tmp_path)
    monkeypatch.setattr(ndo_mod, "call_claude_dgi_arbitrage_batch", lambda *a, **k: None)

    df = pd.DataFrame([_row(NomDonneurOrdre="Mohamed Trading Sarl")])
    out = treating_nomdonneurordre(
        df, ref={}, dgi_index=_ambiguous_dgi_index(), public_index=_public_index(),
        api_id="TEST_API", cfg={}, warm_start=True,
    )
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "OUTLIER"
    assert not (tmp_path / "validated_classif_nomdonneurordre_test_api.json").exists()


def test_dgi_match_redirected_to_public_entity(monkeypatch):
    """Un match DGI (arbitré) qui désigne en réalité une entreprise publique connue
    doit être redirigé vers son short name canonique."""
    import e08_ocd.fields.nomdonneurordre as ndo_mod

    dgi_df = pd.DataFrame({
        "RAISON_SOCIALE": ["SOCIETE NATIONALE INDUSTRIELLE ET MINIERE VARIANTE"],
        "NIF": ["01371954"],
        "FORME_JURIDIQUE": ["SA"],
    })
    dgi_index = prepare_dgi_index(dgi_df)

    monkeypatch.setattr(ndo_mod, "call_claude_dgi_arbitrage_batch", lambda items, cfg: [1])

    df = pd.DataFrame([_row(NomDonneurOrdre="SOCIETE NATIONALE INDUSTRIELLE MINIERE")])
    out = treating_nomdonneurordre(
        df, ref={}, dgi_index=dgi_index, public_index=_public_index(),
        api_id="TEST_API", cfg={}, warm_start=False,
    )
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "SNIM"
    assert out["NomDonneurOrdre_method"].iloc[0] == "PUBLIC_ENT"


def test_warm_start_cache_hit(monkeypatch, tmp_path):
    import e08_ocd.fields.nomdonneurordre as ndo_mod
    import json

    monkeypatch.setattr(ndo_mod, "_REFERENTIEL_DIR", tmp_path)
    cache_path = tmp_path / "validated_classif_nomdonneurordre_test_api.json"
    cache_path.write_text(json.dumps({"classif": {"UNKNOWN CO": "KNOWN LEGAL NAME"}}), encoding="utf-8")

    out = _run([_row(NomDonneurOrdre="Unknown Co")], warm_start=True)
    assert out["NomDonneurOrdre_Normalisé"].iloc[0] == "KNOWN LEGAL NAME"
    assert out["NomDonneurOrdre_method"].iloc[0] == "WARM"
