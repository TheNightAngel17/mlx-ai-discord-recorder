# MLX AI Discord Recorder

A Discord bot that records voice channel audio, transcribes it with [OpenAI Whisper](https://github.com/openai/whisper), and makes sessions searchable via a local RAG (Retrieval-Augmented Generation) pipeline — perfect for archiving D&D sessions.

---

## Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Quick Start](#quick-start)
- [Discord Developer Portal Setup](#discord-developer-portal-setup)
- [AI Pipeline](#ai-pipeline)
- [Project Structure](#project-structure)
- [Sub-Project Documentation](#sub-project-documentation)
- [License](#license)

---

## Features

- 🎙️ **Per-user WAV recording** — each participant gets their own clean audio file
- 📁 **Timestamped session folders** — `YYYYMMDD_HHMMSS_<session_name>`
- 🔴 **Mid-session join detection** — players who join late are recorded automatically
- ⏹️ **Auto-stop when channel empties** — no manual intervention needed
- 💬 **Text-channel announcements** — configurable channel for start/stop notifications
- 🎵 **Audio merge** — mix all per-user WAVs into a single combined MP3
- 📝 **Whisper transcription** — per-user and combined transcripts with timestamps
- 🧠 **Vector embeddings** — chunk and embed transcripts into ChromaDB
- 🔍 **RAG query** — ask natural-language questions about your sessions via Discord or CLI
- 🔌 **Multi-provider LLM support** — Ollama (local), OpenAI, and Anthropic

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Discord Server                              │
│   Voice Channel ──► js-bot/ (Node.js)                           │
│                       │  Records per-user WAV files              │
│                       │  Registers /mlx-ai slash commands        │
│                       ▼                                          │
│                   py-process/ (Python)                           │
│                       │  Transcribes (Whisper)                   │
│                       │  Merges audio (pydub/ffmpeg)             │
│                       │  Vectorizes (ChromaDB + Ollama)          │
│                       ▼                                          │
│                   py-query/ (Python)                             │
│                       │  RAG queries (embed → retrieve → chat)   │
│                       │  Supports Ollama, OpenAI, Anthropic      │
│                       ▼                                          │
│                   Discord / CLI answer                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

### Required Software

| Software | Version | Purpose | Install Guide |
|----------|---------|---------|---------------|
| **Node.js** | 18+ | Discord bot runtime | [nodejs.org](https://nodejs.org/) |
| **Python** | 3.11+ | Transcription, vectorization, RAG | [python.org](https://www.python.org/downloads/) |
| **ffmpeg** | Latest | Audio processing (Whisper + pydub) | See below |
| **Ollama** | Latest | Local LLM inference (optional — can use OpenAI/Anthropic instead) | [ollama.com](https://ollama.com/) |

### Installing ffmpeg

#### Bash (macOS / Linux)

```bash
# macOS
brew install ffmpeg

# Debian / Ubuntu
sudo apt update && sudo apt install ffmpeg

# Verify
ffmpeg -version
```

#### PowerShell (Windows)

```powershell
# Using winget (Windows 11 / Windows 10 with winget installed)
winget install --id Gyan.FFmpeg -e

# Or using Chocolatey
choco install ffmpeg

# Or download manually from https://ffmpeg.org/download.html and add to PATH

# Verify
ffmpeg -version
```

### Installing Ollama (for local LLM)

If using Ollama as your embedding/chat provider:

#### Bash

```bash
# macOS / Linux
curl -fsSL https://ollama.com/install.sh | sh

# Pull required models
ollama pull nomic-embed-text    # embedding model
ollama pull llama3.2            # chat model (or your preferred model)
```

#### PowerShell

```powershell
# Download and install from https://ollama.com/download

# Pull required models
ollama pull nomic-embed-text
ollama pull llama3.2
```

---

## Installation

### 1. Clone the Repository

#### Bash

```bash
git clone https://github.com/TheNightAngel17/mlx-ai-discord-recorder.git
cd mlx-ai-discord-recorder
```

#### PowerShell

```powershell
git clone https://github.com/TheNightAngel17/mlx-ai-discord-recorder.git
cd mlx-ai-discord-recorder
```

### 2. Set Up Environment Variables

#### Bash

```bash
cp .env.example .env
# Edit .env with your favorite editor:
nano .env
```

#### PowerShell

```powershell
Copy-Item .env.example .env
# Edit .env with your favorite editor:
notepad .env
```

Fill in the required values:

```dotenv
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here

# Optional (only if using cloud LLM providers)
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Review Configuration

Edit `config.yaml` to set your output directory, announcement channel, and LLM provider preferences. See [Configuration](#configuration) for details.

### 4. Install JS Bot Dependencies

#### Bash

```bash
cd js-bot
npm install
cd ..
```

#### PowerShell

```powershell
cd js-bot
npm install
cd ..
```

### 5. Install Python Dependencies

It's recommended to use a virtual environment:

#### Bash

```bash
python -m venv .venv
source .venv/bin/activate

pip install -r py-process/requirements.txt
pip install -r py-query/requirements.txt
```

#### PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r py-process/requirements.txt
pip install -r py-query/requirements.txt
```

> **GPU Acceleration (recommended):** If you have an NVIDIA GPU, install CUDA-enabled PyTorch for significantly faster Whisper transcription. See the [py-process README](./py-process/README.md#gpu-acceleration-recommended) for instructions.

---

## Configuration

### `.env` — Secrets (never commit this file)

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token from the Discord Developer Portal |
| `GUILD_ID` | ✅ | Numeric server ID where the bot operates |
| `OPENAI_API_KEY` | Only if using OpenAI provider | OpenAI API key |
| `ANTHROPIC_API_KEY` | Only if using Anthropic provider | Anthropic API key |

### `config.yaml` — Non-Secret Settings

| Key | Default | Description |
|-----|---------|-------------|
| `output_directory` | `./recordings` | Where session folders and audio files are written |
| `announce_channel` | `"bot-commands"` | Text channel for bot announcements |
| `mp3_bitrate` | `"128k"` | MP3 compression bitrate |
| `keep_wav` | `true` | Keep original per-user WAV files after MP3 export |
| `whisper_model` | `"base"` | Default Whisper model size (`tiny`, `base`, `small`, `medium`, `large`) |
| `whisper_language` | `"en"` | Default language code, or `"auto"` for auto-detection |
| `vector_db_directory` | `./vectordb` | Where ChromaDB persists its data |
| `chunk_minutes` | `3` | Time-window size (minutes) for chunking transcripts |
| `embedding_provider` | `ollama` | Embedding backend: `ollama` or `openai` |
| `embedding_model` | `nomic-embed-text` | Model name for the chosen embedding provider |
| `chat_provider` | `ollama` | Chat backend: `ollama`, `openai`, or `anthropic` |
| `chat_model` | `llama3.2` | Model name for the chosen chat provider |
| `ollama_base_url` | `http://localhost:11434` | Ollama API base URL |

> ⚠️ **Warning:** Changing `embedding_provider` or `embedding_model` after vectorizing sessions requires re-running `python py-process/vectorize.py --all --force`.

---

## Quick Start

After installation, start the bot:

#### Bash

```bash
cd js-bot
node bot.js
```

#### PowerShell

```powershell
cd js-bot
node bot.js
```

Then use Discord slash commands:

1. `/mlx-ai record start voice_channel:#DnD-Voice session_name:Campaign1_Session4` — Start recording
2. `/mlx-ai record stop` — Stop recording (or just leave the channel — it auto-stops)
3. `/mlx-ai post-process start session_name:20260330_143000_Campaign1_Session4` — Transcribe + merge + vectorize
4. `/mlx-ai query ask question:What happened when the party entered the cave?` — Ask questions about recorded sessions

---

## Discord Developer Portal Setup

### 1 — Create an Application

1. Go to <https://discord.com/developers/applications> and click **New Application**.
2. Give it a name (e.g. `MLX AI Recorder`) and click **Create**.

### 2 — Create a Bot

1. In the left sidebar, click **Bot**.
2. Click **Add Bot** → **Yes, do it!**
3. Under **Token**, click **Reset Token**, confirm, then copy the token into your `.env` file.

### 3 — Enable Privileged Gateway Intents

On the **Bot** page, scroll down to **Privileged Gateway Intents** and enable:

- ✅ **Server Members Intent**
- ✅ **Message Content Intent**

Click **Save Changes**.

### 4 — Invite the Bot to Your Server

1. In the left sidebar, click **OAuth2** → **URL Generator**.
2. Under **Scopes**, select: `bot`, `applications.commands`
3. Under **Bot Permissions**, select: `Connect`, `Speak`, `Use Voice Activity`, `Read Messages / View Channels`, `Send Messages`
4. Copy the generated URL, paste it into your browser, and invite the bot to your server.

### 5 — Get Your Guild (Server) ID

1. In Discord, open **User Settings → Advanced** and enable **Developer Mode**.
2. Right-click your server icon and select **Copy Server ID**.

---

## AI Pipeline

The full workflow from recording to searchable archive:

```
Record ──► Transcribe ──► Merge Audio ──► Vectorize ──► Query
  │            │               │               │           │
 WAVs        .txt files     MP3 mix      ChromaDB     LLM answer
```

| Step | Trigger | Tool |
|------|---------|------|
| **Record** | `/mlx-ai record start` | `js-bot/recorder.js` |
| **Transcribe** | `/mlx-ai post-process start` or `python py-process/transcribe.py` | OpenAI Whisper |
| **Merge Audio** | `/mlx-ai merge-audio start` or `python py-process/merge_audio.py` | pydub + ffmpeg |
| **Vectorize** | `/mlx-ai vectorize start` or `python py-process/vectorize.py` | ChromaDB + Ollama/OpenAI |
| **Query** | `/mlx-ai query ask` or `python py-query/query.py` | RAG (embed → retrieve → chat) |

The **post-process** command runs steps 2–4 automatically in sequence. Each step can also be run individually.

---

## Project Structure

```
mlx-ai-discord-recorder/
├── .env.example              # Template for secrets
├── config.yaml               # User-editable non-secret settings
├── CHANGELOG.md              # Project changelog
├── README.md                 # This file
│
├── js-bot/                   # Discord bot (Node.js)
│   ├── bot.js                # Entry point — slash commands, interaction routing
│   ├── recorder.js           # Voice recording logic (DAVE/E2EE support)
│   ├── postProcessor.js      # Spawns Python scripts for processing pipeline
│   ├── queryHandler.js       # Spawns py-query for RAG queries
│   ├── package.json          # Node.js dependencies
│   └── README.md             # JS bot documentation
│
├── py-process/               # Audio processing pipeline (Python)
│   ├── process.py            # Orchestrator — runs transcribe → merge → vectorize
│   ├── transcribe.py         # Whisper transcription (WAVs → .txt files)
│   ├── merge_audio.py        # Mix per-user WAVs → combined WAV + MP3
│   ├── vectorize.py          # Chunk + embed transcripts → ChromaDB
│   ├── vectordb_helper.py    # CLI tool to inspect/search/manage the vector DB
│   ├── requirements.txt      # Python dependencies
│   └── README.md             # Processing pipeline documentation
│
└── py-query/                 # RAG query service (Python)
    ├── query.py              # CLI entry point for RAG queries
    ├── rag.py                # Core RAG logic (embed → retrieve → generate)
    ├── providers.py          # LLM provider abstraction (Ollama, OpenAI, Anthropic)
    ├── requirements.txt      # Python dependencies
    └── README.md             # Query service documentation
```

---

## Sub-Project Documentation

Each sub-project has its own detailed README with command references, dependency details, and examples:

| Sub-Project | Description | Documentation |
|-------------|-------------|---------------|
| **js-bot/** | Discord bot — recording, slash commands, interaction routing | [js-bot/README.md](./js-bot/README.md) |
| **py-process/** | Audio processing — transcription, merging, vectorization | [py-process/README.md](./py-process/README.md) |
| **py-query/** | RAG query — ask questions about recorded sessions | [py-query/README.md](./py-query/README.md) |

---

## License

MIT
