"""Claude batch scoring pass: score novel candidates against the taste profile.

Calls go through the Claude Code CLI (subscription, no API costs). Results are
cached in the scores table — a title is scored exactly once, under whatever
profile version was current at the time. Parsing is defensive: fences
stripped, ids validated against the input set, malformed entries logged and
skipped.
"""
import json
import logging
import re
import sqlite3
from typing import Dict, List, Optional, Set, Tuple

import claude_cli
import config
import db

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You score movie/TV candidates against a personal taste profile. "
    "For EVERY candidate, output one object: {\"tmdb_id\": int, \"media_type\": "
    "\"movie\"|\"tv\", \"cluster\": <one of the profile's cluster names>, "
    "\"fit_score\": 0-100, \"why\": one-line rationale tied to specific taste "
    "signals, \"dealbreakers\": string or null}. "
    "Be genuinely discriminating: fit_score below 50 should be common. "
    "Popularity is NOT fit. Penalize tonal mismatch hard (e.g., wholesome "
    "feel-good or educational content vs. this user's preference for dark, "
    "investigative, morally complex, or irreverent material). "
    "Candidates are recent releases. Score TASTE FIT ONLY — recency and trending "
    "buzz are applied separately in ranking, so neither reward nor penalize "
    "them here. For a returning series (new season), judge the show as a whole. "
    "The user has FOUR lanes. Documentaries: (1) sports docs (all sports — "
    "NFL/NBA/soccer/wrestling/boxing/racing, team- and personality-driven), "
    "(2) true crime (murder, serial killers, manhunts, missing persons, cold "
    "cases), (3) scams/cons/financial & corporate crime/cults/corruption. "
    "Scripted: (4) prestige drama & crime series — gangster/crime sagas (Peaky "
    "Blinders, The Gentlemen, MobLand, Sons of Anarchy, The Wire), serialized "
    "prestige drama with morally complex leads (Mad Men, Severance, White "
    "Lotus, Euphoria), and dark/irreverent comedy (Righteous Gemstones, "
    "Peacemaker, Bookie). "
    "OFF-SUBJECT = LOW SCORE (below 30 even if acclaimed): documentaries "
    "outside lanes 1-3 (music/concert, nature/wildlife, art/culture, science/"
    "tech, food/travel, health, general history unless crime- or scandal-"
    "driven); and scripted content outside lane 4 — broad network sitcoms, "
    "cozy/case-of-the-week procedurals, romance, teen/YA, kids/family, soaps, "
    "reality TV, and generic superhero/fantasy spectacle. "
    "FORMAT: this user watches SERIES far more than films — give multi-part "
    "series a real edge over one-off films of equal fit. "
    "HARD NEGATIVE SIGNALS — dock heavily and name the issue in dealbreakers "
    "when the description suggests any of these: (1) reenactment/dramatized-"
    "recreation-driven true crime (cheap actor recreations instead of real "
    "footage and interviews); (2) slow, artsy, meandering, mood-over-story "
    "filmmaking; (3) preachy/moralizing agenda docs or puffy, promotional, "
    "celebrity-approved fluff with no critical edge. This user bails on all "
    "three. Reward momentum, real access, investigative rigor, and craft. "
    "Assign each candidate to the single best-fitting cluster. "
    "Output ONLY a JSON array of these objects. No markdown fences, no prose. "
    "Respond directly from the information given — do not use tools, read "
    "files, or ask questions."
)


def build_prompt(profile_json: str, candidates: List[Dict],
                 blocklist: Optional[List[str]] = None) -> str:
    slim = [
        {
            "tmdb_id": c["tmdb_id"],
            "media_type": c["media_type"],
            "title": c["title"],
            "year": c["year"],
            "genres": c.get("genre_names", []),
            "keywords": c.get("keyword_names", [])[:12],
            "overview": (c.get("overview") or "")[:400],
            "tmdb_rating": c.get("tmdb_rating"),
        }
        for c in candidates
    ]
    block_line = ""
    if blocklist:
        block_line = (
            "\n\nHARD BLOCK — the user refuses content whose PRIMARY SUBJECT is any "
            "of these people/teams/subjects, regardless of how well-made it is. Give "
            "fit_score 0 and name the blocked subject in dealbreakers ONLY when they "
            "are the main focus (a league-wide or ensemble doc that merely mentions "
            "them in passing is fine — do not block those): "
            f"{', '.join(blocklist)}."
        )
    creator_line = ""
    if config.BOOSTED_PEOPLE:
        names = ", ".join(config.BOOSTED_PEOPLE.values())
        creator_line = (
            f"\n\nCREATOR BONUS — the user especially loves the work of {names}. "
            "Add +10 to +15 to anything they created, directed, or wrote (use your "
            "own knowledge of their filmographies), and a smaller bump to work in "
            "their distinctive style. The bonus never rescues an off-lane title "
            "(e.g. a family film)."
        )
    return (
        f"Taste profile:\n{profile_json}{block_line}{creator_line}\n\n"
        f"Candidates ({len(slim)}):\n{json.dumps(slim, indent=1)}"
    )


def _normalize_cluster(cluster: str) -> str:
    """Squash whitespace variance ('True Crime/Dark Nonfiction' vs the
    canonical 'True Crime / Dark Nonfiction') so cluster filter chips and
    accent colors still match even if Claude drops a space."""
    return re.sub(r"\s+", " ", cluster).strip()


def _snap_to_canonical(cluster: str) -> str:
    normalized = _normalize_cluster(cluster)
    for canonical in config.CLUSTER_NAMES:
        if re.sub(r"\s*/\s*", "/", normalized).lower() == re.sub(r"\s*/\s*", "/", canonical).lower():
            return canonical
    return normalized


def parse_scores(text: str, valid_keys: Set[Tuple[int, str]]) -> List[Dict]:
    """Parse a Claude scoring response. Returns only well-formed entries whose
    (tmdb_id, media_type) is in valid_keys; everything else is logged+skipped."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end <= start:
        logger.error("no JSON array found in scoring response")
        return []
    try:
        raw = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        logger.error("scoring response is not valid JSON: %s", exc)
        return []
    if not isinstance(raw, list):
        logger.error("scoring response is not a JSON array")
        return []

    out: List[Dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            logger.warning("skipping non-object entry: %r", entry)
            continue
        tmdb_id = entry.get("tmdb_id")
        media_type = entry.get("media_type")
        fit = entry.get("fit_score")
        why = entry.get("why")
        cluster = entry.get("cluster")
        if not isinstance(tmdb_id, int) or (tmdb_id, media_type) not in valid_keys:
            logger.warning("skipping entry with unknown id: %r/%r", tmdb_id, media_type)
            continue
        if not isinstance(fit, int) or not isinstance(why, str) or not why.strip():
            logger.warning("skipping malformed entry for id %s: %r", tmdb_id, entry)
            continue
        if not isinstance(cluster, str) or not cluster.strip():
            logger.warning("skipping entry with missing cluster for id %s", tmdb_id)
            continue
        cluster = _snap_to_canonical(cluster)
        if cluster not in config.CLUSTER_NAMES:
            logger.warning("non-canonical cluster %r for id %s (keeping)", cluster, tmdb_id)
        dealbreakers = entry.get("dealbreakers")
        if dealbreakers is not None and not isinstance(dealbreakers, str):
            dealbreakers = str(dealbreakers)
        out.append({
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "cluster": cluster.strip(),
            "fit_score": max(0, min(100, fit)),
            "why": why.strip(),
            "dealbreakers": dealbreakers,
        })
    return out


def score_candidates(
    conn: sqlite3.Connection,
    candidates: List[Dict],
    profile_row: sqlite3.Row,
    dry_run: bool = False,
) -> Tuple[List[Dict], int]:
    """Score all un-cached candidates in chunks. Returns (new score rows,
    Claude call count). Cached titles are never re-sent."""
    already = db.scored_keys(conn)
    novel = [c for c in candidates
             if (c["tmdb_id"], c["media_type"]) not in already]
    if len(novel) < len(candidates):
        logger.info("%d candidates already cached, %d to score",
                    len(candidates) - len(novel), len(novel))
    if not novel:
        return [], 0

    blocklist = db.blocklist_labels(conn)

    if dry_run:
        prompt = build_prompt(profile_row["profile_json"], novel, blocklist)
        print("[dry-run] scoring prompt (no API call):\n")
        print(SYSTEM_PROMPT + "\n\n" + prompt)
        return [], 0

    scored: List[Dict] = []
    calls = 0
    for i in range(0, len(novel), config.SCORER_CHUNK_SIZE):
        chunk = novel[i:i + config.SCORER_CHUNK_SIZE]
        valid_keys = {(c["tmdb_id"], c["media_type"]) for c in chunk}
        prompt = build_prompt(profile_row["profile_json"], chunk, blocklist)
        try:
            text = claude_cli.call(SYSTEM_PROMPT, prompt)
            calls += 1
        except claude_cli.ClaudeCLIError as exc:
            logger.error("scoring call failed for chunk %d: %s", i // config.SCORER_CHUNK_SIZE, exc)
            continue
        entries = parse_scores(text, valid_keys)
        logger.info("chunk %d: %d/%d candidates scored",
                    i // config.SCORER_CHUNK_SIZE, len(entries), len(chunk))
        for e in entries:
            e["scored_at"] = db.now_iso()
            e["profile_ver"] = profile_row["version"]
            db.insert_score(conn, e)
            scored.append(e)
        conn.commit()
    return scored, calls
