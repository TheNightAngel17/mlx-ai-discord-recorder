# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

> _Hash: 138a862583563980dfd8e98425f5da902bd938d2_

### Added

- **py-process/summarize.py** — New standalone summarization script (Phase 6):
  - Reads `_combined_transcript.txt` and sends it to the configured chat provider (Ollama, OpenAI, or Anthropic) with a structured system prompt
  - LLM generates a narrative summary (2–4 paragraphs), key moments with timestamps and categories (`combat`, `plot_reveal`, `npc_introduction`, `funny_moment`, `decision_point`), and lists of NPCs, locations, and items
  - Outputs `_session_summary.json` (structured) and `_session_summary.md` (human-readable) to the session folder
  - Stores the narrative summary (`type=summary`) and each key moment (`type=key_moment`) as ChromaDB chunks for improved cross-session RAG quality
  - CLI: `python summarize.py <session_name>` or `python summarize.py --all`
- **py-process/process.py** — Integrated `summarize.py` as Step 4 of the post-processing pipeline (runs after vectorization when `auto_summarize: true`)
- **js-bot/postProcessor.js** — After successful post-processing, reads `_session_summary.md` and posts it to the configured announce channel (split across messages if needed to stay within Discord's 2000-char limit)
- **config.yaml** — Added `auto_summarize: true` and `summary_max_tokens: 2000` settings

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

- Upgraded `@discordjs/voice` to ^0.19.2 for DAVE protocol support
- Codebase cleanup: moved inline `require()` calls to top-level imports, added comprehensive JSDoc comments

### Fixed

- Voice connection timeout caused by missing DAVE protocol support (upgraded `@discordjs/voice`) and missing encryption library (added `sodium-native`)
- Double-transcription bug in `transcribe.py` — Whisper results are now cached per-user
- Per-user WAV timeline alignment — silence padding via `SilencePadTransform` keeps all files in sync

### Removed

- Archived the original Python bot (`py-bot_old/`) — non-functional due to Discord DAVE/E2EE requirements
