from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path

# Ensure SSL certs are available regardless of how Python was installed
# (python.org builds ship without certs by default).
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import downloader, settings
from .jobs import Job, JobRegistry

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="YTDownload")
registry = JobRegistry()


@app.on_event("startup")
async def _on_startup() -> None:
    registry.attach_loop(asyncio.get_running_loop())


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/settings")
async def get_settings() -> dict:
    return settings.load()


@app.put("/api/settings")
async def put_settings(payload: dict) -> dict:
    return settings.save(payload)


@app.post("/api/probe")
async def probe(payload: dict) -> dict:
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")
    try:
        return await asyncio.to_thread(downloader.probe, url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read video: {e}")


@app.post("/api/download")
async def start_download(payload: dict) -> dict:
    url = (payload.get("url") or "").strip()
    choice = payload.get("choice")
    out_dir = (payload.get("out_dir") or "").strip()
    if not url or not isinstance(choice, dict) or not out_dir:
        raise HTTPException(status_code=400, detail="url, choice, and out_dir are required")
    out_dir = os.path.expanduser(out_dir)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Bad folder: {e}")
    if not os.access(out_dir, os.W_OK):
        raise HTTPException(status_code=400, detail=f"Folder not writable: {out_dir}")

    settings.remember_folder(out_dir)
    job = registry.create(url, choice, out_dir)
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
    return {"job_id": job.id}


def _run_job(job: Job) -> None:
    def cb(progress: dict) -> None:
        registry.update_progress(job, progress)

    try:
        result = downloader.download(job.url, job.choice, job.out_dir, cb)
        registry.mark_done(job, result)
    except Exception as e:
        registry.mark_error(job, str(e))


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    queue = registry.subscribe(job_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="No such job")

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                event = msg.get("event", "message")
                data = json.dumps(msg.get("data") or {})
                yield f"event: {event}\ndata: {data}\n\n"
                if event in ("done", "error"):
                    break
        finally:
            registry.unsubscribe(job_id, queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/reveal")
async def reveal(payload: dict) -> dict:
    path = payload.get("path")
    if not path or not isinstance(path, str) or not os.path.exists(path):
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        subprocess.run(["open", "-R", path], check=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}
