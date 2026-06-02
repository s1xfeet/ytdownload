# CLAUDE.md — YTDownload

Context for Claude Code sessions working in this repo. Read this first, then help the user run, use, or extend the app.

## What this is

A **local** YouTube downloader. The user pastes a URL into a web UI, the app probes the real available formats with `yt-dlp`, the user picks one (video quality or audio-only), and it downloads into a folder they choose — with a live progress bar. Everything runs on `127.0.0.1`. No accounts, no cloud, single user.

**Stack:** Python + FastAPI backend, vanilla JS/HTML/CSS frontend (no build step). `yt-dlp` is imported as a Python module (not shelled out). `ffmpeg` does the video+audio merge and MP3 conversion.

**Platform:** Built and tested on macOS (Apple Silicon). The helper scripts use `open`, `lsof`, and `/opt/homebrew` — see "Other platforms" below for Linux/Windows.

## Quick start (what to tell the user)

```sh
./start.sh
```

That's it. On first run `start.sh` creates `.venv/`, installs deps from `requirements.txt`, finds a free port (default **8765**, auto-increments if taken), starts uvicorn, and opens the browser at `http://127.0.0.1:8765`.

**Prerequisites:**
- `python3` 3.11+
- `ffmpeg` — install with `brew install ffmpeg`. Without it, merged-video downloads and MP3 conversion will fail.

**To stop:** Ctrl-C in the terminal running `start.sh`.

## How a user uses the app

1. Paste a YouTube URL → after ~0.3s it auto-probes and shows the thumbnail, title, and format buttons.
2. Click a format (e.g. `1080p`, `720p`, `Audio only (MP3 320kbps)`).
3. The "Save to" folder is prefilled with the default; edit the path or pick from the recent-folders dropdown.
4. Click **Download** → a job card appears with a live progress bar (speed + ETA).
5. When done, click **Reveal in Finder** to jump to the file (macOS only).

Settings (gear/Settings button) lets the user change the default download folder and view recent folders.

## Architecture

```
app/
├── main.py        # FastAPI app: routes + SSE endpoint. Spawns each download on a daemon thread.
├── downloader.py  # yt-dlp wrapper: probe() builds the format list; download() runs yt-dlp with a progress hook.
├── jobs.py        # JobRegistry: in-memory jobs keyed by id; bridges thread-side progress callbacks → asyncio queues for SSE.
├── settings.py    # JSON persistence (../settings.json): default_folder + recent_folders (MRU, capped at 10).
└── static/        # Frontend: index.html, app.js (paste→probe→download→SSE), styles.css (dark UI).
```

**Why threads + a queue:** `yt-dlp` is synchronous and blocking. Each download runs on its own `threading.Thread`. Its progress hook pushes snapshots into the `JobRegistry`, which uses `loop.call_soon_threadsafe` to hand events to per-subscriber `asyncio.Queue`s. The SSE endpoint drains that queue. This is the load-bearing concurrency seam — preserve it if you refactor.

### HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serves the SPA (`static/index.html`) |
| GET / PUT | `/api/settings` | Read / write default folder + recents |
| POST | `/api/probe` | `{url}` → `{title, uploader, duration, thumbnail, webpage_url, formats[]}` |
| POST | `/api/download` | `{url, choice, out_dir}` → `{job_id}`; validates folder, remembers it, starts the thread |
| GET | `/api/jobs/{id}` | Snapshot (status, progress, result, error) — for reload recovery |
| GET | `/api/jobs/{id}/events` | **SSE** stream: `snapshot` → `progress`… → `done`/`error` |
| POST | `/api/reveal` | `{path}` → `open -R` (macOS Finder reveal) |

### Format selection (in `downloader.probe`)

- **Video:** formats are grouped by height (1080/720/480/360/…). Each option carries a yt-dlp `format_spec` like `bv*[height=1080]+ba/b[height=1080]/b[height<=1080]`, and `merge_output_format="mp4"` so ffmpeg muxes to a single `.mp4`.
- **Audio:** `Audio only (MP3 320kbps)` uses `FFmpegExtractAudio` postprocessor; `Audio only (M4A best)` grabs `bestaudio[ext=m4a]`.
- Playlists are disabled (`noplaylist=True`): pasting a playlist URL downloads only the single linked video. Output template is `%(title)s.%(ext)s`.

## Known gotchas (you will likely hit these)

1. **`SSL: CERTIFICATE_VERIFY_FAILED` on probe/download.** python.org Python ships without trusted CA roots. The app already mitigates this: `main.py` imports `certifi` and sets `SSL_CERT_FILE` at startup. If it still fails, ensure certifi is installed in the venv (`./.venv/bin/pip install -U certifi`) or run `/Applications/Python\ 3.x/Install\ Certificates.command`.
2. **`ffmpeg` not found / merged file has no audio.** Install ffmpeg (`brew install ffmpeg`). When launched from Finder via the `.app`, the launcher prepends `/opt/homebrew/bin:/usr/local/bin` to `PATH` because Finder doesn't inherit the shell PATH. If you add features that call ffmpeg, keep that PATH handling.
3. **Port already in use.** `start.sh` and the `.app` launcher auto-increment the port. If the user reports "it opened the wrong/old tab," the server may be on 8766+. Check the terminal output for the actual URL.
4. **AV1-only low-res videos.** Some videos only offer AV1 at low resolutions. They merge fine and play in QuickTime on Apple Silicon, but if the user wants guaranteed H.264, suggest 720p+.
5. **yt-dlp extractor breakage.** When YouTube changes its site, downloads start failing site-wide. Fix: `./.venv/bin/pip install -U yt-dlp`. This is the #1 cause of "it stopped working" months later.
6. **`/api/reveal` is macOS-only** (`open -R`). Harmless elsewhere but won't reveal.

## macOS .app bundle

```sh
./build_app.sh          # produces YTDownload.app (self-contained venv inside)
mv YTDownload.app /Applications/
```

Double-clicking the app boots the local server and opens the browser. The bundle is gitignored (it's ~60 MB).

## Other platforms (Linux/Windows)

`start.sh` and `build_app.sh` are macOS bash. To run elsewhere:

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8765
# then open http://127.0.0.1:8765 manually
```

`/api/reveal` (Finder) won't work; everything else does. On Windows, swap path separators and use `py -m venv`.

## Extending it (currently out of scope)

If the user asks for more, these are the natural additions:
- **Playlists / batch URLs:** loosen `noplaylist=True` in `downloader.py`; the frontend would need a per-entry job list.
- **Subtitles / chapters:** add `writesubtitles` / `writeautomaticsub` to the yt-dlp opts.
- **Private/age-gated videos:** add `cookiesfrombrowser` to the opts in `downloader.download`.
- **Custom filename templates:** expose `outtmpl` in settings.

## Conventions

- No test suite yet. The clean unit-test seams are `downloader.probe` (format parsing) and `settings.py` (persistence/validation). Add `pytest` if you write tests.
- Keep `settings.json` out of git (already in `.gitignore`) — it holds the user's local folder paths.
- Frontend is intentionally build-step-free. Don't add a bundler unless the user asks.
