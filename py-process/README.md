# py-process — Audio Processing Pipeline

The Python audio processing pipeline that handles transcription, audio merging, vector embedding, and session summarization for recorded Discord sessions.

> 🐳 **Docker:** this runs as the `py-process` service — a FastAPI job runner (`app.py`) the bot triggers over HTTP, wrapping the same CLIs documented below. See [DOCKER.md](../DOCKER.md).

> **↩️ Back to [main README](../README.md)** | See also: [js-bot](../js-bot/README.md) · [py-query](../py-query/README.md)

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [GPU Acceleration (Recommended)](#gpu-acceleration-recommended)
- [Scripts](#scripts)
  - [process.py — Full Pipeline Orchestrator](#processpy--full-pipeline-orchestrator)
  - [transcribe.py — Whisper Transcription](#transcribepy--whisper-transcription)
  - [merge\_audio.py — Audio Mixing & Compression](#merge_audiopy--audio-mixing--compression)
  - [vectorize.py — Transcript Vectorization](#vectorizepy--transcript-vectorization)
  - [summarize.py — Session Summarization](#summarizepy--session-summarization)
  - [vectordb\_helper.py — Vector DB Management](#vectordb_helperpy--vector-db-management)
- [Configuration Reference](#configuration-reference)
- [Whisper Model Sizes](#whisper-model-sizes)
- [Output Structure](#output-structure)
- [Dependencies](#dependencies)

---

## Overview

The `js-bot/` records each participant's speech as discrete **utterance snippets**
in a per-user sub-folder of a timestamped session folder:

```
recordings/
└── 20260330_143000_Campaign1_Session4/
    ├── _session.metadata.json           # { session_start_ms, session_name }
    ├── thenightangel17/                  # one sub-folder per participant
    │   ├── 0000000000.wav                # filename = session-relative start offset (ms)
    │   ├── 0000012840.wav                # => 12.84s into the session
    │   └── ...
    └── playerone/
        ├── 0000003120.wav
        └── ...
```

Each snippet covers a single utterance (the recorder closes a snippet after a
short silence gap), and the filename is its zero-padded session-relative start
offset in milliseconds. Because the offset is encoded in the filename,
`transcribe.py` reconstructs session-relative timestamps exactly by adding the
offset to Whisper's per-snippet timing — no silence padding and no drift
correction needed. `_session.metadata.json` records the absolute session start
time for reference.

This pipeline processes those recordings through four stages:

1. **Transcribe** — assemble per-user `.txt` files + merged `_combined_transcript.txt` from each snippet. With `auto_transcribe` on, snippets were already transcribed live by [py-transcribe](../py-transcribe/README.md) (sidecars on disk); this step just drains the queue and assembles. Otherwise it batch-transcribes with Whisper.
2. **Merge Audio** — Place snippets at their offsets and overlay all users → `_session_mix.wav` + `_session_mix.mp3`
3. **Vectorize** — Chunk and embed the combined transcript → the session's category ChromaDB collection for RAG queries
4. **Summarize** — LLM-generated Markdown summary (format driven by the session's category prompt) → `_session_summary.md` (and a ChromaDB summary chunk)

---

## Prerequisites

- **Python 3.11+** — [Download](https://www.python.org/downloads/)
- **ffmpeg** — Required by both Whisper and pydub. See the [main README](../README.md#installing-ffmpeg) for installation.
- **Ollama** — Required for vectorization (embedding). See the [main README](../README.md#installing-ollama-for-local-llm) for installation.

---

## Installation

### Bash

```bash
cd py-process
pip install -r requirements.txt
```

### PowerShell

```powershell
cd py-process
pip install -r requirements.txt
```

> **Note:** If using a virtual environment from the repo root, ensure it's activated first. See the [main README](../README.md#5-install-python-dependencies).

---

## GPU Acceleration (Recommended)

By default, `pip install openai-whisper` installs CPU-only PyTorch. If you have an NVIDIA GPU, install CUDA-enabled PyTorch for significantly faster transcription.

**Requirements:** An NVIDIA GPU with up-to-date drivers ([download](https://www.nvidia.com/Download/index.aspx)). No separate CUDA SDK install needed — PyTorch bundles its own CUDA runtime.

### Bash

```bash
# Remove CPU-only PyTorch
pip uninstall torch torchaudio torchvision -y

# Install CUDA 12.6 build
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

### PowerShell

```powershell
# Remove CPU-only PyTorch
pip uninstall torch torchaudio torchvision -y

# Install CUDA 12.6 build
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

### Verify GPU

```bash
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0)}') if torch.cuda.is_available() else print('CPU only')"
```

### Performance Comparison

| | CPU | GPU (RTX 2070 Super) |
|---|---|---|
| Model load | ~3s | ~0.8s |
| 7s clip (base) | 1.9s | 1.0s |
| FP16 | ❌ (FP32 fallback) | ✅ (2× memory efficiency) |

> GPU acceleration is dramatically more impactful on longer recordings. Multi-hour D&D sessions benefit significantly.

---

## Scripts

### `process.py` — Full Pipeline Orchestrator

Runs all four processing steps in order for a single session. This is the script the JS bot calls when you press **⚙️ Post-Process** in the `/mlx-ai session` control panel — and, with the re-run flags below, when you run `/mlx-ai re-post-process`.

If any step fails, the pipeline halts immediately. Step 4 (summarize) is controlled by the **☑ Generate Summary** toggle in the session panel, which passes `--summarize` or `--no-summarize` to this script. You can also pass `--no-summarize` on the command line to skip it.

**Re-processing an existing session.** Transcription is sidecar-driven (see [`transcribe.py`](#transcribepy--transcript-assembly-sidecar-aware)) and vectorization skips sessions already in the DB, so a plain re-run reuses the old transcript and won't re-index. Two flags make a true re-run possible:

- `--retranscribe` — clear the per-snippet `<offset_ms>.json` sidecars and rebuild the transcript from audio. Re-transcription runs through the warm-model [py-transcribe](../py-transcribe/README.md) service (the lean Docker image of this service has no Whisper); if that service is unreachable, transcribe.py's local Whisper batch fallback handles it. Needs the original snippet WAVs (only present when `keep_wav: true`).
- `--force-vectorize` — pass `--force` to `vectorize.py` so the already-indexed session is re-indexed instead of skipped.

#### Usage

```bash
# Run all steps with defaults from config.yaml
python process.py <session_name>

# Override Whisper model
python process.py <session_name> --model medium

# Override model and language
python process.py <session_name> --model large --language en

# Skip summary generation
python process.py <session_name> --no-summarize

# Re-process an existing session: rebuild the transcript from audio and re-index
python process.py <session_name> --retranscribe --force-vectorize
```

#### Terminal Example

```bash
python process.py 20260330_143000_Campaign1_Session4 --model medium --language en
```

#### Discord Example

```
# Triggered automatically via the ⚙️ Post-Process button in /mlx-ai session
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `session` | ✅ | — | Session folder name |
| `--model` | ❌ | From `config.yaml` | Whisper model: `tiny`, `base`, `small`, `medium`, `large` |
| `--language` | ❌ | From `config.yaml` | Language code (e.g. `en`). Omit for auto-detect. |
| `--no-summarize` | ❌ | Summarize is on by default | Pass to skip Step 4 (session summary generation) |
| `--retranscribe` | ❌ | off | Clear sidecars and rebuild the transcript from audio before assembling (needs the snippet WAVs) |
| `--force-vectorize` | ❌ | off | Re-index a session already present in the vector DB (passes `--force` to `vectorize.py`) |

---

### `transcribe.py` — Transcript Assembly (sidecar-aware)

Assembles the session transcripts from each participant's utterance snippets,
adding each snippet's filename offset so timestamps are session-relative, then
merges all segments into a chronological combined transcript.

Two sources of per-snippet text:

- **Sidecars (fast path):** if the live transcription service ([py-transcribe](../py-transcribe/README.md))
  already transcribed a snippet, a `<offset_ms>.json` sidecar sits next to the
  WAV. Those are loaded directly. **When every snippet has a sidecar, Whisper is
  never loaded** and assembly is near-instant.
- **Batch fallback:** snippets without a sidecar are transcribed here with
  [OpenAI Whisper](https://github.com/openai/whisper) (loaded once). Clips shorter
  than `transcribe_min_ms` are skipped as non-speech blips.

#### Output Files

- `<username>.txt` — Per-user transcript with `[HH:MM:SS.mmm --> HH:MM:SS.mmm]` session-relative timestamps; includes a `# First spoke at:` header line
- `_combined_transcript.txt` — All users merged and sorted chronologically. Timestamps are session-relative by construction (each snippet's start offset comes from its filename), so no drift correction is needed; a `*** joined the session ***` marker records when each participant first spoke

#### Usage

```bash
# Basic (uses defaults from config.yaml)
python transcribe.py <session_name>

# Specify model size
python transcribe.py <session_name> --model medium

# Specify model and language (skips auto-detection)
python transcribe.py <session_name> --model small --language en
```

#### Terminal Example

```bash
python transcribe.py 20260330_143000_Campaign1_Session4 --model base --language en
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `session` | ✅ | — | Session folder name |
| `--model` | ❌ | From `config.yaml` (`base`) | Whisper model size |
| `--language` | ❌ | From `config.yaml` (`en`) | Language code or auto-detect |

---

### `merge_audio.py` — Audio Mixing & Compression

Reconstructs the session timeline from the per-user utterance snippets into a single combined recording, then exports as both WAV and compressed MP3.

#### How It Works

1. Walks each participant's sub-folder and reads every snippet's start offset from its filename (skips `_`/`.`-prefixed entries)
2. Rebuilds one full-length track per user by placing each snippet at its offset on a silent canvas (a user's own utterances never overlap), then overlays all user tracks — preserving silence where someone wasn't talking and overlap where people talked over each other
3. Exports `_session_mix.mp3` (always) and `_session_mix.wav` (only when `keep_mix_wav: true`)
4. Optionally deletes the snippet WAVs and their now-empty sub-folders (controlled by `keep_wav` in `config.yaml`), and removes any user sub-folder left empty (e.g. by live empty-snippet pruning)

#### Usage

```bash
python merge_audio.py <session_name>
```

#### Terminal Example

```bash
python merge_audio.py 20260330_143000_Campaign1_Session4
```

#### Discord Example

```
# Triggered automatically as part of ⚙️ Post-Process in /mlx-ai session
```

| Argument | Required | Description |
|----------|----------|-------------|
| `session` | ✅ | Session folder name |

#### Config Options (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `mp3_bitrate` | `"128k"` | MP3 bitrate (e.g. `"64k"`, `"128k"`, `"192k"`, `"320k"`) |
| `keep_wav` | `true` | Keep the per-user snippet WAVs (and sub-folders) after the mix is exported |
| `keep_mix_wav` | `false` | Keep the uncompressed `_session_mix.wav` next to the MP3. It's large and nothing downstream reads it; the MP3 is always exported |

---

### `vectorize.py` — Transcript Vectorization

Chunks `_combined_transcript.txt` into time-window segments, embeds each chunk via the configured embedding provider, and stores them in a local [ChromaDB](https://www.trychroma.com/) vector database for RAG queries.

#### Prerequisites

1. **Ollama** running with the embedding model pulled:
   ```bash
   ollama pull nomic-embed-text
   ollama serve   # if not running as a service
   ```
2. A completed transcription — `_combined_transcript.txt` must exist in the session folder.

#### Usage

```bash
# Vectorize a single session
python vectorize.py <session_name>

# Re-index (overwrite existing vectors)
python vectorize.py <session_name> --force

# Vectorize all sessions
python vectorize.py --all

# Re-index all sessions
python vectorize.py --all --force
```

#### Terminal Example

```bash
python vectorize.py 20260330_143000_Campaign1_Session4 --force
```

#### Discord Example

```
# Triggered automatically as part of ⚙️ Post-Process in /mlx-ai session
```

| Argument | Required | Description |
|----------|----------|-------------|
| `session` | ✅ (or `--all`) | Session folder name |
| `--all` | ✅ (or `session`) | Process every session folder |
| `--force` | ❌ | Re-index already-vectorized sessions |

#### Config Options (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `vector_db_directory` | `./vectordb` | Where ChromaDB persists data |
| `embedding_model` | `nomic-embed-text` | Ollama embedding model name |
| `chunk_minutes` | `3` | Time-window size (minutes) for grouping transcript lines |
| `ollama_base_url` | `http://localhost:11434` | Ollama API base URL |

---

### `summarize.py` — Session Summarization

Reads `_combined_transcript.txt` for a session, looks up the session's **category** (from `_session.metadata.json`), loads that category's summary prompt (`categories/<name>.md`), and sends the transcript to the configured chat LLM. The category prompt fully controls the output, which the model returns as finished Markdown.

Output is written to the session folder:
- `_session_summary.md` — The model's Markdown summary (posted to the Discord announce channel)

The summary text is also stored as a single ChromaDB chunk (`type=summary`) in the category's collection, improving cross-session RAG query quality. Sessions with no category in their metadata fall back to the built-in `dnd` category.

#### Prerequisites

1. The configured **chat model** must be running (Ollama) or have a valid API key (OpenAI/Anthropic). See the [LLM Provider Settings](#configuration-reference) section below.
2. The configured **embedding model** must be running (Ollama) or have a valid API key (OpenAI). Used to embed the summary chunks into ChromaDB.
3. A completed transcription — `_combined_transcript.txt` must exist in the session folder.

#### Usage

```bash
# Summarize a single session
python summarize.py <session_name>

# Summarize all sessions
python summarize.py --all
```

#### Terminal Example

```bash
python summarize.py 20260330_143000_Campaign1_Session4
```

#### Example Output

```
Session: 20260330_143000_Campaign1_Session4
  Category: dnd (collection: dnd_sessions)
  Transcript: 42381 chars, 3 speaker(s)
  Sending transcript to ollama (llama3.2) (single pass)… done (8.4s)
  Written: _session_summary.md
  Embedding summary… done (1.2s)
  Stored summary in ChromaDB collection 'dnd_sessions'.
```

#### Key Moment Categories

| Category | Emoji | Description |
|----------|-------|-------------|
| `combat` | ⚔️ | Battle encounters and fight sequences |
| `plot_reveal` | 🔮 | Story revelations and plot twists |
| `npc_introduction` | 🧙 | Introduction of a new NPC |
| `funny_moment` | 😄 | Memorable humorous events |
| `decision_point` | 🎲 | Significant choices made by the party |

#### Config Options (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `auto_summarize` | `true` | Default state of the **☑ Generate Summary** toggle in the `/mlx-ai session` panel |
| `summary_max_tokens` | `2000` | Approximate token budget for the LLM summary output |
| `summary_max_input_tokens` | `32000` | Max input tokens per summarization chunk; transcripts over this limit use map-reduce |
| `chat_provider` | `ollama` | LLM backend for summarization: `ollama`, `openai`, or `anthropic` |
| `chat_model` | `llama3.2` | Chat model name |

---

### `vectordb_helper.py` — Vector DB Management

CLI tool for inspecting, searching, and managing the ChromaDB collection. Useful for verifying what has been vectorized, running quick semantic searches, and cleaning up data.

#### Usage

```bash
# Summary of all sessions
python vectordb_helper.py

# Summary for one session
python vectordb_helper.py --session <session_name>

# List session names only
python vectordb_helper.py --list-sessions

# Semantic search (requires Ollama running)
python vectordb_helper.py --search "what happened at the cave entrance"

# Search within a specific session
python vectordb_helper.py --search "who found the treasure" --session <session_name>

# Return more results
python vectordb_helper.py --search "dragon attack" --limit 10

# Delete one session's chunks (prompts for confirmation)
python vectordb_helper.py --delete-session <session_name>

# Wipe the entire collection (prompts for confirmation)
python vectordb_helper.py --clear-all
```

| Argument | Description |
|----------|-------------|
| `--session <name>` | Filter summary or search to a single session |
| `--search "<query>"` | Semantic search (requires Ollama) |
| `--limit <n>` | Max search results (default: 5) |
| `--list-sessions` | List all session names |
| `--delete-session <name>` | Remove all chunks for a session |
| `--clear-all` | Wipe the entire collection |

#### Example Output — Summary

```
Vector DB : D:/mlx-ai-vectordb
Category: dnd
Collection: dnd_sessions

Total chunks in DB: 5
Sessions stored: 1

  Session : 20260330_143000_Campaign1_Session4
  Chunks  : 5
  Span    : 00:00:00.00 → 00:14:32.80
  Speakers: playerone, thenightangel17
```

#### Example Output — Search

```
Top 3 result(s) for: "cave entrance"

============================================================
[1] 20260330_143000_test__chunk_0002
    Session : 20260330_143000_test
    Time    : 00:06:00.00 → 00:09:00.00
    Speakers: thenightangel17, playerone
    Distance: 142.3401
    Text    :
      thenightangel17: We approach the cave entrance carefully.
      playerone: I cast detect magic on the doorway.
```

> **Note:** Lower `Distance` values indicate more relevant results. The raw distance is an L2 (Euclidean) distance from ChromaDB.

---

## Configuration Reference

All configuration is in the root `config.yaml`. The fields relevant to `py-process` are:

| Key | Default | Description |
|-----|---------|-------------|
| `output_directory` | `./recordings` | Where session folders are read from and written to |
| `whisper_model` | `"base"` | Default Whisper model size |
| `whisper_language` | `"en"` | Default language (set to `"auto"` for auto-detection) |
| `mp3_bitrate` | `"128k"` | MP3 compression bitrate |
| `keep_wav` | `true` | Keep original WAV files after MP3 export |
| `keep_mix_wav` | `false` | Keep the uncompressed `_session_mix.wav` (MP3 is always exported) |
| `vector_db_directory` | `./vectordb` | ChromaDB persistence directory |
| `embedding_provider` | `ollama` | Embedding backend: `ollama`, `openai`, or `voyage` |
| `embedding_model` | `nomic-embed-text` | Embedding model name |
| `chunk_minutes` | `3` | Time-window for transcript chunking (minutes) |
| `chat_provider` | `ollama` | Chat LLM backend: `ollama`, `openai`, or `anthropic` |
| `chat_model` | `llama3.2` | Chat model name (used by `summarize.py`) |
| `ollama_base_url` | `http://localhost:11434` | Ollama API URL |
| `auto_summarize` | `true` | Default state of the **☑ Generate Summary** toggle in the `/mlx-ai session` panel |
| `summary_max_tokens` | `2000` | Approximate token budget for the LLM when generating session summaries |
| `summary_max_input_tokens` | `32000` | Max input tokens per summarization chunk; transcripts over this limit use map-reduce |

---

## Whisper Model Sizes

| Model | Parameters | Speed | Accuracy | VRAM |
|-------|-----------|-------|----------|------|
| `tiny` | 39M | Fastest | Lower | ~1 GB |
| `base` | 74M | Fast | Good | ~1 GB |
| `small` | 244M | Medium | Better | ~2 GB |
| `medium` | 769M | Slow | Great | ~5 GB |
| `large` | 1550M | Slowest | Best | ~10 GB |

---

## Output Structure

After a full pipeline run, the session folder contains:

```
recordings/
└── 20260330_143000_Campaign1_Session4/
    ├── _session.metadata.json          # { session_start_ms, session_name }
    ├── thenightangel17/                 # Per-user snippet sub-folder (kept if keep_wav: true)
    │   ├── 0000000000.wav               #   utterance snippet, name = start offset (ms)
    │   ├── 0000000000.json              #   live-transcription sidecar (when auto_transcribe is on)
    │   └── 0000012840.wav
    ├── playerone/
    │   └── 0000003120.wav
    ├── thenightangel17.txt              # Per-user Whisper transcript
    ├── playerone.txt
    ├── _combined_transcript.txt         # All users merged chronologically
    ├── _session_mix.wav                 # Combined WAV (only when keep_mix_wav: true)
    ├── _session_mix.mp3                 # Compressed combined audio (always exported)
    └── _session_summary.md              # Markdown summary (format set by the session's category prompt)
```

Vector embeddings are stored separately in `vector_db_directory` (default: `./vectordb`).

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| [`PyYAML`](https://pypi.org/project/PyYAML/) | ≥6.0 | Parse `config.yaml` configuration file |
| [`openai-whisper`](https://pypi.org/project/openai-whisper/) | ≥20231117 | OpenAI Whisper for speech-to-text transcription |
| [`pydub`](https://pypi.org/project/pydub/) | ≥0.25.1 | Audio mixing and MP3 compression (requires ffmpeg) |
| [`chromadb`](https://pypi.org/project/chromadb/) | ≥0.5.0 | Local persistent vector database for transcript embeddings |
| [`requests`](https://pypi.org/project/requests/) | ≥2.31.0 | HTTP client for Ollama embedding API calls |
