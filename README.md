# MLX AI Discord Recorder

A Discord bot that joins a voice channel and records each participant's audio into a separate `.wav` file per session. The recordings can then be fed into a speech-to-text pipeline (such as [Whisper](https://github.com/openai/whisper)) to generate session transcripts, which can be vectorised and queried via a self-hosted RAG system — perfect for archiving D&D sessions.

---

## Features

- 🎙️ **Per-user WAV recording** — each participant gets their own clean audio file
- 📁 **Timestamped session folders** — `yyyyMMdd_HHmmss_<session_name>`
- 🔴 **Mid-session join detection** — players who join late are recorded automatically
- ⏹️ **Auto-stop when channel empties** — no manual intervention needed at session end
- 💬 **Text-channel announcements** — configurable channel for start/stop notifications
- 🐳 **Docker-ready** — pre-wired for future Kubernetes deployment

---

## Prerequisites

- **Python 3.11+**
- **ffmpeg** (required by py-cord for voice audio)

### Installing ffmpeg

| Platform | Command |
|----------|---------|
| macOS    | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Windows  | Download from <https://ffmpeg.org/download.html> and add to PATH |

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
# Edit .env — add your DISCORD_TOKEN and GUILD_ID

# 3. Review and adjust config.yaml (output directory, announcement channel)
# nano config.yaml

# 4. Install Python dependencies
pip install -r requirements.txt
```

---

## Configuration

### `.env` (secrets — never commit this file)

```dotenv
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_numeric_guild_id_here
```

### `config.yaml` (non-secret settings)

```yaml
output_directory: ./recordings       # Where session folders are written
announce_channel: "bot-commands"     # Text channel for bot announcements
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
| `/mlx-ai record start` | `voice_channel` (required), `session_name` (required) | Join the voice channel and begin per-user recording |
| `/mlx-ai record stop` | — | Stop the active recording and save all WAV files |
| `/mlx-ai record status` | — | Show the current session name, channel, duration, and user count |

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
| Recording stops (command) | `⏹️ Recording stopped — files saved to \`recordings/20260327_143000_Campaign1_Session4\`` |
| Recording stops (channel empty) | `⏹️ Channel empty — recording automatically stopped. Files saved to \`recordings/20260327_143000_Campaign1_Session4\`` |

---

## Output Structure

After a session, the output directory will look like this:

```
recordings/
└── 20260327_143000_Campaign1_Session4/
    ├── TheNightAngel17.wav
    ├── PlayerTwo.wav
    └── PlayerThree.wav
```

Each `.wav` file is named after the Discord username of the participant and contains only that user's audio — ideal for per-speaker speech-to-text processing.

---

## Next Steps — AI Pipeline

Once you have per-user WAV files, a natural next pipeline is:

1. **Transcription** — feed each WAV into [Whisper](https://github.com/openai/whisper) (runs locally, free)
2. **Merging** — combine per-user transcripts into a single session script ordered by timestamp
3. **Vectorisation** — chunk and embed the transcript with a local model (e.g. `nomic-embed-text` via [Ollama](https://ollama.com/))
4. **Storage** — store vectors in [Chroma](https://www.trychroma.com/) or [Qdrant](https://qdrant.tech/)
5. **RAG Chat** — query sessions with [Open WebUI](https://github.com/open-webui/open-webui) + Ollama

---

## Future: Kubernetes

A `Dockerfile` is included and ready to use. Kubernetes manifests (Deployment, PersistentVolumeClaim, Secret, ConfigMap) are planned as a follow-up to enable fully containerised, self-hosted deployment of the complete D&D transcription pipeline.

Key notes for K8s deployment:

- Run **exactly 1 replica** — Discord's gateway connection is stateful
- Mount a `PersistentVolumeClaim` to `/app/recordings` to retain audio files across pod restarts
- Store `DISCORD_TOKEN` in a Kubernetes `Secret`, not a `ConfigMap`

---

## Project Structure

```
mlx-ai-discord-recorder/
├── bot.py                  # Entry point — loads config, initialises bot, loads cogs
├── cogs/
│   ├── __init__.py         # Makes cogs/ a proper Python package
│   └── recorder.py         # /mlx-ai command group + all recording logic
├── config.yaml             # User-editable configuration (non-secret)
├── .env.example            # Template for secrets (DISCORD_TOKEN, GUILD_ID)
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container definition for future K8s deployment
├── .dockerignore
├── .gitignore
└── README.md
```

---

## License

MIT
