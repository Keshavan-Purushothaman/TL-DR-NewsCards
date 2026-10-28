"""Stage 3: AI triage. Cheap labelling on headline + description only.

Returns type / significance / category for each article, using a response
schema so the JSON is always valid. Results are cached by id so each article
costs at most one triage, ever. Editorial judgement only: no reader preferences
live here (that is the recommender's job).
"""
import logging
import os
import time
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from cache import Cache
from models import Article, now_iso

log = logging.getLogger("triage")

MODEL = "gemini-3.5-flash-lite"
BATCH_SIZE = 20
DROP_TYPES = {"opinion", "promo"}
MIN_SIGNIFICANCE = 3          # significance <= 2 is dropped

Type = Literal["news", "analysis", "explainer", "feature", "sport_result", "opinion", "promo", "other"]
Category = Literal["India", "World", "Business", "Tech", "Science", "Sports", "Other"]


class TriageItem(BaseModel):
    id: str
    type: Type
    significance: int = Field(ge=1, le=5)
    category: Category


RUBRIC = """You are a senior news editor doing a first-pass triage of wire items.
For each item you see only the headline and a one-line description. Label every item.

type:
- news: reports a development or event
- analysis: interprets/contextualises news (still reported, not personal opinion)
- explainer: background / "what you need to know"
- feature: human-interest, profile, lifestyle, long-form colour
- sport_result: match report, result, scoreline, selection news
- opinion: editorial, op-ed, column, personal viewpoint
  NOTE: a headline quoting a politician, official or player ("...: Speaker") is reported
      news, not opinion. Opinion means the publication's or author's own viewpoint.
- promo: subscription pushes, sponsored content, event promotion, self-referential items
- other: none of the above

significance (judge the importance of the underlying story, not the quality of the headline):
- 5: Major development with national or international consequences; would lead a serious front page.
- 4: Important story that most informed readers should know today.
- 3: Solid news; worth a card if there is room.
- 2: Routine or incremental: minor announcements, scheduled events, procedural updates,
      celebrity, routine match reports, regional/local/district-level items.
      Peer-reviewed research findings or scientific discoveries with broad interest are at least 3.
- 1: Trivial, promotional, or not really news.

Sport is rated on the SAME scale as everything else: a major final, a world record, a national
team result, a landmark achievement at national/international level can reach 3-5. Domestic league
rounds, regional tournaments, local meets, transfer gossip and routine fixtures are 2 or below.

category: the best single bucket for the story's subject, regardless of which feed it came from.
India = Indian politics, society, governance; Business = economy, markets, companies, policy with
economic focus (including Indian business); Tech and Science as usual; Sports for any sport.

Be consistent. Do not inflate: most items in a typical feed are 2 or 3."""


def _client() -> genai.Client:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")
    return genai.Client(api_key=key)


def _format_batch(batch: list[Article]) -> str:
    lines = []
    for a in batch:
        desc = (a.description or "")[:220]
        lines.append(f"ID: {a.id}\nSOURCE: {a.source} / {a.section}\nHEADLINE: {a.title}\nDESCRIPTION: {desc}\n")
    return "\n".join(lines)


def triage_batch(client: genai.Client, batch: list[Article], retries: int = 3) -> dict[str, TriageItem]:
    """One API call for a batch. Returns {id: TriageItem} for ids we recognise."""
    prompt = f"{RUBRIC}\n\nThere are {len(batch)} items. Return one object per ID.\n\n{_format_batch(batch)}"
    valid_ids = {a.id for a in batch}
    for attempt in range(1, retries + 1):
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=list[TriageItem],
                    temperature=0.1,
                ),
            )
            items: list[TriageItem] = resp.parsed or []
            out = {}
            for it in items:
                if it.id in valid_ids:
                    out[it.id] = it
                else:
                    log.warning("ignoring unknown id from model: %s", it.id)
            missing = valid_ids - out.keys()
            if missing:
                log.warning("model omitted %d/%d ids; they will be retried next run", len(missing), len(batch))
            return out
        except Exception as e:
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            transient = status in (429, 500, 502, 503, 504) or status is None
            if not transient or attempt == retries:
                log.error("triage batch failed (status=%s, attempt %d): %s", status, attempt, e)
                return {}
            wait = 4 * attempt
            log.warning("triage attempt %d/%d failed (status=%s): retry in %ds", attempt, retries, status, wait)
            time.sleep(wait)
    log.error("triage batch failed after %d attempts; %d articles left untriaged", retries, len(batch))
    return {}


def run_triage(articles: list[Article], cache: Cache) -> tuple[list[Article], list[dict], dict]:
    """Label all articles (using cache where possible). Returns (keepers, dropped, stats)."""
    client = None
    todo = [a for a in articles if "triage" not in cache.get(a.id)]
    stats = {"total": len(articles), "cached": len(articles) - len(todo), "api_calls": 0, "untriaged": 0}
    log.info("triage: %d articles, %d from cache, %d to label", len(articles), stats["cached"], len(todo))

    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        client = client or _client()
        results = triage_batch(client, batch)
        stats["api_calls"] += 1
        for a in batch:
            if a.id in results:
                r = results[a.id]
                cache.update(a.id, triage={"type": r.type, "significance": r.significance,
                                            "category": r.category, "model": MODEL, "at": now_iso()})
        cache.save()                       # save after every batch: a crash loses at most one call
        time.sleep(1)

    keepers, dropped = [], []
    for a in articles:
        t = cache.get(a.id).get("triage")
        if not t:
            stats["untriaged"] += 1
            continue                      # not dropped, not kept: will be retried next run
        a.type, a.significance, a.category = t["type"], t["significance"], t["category"]
        if a.type in DROP_TYPES:
            dropped.append(_drop(a, f"triage:type={a.type}"))
        elif a.significance < MIN_SIGNIFICANCE:
            dropped.append(_drop(a, f"triage:sig={a.significance}"))
        else:
            keepers.append(a)
    log.info("triage: %d kept, %d dropped, %d untriaged, %d api calls",
              len(keepers), len(dropped), stats["untriaged"], stats["api_calls"])
    return keepers, dropped, stats


def _drop(a: Article, reason: str) -> dict:
    return {"id": a.id, "title": a.title, "source": a.source, "section": a.section, "url": a.url,
            "type": a.type, "significance": a.significance, "category": a.category,
            "reason": reason, "dropped_at": now_iso()}
