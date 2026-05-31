---
applyTo: "py-transcribe/**"
---

# Python Live Transcription Service — Copilot Instructions

## Purpose

Always-on FastAPI service that loads a Whisper model **once** (warm) and
transcribes utterance snippets as they're recorded, writing a `<offset_ms>.json`
sidecar next to each `<session>/<username>/<offset_ms>.wav`. Mirrors the
structure of `py-query/app.py`.

## Language & Runtime

- **Runtime:** Python 3.11+. PEP 8, type hints on all signatures.
- **Imports:** stdlib → third-party → local, blank-line separated.
- **Naming:** snake_case functions/vars, PascalCase classes, UPPER_SNAKE_CASE constants.
- **Logging:** `logger = logging.getLogger(__name__)` — no `print()` in the service.
- **Paths:** always `pathlib.Path`. **Config:** read `../config.yaml` via PyYAML; never hardcode.

## Architecture Decisions (do not change without discussion)

- **One warm model, one worker, one transcription at a time.** The service holds
  a single model and a single `asyncio.Queue` worker. Do **not** add parallel
  workers or load multiple models on one GPU — that contends for VRAM/CUDA and
  doesn't speed anything up. (Multi-replica scaling is a Kubernetes concern: more
  pods behind a shared broker, not more workers per box.)
- **Sidecars are the source of truth.** Per-snippet results are written
  atomically (temp file + `replace`) as `<offset_ms>.json` with **session-relative**
  timestamps (the snippet `offset_ms/1000` is added on write). A service restart
  loses only the in-memory queue; `py-process/transcribe.py` fills any gaps.
- **Two backends behind one interface.** `providers.py` exposes
  `TranscriptionProvider` with `faster-whisper` and `openai-whisper` impls,
  selected by `transcribe_provider`. Backend libraries are **lazy-imported**
  inside each impl's `__init__` so importing the module never requires both.
  whisper / faster-whisper are the only permitted transcription dependencies —
  no LLM SDKs here.
- **Never block the event loop.** Run the blocking `provider.transcribe()` via
  `loop.run_in_executor(...)`; only the single worker awaits it, preserving
  serialization while keeping `/api/health` and enqueues responsive.
- **Degrade gracefully.** If the model fails to load, mark the service not-ready
  and return `503` from `/api/transcribe` — the recorder + batch fallback cover it.

## Code Quality Checklist

1. Imports organized: stdlib → third-party → local.
2. No unused imports or variables.
3. Docstrings on all public functions/classes and FastAPI endpoints.
4. Sidecar writes are atomic; one failing snippet never stalls the queue.
5. No hardcoded values — anything configurable goes in `config.yaml`.
6. `pathlib.Path` for all file operations.

## Common Pitfalls

- **Reloading the model per request** defeats the entire point — load it once in
  the lifespan and reuse it.
- **Forgetting the offset.** Whisper times each clip from zero; the sidecar must
  store session-relative timestamps (add `offset_ms/1000`).
- **Config validation:** don't assume `config.yaml` is well-formed; fall back to
  safe defaults (e.g. `transcribe_min_ms`, port).
