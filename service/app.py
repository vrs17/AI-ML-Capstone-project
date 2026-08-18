"""FastAPI service for the Uzbek Car Model Recognizer.

Endpoints
    GET  /                     operations console (the stakeholder screen)
    GET  /photo                snapshot inspector
    GET  /health               model + device + VRAM status
    GET  /metrics              decision counters
    POST /predict              one image  -> decision
    POST /predict/batch        many images -> decisions
    POST /video/upload         start a job from a file
    POST /video/camera         start a job from a webcam / RTSP url
    GET  /video/stream/{id}    annotated MJPEG   (?raw=1 for un-annotated)
    GET  /video/summary/{id}   live counters
    GET  /video/events/{id}    ordered event log (?after=<seq> for the tail)
    GET  /docs                 OpenAPI (automatic)
"""
from __future__ import annotations

import asyncio, logging, os, uuid, tempfile, shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from pipeline import recognizer
import video as vid

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

_sem = asyncio.Semaphore(settings.max_concurrent)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("loading model…")
    await asyncio.get_running_loop().run_in_executor(None, recognizer.load)
    yield
    log.info("shutting down")


app = FastAPI(title="Uzbek Car Model Recognizer",
              description="Two-stage vehicle recognition with calibrated abstention.",
              version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_here = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(_here, "static")), name="static")


async def _read(f: UploadFile) -> bytes:
    data = await f.read()
    if not data:
        raise HTTPException(400, f"{f.filename}: empty file")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"{f.filename}: larger than {settings.max_upload_mb} MB")
    if not (f.content_type or "").startswith("image/"):
        raise HTTPException(415, f"{f.filename}: not an image ({f.content_type})")
    return data


def _page(name: str):
    return FileResponse(os.path.join(_here, "static", name))


@app.get("/", include_in_schema=False)
async def console():
    """The operations console — what a site operator (and a stakeholder) actually looks at."""
    return _page("console.html")


@app.get("/photo", include_in_schema=False)
async def photo():
    return _page("index.html")


@app.get("/health")
async def health():
    h = recognizer.health()
    return JSONResponse(h, status_code=200 if h["ready"] else 503)


@app.get("/metrics")
async def metrics():
    s = dict(recognizer.stats)
    done = s["answered"] + s["rejected"] + s["abstained"]
    s["coverage"] = round(s["answered"] / done, 4) if done else None
    return s


@app.post("/predict")
async def predict(file: UploadFile = File(...)):
    if not recognizer.ready:
        raise HTTPException(503, "model still loading")
    data = await _read(file)
    async with _sem:
        try:
            pred = await asyncio.get_running_loop().run_in_executor(
                None, recognizer.predict, data)
        except HTTPException:
            raise
        except Exception as e:
            recognizer.stats["errors"] += 1
            log.exception("inference failed")
            raise HTTPException(500, f"inference failed: {e}") from e
    return {"filename": file.filename, **pred.to_dict()}


@app.post("/predict/batch")
async def predict_batch(files: list[UploadFile] = File(...)):
    if not recognizer.ready:
        raise HTTPException(503, "model still loading")
    if len(files) > settings.max_batch_files:
        raise HTTPException(413, f"at most {settings.max_batch_files} files per request")
    blobs = [(f.filename, await _read(f)) for f in files]
    loop = asyncio.get_running_loop()

    async def one(name, data):
        async with _sem:
            try:
                p = await loop.run_in_executor(None, recognizer.predict, data)
                return {"filename": name, **p.to_dict()}
            except Exception as e:
                recognizer.stats["errors"] += 1
                return {"filename": name, "error": str(e)}

    return {"results": await asyncio.gather(*[one(n, d) for n, d in blobs])}


# ─────────────────────────── video / realtime ────────────────────────────
@app.get("/video", include_in_schema=False)
async def video_page():
    return _page("console.html")          # kept: older bookmarks land on the console


@app.post("/video/upload")
async def video_upload(file: UploadFile = File(...)):
    """Accept a video, start a tracking job, return its id."""
    if not recognizer.ready:
        raise HTTPException(503, "model still loading")
    suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        shutil.copyfileobj(file.file, tmp)
    finally:
        tmp.close()
    job_id = uuid.uuid4().hex[:12]
    vid.jobs[job_id] = vid.VideoProcessor(tmp.name, label=file.filename or "uploaded video")
    log.info("video job %s -> %s", job_id, tmp.name)
    return {"job_id": job_id, "filename": file.filename,
            "stream_url": f"/video/stream/{job_id}", "summary_url": f"/video/summary/{job_id}"}


@app.post("/video/camera")
async def video_camera(source: str = "0"):
    """Start a job from a live camera: 0 for the local webcam, or an RTSP/HTTP URL."""
    if not recognizer.ready:
        raise HTTPException(503, "model still loading")
    src = int(source) if source.isdigit() else source
    label = f"webcam {source}" if source.isdigit() else source.split("@")[-1][:48]
    job_id = uuid.uuid4().hex[:12]
    vid.jobs[job_id] = vid.VideoProcessor(src, label=label)
    return {"job_id": job_id, "source": source, "stream_url": f"/video/stream/{job_id}",
            "summary_url": f"/video/summary/{job_id}"}


@app.get("/video/stream/{job_id}")
async def video_stream(job_id: str, raw: int = 0):
    """Annotated MJPEG stream — drop straight into an <img src=...>.

    `raw=1` streams the un-annotated frames (useful for a side-by-side "before" panel);
    detection and tracking still run, so the counters stay live either way.
    """
    job = vid.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job id")
    return StreamingResponse(job.frames(annotate=not raw),
                             media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/video/summary/{job_id}")
async def video_summary(job_id: str):
    job = vid.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job id")
    return job.summary()


@app.get("/video/events/{job_id}")
async def video_events(job_id: str, after: int = 0, limit: int = 60):
    """Ordered decision log. Poll with the last `seq` you saw to get only what is new."""
    job = vid.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job id")
    return job.recent_events(after=after, limit=limit)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.getenv("HOST", "0.0.0.0"),
                port=int(os.getenv("PORT", "8000")), workers=1)
