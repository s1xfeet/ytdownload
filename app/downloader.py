from __future__ import annotations

import os
from typing import Callable

import yt_dlp


def probe(url: str) -> dict:
    """Extract video metadata + format choices without downloading."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        # noplaylist=True usually picks the single video; if we still got a
        # playlist (rare), fall back to the first entry.
        entries = info.get("entries") or []
        if entries:
            info = entries[0]

    formats = info.get("formats") or []

    # Build a deduplicated list of merged-video options grouped by height.
    by_height: dict[int, dict] = {}
    for f in formats:
        if f.get("vcodec") in (None, "none"):
            continue
        h = f.get("height")
        if not h:
            continue
        existing = by_height.get(h)
        # Prefer mp4-compatible video codecs; pick highest tbr per height.
        score = (
            1 if (f.get("ext") == "mp4") else 0,
            f.get("tbr") or 0,
            f.get("filesize") or f.get("filesize_approx") or 0,
        )
        if existing is None or score > existing["_score"]:
            by_height[h] = {
                "_score": score,
                "height": h,
                "fps": f.get("fps"),
                "ext": f.get("ext"),
                "vcodec": f.get("vcodec"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
            }

    video_choices = []
    for h in sorted(by_height.keys(), reverse=True):
        e = by_height[h]
        spec = f"bv*[height={h}]+ba/b[height={h}]/b[height<={h}]"
        video_choices.append(
            {
                "kind": "video",
                "id": f"video-{h}",
                "label": f"{h}p" + (f"{int(e['fps'])}" if e.get("fps") and e["fps"] >= 50 else ""),
                "height": h,
                "fps": e.get("fps"),
                "ext": "mp4",
                "vcodec": e.get("vcodec"),
                "filesize": e.get("filesize"),
                "format_spec": spec,
            }
        )

    # Audio-only options.
    audio_choices = [
        {
            "kind": "audio",
            "id": "audio-mp3",
            "label": "Audio only (MP3 320kbps)",
            "ext": "mp3",
            "format_spec": "bestaudio/best",
            "audio_format": "mp3",
            "audio_quality": "320",
        },
        {
            "kind": "audio",
            "id": "audio-m4a",
            "label": "Audio only (M4A best)",
            "ext": "m4a",
            "format_spec": "bestaudio[ext=m4a]/bestaudio",
            "audio_format": None,
        },
    ]

    return {
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "formats": video_choices + audio_choices,
    }


def download(
    url: str,
    choice: dict,
    out_dir: str,
    progress_cb: Callable[[dict], None],
) -> dict:
    """Download a single video using the given format choice. Blocks."""
    os.makedirs(out_dir, exist_ok=True)

    def hook(d: dict) -> None:
        try:
            progress_cb(
                {
                    "status": d.get("status"),
                    "downloaded_bytes": d.get("downloaded_bytes"),
                    "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate"),
                    "speed": d.get("speed"),
                    "eta": d.get("eta"),
                    "filename": d.get("filename"),
                    "fragment_index": d.get("fragment_index"),
                    "fragment_count": d.get("fragment_count"),
                }
            )
        except Exception:
            pass

    opts: dict = {
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "format": choice["format_spec"],
        "progress_hooks": [hook],
        "merge_output_format": "mp4" if choice.get("kind") == "video" else None,
        "restrictfilenames": False,
        "windowsfilenames": False,
    }

    if choice.get("kind") == "audio" and choice.get("audio_format"):
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": choice["audio_format"],
                "preferredquality": choice.get("audio_quality") or "192",
            }
        ]

    # Strip None values that yt-dlp may not like.
    opts = {k: v for k, v in opts.items() if v is not None}

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("_type") == "playlist":
            entries = info.get("entries") or []
            info = entries[0] if entries else info
        # Resolve the actual final filename (after postprocessing).
        try:
            final_path = ydl.prepare_filename(info)
        except Exception:
            final_path = None

    if choice.get("kind") == "audio" and choice.get("audio_format") and final_path:
        # FFmpegExtractAudio rewrites extension.
        base, _ = os.path.splitext(final_path)
        final_path = f"{base}.{choice['audio_format']}"

    return {
        "title": info.get("title"),
        "filepath": final_path,
    }
