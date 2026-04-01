# py-process — Audio Processing Pipeline

The Python audio processing pipeline that handles transcription, audio merging, and vector embedding for recorded Discord sessions.

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
  - [vectordb\_helper.py — Vector DB Management](#vectordb_helperpy--vector-db-management)
- [Configuration Reference](#configuration-reference)
- [Whisper Model Sizes](#whisper-model-sizes)
- [Output Structure](#output-structure)
- [Dependencies](#dependencies)

---

## Overview

The `js-bot/` records per-user WAV files into timestamped session folders:

```
recordings/
└── 20260330_143000_Campaign1_Session4/
    ├── thenightangel17.wav       # per-user recording
    └── playerone.wav
```

This pipeline processes those recordings through three stages:

1. **Transcribe** — Whisper speech-to-text → per-user `.txt` files + merged `_combined_transcript.txt`
2. **Merge Audio** — Overlay all per-user WAVs → `_session_mix.wav` + `_session_mix.mp3`
3. **Vectorize** — Chunk and embed the combined transcript → ChromaDB for RAG queries

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

Runs all three processing steps in order for a single session. This is the script the JS bot calls when you press **⚙️ Post-Process** in the `/mlx-ai session` control panel.

If any step fails, the pipeline halts immediately.

#### Usage

```bash
# Run all steps with defaults from config.yaml
python process.py <session_name>

# Override Whisper model
python process.py <session_name> --model medium

# Override model and language
python process.py <session_name> --model large --language en
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

---

### `transcribe.py` — Whisper Transcription

Transcribes each per-user WAV file using [OpenAI Whisper](https://github.com/openai/whisper), then merges all segments into a chronological combined transcript.

#### Output Files

- `<username>.txt` — Per-user transcript with `[HH:MM:SS.mmm --> HH:MM:SS.mmm]` timestamps
- `_combined_transcript.txt` — All users merged and sorted by timestamp

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

Overlays all per-user WAV files into a single combined recording, then exports as both WAV and compressed MP3.

#### How It Works

1. Loads every `<username>.wav` in the session directory (skips `_`-prefixed files)
2. Mixes them together — all users start at time zero, matching the recording start
3. Exports `_session_mix.wav` and `_session_mix.mp3`
4. Optionally deletes original per-user WAVs (controlled by `keep_wav` in `config.yaml`)

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
| `keep_wav` | `true` | Keep original per-user WAV files after export |

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
| `vector_db_directory` | `./vectordb` | ChromaDB persistence directory |
| `embedding_model` | `nomic-embed-text` | Embedding model name |
| `chunk_minutes` | `3` | Time-window for transcript chunking (minutes) |
| `ollama_base_url` | `http://localhost:11434` | Ollama API URL |

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
    ├── thenightangel17.wav              # Per-user recording (kept if keep_wav: true)
    ├── playerone.wav
    ├── thenightangel17.txt              # Per-user Whisper transcript
    ├── playerone.txt
    ├── _combined_transcript.txt         # All users merged chronologically
    ├── _session_mix.wav                 # Combined WAV (all users mixed)
    └── _session_mix.mp3                 # Compressed combined audio
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
