# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

> _Hash: 138a862583563980dfd8e98425f5da902bd938d2_

### Added

- **py-query/app.py** — Always-on FastAPI RAG API service with three endpoints:
  - `POST /api/query` — accepts `{question, session?, top_k?, show_sources?}`, returns `{answer, sources[], timings}`
  - `GET /api/sessions` — lists all indexed session names from ChromaDB
  - `GET /api/health` — returns ChromaDB status, chunk count, and provider info
  - Config, providers, and ChromaDB are loaded once at startup for zero cold-start per query
- **config.yaml** — Added `query_api_host: 0.0.0.0` and `query_api_port: 8100` settings
- **js-bot/** — Discord voice recording bot built on `discord.js` + `@discordjs/voice` with native DAVE/E2EE support
  - Per-user WAV recording with silence padding for time-aligned audio
  - Slash commands: `/mlx-ai record`, `/mlx-ai post-process`, `/mlx-ai merge-audio`, `/mlx-ai vectorize`, `/mlx-ai query`
  - Auto-stop when voice channel empties, mid-session join detection
  - Interactive post-processing button with model selector after recording stops
  - Text-channel announcements for recording lifecycle events
- **py-process/** — Python audio processing pipeline
  - `transcribe.py` — Whisper speech-to-text (per-user + combined transcripts)
  - `merge_audio.py` — Mix per-user WAVs into combined WAV + MP3 via pydub/ffmpeg
  - `vectorize.py` — Chunk and embed transcripts into ChromaDB via Ollama
  - `vectordb_helper.py` — CLI tool to inspect, search, and manage the vector database
  - `process.py` — Orchestrator that runs transcribe → merge → vectorize in sequence
- **py-query/** — RAG query service with multi-provider LLM support
  - `query.py` — CLI entry point for natural-language questions about recorded sessions
  - `rag.py` — Core RAG logic (embed → retrieve → generate)
  - `providers.py` — Provider abstraction for Ollama, OpenAI, and Anthropic (no vendor SDKs)
- `config.yaml` — Centralized non-secret configuration for all services
- `.env.example` — Template for secrets (Discord token, guild ID, API keys)
- `.github/copilot-instructions.md` — Project-wide Copilot instructions covering coding conventions, security scrutiny checklist, documentation requirements, and architecture decisions

### Changed

- **js-bot/queryHandler.js** — Replaced `child_process.spawn(query.py)` with a `fetch()` call to `http://localhost:<query_api_port>/api/query`. Eliminates ~1–2 s cold-start per query. Handles API-down and timeout errors gracefully with user-friendly Discord messages.
- **py-query/rag.py** — `query_rag()` now accepts optional `embedding_provider`, `chat_provider`, and `chroma_client` parameters so the API can pass pre-initialised objects (CLI path unchanged — passes nothing, creates fresh objects).
- **py-query/requirements.txt** — Added `fastapi>=0.111.0` and `uvicorn[standard]>=0.29.0`
- Upgraded `@discordjs/voice` to ^0.19.2 for DAVE protocol support
- Codebase cleanup: moved inline `require()` calls to top-level imports, added comprehensive JSDoc comments

### Fixed

- Voice connection timeout caused by missing DAVE protocol support (upgraded `@discordjs/voice`) and missing encryption library (added `sodium-native`)
- Double-transcription bug in `transcribe.py` — Whisper results are now cached per-user
- Per-user WAV timeline alignment — silence padding via `SilencePadTransform` keeps all files in sync

### Removed

- Archived the original Python bot (`py-bot_old/`) — non-functional due to Discord DAVE/E2EE requirements
