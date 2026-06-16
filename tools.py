"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re
import statistics

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

# Model used for both LLM-backed tools.
_MODEL = "llama-3.3-70b-versatile"


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(prompt: str, *, temperature: float, max_tokens: int = 300) -> str:
    """
    Send a single user prompt to the Groq chat model and return the text.

    Centralizes the API call so both LLM tools share one code path. Raises on
    any API/transport error — callers are responsible for catching and falling
    back so the agent never crashes on an LLM failure.
    """
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


# ── search helpers (pure, no LLM) ─────────────────────────────────────────────

# Common filler words stripped from the query so they don't inflate relevance.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "under", "over", "my", "me",
    "i", "im", "to", "of", "in", "on", "is", "it", "this", "that", "looking",
    "want", "wanting", "need", "some", "please", "find", "show", "size",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, and drop stopwords."""
    return [
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if t and t not in _STOPWORDS
    ]


def _size_tokens(size_str: str) -> set[str]:
    """Split a listing's size string into discrete tokens.

    Splits on '/', whitespace, and parentheses so "S/M" -> {"s", "m"},
    "XL (oversized)" -> {"xl", "oversized"}, and "US 8" -> {"us", "8"}.
    """
    return {t for t in re.split(r"[\s/()]+", (size_str or "").lower()) if t}


def _size_matches(requested: str | None, listing_size: str) -> bool:
    """Token-based size match (not raw substring) to avoid false positives.

    A listing passes only if the requested size equals one of its size tokens,
    so "M" matches "S/M" and "M/L" but not "XL", and not the "S" inside
    "US 8" or "One Size". An empty/None request matches everything.
    """
    req = (requested or "").strip().lower()
    if not req:
        return True
    return req in _size_tokens(listing_size)


def _relevance_score(query_tokens: list[str], listing: dict) -> int:
    """Score keyword overlap of the query against a listing.

    Style-tag hits are weighted higher (+2) than title/description hits (+1),
    since tags are the most deliberate descriptors. A listing with no token
    overlap scores 0 and is dropped by the caller.
    """
    tag_text = " ".join(listing.get("style_tags", []) or []).lower()
    tag_tokens = set(_tokenize(tag_text))
    body_text = (
        f"{listing.get('title', '')} {listing.get('description', '')}".lower()
    )
    body_tokens = set(_tokenize(body_text))

    score = 0
    for tok in set(query_tokens):
        if tok in tag_tokens or tok in tag_text:
            score += 2
        elif tok in body_tokens or tok in body_text:
            score += 1
    return score


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()
    query_tokens = _tokenize(description)

    scored: list[tuple[int, dict]] = []
    for listing in listings:
        # 1. Price filter (inclusive ceiling).
        if max_price is not None and listing["price"] > max_price:
            continue
        # 2. Size filter (token-based, case-insensitive).
        if not _size_matches(size, listing.get("size", "")):
            continue
        # 3. Relevance score; drop anything with no keyword overlap.
        score = _relevance_score(query_tokens, listing)
        if score == 0:
            continue
        scored.append((score, listing))

    # 4. Sort by score, highest first. Python's sort is stable, so listings
    #    with equal scores keep their original dataset order (deterministic).
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [listing for _, listing in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_summary = (
        f"- Title: {new_item.get('title', 'Unknown item')}\n"
        f"- Category: {new_item.get('category', 'n/a')}\n"
        f"- Colors: {', '.join(new_item.get('colors', []) or []) or 'n/a'}\n"
        f"- Style tags: {', '.join(new_item.get('style_tags', []) or []) or 'n/a'}"
    )

    items = (wardrobe or {}).get("items", []) or []

    if not items:
        # Empty-wardrobe path: general styling advice, no invented pieces.
        prompt = (
            "You are a thrift-fashion stylist. A shopper is considering this "
            "secondhand item but has not entered any wardrobe yet:\n\n"
            f"{item_summary}\n\n"
            "Give 1-2 short, concrete styling suggestions for this piece in "
            "general terms — what categories, colors, and silhouettes pair well "
            "and what overall vibe it suits. Do NOT reference specific items the "
            "shopper owns, since their closet is empty. Keep it to 2-3 sentences, "
            "casual and practical."
        )
    else:
        # Populated-wardrobe path: name real pieces from their closet.
        wardrobe_lines = []
        for it in items:
            colors = ", ".join(it.get("colors", []) or [])
            tags = ", ".join(it.get("style_tags", []) or [])
            notes = it.get("notes")
            line = f"- {it.get('name', 'item')} ({it.get('category', 'n/a')})"
            extra = "; ".join(p for p in [colors, tags] if p)
            if extra:
                line += f" — {extra}"
            if notes:
                line += f" [{notes}]"
            wardrobe_lines.append(line)
        wardrobe_text = "\n".join(wardrobe_lines)

        prompt = (
            "You are a thrift-fashion stylist. A shopper is considering this "
            "secondhand item:\n\n"
            f"{item_summary}\n\n"
            "Here is their current wardrobe:\n"
            f"{wardrobe_text}\n\n"
            "Suggest 1-2 complete outfits pairing the new item with specific "
            "pieces NAMED from their wardrobe above. Reference real items by "
            "name. Add a quick styling tip (tuck, roll, layer) if useful. Keep "
            "it to 2-3 sentences, casual and practical."
        )

    # Safe fallback so create_fit_card always receives usable, non-empty text.
    fallback = (
        "Couldn't generate a full outfit, but this piece works as a statement "
        "layer — pair it with neutral basics and let it lead."
    )
    try:
        result = _chat(prompt, temperature=0.7, max_tokens=300)
        return result if result.strip() else fallback
    except Exception:
        return fallback


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # 1. Guard: no usable outfit text -> descriptive message, no LLM call.
    if not outfit or not outfit.strip():
        title = new_item.get("title", "this piece")
        price = new_item.get("price", "?")
        platform = new_item.get("platform", "the marketplace")
        return (
            f"No outfit suggestion was available, so I couldn't write a fit "
            f"card — but this {title} (${price}, {platform}) is a strong solo "
            f"piece worth grabbing."
        )

    title = new_item.get("title", "this thrifted piece")
    price = new_item.get("price", "?")
    platform = new_item.get("platform", "a resale app")

    # 2. Build the caption prompt.
    prompt = (
        "Write a short, casual social-media caption (Instagram/TikTok OOTD "
        "style) for a thrifted fashion find. Make it sound like a real person "
        "posting, not a product description.\n\n"
        f"Item: {title}\n"
        f"Price: ${price}\n"
        f"Platform: {platform}\n"
        f"Outfit / styling: {outfit}\n\n"
        "Requirements: 2-4 sentences. Mention the item name, the price, and the "
        "platform naturally, once each. Capture the outfit vibe in specific "
        "terms. Keep it authentic and a little fun. Emoji are okay but optional."
    )

    # 3. Higher temperature so repeated calls vary; fallback template on failure.
    fallback = (
        f"Thrifted this {title} for ${price} on {platform} — styling notes "
        "coming soon."
    )
    try:
        result = _chat(prompt, temperature=1.0, max_tokens=200)
        return result if result.strip() else fallback
    except Exception:
        return fallback


# ── Tool 4 (stretch): estimate_price_fairness ─────────────────────────────────

def estimate_price_fairness(item: dict) -> dict:
    """
    Estimate whether a listing's price is fair vs. comparable items in the
    dataset. Pure local function — no LLM, never raises.

    "Comparable" = a different listing in the same category that shares at least
    one style_tag with `item`. The item's price is compared to the median of
    those comparables.

    Args:
        item: A listing dict (typically the selected search result). Uses its
              id, category, style_tags, price, and title.

    Returns:
        A dict with:
            verdict          (str): "great deal" | "fair price" | "priced high"
                                    | "unknown"
            message          (str): human-readable one-liner
            item_price       (float)
            comparable_count (int)
            median_price     (float | None)
            min_price        (float | None)
            max_price        (float | None)

        If fewer than 2 comparables exist, verdict is "unknown" and the price
        stats are None — it does not guess.
    """
    item_price = float(item.get("price", 0.0))
    item_id = item.get("id")
    category = item.get("category")
    item_tags = set(item.get("style_tags", []) or [])

    comparables = []
    for other in load_listings():
        if other.get("id") == item_id:
            continue
        if other.get("category") != category:
            continue
        if not item_tags.intersection(other.get("style_tags", []) or []):
            continue
        comparables.append(other)

    prices = [float(c["price"]) for c in comparables]

    # Not enough data to judge -> say so honestly rather than guess.
    if len(prices) < 2:
        return {
            "verdict": "unknown",
            "message": (
                f"Not enough comparable listings to judge the ${item_price:.0f} "
                f"price (found {len(prices)})."
            ),
            "item_price": item_price,
            "comparable_count": len(prices),
            "median_price": None,
            "min_price": None,
            "max_price": None,
        }

    median_price = statistics.median(prices)
    ratio = item_price / median_price if median_price else 1.0

    if ratio <= 0.85:
        verdict = "great deal"
        message = (
            f"Great deal — ${item_price:.0f} is below the ${median_price:.0f} "
            f"median for {len(prices)} similar pieces."
        )
    elif ratio <= 1.15:
        verdict = "fair price"
        message = (
            f"Fairly priced — ${item_price:.0f} is right around the "
            f"${median_price:.0f} median for similar pieces."
        )
    else:
        verdict = "priced high"
        message = (
            f"A bit high — ${item_price:.0f} is above the ${median_price:.0f} "
            f"median for {len(prices)} similar pieces."
        )

    return {
        "verdict": verdict,
        "message": message,
        "item_price": item_price,
        "comparable_count": len(prices),
        "median_price": median_price,
        "min_price": min(prices),
        "max_price": max(prices),
    }
