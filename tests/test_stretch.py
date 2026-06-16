"""
tests/test_stretch.py

Tests for the three stretch features:
  A. estimate_price_fairness  (price comparison tool)
  B. retry-with-fallback      (planning loop loosens constraints on empty search)
  C. style profile memory     (cross-session wardrobe persistence)

search_listings is pure/offline; the LLM tools are stubbed via monkeypatch so
the agent loop runs deterministically without network.
"""

import agent
from agent import run_agent, _search_with_fallback, _parse_query
from tools import estimate_price_fairness, search_listings
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe
from utils import profile_store


# ── A. estimate_price_fairness ────────────────────────────────────────────────

def test_price_fairness_returns_expected_shape():
    item = search_listings("vintage graphic tee", size=None, max_price=None)[0]
    result = estimate_price_fairness(item)
    for key in ("verdict", "message", "item_price", "comparable_count",
                "median_price", "min_price", "max_price"):
        assert key in result
    assert result["verdict"] in {"great deal", "fair price", "priced high", "unknown"}


def test_price_fairness_cheap_item_is_great_deal():
    # A cheap top among many pricier comparable tops should read as a great deal.
    item = {"id": "test_cheap", "title": "Test Tee", "category": "tops",
            "style_tags": ["vintage", "graphic tee"], "price": 5.0}
    result = estimate_price_fairness(item)
    assert result["comparable_count"] >= 2
    assert result["verdict"] == "great deal"
    assert result["item_price"] == 5.0


def test_price_fairness_expensive_item_is_priced_high():
    item = {"id": "test_pricey", "title": "Test Tee", "category": "tops",
            "style_tags": ["vintage", "graphic tee"], "price": 500.0}
    result = estimate_price_fairness(item)
    assert result["verdict"] == "priced high"


def test_price_fairness_unknown_when_too_few_comparables():
    # A category/tag combo with no peers -> unknown, not a crash or a guess.
    item = {"id": "test_unique", "title": "One of a kind", "category": "tops",
            "style_tags": ["a_tag_that_matches_nothing_xyz"], "price": 25.0}
    result = estimate_price_fairness(item)
    assert result["verdict"] == "unknown"
    assert result["median_price"] is None


# ── B. retry with fallback ────────────────────────────────────────────────────

def test_fallback_no_loosening_on_exact_match():
    parsed = _parse_query("vintage graphic tee under $30")
    results, adjustments = _search_with_fallback(parsed)
    assert results
    assert adjustments == []   # exact match needs no loosening


def test_fallback_drops_size_when_needed():
    # A real keyword with an impossible size -> size gets dropped, results return.
    parsed = {"description": "track jacket", "size": "XXS", "max_price": None}
    results, adjustments = _search_with_fallback(parsed)
    assert results                                  # found after loosening
    assert any("size" in a for a in adjustments)


def test_agent_records_search_note_when_loosened(monkeypatch):
    monkeypatch.setattr(agent, "suggest_outfit", lambda i, w: "outfit")
    monkeypatch.setattr(agent, "create_fit_card", lambda o, i: "card")
    # impossible size forces the loop to loosen and disclose it
    session = run_agent("track jacket size XXS", get_example_wardrobe())
    assert session["error"] is None
    assert session["adjustments"]                    # something was loosened
    assert session["search_note"] is not None
    assert "removed the size filter" in session["search_note"]


def test_agent_still_errors_when_nothing_matches(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(agent, "suggest_outfit",
                        lambda i, w: called.__setitem__("n", called["n"] + 1))
    # gibberish keyword matches nothing even fully loosened
    session = run_agent("zzzqqq nonexistent garment", get_example_wardrobe())
    assert session["search_results"] == []
    assert session["error"] is not None
    assert session["fit_card"] is None
    assert called["n"] == 0                           # LLM tools never called


# ── C. style profile memory ───────────────────────────────────────────────────

def test_profile_save_and_load_roundtrip(tmp_path):
    base = str(tmp_path)
    wardrobe = get_example_wardrobe()
    profile_store.save_profile("hannah", wardrobe, base_dir=base)
    loaded = profile_store.load_profile("hannah", base_dir=base)
    assert loaded is not None
    assert loaded["user_id"] == "hannah"
    assert loaded["wardrobe"]["items"] == wardrobe["items"]
    assert isinstance(loaded["style_preferences"], list)
    assert loaded["style_preferences"]                # derived from the wardrobe


def test_load_missing_profile_returns_none(tmp_path):
    assert profile_store.load_profile("nobody", base_dir=str(tmp_path)) is None


def test_derive_style_preferences_counts_tags():
    wardrobe = get_example_wardrobe()
    prefs = profile_store.derive_style_preferences(wardrobe)
    assert isinstance(prefs, list) and len(prefs) > 0


def test_agent_loads_remembered_wardrobe(monkeypatch, tmp_path):
    # Point the profile store at a temp dir and pre-save a wardrobe.
    base = str(tmp_path)
    monkeypatch.setattr(profile_store, "_PROFILES_DIR", base)
    profile_store.save_profile("hannah", get_example_wardrobe(), base_dir=base)

    captured = {}

    def spy_suggest(item, wardrobe):
        captured["wardrobe"] = wardrobe
        return "outfit"

    monkeypatch.setattr(agent, "suggest_outfit", spy_suggest)
    monkeypatch.setattr(agent, "create_fit_card", lambda o, i: "card")

    # No wardrobe passed, but a user_id -> the loop should load the saved one.
    session = run_agent("vintage graphic tee under $30", wardrobe=None, user_id="hannah")
    assert session["error"] is None
    assert captured["wardrobe"]["items"]              # remembered wardrobe was used
