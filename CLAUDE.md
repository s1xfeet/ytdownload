# CLAUDE.md — YTDownload

Context for Claude Code sessions working in this repo. Read this first, then help the user run, use, or extend the app.

## What this is

A **local** video downloader for **YouTube, X (Twitter), TikTok, Instagram**, and any other yt-dlp-supported site. The user pastes a URL into a web UI, the app probes the real available formats with `yt-dlp`, the user picks one (video quality or audio-only), and it downloads into a folder they choose — with a live progress bar. Everything runs on `127.0.0.1`. No accounts, no cloud, single user.

**Multi-platform note:** unlike YouTube (which serves separate video/audio streams), most social platforms serve a single muxed file, and some — notably X — omit codec metadata on their progressive mp4s. `downloader.probe` keys video options off `height` (not vcodec) so those formats aren't dropped, and always offers a universal "Best quality" option. **Instagram requires the user's browser login** (see the cookies gotcha below).

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

1. Paste a URL (YouTube / X / TikTok / Instagram / …) → after ~0.3s it auto-probes and shows the thumbnail, title, a platform badge, and format buttons.
2. Click a format — `Best quality` (always first), a specific resolution (e.g. `1080p`, `720p`), or `Audio only (MP3 320kbps)`.
3. The "Save to" folder is prefilled with the default; edit the path or pick from the recent-folders dropdown.
4. Click **Download** → a job card appears with a live progress bar (speed + ETA).
5. When done, click **Reveal in Finder** to jump to the file (macOS only).

Settings (gear/Settings button) lets the user change the default download folder, view recent folders, and pick a **browser to borrow cookies from** (required for Instagram).

## Architecture

```
desktop.py         # Native-window entry point (pywebview). Used by the .app bundle; not imported by the server.
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
| POST | `/api/probe` | `{url}` → `{title, uploader, duration, thumbnail, webpage_url, platform, formats[]}`. Reads `cookies_browser` from settings. Login-required failures return an actionable hint. |
| POST | `/api/download` | `{url, choice, out_dir}` → `{job_id}`; validates folder, remembers it, starts the thread |
| GET | `/api/jobs/{id}` | Snapshot (status, progress, result, error) — for reload recovery |
| GET | `/api/jobs/{id}/events` | **SSE** stream: `snapshot` → `progress`… → `done`/`error` |
| POST | `/api/reveal` | `{path}` → `open -R` (macOS Finder reveal) |

### Format selection (in `downloader.probe`)

- **Best quality (always first):** `format_spec = "bv*+ba/b"`. The safest choice on any platform, and the only video option when a site exposes no per-height metadata.
- **Video by height:** any format reporting a `height` becomes an option (keyed by height, *not* vcodec — this is what makes X's progressive mp4s show up). Each carries `format_spec = "b[height=H]/bv*[height=H]+ba/b[height<=H]"` — prefer a single progressive file, then a merged pair, then best-at-or-below. `merge_output_format="mp4"` muxes merges to one `.mp4`.
- **Audio:** `Audio only (MP3 320kbps)` uses the `FFmpegExtractAudio` postprocessor (works even on muxed-only platforms like TikTok — ffmpeg pulls the audio track); `Audio only (M4A best)` grabs `bestaudio[ext=m4a]/bestaudio`.
- **Cookies:** `downloader.probe`/`download` accept `cookies_browser`; when set, `cookiesfrombrowser=(browser,)` is added so yt-dlp reuses the user's browser login (Instagram, private/age-gated).
- Playlists are disabled (`noplaylist=True`). Output template is `%(title).200B.%(ext)s` — the `.200B` truncates long social captions (TikTok/X titles can be enormous) to a safe filename length.

## Known gotchas (you will likely hit these)

1. **`SSL: CERTIFICATE_VERIFY_FAILED` on probe/download.** python.org Python ships without trusted CA roots. The app already mitigates this: `main.py` imports `certifi` and sets `SSL_CERT_FILE` at startup. If it still fails, ensure certifi is installed in the venv (`./.venv/bin/pip install -U certifi`) or run `/Applications/Python\ 3.x/Install\ Certificates.command`.
2. **`ffmpeg` not found / merged file has no audio.** Install ffmpeg (`brew install ffmpeg`). When launched from Finder via the `.app`, the launcher prepends `/opt/homebrew/bin:/usr/local/bin` to `PATH` because Finder doesn't inherit the shell PATH. If you add features that call ffmpeg, keep that PATH handling.
3. **Port already in use.** `start.sh` and the `.app` launcher auto-increment the port. If the user reports "it opened the wrong/old tab," the server may be on 8766+. Check the terminal output for the actual URL.
4. **AV1-only low-res videos.** Some videos only offer AV1 at low resolutions. They merge fine and play in QuickTime on Apple Silicon, but if the user wants guaranteed H.264, suggest 720p+.
5. **yt-dlp breakage — the #1 recurring failure.** When YouTube changes its internals, downloads fail site-wide. The classic 2026 symptom is **`unable to download video data: HTTP Error 403: Forbidden`** on the *download* (probe/formats still work): YouTube's high-res `web`-client formats are PO-token-gated, and stable yt-dlp lags the fix. **Fix: `./update.sh`** — it upgrades yt-dlp to the **nightly** channel (`pip install -U --pre "yt-dlp[default]"`) in both the installed `/Applications` app and the dev `.venv`, then you restart the app. That's why `start.sh`/`build_app.sh` install nightly, not stable. Notes: nightly (not stable) is where YouTube fixes land first; the `android` player client downloads without a token but caps at ~360p, so it's not a substitute for updating. **Use `python -m pip`, never the `bin/pip` wrapper, on the installed app** — the bundle venv is built in the project dir then moved to `/Applications`, which leaves `bin/pip` with a dead absolute shebang (the `python` symlink still works, which is why the app runs). `update.sh` already does this.
6. **`/api/reveal` is macOS-only** (`open -R`). Harmless elsewhere but won't reveal.
7. **Instagram returns "empty media response" / login required.** Instagram blocks logged-out access. Fix: Settings → Browser cookies → pick the browser the user is signed into Instagram with. This sets `cookies_browser`, and `probe`/`download` pass `cookiesfrombrowser` to yt-dlp. The probe error is already rewritten (`_friendly_error` in `main.py`) to point the user there. Note: reading **Chrome/Brave/Edge** cookies on macOS can trigger a Keychain prompt; **Safari** may need Full Disk Access; **Firefox** is usually frictionless.
8. **X "Bad guest token" on some tweets.** Transient Twitter/X API hiccup, not our bug. Retry, update yt-dlp, or set browser cookies for X. Other tweets on the same run work fine (verified against public video tweets).

## macOS .app bundle (native window)

```sh
./build_app.sh          # produces YTDownload.app (self-contained venv inside)
mv YTDownload.app /Applications/
```

Double-clicking the app runs `desktop.py`, which starts the FastAPI server on a free port and opens a **native WebKit window** via `pywebview` — its own Dock icon, no browser, no URL. Closing the window quits the server. Dev-run the same window with `./.venv/bin/python desktop.py`. The bundle is gitignored (~90 MB with pyobjc).

- **`desktop.py`** is the native entry point: `_find_free_port` → `_start_server` (uvicorn in a daemon thread, signal handlers disabled since it's off the main thread) → `_wait_ready` → `webview.create_window(..., js_api=Api())` + `webview.start()` (blocks the main thread, which pywebview/Cocoa requires).
- **Native folder picker:** `desktop.Api.choose_folder(start)` is exposed to the page as `window.pywebview.api.choose_folder()` and opens a real macOS folder dialog (`FileDialog.FOLDER`). The frontend's "Choose…" buttons (Save-to row + Settings) call it and only unhide when `window.pywebview` exists — in a plain browser (dev via `start.sh`) they stay hidden and the user types a path. This is the only feature that behaves differently between the `.app` and the browser.
- **Launcher logs to `$TMPDIR/ytdownload.log`** — a bundled GUI app has no console, so this is where a failed launch (bad import, etc.) shows up.
- **Architecture pin (important):** the launcher runs `arch -arm64 ./venv/bin/python desktop.py`. The python.org Python is universal2, and LaunchServices can start it in the **x86_64** slice, which then can't load the arm64-only wheels (`pydantic_core`, etc.) — you get `ImportError: incompatible architecture (have 'arm64', need 'x86_64')` and the window silently never appears. Forcing `arch -arm64` fixes it; the launcher falls back to a plain launch on Intel Macs. If you ever see that ImportError in the log, this is why.

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
- **Custom filename templates:** expose `outtmpl` in settings.

Already done: multi-platform support (YouTube/X/TikTok/Instagram/…) and browser-cookie auth via the `cookies_browser` setting.

## Conventions

- No test suite yet. The clean unit-test seams are `downloader.probe` (format parsing) and `settings.py` (persistence/validation). Add `pytest` if you write tests.
- Keep `settings.json` out of git (already in `.gitignore`) — it holds the user's local folder paths.
- Frontend is intentionally build-step-free. Don't add a bundler unless the user asks.
