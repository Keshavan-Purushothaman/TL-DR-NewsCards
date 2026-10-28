"""Stage 2: rule-based filtering. No AI, no network.

Removes exact duplicates and things that are never a news story (quizzes,
galleries, live blogs, opinion...). Everything removed is logged with the rule
that fired, so bad rules are visible and tunable.

Deliberately does NOT remove same-story-different-source items: that overlap is
the importance signal the clustering stage uses later.
"""
import json
import logging
import re
from pathlib import Path

from models import Article, now_iso

log = logging.getLogger("filters")

# ---------------------------------------------------------------------------
# Blocklist. Each rule: (name, field, compiled regex).
# field is "title", "url" or "tags". Keep patterns anchored/specific so that
# e.g. "video" does not kill "Video game industry layoffs".
# ---------------------------------------------------------------------------
_R = lambda p: re.compile(p, re.IGNORECASE)

RULES = [
    # --- formats that are not articles ---
    ("quiz",        "title", _R(r"\bquiz\b")),
    ("quiz",        "url",   _R(r"/quiz")),
    ("puzzle",      "title", _R(r"\b(crossword|sudoku|puzzle)\b")),
    ("puzzle",      "url",   _R(r"/(crossword|puzzles?)/")),
    ("cartoon",     "title", _R(r"\bcartoon")),
    ("cartoon",     "url",   _R(r"/cartoon")),
    ("gallery",     "title", _R(r"\b(in pictures|in photos|photo gallery|photos?:)")),
    ("gallery",     "url",   _R(r"/(photos?|gallery|galleries|in-pictures|picture-gallery)/")),
    ("podcast",     "title", _R(r"\bpodcast\b")),
    ("podcast",     "url",   _R(r"/podcasts?/")),
    ("podcast",     "tags",  _R(r"^podcasts?$")),
    ("multimedia",  "url",   _R(r"/multimedia/")),

    # --- live blogs: a stream, not a story; the wrap-up article comes separately ---
    ("live",        "title", _R(r"\b(live updates?|live blog|as it happened)\b|^live[:\s|]")),
    ("live",        "url",   _R(r"live-?(updates?|blog|news)|/live/")),

    # --- recurring editorial furniture ---
    ("digest",      "title", _R(r"\b(morning digest|evening wrap|daily briefing|today'?s cache|news in numbers|top news of the day|newsletter)\b")),
    ("letters",     "title", _R(r"\bletters to the editor\b")),
    ("letters",     "url",   _R(r"/letters/")),
    ("horoscope",   "title", _R(r"\bhoroscope\b")),

    # --- opinion: dropped in v1 (plan section 6). Flip by removing these lines. ---
    ("opinion",     "title", _R(r"^(opinion|editorial|op-ed|column)\s*[:|\-]")),
    ("opinion",     "url",   _R(r"/(opinions?|editorials?|op-ed|columns?|comment|blogs?)/")),
    ("opinion",     "tags",  _R(r"^(opinion|opinions|editorial|op-ed|columns?)$")),
]


# Rules that LABEL rather than drop. The page-fetch stage decides what to do
# with the label once it can see how much text the page actually has.
FLAG_RULES = [
    ("video", "title", _R(r"^(watch|video)\s*[:|\-]")),
    ("video", "url",   _R(r"/(videos?|watch)/|/program/newsfeed/")),
    ("video", "tags",  _R(r"^(video|videos|shorts?)$")),
]


def _field_text(a: Article, field: str) -> str:
    if field == "title":
        return a.title or ""
    if field == "url":
        return a.url or ""
    return " ".join(a.tags or [])


def first_hit(a: Article, rules):
    """Return the name of the first rule in `rules` that matches, or None."""
    for name, field, rx in rules:
        if field == "tags":
            if any(rx.search(t) for t in (a.tags or [])):
                return name
        elif rx.search(_field_text(a, field)):
            return name
    return None


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", t.lower()).strip()


def apply_filters(articles: list[Article]) -> tuple[list[Article], list[dict]]:
    """Returns (keepers, dropped). dropped items carry 'reason' and the article basics."""
    seen_ids, seen_titles = set(), set()
    keepers, dropped = [], []

    def drop(a: Article, reason: str):
        dropped.append({
            "id": a.id, "title": a.title, "source": a.source, "section": a.section,
            "url": a.url, "reason": reason, "dropped_at": now_iso(),
        })

    for a in articles:
        # 1. exact duplicate (same normalised URL, e.g. story listed in two feeds)
        if a.id in seen_ids:
            drop(a, "dup_url"); continue
        seen_ids.add(a.id)

        # 2. same publisher re-posting the same headline under a new URL
        tkey = (a.source, _norm_title(a.title))
        if tkey in seen_titles:
            drop(a, "dup_title_same_source"); continue
        seen_titles.add(tkey)

        # 3. blocklist (hard drop)
        rule = first_hit(a, RULES)
        if rule:
            drop(a, f"blocklist:{rule}"); continue

        # 4. flags (keep, but label for later stages)
        flag = first_hit(a, FLAG_RULES)
        if flag:
            a.format = flag

        keepers.append(a)

    log.info("filters: %d in -> %d kept, %d dropped", len(articles), len(keepers), len(dropped))
    return keepers, dropped


def append_audit_log(dropped: list[dict], path: str = "filtered_out.json", keep_last: int = 2000):
    """Append this run's drops to the audit file, capped so it never grows unbounded."""
    p = Path(path)
    existing = json.loads(p.read_text()) if p.exists() else []
    merged = (existing + dropped)[-keep_last:]
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=1))
    return len(merged)
