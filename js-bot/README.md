# MLX AI Discord Recorder — JS Bot

This JavaScript bot replaces the Python voice-recording layer with one that
supports Discord's **DAVE (E2EE)** protocol. The Python bot (`bot.py` +
`cogs/recorder.py`) cannot connect to voice channels when E2EE is enabled on a
server because py-cord does not support DAVE yet (error `4017: E2EE/DAVE
protocol required`). `discord.js` + `@discordjs/voice` already support DAVE,
so this bot handles the voice recording while the Python transcription pipeline
(Whisper/MLX) remains unchanged.

---

## Prerequisites

- **Node.js 18 or newer** — <https://nodejs.org>
- **ffmpeg** and **libopus** installed on your system:

  ```bash
  # Debian / Ubuntu
  sudo apt install ffmpeg libopus0 libopus-dev
  ```

---

## Quick Start

1. **Install dependencies**

   ```bash
   cd js-bot
   npm install
   ```

2. **Config** — no extra configuration needed.  
   The bot reads the same files as the Python bot:

   | File | Purpose |
   |---|---|
   | `../config.yaml` | `output_directory`, `announce_channel` |
   | `../.env` | `DISCORD_TOKEN`, `GUILD_ID` |

   If you haven't created `.env` yet, copy the example:

   ```bash
   cp ../.env.example ../.env
   # then edit ../.env and fill in your token and guild ID
   ```

3. **Run the bot**

   ```bash
   node bot.js
   # or
   npm start
   ```

---

## Commands

All commands are slash commands registered to the configured guild.

| Command | Description |
|---|---|
| `/mlx-ai record start voice_channel:<channel> session_name:<name>` | Join the voice channel and start recording each user to a separate WAV file |
| `/mlx-ai record stop` | Stop the active recording, save WAV files, and announce in the configured text channel |
| `/mlx-ai record status` | Show the current session name, voice channel, elapsed duration, and number of humans being recorded |

### Behaviour

- Each user's audio is saved to `<output_directory>/<YYYYMMDD_HHMMSS>_<session_name>/<username>.wav`
- When the last human leaves the voice channel the recording stops automatically
- When a human joins mid-session an announcement is sent to `announce_channel`

---

## Why a separate JS bot?

py-cord (the Python Discord library used by the existing `bot.py`) does not
yet implement the **DAVE (Discord's Audio/Video E2EE)** protocol.  When E2EE
is enabled on a server (which is the default for voice channels in newer
Discord builds), the voice gateway returns close code `4017: E2EE/DAVE
protocol required` and the Python bot cannot connect.

`discord.js` v14 + `@discordjs/voice` implement DAVE natively, so this bot
can connect and record in E2EE-enabled servers without any changes to your
server settings.

The Python transcription pipeline (Whisper/MLX, `cogs/recorder.py`) is
**not removed or modified** — only the voice-recording layer moves to JS.
