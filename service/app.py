"""FastAPI service for the Uzbek Car Model Recognizer.

Endpoints
    GET  /                 web UI
    GET  /health           model + device + VRAM status
    GET  /metrics          decision counters
    POST /predict          one image  -> decision
    POST /predict/batch    many images -> decisions
    GET  /docs             OpenAPI (automatic)
"""
from __future__ import annotations

import asyncio, logging, os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from pipeline import recognizer

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


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(_here, "static", "index.html"))


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=os.getenv("HOST", "0.0.0.0"),
                port=int(os.getenv("PORT", "8000")), workers=1)
