"""Persistent per-article results, keyed by id. The main cost control:
an article is triaged once and summarised once, no matter how many runs see it."""
import json
from pathlib import Path

from models import now_iso


class Cache:
    def __init__(self, path: str = "seen_cache.json"):
        self.path = Path(path)
        self.data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, article_id: str) -> dict:
        return self.data.get(article_id, {})

    def update(self, article_id: str, **fields):
        entry = self.data.setdefault(article_id, {"first_seen": now_iso()})
        entry.update(fields)
        entry["updated_at"] = now_iso()

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))

    def prune(self, keep_last: int = 5000):
        """Drop oldest entries if the file gets large (feeds only show recent items anyway)."""
        if len(self.data) > keep_last:
            items = sorted(self.data.items(), key=lambda kv: kv[1].get("first_seen", ""))
            self.data = dict(items[-keep_last:])
