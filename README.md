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
# linux/deb: sudo apt install mediainfo
# macOS: brew install mediainfo
# Windows: https://mediaarea.net/en/MediaInfo/Download
```

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
- **MKV and others**: Your browser may not support all formats. For full compatibility, consider:
  - Remuxing to MP4 with FFmpeg: `ffmpeg -i input.mkv -c copy output.mp4`
  - Using a browser extension that supports MKV (e.g. VLC browser plugin)

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
