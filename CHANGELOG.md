# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `py-process/merge_audio.py` — new CLI script that mixes all per-user WAV recordings for a session into a single `_session_mix.wav` (all users overlaid at time zero), then compresses the combined mix to `_session_mix.mp3`.
  - Usage: `python merge_audio.py <session_name>`
  - Config: `mp3_bitrate` (default `"128k"`) and `keep_wav` (default `true`) in `config.yaml`.
- `mp3_bitrate` and `keep_wav` settings in `config.yaml` to control MP3 compression bitrate and WAV file retention.
- `pydub>=0.25.1` added to `py-process/requirements.txt` (wraps `ffmpeg`, already a prerequisite).
- `py-process/vectorize.py` — transcript vectorization tool that chunks `_combined_transcript.txt` into configurable time-window segments, embeds each chunk via Ollama (`nomic-embed-text`), and persists the embeddings to a local ChromaDB vector database.
  - CLI: `python vectorize.py <session_name>` or `python vectorize.py --all` to process every session.
  - `--force` flag to re-index an already-vectorized session.
  - Stores all sessions in a single ChromaDB collection (`dnd_sessions`) with `session_name`, `start_time`, `end_time`, and `speakers` metadata for cross-session queries.
- `py-process/vectordb_helper.py` — CLI utility for inspecting, searching, and managing the ChromaDB vector database.
  - `--list-sessions` — list all session names stored in the DB.
  - `--session <name>` — filter summary or search results to a single session.
  - `--search "<query>"` — semantic search via Ollama embeddings (requires Ollama running).
  - `--limit <n>` — control the number of search results returned (default: 5).
  - `--delete-session <name>` — remove all chunks for a specific session (prompts for confirmation).
  - `--clear-all` — wipe the entire collection (prompts for confirmation).
- `config.yaml` — new vector DB and RAG settings: `vector_db_directory`, `embedding_model`, `chunk_minutes`, `ollama_base_url`.
- `py-process/requirements.txt` — added `chromadb` and `requests` dependencies for vectorization support.
- `py-process/process.py` updated — orchestrator now runs all three steps in order: transcribe → merge audio → vectorize. Previously only ran transcription.
- `/mlx-ai merge-audio start <session_name>` slash command — triggers `merge_audio.py` from Discord.
- `/mlx-ai merge-audio status` slash command — reports whether an audio merge is in progress.
- `/mlx-ai vectorize start <session_name|"all"> [force]` slash command — triggers `vectorize.py` from Discord. Pass `"all"` to process every session; `force` re-indexes already-vectorized sessions.
- `/mlx-ai vectorize status` slash command — reports whether vectorization is in progress.
- `js-bot/postProcessor.js` — added `mergeAudio`, `mergeAudioStatus`, `vectorize`, and `vectorizeStatus` methods. Each operation tracks its own running state independently so recording, merging, and vectorizing can be monitored separately.
- Updated `README.md`, `js-bot/README.md`, and `py-process/README.md` with full documentation for all new scripts and slash commands.

### Moved

- Archived the original Python bot (`bot.py`, `cogs/`, `Dockerfile`, `.dockerignore`, `requirements.txt`) into `py-bot_old/` for historical reference. The Python bot is non-functional due to Discord's DAVE E2EE requirement and has been fully replaced by the JS bot.

### Changed

- Upgraded `@discordjs/voice` from ^0.18.0 to ^0.19.2 to gain DAVE (Discord Audio & Video Encryption) E2EE support. Version 0.18.0 did not implement the DAVE protocol, causing the voice connection to loop between `signalling` and `connecting` states indefinitely and never reach `Ready`.

### Fixed

- Double-transcription bug in `py-process/transcribe.py`: each WAV file was being transcribed twice — once for the per-user `.txt` file and again when building the combined transcript. Results from the first pass are now cached in a dict and reused, halving Whisper processing time per session.
- Voice connection timeout ("Timed out waiting for voice connection to be ready") caused by two missing pieces:
  1. `@discordjs/voice` 0.18.0 had no DAVE protocol support — upgraded to 0.19.2 which bundles `@snazzah/davey` for the DAVE handshake.
  2. No encryption library was installed — added `sodium-native` so the voice connection can complete its crypto negotiation.
- Per-user WAV files now include silence padding for gaps when the user isn't speaking. Previously, Discord's voice activity detection caused silent periods to be dropped entirely, collapsing the timeline and making multi-user transcripts misaligned. A `SilencePadTransform` stream now tracks wall-clock time from session start and injects zero-filled PCM data for any gap longer than one frame (20ms).
