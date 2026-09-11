"""
Règle NA vectorisée face aux vrais NULL SQL (None / NaN), et pas seulement aux
chaînes "nan"/"None" : depuis pandas 3, astype(str) conserve les valeurs
manquantes, ce qui faisait échapper un champ NULL à la règle (valeur normalisée
vide au lieu d'OUTLIER). Constaté sur une extraction réelle E07
(NatureEconomique NULL).
"""
import numpy as np
import pandas as pd

from shared.na_rule import apply_na_rule, apply_na_rule_frame


def test_champ_null_devient_outlier_comme_la_version_ligne_a_ligne():
    df = pd.DataFrame({
        "Val": [None, np.nan, "NA", "NA"],
        "Ref": ["R1", "R1", None, "NA"],
        "Iso": [None, np.nan, "OUTLIER", "OUTLIER"],
        "Mth": [None, np.nan, "NOISE", "NOISE"],
    })
    iso, mth = apply_na_rule_frame(df, "Val", "Ref", "Iso", "Mth")

    assert list(iso) == ["OUTLIER", "OUTLIER", "OUTLIER", "NA"]
    assert list(mth) == ["OUTLIER", "OUTLIER", "OUTLIER", "NA"]
    reference = df.apply(lambda r: apply_na_rule(r, "Val", "Ref", "Iso", "Mth"), axis=1)
    assert list(iso) == [r[0] for r in reference]


def test_champ_e07_null_devient_outlier():
    import e07_fs.fields.typeswift as sw

    df = pd.DataFrame({"TypeSwfit": [None, np.nan], "ReferenceTransaction": ["TX1", "TX2"],
                       "RefBanque": ["B1", "B1"]})
    ref = sw.load_typeswift_referentiel("e07_fs/referentiel/typeswift_referentiel.json")
    out = sw.treating_typeswift(df, ref=ref, warm_start=False)
    assert list(out["TypeSwift_Normalisé"]) == ["OUTLIER", "OUTLIER"]
    assert out["TypeSwfit_check"].all()
