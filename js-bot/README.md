# js-bot — Discord Voice Recording Bot

The JavaScript Discord bot that handles voice recording, slash command registration, and orchestrates the Python processing pipeline. Built on [discord.js](https://discord.js.org/) with native **DAVE (Discord Audio & Video Encryption)** support.

> 🐳 **Running in Docker?** See [DOCKER.md](../DOCKER.md). The bot is a lean Node image that reaches the Python services (`py-transcribe`, `py-process`, `py-query`) by name over the Compose network.

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

All commands are registered under the `/mlx-ai` root command: the `session`, `ask`, and `re-post-process` subcommands, plus the `category` management group.

---

### `/mlx-ai session` — Interactive Session Control Panel

Opens an ephemeral (only visible to you) control panel that walks you through the full session lifecycle: name → channel → record → post-process.

**Discord example:**
```
/mlx-ai session
```

The panel progresses through the following states:

| State | Description |
|-------|-------------|
| ⚪ **Idle** | Panel just opened — set a session name and select a voice channel to continue |
| 🟡 **Ready** | Both name and channel are set — press **⏺ Start Recording** to begin |
| 🔴 **Recording** | Actively recording — press **⏹ Stop Recording** when done |
| 🟠 **Stopping** | Stop requested — WAV files are being saved |
| 🟢 **Stopped** | Recording saved — choose a Whisper model and press **⚙️ Post-Process** |
| ⏳ **Processing** | Post-processing pipeline running (transcribe → merge → vectorize) |
| ✅ **Done** | Post-processing complete — a public summary embed is posted to the channel |

#### Panel Controls

| Control | Description |
|---------|-------------|
| Voice channel selector | Drop-down to choose which voice channel to record (shown in Idle and Ready states) |
| **✏️ Edit Session Details** | Opens a modal text input for the session name. Shown in green until a name is set. |
| **⏺ Start Recording** | Joins the selected channel and begins recording (enabled once name + channel are set) |
| **⏹ Stop Recording** | Stops recording and saves WAV files (shown while recording) |
| Whisper model selector | Drop-down to choose the Whisper model size for transcription (shown after recording stops) |
| **⚙️ Post-Process** | Runs the full transcription → merge → vectorize pipeline (enabled after recording stops) |

A **Log** field inside the embed shows live progress for each operation.

> **Note:** The panel is stateful and lives in memory for the lifetime of the bot process. If the bot restarts, open a new panel with `/mlx-ai session`.

---

### `/mlx-ai ask` — RAG Query

Ask a natural-language question about recorded and vectorized sessions. Spawns `py-query/query.py` and returns the answer directly in Discord.

**Discord examples:**
```
/mlx-ai ask question:What happened when the party entered the cave?
/mlx-ai ask question:Who attacked the dragon? session:20260330_143000_Campaign1_Session4 top_k:10 show_sources:true
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `question` | ✅ | — | The question to ask about recorded sessions |
| `session` | ❌ | All sessions | Restrict search to a specific session folder name |
| `top_k` | ❌ | `5` | Number of transcript chunks to retrieve (1–20) |
| `show_sources` | ❌ | `false` | Include the retrieved source chunks in the reply |

---

### `/mlx-ai re-post-process` — Re-run the Pipeline on an Existing Session

Re-runs the **full** post-processing pipeline (transcribe → merge → vectorize → summarize) on a session you already recorded — handy after editing a category prompt, switching the summary/embedding model, or recovering a session whose transcript came out wrong. Pick the session from an autocompleted list of your recorded folders.

Because the session is already in the vector DB, this always **re-indexes** it (vectorize.py would otherwise skip it). Whisper model, language, and the summary toggle come from `config.yaml` (`whisper_model`, `whisper_language`, `auto_summarize`).

Setting `re_transcribe:true` rebuilds the transcript **from the audio**: it clears the per-snippet `<offset_ms>.json` sidecars and re-transcribes via the warm-model [py-transcribe](../py-transcribe/README.md) service (or the local Whisper batch fallback if that service isn't running). Leave it `false` to keep the existing transcription and just re-merge / re-index / re-summarize.

**Discord examples:**
```
/mlx-ai re-post-process session:20260330_143000_Campaign1_Session4
/mlx-ai re-post-process session:20260330_143000_Campaign1_Session4 re_transcribe:true
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `session` | ✅ | — | Session folder to re-process (autocompleted, newest first) |
| `re_transcribe` | ❌ | `false` | Also rebuild the transcript from audio (clears sidecars and re-transcribes) |

> **Note:** `re_transcribe:true` needs the original snippet WAVs. If `keep_wav` is `false` they were deleted after the first merge, so only a transcript-preserving re-run is possible.

---

## Behaviour & Automation

- **Per-user utterance snippets** — Each user's speech is saved as discrete snippet WAVs in a per-user sub-folder: `<output_directory>/<YYYYMMDD_HHMMSS>_<session_name>/<username>/<offset_ms>.wav`. The filename is the snippet's session-relative start offset in milliseconds, so files sort chronologically and carry their own timing
- **Utterance segmentation** — A fresh capture opens when a user starts talking and closes after they've been silent for `snippet_silence_ms` (default 1000ms; see `config.yaml`). That silence window also acts as a back-pad, keeping trailing words that Discord can signal as "stopped" slightly early
- **Live transcription (optional)** — when `auto_transcribe` is enabled, each finished snippet is POSTed (fire-and-forget) to the warm-model transcription service ([py-transcribe](../py-transcribe/README.md)) so it's transcribed *during* the session. If the service is down, capture is unaffected and the batch fallback transcribes at post-process time
- **Do-not-record list** — speakers can be skipped entirely (no sub-folder, no WAV, no transcription). `ignore_bots` (default `true`) skips every Discord bot — the usual fix for a music bot eating disk — and `do_not_record` is an explicit blacklist of Discord user IDs (preferred; stable) and/or usernames (case-insensitive). See `config.yaml`
- **Auto-stop** — When the last human leaves the voice channel, the recording stops automatically (any in-flight snippet is flushed first)
- **Mid-session joins** — Users who join after recording starts are detected and recorded; their sub-folder is created on their first utterance, their username appears in the panel log, and an announcement is sent to the configured text channel
- **Session panel** — The `/mlx-ai session` panel guides the user through the entire session lifecycle. When post-processing completes, a public summary embed is posted to the channel.
- **Python spawning** — Post-processing scripts (`process.py`, `merge_audio.py`, `vectorize.py`) are spawned from the repo root via `child_process.spawn()`, using the `.venv` Python if present, falling back to system `python`. RAG queries are sent to the always-on `py-query/app.py` HTTP API instead of spawning a script
- **Concurrency guards** — Only one recording and one post-processing job can run at a time; the panel disables its buttons accordingly

---

## Text-Channel Announcements

The bot posts status messages to the channel configured as `announce_channel` in `config.yaml`:

| Event | Example Message |
|-------|-----------------|
| Recording starts | `Recording started in \`DnD-Voice\` — Session: \`20260330_143000_Campaign1_Session4\`` |
| User joins mid-session | `Now recording \`PlayerTwo\` who joined mid-session` |
| Recording stops | `Recording stopped — files saved to \`recordings/20260330_143000_Campaign1_Session4\`` |
| Auto-stop (empty channel) | `Recording automatically stopped (channel empty). Files saved to \`recordings/...\`` |
| Post-processing starts | `Post-processing started for session \`...\` (model: base, language: en, 42 snippet(s))` |
| Post-processing complete | `✅ Post-processing complete for session \`...\` — files saved to \`...\`` |
| Re-post-processing starts | `Re-post-processing started for session \`...\` (model: base, language: en)` |
| Re-post-processing complete | `✅ Re-post-processing complete for session \`...\` — files saved to \`...\`` |

---

## Output Structure

After recording and full pipeline processing, a session folder looks like:

```
recordings/
└── 20260330_143000_Campaign1_Session4/
    ├── _session.metadata.json          # { session_start_ms, session_name }
    ├── TheNightAngel17/                 # Per-user snippet sub-folder
    │   ├── 0000000000.wav               #   utterance snippet, name = start offset (ms)
    │   └── 0000012840.wav
    ├── PlayerTwo/
    │   └── 0000003120.wav
    ├── TheNightAngel17.txt              # Per-user Whisper transcript
    ├── PlayerTwo.txt
    ├── _combined_transcript.txt         # All users merged chronologically
    ├── _session_mix.wav                 # Snippets placed at their offsets, all users mixed (only when keep_mix_wav: true)
    └── _session_mix.mp3                 # Compressed combined audio (only when keep_mix_mp3: true, the default)
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
| `bot.js` | **Entry point.** Loads config/env, creates the Discord client, registers slash commands as guild commands on startup, and routes all interactions (`/mlx-ai` subcommands, buttons, select menus, and modals) to the appropriate handler. |
| `recorder.js` | **Voice recording logic.** Manages the voice connection lifecycle: joining channels and, each time a user starts talking, opening a per-utterance Opus subscription (`EndBehaviorType.AfterSilence`) that decodes to PCM via prism-media and writes a timestamped snippet WAV into the user's sub-folder when the utterance ends. When `auto_transcribe` is on, also enqueues each finished snippet to the [py-transcribe](../py-transcribe/README.md) service. Handles auto-stop and mid-session join detection. |
| `postProcessor.js` | **Post-processing trigger.** POSTs jobs to the always-on `py-process/app.py` job runner (transcribe → merge → vectorize → summarize), polls them for progress, and reports results back to Discord. Handles the `/mlx-ai session` **⚙️ Post-Process** button and the `/mlx-ai re-post-process` re-run (with optional re-transcription and forced re-indexing). Tracks running state so only one job runs at a time. |
| `queryHandler.js` | **RAG query handler.** Calls the `py-query/app.py` HTTP API via `fetch()`. Formats the JSON response into a Discord reply with the answer, timings, and optional source citations. |
| `sessionPanel.js` | **Interactive session control panel.** Manages the ephemeral `/mlx-ai session` panel: panel state machine (idle → ready → recording → stopping → stopped → processing → done), embed builder, and handlers for the channel select menu, session name modal, Whisper model selector, and record/stop/post-process buttons. Posts a public completion embed when post-processing finishes. |
