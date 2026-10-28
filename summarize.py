"""Stage 5: AI summaries for articles with real page text.                                                                        
                                                                                                                                  
Small batches, structured output, cached by id. Highest significance first so a                                                   
backlog never spends quota on minor items. Articles without usable text are not                                                   
summarised: the card shows the RSS description as a preview instead.                                                              
"""                                                                                                                               
import logging                                                                                                                    
import os                                                                                                                         
import time                                                                                                                       
                                                                                                                                  
from google import genai                                                                                                          
from google.genai import types                                                                                                    
from pydantic import BaseModel                                                                                                    
                                                                                                                                  
from cache import Cache                                                                                                           
from models import Article, now_iso                                                                                               
                                                                                                                                  
log = logging.getLogger("summarize")                                                                                              
                                                                                                                                  
MODEL = os.environ.get("SUMMARY_MODEL", "gemini-3.5-flash-lite")                                                                  
FALLBACK_MODEL = "gemini-3.1-flash-lite"      # separate quota pool, same family; used only on 503/429                            
BATCH_SIZE = 5                                                                                                                    
MAX_PER_RUN = 30                                                                                                                  
                                                                                                                                  
                                                                                                                                  
class SummaryItem(BaseModel):                                                                                                     
    id: str                                                                                                                       
    headline: str                                                                                                                 
    summary: str                                                                                                                  
                                                                                                                                  
                                                                                                                                  
INSTRUCTIONS = """You are a professional news editor writing cards for a mobile news app.                                         
For each article return a rewritten headline and a summary.                                                                       
                                                                                                                                  
headline:                                                                                                                         
- Maximum 12 words. Sentence case (capitalise only the first word and proper nouns). Neutral, specific, no clickbait, no          
questions, no puns.                                                                                                               
- Lead with the subject and the development.                                                                                      
                                                                                                                                  
summary:                                                                                                                          
- HARD LIMIT: 50 to 60 words. Count them. Over 65 words is a failure. One paragraph.                                              
- Neutral professional tone. No bullet points, URLs, emojis or opinion.                                                           
- Cover who, what, where, when, and why it matters. Summarise the whole article, not just the lead.                               
- The summary must convey the core development stated in the headline, but in different words; never omit it.                     
- The first sentence must not repeat or rephrase the headline verbatim.                                                           
- Use neutral vocabulary. Attribute contested claims to their source (e.g. "according to xxx media", "the ministry said").     
- Prefer absolute dates when the text gives them; otherwise omit the weekday rather than writing "on Saturday".                   
- Be factually dense: include names, places, dates, numbers and specific outcomes.                                                
- Briefly identify people and organisations on first mention. Expand abbreviations in brackets the first time.                    
- STRICT: use only facts present in the article text. Never add background, context or numbers that are not there.                
- If the text is partial or clearly not the article (paywall notice, cookie banner), say so in one sentence instead of            
guessing."""                                                                                                                      
                                                                                                                                  
                                                                                                                                  
def _client() -> genai.Client:                                                                                                    
    key = os.environ.get("GEMINI_API_KEY")                                                                                        
    if not key:                                                                                                                   
        raise RuntimeError("GEMINI_API_KEY not set in environment")                                                               
    return genai.Client(api_key=key)                                                                                              
                                                                                                                                  
                                                                                                                                  
def _format_batch(batch: list[Article]) -> str:                                                                                   
    parts = []                                                                                                                    
    for a in batch:                                                                                                               
        parts.append(f"=== ARTICLE_ID: {a.id}\nSOURCE: {a.source}\nORIGINAL_TITLE: {a.title}\nTEXT:\n{a.text}\n")                 
    return "\n".join(parts)                                                                                                       
                                                                                                                                  
                                                                                                                                  
def summarize_batch(client: genai.Client, batch: list[Article], model: str = MODEL, retries: int = 3) -> dict[str, SummaryItem]:  
    prompt = f"{INSTRUCTIONS}\n\nThere are {len(batch)} articles. Return one object per ARTICLE_ID.\n\n{_format_batch(batch)}"    
    valid = {a.id for a in batch}                                                                                                 
    for attempt in range(1, retries + 1):                                                                                         
        try:                                                                                                                      
            resp = client.models.generate_content(                                                                                
                model=model, contents=prompt,                                                                                     
                config=types.GenerateContentConfig(                                                                               
                    response_mime_type="application/json",                                                                        
                    response_schema=list[SummaryItem],                                                                            
                    temperature=0.2,                                                                                              
                ),                                                                                                                
            )                                                                                                                     
            out = {}                                                                                                              
            for it in (resp.parsed or []):                                                                                        
                if it.id in valid and it.summary.strip() and it.headline.strip():                                                 
                    out[it.id] = it                                                                                               
                elif it.id not in valid:                                                                                          
                    log.warning("ignoring unknown id from model: %s", it.id)                                                      
            missing = valid - out.keys()                                                                                          
            if missing:                                                                                                           
                log.warning("model omitted %d/%d ids; retried next run", len(missing), len(batch))                                
            return out                                                                                                            
        except Exception as e:                                                                                                    
            status = getattr(e, "code", None) or getattr(e, "status_code", None)                                                  
            transient = status in (429, 500, 502, 503, 504) or status is None                                                     
            if not transient or attempt == retries:                                                                               
                log.error("summary batch failed (status=%s, attempt %d): %s", status, attempt, e)                                 
                return {}                                                                                                         
            wait = 5 * attempt                                                                                                    
            log.warning("summary attempt %d/%d failed (status=%s): retry in %ds", attempt, retries, status, wait)                 
            time.sleep(wait)                                                                                                      
    return {}                                                                                                                     
                                                                                                                                  
                                                                                                                                  
def run_summarize(articles: list[Article], cache: Cache) -> dict:                                                                 
    """Summarise up to MAX_PER_RUN articles that have text and no cached summary. Mutates in place."""                            
    todo = [a for a in articles if a.fetch_status == "ok" and a.text and "summary" not in cache.get(a.id)]                        
    todo.sort(key=lambda a: a.published_at, reverse=True)      # newest first...                                                  
    todo.sort(key=lambda a: -(a.significance or 0))            # ...then significance desc (stable sort)                          
    deferred = max(0, len(todo) - MAX_PER_RUN)                                                                                    
    todo = todo[:MAX_PER_RUN]                                                                                                     
    stats = {"candidates": len(todo) + deferred, "sent": len(todo), "deferred": deferred,                                         
              "api_calls": 0, "got": 0, "long": 0, "model": MODEL}                                                                 
    log.info("summarize: %d candidates, sending %d, deferring %d (model %s)", stats["candidates"], len(todo), deferred, MODEL)    
                                                                                                                                  
    client = None                                                                                                                 
    for i in range(0, len(todo), BATCH_SIZE):                                                                                     
        batch = todo[i:i + BATCH_SIZE]                                                                                            
        client = client or _client()                                                                                              
        results = summarize_batch(client, batch)                                                                                  
        stats["api_calls"] += 1                                                                                                   
        used = MODEL                                                                                                              
        if not results:                                                                                                           
            log.warning("primary model failed for this batch; trying %s", FALLBACK_MODEL)                                         
            results = summarize_batch(client, batch, model=FALLBACK_MODEL, retries=2)                                             
            stats["api_calls"] += 1                                                                                               
            stats["fallback_batches"] = stats.get("fallback_batches", 0) + 1                                                      
            used = FALLBACK_MODEL                                                                                                 
        for a in batch:                                                                                                           
            if a.id in results:                                                                                                   
                r = results[a.id]                                                                                                 
                words = len(r.summary.split())                                                                                    
                if words > 75:                                                                                                    
                    stats["long"] += 1                                                                                            
                    log.warning("long summary (%d words, %s): %s", words, used, r.headline[:60])                                  
                cache.update(a.id, summary={"headline": r.headline.strip(), "summary": r.summary.strip(),                         
                                            "words": words, "long": words > 75, "model": used, "at": now_iso()})                  
                stats["got"] += 1                                                                                                 
        cache.save()
        time.sleep(1)

    for a in articles:
        s = cache.get(a.id).get("summary")
        if s:
            a.headline, a.summary = s["headline"], s["summary"]
    log.info("summarize: %s", stats)
    return stats
