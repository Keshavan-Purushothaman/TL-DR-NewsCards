// TL;DR — step 1: load feed.json and render cards. Actions/log come in later steps.
"use strict";

const FEED_URL = "feed.json";
const deck = document.getElementById("deck");
const tpl = document.getElementById("card-tpl");
const counterEl = document.getElementById("counter");
const freshEl = document.getElementById("fresh");

// ---------- helpers ----------
function ago(iso) {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.floor(ms / 60000), h = Math.floor(m / 60);
  if (m < 60) return `${Math.max(m, 1)}m ago`;
  if (h < 24) return `${h}h ago`;
  if (h < 48) return "Yesterday";
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function catVar(cat) { return `var(--cat-${["India","World","Business","Tech","Science","Sports"].includes(cat) ? cat :           
"Other"})`; }

// ---------- render one card ----------
function renderCard(c) {
  const node = tpl.content.firstElementChild.cloneNode(true);
  const q = (sel) => node.querySelector(sel);

  node.dataset.id = c.id; 
  if (c.long) node.classList.add("long");
                                                                                                                                  
  // media                                                                                                                        
  q(".media").style.setProperty("--ph", catVar(c.category));
  const img = q(".img");
  if (c.image) { img.src = c.image; img.onerror = () => { img.hidden = true; }; } else { img.hidden = true; }
  const badge = q(".badge");
  if (c.kind === "video") { badge.textContent = "🎥 Video"; badge.hidden = false; }                                               
  else if (c.kind === "preview") { badge.textContent = "Preview"; badge.hidden = false; }

  // text
  q(".cat").textContent = c.category;
  q(".cat").style.color = catVar(c.category);
  q(".src").textContent = c.source;
  q(".age").textContent = ago(c.published_at);
  q(".headline").textContent = c.headline || c.title;
  q(".summary").textContent = c.summary || c.description || "";

  // link
  q(".open").href = c.url;
  return node;
}

// ---------- load & draw ----------
async function main() {
  let feed;
  try {
    const res = await fetch(FEED_URL, { cache: "no-cache" });
    feed = await res.json();
  } catch (e) {
    deck.innerHTML = `<div class="end"><div><h2>Couldn't load the feed</h2><p>${e.message}</p></div></div>`;
    return;
  }

  const cards = feed.articles;            // already ranked by the pipeline
  const frag = document.createDocumentFragment();
  cards.forEach((c) => frag.appendChild(renderCard(c)));
  const end = document.createElement("div");
  end.className = "end";
  end.innerHTML = `<div><h2>You're all caught up</h2><p>${cards.length} stories · feed updated
  ${ago(feed.generated_at)}</p></div>`;
  frag.appendChild(end);
  deck.replaceChildren(frag);

  counterEl.textContent = `${cards.length} stories`;
  freshEl.textContent = `Updated ${ago(feed.generated_at)}`;
  const ageH = (Date.now() - new Date(feed.generated_at)) / 3.6e6; 
  freshEl.classList.toggle("stale", ageH > 4);

  document.getElementById("brand").onclick = () => deck.scrollTo({ top: 0, behavior: "smooth" }); 
}

main();
