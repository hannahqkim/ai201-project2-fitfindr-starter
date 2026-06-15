"""
tests/test_agent.py

Tests for the planning loop in agent.py — focused on the two things the
Milestone 4 checkpoint cares about:

  1. State flows between tools (no re-entry, no hardcoded values): the dict
     selected by search lands in selected_item AND is the exact object passed
     into suggest_outfit; suggest_outfit's output is the exact string passed
     into create_fit_card.
  2. Behavior branches on the search result: with matches, all three tools run
     and error is None; with NO matches, the loop sets an error, leaves
     fit_card None, and never calls suggest_outfit.

search_listings runs for real (pure/offline). The two LLM tools are stubbed via
monkeypatch so the loop is exercised deterministically without network.
"""

import agent
from agent import run_agent, _parse_query
from utils.data_loader import get_example_wardrobe


# ── query parsing ─────────────────────────────────────────────────────────────

def test_parse_extracts_price_size_description():
    parsed = _parse_query("vintage graphic tee under $30, size M")
    assert parsed["max_price"] == 30.0
    assert parsed["size"] == "M"
    assert "vintage graphic tee" in parsed["description"].lower()
    # the price/size phrases should be stripped out of the description
    assert "$" not in parsed["description"]
    assert "size" not in parsed["description"].lower()


def test_parse_numeric_size():
    parsed = _parse_query("black combat boots size 8")
    assert parsed["size"] == "8"
    assert parsed["max_price"] is None


# ── happy path: state flows through all three tools ───────────────────────────

def test_happy_path_state_flows(monkeypatch):
    captured = {}

    def fake_suggest(new_item, wardrobe):
        captured["suggest_item"] = new_item        # what suggest_outfit received
        captured["suggest_wardrobe"] = wardrobe
        return "OUTFIT: baggy jeans + chunky sneakers"

    def fake_fitcard(outfit, new_item):
        captured["fitcard_outfit"] = outfit         # what create_fit_card received
        captured["fitcard_item"] = new_item
        return "FITCARD: thrifted find 🖤"

    monkeypatch.setattr(agent, "suggest_outfit", fake_suggest)
    monkeypatch.setattr(agent, "create_fit_card", fake_fitcard)

    wardrobe = get_example_wardrobe()
    session = run_agent("vintage graphic tee under $30", wardrobe)

    # all outputs populated, no error
    assert session["error"] is None
    assert session["search_results"]
    assert session["selected_item"] is not None
    assert session["outfit_suggestion"] == "OUTFIT: baggy jeans + chunky sneakers"
    assert session["fit_card"] == "FITCARD: thrifted find 🖤"

    # selected_item is the top search result AND the exact object passed onward
    assert session["selected_item"] is session["search_results"][0]
    assert captured["suggest_item"] is session["selected_item"]
    assert captured["suggest_wardrobe"] is wardrobe

    # the outfit string produced is the exact string fed into create_fit_card
    assert captured["fitcard_outfit"] is session["outfit_suggestion"]
    assert captured["fitcard_item"] is session["selected_item"]


# ── branch path: no results -> early exit, LLM tools never called ─────────────

def test_no_results_branch_stops_early(monkeypatch):
    called = {"suggest": 0, "fitcard": 0}

    def fake_suggest(new_item, wardrobe):
        called["suggest"] += 1
        return "should never run"

    def fake_fitcard(outfit, new_item):
        called["fitcard"] += 1
        return "should never run"

    monkeypatch.setattr(agent, "suggest_outfit", fake_suggest)
    monkeypatch.setattr(agent, "create_fit_card", fake_fitcard)

    session = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())

    assert session["search_results"] == []
    assert session["error"] is not None
    assert "No listings found" in session["error"]
    assert session["selected_item"] is None
    assert session["outfit_suggestion"] is None
    assert session["fit_card"] is None
    # the whole point: suggest_outfit / create_fit_card were NOT called
    assert called["suggest"] == 0
    assert called["fitcard"] == 0


def test_behavior_differs_between_inputs(monkeypatch):
    # Same agent, two queries -> one runs the tools, one stops early.
    monkeypatch.setattr(agent, "suggest_outfit", lambda i, w: "outfit")
    monkeypatch.setattr(agent, "create_fit_card", lambda o, i: "card")

    hit = run_agent("vintage graphic tee under $30", get_example_wardrobe())
    miss = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())

    assert hit["error"] is None and hit["fit_card"] == "card"
    assert miss["error"] is not None and miss["fit_card"] is None
