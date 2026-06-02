# YTDownload

Local YouTube downloader. Paste a URL → pick a real format → file lands in the folder you choose. Runs entirely on `localhost`, no accounts, no cloud.

## Requirements

- macOS
- `python3` (3.11+)
- `ffmpeg` (`brew install ffmpeg`) — needed for merging video+audio and MP3 conversion

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

Then double-click `YTDownload` from Finder/Spotlight.

## How it works

- `app/main.py` — FastAPI server, routes, SSE
- `app/downloader.py` — wraps the `yt_dlp` Python module (probe + download + progress hook)
- `app/jobs.py` — in-memory job registry; bridges progress callbacks (running on a thread) into SSE events
- `app/settings.py` — JSON-backed default folder + recent folders list
- `app/static/` — single-page vanilla JS frontend

Settings file: `settings.json` next to the source (gitignored).

## Updating yt-dlp

```sh
./.venv/bin/pip install -U yt-dlp
```

For the bundled `.app`, rebuild with `./build_app.sh`.
