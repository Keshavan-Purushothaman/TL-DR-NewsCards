"""Shared data shapes for the pipeline. Every stage reads and writes these."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
import hashlib


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url: str) -> str:
    """Canonical form for identity: drop query string, fragment, trailing slash.
    'https://x.com/a/?utm_source=rss#top' and 'https://x.com/a' become the same article."""
    p = urlsplit(url.strip())
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, "", ""))


def make_id(url: str) -> str:
    """Stable short id from the normalised URL. Same article -> same id, across runs."""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:12]


@dataclass
class Article:
    # --- identity (set by fetch) ---
    id: str
    url: str
    title: str
    source: str            # publisher, e.g. "The Hindu"
    section: str           # feed section, e.g. "National"
    category: str          # our bucket, from sources.yaml
    published_at: str      # ISO-8601 UTC
    fetched_at: str = field(default_factory=now_iso)

    # --- raw material (set by fetch; may be thin) ---
    description: str = ""          # RSS summary/description, HTML stripped
    image: Optional[str] = None    # from RSS media tags, upgraded later by og:image
    tags: list = field(default_factory=list)
    format: str = "article"            # "article" | "video"  (set by filters, refined by page fetch)
    video_url: Optional[str] = None    # og:video / YouTube embed, if the page exposes one (for v2 video cards)
    fetch_status: Optional[str] = None  # ok | short | video_only | failed  (set by extract)
    word_count: int = 0                 # words of extracted text

    # --- filled by later stages (left None until then) ---
    text: Optional[str] = None            # full article text, if we fetch the page
    type: Optional[str] = None            # news | analysis | explainer | opinion | ...
    significance: Optional[int] = None    # 1..5 from triage
    summary: Optional[str] = None         # ~60 words
    headline: Optional[str] = None        # rewritten short title
    cluster_id: Optional[str] = None
    importance: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)
