"""Local write-back server: serves the dashboard and records decisions.

Binds 127.0.0.1 only. Run directly (python3 server.py) or via launchd.
"""
import logging
import sqlite3
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
import db
import digest as digest_mod
import render

logger = logging.getLogger("server")

app = FastAPI(title="theater-recommender", docs_url=None, redoc_url=None)

# The dashboard may be opened as a file:// static page (origin "null"), so
# allow all origins — the server itself is bound to loopback only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

DECIDABLE = ("seen", "not_interested", "watchlist")


class Decision(BaseModel):
    tmdb_id: int
    media_type: str
    status: str
    rating: Optional[int] = None  # 1-5, only meaningful when status == 'seen'


def _conn() -> sqlite3.Connection:
    return db.connect()


@app.get("/")
def dashboard() -> HTMLResponse:
    """Renders fresh from the DB on every request — decisions made via
    /decision show up immediately on refresh, unlike the static file written
    by the weekly cron run."""
    conn = _conn()
    try:
        if db.last_run(conn) is None:
            raise HTTPException(404, "no run yet — run run_weekly.py")
        result = digest_mod.build_digest(conn, config.SCORE_THRESHOLD)
    finally:
        conn.close()
    return HTMLResponse(render.build_html(result))


@app.post("/decision")
def decision(d: Decision) -> Dict:
    if d.media_type not in ("movie", "tv"):
        raise HTTPException(422, f"invalid media_type: {d.media_type}")
    if d.status not in DECIDABLE:
        raise HTTPException(422, f"invalid status: {d.status} (allowed: {DECIDABLE})")
    if d.rating is not None and not (1 <= d.rating <= 5):
        raise HTTPException(422, f"rating must be 1-5: {d.rating}")
    conn = _conn()
    try:
        if not db.title_exists(conn, d.tmdb_id, d.media_type):
            raise HTTPException(404, f"unknown title: {d.tmdb_id}/{d.media_type}")
        db.set_status(conn, d.tmdb_id, d.media_type, d.status, source="manual",
                      decided=True, rating=d.rating)
        # A decision resolves any pending Rate-Your-History prompt for this title.
        db.clear_rate_prompt(conn, d.tmdb_id, d.media_type)
        conn.commit()
        logger.info("decision: %s/%s -> %s (rating=%s)",
                    d.tmdb_id, d.media_type, d.status, d.rating)
    finally:
        conn.close()
    return {"ok": True}


class Skip(BaseModel):
    tmdb_id: int
    media_type: str


@app.post("/skip_history")
def skip_history(s: Skip) -> Dict:
    """'Haven't seen it' on a Rate-Your-History card: drop the prompt without
    recording a status, so the title stays eligible for normal recommendations."""
    conn = _conn()
    try:
        db.clear_rate_prompt(conn, s.tmdb_id, s.media_type)
        conn.commit()
        logger.info("skip_history: %s/%s", s.tmdb_id, s.media_type)
    finally:
        conn.close()
    return {"ok": True}


@app.get("/health")
def health() -> Dict:
    return {"status": "ok"}


@app.get("/stats")
def stats() -> JSONResponse:
    conn = _conn()
    try:
        counts = db.status_counts(conn)
        latest = db.latest_profile(conn)
        since = latest["derived_at"] if latest else None
        decisions = db.count_manual_decisions_since(conn, since)
        run = db.last_run(conn)
        rate_prompts = db.rate_prompt_count(conn)
    finally:
        conn.close()
    return JSONResponse({
        "status_counts": counts,
        "profile_version": latest["version"] if latest else None,
        "decisions_since_profile": decisions,
        "decisions_until_rederive": max(0, config.REDERIVE_INTERVAL - decisions),
        "rate_history_pending": rate_prompts,
        "last_run": dict(run) if run else None,
    })


if __name__ == "__main__":
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # PORT env override lets a second instance (e.g. a dev preview) coexist
    # with the launchd service on SERVER_PORT; both share the same SQLite DB.
    port = int(os.environ.get("PORT", config.SERVER_PORT))
    uvicorn.run(app, host=config.SERVER_HOST, port=port, log_level="warning")
