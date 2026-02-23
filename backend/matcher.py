import httpx
import json
from pathlib import Path
from backend.config import TMDB_BASE_URL, TMDB_IMAGE_BASE, SETTINGS_PATH


def get_api_key() -> str:
    if SETTINGS_PATH.exists():
        data = json.loads(SETTINGS_PATH.read_text())
        return data.get("tmdb_api_key", "")
    return ""


def poster_url(path: str, size: str = "w500") -> str | None:
    if not path:
        return None
    return f"{TMDB_IMAGE_BASE}/{size}{path}"


def backdrop_url(path: str, size: str = "w1280") -> str | None:
    if not path:
        return None
    return f"{TMDB_IMAGE_BASE}/{size}{path}"


async def search_movie(title: str, year: int = None) -> dict | None:
    api_key = get_api_key()
    if not api_key:
        return None
    params = {"api_key": api_key, "query": title, "language": "en-US"}
    if year:
        params["year"] = year
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{TMDB_BASE_URL}/search/movie", params=params, timeout=10)
            data = r.json()
            results = data.get("results", [])
            if not results:
                return None
            best = results[0]
            # Get full details
            detail_r = await client.get(
                f"{TMDB_BASE_URL}/movie/{best['id']}",
                params={"api_key": api_key, "language": "en-US"},
                timeout=10
            )
            return detail_r.json()
        except Exception as e:
            print(f"TMDB movie search error: {e}")
            return None


async def search_tv(title: str, year: int = None) -> dict | None:
    api_key = get_api_key()
    if not api_key:
        return None
    params = {"api_key": api_key, "query": title, "language": "en-US"}
    if year:
        params["first_air_date_year"] = year
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{TMDB_BASE_URL}/search/tv", params=params, timeout=10)
            data = r.json()
            results = data.get("results", [])
            if not results:
                return None
            best = results[0]
            detail_r = await client.get(
                f"{TMDB_BASE_URL}/tv/{best['id']}",
                params={"api_key": api_key, "language": "en-US"},
                timeout=10
            )
            return detail_r.json()
        except Exception as e:
            print(f"TMDB TV search error: {e}")
            return None


async def search_movie_multi(title: str, year: int = None) -> list:
    """Return multiple search results for manual matching."""
    api_key = get_api_key()
    if not api_key:
        return []
    params = {"api_key": api_key, "query": title, "language": "en-US"}
    if year:
        params["year"] = year
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{TMDB_BASE_URL}/search/movie", params=params, timeout=10)
            results = r.json().get("results", [])[:8]
            return [{
                "id": m.get("id"),
                "title": m.get("title"),
                "year": m.get("release_date", "")[:4] or None,
                "overview": m.get("overview"),
                "poster_path": poster_url(m.get("poster_path"), "w185"),
                "rating": m.get("vote_average"),
            } for m in results]
        except Exception as e:
            print(f"TMDB multi search error: {e}")
            return []


async def search_tv_multi(title: str, year: int = None) -> list:
    """Return multiple TV search results for manual matching."""
    api_key = get_api_key()
    if not api_key:
        return []
    params = {"api_key": api_key, "query": title, "language": "en-US"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{TMDB_BASE_URL}/search/tv", params=params, timeout=10)
            results = r.json().get("results", [])[:8]
            return [{
                "id": m.get("id"),
                "title": m.get("name"),
                "year": m.get("first_air_date", "")[:4] or None,
                "overview": m.get("overview"),
                "poster_path": poster_url(m.get("poster_path"), "w185"),
                "rating": m.get("vote_average"),
            } for m in results]
        except Exception as e:
            print(f"TMDB TV multi search error: {e}")
            return []


async def get_season_episodes(tmdb_show_id: int, season: int) -> dict:
    """
    Fetch all episodes in a season in ONE API call.
    Returns a dict keyed by episode number → episode data dict.
    Much faster than one call per episode.
    """
    api_key = get_api_key()
    if not api_key:
        return {}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{TMDB_BASE_URL}/tv/{tmdb_show_id}/season/{season}",
                params={"api_key": api_key, "language": "en-US"},
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                return {ep["episode_number"]: ep for ep in data.get("episodes", [])}
        except Exception as e:
            print(f"TMDB season fetch error (show={tmdb_show_id} s{season}): {e}")
    return {}


async def get_episode_details(tmdb_show_id: int, season: int, episode: int) -> dict | None:
    api_key = get_api_key()
    if not api_key:
        return None
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{TMDB_BASE_URL}/tv/{tmdb_show_id}/season/{season}/episode/{episode}",
                params={"api_key": api_key, "language": "en-US"},
                timeout=10
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"TMDB episode error: {e}")
    return None
