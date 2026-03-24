# 🎬 Sakere — Personal Media Center
---
A modern, self-hosted media center built with Python, FastAPI, and a sleek dark UI.

## Features
- 🎬 Movies & 📺 TV Shows library with full metadata from TMDB
- 🔍 Smart filename parsing via `guessit` (handles S01E01, year detection, etc.)
- 📊 Technical metadata extraction via `pymediainfo` (codec, resolution, duration)
- ▶ In-browser streaming with byte-range support
- 💾 SQLite database — single file, no server needed
- 🌐 Cross-platform (Linux, macOS, Windows)
- 🖌️ Custom-Theme Accents/Color Options.
- 📑 Subtitle Support: WebVTT extraction for text subs and live burn-in for image-based subs (PGS/VOBSUB)
- 🔊 Audio/Sub Track Selection: includes a track-picker UI during playback.
- ⏭️ Continue Watching & Up Next: The frontend has built-in progress tracking and an "Up Next" autoplay countdown for TV shows.
- Etc.

Feel free & please pitch in what you can to improve it! The more the merrier!
If you pitch in please add yourself to the Contributors.md!

As of today the issues section, also has some need-to-do's/wants.

You can get a slight feel at [Display-site](https://sakere-mc.web.app/) which looks like a blank library. 
Example in display-site is v1.9, Now on v2.2.

Default Theme (Demo Screenshots from v1.9)
---
<details>
  <summary>Click to see the screenshots!</summary>

![Image](./screenshots/1.png)
![Image](./screenshots/2.png)
![Image](./screenshots/3.png)
![Image](./screenshots/4.png)
![Image](./screenshots/5.png)
![Image](./screenshots/6.png)
![Image](./screenshots/7.png)
![Image](./screenshots/8.png)

</details>

## Quick Start

### 1. Install system dependency
```bash
# Linux (Ubuntu/Debian):
sudo apt install mediainfo ffmpeg

# Linux (Fedora/RHEL):
sudo dnf install mediainfo ffmpeg

# Linux (Arch):
sudo pacman -S mediainfo ffmpeg

# macOS (Homebrew):
brew install mediainfo ffmpeg

# Windows (Winget CLI):
winget install MediaArea.MediaInfo Gyan.FFmpeg

# Windows (Manual): 
# - MediaInfo: https://mediaarea.net/en/MediaInfo/Download
# - FFmpeg: https://ffmpeg.org/download.html#build-windows
```
> [!NOTE]
> Ensure **ffmpeg** and **mediainfo** are accessible in your system PATH. You can verify this by running `ffmpeg -version` in your terminal.

### 2. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 3. Run
```bash
python run.py
```

Then open http://localhost:7575 in your browser.

### 4. Configure
1. Go to **Settings** in the sidebar
2. Enter your TMDB API key (free at https://www.themoviedb.org/settings/api)
3. Add your library paths (e.g. `/home/user/Movies`)
4. Click **Scan Library**

## Supported Formats
.mkv, .mp4, .avi, .m4v, .mov, .wmv, .flv, .m2ts, .ts, .webm, .mpg, .mpeg

## Notes on Playback
- **MP4/WebM**: Play natively in the browser, no transcoding needed
- **MKV and others**: On-the-fly remuxing and transcoding for unsupported video (HEVC/H.265) and audio (DTS, AC-3) formats

## Project Structure
```
sakere/
├── backend/
│   ├── main.py      # FastAPI routes
│   ├── database.py  # SQLAlchemy models
│   ├── scanner.py   # File scanning + metadata
│   ├── matcher.py   # TMDB API integration
│   ├── stream.py    # Video streaming
│   └── config.py    # Configuration
├── frontend/
│   └── index.html   # Full SPA frontend
├── run.py           # Entry point
└── requirements.txt
```
