from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from backend.config import DB_PATH

from sqlalchemy import event as sa_event

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

@sa_event.listens_for(engine, "connect")
def set_wal_mode(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA synchronous=NORMAL")
    dbapi_conn.execute("PRAGMA busy_timeout=10000")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class LibraryPath(Base):
    __tablename__ = "library_paths"
    id = Column(Integer, primary_key=True, index=True)
    path = Column(String, unique=True, nullable=False)
    media_type = Column(String, default="auto")  # auto, movies, shows
    created_at = Column(DateTime, default=datetime.utcnow)


class Movie(Base):
    __tablename__ = "movies"
    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    tmdb_id = Column(Integer, nullable=True)
    imdb_id = Column(String, nullable=True)
    overview = Column(Text, nullable=True)
    poster_path = Column(String, nullable=True)
    backdrop_path = Column(String, nullable=True)
    rating = Column(Float, nullable=True)
    runtime = Column(Integer, nullable=True)  # minutes
    genres = Column(String, nullable=True)  # comma-separated
    tagline = Column(String, nullable=True)
    # File metadata
    duration = Column(Float, nullable=True)  # seconds
    resolution = Column(String, nullable=True)
    video_codec = Column(String, nullable=True)
    audio_codec = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)  # bytes
    # State
    watched = Column(Boolean, default=False)
    watch_progress = Column(Float, default=0.0)  # seconds
    last_watched = Column(DateTime, nullable=True)
    date_added = Column(DateTime, default=datetime.utcnow)
    last_scanned = Column(DateTime, default=datetime.utcnow)


class TVShow(Base):
    __tablename__ = "tv_shows"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    tmdb_id = Column(Integer, nullable=True, unique=True)
    overview = Column(Text, nullable=True)
    poster_path = Column(String, nullable=True)
    backdrop_path = Column(String, nullable=True)
    rating = Column(Float, nullable=True)
    genres = Column(String, nullable=True)
    first_air_date = Column(String, nullable=True)
    status = Column(String, nullable=True)
    date_added = Column(DateTime, default=datetime.utcnow)
    episodes = relationship("Episode", back_populates="show", cascade="all, delete-orphan")


class Episode(Base):
    __tablename__ = "episodes"
    id = Column(Integer, primary_key=True, index=True)
    show_id = Column(Integer, ForeignKey("tv_shows.id"))
    show = relationship("TVShow", back_populates="episodes")
    file_path = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=True)
    season = Column(Integer, nullable=False)
    episode = Column(Integer, nullable=False)
    tmdb_id = Column(Integer, nullable=True)
    overview = Column(Text, nullable=True)
    still_path = Column(String, nullable=True)
    air_date = Column(String, nullable=True)
    runtime = Column(Integer, nullable=True)
    # File metadata
    duration = Column(Float, nullable=True)
    resolution = Column(String, nullable=True)
    video_codec = Column(String, nullable=True)
    audio_codec = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    # State
    watched = Column(Boolean, default=False)
    watch_progress = Column(Float, default=0.0)
    last_watched = Column(DateTime, nullable=True)
    date_added = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
