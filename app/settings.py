from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"
DEFAULT_DOWNLOADS = str(Path.home() / "Downloads")
MAX_RECENT = 10

_lock = Lock()


def _default_settings() -> dict:
    return {
        "default_folder": DEFAULT_DOWNLOADS,
        "recent_folders": [DEFAULT_DOWNLOADS],
    }


def _validate(data: dict) -> dict:
    out = _default_settings()
    if isinstance(data, dict):
        df = data.get("default_folder")
        if isinstance(df, str) and df.strip():
            out["default_folder"] = os.path.expanduser(df)
        recents = data.get("recent_folders")
        if isinstance(recents, list):
            seen: list[str] = []
            for r in recents:
                if isinstance(r, str) and r.strip():
                    p = os.path.expanduser(r)
                    if p not in seen:
                        seen.append(p)
            if seen:
                out["recent_folders"] = seen[:MAX_RECENT]
    if out["default_folder"] not in out["recent_folders"]:
        out["recent_folders"].insert(0, out["default_folder"])
        out["recent_folders"] = out["recent_folders"][:MAX_RECENT]
    return out


def load() -> dict:
    with _lock:
        if not SETTINGS_PATH.exists():
            data = _default_settings()
            _write_unlocked(data)
            return data
        try:
            raw = json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            raw = {}
        return _validate(raw)


def save(data: dict) -> dict:
    with _lock:
        validated = _validate(data)
        _write_unlocked(validated)
        return validated


def remember_folder(folder: str) -> dict:
    folder = os.path.expanduser(folder)
    with _lock:
        current = (
            _validate(json.loads(SETTINGS_PATH.read_text()))
            if SETTINGS_PATH.exists()
            else _default_settings()
        )
        recents = [folder] + [f for f in current["recent_folders"] if f != folder]
        current["recent_folders"] = recents[:MAX_RECENT]
        _write_unlocked(current)
        return current


def _write_unlocked(data: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))
