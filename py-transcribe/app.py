#!/usr/bin/env python3
"""
app.py — Always-on FastAPI transcription service (warm Whisper model + queue).

Loads a single Whisper model once on startup and keeps it warm. The JS recorder
POSTs each utterance snippet to /api/transcribe as it finishes; a single
background worker transcribes snippets one at a time (no GPU contention) and
writes a per-snippet JSON sidecar next to the WAV:

    <session>/<username>/<offset_ms>.json
    { "offset_ms": 12840, "language": "en",
      "segments": [ { "start": 12.84, "end": 14.10, "text": "..." } ] }

Timestamps in the sidecar are session-relative (the snippet's offset has already
been added). At session stop the post-process pipeline calls
/api/session/finalize to drain the queue, then transcribe.py assembles the
combined transcript from the sidecars.

Start with:
    cd py-transcribe && uvicorn app:app --host 0.0.0.0 --port 8200
"""

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from providers import get_transcription_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-transcribe/)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        logger.error("config.yaml not found at %s", config_path)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Sidecar writing
# ---------------------------------------------------------------------------

def write_sidecar(wav_path: Path, offset_ms: int, language: str | None, segments: list[dict]) -> Path:
    """Write <offset_ms>.json next to the snippet WAV with session-relative segments.

    Written atomically (temp file + rename) so a reader never sees a partial file.
    """
    offset_s = offset_ms / 1000.0
    payload = {
        "offset_ms": offset_ms,
        "language": language or "unknown",
        "segments": [
            {
                "start": offset_s + float(s["start"]),
                "end": offset_s + float(s["end"]),
                "text": s["text"],
            }
            for s in segments
        ],
    }
    sidecar = wav_path.with_suffix(".json")
    tmp = wav_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(sidecar)  # atomic on the same filesystem
    return sidecar


# ---------------------------------------------------------------------------
# Worker — the single-consumer that guarantees one transcription at a time
# ---------------------------------------------------------------------------

async def _worker(app: FastAPI) -> None:
    """Drain the queue serially. Exactly one of these runs for the whole service."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = app.state.queue

    while True:
        item = await queue.get()
        session = item["session"]
        wav_path = Path(item["wav_path"])
        offset_ms = item["offset_ms"]
        language = item.get("language") or app.state.language
        try:
            if not app.state.ready:
                logger.warning("Transcriber not ready — dropping %s", wav_path.name)
            elif not wav_path.exists():
                logger.warning("Snippet not found, skipping: %s", wav_path)
            else:
                # Run the blocking transcription off the event loop. Because only
                # this single worker awaits it, transcriptions never overlap.
                segments = await loop.run_in_executor(
                    None, app.state.provider.transcribe, str(wav_path), language
                )
                write_sidecar(wav_path, offset_ms, language, segments)
                logger.info(
                    "Transcribed %s/%s (%d segments)",
                    session, wav_path.name, len(segments),
                )
        except Exception as exc:  # never let one bad snippet stall the queue
            logger.error("Failed to transcribe %s: %s", wav_path, exc)
        finally:
            queue.task_done()
            app.state.pending[session] = max(0, app.state.pending.get(session, 1) - 1)


# ---------------------------------------------------------------------------
# Lifespan — load the warm model and start the worker
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
    config = load_config()
    app.state.config = config

    # Resolve language default (shared with the batch pipeline).
    language = config.get("whisper_language") or None
    if language == "auto":
        language = None
    app.state.language = language
    app.state.min_ms = int(config.get("transcribe_min_ms", 400))

    # Build the warm model once.
    app.state.provider = None
    app.state.ready = False
    try:
        app.state.provider = get_transcription_provider(config)
        app.state.ready = True
        logger.info(
            "Transcriber ready: backend=%s model=%s device=%s",
            app.state.provider.backend,
            app.state.provider.model_name,
            app.state.provider.device,
        )
    except SystemExit:
        logger.error(
            "Transcriber init failed (bad config or missing backend). "
            "/api/transcribe will return 503 until fixed and the service restarted."
        )
    except Exception as exc:
        logger.error("Transcriber init failed: %s — /api/transcribe will return 503.", exc)

    # Queue + per-session pending counters + the single worker task.
    app.state.queue = asyncio.Queue()
    app.state.pending = {}  # session_name -> number of snippets still in flight
    worker_task = asyncio.create_task(_worker(app))

    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="MLX AI Transcription Service",
    description=(
        "Always-on warm-model Whisper transcription. Snippets are enqueued as they "
        "are recorded and transcribed one at a time; results are written as JSON "
        "sidecars next to each WAV."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TranscribeRequest(BaseModel):
    """Enqueue one utterance snippet for transcription."""

    session: str = Field(..., description="Session folder name")
    username: str = Field(..., description="Sanitised username (sub-folder name)")
    offset_ms: int = Field(..., ge=0, description="Session-relative start offset in ms")
    wav_path: str = Field(..., description="Absolute path to the snippet WAV")
    language: str | None = Field(None, description="Language code; defaults to config")


class FinalizeRequest(BaseModel):
    """Wait for a session's queue to drain so all sidecars exist."""

    session: str = Field(..., description="Session folder name")
    timeout_s: float = Field(600.0, gt=0, description="Max seconds to wait for drain")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/transcribe", status_code=202)
async def transcribe_endpoint(req: TranscribeRequest):
    """Enqueue a snippet. Returns immediately; the worker transcribes it."""
    if not app.state.ready:
        raise HTTPException(status_code=503, detail="Transcriber not ready.")

    app.state.pending[req.session] = app.state.pending.get(req.session, 0) + 1
    await app.state.queue.put(
        {
            "session": req.session,
            "username": req.username,
            "offset_ms": req.offset_ms,
            "wav_path": req.wav_path,
            "language": req.language,
        }
    )
    return {"status": "queued", "session": req.session, "offset_ms": req.offset_ms}


@app.post("/api/session/finalize")
async def finalize_endpoint(req: FinalizeRequest):
    """Block until the session has no snippets left in the queue (or timeout)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + req.timeout_s
    while app.state.pending.get(req.session, 0) > 0:
        if loop.time() >= deadline:
            break
        await asyncio.sleep(0.2)

    remaining = app.state.pending.get(req.session, 0)
    if remaining == 0:
        app.state.pending.pop(req.session, None)
    return {"session": req.session, "drained": remaining == 0, "remaining": remaining}


@app.get("/api/session/{session}/status")
async def session_status(session: str):
    """Report how many of a session's snippets are still pending transcription."""
    return {"session": session, "pending": app.state.pending.get(session, 0)}


@app.get("/api/health")
async def health():
    """Readiness + warm-model info + queue depth."""
    provider = app.state.provider
    return {
        "status": "ok" if app.state.ready else "degraded",
        "ready": app.state.ready,
        "backend": getattr(provider, "backend", None),
        "model": getattr(provider, "model_name", None),
        "device": getattr(provider, "device", None),
        "queue_depth": app.state.queue.qsize() if hasattr(app.state, "queue") else 0,
        "pending_sessions": {k: v for k, v in app.state.pending.items() if v > 0},
    }
