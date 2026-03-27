## Overview

Build a Discord bot in Python using `discord.py` that records each voice channel participant's audio into a separate `.wav` file per session. The bot should be well-structured, configurable, Dockerfile-ready for future Kubernetes deployment, and follow modern Discord bot conventions using slash commands.

---

## Project Structure

```
mlx-ai-discord-recorder/
├── bot.py                  # Entry point — loads config, initializes bot, loads cogs
├── cogs/
│   └── recorder.py         # /mlx-ai command group + all recording logic
├── config.yaml             # User-editable configuration (non-secret)
├── .env.example            # Template for secrets (DISCORD_TOKEN, GUILD_ID)
├── requirements.txt        # All Python dependencies
├── Dockerfile              # Container-ready for future K8s deployment
├── .dockerignore
└── README.md               # Setup and usage instructions (replace the existing placeholder)
```

---

## Configuration

### `.env` / `.env.example`
Secrets only — should never be hardcoded:
```
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here
```

### `config.yaml`
Non-secret, user-editable settings:
```yaml
output_directory: ./recordings
announce_channel: "bot-commands"   # Name of the text channel for announcements
```

- `output_directory`: Where session folders will be written. Should support both relative and absolute paths.
- `announce_channel`: The name of the text channel the bot will send status announcements to. This is configured once and used for every session regardless of which voice channel is being recorded.

---

## Slash Commands

All commands live under the `/mlx-ai` command group.

### `/mlx-ai record start`
**Parameters:**
- `voice_channel` (required) — `Channel` type, filtered to voice channels only, rendered as a dropdown in Discord UI
- `session_name` (required) — `String` type, free text for the session name (e.g. `Campaign1_Session4`)

**Behavior:**
1. Bot joins the specified voice channel
2. Creates a session output folder named: `yyyyMMdd_HHmmss_{{session_name}}` (e.g. `20260327_143000_Campaign1_Session4`) inside `output_directory`
3. Begins recording each user present in the channel to their own `.wav` file named by their Discord username (e.g. `TheNightAngel17.wav`)
4. Announces in the configured text channel:
   > 🔴 Recording started in `DnD-Voice` — Session: `20260327_143000_Campaign1_Session4`
5. Returns an ephemeral confirmation to the user who ran the command

**Edge cases:**
- If the bot is already recording, respond with an ephemeral error message
- If the specified voice channel doesn't exist or bot lacks permissions, respond with an ephemeral error

---

### `/mlx-ai record stop`
**Parameters:** None

**Behavior:**
1. Stops all active recordings
2. Bot leaves the voice channel
3. Finalizes and closes all `.wav` files
4. Announces in the configured text channel:
   > ⏹️ Recording stopped — files saved to `recordings/20260327_143000_Campaign1_Session4`
5. Returns an ephemeral confirmation to the user who ran the command

**Edge cases:**
- If the bot is not currently recording, respond with an ephemeral error message

---

### `/mlx-ai record status`
**Parameters:** None

**Behavior:**
- If recording: respond ephemerally with current session name, voice channel, how long it's been recording, and how many users are being recorded
- If not recording: respond ephemerally with "No active recording session"

---

## Recording Behavior

### Audio Format
- Record in `.wav` format
- Discord provides audio as Opus streams; use `discord.py`'s built-in sink API for per-user wav output
- Use `discord.sinks.WaveSink` for per-user WAV recording

### Per-User Files
- One `.wav` file per user, named by Discord username: `{username}.wav`
- Stored inside the session folder

### Mid-Session Joins
- If a user joins the voice channel **after** recording has started, begin recording them automatically
- Announce in the configured text channel:
  > 🎙️ Now recording `PlayerTwo` who joined mid-session

### Auto-Stop on Empty Channel
- If **all human users** leave the voice channel (bot is the only one remaining), automatically trigger the same stop behavior as `/mlx-ai record stop`
- Announce in the configured text channel:
  > ⏹️ Channel empty — recording automatically stopped. Files saved to `recordings/20260327_143000_Campaign1_Session4`

### Bot Exclusion
- Never record audio from bots, only human users

---

## Dockerfile

The Dockerfile should:
- Use a slim Python base image (e.g. `python:3.11-slim`)
- Install `ffmpeg` (required by discord.py voice)
- Install Python dependencies from `requirements.txt`
- Copy all project files
- Set the entry point to `python bot.py`
- Support `.env` file injection at runtime (do not bake secrets into the image)

### `.dockerignore`
Exclude: `.env`, `recordings/`, `__pycache__/`, `.git/`, `*.pyc`

---

## requirements.txt

Should include at minimum:
- `discord.py[voice]` — with voice extras
- `PyNaCl` — required for Discord voice
- `pyyaml` — for config parsing
- `python-dotenv` — for `.env` loading

Pin versions where possible for reproducibility.

---

## README.md

Replace the existing placeholder README with a full one that includes:

1. **Project description** — what this bot does and why
2. **Prerequisites** — Python 3.11+, ffmpeg installation instructions for Mac (`brew install ffmpeg`), Linux (`apt install ffmpeg`), and Windows (download link)
3. **Discord Developer Portal setup** — step by step:
   - Create an application and bot
   - Enable **Privileged Gateway Intents**: `SERVER MEMBERS INTENT` and `MESSAGE CONTENT INTENT`
   - OAuth2 scopes needed: `bot` + `applications.commands`
   - Required bot permissions: `Connect`, `Speak`, `Use Voice Activity`, `Read Messages/View Channels`, `Send Messages`
   - How to copy the bot token
   - How to invite the bot to your server
4. **Installation** — clone repo, copy `.env.example` to `.env`, fill in values, edit `config.yaml`, `pip install -r requirements.txt`
5. **Running locally** — `python bot.py`
6. **Running with Docker** — `docker build` and `docker run` commands with `--env-file .env` flag
7. **Command reference** — table of all `/mlx-ai` commands with parameters and descriptions
8. **Output structure** — example of the folder/file layout after a session
9. **Future: Kubernetes** — brief note that a Dockerfile is included and K8s manifests are planned

---

## Additional Notes

- Use `discord.app_commands` for slash commands (not the legacy prefix command system)
- Sync slash commands on bot startup scoped to the configured `GUILD_ID` for instant availability (global sync can take up to an hour)
- Use a `commands.Cog` class in `
