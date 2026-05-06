import os
import re
import subprocess
import shutil
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, Response

# Containers browsers can play natively
BROWSER_NATIVE_EXTS = {".mp4", ".webm", ".m4v"}

BROWSER_BAD_AUDIO = {"AC-3", "E-AC-3", "DTS", "TrueHD", "DTS-HD", "MLP FBA", "FLAC", "PCM"}

# Subtitle codecs that are image-based (must be burned in — can't be WebVTT)
IMAGE_SUB_CODECS = {"PGS", "HDMV_PGS_SUBTITLE", "S_HDMV/PGS", "VOBSUB", "DVD_SUBTITLE",
                    "S_VOBSUB", "BMP", "DVDSUB"}


def needs_remux(file_path: str, video_codec: str = None, audio_codec: str = None) -> bool:
    ext = Path(file_path).suffix.lower()
    if ext not in BROWSER_NATIVE_EXTS:
        return True
    if audio_codec:
        for bad in BROWSER_BAD_AUDIO:
            if bad.lower() in audio_codec.lower():
                return True
    if video_codec and "HEVC" in video_codec.upper():
        return True
    return False


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def get_content_type(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return {
        ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo", ".mov": "video/quicktime", ".m4v": "video/mp4",
        ".m2ts": "video/mp2t", ".ts": "video/mp2t", ".wmv": "video/x-ms-wmv",
    }.get(ext, "video/mp4")


def get_media_tracks(file_path: str) -> dict:
    """
    Parse all tracks in a media file using pymediainfo.
    Returns { video: [...], audio: [...], subtitles: [...] }
    Each entry has: idx (stream index within type), codec, language, title, is_image (subs only).
    """
    try:
        from pymediainfo import MediaInfo
        info = MediaInfo.parse(file_path)
    except Exception:
        return {"video": [], "audio": [], "subtitles": []}

    video, audio, subtitles = [], [], []
    vid_i = aud_i = sub_i = 0

    for track in info.tracks:
        t = track.track_type
        lang  = (track.language or "").upper() or "UND"
        title = track.title or ""

        if t == "Video":
            codec = track.format or "?"
            res   = ""
            if track.height:
                h = int(track.height)
                res = "4K" if h >= 2160 else "1080p" if h >= 1080 else "720p" if h >= 720 else f"{h}p"
            label = title or f"Video {vid_i+1} — {codec} {res}".strip()
            video.append({"idx": vid_i, "codec": codec, "resolution": res,
                          "language": lang, "title": label})
            vid_i += 1

        elif t == "Audio":
            codec    = track.format or "?"
            channels = track.channel_s or 2
            ch_label = {1:"Mono", 2:"Stereo", 6:"5.1", 8:"7.1"}.get(int(channels), f"{channels}ch")
            label    = title or f"{lang} — {codec} {ch_label}"
            audio.append({"idx": aud_i, "codec": codec, "channels": int(channels),
                          "language": lang, "title": label})
            aud_i += 1

        elif t == "Text":
            codec     = track.format or track.codec_id or "?"
            is_image  = any(ic in codec.upper() for ic in IMAGE_SUB_CODECS)
            label     = title or f"{lang} — {codec}"
            subtitles.append({"idx": sub_i, "codec": codec, "language": lang,
                               "title": label, "is_image": is_image})
            sub_i += 1

    return {"video": video, "audio": audio, "subtitles": subtitles}


async def stream_subtitles(file_path: str, sub_idx: int) -> StreamingResponse:
    """Extract one subtitle stream as WebVTT and stream it. Text subs only."""
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    if not ffmpeg_available():
        raise HTTPException(status_code=500, detail="FFmpeg not installed")

    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-i", file_path,
        "-map", f"0:s:{sub_idx}",
        "-f", "webvtt",
        "pipe:1",
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def generate():
        try:
            while True:
                chunk = process.stdout.read(524288)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.stdout.close()
                process.wait(timeout=5)
            except Exception:
                process.kill()

    return StreamingResponse(generate(), media_type="text/vtt",
                             headers={"Content-Type": "text/vtt; charset=utf-8",
                                      "Access-Control-Allow-Origin": "*"})


async def stream_video(file_path: str, range_header: str = None) -> Response:
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(file_path)
    content_type = get_content_type(file_path)

    if range_header:
        match = re.search(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            chunk_size = end - start + 1

            def iterfile(start, end):
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        data = f.read(min(1024 * 1024, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return StreamingResponse(iterfile(start, end), status_code=206,
                                     media_type=content_type,
                                     headers={"Content-Range": f"bytes {start}-{end}/{file_size}",
                                              "Accept-Ranges": "bytes",
                                              "Content-Length": str(chunk_size),
                                              "Content-Type": content_type})

    def stream_all():
        with open(file_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(stream_all(), media_type=content_type,
                             headers={"Accept-Ranges": "bytes",
                                      "Content-Length": str(file_size)})


async def stream_remux(file_path: str, seek: float = 0,
                       audio_idx: int = 0, video_idx: int = 0,
                       sub_idx: int = -1, sub_is_image: bool = False,
                       force_transcode: bool = False,
                       range_header: str = None,
                       video_preset: str = "fast",
                       video_crf: int = 22,
                       audio_bitrate: str = "192k") -> StreamingResponse:
    """
    Remux to fragmented MP4 via FFmpeg.

    Windows Edge compatibility:
    - Edge sends Range requests before playing any <video>. We respond 206
      so Edge doesn't reject the stream. We can't byte-seek a live pipe so
      we serve from the FFmpeg -ss offset regardless of the byte range.
    - HEVC (H.265) inside fragmented MP4 is hit-or-miss on Windows even with
      the HEVC codec pack. We auto-detect HEVC and always transcode to H.264.
    - force_transcode=True is the last-resort retry path when the browser
      detects the stream ended/stalled with no playback.
    """
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    if not ffmpeg_available():
        raise HTTPException(status_code=500, detail="FFmpeg not installed")

    # Clamp / validate caller-supplied values
    VALID_PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"}
    if video_preset not in VALID_PRESETS:
        video_preset = "fast"
    video_crf = max(0, min(51, int(video_crf)))
    crf_str   = str(video_crf)

    # Auto-detect HEVC — must transcode to H.264 for broad browser compat
    is_hevc = False
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", f"v:{video_idx}",
             "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", file_path],
            capture_output=True, text=True, timeout=5
        )
        is_hevc = "hevc" in probe.stdout.lower() or "h265" in probe.stdout.lower()
    except Exception:
        pass

    burn_subs    = sub_idx >= 0 and sub_is_image
    need_encode  = is_hevc or burn_subs or force_transcode

    cmd = ["ffmpeg", "-loglevel", "error"]
    if seek and seek > 0:
        cmd += ["-ss", str(seek)]
    cmd += ["-i", file_path]

    if burn_subs:
        cmd += ["-filter_complex", f"[0:v:{video_idx}][0:s:{sub_idx}]overlay[v]",
                "-map", "[v]", "-map", f"0:a:{audio_idx}?"]
        cmd += ["-c:v", "libx264", "-preset", video_preset, "-crf", crf_str]
    elif sub_idx >= 0 and not sub_is_image:
        cmd += ["-map", f"0:v:{video_idx}", "-map", f"0:a:{audio_idx}?"]
        safe = file_path.replace("\\", "/").replace("'", r"\'").replace(":", r"\:")
        cmd += ["-c:v", "libx264", "-preset", video_preset, "-crf", crf_str,
                "-vf", f"subtitles='{safe}':si={sub_idx}"]
    else:
        cmd += ["-map", f"0:v:{video_idx}", "-map", f"0:a:{audio_idx}?"]
        if need_encode:
            cmd += ["-c:v", "libx264", "-preset", video_preset, "-crf", crf_str]
        else:
            cmd += ["-c:v", "copy"]

    cmd += [
        "-c:a", "aac", "-b:a", audio_bitrate, "-ac", "2",
        "-af", "aresample=async=1:min_hard_comp=0.1",   # Fix 2: audio/video drift correction
        "-f", "mp4",
        "-movflags", "empty_moov+faststart+default_base_moof",
        "-frag_duration", "500000",   # Fix 1: flush every 500ms regardless of keyframes
        "-flush_packets", "1",        # Fix 3: don't let OS buffer hold chunks back
        "pipe:1",
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def generate():
        try:
            while True:
                chunk = process.stdout.read(524288)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                process.stdout.close()
                process.wait(timeout=2)
            except Exception:
                process.kill()

    # Windows Edge requires a Range response (206) before it will buffer a
    # <video> stream. We can't byte-seek a live FFmpeg pipe, so we always
    # deliver from the -ss offset, but we satisfy Edge's handshake by
    # returning 206 + a Content-Range header when a Range header is present.
    status  = 206 if range_header else 200
    headers = {
        "Content-Type":           "video/mp4",
        "Accept-Ranges":          "bytes",
        "Cache-Control":          "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if range_header:
        headers["Content-Range"] = "bytes 0-1/*"

    return StreamingResponse(generate(), status_code=status,
                             media_type="video/mp4", headers=headers)