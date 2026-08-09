"""Render the self-contained dark-themed HTML dashboard.

The digest payload is embedded as JSON and rendered client-side (enables the
cluster filter chips and search box without a server). Decision buttons POST
to the local FastAPI server; if it's not running, a toast says so instead of
silently failing.

Standalone rebuild (e.g. after making decisions):
    python3 render.py
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import config

logger = logging.getLogger(__name__)

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Theater Recommender</title>
<style>
  :root {
    --bg: #0f1419; --panel: #1a212b; --panel2: #212a36; --accent: #4aa8ff;
    --text: #d8dee6; --muted: #8494a8; --good: #7ee787; --bad: #ff6b6b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  header { padding: 22px 28px 10px; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  h1 .tag { color: var(--accent); }
  .summary { color: var(--muted); font-size: 13px; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 28px; align-items: center; }
  .chip { border: 1px solid #2c3947; background: var(--panel); color: var(--text);
          border-radius: 14px; padding: 4px 12px; font-size: 13px; cursor: pointer; }
  .chip.active { border-color: var(--accent); color: var(--accent); }
  #search { background: var(--panel); border: 1px solid #2c3947; color: var(--text);
            border-radius: 8px; padding: 6px 10px; font-size: 13px; width: 220px; }
  section { padding: 6px 28px 18px; }
  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em;
       color: var(--muted); border-bottom: 1px solid #263140; padding-bottom: 6px;
       margin-bottom: 4px; }
  .sec-hint { color: var(--muted); font-size: 12.5px; margin: 0 0 12px; }
  #sec-rate_history .card { border-color: #3a4656; }
  .rate-prompt { font-size: 12px; color: var(--muted); margin-top: 6px;
                 display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .rate-prompt .skip { background: none; border: 1px solid #2c3947; color: var(--muted);
                       border-radius: 6px; padding: 3px 8px; font-size: 12px; cursor: pointer; }
  .rate-prompt .skip:hover { border-color: var(--muted); color: var(--text); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
  .card { background: var(--panel); border: 1px solid #263140; border-radius: 10px;
          overflow: hidden; display: flex; transition: opacity .4s; }
  .card.decided { opacity: .25; pointer-events: none; }
  .card.rated { opacity: .55; }
  .stars { display: inline-flex; gap: 2px; }
  .stars span { cursor: pointer; font-size: 15px; color: #3a4656; line-height: 1; }
  .stars span.filled { color: #ffd166; }
  .poster { width: 92px; min-height: 138px; background: var(--panel2); flex-shrink: 0;
            background-size: cover; background-position: center; }
  .body { padding: 10px 12px; flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .title { font-weight: 600; font-size: 14px; }
  .meta { color: var(--muted); font-size: 12px; margin: 2px 0 6px; }
  .badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 4px;
           background: var(--panel2); color: var(--muted); text-transform: uppercase;
           margin-left: 6px; vertical-align: 1px; }
  .cluster { font-size: 11px; font-weight: 600; }
  .fitbar { height: 5px; background: var(--panel2); border-radius: 3px; margin: 6px 0; }
  .fitbar > div { height: 100%; border-radius: 3px; }
  .overview { font-size: 12.5px; color: var(--text); flex: 1; margin: 2px 0 4px; }
  .why { font-size: 11.5px; color: var(--muted); font-style: italic; margin-bottom: 4px; }
  .deal { font-size: 11.5px; color: var(--bad); margin-top: 4px; }
  .foot { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
  .rating { color: var(--muted); font-size: 12px; }
  .fit { font-weight: 700; font-size: 13px; }
  .trailer { font-size: 11.5px; color: var(--accent); text-decoration: none;
             border: 1px solid #2c3947; border-radius: 6px; padding: 2px 7px; }
  .trailer:hover { border-color: var(--accent); }
  .btns { display: flex; gap: 6px; }
  .btns button { background: var(--panel2); color: var(--text); border: 1px solid #2c3947;
                 border-radius: 6px; padding: 3px 8px; font-size: 12px; cursor: pointer; }
  .btns button:hover { border-color: var(--accent); }
  .age { color: var(--muted); font-size: 11px; margin-left: 6px; }
  .empty { color: var(--muted); font-size: 13px; }
  #toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%);
           background: var(--bad); color: #14181d; font-weight: 600; padding: 10px 18px;
           border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity .3s;
           pointer-events: none; }
  #toast.show { opacity: 1; }
</style>
</head>
<body>
<header>
  <h1>Theater <span class="tag">Recommender</span></h1>
  <div class="summary" id="summary"></div>
</header>
<div class="controls">
  <input id="search" type="search" placeholder="Search titles…">
  <span id="chips"></span>
</div>
<section id="sec-rate_history">
  <h2>Rate Your History</h2>
  <div class="sec-hint">Seen these? Rate the ones you know so the engine learns your taste. Skip what you haven't.</div>
  <div class="grid"></div>
</section>
<section id="sec-watchlist"><h2>Watchlist</h2><div class="grid"></div></section>
<section id="sec-recent"><h2>Recently Released &middot; Last 6 Months</h2><div class="grid"></div></section>
<section id="sec-recommendations"><h2>Recommendations &middot; Older</h2><div class="grid"></div></section>
<div id="toast"></div>
<script>
const DATA = __DATA__;
const ACCENTS = __ACCENTS__;
const SERVER = "http://__HOST__:__PORT__";
const IMG = "https://image.tmdb.org/t/p/w342";

let activeCluster = "All";
let query = "";

function accent(cluster) { return ACCENTS[cluster] || "var(--accent)"; }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function truncate(s, max) {
  s = s || "";
  return s.length > max ? s.slice(0, max).trimEnd() + "…" : s;
}

function card(item, section) {
  const el = document.createElement("div");
  el.className = "card";
  el.dataset.cluster = item.cluster;
  el.dataset.title = (item.title || "").toLowerCase();
  const col = accent(item.cluster);
  let age = "";
  if (section === "recent" && item.days_since_release != null) {
    const d = item.days_since_release;
    age = `<span class="age">released ${d === 0 ? "today" : d + " day" + (d > 1 ? "s" : "") + " ago"}</span>`;
  } else if (section === "recommendations" && item.weeks_ago > 0) {
    age = `<span class="age">recommended ${item.weeks_ago} wk${item.weeks_ago > 1 ? "s" : ""} ago</span>`;
  }
  const deal = item.dealbreakers
    ? `<div class="deal">&#9888; ${esc(item.dealbreakers)}</div>` : "";
  // Every card gets a trailer link: TMDB's YouTube link when it has one,
  // otherwise a YouTube search for "<title> <year> trailer".
  const trailerHref = item.trailer_url ||
    ("https://www.youtube.com/results?search_query=" +
     encodeURIComponent(`${item.title || ""} ${item.year || ""} trailer`.trim()));
  const trailer = `<a class="trailer" href="${esc(trailerHref)}" target="_blank" rel="noopener">&#9654; Trailer</a>`;
  const buttons = section === "watchlist"
    ? `<button data-s="seen">Seen</button><button data-s="not_interested">Nope</button>`
    : `<button data-s="seen">Seen</button><button data-s="not_interested">Nope</button><button data-s="watchlist">&#9733; Watch</button>`;
  el.innerHTML = `
    <div class="poster" style="${item.poster_path ? `background-image:url('${IMG}${esc(item.poster_path)}')` : ""}"></div>
    <div class="body">
      <div class="title">${esc(item.title)}
        <span class="meta">${item.year || ""}</span>
        <span class="badge">${item.media_type}</span>${age}</div>
      <div class="cluster" style="color:${col}">${esc(item.cluster)}</div>
      <div class="fitbar"><div style="width:${item.fit_score}%;background:${col}"></div></div>
      <div class="overview">${esc(truncate(item.overview, 320))}</div>
      <div class="why">${esc(item.why)}</div>
      ${deal}
      <div class="foot">
        <span><span class="fit" style="color:${col}">${item.fit_score}</span>
          <span class="rating">&nbsp;TMDB ${item.tmdb_rating ? Number(item.tmdb_rating).toFixed(1) : "–"}</span>
          ${trailer ? " &nbsp;" + trailer : ""}</span>
        <span class="btns">${buttons}</span>
      </div>
    </div>`;
  el.querySelectorAll(".btns button").forEach(btn => {
    const s = btn.dataset.s;
    btn.addEventListener("click", () => s === "seen" ? markSeen(item, el) : decide(item, s, el));
  });
  return el;
}

function postDecision(item, status, rating) {
  const body = {tmdb_id: item.tmdb_id, media_type: item.media_type, status: status};
  if (rating != null) body.rating = rating;
  return fetch(SERVER + "/decision", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  }).then(r => { if (!r.ok) throw new Error("HTTP " + r.status); });
}

function decide(item, status, el) {
  postDecision(item, status, null).then(() => {
    el.classList.add("decided");
  }).catch(() => {
    toast("Server offline — start server.py to record decisions");
  });
}

function starsRow(rating, onPick) {
  const row = document.createElement("span");
  row.className = "stars";
  for (let i = 1; i <= 5; i++) {
    const s = document.createElement("span");
    s.textContent = "★";
    if (i <= rating) s.classList.add("filled");
    s.addEventListener("click", () => onPick(i));
    row.appendChild(s);
  }
  return row;
}

function renderRatingWidget(btns, item, rating) {
  btns.innerHTML = "";
  btns.appendChild(starsRow(rating, newRating => {
    postDecision(item, "seen", newRating)
      .then(() => renderRatingWidget(btns, item, newRating))
      .catch(() => toast("Server offline — rating not saved"));
  }));
}

function markSeen(item, el) {
  const btns = el.querySelector(".btns");
  const defaultRating = 3;
  postDecision(item, "seen", defaultRating).then(() => {
    el.classList.add("rated");
    renderRatingWidget(btns, item, defaultRating);
  }).catch(() => {
    toast("Server offline — start server.py to record decisions");
  });
}

// --- Rate Your History cards -------------------------------------------------

function postSkip(item) {
  return fetch(SERVER + "/skip_history", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({tmdb_id: item.tmdb_id, media_type: item.media_type}),
  }).then(r => { if (!r.ok) throw new Error("HTTP " + r.status); });
}

function historyCard(item) {
  const el = document.createElement("div");
  el.className = "card";
  el.dataset.title = (item.title || "").toLowerCase();
  const trailerHref = item.trailer_url ||
    ("https://www.youtube.com/results?search_query=" +
     encodeURIComponent(`${item.title || ""} ${item.year || ""} trailer`.trim()));
  el.innerHTML = `
    <div class="poster" style="${item.poster_path ? `background-image:url('${IMG}${esc(item.poster_path)}')` : ""}"></div>
    <div class="body">
      <div class="title">${esc(item.title)}
        <span class="meta">${item.year || ""}</span>
        <span class="badge">${item.media_type}</span></div>
      <div class="overview">${esc(truncate(item.overview, 260))}</div>
      <div class="foot">
        <span class="rating">TMDB ${item.tmdb_rating ? Number(item.tmdb_rating).toFixed(1) : "–"}
          &nbsp;<a class="trailer" href="${esc(trailerHref)}" target="_blank" rel="noopener">&#9654; Trailer</a></span>
      </div>
      <div class="rate-prompt">
        <span>Seen it?</span>
        <span class="stars-slot"></span>
        <button class="skip">Haven't seen</button>
      </div>
    </div>`;
  const slot = el.querySelector(".stars-slot");
  slot.appendChild(starsRow(0, rating => {
    postDecision(item, "seen", rating)
      .then(() => el.classList.add("decided"))
      .catch(() => toast("Server offline — rating not saved"));
  }));
  el.querySelector(".skip").addEventListener("click", () => {
    postSkip(item)
      .then(() => el.classList.add("decided"))
      .catch(() => toast("Server offline — try again"));
  });
  return el;
}

let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

// Sections that vanish entirely when empty (rather than showing "Nothing here").
const HIDE_WHEN_EMPTY = new Set(["watchlist", "rate_history"]);

function renderAll() {
  for (const section of ["rate_history", "watchlist", "recent", "recommendations"]) {
    const sec = document.getElementById("sec-" + section);
    const grid = sec.querySelector(".grid");
    grid.innerHTML = "";
    // Pure score order (server already sorts by fit_score desc); cluster is a
    // filter chip, not a grouping. Rate-history keeps the server's acclaim order.
    const items = section === "rate_history"
      ? DATA[section].slice()
      : DATA[section].slice().sort((a, b) => b.fit_score - a.fit_score);
    let shown = 0;
    for (const item of items) {
      if (section !== "rate_history" && activeCluster !== "All" && item.cluster !== activeCluster) continue;
      if (query && !(item.title || "").toLowerCase().includes(query)) continue;
      grid.appendChild(section === "rate_history" ? historyCard(item) : card(item, section));
      shown++;
    }
    if (!shown && !HIDE_WHEN_EMPTY.has(section)) {
      const d = document.createElement("div");
      d.className = "empty";
      d.textContent = "Nothing here.";
      grid.appendChild(d);
    }
    sec.style.display = (DATA[section].length || !HIDE_WHEN_EMPTY.has(section)) ? "" : "none";
  }
}

function buildChips() {
  const holder = document.getElementById("chips");
  const clusters = ["All", ...Object.keys(ACCENTS)];
  for (const c of clusters) {
    const b = document.createElement("button");
    b.className = "chip" + (c === "All" ? " active" : "");
    b.textContent = c;
    if (c !== "All") b.style.borderColor = accent(c);
    b.addEventListener("click", () => {
      activeCluster = c;
      document.querySelectorAll(".chip").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      renderAll();
    });
    holder.appendChild(b);
  }
}

document.getElementById("search").addEventListener("input", e => {
  query = e.target.value.trim().toLowerCase();
  renderAll();
});

const s = DATA.summary;
document.getElementById("summary").textContent =
  `${s.rate_history_count ? s.rate_history_count + " to rate · " : ""}` +
  `${s.recent_count} recently released · ${s.recommendations_count} recommendations · ` +
  `${s.watchlist_count} on watchlist · generated ${DATA.generated_at}`;
buildChips();
renderAll();
</script>
</body>
</html>
"""


def build_html(digest: Dict) -> str:
    payload = dict(digest)
    payload["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        TEMPLATE
        .replace("__DATA__", json.dumps(payload))
        .replace("__ACCENTS__", json.dumps(config.CLUSTER_ACCENTS))
        .replace("__HOST__", config.SERVER_HOST)
        .replace("__PORT__", str(config.SERVER_PORT))
    )


def render_dashboard(digest: Dict, out_path: Optional[Path] = None) -> Path:
    """Write a static snapshot to disk (used by the weekly cron run so a copy
    exists even if the server isn't running). The live server renders fresh
    on every request instead of serving this file — see server.py."""
    path = out_path or config.DASHBOARD_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(digest), encoding="utf-8")
    logger.info("dashboard written: %s", path)
    return path


def rebuild_from_db() -> Path:
    import db
    from digest import build_digest

    conn = db.connect()
    return render_dashboard(build_digest(conn, config.SCORE_THRESHOLD))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    rebuild_from_db()
