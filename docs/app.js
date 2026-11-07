// TL;DR — step 2: render + remember + record.
// Event log (IndexedDB), persistent toggles, impression/dwell/seen tracking, sessions, export.
"use strict";

const FEED_URL = "feed.json";
const CATS = ["India", "World", "Business", "Tech", "Science", "Sports"];
const SEEN_MS = 1000;            // dwell >= 1 s marks a card seen
const LOG_VERSION = 1;
const LOG_KEEP_DAYS = 90;
const SESSION_GAP_MIN = 30;      // hidden longer than this -> new session on return
const FEED_POLL_MS = 5 * 60 * 1000;

const deck = document.getElementById("deck");
const tpl = document.getElementById("card-tpl");
const counterEl = document.getElementById("counter");
const freshEl = document.getElementById("fresh");
const root = document.documentElement;

// =====================================================================
// Persistent state (small, synchronous): localStorage
// =====================================================================
const STATE_KEY = "tldr.state.v1";
const state = Object.assign(
  { seen: {}, liked: {}, disliked: {}, saved: {}, dup: {}, session: null, lastHidden: 0 },
  JSON.parse(localStorage.getItem(STATE_KEY) || "{}")
);
function saveState() { localStorage.setItem(STATE_KEY, JSON.stringify(state)); }
function pruneState() {                                  // forget "seen" older than 10 days (feed window is 3)
  const cutoff = Date.now() - 10 * 864e5;
  for (const k of Object.keys(state.seen)) if (state.seen[k] < cutoff) delete state.seen[k];
}

// =====================================================================
// Event log: IndexedDB, append-only
// =====================================================================
const DB = (() => {
  // IndexedDB when available; if it is blocked or slow to open (private mode, odd browsers),
  // fall back to an in-memory list so the app never stalls on logging.
  let dbp, mem = null;
  function open() {
    if (dbp) return dbp;
    if (window.__NO_IDB__) { mem = []; return (dbp = Promise.resolve(null)); }   // test harness
    dbp = new Promise((resolve) => {
      const giveUp = setTimeout(() => { mem = mem || []; console.warn("IndexedDB unavailable; logging in memory"); resolve(null); }, 1500);
      let req;
      try { req = indexedDB.open("tldr", 1); } catch { clearTimeout(giveUp); mem = []; return resolve(null); }
      req.onupgradeneeded = () => {
        const s = req.result.createObjectStore("events", { keyPath: "seq", autoIncrement: true });
        s.createIndex("ts", "ts"); s.createIndex("article_id", "article_id"); s.createIndex("event", "event");
      };
      req.onsuccess = () => { clearTimeout(giveUp); resolve(req.result); };
      req.onerror = () => { clearTimeout(giveUp); mem = mem || []; resolve(null); };
    });
    return dbp;
  }
  async function add(row) {
    const db = await open();
    if (!db) { mem.push(Object.assign({ seq: mem.length + 1 }, row)); return; }
    return new Promise((res, rej) => {
      const tx = db.transaction("events", "readwrite");
      tx.objectStore("events").add(row); tx.oncomplete = res; tx.onerror = () => rej(tx.error);
    });
  }
  async function all() {
    const db = await open();
    if (!db) return mem.slice();
    return new Promise((res, rej) => {
      const req = db.transaction("events").objectStore("events").getAll();
      req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error);
    });
  }
  async function pruneOlderThan(days) {
    const db = await open();
    if (!db) return;
    const cutoff = new Date(Date.now() - days * 864e5).toISOString();
    return new Promise((res) => {
      const tx = db.transaction("events", "readwrite");
      const cur = tx.objectStore("events").index("ts").openCursor(IDBKeyRange.upperBound(cutoff));
      cur.onsuccess = () => { const c = cur.result; if (c) { c.delete(); c.continue(); } };
      tx.oncomplete = res;
    });
  }
  return { add, all, pruneOlderThan, get inMemory() { return !!mem; } };
})();

// ---------- sessions ----------
function newSession() {
  state.session = { id: "s_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), started: Date.now() };
  saveState();
  log("session_start", null);
}
function ensureSession() {
  const gap = state.lastHidden ? (Date.now() - state.lastHidden) / 60000 : 0;
  if (!state.session || gap > SESSION_GAP_MIN) newSession();
}

// ---------- one event ----------
const cardsById = new Map();     // id -> card object (feed data + runtime position)
let feedGeneratedAt = null;

function snapshot(c) {
  if (!c) return null;
  return {
    source: c.source, section: c.section, category: c.category, type: c.type, significance: c.significance,
    kind: c.kind, importance: c.importance, age_hours: c.age_hours, cluster_size: c.cluster_size || 1,
    summary_words: c.summary ? c.summary.split(/\s+/).length : 0, has_image: !!c.image, feed_generated_at: feedGeneratedAt,
  };
}
function log(event, articleId, value = {}) {
  const c = articleId ? cardsById.get(articleId) : null;
  const now = new Date();
  const row = {
    v: LOG_VERSION, ts: now.toISOString(), event, article_id: articleId,
    session_id: state.session && state.session.id,
    position: c ? { index: c._index, batch: 1, explore: false } : null,
    value, snapshot: snapshot(c),
    context: { hour: now.getHours(), weekday: now.getDay(), online: navigator.onLine, visible: document.visibilityState === "visible" },
  };
  DB.add(row).catch((e) => console.warn("log failed", e));
}

// =====================================================================
// Helpers
// =====================================================================
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
const setOn = (el, on) => { el.classList.toggle("on", on); el.setAttribute("aria-pressed", String(on)); };
// RSS thumbnails are tiny (BBC 240px, Hindu 615px). Ask the same CDNs for a phone-sized rendition.
function hiRes(url) {
  if (!url) return url;
  return url
    .replace(/\/standard\/\d+\//, "/standard/1024/")                 // ichef.bbci.co.uk
    .replace(/\/alternates\/LANDSCAPE_\d+\//, "/alternates/LANDSCAPE_1200/")   // th-i.thgim.com
    .replace(/\/alternates\/FREE_\d+\//, "/alternates/FREE_1200/");
}

// =====================================================================
// Card
// =====================================================================
function renderCard(c) {
  const node = tpl.content.firstElementChild.cloneNode(true);
  const q = (sel) => node.querySelector(sel);
  const id = c.id;

  node.dataset.id = id;
  node.style.setProperty("--cat", catVar(c.category));
  if (c.kind === "video" || c.kind === "preview") node.classList.add(c.kind);

  const img = q(".img");
  if (c.image) {
    img.src = hiRes(c.image);
    img.onerror = () => { if (img.src !== c.image) img.src = c.image; else img.hidden = true; };   // fall back to the original size
  } else { img.hidden = true; }
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

  // ----- actions (all toggles; every change is an event, reversals included) -----
  const like = q(".like"), dislike = q(".dislike"), save = q(".save"), dup = q(".dup"), why = q(".why"), heart = q(".heart");
  setOn(like, !!state.liked[id]); setOn(dislike, !!state.disliked[id]); setOn(save, !!state.saved[id]); setOn(dup, !!state.dup[id]);
  let whyTimer;

  function setLike(on) {
    if (!!state.liked[id] === on) return;
    if (on) { state.liked[id] = Date.now(); if (state.disliked[id]) { delete state.disliked[id]; setOn(dislike, false); log("undislike", id); } }
    else delete state.liked[id];
    setOn(like, on); saveState(); log(on ? "like" : "unlike", id);
  }
  function setDislike(on) {
    if (!!state.disliked[id] === on) return;
    if (on) { state.disliked[id] = Date.now(); if (state.liked[id]) { delete state.liked[id]; setOn(like, false); log("unlike", id); } }
    else delete state.disliked[id];
    setOn(dislike, on); saveState(); log(on ? "dislike" : "undislike", id);
    why.hidden = !on; clearTimeout(whyTimer);
    if (on) whyTimer = setTimeout(() => { why.hidden = true; }, 5000);
  }
  like.onclick = () => setLike(!state.liked[id]);
  dislike.onclick = () => setDislike(!state.disliked[id]);
  why.onclick = (e) => { const r = e.target.dataset.r; if (!r) return; log("dislike_reason", id, { reason: r }); why.hidden = true; };
  save.onclick = () => {
    const on = !state.saved[id]; if (on) state.saved[id] = Date.now(); else delete state.saved[id];
    setOn(save, on); saveState(); log(on ? "save" : "unsave", id);
  };
  dup.onclick = () => {                                  // "seen this story already": marks seen, not a taste signal
    const on = !state.dup[id]; if (on) { state.dup[id] = Date.now(); markSeen(id, "dup"); } else delete state.dup[id];
    setOn(dup, on); saveState(); log(on ? "duplicate" : "unduplicate", id);
  };
  q(".read").addEventListener("click", () => log("open_full", id));

  // double-tap anywhere on the card (except the action bar) = like (never un-like), with a heart
  let lastTap = 0;
  node.addEventListener("pointerup", (e) => {
    if (e.target.closest(".actions, .why")) return;
    const t = Date.now();
    if (t - lastTap < 320) { setLike(true); heart.classList.remove("pop"); void heart.offsetWidth; heart.classList.add("pop"); }
    lastTap = t;
  });
  return node;
}

// =====================================================================
// Impressions, dwell, seen
// =====================================================================
let current = null;              // { id, start, acc }  the card currently >=60% visible
const impressed = new Set();     // one impression per card per session

function markSeen(id, how) {
  if (state.seen[id]) return;
  state.seen[id] = Date.now(); saveState(); updateCounter();
}
function enter(id) {
  if (current && current.id === id) return;
  leave();
  current = { id, start: document.visibilityState === "visible" ? Date.now() : null, acc: 0 };
  if (!impressed.has(id)) { impressed.add(id); log("impression", id); }
  else log("revisit", id);
}
function leave() {
  if (!current) return;
  pause();
  const ms = current.acc;
  log("dwell", current.id, { dwell_ms: ms });
  if (ms >= SEEN_MS) markSeen(current.id, "dwell");
  current = null;
}
function pause() { if (current && current.start) { current.acc += Date.now() - current.start; current.start = null; } }
function resume() { if (current && !current.start) current.start = Date.now(); }

// The deck is paginated (every card = one viewport), so "the card in view" is just scrollTop / height.
// A short debounce lets the snap settle; the card must then hold >= 60% of the viewport.
let scrollTimer;
function cardInView() {
  const H = deck.clientHeight; if (!H) return null;
  const idx = Math.round(deck.scrollTop / H);
  const el = deck.children[idx];
  if (!el || !el.classList.contains("card")) return null;
  const overlap = H - Math.abs(deck.scrollTop - idx * H);
  return overlap / H >= 0.6 ? el.dataset.id : null;
}
function onScroll() {
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(() => { const id = cardInView(); if (id) enter(id); else leave(); }, 120);
}
deck.addEventListener("scroll", onScroll, { passive: true });

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    pause(); state.lastHidden = Date.now(); saveState();
    if (state.session) log("session_end", null, { minutes: Math.round((Date.now() - state.session.started) / 6000) / 10 });
  } else { ensureSession(); resume(); }
});
addEventListener("pagehide", () => { leave(); });

// =====================================================================
// Deck
// =====================================================================
let allCards = [], shown = [];

function updateCounter() {
  const left = shown.filter((c) => !state.seen[c.id]).length;
  counterEl.textContent = `${left} left`;
}

function buildDeck(cards, { includeSeen = false } = {}) {
  const frag = document.createDocumentFragment();
  const keys = new Set();
  shown = [];
  cards.forEach((c) => {
    const k = titleKey(c);
    if (keys.has(k)) return;                             // exact-repeat headline (until clustering lands)
    if (!includeSeen && state.seen[c.id]) return;
    keys.add(k);
    c._index = shown.length; shown.push(c);
    frag.appendChild(renderCard(c));
  });
  const end = document.createElement("div");
  end.className = "end";
  end.innerHTML = `<div><h2>You're all caught up</h2><p>${shown.length} stories · feed updated ${ago(feedGeneratedAt)}</p></div>`;
  frag.appendChild(end);
  deck.replaceChildren(frag);
  current = null; impressed.clear();
  setTimeout(() => { const id = cardInView(); if (id) enter(id); }, 50);
  updateCounter();
  fitCards();
}

async function loadFeed() {
  if (window.__FEED__) return window.__FEED__;           // test harness injection
  const res = await fetch(FEED_URL, { cache: "no-cache" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function main() {
  pruneState(); ensureSession();
  DB.pruneOlderThan(LOG_KEEP_DAYS).catch(() => {});
  let feed;
  try { feed = await loadFeed(); }
  catch (e) { deck.innerHTML = `<div class="end"><div><h2>Couldn't load the feed</h2><p>${e.message}</p></div></div>`; return; }

  feedGeneratedAt = feed.generated_at;
  allCards = feed.articles;
  allCards.forEach((c) => cardsById.set(c.id, c));
  buildDeck(allCards, { includeSeen: new URLSearchParams(location.search).has("all") });

  freshEl.textContent = ago(feed.generated_at);
  freshEl.classList.toggle("stale", (Date.now() - new Date(feed.generated_at)) / 3.6e6 > 4);

  const i = Number(new URLSearchParams(location.search).get("i"));   // dev: ?i=3 opens on card 3
  if (i > 0) deck.scrollTop = i * deck.clientHeight;
}

// One type scale for the whole deck; overflow is absorbed by a shorter image, then a fade.
function fitCards() {
  const cards = [...deck.querySelectorAll(".card")];
  const over = (c) => { const b = c.querySelector(".body"); return b.scrollHeight > b.clientHeight + 2; };
  deck.classList.remove("scale-s");
  cards.forEach((c) => c.classList.remove("shrink", "clipped"));
  if (cards.filter(over).length > cards.length * 0.15) deck.classList.add("scale-s");
  cards.forEach((c) => { if (over(c)) c.classList.add("shrink"); });
  cards.forEach((c) => { if (over(c)) c.classList.add("clipped"); });
}
let fitTimer; addEventListener("resize", () => { clearTimeout(fitTimer); fitTimer = setTimeout(fitCards, 150); });

// Pull-to-refresh: at the top of the deck, drag down; past the threshold, release to reload.
(function pullToRefresh() {
  const el = document.getElementById("ptr"), text = el.querySelector(".ptr-text");
  const THRESH = 80; let startY = null, pulling = false, dy = 0;
  deck.addEventListener("touchstart", (e) => { if (deck.scrollTop <= 0) { startY = e.touches[0].clientY; pulling = false; dy = 0; } else startY = null; }, { passive: true });
  deck.addEventListener("touchmove", (e) => {
    if (startY === null) return;
    dy = e.touches[0].clientY - startY;
    if (dy <= 0 || deck.scrollTop > 0) { if (pulling) { pulling = false; el.style.height = "0px"; } return; }
    pulling = true;
    const h = Math.min(dy * 0.5, 72);
    el.style.height = h + "px";
    const ready = dy > THRESH; el.classList.toggle("ready", ready); text.textContent = ready ? "Release to refresh" : "Pull to refresh";
  }, { passive: true });
  deck.addEventListener("touchend", () => {
    if (!pulling) { startY = null; return; }
    if (dy > THRESH) { el.classList.add("loading"); text.textContent = "Refreshing…"; el.style.height = "44px"; log("refresh", null, { via: "pull" }); setTimeout(() => location.reload(), 250); }
    else { el.style.height = "0px"; el.classList.remove("ready"); }
    startY = null; pulling = false;
  });
})();

// =====================================================================
// Header: top, data sheet (cloud), theme
// =====================================================================
document.getElementById("brand").onclick = () => deck.scrollTo({ top: 0, behavior: "smooth" });
document.getElementById("sync").onclick = () => { log("sync_open", null); openSheet(); };

async function openSheet() {
  const sheet = document.getElementById("sheet"), stats = document.getElementById("stats");
  const rows = await DB.all();
  const by = {}; rows.forEach((r) => { by[r.event] = (by[r.event] || 0) + 1; });
  const dwell = rows.filter((r) => r.event === "dwell").map((r) => r.value.dwell_ms);
  const med = dwell.length ? dwell.sort((a, b) => a - b)[Math.floor(dwell.length / 2)] : 0;
  stats.textContent =
    `events: ${rows.length}   since: ${rows[0] ? rows[0].ts.slice(0, 10) : "-"}\n` +
    Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k.padEnd(15)} ${v}`).join("\n") +
    `\n\nseen: ${Object.keys(state.seen).length}   liked: ${Object.keys(state.liked).length}   saved: ${Object.keys(state.saved).length}` +
    `\nmedian dwell: ${(med / 1000).toFixed(1)}s   session: ${state.session && state.session.id}`;
  sheet.hidden = false;
  document.getElementById("close").onclick = () => { sheet.hidden = true; };
  document.getElementById("unsee").onclick = () => { sheet.hidden = true; buildDeck(allCards, { includeSeen: true }); };
  document.getElementById("export").onclick = async () => {
    const data = { exported_at: new Date().toISOString(), state, events: await DB.all() };
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `tldr-events-${Date.now()}.json` });
    a.click(); URL.revokeObjectURL(a.href); log("export", null, { events: data.events.length });
  };
}

// Two-state theme: first launch follows the phone; a tap flips it and is remembered.
const prefersDark = matchMedia("(prefers-color-scheme: dark)");
function applyTheme(mode) { root.classList.toggle("dark", mode === "dark"); root.classList.toggle("light", mode === "light"); }
const urlTheme = new URLSearchParams(location.search).get("theme");
applyTheme(localStorage.getItem("theme") || urlTheme || (prefersDark.matches ? "dark" : "light"));
prefersDark.onchange = () => { if (!localStorage.getItem("theme")) applyTheme(prefersDark.matches ? "dark" : "light"); };
document.getElementById("theme").onclick = () => {
  const next = root.classList.contains("dark") ? "light" : "dark";
  applyTheme(next); localStorage.setItem("theme", next); log("theme", null, { theme: next });
};

main();
