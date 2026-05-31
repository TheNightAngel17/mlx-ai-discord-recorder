# py-transcribe — Live Transcription Service

An always-on FastAPI service that loads a single Whisper model **once** and keeps
it warm, transcribing utterance snippets **as they are recorded** instead of in
one cold-start batch after the session ends.

## Why it exists

The recorder writes each utterance as a snippet WAV (`<session>/<username>/<offset_ms>.wav`).
Without this service, `py-process/transcribe.py` loads Whisper from scratch at
post-process time and transcribes every snippet serially while you wait. This
service moves that work to **during** the session:

- **One warm model, one worker, one transcription at a time.** Running multiple
  Whisper instances on a single GPU just contends for VRAM/CUDA and doesn't help
  — so snippets are processed serially behind a warm model. The win is *temporal*
  (work spread across the session), not parallel.
- By session end, almost everything is already transcribed, so the post-stop step
  collapses to "assemble the combined transcript from cached results."

## How it fits

```
recorder.js  ──POST /api/transcribe (fire-and-forget)──►  this service
                                                            │ warm model, 1 worker
                                                            ▼
                              writes <session>/<username>/<offset_ms>.json sidecar
process.py  ──POST /api/session/finalize (drain queue)──►  this service
transcribe.py  ── assembles _combined_transcript.txt from the sidecars
```

**Sidecar format** (`<offset_ms>.json`, written atomically next to each WAV):

```json
{ "offset_ms": 12840, "language": "en",
  "segments": [ { "start": 12.84, "end": 14.10, "text": "..." } ] }
```

Timestamps are session-relative (the snippet offset is already added). Sidecars
on disk are the source of truth: a service restart loses only the in-memory
queue, never data — `transcribe.py` fills any gaps at post-process time.

## Run it

```bash
cd py-transcribe
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8200
```

The service reads the repo-root `config.yaml` and `.env` on startup.

## Configuration (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `auto_transcribe` | `true` | The recorder enqueues snippets live when on |
| `transcribe_api_port` | `8200` | Port the service listens on / the bot posts to |
| `transcribe_provider` | `faster-whisper` | `faster-whisper` or `openai-whisper` |
| `transcribe_model` | `whisper_model` | Model size (falls back to `whisper_model`) |
| `transcribe_device` | `cuda` | `cuda`, `cpu`, or `auto` |
| `transcribe_compute_type` | `float16` | faster-whisper compute type (`int8` on CPU) |
| `transcribe_min_ms` | `400` | Snippets shorter than this are skipped (likely noise) |

Language is shared with the batch pipeline via `whisper_language`.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/transcribe` | Enqueue a snippet `{ session, username, offset_ms, wav_path, language? }` → `202` |
| `POST` | `/api/session/finalize` | Block until a session's queue drains `{ session, timeout_s? }` |
| `GET`  | `/api/session/{session}/status` | Pending snippet count for a session |
| `GET`  | `/api/health` | Readiness, backend/model/device, queue depth |

## Docker / Kubernetes graduation

This service is the natural first containerized unit. To scale beyond one node:

1. **Containerize** this directory (CUDA base image; mount `config.yaml`/`.env`).
2. **Swap the in-memory `asyncio.Queue` for a shared broker** (Redis / RabbitMQ /
   Celery) so multiple **worker replicas** can pull from it — each replica = one
   warm model on one GPU. Do **not** multi-thread one box.
3. **Snippet delivery**: locally the recorder passes a `wav_path` on a shared
   filesystem. In K8s, either mount the recordings on a shared volume (PVC) or
   switch `/api/transcribe` to accept audio bytes in the request body — the
   request model is intentionally left open to that.
