"""
Une correction manuelle doit être RETROUVÉE par le champ, quelle que soit la
convention de recherche de son cache warm-start.

Cas réel constaté le 27/09/2026 en testant la boucle Instructions d'E10 : une
correction sur NatureEconomique n'était jamais appliquée (aucun message) car la clé
enregistrée passait par `nettoyer()` (accents, ponctuation et tokens numériques
retirés) alors que le champ cherche la valeur BRUTE en majuscules. Même fragilité
sur Devise (recherche sur la valeur brute) et Pays. `apply_corrections` enregistre
donc la forme nettoyée ET la valeur brute (telle quelle et en majuscules).
"""
import pandas as pd
import pytest


def _instructions(path, lignes) -> None:
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        pd.DataFrame(lignes, columns=["Champ", "Input", "Label_Attendu"]).to_excel(
            writer, sheet_name="Instructions", index=False)


def test_devise_correction_saisie_en_minuscules(tmp_path, isolated_referentiel_dir):
    """Le testeur saisit la valeur telle qu'il la lit, sans respecter la casse."""
    from e11_rdcc.apply_corrections import apply_corrections
    from e11_rdcc.fields.devise import treating_devise

    xlsx = tmp_path / "corrections.xlsx"
    _instructions(xlsx, [{"Champ": "Devise", "Input": " zzz ", "Label_Attendu": "eur"}])
    apply_corrections(xlsx, "E11_RDCC", "e11_rdcc/config/E11_RDCC.yaml")

    out = treating_devise(pd.DataFrame({"Devise": ["zzz"], "NomCorrespondant": ["NA"]}),
                          api_id="E11_RDCC", warm_start=True)
    assert out.loc[0, "Devise_Normalisée"] == "EUR"
    assert out.loc[0, "Devise_method"] == "WARM"


def test_nature_economique_correction_avec_nettoyage_destructif(tmp_path, monkeypatch):
    """Valeur dont le nettoyage retire accents, ponctuation et nombre : la correction
    doit tout de même s'appliquer au run suivant."""
    import shared.corrections_history as histo
    import e10_fe.fields.beneficiaire as benef_mod
    import e10_fe.fields.nature_economique as nat_mod
    import e10_fe.fields.nomdonneurordre as ndo_mod
    import e10_fe.fields.pays as pays_mod
    import e10_fe.fields.devise as devise_mod
    import e10_fe.fields.mode_reglement as mode_mod
    import e10_fe.fields.typeswift as swift_mod
    from e10_fe.apply_corrections import apply_corrections

    # Base DGI réduite : le vrai fichier (52 000 lignes) n'apporte rien à ce test.
    monkeypatch.setattr(benef_mod, "load_dgi_base", lambda *a, **k: pd.DataFrame(
        {"RAISON_SOCIALE": ["SOCIETE EXEMPLE SARL"], "NIF": ["01371954"], "FORME_JURIDIQUE": ["SARL"]}))
    monkeypatch.setattr(benef_mod, "load_public_entities", lambda *a, **k: pd.DataFrame(
        {"Short Name": ["SNIM"], "Raison social - Public Ent": ["Societe Nationale Industrielle et Miniere"]}))
    monkeypatch.setattr(ndo_mod, "load_public_entities", lambda *a, **k: pd.DataFrame(
        {"Short Name": ["SNIM"], "Raison social - Public Ent": ["Societe Nationale Industrielle et Miniere"]}))

    referentiel = tmp_path / "e10_fe" / "referentiel"
    referentiel.mkdir(parents=True)
    monkeypatch.setattr(histo, "_REPO_ROOT", tmp_path)
    for mod in (nat_mod, pays_mod, devise_mod, mode_mod, swift_mod, ndo_mod, benef_mod):
        monkeypatch.setattr(mod, "_REFERENTIEL_DIR", referentiel)

    brut = "Frais d'hôtel 2024"
    xlsx = tmp_path / "corrections.xlsx"
    _instructions(xlsx, [{"Champ": "NatureEconomique", "Input": brut, "Label_Attendu": "AUTRES"}])
    applied = apply_corrections(xlsx, "E10_FE", "e10_fe/config/E10_FE.yaml")

    assert nat_mod.nettoyer(brut) != brut.upper(), "cas de test : le nettoyage doit modifier la valeur"
    assert applied["NatureEconomique"][brut] == "AUTRES"

    ref = nat_mod.load_nature_economique_referentiel(
        "e10_fe/referentiel/nature_economique_referentiel_E10.json")
    out = nat_mod.treating_nature_economique(
        pd.DataFrame({"NatureEconomique": [brut], "ReferenceTransaction": ["TX1"], "RefBanque": ["B1"]}),
        ref=ref, api_id="E10_FE", warm_start=True,
    )
    assert out.loc[0, "NatureEconomique_Normalisé"] == "AUTRES"
    assert out.loc[0, "NatureEconomique_method"] == "WARM"


def test_ligne_sans_label_reste_ignoree(tmp_path, isolated_referentiel_dir, capsys):
    from e11_rdcc.apply_corrections import apply_corrections

    xlsx = tmp_path / "corrections.xlsx"
    _instructions(xlsx, [{"Champ": "Devise", "Input": "QQQ", "Label_Attendu": ""}])
    applied = apply_corrections(xlsx, "E11_RDCC", "e11_rdcc/config/E11_RDCC.yaml")

    assert applied["Devise"] == {}
    assert "1 ligne(s) ignorée(s)" in capsys.readouterr().out
