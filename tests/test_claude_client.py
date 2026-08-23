"""
Vérifie call_claude_match_batch (shared/claude_client.py) : le garde-fou qui
rejette toute réponse hors de la liste fermée des labels valides — c'est ce qui
distingue cette primitive de call_claude_nomcorrespondant_batch (qui accepte un
label libre). Aucun vrai appel réseau : anthropic.Anthropic est mocké.

Vérifie aussi call_claude_dgi_arbitrage_batch (choix d'un candidat 1/2/3 ou
OUTLIER) et call_claude_beneficiaire_web_batch (résolution + outil web_search
serveur — on vérifie ici seulement que le tool est bien déclaré dans l'appel,
jamais de vraie recherche).
"""
import pytest

from shared.claude_client import (
    call_claude_beneficiaire_web_batch,
    call_claude_dgi_arbitrage_batch,
    call_claude_match_batch,
)


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse(self._text)


class _FakeAnthropic:
    def __init__(self, text):
        self._text = text
        self.messages = _FakeMessages(text)

    def __call__(self, api_key=None):
        return self


def _patch_anthropic(monkeypatch, response_text: str) -> _FakeAnthropic:
    import anthropic
    fake = _FakeAnthropic(response_text)
    monkeypatch.setattr(anthropic, "Anthropic", fake)
    return fake


def test_match_batch_accepts_value_in_valid_list(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    _patch_anthropic(monkeypatch, "1. riz")

    result = call_claude_match_batch(["RIZ BASMATI"], ["riz", "sucres"], "system prompt", cfg={})
    assert result == ["riz"]


def test_match_batch_rejects_hallucinated_value_outside_list(monkeypatch):
    """Le garde-fou clé : une réponse hors liste (hallucination) doit être traitée
    comme non résolue, jamais silencieusement acceptée."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    _patch_anthropic(monkeypatch, "1. patates douces")

    result = call_claude_match_batch(["TRUC"], ["riz", "sucres"], "system prompt", cfg={})
    assert result == [None]


def test_match_batch_outlier_token_maps_to_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    _patch_anthropic(monkeypatch, "1. OUTLIER")

    result = call_claude_match_batch(["TRUC"], ["riz", "sucres"], "system prompt", cfg={})
    assert result == [None]


def test_match_batch_missing_api_key_returns_none_not_a_list(monkeypatch):
    """None (pas une liste) signale un échec TECHNIQUE — l'appelant ne doit
    jamais le confondre avec une liste de verdicts OUTLIER."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = call_claude_match_batch(["TRUC"], ["riz", "sucres"], "system prompt", cfg={})
    assert result is None


def test_match_batch_multiple_items_preserve_order(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    _patch_anthropic(monkeypatch, "1. sucres\n2. OUTLIER\n3. riz")

    result = call_claude_match_batch(["A", "B", "C"], ["riz", "sucres"], "system prompt", cfg={})
    assert result == ["sucres", None, "riz"]


# ---- call_claude_dgi_arbitrage_batch ---------------------------------------------------

def _arbitrage_items() -> list[dict]:
    return [{
        "label": "SOCIETE TEST",
        "candidates": [
            {"raison_sociale": "SOCIETE TEST SARL", "nif": "01371954", "forme_juridique": "SARL"},
            {"raison_sociale": "SOCIETE TESTE SA", "nif": "01371955", "forme_juridique": "SA"},
        ],
    }]


def test_dgi_arbitrage_picks_candidate_index(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    _patch_anthropic(monkeypatch, "1. 2")

    result = call_claude_dgi_arbitrage_batch(_arbitrage_items(), cfg={})
    assert result == [2]


def test_dgi_arbitrage_outlier_maps_to_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    _patch_anthropic(monkeypatch, "1. OUTLIER")

    result = call_claude_dgi_arbitrage_batch(_arbitrage_items(), cfg={})
    assert result == [None]


def test_dgi_arbitrage_missing_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = call_claude_dgi_arbitrage_batch(_arbitrage_items(), cfg={})
    assert result is None


def test_dgi_arbitrage_does_not_declare_web_search_tool(monkeypatch):
    """L'arbitrage DGI est un simple choix textuel entre candidats déjà fournis —
    pas besoin (et pas de coût) de recherche web."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake = _patch_anthropic(monkeypatch, "1. 1")

    call_claude_dgi_arbitrage_batch(_arbitrage_items(), cfg={})
    assert "tools" not in fake.messages.last_kwargs


# ---- call_claude_beneficiaire_web_batch ------------------------------------------------

def test_beneficiaire_web_resolves_and_declares_web_search_tool(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    fake = _patch_anthropic(monkeypatch, "1. FOREIGN SUPPLIER LTD")

    result = call_claude_beneficiaire_web_batch(["FOREIGN SUPPLIER"], cfg={"llm": {"web_search_max_uses": 3}})
    assert result == ["FOREIGN SUPPLIER LTD"]

    tools = fake.messages.last_kwargs["tools"]
    assert tools[0]["type"] == "web_search_20250305"
    assert tools[0]["max_uses"] == 3  # 3 (max_uses) * 1 (taille du batch)


def test_beneficiaire_web_outlier_maps_to_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    _patch_anthropic(monkeypatch, "1. OUTLIER")

    result = call_claude_beneficiaire_web_batch(["TRUC INCONNU"], cfg={})
    assert result == [None]


def test_beneficiaire_web_missing_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = call_claude_beneficiaire_web_batch(["TRUC"], cfg={})
    assert result is None
