# YTDownload

Local video downloader for **YouTube, X (Twitter), TikTok, Instagram**, and any other [yt-dlp-supported site](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md). Paste a URL → pick a real format → file lands in the folder you choose. Runs entirely on `localhost`, no accounts, no cloud.

## Requirements

- macOS
- `python3` (3.11+)
- `ffmpeg` (`brew install ffmpeg`) — needed for merging video+audio and MP3 conversion

## Instagram & private videos

Instagram blocks logged-out access, so it needs your browser login. Open **Settings → Browser cookies** and pick the browser you're signed into Instagram with (Safari/Chrome/Firefox/Edge/Brave). yt-dlp reuses those cookies. Same setting unlocks age-gated or private videos on other sites. YouTube, X, and TikTok work without it.

## Dev mode

```sh
./start.sh
```

First run creates a `.venv/` and installs deps. The browser opens at `http://127.0.0.1:8765`.

## Build the macOS .app

```sh
./build_app.sh
mv YTDownload.app /Applications/
```

Then double-click `YTDownload` from Finder/Spotlight. It opens as a **native Mac window** (its own Dock icon, no browser, no URL) — the FastAPI server runs inside and a WebKit window (via `pywebview`) shows the UI. Closing the window quits everything.

To run the native window in dev without building the app:

```sh
./.venv/bin/python desktop.py
```

Launch log (the bundled app has no console): `$TMPDIR/ytdownload.log`.

## How it works

- `app/main.py` — FastAPI server, routes, SSE
- `app/downloader.py` — wraps the `yt_dlp` Python module (probe + download + progress hook)
- `app/jobs.py` — in-memory job registry; bridges progress callbacks (running on a thread) into SSE events
- `app/settings.py` — JSON-backed default folder + recent folders list
- `app/static/` — single-page vanilla JS frontend

Settings file: `settings.json` next to the source (gitignored).

## Downloads suddenly failing? Update yt-dlp

Sites (especially YouTube) change constantly. When downloads start failing site-wide — the usual symptom is `unable to download video data: HTTP Error 403: Forbidden` — update yt-dlp:

```sh
./update.sh
```

This upgrades yt-dlp to the **nightly** channel (where fixes land first, ahead of stable) in both the installed `/Applications` app and the dev `.venv`. Then quit and reopen YTDownload. No rebuild needed.
