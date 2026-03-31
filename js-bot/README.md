# js-bot — Discord Voice Recording Bot

The JavaScript Discord bot that handles voice recording, slash command registration, and orchestrates the Python processing pipeline. Built on [discord.js](https://discord.js.org/) with native **DAVE (Discord Audio & Video Encryption)** support.

> **↩️ Back to [main README](../README.md)** | See also: [py-process](../py-process/README.md) · [py-query](../py-query/README.md)

---

## Table of Contents

- [Why JavaScript?](#why-javascript)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Running the Bot](#running-the-bot)
- [Slash Command Reference](#slash-command-reference)
- [Behaviour & Automation](#behaviour--automation)
- [Text-Channel Announcements](#text-channel-announcements)
- [Output Structure](#output-structure)
- [Dependencies](#dependencies)
- [File Descriptions](#file-descriptions)

---

## Why JavaScript?

py-cord (the Python Discord library) does not yet implement the **DAVE (Discord's Audio/Video E2EE)** protocol. When E2EE is enabled on a server (the default for voice channels in newer Discord builds), the voice gateway returns close code `4017: E2EE/DAVE protocol required` and the Python bot cannot connect.

`discord.js` v14 + `@discordjs/voice` v0.19+ implement DAVE natively, so this bot can connect and record in E2EE-enabled servers without any server-side changes.

---

## Prerequisites

- **Node.js 18+** — [Download](https://nodejs.org/)
- **ffmpeg** — Required for audio processing. See the [main README](../README.md#installing-ffmpeg) for installation instructions.
- **Python 3.11+** — Required for the processing pipeline scripts that this bot spawns. See the [main README](../README.md#prerequisites) for details.
- A configured `.env` file at the repository root (see [main README](../README.md#installation))

---

## Installation

```bash
cd js-bot
npm install
```

No additional configuration is needed beyond what's in the root `.env` and `config.yaml` files.

| Config File | Location | Used For |
|-------------|----------|----------|
| `.env` | `../` (repo root) | `DISCORD_TOKEN`, `GUILD_ID`, API keys |
| `config.yaml` | `../` (repo root) | `output_directory`, `announce_channel`, Whisper settings, LLM settings |

---

## Running the Bot

```bash
cd js-bot
node bot.js
# or
npm start
```

The bot logs to stdout with timestamps. On first startup it registers slash commands to the configured guild (takes effect immediately).

---

## Slash Command Reference

All commands are registered under the `/mlx-ai` root command.

### Recording

| Command | Description |
|---------|-------------|
| `/mlx-ai record start voice_channel:<channel> session_name:<name>` | Join the voice channel and start recording each user to a separate WAV file |
| `/mlx-ai record stop` | Stop the active recording, save WAV files, disconnect, and announce |
| `/mlx-ai record status` | Show session name, voice channel, elapsed duration, and number of users being recorded |

**Discord example:**
```
/mlx-ai record start voice_channel:#DnD-Voice session_name:Campaign1_Session4
```

### Post-Processing (Full Pipeline)

Triggers `py-process/process.py` which runs: transcribe → merge audio → vectorize.

| Command | Description |
|---------|-------------|
| `/mlx-ai post-process start session_name:<name> [model:<size>] [language:<code>]` | Run the full processing pipeline on a recorded session |
| `/mlx-ai post-process status` | Check if post-processing is currently running |

**Discord example:**
```
/mlx-ai post-process start session_name:20260330_143000_Campaign1_Session4 model:medium language:en
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `session_name` | ✅ | — | Session folder name (e.g. `20260330_143000_Campaign1_Session4`) |
| `model` | ❌ | From `config.yaml` | Whisper model: `tiny`, `base`, `small`, `medium`, `large` |
| `language` | ❌ | From `config.yaml` | Language code (e.g. `en`). Omit for auto-detect. |

### Audio Merge

Triggers `py-process/merge_audio.py` to mix per-user WAVs into a single combined recording.

| Command | Description |
|---------|-------------|
| `/mlx-ai merge-audio start session_name:<name>` | Mix all per-user WAV files into `_session_mix.wav` + `_session_mix.mp3` |
| `/mlx-ai merge-audio status` | Check if an audio merge is currently running |

**Discord example:**
```
/mlx-ai merge-audio start session_name:20260330_143000_Campaign1_Session4
```

### Vectorize

Triggers `py-process/vectorize.py` to chunk, embed, and store transcripts in ChromaDB.

| Command | Description |
|---------|-------------|
| `/mlx-ai vectorize start session_name:<name\|"all"> [force:<true\|false>]` | Chunk and embed a session's transcript into ChromaDB |
| `/mlx-ai vectorize status` | Check if vectorization is currently running |

**Discord example:**
```
/mlx-ai vectorize start session_name:all force:true
```

### RAG Query

Triggers `py-query/query.py` to answer natural-language questions about recorded sessions.

| Command | Description |
|---------|-------------|
| `/mlx-ai query ask question:<text> [session:<name>] [top_k:<n>] [show_sources:<bool>]` | Ask a question about recorded D&D sessions using RAG |

**Discord example:**
```
/mlx-ai query ask question:What happened when the party entered the cave? session:20260330_143000_test top_k:5 show_sources:true
```

---

## Behaviour & Automation

- **Per-user audio files** — Each user's audio is saved to `<output_directory>/<YYYYMMDD_HHMMSS>_<session_name>/<username>.wav`
- **Auto-stop** — When the last human leaves the voice channel, the recording stops automatically
- **Mid-session joins** — Users who join after recording starts are detected and recorded; an announcement is sent to the configured text channel
- **Silence padding** — Per-user WAV files include silence for gaps when the user isn't speaking, keeping all files time-aligned
- **Post-process button** — When a recording stops, the bot posts a message with a model selector and a "Start Processing" button for one-click post-processing
- **Python spawning** — All Python scripts are spawned from the repo root, using the `.venv` Python if present, falling back to system `python`
- **Concurrency guards** — Only one recording, one post-processing job, one merge, and one vectorize job can run at a time

---

## Text-Channel Announcements

The bot posts status messages to the channel configured as `announce_channel` in `config.yaml`:

| Event | Example Message |
|-------|-----------------|
| Recording starts | `Recording started in \`DnD-Voice\` — Session: \`20260330_143000_Campaign1_Session4\`` |
| User joins mid-session | `Now recording \`PlayerTwo\` who joined mid-session` |
| Recording stops | `Recording stopped — files saved to \`recordings/20260330_143000_Campaign1_Session4\`` |
| Auto-stop (empty channel) | `Recording automatically stopped (channel empty). Files saved to \`recordings/...\`` |
| Post-processing starts | `Post-processing started for session \`...\` (model: base, language: en, 3 file(s))` |
| Post-processing complete | `✅ Post-processing complete for session \`...\` — files saved to \`...\`` |

---

## Output Structure

After recording and full pipeline processing, a session folder looks like:

```
recordings/
└── 20260330_143000_Campaign1_Session4/
    ├── TheNightAngel17.wav              # Per-user recording
    ├── PlayerTwo.wav
    ├── TheNightAngel17.txt              # Per-user Whisper transcript
    ├── PlayerTwo.txt
    ├── _combined_transcript.txt         # All users merged chronologically
    ├── _session_mix.wav                 # All users mixed into one WAV
    └── _session_mix.mp3                 # Compressed combined audio
```

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| [`discord.js`](https://www.npmjs.com/package/discord.js) | ^14.18.0 | Discord API client — slash commands, interactions, gateway |
| [`@discordjs/voice`](https://www.npmjs.com/package/@discordjs/voice) | ^0.19.2 | Voice connections with DAVE/E2EE support |
| [`prism-media`](https://www.npmjs.com/package/prism-media) | ^1.3.5 | Opus decoding for incoming voice audio streams |
| [`opusscript`](https://www.npmjs.com/package/opusscript) | ^0.1.1 | Pure-JS Opus codec (peer dependency of prism-media) |
| [`sodium-native`](https://www.npmjs.com/package/sodium-native) | ^5.1.0 | Native encryption library for Discord voice crypto negotiation |
| [`dotenv`](https://www.npmjs.com/package/dotenv) | ^16.4.7 | Load `.env` file into `process.env` |
| [`js-yaml`](https://www.npmjs.com/package/js-yaml) | ^4.1.0 | Parse `config.yaml` configuration |

---

## File Descriptions

| File | Description |
|------|-------------|
| `bot.js` | **Entry point.** Loads config/env, creates the Discord client, registers slash commands as guild commands on startup, and routes all interactions (`/mlx-ai` subcommands, buttons, select menus) to the appropriate handler. |
| `recorder.js` | **Voice recording logic.** Manages the voice connection lifecycle: joining channels, subscribing to per-user Opus audio streams, decoding to PCM via prism-media, padding silence for gaps, writing temporary PCM files, and converting to WAV on stop. Handles auto-stop and mid-session join detection. |
| `postProcessor.js` | **Python script spawner.** Spawns `py-process/process.py`, `merge_audio.py`, and `vectorize.py` as child processes. Tracks running state for each operation independently and reports results back to Discord. |
| `queryHandler.js` | **RAG query handler.** Spawns `py-query/query.py` with the user's question and options. Parses the structured stdout output and formats it into a clean Discord reply with answer, timings, and optional sources. |
