#!/usr/bin/env python3
"""
app.py — Always-on FastAPI job-runner for the post-processing pipeline.

The Discord bot used to spawn `process.py` (and `merge_audio.py` / `vectorize.py`)
directly. In the containerized deployment the bot is pure Node, so it triggers
those same CLIs here over HTTP instead. This service is a thin job runner: it
shells out to the existing scripts, captures their output, and exposes job
status — so the pipeline logic in process.py/merge_audio.py/vectorize.py is
unchanged.

A single background worker processes one job at a time (post-processing is heavy
and the scripts already assume serial execution), mirroring the warm-model
queue in py-transcribe.

Start with:
    cd py-process && uvicorn app:app --host 0.0.0.0 --port 8300

Endpoints:
    POST /api/process    — run transcribe → merge → vectorize → summarize
    POST /api/merge      — run merge_audio.py only
    POST /api/vectorize  — run vectorize.py (one session or "all")
    GET  /api/jobs/{id}  — job status + recent log lines
    GET  /api/health     — readiness + queue depth
"""

import asyncio
import logging
import sys
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
LOG_TAIL = 200          # lines retained per job
MAX_JOBS = 100          # completed jobs retained before pruning oldest


def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-process/)."""
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        logger.error("config.yaml not found at %s", config_path)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

def _new_job(kind: str, target: str) -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,          # process | merge | vectorize
        "target": target,      # session name (or "all" for vectorize)
        "status": "queued",    # queued | running | done | failed
        "step": "queued",
        "returncode": None,
        "error": None,
        "log": deque(maxlen=LOG_TAIL),
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
    }


def _job_view(job: dict) -> dict:
    """Public, JSON-serialisable snapshot of a job."""
    return {
        "id": job["id"],
        "kind": job["kind"],
        "target": job["target"],
        "status": job["status"],
        "step": job["step"],
        "returncode": job["returncode"],
        "error": job["error"],
        "log_tail": list(job["log"]),
        "created_at": job["created_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def _build_command(job: dict, config: dict) -> list[str]:
    """Map a job to the CLI invocation that performs it."""
    kind = job["kind"]
    if kind == "process":
        cmd = [sys.executable, str(SCRIPT_DIR / "process.py"), job["target"]]
        if job.get("model"):
            cmd += ["--model", job["model"]]
        if job.get("language"):
            cmd += ["--language", job["language"]]
        if not job.get("summarize", True):
            cmd += ["--no-summarize"]
        if job.get("retranscribe"):
            cmd += ["--retranscribe"]
        if job.get("force_vectorize"):
            cmd += ["--force-vectorize"]
        return cmd
    if kind == "merge":
        return [sys.executable, str(SCRIPT_DIR / "merge_audio.py"), job["target"]]
    if kind == "vectorize":
        cmd = [sys.executable, str(SCRIPT_DIR / "vectorize.py")]
        if job["target"].lower() == "all":
            cmd += ["--all"]
        else:
            cmd += [job["target"]]
        if job.get("force"):
            cmd += ["--force"]
        return cmd
    raise ValueError(f"Unknown job kind: {kind}")


# ---------------------------------------------------------------------------
# Worker — single consumer, one job at a time
# ---------------------------------------------------------------------------

async def _worker(app: FastAPI) -> None:
    queue: asyncio.Queue = app.state.queue
    while True:
        job_id = await queue.get()
        job = app.state.jobs.get(job_id)
        if job is None:
            queue.task_done()
            continue

        app.state.active = job_id
        job["status"] = "running"
        job["step"] = "starting"
        job["started_at"] = time.time()
        logger.info("Job %s (%s %s) started", job_id, job["kind"], job["target"])

        try:
            cmd = _build_command(job, app.state.config)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(REPO_ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                job["log"].append(line)
                if "Step:" in line:
                    job["step"] = line.split("Step:", 1)[1].strip()
                logger.info("[%s] %s", job_id, line)

            job["returncode"] = await proc.wait()
            job["status"] = "done" if job["returncode"] == 0 else "failed"
            job["step"] = "complete" if job["returncode"] == 0 else "failed"
        except Exception as exc:  # noqa: BLE001 — never let one job kill the worker
            job["status"] = "failed"
            job["error"] = str(exc)
            logger.error("Job %s failed: %s", job_id, exc)
        finally:
            job["finished_at"] = time.time()
            app.state.active = None
            queue.task_done()
            logger.info("Job %s finished: %s", job_id, job["status"])


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(dotenv_path=REPO_ROOT / ".env")
    app.state.config = load_config()
    app.state.jobs = {}
    app.state.queue = asyncio.Queue()
    app.state.active = None
    worker_task = asyncio.create_task(_worker(app))
    logger.info("Post-process job runner ready (python=%s)", sys.executable)
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="MLX AI Post-Process Job Runner",
    description="Runs the transcribe/merge/vectorize/summarize pipeline as background jobs.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ProcessRequest(BaseModel):
    session: str = Field(..., description="Session folder name")
    model: str | None = Field(None, description="Whisper model size (batch fallback)")
    language: str | None = Field(None, description="Language code, or null for default")
    summarize: bool = Field(True, description="Generate a session summary")
    retranscribe: bool = Field(
        False, description="Clear sidecars and re-transcribe the audio before assembling"
    )
    force_vectorize: bool = Field(
        False, description="Re-index the session even if it is already in the vector DB"
    )


class MergeRequest(BaseModel):
    session: str = Field(..., description="Session folder name")


class VectorizeRequest(BaseModel):
    session: str = Field(..., description="Session folder name, or 'all'")
    force: bool = Field(False, description="Re-index even if already vectorized")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enqueue(app: FastAPI, job: dict) -> dict:
    # Prune old finished jobs to bound memory.
    if len(app.state.jobs) > MAX_JOBS:
        finished = sorted(
            (j for j in app.state.jobs.values() if j["finished_at"]),
            key=lambda j: j["finished_at"],
        )
        for old in finished[: len(app.state.jobs) - MAX_JOBS]:
            app.state.jobs.pop(old["id"], None)

    app.state.jobs[job["id"]] = job
    app.state.queue.put_nowait(job["id"])
    return {"job_id": job["id"], "status": job["status"]}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/process", status_code=202)
async def process_endpoint(req: ProcessRequest):
    job = _new_job("process", req.session)
    job.update(
        model=req.model,
        language=req.language,
        summarize=req.summarize,
        retranscribe=req.retranscribe,
        force_vectorize=req.force_vectorize,
    )
    return _enqueue(app, job)


@app.post("/api/merge", status_code=202)
async def merge_endpoint(req: MergeRequest):
    return _enqueue(app, _new_job("merge", req.session))


@app.post("/api/vectorize", status_code=202)
async def vectorize_endpoint(req: VectorizeRequest):
    job = _new_job("vectorize", req.session)
    job.update(force=req.force)
    return _enqueue(app, job)


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job id: {job_id}")
    return _job_view(job)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "queue_depth": app.state.queue.qsize() if hasattr(app.state, "queue") else 0,
        "active_job": getattr(app.state, "active", None),
        "jobs_tracked": len(getattr(app.state, "jobs", {})),
    }
