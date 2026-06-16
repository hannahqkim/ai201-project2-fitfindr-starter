"""
profile_store.py  (stretch feature: style profile memory)

A tiny persistence layer so a returning user doesn't have to re-enter their
wardrobe every session. One JSON file per user lives in data/profiles/.

A profile looks like:
    {
        "user_id": "hannah",
        "wardrobe": {"items": [...]},          # same shape as the wardrobe schema
        "style_preferences": ["vintage", ...], # most common style_tags, derived
        "updated_at": "2026-06-15T12:00:00Z"
    }

All reads are forgiving: a missing or corrupt profile returns None rather than
raising, so a failed load never breaks an agent run.
"""

import json
import os
from collections import Counter
from datetime import datetime, timezone

# Default location: data/profiles/ next to this file's parent.
_PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "profiles")


def _profile_path(user_id: str, base_dir: str | None = None) -> str:
    """Resolve the JSON path for a user, sanitizing the id to a safe filename."""
    safe = "".join(c for c in str(user_id) if c.isalnum() or c in ("-", "_")).strip()
    safe = safe or "anon"
    directory = base_dir or _PROFILES_DIR
    return os.path.join(directory, f"{safe}.json")


def derive_style_preferences(wardrobe: dict, top_n: int = 5) -> list[str]:
    """Return the most common style_tags across a wardrobe's items.

    This is what lets the agent "remember" a user's taste (e.g. leans
    vintage/streetwear) without them restating it.
    """
    counter: Counter = Counter()
    for item in (wardrobe or {}).get("items", []) or []:
        for tag in item.get("style_tags", []) or []:
            counter[tag] += 1
    return [tag for tag, _ in counter.most_common(top_n)]


def save_profile(user_id: str, wardrobe: dict, base_dir: str | None = None) -> dict:
    """Persist a user's wardrobe (and derived style preferences) to disk.

    Returns the profile dict that was written. Creates the profiles directory
    if needed.
    """
    profile = {
        "user_id": str(user_id),
        "wardrobe": wardrobe or {"items": []},
        "style_preferences": derive_style_preferences(wardrobe),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _profile_path(user_id, base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    return profile


def load_profile(user_id: str, base_dir: str | None = None) -> dict | None:
    """Load a saved profile, or None if it doesn't exist or is unreadable."""
    path = _profile_path(user_id, base_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable profile -> behave as if there's no memory.
        return None


def get_remembered_wardrobe(user_id: str, base_dir: str | None = None) -> dict | None:
    """Convenience: return just the saved wardrobe for a user, or None."""
    profile = load_profile(user_id, base_dir)
    if not profile:
        return None
    return profile.get("wardrobe")
