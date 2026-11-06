// TL;DR — step 1: load feed.json and render cards.
// Actions are visual only in this step; the event log arrives in step 2. 
"use strict";

const FEED_URL = "feed.json";
const CATS = ["India", "World", "Business", "Tech", "Science", "Sports"]; 

const deck = document.getElementById("deck");
const tpl = document.getElementById("card-tpl"); 
const counterEl = document.getElementById("counter");
const freshEl = document.getElementById("fresh");
const root = document.documentElement;

// ---------- helpers ----------
function ago(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000), h = Math.floor(m / 60);
  if (m < 60) return `${Math.max(m, 1)}m ago`;
  if (h < 24) return `${h}h ago`;
  if (h < 48) return "Yesterday";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
const catVar = (cat) => `var(--cat-${CATS.includes(cat) ? cat : "Other"})`; 
const titleKey = (c) => (c.headline || c.title || "").toLowerCase().replace(/[^a-z0-9 ]/g, "").slice(0, 60); 
                                                                                                                                  
// ---------- one card ----------                                                                                                 
function renderCard(c) {                                                                                                          
  const node = tpl.content.firstElementChild.cloneNode(true);                                                                     
  const q = (sel) => node.querySelector(sel);                                                                                     
                                                                                                                                  
  node.dataset.id = c.id;                                                                                                         
  node.style.setProperty("--cat", catVar(c.category));                                                                            
  if (c.long) node.classList.add("long");                                                                                         
  if (c.kind === "video" || c.kind === "preview") node.classList.add(c.kind);                                                     
                                                                                                                                  
  const img = q(".img");                                                                                                          
  if (c.image) { img.src = c.image; img.onerror = () => { img.hidden = true; }; } else { img.hidden = true; }                     
  q(".ph-letter").textContent = (c.category || "N")[0];                                                                           
                                                                                                                                  
  const badge = q(".badge");                                                                                                      
  if (c.kind === "video") { badge.textContent = "Video"; badge.hidden = false; }                                                  
  else if (c.kind === "preview") { badge.textContent = "Preview"; badge.hidden = false; }                                         
                                                                                                                                  
  q(".cat").textContent = c.category;                                                                                             
  q(".src").textContent = c.source;                                                                                               
  q(".age").textContent = ago(c.published_at);                                                                                    
  q(".headline").textContent = c.headline || c.title;                                                                             
  q(".summary").textContent = c.summary || c.description || "";                                                                   
  q(".read").href = c.url;                                                                                                        
                                                                                                                                  
  // step-1 toggles: visual state only (logging comes next step)                                                                  
  const like = q(".like"), dislike = q(".dislike"), save = q(".save");                                                            
  const setOn = (el, on) => { el.classList.toggle("on", on); el.setAttribute("aria-pressed", String(on)); };                      
  like.onclick = () => { setOn(like, !like.classList.contains("on")); setOn(dislike, false); };                                   
  dislike.onclick = () => { setOn(dislike, !dislike.classList.contains("on")); setOn(like, false); };                             
  save.onclick = () => setOn(save, !save.classList.contains("on"));                                                               
  return node;                                                                                                                    
}                                                                                                                                 
                                                                                                                                  
// ---------- load & draw ----------                                                                                              
async function main() {                                                                                                           
  let feed;                                                                                                                       
  try {                                                                                                                           
    if (window.__FEED__) feed = window.__FEED__;            // test harness injection                                             
    else {                                                                                                                        
      const res = await fetch(FEED_URL, { cache: "no-cache" });                                                                   
      if (!res.ok) throw new Error(`HTTP ${res.status}`);                                                                         
      feed = await res.json();                                                                                                    
    }                                                                                                                             
  } catch (e) {                                                                                                                   
    deck.innerHTML = `<div class="end"><div><h2>Couldn't load the feed</h2><p>${e.message}</p></div></div>`;                      
    return;                                                                                                                       
  }                                                                                                                               
                                                                                                                                  
  const frag = document.createDocumentFragment();                                                                                 
  const seenTitles = new Set();                                                                                                   
  let n = 0;                                                                                                                      
  for (const c of feed.articles) {              // pipeline order; exact-repeat headlines hidden until clustering lands           
    const k = titleKey(c);                                                                                                        
    if (seenTitles.has(k)) continue;                                                                                              
    seenTitles.add(k);                                                                                                            
    frag.appendChild(renderCard(c)); n++;                                                                                         
  }                                                                                                                               
  const end = document.createElement("div");                                                                                      
  end.className = "end";                                                                                                          
  end.innerHTML = `<div><h2>You're all caught up</h2><p>${n} stories · feed updated ${ago(feed.generated_at)}</p></div>`;         
  frag.appendChild(end);                                                                                                          
  deck.replaceChildren(frag);                                                                                                     
                                                                                                                                  
  counterEl.textContent = `${n} stories`;                                                                                         
  freshEl.textContent = ago(feed.generated_at);                                                                                   
  freshEl.classList.toggle("stale", (Date.now() - new Date(feed.generated_at)) / 3.6e6 > 4);                                      
                                                                                                                                  
  fitCards();                                                                                                                     
  // dev: ?i=3 opens on card 3                                                                                                    
  const i = Number(new URLSearchParams(location.search).get("i"));                                                                
  if (i > 0) deck.scrollTop = i * deck.clientHeight;                                                                              
}                                                                                                                                 

// If a card's text would overflow (short phones, browser bar visible), step the type down.
function fitCards() {                                                                                                             
  for (const card of deck.querySelectorAll(".card")) {
    card.classList.remove("tight");
    const body = card.querySelector(".body");
    if (body.scrollHeight > body.clientHeight + 2) card.classList.add("long");  
    if (body.scrollHeight > body.clientHeight + 2) card.classList.add("tight");
  } 
} 
let fitTimer; addEventListener("resize", () => { clearTimeout(fitTimer); fitTimer = setTimeout(fitCards, 150); });

// ---------- header controls ----------
document.getElementById("brand").onclick = () => deck.scrollTo({ top: 0, behavior: "smooth" });

const savedTheme = localStorage.getItem("theme") || new URLSearchParams(location.search).get("theme");
if (savedTheme) root.dataset.theme = savedTheme;
document.getElementById("theme").onclick = () => {              // system → light → dark → system
  const next = { light: "dark", dark: "" }[root.dataset.theme || ""] ?? "light";
  if (next) { root.dataset.theme = next; localStorage.setItem("theme", next); }
  else { delete root.dataset.theme; localStorage.removeItem("theme"); }
};

main();
