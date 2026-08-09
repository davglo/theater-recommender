# theater-recommender

Personal movie/TV recommendation agent. Weekly (Monday 08:00) it pulls
candidates from TMDB, scores them against a versioned taste profile via
Claude, and renders a dark-themed dashboard with Seen / Not Interested /
Watchlist buttons backed by a local FastAPI server and SQLite.

Claude calls go through the **Claude Code CLI in headless mode** (bundled
with the desktop app, auto-resolved by `claude_cli.py`) — billed to the
subscription, **no ANTHROPIC_API_KEY, no incremental API costs**.

## Setup

```bash
cd ~/Claude/theater-recommender
pip3 install --user -r requirements.txt
cp .env.example .env      # fill in TMDB_API_KEY (only secret needed)
```

## One-time seed bootstrap

```bash
/usr/bin/python3 seed_bootstrap.py --dry-run       # preview matches
/usr/bin/python3 seed_bootstrap.py --interactive   # insert; confirm ambiguous ones
```

Anything below 0.6 match confidence is flagged, never silently inserted.

## Weekly run

```bash
/usr/bin/python3 run_weekly.py --dry-run --limit 10   # preview pool + prompt, no writes
/usr/bin/python3 run_weekly.py --limit 10             # small first real run
/usr/bin/python3 run_weekly.py                        # full run
```

Flags: `--dry-run` (no writes, no Claude call), `--force-profile-refresh`,
`--limit N`. Dashboard lands at `output/dashboard.html`.

## Server (decision write-back)

```bash
/usr/bin/python3 server.py    # http://127.0.0.1:8757
```

`GET /` dashboard · `POST /decision` · `GET /health` · `GET /stats`.
Opened as a static file without the server, buttons show a "server offline"
toast. After a batch of decisions, `python3 render.py` re-renders the
dashboard from the DB.

## Scheduling (launchd)

```bash
cp com.dave.theaterrecommender.plist ~/Library/LaunchAgents/
cp com.dave.theaterrecommender.server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dave.theaterrecommender.plist
launchctl load ~/Library/LaunchAgents/com.dave.theaterrecommender.server.plist
# kick a run manually:
launchctl start com.dave.theaterrecommender
```

## Tests

```bash
/usr/bin/python3 -m unittest discover -s tests -v
```

## How it decides

- Clusters are nonfiction-only: `True Crime / Dark Nonfiction`,
  `Sports & Wrestling Docs`, `Other Nonfiction`. Scripted drama/comedy is
  scored low on purpose regardless of quality.
- Statuses: `seen` and `not_interested` are permanent; `pending` carries over
  (original score/blurb, "recommended N wks ago"); `watchlist` is pinned.
- Clicking **Seen** rates it 3★ immediately, then swaps to a 5-star widget you
  can click to adjust anytime — 4-5★ is a strong positive signal, 3★ neutral,
  1-2★ a soft negative (distinct from Not Interested, the hardest negative).
- Rejects are human-only: sub-threshold (<40) titles are simply never
  rendered, but stay cached so they're never re-fetched or re-scored.
- Taste profile re-derives via Claude every 20 manual decisions, weighing
  ratings as described above; cached scores from older profile versions stand.

Deferred by design: YouTube lane, email digest, JustWatch availability,
Trakt trending, OMDb ratings, rewatch queue.
