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

## `merge_audio.py` — Mix and Compress Session Audio

Overlays all per-user WAV files into a single combined recording, then exports
the mix as a compressed MP3.

### What it does

1. Loads every `<username>.wav` in the session directory.
2. **Mixes** them together (all users start at time zero — same as the recording start).
3. Exports the mixed audio as `_session_mix.wav` and `_session_mix.mp3`.
4. Optionally deletes the original WAV files (controlled by `keep_wav` in `config.yaml`).

### Usage

```bash
python merge_audio.py <session_name>
```

**Example:**

```bash
python merge_audio.py 20260330_033020_test
```

### Config options (`config.yaml`)

| Key | Default | Description |
|---|---|---|
| `mp3_bitrate` | `"128k"` | MP3 bitrate (e.g. `"64k"`, `"128k"`, `"192k"`, `"320k"`) |
| `keep_wav` | `true` | Keep original WAV files after MP3 conversion |

### Expected output

```
D:/mlx-ai-recordings/20260330_033020_test/
├── thenightangel17.wav          # kept if keep_wav: true
├── someotheruser.wav            # kept if keep_wav: true
├── _session_mix.wav             # NEW — all users combined
└── _session_mix.mp3             # NEW — compressed combined audio
```

> **Note:** `pydub` requires `ffmpeg` to be installed and on your `PATH` — the same prerequisite as Whisper.
