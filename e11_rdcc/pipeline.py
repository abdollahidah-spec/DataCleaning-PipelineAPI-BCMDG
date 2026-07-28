"""
e11_rdcc/pipeline.py
======================
Pipeline consolidée E11_RDCC : orchestre NomCorrespondant, Devise (catégoriels)
et la cohérence numérique SoldesRDCC en un seul run — un seul jeu de livrables
pour toute l'API (voir shared/base_api_pipeline.py pour l'orchestration générique).
"""
from __future__ import annotations

from shared.base_api_pipeline import BaseApiPipeline
from shared.field_processor import FieldProcessor

from e11_rdcc.fields.devise import build_devise_processor
from e11_rdcc.fields.nomcorrespondant import build_nomcorrespondant_processor
from e11_rdcc.fields.numeric_coherence import build_numeric_coherence_processor


def build_e11_field_processors(cfg: dict) -> list:
    """
    Construit la liste des FieldProcessor à partir de cfg["fields"].
    `type: categorical` -> le `name` du champ sélectionne le module e11_rdcc.fields.* ;
    `type: numeric_coherence` -> moteur de cohérence générique (voir numeric_coherence.py).

    Point d'extension pour une future API : dupliquer ce fichier avec ses propres
    champs, sans dépendre de celui-ci (décision H du plan).
    """
    processors = []
    for field_cfg in cfg["fields"]:
        ftype = field_cfg["type"]
        name = field_cfg["name"]
        if ftype == "categorical":
            if name == "NomCorrespondant":
                processors.append(build_nomcorrespondant_processor(field_cfg))
            elif name == "Devise":
                processors.append(build_devise_processor(field_cfg))
            else:
                raise ValueError(f"Champ catégoriel inconnu pour E11_RDCC : {name!r}")
        elif ftype == "numeric_coherence":
            processors.append(build_numeric_coherence_processor(field_cfg))
        else:
            raise ValueError(f"Type de champ inconnu : {ftype!r}")
    return processors


class E11Pipeline(BaseApiPipeline):
    def _build_field_processors(self, cfg: dict) -> list[FieldProcessor]:
        return build_e11_field_processors(cfg)
