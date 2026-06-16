"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    estimate_price_fairness,
)
from utils.profile_store import get_remembered_wardrobe, save_profile


# ── query parsing ─────────────────────────────────────────────────────────────

# Letter sizes we recognize as a standalone token (no "size" keyword needed).
_SIZE_WORDS = {"xxs", "xs", "s", "m", "l", "xl", "xxl"}


def _parse_query(query: str) -> dict:
    """Extract {description, size, max_price} from a raw natural-language query.

    Lightweight rules (no LLM):
      - max_price: pulled from "under $30" / "below 30" / "$30" / "30 dollars".
      - size:      pulled from an explicit "size M" / "size 8", else a standalone
                   letter-size token (S, M, L, XL, ...).
      - description: the remaining text with the price/size phrases stripped out,
                   so only real search keywords are left.

    Returns a dict with keys 'description' (str), 'size' (str | None),
    'max_price' (float | None). Swappable for an LLM parse with the same shape.
    """
    text = query.strip()
    low = text.lower()
    max_price: float | None = None
    size: str | None = None
    desc = text

    # --- price ---
    price_match = (
        re.search(r"(?:under|below|less than|<)\s*\$?\s*(\d+(?:\.\d+)?)", low)
        or re.search(r"\$\s*(\d+(?:\.\d+)?)", low)
        or re.search(r"(\d+(?:\.\d+)?)\s*dollars?", low)
    )
    if price_match:
        max_price = float(price_match.group(1))
        desc = re.sub(
            r"(?:under|below|less than|<)\s*\$?\s*\d+(?:\.\d+)?"
            r"|\$\s*\d+(?:\.\d+)?"
            r"|\d+(?:\.\d+)?\s*dollars?",
            "",
            desc,
            flags=re.IGNORECASE,
        )

    # --- size ---
    size_kw = re.search(r"\bsize\s+([a-z0-9]+)\b", low)
    if size_kw:
        size = size_kw.group(1).upper()
        desc = re.sub(r"\bsize\s+[a-z0-9]+\b", "", desc, flags=re.IGNORECASE)
    else:
        for tok in re.findall(r"[a-z]+", low):
            if tok in _SIZE_WORDS:
                size = tok.upper()
                desc = re.sub(rf"\b{re.escape(tok)}\b", "", desc, flags=re.IGNORECASE)
                break

    # --- description cleanup ---
    desc = re.sub(r"\s+", " ", desc).strip(" ,.-")
    return {"description": desc, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
        # ── stretch-feature fields ──
        "adjustments": [],           # retry: constraints that were loosened
        "search_note": None,         # retry: human sentence about loosening
        "price_check": None,         # price-fairness verdict dict for selected_item
    }


# ── retry with fallback (stretch) ─────────────────────────────────────────────

def _search_with_fallback(parsed: dict) -> tuple[list[dict], list[str]]:
    """Search, loosening constraints if nothing matches.

    Escalation order (size is dropped before price, since the dataset's size
    strings are the most common cause of a zero-match):
        1. full constraints (description, size, max_price)
        2. drop size       (description, None, max_price)   [if a size was set]
        3. drop price too  (description, None, None)         [if a price was set]

    Returns (results, adjustments) where `adjustments` lists, in plain English,
    what was loosened to get those results. Empty adjustments = exact match.
    """
    description = parsed["description"]
    size = parsed["size"]
    max_price = parsed["max_price"]

    # Attempt 1 — exactly what the user asked for.
    results = search_listings(description, size, max_price)
    if results:
        return results, []

    adjustments: list[str] = []

    # Attempt 2 — drop the size filter.
    if size is not None:
        adjustments.append(f"removed the size filter ({size})")
        results = search_listings(description, None, max_price)
        if results:
            return results, adjustments

    # Attempt 3 — drop the price filter too.
    if max_price is not None:
        adjustments.append(f"removed the price filter (${max_price:.0f})")
        results = search_listings(description, None, None)
        if results:
            return results, adjustments

    # Nothing worked even fully loosened.
    return [], adjustments


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict | None = None, *, user_id: str | None = None) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py
        user_id:  Optional (stretch: style memory). If given and `wardrobe` is
                  empty/None, the user's remembered wardrobe is loaded from disk.
                  If given alongside a non-empty wardrobe, that wardrobe is saved
                  for next time. Omit it for the plain core behavior.

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Stretch (style memory): resolve the wardrobe from saved profile if needed.
    if user_id is not None:
        if not (wardrobe or {}).get("items"):
            remembered = get_remembered_wardrobe(user_id)
            if remembered is not None:
                wardrobe = remembered
        elif wardrobe and wardrobe.get("items"):
            # A real wardrobe was supplied — remember it for next session.
            try:
                save_profile(user_id, wardrobe)
            except OSError:
                pass  # persistence failure must never break a run

    # Step 1: fresh session — the single source of truth for this interaction.
    session = _new_session(query, wardrobe)

    # Step 2: parse the raw query into structured search parameters.
    session["parsed"] = _parse_query(query)
    parsed = session["parsed"]

    # Step 3: search, with retry-on-empty that loosens constraints (stretch).
    session["search_results"], session["adjustments"] = _search_with_fallback(parsed)
    if session["adjustments"]:
        session["search_note"] = (
            "No exact match, so I " + " and ".join(session["adjustments"]) + "."
        )
    if not session["search_results"]:
        # Early exit: even fully loosened, nothing matched -> set error and STOP.
        # Do not call the LLM tools with empty input; outputs stay None.
        session["error"] = (
            f"No listings found for '{query}', even after loosening the size "
            f"and price filters. Try broader keywords (e.g. 'graphic tee' "
            f"instead of a brand name)."
        )
        return session

    # Step 4: select the top-ranked match.
    session["selected_item"] = session["search_results"][0]

    # Step 4b (stretch): judge whether the price is fair vs. comparable items.
    # Non-fatal context — a "unknown" verdict never stops the run.
    session["price_check"] = estimate_price_fairness(session["selected_item"])

    # Step 5: suggest an outfit from the selected item + wardrobe.
    #         (self-handles the empty-wardrobe case; always returns a string.)
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 6: turn the outfit suggestion into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: success — error stays None, all three outputs populated.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
