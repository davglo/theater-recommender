"""Thin TMDB API wrapper: search, discover, recommendations, details.

Counts every HTTP call (logged to the runs table) and sleeps briefly between
calls to stay polite. Supports both v3 API keys (query param) and v4 read
access tokens (Bearer header) — TMDB hands out both.
"""
import logging
import time
from typing import Dict, List, Optional

import requests

import config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"


class TMDBError(Exception):
    """Raised when a TMDB request fails after retries."""


class TMDBClient:
    def __init__(self, api_key: str, sleep_seconds: float = config.TMDB_SLEEP_SECONDS) -> None:
        self.sleep_seconds = sleep_seconds
        self.call_count = 0
        self.session = requests.Session()
        # v4 read access tokens are JWTs; v3 keys are 32-char hex strings.
        if api_key.startswith("eyJ"):
            self.session.headers["Authorization"] = f"Bearer {api_key}"
            self._key_param: Dict[str, str] = {}
        else:
            self._key_param = {"api_key": api_key}
        self._keyword_id_cache: Dict[str, Optional[int]] = {}

    def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        merged = dict(self._key_param)
        if params:
            merged.update(params)
        url = f"{BASE_URL}{path}"
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                self.call_count += 1
                resp = self.session.get(url, params=merged, timeout=30)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", "2"))
                    logger.warning("TMDB 429 on %s, sleeping %.1fs", path, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                time.sleep(self.sleep_seconds)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_err = exc
                logger.warning("TMDB call failed (attempt %d/3) %s: %s", attempt + 1, path, exc)
                time.sleep(1.5 * (attempt + 1))
        raise TMDBError(f"TMDB request failed after retries: {path}: {last_err}")

    # --- endpoints ------------------------------------------------------------

    def search(self, query: str, media_type: str, year: Optional[int] = None) -> List[Dict]:
        params: Dict = {"query": query, "include_adult": "false"}
        if year:
            params["first_air_date_year" if media_type == "tv" else "primary_release_year"] = year
        data = self._get(f"/search/{media_type}", params)
        return [normalize_result(r, media_type) for r in data.get("results", [])]

    def search_keyword_id(self, keyword: str) -> Optional[int]:
        """Resolve a keyword string to its TMDB keyword id (cached per client)."""
        if keyword in self._keyword_id_cache:
            return self._keyword_id_cache[keyword]
        data = self._get("/search/keyword", {"query": keyword})
        results = data.get("results", [])
        kid: Optional[int] = None
        for r in results:
            if str(r.get("name", "")).lower() == keyword.lower():
                kid = r.get("id")
                break
        if kid is None and results:
            kid = results[0].get("id")
        if kid is None:
            logger.warning("no TMDB keyword id found for %r", keyword)
        self._keyword_id_cache[keyword] = kid
        return kid

    def discover(
        self,
        media_type: str,
        with_genres: str,
        keyword_ids: List[int],
        vote_floor: int,
        date_gte: str,
        page: int = 1,
        sort_by: str = "popularity.desc",
    ) -> List[Dict]:
        date_field = "first_air_date.gte" if media_type == "tv" else "primary_release_date.gte"
        params: Dict = {
            "with_genres": with_genres,
            "vote_count.gte": vote_floor,
            "sort_by": sort_by,
            "include_adult": "false",
            "with_original_language": config.ORIGINAL_LANGUAGE,
            date_field: date_gte,
            "page": page,
        }
        if keyword_ids:
            params["with_keywords"] = "|".join(str(k) for k in keyword_ids)  # OR
        data = self._get(f"/discover/{media_type}", params)
        return [normalize_result(r, media_type) for r in data.get("results", [])]

    def recommendations(self, media_type: str, tmdb_id: int, page: int = 1) -> List[Dict]:
        data = self._get(f"/{media_type}/{tmdb_id}/recommendations", {"page": page})
        return [normalize_result(r, media_type) for r in data.get("results", [])]

    def details(self, media_type: str, tmdb_id: int) -> Dict:
        """Full details incl. genre names, keywords, and a trailer link, normalized."""
        data = self._get(f"/{media_type}/{tmdb_id}",
                         {"append_to_response": "keywords,videos"})
        out = normalize_result(data, media_type)
        out["genre_names"] = [
            g.get("name", "") for g in data.get("genres", []) if g.get("name")
        ]
        kw_block = data.get("keywords", {}) or {}
        kw_list = kw_block.get("results") if media_type == "tv" else kw_block.get("keywords")
        out["keyword_names"] = [
            k.get("name", "") for k in (kw_list or []) if k.get("name")
        ]
        videos = (data.get("videos", {}) or {}).get("results", []) or []
        out["trailer_url"] = _pick_trailer_url(videos)
        return out


def _pick_trailer_url(videos: List[Dict]) -> Optional[str]:
    """Best YouTube trailer link: official Trailer > any Trailer > any Teaser."""
    def score(v: Dict) -> int:
        if v.get("site") != "YouTube":
            return -1
        if v.get("type") == "Trailer":
            return 2 if v.get("official") else 1
        if v.get("type") == "Teaser":
            return 0
        return -1

    best = max(videos, key=score, default=None)
    if best is None or score(best) < 0:
        return None
    return f"https://www.youtube.com/watch?v={best['key']}"


def normalize_result(raw: Dict, media_type: str) -> Dict:
    """Map a raw TMDB result to our common candidate shape. Defensive: missing
    fields become None rather than raising."""
    if media_type == "tv":
        title = raw.get("name") or raw.get("original_name") or ""
        date = raw.get("first_air_date") or ""
    else:
        title = raw.get("title") or raw.get("original_title") or ""
        date = raw.get("release_date") or ""
    year: Optional[int] = None
    if len(date) >= 4 and date[:4].isdigit():
        year = int(date[:4])
    return {
        "tmdb_id": raw.get("id"),
        "media_type": media_type,
        "title": title,
        "year": year,
        "release_date": date or None,
        "original_language": raw.get("original_language"),
        "overview": raw.get("overview") or "",
        "poster_path": raw.get("poster_path"),
        "tmdb_rating": raw.get("vote_average"),
        "vote_count": raw.get("vote_count") or 0,
        "popularity": raw.get("popularity") or 0.0,
    }
