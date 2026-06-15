"""
tests/test_tools.py

Unit tests for the three FitFindr tools, run with `pytest tests/`.

Each tool's failure mode has at least one test:
  - search_listings    -> no matches returns [] (no exception)
  - suggest_outfit      -> empty wardrobe still returns usable text;
                           LLM failure falls back instead of crashing
  - create_fit_card     -> empty outfit returns a message (no exception);
                           LLM failure falls back instead of crashing

The LLM-backed tools are tested with `monkeypatch` stubbing tools._chat, so
these tests are deterministic and run offline with no GROQ_API_KEY or network.
Live behaviour (real Groq output, caption variation) is exercised separately
in the __main__ smoke test of tools.py, not here.
"""

import pytest

import tools
from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── search_listings (pure, no LLM) ────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0
    # Each result is a listing dict with the documented fields.
    assert all(isinstance(r, dict) and "price" in r and "title" in r for r in results)


def test_search_empty_results():
    # Nonsense query + impossible filters -> empty list, never an exception.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=40)
    assert all(item["price"] <= 40 for item in results)


def test_search_sorted_by_relevance_descending():
    # Results must be ordered best-match-first; verify scores are non-increasing.
    results = search_listings("vintage graphic tee", size=None, max_price=None)
    tokens = tools._tokenize("vintage graphic tee")
    scores = [tools._relevance_score(tokens, r) for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_size_filter_is_token_based():
    # "M" should match listings whose size tokens include "m" (e.g. "S/M"),
    # and must NOT match on the stray "s" inside sizes like "US 8".
    results = search_listings("tee", size="M", max_price=None)
    for item in results:
        assert tools._size_matches("M", item["size"])
    # Guard against the substring false positive directly.
    assert tools._size_matches("M", "S/M") is True
    assert tools._size_matches("M", "XL (oversized)") is False
    assert tools._size_matches("S", "US 8") is False


# ── suggest_outfit (LLM stubbed) ──────────────────────────────────────────────

def test_suggest_outfit_empty_wardrobe(monkeypatch):
    # Empty wardrobe must not crash and must return non-empty advice.
    monkeypatch.setattr(
        tools, "_chat", lambda prompt, **kw: "General advice: pair with neutral basics."
    )
    item = {"title": "Faded Band Tee", "category": "tops",
            "colors": ["black"], "style_tags": ["vintage", "band tee"]}
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str)
    assert out.strip() != ""


def test_suggest_outfit_with_wardrobe(monkeypatch):
    monkeypatch.setattr(
        tools, "_chat",
        lambda prompt, **kw: "Pair it with your baggy straight-leg jeans.",
    )
    item = {"title": "Faded Band Tee", "category": "tops",
            "colors": ["black"], "style_tags": ["vintage", "band tee"]}
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str)
    assert out.strip() != ""


def test_suggest_outfit_llm_failure_falls_back(monkeypatch):
    # If the LLM call raises, the tool returns a safe non-empty string.
    def boom(prompt, **kw):
        raise RuntimeError("groq down")

    monkeypatch.setattr(tools, "_chat", boom)
    item = {"title": "Faded Band Tee", "category": "tops",
            "colors": ["black"], "style_tags": ["vintage"]}
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str)
    assert out.strip() != ""


# ── create_fit_card (LLM stubbed) ─────────────────────────────────────────────

def test_create_fit_card_empty_outfit_returns_message():
    # Empty outfit must return a descriptive string, not raise — and no LLM call.
    item = {"title": "Faded Band Tee", "price": 22, "platform": "depop"}
    out = create_fit_card("", item)
    assert isinstance(out, str)
    assert out.strip() != ""
    assert "Faded Band Tee" in out


def test_create_fit_card_whitespace_outfit_returns_message():
    item = {"title": "Faded Band Tee", "price": 22, "platform": "depop"}
    out = create_fit_card("   \n  ", item)
    assert isinstance(out, str)
    assert out.strip() != ""


def test_create_fit_card_happy_path(monkeypatch):
    monkeypatch.setattr(
        tools, "_chat",
        lambda prompt, **kw: "Snagged this band tee for $22 on depop 🖤",
    )
    item = {"title": "Faded Band Tee", "price": 22, "platform": "depop"}
    out = create_fit_card("Wear with baggy jeans and chunky sneakers.", item)
    assert isinstance(out, str)
    assert out.strip() != ""


def test_create_fit_card_llm_failure_falls_back(monkeypatch):
    def boom(prompt, **kw):
        raise RuntimeError("groq down")

    monkeypatch.setattr(tools, "_chat", boom)
    item = {"title": "Faded Band Tee", "price": 22, "platform": "depop"}
    out = create_fit_card("Wear with baggy jeans.", item)
    assert isinstance(out, str)
    assert out.strip() != ""
    assert "Faded Band Tee" in out
