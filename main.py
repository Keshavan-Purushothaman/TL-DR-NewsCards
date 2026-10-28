"""Pipeline entry point. Run: python main.py                                                                                      
Needs GEMINI_API_KEY in the environment. Writes docs/feed.json, docs/filtered_out.json,                                           
docs/last_run.json and seen_cache.json."""                                                                                        
import json                                                                                                                       
import logging                                                                                                                    
import os                                                                                                                         
import sys                                                                                                                        
import time                                                                                                                       
                                                                                                                                  
import fetch, filters, triage, extract, summarize, build_feed                                                                     
from cache import Cache                                                                                                           
from models import now_iso                                                                                                        
                                                                                                                                  
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")             
log = logging.getLogger("main")                                                                                                   
                                                                                                                                  
                                                                                                                                  
def run() -> dict:                                                                                                                
    t0 = time.time()                                                                                                              
    if not os.environ.get("GEMINI_API_KEY"):                                                                                      
        log.error("GEMINI_API_KEY not set"); sys.exit(2)                                                                          
    os.makedirs("docs", exist_ok=True)                                                                                            
                                                                                                                                  
    articles, report = fetch.fetch_all()                                                                                          
    if not articles:                                                                                                              
        log.error("no articles fetched from any feed; leaving feed.json untouched"); sys.exit(1)                                  
                                                                                                                                  
    k1, d1 = filters.apply_filters(articles)                                                                                      
    cache = Cache()                                                                                                               
    k2, d2, tstats = triage.run_triage(k1, cache)                                                                                 
    filters.append_audit_log(d1 + d2, path="docs/filtered_out.json")                                                              
                                                                                                                                  
    xstats = extract.run_extract(k2, cache)                                                                                       
    sstats = summarize.run_summarize(k2, cache)                                                                                   
                                                                                                                                  
    build_feed.store_cards(k2, cache)                                                                                             
    cache.prune()                                                                                                                 
    cache.save()                                                                                                                  
    feed = build_feed.build(cache)                                                                                                
                                                                                                                                  
    summary = {                                                                                                                   
        "at": now_iso(), "seconds": round(time.time() - t0, 1),                                                                   
        "fetched": len(articles), "past_rules": len(k1), "past_triage": len(k2),                                                  
        "feeds": report, "triage": tstats, "extract": xstats, "summarize": sstats,                                                
        "feed_cards": feed["count"], "feed_kinds": feed["kinds"],                                                                 
    }                                                                                                                             
    with open("docs/last_run.json", "w") as f:                                                                                    
        json.dump(summary, f, indent=1, default=str)                                                                              
    log.info("RUN COMPLETE in %.0fs: %d fetched -> %d rules -> %d triage -> feed %d %s",                                          
              summary["seconds"], len(articles), len(k1), len(k2), feed["count"], feed["kinds"])                                   
    return summary                                                                                                                
                                                                                                                                  
                                                                                                                                  
if __name__ == "__main__":                                                                                                        
    run()
