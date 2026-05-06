import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import init_db, get_db, Movie, TVShow, Episode, LibraryPath
from backend.config import SETTINGS_PATH, FRONTEND_DIR, IMAGES_DIR
from backend.stream import stream_video, stream_remux, stream_subtitles, get_media_tracks, needs_remux, ffmpeg_available
from backend import scanner
from backend import matcher


# ─── Startup ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Sakere", version="1.0.0", lifespan=lifespan)


# Serve everything in /frontend (CSS, JS, fonts, images, etc.)
# This MUST be mounted before your other routes
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/api/images/{filename}")
async def serve_image(filename: str):
    image_path = IMAGES_DIR / filename
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Settings helpers ────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {"tmdb_api_key": "", "library_paths": []}


def save_settings(data: dict):
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))


# ─── Pydantic schemas ────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    tmdb_api_key: Optional[str] = None
    theme: Optional[dict] = None
    transcode_preset: Optional[str] = None    # ultrafast → slow
    transcode_crf: Optional[int] = None       # 0–51, lower = better quality
    transcode_audio_bitrate: Optional[str] = None  # e.g. "128k", "192k", "256k", "320k"


class LibraryPathCreate(BaseModel):
    path: str
    media_type: str = "auto"  # auto, movies, shows


class ProgressUpdate(BaseModel):
    current: int = 0
    total: int = 0
    current_file: str = ""
    running: bool = False


class WatchProgressUpdate(BaseModel):
    progress: float
    watched: Optional[bool] = None


class MovieEditBody(BaseModel):
    title: Optional[str] = None
    year: Optional[int] = None
    overview: Optional[str] = None
    tagline: Optional[str] = None
    rating: Optional[float] = None
    genres: Optional[str] = None
    runtime: Optional[int] = None


class ShowEditBody(BaseModel):
    title: Optional[str] = None
    overview: Optional[str] = None
    rating: Optional[float] = None
    genres: Optional[str] = None
    status: Optional[str] = None
    first_air_date: Optional[str] = None


class EpisodeEditBody(BaseModel):
    title: Optional[str] = None
    overview: Optional[str] = None


class ArtworkBody(BaseModel):
    image_type: str   # "poster" or "backdrop"
    tmdb_path: str    # TMDB path e.g. "/abc123.jpg"


# Global scan progress
scan_progress = ProgressUpdate()


# ─── Utility ─────────────────────────────────────────────────────────────────

def movie_to_dict(m: Movie) -> dict:
    return {
        "id": m.id, "type": "movie",
        "title": m.title, "year": m.year,
        "overview": m.overview, "tagline": m.tagline,
        "poster_path": m.poster_path, "backdrop_path": m.backdrop_path,
        "rating": m.rating, "runtime": m.runtime,
        "genres": m.genres, "resolution": m.resolution,
        "video_codec": m.video_codec, "audio_codec": m.audio_codec,
        "file_size": m.file_size, "duration": m.duration,
        "watched": m.watched, "watch_progress": m.watch_progress,
        "last_watched": m.last_watched.isoformat() if m.last_watched else None,
        "date_added": m.date_added.isoformat() if m.date_added else None,
        "file_path": m.file_path,
        "needs_remux": needs_remux(m.file_path, m.video_codec, m.audio_codec),
    }


def show_to_dict(s: TVShow, db: Session) -> dict:
    episodes = db.query(Episode).filter(Episode.show_id == s.id).order_by(
        Episode.season, Episode.episode
    ).all()
    seasons: dict = {}
    for ep in episodes:
        sk = str(ep.season)
        if sk not in seasons:
            seasons[sk] = []
        seasons[sk].append({
            "id": ep.id, "title": ep.title,
            "season": ep.season, "episode": ep.episode,
            "overview": ep.overview, "still_path": ep.still_path,
            "air_date": ep.air_date, "duration": ep.duration,
            "resolution": ep.resolution, "watched": ep.watched,
            "watch_progress": ep.watch_progress,
            "runtime": ep.runtime,
            "needs_remux": needs_remux(ep.file_path, ep.video_codec, ep.audio_codec),
        })
    episode_count = len(episodes)
    watched_count = sum(1 for ep in episodes if ep.watched)
    return {
        "id": s.id, "type": "show",
        "title": s.title, "overview": s.overview,
        "poster_path": s.poster_path, "backdrop_path": s.backdrop_path,
        "rating": s.rating, "genres": s.genres,
        "first_air_date": s.first_air_date, "status": s.status,
        "episode_count": episode_count, "watched_count": watched_count,
        "seasons": seasons,
        "date_added": s.date_added.isoformat() if s.date_added else None,
    }


def transcode_settings() -> dict:
    """Return current transcoding settings with safe defaults."""
    s = load_settings()
    return {
        "video_preset":   s.get("transcode_preset", "fast"),
        "video_crf":      int(s.get("transcode_crf", 22)),
        "audio_bitrate":  s.get("transcode_audio_bitrate", "192k"),
    }


# ─── Routes ──────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root():
    index = FRONTEND_DIR / "index.html"
    return HTMLResponse(content=index.read_text(), status_code=200)


@app.get("/demo", response_class=HTMLResponse)
async def demo():
    index = FRONTEND_DIR / "index.html"
    html = index.read_text()
    # Inject demo flag before any scripts run
    html = html.replace("<script>", "<script>\nwindow.DEMO_MODE = true;\n", 1)
    return HTMLResponse(content=html, status_code=200)


@app.get("/api/settings")
async def get_settings():
    return load_settings()


@app.post("/api/settings")
async def update_settings(body: SettingsUpdate):
    settings = load_settings()
    if body.tmdb_api_key is not None:
        settings["tmdb_api_key"] = body.tmdb_api_key
    if body.theme is not None:
        settings["theme"] = body.theme
    if body.transcode_preset is not None:
        settings["transcode_preset"] = body.transcode_preset
    if body.transcode_crf is not None:
        settings["transcode_crf"] = int(body.transcode_crf)
    if body.transcode_audio_bitrate is not None:
        settings["transcode_audio_bitrate"] = body.transcode_audio_bitrate
    save_settings(settings)
    return {"ok": True}


@app.get("/api/system")
async def system_info():
    """Return system capabilities so the frontend can show relevant options."""
    return {"ffmpeg": ffmpeg_available()}


@app.get("/api/library/paths")
async def get_library_paths(db: Session = Depends(get_db)):
    paths = db.query(LibraryPath).all()
    return [{"id": p.id, "path": p.path, "media_type": p.media_type} for p in paths]


@app.post("/api/library/paths")
async def add_library_path(body: LibraryPathCreate, db: Session = Depends(get_db)):
    if not Path(body.path).exists():
        raise HTTPException(status_code=400, detail="Directory does not exist")
    existing = db.query(LibraryPath).filter(LibraryPath.path == body.path).first()
    if existing:
        raise HTTPException(status_code=400, detail="Path already added")
    lp = LibraryPath(path=body.path, media_type=body.media_type)
    db.add(lp)
    db.commit()
    return {"ok": True, "id": lp.id}


@app.delete("/api/library/paths/{path_id}")
async def remove_library_path(path_id: int, db: Session = Depends(get_db)):
    lp = db.query(LibraryPath).filter(LibraryPath.id == path_id).first()
    if not lp:
        raise HTTPException(status_code=404)
    db.delete(lp)
    db.commit()
    return {"ok": True}


@app.post("/api/library/scan")
async def trigger_scan(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    global scan_progress
    if scan_progress.running:
        return {"ok": False, "message": "Scan already running"}

    paths = db.query(LibraryPath).all()
    if not paths:
        return {"ok": False, "message": "No library paths configured"}

    async def do_scan():
        global scan_progress
        scan_progress.running = True
        from backend.database import SessionLocal
        scan_db = SessionLocal()
        try:
            all_paths = scan_db.query(LibraryPath).all()
            for lp in all_paths:
                async def progress(i, total, file):
                    global scan_progress
                    scan_progress.current = i
                    scan_progress.total = total
                    scan_progress.current_file = file
                await scanner.scan_directory(lp.path, lp.media_type, scan_db, progress)
        finally:
            scan_db.close()
            scan_progress.running = False
            scan_progress.current = 0
            scan_progress.total = 0
            scan_progress.current_file = ""

    background_tasks.add_task(do_scan)
    return {"ok": True, "message": "Scan started"}


@app.get("/api/library/scan/progress")
async def scan_status():
    return {
        "running": scan_progress.running,
        "current": scan_progress.current,
        "total": scan_progress.total,
        "current_file": scan_progress.current_file,
        "percent": round((scan_progress.current / scan_progress.total * 100) if scan_progress.total > 0 else 0, 1)
    }


@app.get("/api/movies")
async def list_movies(
    search: Optional[str] = None,
    genre: Optional[str] = None,
    sort: Optional[str] = "date_added",
    db: Session = Depends(get_db)
):
    q = db.query(Movie)
    if search:
        q = q.filter(Movie.title.ilike(f"%{search}%"))
    if genre:
        q = q.filter(Movie.genres.ilike(f"%{genre}%"))
    if sort == "title":
        q = q.order_by(Movie.title)
    elif sort == "year":
        q = q.order_by(Movie.year.desc())
    elif sort == "rating":
        q = q.order_by(Movie.rating.desc())
    else:
        q = q.order_by(Movie.date_added.desc())
    return [movie_to_dict(m) for m in q.all()]


@app.get("/api/movies/{movie_id}")
async def get_movie(movie_id: int, db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    return movie_to_dict(m)


@app.put("/api/movies/{movie_id}/progress")
async def update_movie_progress(movie_id: int, body: WatchProgressUpdate, db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    m.watch_progress = body.progress
    m.last_watched = datetime.utcnow()
    if body.watched is not None:
        m.watched = body.watched
    elif m.duration and body.progress > m.duration * 0.9:
        m.watched = True
    db.commit()
    return {"ok": True}


@app.get("/api/shows")
async def list_shows(
    search: Optional[str] = None,
    sort: Optional[str] = "date_added",
    db: Session = Depends(get_db)
):
    q = db.query(TVShow)
    if search:
        q = q.filter(TVShow.title.ilike(f"%{search}%"))
    if sort == "title":
        q = q.order_by(TVShow.title)
    elif sort == "rating":
        q = q.order_by(TVShow.rating.desc())
    else:
        q = q.order_by(TVShow.date_added.desc())
    return [show_to_dict(s, db) for s in q.all()]


@app.get("/api/shows/{show_id}")
async def get_show(show_id: int, db: Session = Depends(get_db)):
    s = db.query(TVShow).filter(TVShow.id == show_id).first()
    if not s:
        raise HTTPException(status_code=404)
    return show_to_dict(s, db)


@app.delete("/api/movies/{movie_id}")
async def delete_movie(movie_id: int, db: Session = Depends(get_db)):
    """Delete a movie from the database."""
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Movie not found")
    
    db.delete(m)
    db.commit()
    return {"ok": True, "message": "Movie deleted"}


@app.delete("/api/shows/{show_id}")
async def delete_show(show_id: int, db: Session = Depends(get_db)):
    """Delete a TV show and all its episodes from the database."""
    s = db.query(TVShow).filter(TVShow.id == show_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Show not found")
    
    # The database model automatically cascades and deletes the associated episodes
    db.delete(s)
    db.commit()
    return {"ok": True, "message": "Show and episodes deleted"}


@app.get("/api/episodes/{ep_id}")
async def get_episode(ep_id: int, db: Session = Depends(get_db)):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(status_code=404)
    return {
        "id": ep.id, "show_id": ep.show_id,
        "season": ep.season, "episode": ep.episode,
        "title": ep.title, "duration": ep.duration,
        "watch_progress": ep.watch_progress, "watched": ep.watched,
        "needs_remux": needs_remux(ep.file_path, ep.video_codec, ep.audio_codec),
    }


@app.put("/api/episodes/{ep_id}/progress")
async def update_episode_progress(ep_id: int, body: WatchProgressUpdate, db: Session = Depends(get_db)):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(status_code=404)
    ep.watch_progress = body.progress
    ep.last_watched = datetime.utcnow()
    if body.watched is not None:
        ep.watched = body.watched
    elif ep.duration and body.progress > ep.duration * 0.9:
        ep.watched = True
    db.commit()
    return {"ok": True}


@app.get("/api/stream/movie/{movie_id}")
async def stream_movie(movie_id: int, request: Request, db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    range_header = request.headers.get("range")
    return await stream_video(m.file_path, range_header)


@app.get("/api/stream/episode/{ep_id}")
async def stream_episode(ep_id: int, request: Request, db: Session = Depends(get_db)):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(status_code=404)
    range_header = request.headers.get("range")
    return await stream_video(ep.file_path, range_header)


@app.get("/api/stream/movie/{movie_id}/remux")
async def remux_movie(movie_id: int, request: Request, seek: float = 0,
                      audio_idx: int = 0, video_idx: int = 0,
                      sub_idx: int = -1, sub_is_image: bool = False,
                      transcode: int = 0,
                      db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    ts = transcode_settings()
    return await stream_remux(m.file_path, seek=seek, audio_idx=audio_idx,
                              video_idx=video_idx, sub_idx=sub_idx,
                              sub_is_image=sub_is_image, force_transcode=bool(transcode),
                              range_header=request.headers.get("range"),
                              **ts)


@app.get("/api/stream/episode/{ep_id}/remux")
async def remux_episode(ep_id: int, request: Request, seek: float = 0,
                        audio_idx: int = 0, video_idx: int = 0,
                        sub_idx: int = -1, sub_is_image: bool = False,
                        transcode: int = 0,
                        db: Session = Depends(get_db)):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(status_code=404)
    ts = transcode_settings()
    return await stream_remux(ep.file_path, seek=seek, audio_idx=audio_idx,
                              video_idx=video_idx, sub_idx=sub_idx,
                              sub_is_image=sub_is_image, force_transcode=bool(transcode),
                              range_header=request.headers.get("range"),
                              **ts)


@app.get("/api/tracks/movie/{movie_id}")
async def tracks_movie(movie_id: int, db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    return get_media_tracks(m.file_path)


@app.get("/api/tracks/episode/{ep_id}")
async def tracks_episode(ep_id: int, db: Session = Depends(get_db)):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(status_code=404)
    return get_media_tracks(ep.file_path)


@app.get("/api/subtitles/movie/{movie_id}")
async def subtitles_movie(movie_id: int, track: int = 0, db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    return await stream_subtitles(m.file_path, track)


@app.get("/api/subtitles/episode/{ep_id}")
async def subtitles_episode(ep_id: int, track: int = 0, db: Session = Depends(get_db)):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(status_code=404)
    return await stream_subtitles(ep.file_path, track)


@app.get("/api/tmdb/search")
async def tmdb_search(q: str, type: str = "movie"):
    """Search TMDB for a title. type = 'movie' or 'tv'"""
    if type == "tv":
        results = await matcher.search_tv_multi(q)
    else:
        results = await matcher.search_movie_multi(q)
    return results or []


@app.put("/api/movies/{movie_id}/match/{tmdb_id}")
async def match_movie(movie_id: int, tmdb_id: int, db: Session = Depends(get_db)):
    """Re-match a movie to a specific TMDB entry."""
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)

    import httpx
    api_key = load_settings().get("tmdb_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="No TMDB API key configured")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{matcher.TMDB_BASE_URL}/movie/{tmdb_id}",
            params={"api_key": api_key, "language": "en-US"},
            timeout=10
        )
        if r.status_code != 200:
            raise HTTPException(status_code=404, detail="TMDB ID not found")
        data = r.json()

    m.tmdb_id = data.get("id")
    m.title = data.get("title", m.title)
    m.year = int(data["release_date"][:4]) if data.get("release_date") else m.year
    m.overview = data.get("overview")
    m.poster_path = await matcher.poster_url_cached(data.get("poster_path"))
    m.backdrop_path = await matcher.backdrop_url_cached(data.get("backdrop_path"))
    m.rating = data.get("vote_average")
    m.runtime = data.get("runtime")
    m.genres = ", ".join(g["name"] for g in data.get("genres", []))
    m.tagline = data.get("tagline")
    m.imdb_id = data.get("imdb_id")
    db.commit()
    return movie_to_dict(m)


@app.put("/api/shows/{show_id}/match/{tmdb_id}")
async def match_show(show_id: int, tmdb_id: int, db: Session = Depends(get_db)):
    """Re-match a TV show to a specific TMDB entry and refresh all episode metadata.
    If another show already owns this tmdb_id (duplicate scan artifact), merge
    all episodes from this show into that one and delete this record."""
    s = db.query(TVShow).filter(TVShow.id == show_id).first()
    if not s:
        raise HTTPException(status_code=404)

    import httpx
    api_key = load_settings().get("tmdb_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="No TMDB API key configured")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{matcher.TMDB_BASE_URL}/tv/{tmdb_id}",
            params={"api_key": api_key, "language": "en-US"},
            timeout=10
        )
        if r.status_code != 200:
            raise HTTPException(status_code=404, detail="TMDB ID not found")
        data = r.json()

    # Check if a DIFFERENT show record already has this tmdb_id
    existing_owner = db.query(TVShow).filter(
        TVShow.tmdb_id == tmdb_id,
        TVShow.id != show_id
    ).first()

    if existing_owner:
        # Merge: move all episodes from this show into the existing owner
        db.query(Episode).filter(Episode.show_id == show_id).update(
            {"show_id": existing_owner.id}, synchronize_session=False
        )
        db.delete(s)
        db.commit()
        # Refresh episode metadata on the merged show
        episodes = db.query(Episode).filter(Episode.show_id == existing_owner.id).all()
        for ep in episodes:
            try:
                ep_data = await matcher.get_episode_details(tmdb_id, ep.season, ep.episode)
                if ep_data:
                    ep.title    = ep_data.get("name", ep.title)
                    ep.overview = ep_data.get("overview", ep.overview)
                    ep.still_path = await matcher.poster_url_cached(ep_data.get("still_path"), "w300")
                    ep.air_date = ep_data.get("air_date", ep.air_date)
                    ep.runtime  = ep_data.get("runtime", ep.runtime)
            except Exception:
                pass
        db.commit()
        return show_to_dict(existing_owner, db)

    # No conflict — update this show in place
    s.tmdb_id       = data.get("id")
    s.title         = data.get("name", s.title)
    s.overview      = data.get("overview")
    s.poster_path   = await matcher.poster_url_cached(data.get("poster_path"))
    s.backdrop_path = await matcher.backdrop_url_cached(data.get("backdrop_path"))
    s.rating        = data.get("vote_average")
    s.genres        = ", ".join(g["name"] for g in data.get("genres", []))
    s.first_air_date = data.get("first_air_date")
    s.status        = data.get("status")
    db.commit()

    episodes = db.query(Episode).filter(Episode.show_id == show_id).all()
    for ep in episodes:
        try:
            ep_data = await matcher.get_episode_details(tmdb_id, ep.season, ep.episode)
            if ep_data:
                ep.title    = ep_data.get("name", ep.title)
                ep.overview = ep_data.get("overview", ep.overview)
                ep.still_path = await matcher.poster_url_cached(ep_data.get("still_path"), "w300")
                ep.air_date = ep_data.get("air_date", ep.air_date)
                ep.runtime  = ep_data.get("runtime", ep.runtime)
        except Exception:
            pass
    db.commit()
    return show_to_dict(s, db)


# ─── Image cache helper ──────────────────────────────────────────────────────

def delete_cached_image(img_path: str):
    """Delete a locally-cached image given its /api/images/<filename> path."""
    if not img_path or not img_path.startswith("/api/images/"):
        return
    filename = img_path.removeprefix("/api/images/")
    local = IMAGES_DIR / filename
    try:
        if local.exists():
            local.unlink()
    except Exception as e:
        print(f"Image delete error: {e}")


# ─── Edit metadata routes ────────────────────────────────────────────────────

@app.put("/api/movies/{movie_id}/edit")
async def edit_movie(movie_id: int, body: MovieEditBody, db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    if body.title    is not None: m.title    = body.title
    if body.year     is not None: m.year     = body.year
    if body.overview is not None: m.overview = body.overview
    if body.tagline  is not None: m.tagline  = body.tagline
    if body.rating   is not None: m.rating   = body.rating
    if body.genres   is not None: m.genres   = body.genres
    if body.runtime  is not None: m.runtime  = body.runtime
    db.commit()
    return movie_to_dict(m)


@app.put("/api/shows/{show_id}/edit")
async def edit_show(show_id: int, body: ShowEditBody, db: Session = Depends(get_db)):
    s = db.query(TVShow).filter(TVShow.id == show_id).first()
    if not s:
        raise HTTPException(status_code=404)
    if body.title          is not None: s.title          = body.title
    if body.overview       is not None: s.overview       = body.overview
    if body.rating         is not None: s.rating         = body.rating
    if body.genres         is not None: s.genres         = body.genres
    if body.status         is not None: s.status         = body.status
    if body.first_air_date is not None: s.first_air_date = body.first_air_date
    db.commit()
    return show_to_dict(s, db)


@app.put("/api/episodes/{ep_id}/edit")
async def edit_episode(ep_id: int, body: EpisodeEditBody, db: Session = Depends(get_db)):
    ep = db.query(Episode).filter(Episode.id == ep_id).first()
    if not ep:
        raise HTTPException(status_code=404)
    if body.title    is not None: ep.title    = body.title
    if body.overview is not None: ep.overview = body.overview
    db.commit()
    return {"ok": True, "id": ep.id, "title": ep.title, "overview": ep.overview}


@app.get("/api/tmdb/images")
async def get_tmdb_images(type: str, tmdb_id: int):
    """Return available poster and backdrop images for a movie/show from TMDB."""
    api_key = load_settings().get("tmdb_api_key", "")
    if not api_key or not tmdb_id:
        return {"posters": [], "backdrops": []}
    import httpx
    segment = f"movie/{tmdb_id}/images" if type == "movie" else f"tv/{tmdb_id}/images"
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{matcher.TMDB_BASE_URL}/{segment}",
                params={"api_key": api_key},
                timeout=10,
            )
            if r.status_code != 200:
                return {"posters": [], "backdrops": []}
            data = r.json()
        except Exception as e:
            print(f"TMDB images error: {e}")
            return {"posters": [], "backdrops": []}

    def img_info(img, is_backdrop=False):
        path = img.get("file_path", "")
        size = "w300" if is_backdrop else "w185"
        return {"tmdb_path": path, "url": matcher.poster_url(path, size)}

    return {
        "posters":   [img_info(i)       for i in data.get("posters",   [])[:24]],
        "backdrops": [img_info(i, True) for i in data.get("backdrops", [])[:16]],
    }


@app.put("/api/movies/{movie_id}/artwork")
async def set_movie_artwork(movie_id: int, body: ArtworkBody, db: Session = Depends(get_db)):
    m = db.query(Movie).filter(Movie.id == movie_id).first()
    if not m:
        raise HTTPException(status_code=404)
    if body.image_type == "poster":
        delete_cached_image(m.poster_path)
        m.poster_path = await matcher.poster_url_cached(body.tmdb_path, "w500")
    elif body.image_type == "backdrop":
        delete_cached_image(m.backdrop_path)
        m.backdrop_path = await matcher.backdrop_url_cached(body.tmdb_path, "w1280")
    else:
        raise HTTPException(status_code=400, detail="image_type must be 'poster' or 'backdrop'")
    db.commit()
    return movie_to_dict(m)


@app.put("/api/shows/{show_id}/artwork")
async def set_show_artwork(show_id: int, body: ArtworkBody, db: Session = Depends(get_db)):
    s = db.query(TVShow).filter(TVShow.id == show_id).first()
    if not s:
        raise HTTPException(status_code=404)
    if body.image_type == "poster":
        delete_cached_image(s.poster_path)
        s.poster_path = await matcher.poster_url_cached(body.tmdb_path, "w500")
    elif body.image_type == "backdrop":
        delete_cached_image(s.backdrop_path)
        s.backdrop_path = await matcher.backdrop_url_cached(body.tmdb_path, "w1280")
    else:
        raise HTTPException(status_code=400, detail="image_type must be 'poster' or 'backdrop'")
    db.commit()
    return show_to_dict(s, db)


@app.get("/api/recently-added")
async def recently_added(db: Session = Depends(get_db)):
    movies = db.query(Movie).order_by(Movie.date_added.desc()).limit(10).all()
    shows = db.query(TVShow).order_by(TVShow.date_added.desc()).limit(10).all()
    result = [movie_to_dict(m) for m in movies]
    result += [show_to_dict(s, db) for s in shows]
    result.sort(key=lambda x: x.get("date_added") or "", reverse=True)
    return result[:12]


@app.get("/api/continue-watching")
async def continue_watching(db: Session = Depends(get_db)):
    movies = db.query(Movie).filter(
        Movie.watch_progress > 0, Movie.watched == False
    ).order_by(Movie.last_watched.desc()).limit(6).all()
    episodes = db.query(Episode).filter(
        Episode.watch_progress > 0, Episode.watched == False
    ).order_by(Episode.last_watched.desc()).limit(6).all()
    result = [movie_to_dict(m) for m in movies]
    for ep in episodes:
        show = db.query(TVShow).filter(TVShow.id == ep.show_id).first()
        result.append({
            "id": ep.id,
            "type": "episode",
            "title": f"{show.title if show else '?'} — S{ep.season:02d}E{ep.episode:02d}",
            "subtitle": ep.title,
            "poster_path": show.poster_path if show else None,
            "watch_progress": ep.watch_progress,
            "duration": ep.duration,
            "show_id": ep.show_id,
            "needs_remux": needs_remux(ep.file_path, ep.video_codec, ep.audio_codec),
        })
    result.sort(key=lambda x: x.get("last_watched") or "", reverse=True)
    return result


@app.delete("/api/continue-watching/{media_type}/{item_id}")
async def remove_from_continue_watching(media_type: str, item_id: int, db: Session = Depends(get_db)):
    """Reset watch progress to 0, removing the item from the Continue Watching row."""
    if media_type == "movie":
        m = db.query(Movie).filter(Movie.id == item_id).first()
        if not m:
            raise HTTPException(status_code=404)
        m.watch_progress = 0.0
        m.last_watched = None
    elif media_type == "episode":
        ep = db.query(Episode).filter(Episode.id == item_id).first()
        if not ep:
            raise HTTPException(status_code=404)
        ep.watch_progress = 0.0
        ep.last_watched = None
    else:
        raise HTTPException(status_code=400, detail="media_type must be 'movie' or 'episode'")
    db.commit()
    return {"ok": True}