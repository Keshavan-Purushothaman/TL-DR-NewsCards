"""Stage 6: assemble feed.json (what the phone reads). No AI.                                                                     
                                                                                                                                  
Each keeper is stored as a complete 'card' in the cache, so the feed can include                                                  
stories that have already scrolled off their RSS feed. feed.json = all cached                                                     
cards from the last WINDOW_HOURS, ranked. Importance here is a placeholder                                                        
(significance + recency) until the clustering stage replaces it.                                                                  
"""                                                                                                                               
import json                                                                                                                       
import logging                                                                                                                    
import re                                                                                                                         
from datetime import datetime, timezone                                                                                           
from pathlib import Path                                                                                                          
                                                                                                                                  
from cache import Cache                                                                                                           
from models import Article, now_iso                                                                                               
                                                                                                                                  
log = logging.getLogger("build_feed")                                                                                             
                                                                                                                                  
WINDOW_HOURS = 72                                                                                                                 
_GENERIC_IMG = re.compile(r"og-image|/logo|default|placeholder|favicon", re.I)                                                    
                                                                                                                                  
                                                                                                                                  
def _clean_image(url):                                                                                                            
    """Drop publisher logos / generic og:image fallbacks; the app shows a category placeholder instead."""                        
    if not url or _GENERIC_IMG.search(url):                                                                                       
        return None                                                                                                               
    return url                                                                                                                    
                                                                                                                                  
                                                                                                                                  
def _card_kind(a: Article) -> str:                                                                                                
    if a.summary:                                                                                                                 
        return "summary"                                                                                                          
    if a.fetch_status == "video_only" or a.format == "video":                                                                     
        return "video"                                                                                                            
    return "preview"            # no summary yet (deferred / fetch failed): show RSS description                                  
                                                                                                                                  
                                                                                                                                  
def store_cards(articles: list[Article], cache: Cache) -> int:                                                                    
    """Write/refresh a complete card for every keeper this run."""                                                                
    for a in articles:                                                                                                            
        s = cache.get(a.id).get("summary") or {}                                                                                  
        cache.update(a.id, card={                                                                                                 
            "id": a.id,                                                                                                           
            "kind": _card_kind(a),                                                                                                
            "headline": a.headline or a.title,                                                                                    
            "title": a.title,                                                                                                     
            "summary": a.summary,                                                                                                 
            "description": a.description,                                                                                         
            "long": bool(s.get("long")),                                                                                          
            "image": _clean_image(a.image),                                                                                       
            "video_url": a.video_url,                                                                                             
            "source": a.source,                                                                                                   
            "section": a.section,                                                                                                 
            "category": a.category,                                                                                               
            "type": a.type,                                                                                                       
            "significance": a.significance,                                                                                       
            "url": a.url,                                                                                                         
            "published_at": a.published_at,                                                                                       
            "fetch_status": a.fetch_status,                                                                                       
            "cluster_id": None,                                                                                                   
            "cluster_size": 1,                                                                                                    
        })                                                                                                                        
    cache.save()                                                                                                                  
    return len(articles)                                                                                                          
                                                                                                                                  
                                                                                                                                  
def _age_hours(published_at: str, now: datetime) -> float:                                                                        
    try:                                                                                                                          
        dt = datetime.fromisoformat(published_at)                                                                                 
        if dt.tzinfo is None:                                                                                                     
            dt = dt.replace(tzinfo=timezone.utc)                                                                                  
        return max(0.0, (now - dt).total_seconds() / 3600)                                                                        
    except Exception:                                                                                                             
        return WINDOW_HOURS                                                                                                       
                                                                                                                                  
                                                                                                                                  
def importance(card: dict, now: datetime) -> float:                                                                               
    """Placeholder ranking: significance dominates, freshness breaks ties, fades over the window."""                              
    age = _age_hours(card["published_at"], now)                                                                                   
    recency = 2.0 * max(0.0, 1 - age / WINDOW_HOURS)                                                                              
    return round((card.get("significance") or 3) + recency, 3)                                                                    
                                                                                                                                  
                                                                                                                                  
def build(cache: Cache, out_path: str = "docs/feed.json") -> dict:                                                                
    now = datetime.now(timezone.utc)                                                                                              
    cards = []                                                                                                                    
    for entry in cache.data.values():                                                                                             
        card = entry.get("card")                                                                                                  
        if not card:                                                                                                              
            continue                                                                                                              
        age = _age_hours(card["published_at"], now)                                                                               
        if age > WINDOW_HOURS:                                                                                                    
            continue                                                                                                              
        card = dict(card)                                                                                                         
        card["age_hours"] = round(age, 1)                                                                                         
        card["importance"] = importance(card, now)                                                                                
        cards.append(card)                                                                                                        
                                                                                                                                  
    cards.sort(key=lambda c: c["published_at"], reverse=True)      # newest first...                                              
    cards.sort(key=lambda c: -c["importance"])                     # ...then by importance (stable)                               
    feed = {                                                                                                                      
        "generated_at": now_iso(),                                                                                                
        "window_hours": WINDOW_HOURS,                                                                                             
        "count": len(cards),                                                                                                      
        "kinds": {k: sum(1 for c in cards if c["kind"] == k) for k in ("summary", "video", "preview")},                           
        "articles": cards,                                                                                                        
    }                                                                                                                             
    p = Path(out_path)                                                                                                            
    p.parent.mkdir(parents=True, exist_ok=True)                                                                                   
    p.write_text(json.dumps(feed, ensure_ascii=False, indent=1))                                                                  
    log.info("feed: %d cards %s -> %s", len(cards), feed["kinds"], out_path)                                                      
    return feed 
