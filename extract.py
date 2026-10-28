"""Stage 4: fetch the article page and extract main text, og:image, og:video.

No AI. Runs only for triage keepers that do not yet have a summary. A failed
fetch is not fatal: the card falls back to the RSS description ("preview only").
Fetch failures are counted in the cache so a dead URL is not retried forever.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import trafilatura

from cache import Cache
from models import Article

log = logging.getLogger("extract")

WORKERS = 8
TIMEOUT = 15
MAX_FETCH_ATTEMPTS = 2
MIN_WORDS_ARTICLE = 120         # below this, a 'video'-flagged item stays a video card
MAX_TEXT_CHARS = 6000           # what we hand to the summariser

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_META = re.compile(r'<meta[^>]+(?:property|name)=["\'](og:video(?::url|:secure_url)?|og:image)["\'][^>]+content=["\']([^"\']+)["\']',re.I)
_META_REV = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](og:video(?::url|:secure_url)?|og:image)["\']',re.I)
_YT = re.compile(r'(https?://(?:www\.)?(?:youtube\.com/embed/|youtube-nocookie\.com/embed/)[\w\-]+)', re.I)


def _og(html: str) -> dict:
    found = {}
    for rx in (_META, _META_REV):
        for m in rx.finditer(html):
            a, b = m.group(1), m.group(2)
            key, val = (a, b) if a.lower().startswith("og:") else (b, a)
            key = key.lower()
            found.setdefault("image" if key == "og:image" else "video", val)
    if "video" not in found:
        yt = _YT.search(html)
        if yt:
            found["video"] = yt.group(1)
    return found


def fetch_one(a: Article) -> Article:
    try:
        r = requests.get(a.url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            a.fetch_status = f"failed:http{r.status_code}"
            return a
        html = r.text
        text = trafilatura.extract(html, include_comments=False, include_tables=False,
                                    favor_precision=True, url=a.url) or ""
        og = _og(html)
        if og.get("image") and not a.image:
            a.image = og["image"]
        if og.get("video"):
            a.video_url = og["video"]

        words = len(text.split())
        a.word_count = words
        a.text = text[:MAX_TEXT_CHARS] if text else None

        if a.format == "video" and words < MIN_WORDS_ARTICLE:
            a.fetch_status = "video_only"
        elif words < 60:
            a.fetch_status = "short"
        else:
            a.fetch_status = "ok"
            if a.format == "video":            # enough text to be a real article after all
                a.format = "article"
    except requests.RequestException as e:
        a.fetch_status = f"failed:{type(e).__name__}"
    except Exception as e:
        a.fetch_status = f"failed:{type(e).__name__}"
        log.warning("extract error %s: %s", a.url, e)
    return a


def run_extract(articles: list[Article], cache: Cache) -> dict:
    """Fetch pages for articles that still need one. Mutates articles in place; returns stats."""
    todo, skipped = [], 0
    for a in articles:
        c = cache.get(a.id)
        status = c.get("fetch_status", "")
        gave_up = status.startswith("failed") and c.get("fetch_attempts", 0) >= MAX_FETCH_ATTEMPTS
        if "summary" in c or status in ("video_only", "short") or gave_up:
            # outcome already known: restore page-derived fields from cache, no fetch
            a.fetch_status = "ok" if "summary" in c else status
            a.image = c.get("image") or a.image
            a.video_url = c.get("video_url") or a.video_url
            a.format = c.get("format", a.format)
            a.word_count = c.get("word_count", 0)
            skipped += 1
            continue
        todo.append(a)                      # ok-but-unsummarised, or failed with attempts left

    log.info("extract: %d to fetch, %d skipped (cached)", len(todo), skipped)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for fut in as_completed([ex.submit(fetch_one, a) for a in todo]):
            a = fut.result()
            c = cache.get(a.id)
            cache.update(a.id, fetch_status=a.fetch_status, fetch_attempts=c.get("fetch_attempts", 0) + (1 if (a.fetch_status or "").startswith("failed") else 0),
                          image=a.image, video_url=a.video_url, word_count=a.word_count, format=a.format)
    cache.save()

    stats = {}
    for a in articles:
        k = (a.fetch_status or "none").split(":")[0]
        stats[k] = stats.get(k, 0) + 1
    log.info("extract: %s", stats)
    return stats
