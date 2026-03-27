# MLX AI Discord Recorder

A Discord bot that joins a voice channel, records the session as a single mixed audio file, and automatically transcribes it with [WhisperX](https://github.com/m-bain/whisperX) — producing per-speaker transcripts with word-level timestamps. Perfect for archiving D&D sessions and feeding them into a self-hosted RAG pipeline.

---

## Pipeline

```
/mlx-ai record start
        │
        ▼
Bot joins voice channel
Records all audio → mixed.wav
Saves participants.json
        │
/mlx-ai record stop
        │
        ▼
WhisperX transcription (background)
  ├── Alignment (word-level timestamps)
  └── Diarization (speaker labels via pyannote)
        │
        ▼
Output per session directory:
  mixed.wav          — raw mixed audio
  participants.json  — Discord members present at session start
  transcript.json    — full WhisperX output (word-level timestamps + speaker labels)
  speaker_map.json   — SPEAKER_00 → Discord display name mapping
  transcript.txt     — human-readable per-speaker transcript
```

---

## Features

- 🎙️ **Mixed audio recording** — all participants captured in a single `mixed.wav`
- 🗣️ **Automatic transcription** — WhisperX runs in the background after recording stops
- 👥 **Speaker diarization** — pyannote.audio labels each segment with a speaker ID
- 🏷️ **Discord username mapping** — speaker labels mapped to Discord display names
- 📁 **Timestamped session folders** — `yyyyMMdd_HHmmss_<session_name>/`
- ⏹️ **Auto-stop when channel empties** — no manual intervention needed
- 💬 **Text-channel announcements** — configurable channel for start/stop/transcription events
- 🐳 **Docker-ready** — pre-wired for future Kubernetes deployment

---

## Prerequisites

- **Python 3.11+**
- **ffmpeg** — required by py-cord for voice audio processing

### Installing ffmpeg

| Platform | Command |
|----------|---------|
| macOS    | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows  | Download from <https://ffmpeg.org/download.html> and add to PATH |

---

## HuggingFace Token (required for speaker diarization)

WhisperX uses [pyannote.audio](https://github.com/pyannote/pyannote-audio) for speaker diarization, which requires a HuggingFace token and acceptance of the model licence.

1. Create a free account at <https://huggingface.co>
2. Go to <https://huggingface.co/settings/tokens> and create a **read** token
3. Accept the licence at <https://huggingface.co/pyannote/speaker-diarization-3.1>
4. Add the token to your `.env` file as `HF_TOKEN=...` (or `huggingface_token` in `config.yaml`)

> **Note:** If no token is provided the bot will still record and transcribe, but speaker diarization will be skipped — all segments will be labelled `UNKNOWN`.

---

## Discord Developer Portal Setup

### 1 — Create an Application

1. Go to <https://discord.com/developers/applications> and click **New Application**.
2. Give it a name (e.g. `MLX AI Recorder`) and click **Create**.

### 2 — Create a Bot

1. In the left sidebar, click **Bot**.
2. Click **Add Bot** → **Yes, do it!**
3. Under **Token**, click **Reset Token**, confirm, then copy the token — you will need it for your `.env` file.

### 3 — Enable Privileged Gateway Intents

On the **Bot** page, scroll down to **Privileged Gateway Intents** and enable:

- ✅ **Server Members Intent**
- ✅ **Message Content Intent**

Click **Save Changes**.

### 4 — Invite the Bot to Your Server

1. In the left sidebar, click **OAuth2** → **URL Generator**.
2. Under **Scopes**, select:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, select:
   - `Connect`
   - `Speak`
   - `Use Voice Activity`
   - `Read Messages / View Channels`
   - `Send Messages`
4. Copy the generated URL, paste it into your browser, and invite the bot to your server.

### 5 — Get Your Guild (Server) ID

1. In Discord, open **User Settings → Advanced** and enable **Developer Mode**.
2. Right-click your server icon in the left sidebar and select **Copy Server ID**.

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/TheNightAngel17/mlx-ai-discord-recorder.git
cd mlx-ai-discord-recorder

# 2. Copy the environment template and fill in your values
cp .env.example .env
# Edit .env — add your DISCORD_TOKEN, GUILD_ID, and HF_TOKEN

# 3. Review and adjust config.yaml (output directory, announcement channel,
#    whisper model size)
# nano config.yaml

# 4. Install Python dependencies
pip install -r requirements.txt
```

### WhisperX installation notes

WhisperX depends on PyTorch. The `requirements.txt` installs the CPU version by default. For GPU acceleration install the appropriate CUDA wheel first:

```bash
# Example for CUDA 12.1
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
```

---

## Configuration

### `.env` (secrets — never commit this file)

```dotenv
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_numeric_guild_id_here
HF_TOKEN=your_huggingface_token_here
```

### `config.yaml` (non-secret settings)

```yaml
output_directory: ./recordings       # Where session folders are written
announce_channel: "bot-commands"     # Text channel for bot announcements
huggingface_token: ""                # HuggingFace token (or set HF_TOKEN in .env)
whisper_model: "base"                # Whisper model size: tiny, base, small, medium, large
```

---

## Running Locally

```bash
python bot.py
```

The bot logs to stdout. On first startup it syncs slash commands to your configured guild (takes effect immediately).

---

## Running with Docker

```bash
# Build the image
docker build -t mlx-ai-discord-recorder .

# Run the container, injecting secrets via --env-file
docker run --rm \
  --env-file .env \
  -v "$(pwd)/recordings:/app/recordings" \
  mlx-ai-discord-recorder
```

> **Note:** The `-v` flag mounts a local `recordings/` directory into the container so audio files are written to your host machine and persist across container restarts.

---

## Command Reference

All commands live under the `/mlx-ai` slash command group.

| Command | Parameters | Description |
|---------|-----------|-------------|
| `/mlx-ai record start` | `voice_channel` (required), `session_name` (required) | Join the voice channel and begin recording |
| `/mlx-ai record stop` | — | Stop the active recording and trigger WhisperX transcription |
| `/mlx-ai record status` | — | Show current session info including whether transcription is in progress |

### Parameter details

| Parameter | Type | Description |
|-----------|------|-------------|
| `voice_channel` | Channel (dropdown) | The voice channel to record; rendered as a dropdown filtered to voice channels |
| `session_name` | String | Free-text label appended to the timestamp folder, e.g. `Campaign1_Session4` |

All command responses are **ephemeral** (visible only to the user who ran the command).

---

## Text-Channel Announcements

The bot posts announcements to the channel configured in `config.yaml` (`announce_channel`):

| Event | Message |
|-------|---------|
| Recording starts | `🔴 Recording started in \`DnD-Voice\` — Session: \`20260327_143000_Campaign1_Session4\`` |
| User joins mid-session | `🎙️ Now recording \`PlayerTwo\` who joined mid-session` |
| Recording stops | `⏹️ Recording stopped — files saved to \`recordings/20260327_143000_Campaign1_Session4\`` |
| Transcription starts | `WhisperX is processing the recorded audio...` |
| Transcription complete | `Transcription complete — files saved to \`recordings/20260327_143000_Campaign1_Session4\`` |

---

## Output Structure

After a session, the output directory will look like this:

```
recordings/
└── 20260327_143000_Campaign1_Session4/
    ├── mixed.wav           — single mixed audio file of all participants
    ├── participants.json   — Discord members present at session start
    ├── transcript.json     — full WhisperX output with word-level timestamps
    ├── speaker_map.json    — SPEAKER_00 → Discord display name mapping
    └── transcript.txt      — human-readable per-speaker transcript
```

### Example `transcript.txt`

```
Speaker Map:
  SPEAKER_00 -> Mitchell
  SPEAKER_01 -> Alex

Transcript:
[SPEAKER_00 / Mitchell] 00:01:23 -> 00:01:31: "Let's roll for initiative."
[SPEAKER_01 / Alex]     00:01:32 -> 00:01:35: "I got a 17."
```

---

## Future: Kubernetes

A `Dockerfile` is included and ready to use. Kubernetes manifests (Deployment, PersistentVolumeClaim, Secret, ConfigMap) are planned as a follow-up to enable fully containerised, self-hosted deployment of the complete D&D transcription pipeline.

Key notes for K8s deployment:

- Run **exactly 1 replica** — Discord's gateway connection is stateful
- Mount a `PersistentVolumeClaim` to `/app/recordings` to retain audio files across pod restarts
- Store `DISCORD_TOKEN` and `HF_TOKEN` in Kubernetes `Secrets`, not `ConfigMaps`

---

## Project Structure

```
mlx-ai-discord-recorder/
├── bot.py                  # Entry point — loads config, initialises bot, loads cogs
├── cogs/
│   ├── __init__.py         # Makes cogs/ a proper Python package
│   └── recorder.py         # /mlx-ai command group + recording + WhisperX logic
├── config.yaml             # User-editable configuration (non-secret)
├── .env.example            # Template for secrets (DISCORD_TOKEN, GUILD_ID, HF_TOKEN)
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container definition for future K8s deployment
├── .dockerignore
├── .gitignore
└── README.md
```

---

## License

MIT
