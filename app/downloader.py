from __future__ import annotations

import os
from typing import Callable, Optional

import yt_dlp

# Browsers yt-dlp can read cookies from (for logged-in sites like Instagram).
VALID_BROWSERS = {
    "safari",
    "chrome",
    "firefox",
    "edge",
    "brave",
    "chromium",
    "opera",
    "vivaldi",
}

# Map yt-dlp extractor keys to friendly platform names shown in the UI.
_PLATFORM_NAMES = {
    "youtube": "YouTube",
    "twitter": "X",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "reddit": "Reddit",
    "vimeo": "Vimeo",
    "twitch": "Twitch",
}


def _cookie_opts(cookies_browser: Optional[str]) -> dict:
    """yt-dlp opts to read cookies from a browser, if one is configured."""
    if cookies_browser:
        b = cookies_browser.strip().lower()
        if b in VALID_BROWSERS:
            return {"cookiesfrombrowser": (b,)}
    return {}


def _platform_name(info: dict) -> str:
    key = (info.get("extractor_key") or info.get("extractor") or "").lower()
    for prefix, name in _PLATFORM_NAMES.items():
        if key.startswith(prefix):
            return name
    return info.get("extractor_key") or "Web"


def probe(url: str, cookies_browser: Optional[str] = None) -> dict:
    """Extract video metadata + format choices without downloading.

    Works across YouTube, X/Twitter, TikTok, Instagram, and any other
    yt-dlp-supported site. Unlike YouTube, most social platforms serve a
    single muxed (video+audio) file and sometimes omit codec metadata, so
    formats are keyed by height rather than by a known video codec.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
    }
    opts.update(_cookie_opts(cookies_browser))

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info.get("_type") == "playlist":
        # noplaylist=True usually picks the single video; if we still got a
        # playlist (rare), fall back to the first entry.
        entries = info.get("entries") or []
        if entries:
            info = entries[0]

    formats = info.get("formats") or []

    # Group selectable video formats by height. A format counts as "video" if
    # it reports a height — this catches X's progressive http mp4s whose vcodec
    # yt-dlp leaves as None, which the old codec-based filter dropped.
    by_height: dict[int, dict] = {}
    for f in formats:
        h = f.get("height")
        if not h:
            continue
        score = (
            1 if (f.get("ext") == "mp4") else 0,
            f.get("tbr") or 0,
            f.get("filesize") or f.get("filesize_approx") or 0,
        )
        existing = by_height.get(h)
        if existing is None or score > existing["_score"]:
            by_height[h] = {
                "_score": score,
                "height": h,
                "width": f.get("width"),
                "fps": f.get("fps"),
                "ext": f.get("ext"),
                "vcodec": f.get("vcodec"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
            }

    # Always offer a universal "best" option first — the safest choice on any
    # platform, and the only video option when a site exposes no per-height
    # metadata at all.
    video_choices = [
        {
            "kind": "video",
            "id": "best",
            "label": "Best quality",
            "sub": "Highest resolution · MP4",
            "ext": "mp4",
            "format_spec": "bv*+ba/b",
        }
    ]

    for h in sorted(by_height.keys(), reverse=True):
        e = by_height[h]
        # Prefer a single progressive file at this height (fast, no merge),
        # then a merged video+audio pair, then the best format at or below it.
        spec = f"b[height={h}]/bv*[height={h}]+ba/b[height<={h}]"
        fps_tag = f"{int(e['fps'])}" if e.get("fps") and e["fps"] >= 50 else ""
        res = f"{e['width']}×{h}" if e.get("width") else f"{h}p"
        codec = (e.get("vcodec") or "").split(".")[0]
        sub_bits = [res]
        if codec and codec != "none":
            sub_bits.append(codec)
        if e.get("filesize"):
            sub_bits.append("~" + _human_size(e["filesize"]))
        video_choices.append(
            {
                "kind": "video",
                "id": f"video-{h}",
                "label": f"{h}p{fps_tag}",
                "sub": " · ".join(sub_bits),
                "height": h,
                "fps": e.get("fps"),
                "ext": "mp4",
                "vcodec": e.get("vcodec"),
                "filesize": e.get("filesize"),
                "format_spec": spec,
            }
        )

    audio_choices = [
        {
            "kind": "audio",
            "id": "audio-mp3",
            "label": "Audio only (MP3 320kbps)",
            "sub": "Extracted to MP3",
            "ext": "mp3",
            "format_spec": "bestaudio/best",
            "audio_format": "mp3",
            "audio_quality": "320",
        },
        {
            "kind": "audio",
            "id": "audio-m4a",
            "label": "Audio only (M4A best)",
            "sub": "Original audio",
            "ext": "m4a",
            "format_spec": "bestaudio[ext=m4a]/bestaudio",
            "audio_format": None,
        },
    ]

    return {
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "platform": _platform_name(info),
        "formats": video_choices + audio_choices,
    }


def download(
    url: str,
    choice: dict,
    out_dir: str,
    progress_cb: Callable[[dict], None],
    cookies_browser: Optional[str] = None,
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
        "outtmpl": os.path.join(out_dir, "%(title).200B.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "format": choice["format_spec"],
        "progress_hooks": [hook],
        "merge_output_format": "mp4" if choice.get("kind") == "video" else None,
        "restrictfilenames": False,
        "windowsfilenames": False,
    }
    opts.update(_cookie_opts(cookies_browser))

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
    elif choice.get("kind") == "video" and final_path:
        # merge_output_format may rewrite the container to .mp4.
        base, ext = os.path.splitext(final_path)
        mp4 = f"{base}.mp4"
        if ext.lower() != ".mp4" and os.path.exists(mp4):
            final_path = mp4

    return {
        "title": info.get("title"),
        "filepath": final_path,
    }


def _human_size(b: Optional[int]) -> str:
    if not b:
        return ""
    size = float(b)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if size >= 10 or unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.0f} GB"
