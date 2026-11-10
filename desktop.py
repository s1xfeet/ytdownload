"""Native macOS window for YTDownload.

Starts the FastAPI server on a free local port in a background thread, then
opens a pywebview (WebKit) window pointed at it — so the app runs in its own
Mac window with no browser, no tabs, and no URL to type. Closing the window
shuts everything down.

Run in dev:   ./.venv/bin/python desktop.py
In the .app:  the bundle launcher runs this file with the bundled venv.
"""
from __future__ import annotations

import socket
import threading
import time
import urllib.request

WINDOW_TITLE = "YTDownload"
WIDTH, HEIGHT = 920, 800
MIN_SIZE = (680, 620)
PREFERRED_PORT = 8765


def _find_free_port(preferred: int = PREFERRED_PORT) -> int:
    """Return the first free port at or above `preferred`."""
    port = preferred
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # connect_ex == 0 means something is already listening (taken).
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    # Fallback: let the OS assign one.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int):
    """Launch uvicorn in a daemon thread and return the server object."""
    import uvicorn

    from app.main import app  # imports certifi/SSL setup as a side effect

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    # Signal handlers can only be installed on the main thread; pywebview owns
    # it, so make this a no-op and run the server off-main.
    server.install_signal_handlers = lambda: None
    threading.Thread(target=server.run, daemon=True).start()
    return server


def _wait_ready(port: int, timeout: float = 20.0) -> bool:
    """Poll the server until it answers or the timeout elapses."""
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


class Api:
    """Methods exposed to the page as ``window.pywebview.api.*``.

    Only available when running inside the native window (not a plain browser),
    which is how the frontend decides whether to show the "Choose folder" button.
    """

    def choose_folder(self, start: str | None = None):
        """Open a native macOS folder picker; return the chosen path or None."""
        import webview

        try:
            folder_type = webview.FileDialog.FOLDER  # pywebview >= 6
        except AttributeError:
            folder_type = webview.FOLDER_DIALOG  # older fallback

        window = webview.active_window()
        if window is None:
            windows = getattr(webview, "windows", None)
            window = windows[0] if windows else None
        if window is None:
            return None

        result = window.create_file_dialog(folder_type, directory=start or "")
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result


def main() -> None:
    import webview

    port = _find_free_port()
    server = _start_server(port)
    if not _wait_ready(port):
        raise SystemExit("YTDownload server did not start in time.")

    webview.create_window(
        WINDOW_TITLE,
        f"http://127.0.0.1:{port}",
        width=WIDTH,
        height=HEIGHT,
        min_size=MIN_SIZE,
        js_api=Api(),
    )
    # Blocks on the main thread until the window is closed.
    webview.start()

    # Window closed — ask the server to exit (the daemon thread also dies with
    # the process, releasing the port).
    try:
        server.should_exit = True
    except Exception:
        pass


if __name__ == "__main__":
    main()
