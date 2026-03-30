# py-process — Whisper Transcription Pipeline

Transcribe per-user voice recordings produced by the JS bot using [OpenAI Whisper](https://github.com/openai/whisper).

## Overview

The `js-bot/` records per-user WAV files into the `output_directory` configured in `config.yaml`:

```
D:/mlx-ai-recordings/
└── 20260330_033020_test/        # session folder
    ├── thenightangel17.wav      # per-user recording
    └── someotheruser.wav
```

This tool reads those WAV files and produces:
- **Per-user transcripts** (`<username>.txt`) with timestamps
- **Combined transcript** (`_combined_transcript.txt`) with all users merged chronologically

## Setup

### 1. Install base dependencies

```bash
cd py-process
pip install -r requirements.txt
```

> **Note:** Whisper requires `ffmpeg` to be installed and on your PATH.

### 2. GPU acceleration (recommended)

By default, `pip install openai-whisper` pulls in **CPU-only PyTorch**. If you have an NVIDIA GPU, you should install the CUDA-enabled version for significantly faster transcription.

**Prerequisites:**
- An NVIDIA GPU (any modern GeForce/RTX/Quadro)
- Up-to-date NVIDIA GPU drivers ([download here](https://www.nvidia.com/Download/index.aspx))
- That's it — **no CUDA SDK/Toolkit install needed**. PyTorch bundles its own CUDA runtime.

**Install CUDA-enabled PyTorch:**

```bash
# Uninstall the CPU-only version first
pip uninstall torch torchaudio torchvision -y

# Install with CUDA 12.6 support (works with any CUDA driver ≥ 12.6)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

> For other CUDA versions or platforms, see the [PyTorch install matrix](https://pytorch.org/get-started/locally/).

**Verify GPU is detected:**

```bash
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0)}') if torch.cuda.is_available() else print('No GPU found')"
```

### CPU vs GPU performance

Whisper automatically uses the GPU when CUDA is available — no code changes needed.

| | CPU | GPU (RTX 2070 Super) |
|---|---|---|
| Model load | ~3s | ~0.8s |
| 7s clip (base model) | 1.9s | 1.0s |
| FP16 support | ❌ (FP32 fallback) | ✅ (2× memory efficiency) |

The speedup is more dramatic on longer recordings. For a multi-hour D&D session, GPU will be **significantly** faster.

## Usage

```bash
# Basic — uses 'base' model with auto language detection
python transcribe.py 20260330_033020_test

# Specify model size (tiny | base | small | medium | large)
python transcribe.py 20260330_033020_test --model medium

# Specify language (skips auto-detection, faster)
python transcribe.py 20260330_033020_test --model small --language en
```

## Model Sizes

| Model  | Parameters | Speed   | Accuracy | VRAM   |
|--------|-----------|---------|----------|--------|
| tiny   | 39M       | Fastest | Lower    | ~1 GB  |
| base   | 74M       | Fast    | Good     | ~1 GB  |
| small  | 244M      | Medium  | Better   | ~2 GB  |
| medium | 769M      | Slow    | Great    | ~5 GB  |
| large  | 1550M     | Slowest | Best     | ~10 GB |

## Output

After running `transcribe.py`, the session folder will contain:

```
D:/mlx-ai-recordings/20260330_033020_test/
├── thenightangel17.wav                # original recording
├── thenightangel17.txt                # per-user transcript
├── someotheruser.wav
├── someotheruser.txt
└── _combined_transcript.txt           # all users merged by timestamp
```

---

## `process.py` — Full Pipeline Orchestrator

Runs all post-processing steps in the correct order for a single session:

1. **Transcribe** — `transcribe.py` → per-user `.txt` files + `_combined_transcript.txt`
2. **Merge audio** — `merge_audio.py` → `_session_mix.wav` + `_session_mix.mp3`
3. **Vectorize** — `vectorize.py` → chunks embedded and stored in ChromaDB

This is the script the JS bot calls when you use `/mlx-ai post-process start`.

### Usage

```bash
# Run all steps with defaults from config.yaml
python process.py 20260330_033020_test

# Override the Whisper model
python process.py 20260330_033020_test --model medium

# Override model and language
python process.py 20260330_033020_test --model large --language en
```

If any step fails, the pipeline halts immediately and exits with a non-zero code.

---

---

## `merge_audio.py` — Mix and Compress Session Audio

Overlays all per-user WAV files into a single combined recording, then exports
the mix as both a WAV and a compressed MP3.

### What it does

1. Loads every `<username>.wav` in the session directory (skipping `_` prefixed files).
2. **Mixes** them together — all users start at time zero, matching the recording start.
3. Exports the combined audio as `_session_mix.wav` and `_session_mix.mp3`.
4. Optionally deletes the original per-user WAV files (controlled by `keep_wav` in `config.yaml`).

### Prerequisites

- `ffmpeg` on your `PATH` (same requirement as Whisper)
- `pydub` — included in `requirements.txt`

### Config options (`config.yaml`)

| Key | Default | Description |
|---|---|---|
| `mp3_bitrate` | `"128k"` | MP3 bitrate (e.g. `"64k"`, `"128k"`, `"192k"`, `"320k"`) |
| `keep_wav` | `true` | Keep original per-user WAV files after MP3 conversion |

### Usage

```bash
python merge_audio.py <session_name>
```

**Example:**

```bash
python merge_audio.py 20260330_033020_test
```

### Expected output

```
D:/mlx-ai-recordings/20260330_033020_test/
├── thenightangel17.wav          # kept if keep_wav: true
├── someotheruser.wav            # kept if keep_wav: true
├── _session_mix.wav             # NEW — all users mixed together
└── _session_mix.mp3             # NEW — compressed combined audio
```

---

## `vectorize.py` — Embed Transcripts into ChromaDB

Chunks `_combined_transcript.txt` into time-window segments, embeds each chunk
via [Ollama](https://ollama.com/), and persists the embeddings in a local
[ChromaDB](https://www.trychroma.com/) vector database — ready for downstream
RAG queries.

### Prerequisites

1. **Ollama** must be running locally with the embedding model pulled:
   ```bash
   ollama pull nomic-embed-text
   ollama serve   # if not already running as a service
   ```

2. **Python dependencies** (included in `requirements.txt`):
   ```bash
   pip install -r requirements.txt
   ```

3. A completed transcription run — `_combined_transcript.txt` must exist in the session folder.

### Config options (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `vector_db_directory` | `D:/mlx-ai-vectordb` | Directory where ChromaDB persists its data |
| `embedding_model` | `nomic-embed-text` | Ollama embedding model name |
| `chunk_minutes` | `3` | Time-window size (minutes) for grouping transcript lines into chunks |
| `ollama_base_url` | `http://localhost:11434` | Base URL of your Ollama server |

### Usage

```bash
# Vectorize a single session
python vectorize.py 20260330_033020_test

# Re-index a session (overwrites existing vectors)
python vectorize.py 20260330_033020_test --force

# Vectorize all sessions in the output directory
python vectorize.py --all

# Re-index all sessions
python vectorize.py --all --force
```

### Example output

```
Vector DB: D:/mlx-ai-vectordb
Embedding: nomic-embed-text via http://localhost:11434
Chunk size: 3.0 minutes
Sessions:  1

Session: 20260330_033020_test
  Transcript: 42 lines -> 5 chunks (3.0-min windows)
  Embedding chunk 1/5... done (0.3s)
  ...
  Stored 5 chunks in ChromaDB collection 'dnd_sessions'.

Done.
```

---

## `vectordb_helper.py` — Inspect and Manage the Vector Database

A CLI tool for inspecting, searching, and managing the ChromaDB collection.
Useful for verifying what has been vectorized, doing quick semantic searches,
and cleaning up test data.

### Usage

```bash
# Summary of all sessions (chunks, time span, speakers)
python vectordb_helper.py

# Summary for a single session
python vectordb_helper.py --session 20260330_033020_test

# List session names only
python vectordb_helper.py --list-sessions

# Semantic search across all sessions (requires Ollama running)
python vectordb_helper.py --search "what happened at the cave entrance"

# Semantic search within a specific session
python vectordb_helper.py --search "who found the treasure" --session 20260330_033020_test

# Return more results
python vectordb_helper.py --search "dragon attack" --limit 10

# Delete all chunks for a single session (prompts for confirmation)
python vectordb_helper.py --delete-session 20260330_033020_test

# Wipe the entire collection (prompts for confirmation)
python vectordb_helper.py --clear-all
```

### Example summary output

```
Vector DB : D:/mlx-ai-vectordb
Collection: dnd_sessions

Total chunks in DB: 5
Sessions stored: 1

  Session : 20260330_033020_test
  Chunks  : 5
  Span    : 00:00:00.00 → 00:14:32.80
  Speakers: playerone, thenightangel17
```

### Example search output

```
Embedding query via Ollama (nomic-embed-text)...

Top 3 result(s) for: "cave entrance"

============================================================
[1] 20260330_033020_test__chunk_0002
    Session : 20260330_033020_test
    Time    : 00:06:00.00 → 00:09:00.00
    Speakers: thenightangel17, playerone
    Distance: 142.3401
    Text    :
      thenightangel17: We approach the cave entrance carefully.
      playerone: I cast detect magic on the doorway.
```

> **Note:** Lower `Distance` values indicate more relevant results. The raw distance is an L2 (Euclidean) distance from ChromaDB — not a percentage score.
