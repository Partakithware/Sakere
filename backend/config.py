import os
from pathlib import Path

TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"

DB_PATH = Path("sakere.db")
SETTINGS_PATH = Path("settings.json")

VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv",
    ".flv", ".m2ts", ".ts", ".webm", ".mpg", ".mpeg"
}

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
