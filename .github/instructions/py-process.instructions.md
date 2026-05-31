---
applyTo: "py-process/**"
---

# Python Processing Pipeline — Copilot Instructions

## Language & Runtime

- **Runtime:** Python 3.11+
- **Style:** Follow PEP 8. Use type hints on all function signatures.
- **Imports:** Standard library → third-party → local, separated by blank lines.
- **Naming:** snake_case for variables/functions, PascalCase for classes, UPPER_SNAKE_CASE for constants.
- **Logging:** `logger = logging.getLogger(__name__)`. Never use bare `print()`.
- **Docstrings:** Triple-quote docstrings on all public functions and classes, with parameter descriptions for non-obvious arguments.
- **Path handling:** Always use `pathlib.Path` — never string concatenation for paths.
- **Config loading:** Read from `../config.yaml` (relative to script location) using `PyYAML`. Never hardcode paths or settings.

## Naming Conventions

| Context | Convention | Example |
|---|---|---|
| Variables / functions | snake_case | `session_name`, `merge_audio()` |
| Classes | PascalCase | `AudioProcessor` |
| Constants | UPPER_SNAKE_CASE | `SAMPLE_RATE`, `FRAME_BYTES` |
| Config keys | snake_case | `output_directory`, `whisper_model` |

## Code Quality Checklist

1. Imports organized: stdlib → third-party → local.
2. No unused imports or variables. Remove dead code.
3. All public functions and classes have docstrings.
4. Every `subprocess.run()` checks the return code.
5. No hardcoded values — anything configurable goes in `config.yaml`.
6. Consistent logging — `getLogger(__name__)`, not bare `print()`.
7. Use `pathlib.Path` for all file and directory operations.

## Architecture Decisions (do not change without discussion)

- **Spawned by the JS bot.** These scripts are invoked via `child_process.spawn()` from `js-bot/`. Do not assume they are run standalone by default.
- **Use list arguments in `subprocess.run()`.** Never `shell=True` — this prevents shell injection.
- **Single ChromaDB collection** (`dnd_sessions`). All sessions share one collection with metadata filtering — not one collection per session.
- **Snippet layout & filename-encoded offsets.** `recorder.js` writes per-user utterance snippets to `<session>/<username>/<offset_ms>.wav`, where the filename is the snippet's session-relative start offset in milliseconds. Pipeline scripts derive all timing from that offset (add it to Whisper timestamps; place clips at it when mixing) — there is no in-file silence padding to preserve. Skip `_`/`.`-prefixed entries when scanning a session folder.
- **Sidecar-aware transcription.** When `auto_transcribe` is on, the live service (`py-transcribe`) writes `<offset_ms>.json` sidecars (session-relative segments) during the session. `transcribe.py` loads sidecars when present and **only loads Whisper for snippets that lack one** (skipping it entirely when all are present); `process.py` first calls `/api/session/finalize` to drain the queue. Keep `transcribe.py` runnable standalone as the full batch fallback, and keep `_combined_transcript.txt` format unchanged so vectorize/summarize stay compatible.

## Common Pitfalls

- **Changing `embedding_provider` or `embedding_model`** after vectorizing breaks existing RAG queries. Always warn users to re-run `python py-process/vectorize.py --all --force` when these config values change.
- **WAV header size limits:** WAV uses a 32-bit size field (~4 GB max). Very long recordings can exceed this. Guard against it when writing output files.
- **Config validation:** Don't assume `config.yaml` is well-formed. Check for `None` values and fall back to safe defaults.
