"""Stage 1: read every enabled RSS feed and return a flat list of Articles.

No filtering, no dedupe, no AI. One failing feed never stops the others.
"""
import calendar
import logging
import re
import time
from datetime import datetime, timezone
from html import unescape

import feedparser
import yaml

from models import Article, make_id, now_iso

log = logging.getLogger("fetch")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def load_sources(path: str = "sources.yaml") -> list[dict]:
    with open(path) as f:
        sources = yaml.safe_load(f) or []
    return [s for s in sources if s.get("enabled", True)]


def clean_html(raw: str) -> str:
    """RSS descriptions are often HTML. Strip tags, unescape entities, collapse spaces."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def entry_published_at(entry) -> str:
    """feedparser gives a UTC struct_time; timegm (not mktime) converts it correctly."""
    for key in ("published_parsed", "updated_parsed"):
        st = getattr(entry, key, None)
        if st:
            try:
                ts = calendar.timegm(st)
                return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
            except Exception as e:
                log.debug("bad date in entry %r: %s", entry.get("link"), e)
    return now_iso()  # unknown -> treat as fresh rather than drop


def entry_image(entry):
    """Best-effort image from the common RSS media conventions."""
    for m in entry.get("media_content", []) or []:
        if m.get("url"):
            return m["url"]
    for m in entry.get("media_thumbnail", []) or []:
        if m.get("url"):
            return m["url"]
    for l in entry.get("links", []) or []:
        if l.get("rel") == "enclosure" and "image" in (l.get("type") or ""):
            return l.get("href")
    return None


def entry_tags(entry) -> list[str]:
    return [t.get("term", "").strip().lower() for t in entry.get("tags", []) or [] if t.get("term")]


def parse_feed(url: str, retries: int = 2, backoff: float = 3.0):
    """Download + parse one feed with simple retry. Returns a feedparser result or raises."""
    last_err = None
    for attempt in range(1, retries + 1):
        feed = feedparser.parse(url, agent=USER_AGENT)
        status = getattr(feed, "status", None)
        if feed.entries and (status is None or status < 400):
            return feed
        last_err = f"status={status} bozo={feed.get('bozo_exception')}"
        log.warning("attempt %d/%d failed for %s (%s)", attempt, retries, url, last_err)
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise RuntimeError(f"feed failed after {retries} attempts: {last_err}")


def fetch_source(src: dict) -> list[Article]:
    feed = parse_feed(src["feed"])
    out = []
    for entry in feed.entries[: src.get("max_per_run", 25)]:
        link = (entry.get("link") or "").strip()
        title = clean_html(entry.get("title") or "")
        if not link or not title:
            continue
        out.append(Article(
            id=make_id(link),
            url=link,
            title=title,
            source=src["name"],
            section=src.get("section", ""),
            category=src.get("category", "Other"),
            published_at=entry_published_at(entry),
            description=clean_html(entry.get("summary") or entry.get("description") or ""),
            image=entry_image(entry),
            tags=entry_tags(entry),
        ))
    return out


def fetch_all(sources_path: str = "sources.yaml") -> tuple[list[Article], dict]:
    """Returns (articles, report). report[source/section] = count or 'ERROR: ...'."""
    articles, report = [], {}
    for src in load_sources(sources_path):
        key = f"{src['name']}/{src.get('section', '')}"
        try:
            got = fetch_source(src)
            articles.extend(got)
            report[key] = len(got)
            log.info("%-28s %3d entries", key, len(got))
        except Exception as e:           # isolate: one bad feed must not kill the run
            report[key] = f"ERROR: {e}"
            log.error("%-28s FAILED: %s", key, e)
    return articles, report
