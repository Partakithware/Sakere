import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from sqlalchemy.orm import Session

from backend.config import VIDEO_EXTENSIONS
from backend.database import Movie, TVShow, Episode
from backend import matcher

try:
    from pymediainfo import MediaInfo
    MEDIAINFO_AVAILABLE = True
except Exception:
    MEDIAINFO_AVAILABLE = False

try:
    from guessit import guessit
    GUESSIT_AVAILABLE = True
except Exception:
    GUESSIT_AVAILABLE = False


def extract_file_metadata(file_path: str) -> dict:
    """Extract technical metadata from a video file using pymediainfo."""
    meta = {
        "duration": None,
        "resolution": None,
        "video_codec": None,
        "audio_codec": None,
        "file_size": None,
    }
    try:
        meta["file_size"] = os.path.getsize(file_path)
    except Exception:
        pass

    if not MEDIAINFO_AVAILABLE:
        return meta

    try:
        info = MediaInfo.parse(file_path)
        for track in info.tracks:
            if track.track_type == "General":
                if track.duration:
                    meta["duration"] = float(track.duration) / 1000.0
            elif track.track_type == "Video":
                if track.width and track.height:
                    w = int(track.width)
                    h = int(track.height)
                    # Use width as primary signal to handle widescreen crops correctly.
                    # A 1920×800 film is a 1080p source; checking only height mis-labels it 720p.
                    if w >= 3840 or h >= 2160:
                        meta["resolution"] = "4K"
                    elif w >= 1920 or h >= 1080:
                        meta["resolution"] = "1080p"
                    elif w >= 1280 or h >= 720:
                        meta["resolution"] = "720p"
                    elif w >= 720 or h >= 480:
                        meta["resolution"] = "480p"
                    else:
                        meta["resolution"] = f"{h}p"
                if track.format:
                    meta["video_codec"] = track.format
            elif track.track_type == "Audio":
                if track.format and not meta["audio_codec"]:
                    meta["audio_codec"] = track.format
    except Exception as e:
        print(f"MediaInfo error for {file_path}: {e}")

    return meta


async def extract_file_metadata_async(file_path: str, executor: ThreadPoolExecutor) -> dict:
    """Run mediainfo in a thread so it doesn't block the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, extract_file_metadata, file_path)


# ── Scan-session caches (reset each scan) ───────────────────────────────────
# show_cache:   tmdb_id → TVShow  (avoids repeated DB round-trips per show)
# season_cache: (tmdb_id, season) → {ep_num: ep_data}  (1 API call per season)
_show_cache:   dict = {}
_season_cache: dict = {}


def parse_filename(file_path: str) -> dict:
    """Use guessit to parse metadata from filename."""
    result = {
        "title": None,
        "year": None,
        "season": None,
        "episode": None,
        "media_type": None,  # 'movie' or 'episode'
    }
    if not GUESSIT_AVAILABLE:
        stem = Path(file_path).stem
        result["title"] = stem.replace(".", " ").replace("_", " ")
        return result

    try:
        guess = guessit(file_path)

        raw_title = guess.get("title")

        # guessit splits titles on commas and returns a list — e.g.
        # "Scooby-Doo, Where Are You!" → ['Scooby-Doo', ' Where Are You!']
        # When that happens, use the immediate parent directory name instead
        # (which almost always has the full clean show title).
        if isinstance(raw_title, list):
            parent = Path(file_path).parent
            # Walk up until we find a dir that doesn't look like "Season X" / "S01"
            for part in [parent, parent.parent, parent.parent.parent]:
                name = part.name
                if name and not any(kw in name.lower() for kw in ("season", "series", "disc", "disk")):
                    import re as _re
                    # Strip trailing (year) from folder name
                    clean = _re.sub(r'\s*\(\d{4}\)\s*$', '', name).strip()
                    if clean:
                        raw_title = clean
                        break
            else:
                raw_title = " ".join(raw_title)  # last resort: join the list

        result["title"] = raw_title
        result["year"]  = guess.get("year")

        season = guess.get("season")
        episode = guess.get("episode")
        if isinstance(season,  list): season  = season[0]
        if isinstance(episode, list): episode = episode[0]
        result["season"]  = int(season)  if season  is not None else None
        result["episode"] = int(episode) if episode is not None else None

        t = guess.get("type", "")
        if t == "episode":
            result["media_type"] = "episode"
        elif t == "movie":
            result["media_type"] = "movie"
    except Exception as e:
        print(f"Guessit error for {file_path}: {e}")

    return result


def find_video_files(directory: str) -> list[str]:
    """Recursively find all video files in a directory."""
    files = []
    for root, dirs, filenames in os.walk(directory):
        # Skip hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for filename in filenames:
            if Path(filename).suffix.lower() in VIDEO_EXTENSIONS:
                files.append(os.path.join(root, filename))
    return files


async def get_or_create_show(title: str, year: int, db: Session) -> TVShow:
    """
    Find or create a TVShow with in-memory cache so every episode of the
    same show doesn't trigger a fresh TMDB search + DB query.
    """
    global _show_cache

    if isinstance(title, list):
        title = " ".join(str(t) for t in title)
    title = str(title).strip()

    tmdb_show  = await matcher.search_tv(title, year)
    tmdb_id    = tmdb_show.get("id")          if tmdb_show else None
    tmdb_title = tmdb_show.get("name", title) if tmdb_show else title

    # 1. In-memory cache — instant for every episode after the first per show
    if tmdb_id and tmdb_id in _show_cache:
        return _show_cache[tmdb_id]

    # 2. DB lookup by tmdb_id
    if tmdb_id:
        show = db.query(TVShow).filter(TVShow.tmdb_id == tmdb_id).first()
        if show:
            _show_cache[tmdb_id] = show
            return show

    # 3. DB lookup by canonical TMDB title
    show = db.query(TVShow).filter(TVShow.title == tmdb_title).first()
    if show:
        if tmdb_id: _show_cache[tmdb_id] = show
        return show

    # 4. DB lookup by raw guessit title
    if title != tmdb_title:
        show = db.query(TVShow).filter(TVShow.title == title).first()
        if show:
            if tmdb_id: _show_cache[tmdb_id] = show
            return show

    # 5. Create new record
    show = TVShow(
        title=tmdb_title,
        tmdb_id=tmdb_id,
        overview=tmdb_show.get("overview")      if tmdb_show else None,
        poster_path=matcher.poster_url(tmdb_show.get("poster_path"))      if tmdb_show else None,
        backdrop_path=matcher.backdrop_url(tmdb_show.get("backdrop_path")) if tmdb_show else None,
        rating=tmdb_show.get("vote_average")    if tmdb_show else None,
        genres=", ".join(g["name"] for g in tmdb_show.get("genres", [])) if tmdb_show else None,
        first_air_date=tmdb_show.get("first_air_date") if tmdb_show else None,
        status=tmdb_show.get("status")          if tmdb_show else None,
    )
    db.add(show)
    db.commit()
    db.refresh(show)
    if tmdb_id: _show_cache[tmdb_id] = show
    return show


async def get_episode_tmdb(tmdb_show_id: int, season: int, episode_num: int) -> dict | None:
    """
    Get episode TMDB data using season-level cache.
    First call for a (show, season) fetches ALL episodes at once — one API
    call covers an entire season instead of one per episode.
    """
    global _season_cache
    key = (tmdb_show_id, season)
    if key not in _season_cache:
        _season_cache[key] = await matcher.get_season_episodes(tmdb_show_id, season)
    return _season_cache[key].get(episode_num)


async def scan_episode(file_path: str, db: Session, executor: ThreadPoolExecutor) -> Episode | None:
    """Scan a single episode file and upsert in DB."""
    existing = db.query(Episode).filter(Episode.file_path == file_path).first()
    if existing:
        return existing

    # Run blocking mediainfo in thread pool — doesn't stall the event loop
    file_meta   = await extract_file_metadata_async(file_path, executor)
    parsed      = parse_filename(file_path)
    title       = parsed["title"] or Path(file_path).stem
    season      = parsed["season"]      or 1
    episode_num = parsed["episode"]     or 1
    year        = parsed["year"]

    if isinstance(episode_num, list): episode_num = int(episode_num[0])
    if isinstance(season,      list): season      = int(season[0])
    episode_num = int(episode_num)
    season      = int(season)

    show    = await get_or_create_show(title, year, db)
    ep_tmdb = await get_episode_tmdb(show.tmdb_id, season, episode_num) if show.tmdb_id else None

    ep = Episode(
        show_id=show.id,
        file_path=file_path,
        title=ep_tmdb.get("name")      if ep_tmdb else f"Episode {episode_num}",
        season=season,
        episode=episode_num,
        tmdb_id=ep_tmdb.get("id")      if ep_tmdb else None,
        overview=ep_tmdb.get("overview") if ep_tmdb else None,
        still_path=matcher.poster_url(ep_tmdb.get("still_path"), "w300") if ep_tmdb else None,
        air_date=ep_tmdb.get("air_date") if ep_tmdb else None,
        runtime=ep_tmdb.get("runtime")   if ep_tmdb else None,
        duration=file_meta["duration"],
        resolution=file_meta["resolution"],
        video_codec=file_meta["video_codec"],
        audio_codec=file_meta["audio_codec"],
        file_size=file_meta["file_size"],
    )
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return ep


async def scan_movie(file_path: str, db: Session, executor: ThreadPoolExecutor = None) -> Movie | None:
    """Scan a single movie file and upsert in DB."""
    existing = db.query(Movie).filter(Movie.file_path == file_path).first()
    if existing:
        existing.last_scanned = datetime.utcnow()
        db.commit()
        return existing

    file_meta = await extract_file_metadata_async(file_path, executor) if executor else extract_file_metadata(file_path)
    parsed = parse_filename(file_path)
    title = parsed["title"] or Path(file_path).stem
    year  = parsed["year"]

    tmdb_data = await matcher.search_movie(title, year)

    movie = Movie(
        file_path=file_path,
        title=tmdb_data.get("title", title) if tmdb_data else title,
        year=year or (int(tmdb_data["release_date"][:4]) if tmdb_data and tmdb_data.get("release_date") else None),
        tmdb_id=tmdb_data.get("id")       if tmdb_data else None,
        imdb_id=tmdb_data.get("imdb_id")  if tmdb_data else None,
        overview=tmdb_data.get("overview") if tmdb_data else None,
        poster_path=matcher.poster_url(tmdb_data.get("poster_path"))      if tmdb_data else None,
        backdrop_path=matcher.backdrop_url(tmdb_data.get("backdrop_path")) if tmdb_data else None,
        rating=tmdb_data.get("vote_average") if tmdb_data else None,
        runtime=tmdb_data.get("runtime")     if tmdb_data else None,
        genres=", ".join(g["name"] for g in tmdb_data.get("genres", [])) if tmdb_data else None,
        tagline=tmdb_data.get("tagline")     if tmdb_data else None,
        duration=file_meta["duration"],
        resolution=file_meta["resolution"],
        video_codec=file_meta["video_codec"],
        audio_codec=file_meta["audio_codec"],
        file_size=file_meta["file_size"],
        last_scanned=datetime.utcnow(),
    )
    db.add(movie)
    db.commit()
    db.refresh(movie)
    return movie


async def scan_directory(path: str, media_type: str, db: Session, progress_callback=None) -> dict:
    """Scan an entire directory. Uses thread pool + caches for maximum speed."""
    global _show_cache, _season_cache

    # Reset caches at the start of each directory scan
    _show_cache   = {}
    _season_cache = {}

    files   = find_video_files(path)
    results = {"scanned": 0, "movies": 0, "episodes": 0, "errors": 0, "total": len(files)}

    # Thread pool for blocking mediainfo calls (4 workers)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for i, file_path in enumerate(files):
            if progress_callback:
                await progress_callback(i + 1, len(files), file_path)
            try:
                parsed = parse_filename(file_path)
                is_episode = (
                    media_type == "shows" or
                    (media_type == "auto" and (
                        parsed["season"]     is not None or
                        parsed["episode"]    is not None or
                        parsed["media_type"] == "episode"
                    ))
                )

                if is_episode and media_type != "movies":
                    await scan_episode(file_path, db, executor)
                    results["episodes"] += 1
                else:
                    await scan_movie(file_path, db, executor)
                    results["movies"] += 1
                results["scanned"] += 1

            except Exception as e:
                print(f"Error scanning {file_path}: {e}")
                results["errors"] += 1
                try:
                    db.rollback()
                    db.expire_all()
                except Exception:
                    pass

    return results
