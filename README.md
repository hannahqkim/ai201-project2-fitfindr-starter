# FitFindr — Starter Kit

FitFindr is a secondhand-shopping stylist agent. You describe an item you want
and share your wardrobe; the agent finds a real listing, suggests how to style
it with pieces you already own, and writes a shareable social caption for the
find. It runs as a three-tool agent orchestrated by a small planning loop, with
a Gradio web interface.

---

## Setup & Running

```bash
pip install -r requirements.txt
```

Set your Groq API key in a `.env` file in the project root
(get a free key at [console.groq.com](https://console.groq.com)):

```
GROQ_API_KEY=your_key_here
```

Run the web app:

```bash
python app.py
```

Open the URL shown in your terminal — usually `http://localhost:7860`, but
**check the terminal output**, as the port can differ. Type a query (e.g.
`vintage graphic tee under $30`), pick a wardrobe, and hit **Find it**. All
three panels — listing, outfit idea, fit card — populate on a happy-path query.

Run the agent from the command line instead:

```bash
python agent.py     # runs a happy-path query and the no-results path
```

Run the tests:

```bash
pytest tests/       # 17 tests: tool contracts, failure modes, and state flow
```

---

## Tool Inventory

The agent has exactly three tools, all defined in `tools.py`. `search_listings`
is a pure local function; the other two call the Groq LLM
(`llama-3.3-70b-versatile`).

### 1. `search_listings(description, size, max_price) -> list[dict]`

**Purpose:** Find secondhand listings matching the user's keywords, filtered by
optional size and price ceiling, ranked best-match-first. This is the only tool
that decides *what* the user can be shown; the rest style whatever it returns.

**Inputs:**

| Parameter | Type | Meaning |
|---|---|---|
| `description` | `str` | Free-text keywords, e.g. `"vintage graphic tee"`. Tokenized (lowercased, stopwords dropped) and scored against each listing. |
| `size` | `str \| None` | Size to filter by, e.g. `"M"`. Token-based match (see Error Handling), so `"M"` matches `"S/M"` but not `"XL"` or the `"S"` in `"US 8"`. `None` skips size filtering. |
| `max_price` | `float \| None` | Inclusive price ceiling — a listing passes only if `price <= max_price`. `None` skips price filtering. |

**Output:** `list[dict]` of matching listings, sorted by relevance score
(highest first). Each dict carries the dataset fields: `id`, `title`,
`description`, `category`, `style_tags` (list), `size`, `condition`, `price`
(float), `colors` (list), `brand` (str or `None`), `platform`. Listings with a
relevance score of 0 are dropped. Returns `[]` if nothing matches — **it never
raises.**

### 2. `suggest_outfit(new_item, wardrobe) -> str`

**Purpose:** Given the chosen listing and the user's wardrobe, write 1–2
concrete outfit ideas that name real pieces the user owns.

**Inputs:**

| Parameter | Type | Meaning |
|---|---|---|
| `new_item` | `dict` | One listing dict (the selected search result). Its `title`, `category`, `colors`, and `style_tags` are formatted into the prompt. |
| `wardrobe` | `dict` | A wardrobe dict with an `"items"` key holding a `list[dict]`. Each item has `id`, `name`, `category`, `colors`, `style_tags`, and optional `notes`. May be empty (`{"items": []}`). |

**Output:** A non-empty `str`. With a populated wardrobe, it names real pieces
("pair it with your baggy straight-leg dark-wash jeans"). With an empty
wardrobe, it returns general styling advice for the item's vibe and does **not**
invent owned pieces. Never returns `""`, never raises.

### 3. `create_fit_card(outfit, new_item) -> str`

**Purpose:** Turn the outfit suggestion into a short, casual, shareable caption
(Instagram/TikTok OOTD style).

**Inputs:**

| Parameter | Type | Meaning |
|---|---|---|
| `outfit` | `str` | The outfit-suggestion string from `suggest_outfit()`. |
| `new_item` | `dict` | The selected listing dict — its `title`, `price`, and `platform` are woven into the caption once each. |

**Output:** A 2–4 sentence `str` usable as a social caption. Generated at a
higher temperature (`1.0`) so repeated calls on the same input read
differently. Never raises.

---

## How the Planning Loop Works

The loop lives in `run_agent(query, wardrobe)` in `agent.py`. It is **not** an
open-ended "the agent decides what to do" reasoner — it's a fixed linear
pipeline with exactly **one decision point**: whether the search returned
anything. That single branch is what makes the agent respond differently to
different inputs instead of blindly calling all three tools every time.

The decisions, in order:

1. **Initialize** a fresh `session` dict (the single source of truth).
2. **Parse the query** with `_parse_query()` — lightweight regex, no LLM. It
   pulls `max_price` from phrasing like "under $30" / "$30" / "30 dollars",
   pulls `size` from an explicit "size M" / "size 8" or a standalone letter-size
   token, and treats the cleaned remaining text as the search `description`.
   Results go into `session["parsed"]`.
3. **Search.** Call `search_listings(description, size, max_price)` and store the
   list in `session["search_results"]`.
4. **The one branch — is the result list empty?**
   - **Empty →** set `session["error"]` to a specific, actionable message and
     `return session` immediately. The LLM tools are **never called** with empty
     input; `outfit_suggestion` and `fit_card` stay `None`. This is the whole
     point of the milestone: the agent *decides to stop*.
   - **Non-empty →** continue.
5. **Select** the top-ranked match: `session["selected_item"] =
   search_results[0]`.
6. **Suggest an outfit:** `suggest_outfit(selected_item, wardrobe)` →
   `session["outfit_suggestion"]`. (This tool self-handles the empty-wardrobe
   case, so the loop needs no extra branch here.)
7. **Create the fit card:** `create_fit_card(outfit_suggestion, selected_item)`
   → `session["fit_card"]`, then `return session`.

**How it knows it's done:** the pipeline is finite. There is exactly one
terminal success state (all three outputs populated, `error is None`) and one
terminal error state (`error` set, later outputs `None`).

---

## State Management

A single `session` dict, created by `_new_session()`, is the one source of
truth for an interaction. Each step reads what it needs from the dict and writes
its output back; tools are not chained by passing return values directly — they
communicate through `session`.

| Key | Written by | Read by | Holds |
|---|---|---|---|
| `query` | `_new_session` | parse step | original raw user string |
| `parsed` | parse step | `search_listings` call | `{description, size, max_price}` |
| `search_results` | after search | empty-check, select step | `list[dict]` of matches |
| `selected_item` | select step | both LLM tools, UI | the top listing dict |
| `wardrobe` | `_new_session` | `suggest_outfit` | user's wardrobe dict |
| `outfit_suggestion` | after suggest | `create_fit_card`, UI | outfit string |
| `fit_card` | after fit card | UI | caption string |
| `error` | empty-results branch | UI / caller | `None` on success, message on early exit |

**Caller contract** (`app.py` → `handle_query`): **check `session["error"]`
first.** If it's not `None`, render only the error message and leave the other
two panels blank. Otherwise format `selected_item` into the listing panel and
render `outfit_suggestion` and `fit_card`.

State really flows — it isn't re-derived or hardcoded between steps. The test
`tests/test_agent.py::test_happy_path_state_flows` asserts this by object
identity (`is`), not just equality: `selected_item is search_results[0]`, the
exact dict handed to `suggest_outfit` *is* `selected_item`, and the exact string
handed to `create_fit_card` *is* `outfit_suggestion`.

---

## Error Handling (per tool, with tested examples)

| Tool | Failure mode | What the agent does | Concrete example from testing |
|---|---|---|---|
| `search_listings` | No listing matches (filters too strict or no keyword overlap) | Returns `[]` (never raises). The loop turns that into `session["error"]` and stops before any LLM call. | `search_listings('designer ballgown', size='XXS', max_price=5)` → `[]`. Running the full agent on `"designer ballgown size XXS under $5"` returns `error = "No listings found for '…'. Try removing the size or price filter, or using broader keywords (e.g. 'graphic tee' instead of a brand name)."` with `fit_card = None`. |
| `suggest_outfit` | Empty wardrobe (`wardrobe["items"] == []`) | Switches to a "general styling advice" prompt instead of naming owned pieces; if the LLM call itself fails, returns a safe non-empty fallback. Never returns `""`, never raises. | `suggest_outfit(results[0], get_empty_wardrobe())` returns a non-empty advice string rather than crashing. |
| `create_fit_card` | Empty / whitespace `outfit` string | Guards up front and returns a descriptive message (no LLM call); if the LLM fails on a valid outfit, returns a template caption built from the item fields. Never raises. | `create_fit_card('', results[0])` → `"No outfit suggestion was available, so I couldn't write a fit card — but this Y2K Baby Tee — Butterfly Print ($18.0, depop) is a strong solo piece worth grabbing."` |

All three failure modes are covered by automated tests in `tests/` and were
also triggered manually from the terminal (see
`tests/failure_modes_capture.html`).

---

## Spec Reflection

Writing the spec in `planning.md` before any code paid off most in the **planning
loop** and the **size filter**. Because the spec named the single early-exit
branch explicitly ("if `search_results` is empty, set `error` and return before
calling the LLM tools"), the implementation was almost a transcription of the
spec, and the behavior-differs-by-input requirement fell out naturally rather
than needing a redesign.

The spec also changed *because* of implementation thinking. The first draft
described size matching as a plain case-insensitive substring ("`M` matches
`S/M`"). Tracing that against the real dataset showed it would also match the
`S` inside `"US 8"` and `"One Size"`, so the spec was tightened to **token-based**
matching before the code was written — and `search_listings` was built to that
stricter rule from the start. One thing I'd refine further: the relevance
scoring is a simple weighted keyword overlap (style-tag hits worth more than
title/description hits), which is good enough for 40 listings but would need
real ranking (e.g. embeddings) at scale.

---

## AI Usage

I used **Claude** as the implementation assistant, giving it specific sections
of `planning.md` as the prompt rather than vague asks. Two concrete instances:

**1. Implementing `search_listings`.** I gave Claude the *Tool 1* block from
`planning.md` (the typed inputs, the full list of fields in the returned dicts,
and the "returns `[]`, never raises" failure mode) and the `load_listings()`
docstring from `utils/data_loader.py`. Claude produced a pure function that
filters by price and size, scores keyword overlap, drops zero-score listings,
and sorts descending. **What I changed:** I had it implement the size filter as
*token-based* matching (splitting `"S/M"` and `"US 8"` into tokens) instead of a
raw substring check, to kill the false positive where `"S"` matches `"US 8"`. I
also kept the sort stable so equal-scoring listings stay in dataset order, for
deterministic, testable output.

**2. Implementing the planning loop.** I gave Claude the *Planning Loop*, *State
Management*, and *Error Handling* sections plus the agent diagram from
`planning.md`, along with the `_new_session` / `run_agent` stubs in `agent.py`.
Claude produced the 7-step pipeline with the single empty-results branch,
writing each result back into `session`. **What I overrode/verified:** I checked
that the empty-results branch returns *before* any LLM call (not just sets a
flag and continues), and added tests that assert state flows by object identity
(`selected_item is search_results[0]`, and the same objects are passed into the
downstream tools) so "state passing" is provable, not assumed. I also wrote the
query parser (`_parse_query`) as plain regex rather than an LLM call, to keep
parsing deterministic and free.

---

## Project Structure

```
ai201-project2-fitfindr-starter/
├── data/
│   ├── listings.json          # 40 mock secondhand listings
│   └── wardrobe_schema.json   # Wardrobe format + example/empty wardrobes
├── utils/
│   └── data_loader.py         # load_listings(), get_example_wardrobe(), get_empty_wardrobe()
├── tools.py                   # The 3 tools: search_listings, suggest_outfit, create_fit_card
├── agent.py                   # Planning loop (run_agent) + query parser + session state
├── app.py                     # Gradio UI + handle_query()
├── tests/
│   ├── test_tools.py          # Tool contracts + failure modes
│   ├── test_agent.py          # Planning-loop state flow + branch behavior
│   └── failure_modes_capture.html  # Terminal-style capture of triggered failures (for demo)
├── planning.md                # The spec written before any code
└── requirements.txt
```
