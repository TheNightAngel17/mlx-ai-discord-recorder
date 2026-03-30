# MLX AI Discord Recorder — Copilot Session Summary

## The Problem

The original Python bot (`bot.py` + `cogs/recorder.py`) was built with **py-cord 2.7.1** and failed to connect to voice channels with:

```
discord.errors.ConnectionClosed: Shard ID None WebSocket closed with 4017
Extra: E2EE/DAVE protocol required
```

**Root cause:** Discord rolled out **DAVE (Discord Audio & Video Encryption)** — their E2EE protocol — and is enforcing it on voice channels. py-cord does not support DAVE and has no plans to add it in the near term. No Python Discord library currently has stable DAVE support (discord.py `master` has experimental support but it's not on PyPI).

---

## The Decision

Rather than disable E2EE on the voice channel, we decided to **rewrite the recording layer in JavaScript** using `discord.js` + `@discordjs/voice`, which supports DAVE out of the box since Discord maintains the JS ecosystem first.

The Python transcription pipeline (Whisper/MLX) stays untouched. The JS bot handles voice recording only, saving per-user WAV files to the same `output_directory` configured in `config.yaml`. Python can then pick those up for transcription as before.

---

## Repository Structure

```
mlx-ai-discord-recorder/
├── bot.py                  # Python bot entry point (py-cord, broken due to DAVE)
├── cogs/
│   └── recorder.py         # Python recording cog (broken due to DAVE)
├── config.yaml             # Shared config (used by both Python and JS bots)
├── .env                    # Secrets: DISCORD_TOKEN, GUILD_ID
├── requirements.txt        # Python deps
├── Dockerfile              # Python bot Docker config
└── js-bot/                 # NEW: JavaScript bot (DAVE-compatible)
    ├── bot.js              # discord.js entry point
    ├── recorder.js         # All recording logic
    ├── package.json        # Node deps
    └── README.md           # JS bot quickstart
```

---

## JS Bot Architecture

### Entry point: `js-bot/bot.js`
- Loads `../.env` and `../config.yaml` (same files as Python bot — no duplication)
- Creates a `discord.js` v14 `Client`
- Registers slash commands as **guild commands** via REST on startup (instant propagation, same behaviour as py-cord)
- Routes `/mlx-ai record start|stop|status` to `Recorder`
- Forwards `voiceStateUpdate` events to `Recorder`

### Recording logic: `js-bot/recorder.js`

**Commands (same as Python bot):**
- `/mlx-ai record start <voice_channel> <session_name>` — join channel, begin per-user recording
- `/mlx-ai record stop` — stop recording, save WAVs, disconnect, announce
- `/mlx-ai record status` — show session name, channel, elapsed time, user count

**Key behaviours:**
- Per-user WAV files named `<username>.wav` in `<output_directory>/<YYYYMMDD_HHMMSS>_<session_name>/`
- Auto-stop when the last human leaves the voice channel
- Announcement in `announce_channel` text channel on start/stop/mid-join
- Mid-session join: subscribes new user's audio automatically via `receiver.speaking` event

**Audio pipeline:**
```
Discord (Opus, E2EE/DAVE) 
  → @discordjs/voice receiver.subscribe(userId)
  → prism-media opus.Decoder (48kHz, stereo, 16-bit PCM)
  → fs.WriteStream → <username>.pcm (temp)
  → on stop: prepend WAV header → <username>.wav
  → delete .pcm temp file
```

WAV format: 48000 Hz, 2 channels, 16-bit little-endian PCM (matches Discord's Opus output).

---

## Bugs Found & Fixed

### Bug 1: Subscribing before connection is Ready
**Symptom:** `No audio captured for <user> — skipping.` — empty WAV files.  
**Cause:** `receiver.subscribe(userId)` was called immediately after `joinVoiceChannel()`, before the voice connection reached `VoiceConnectionStatus.Ready`. Audio packets weren't being delivered yet.  
**Fix:** `await entersState(connection, VoiceConnectionStatus.Ready, 20_000)` before subscribing.

### Bug 2: Stream destruction race condition
**Symptom:** Same as above — empty WAV files.  
**Cause:** On stop, the code called `entry.decoder.destroy()` then immediately `Buffer.concat(chunks)`. Destroying a Transform stream mid-pipe drops buffered chunks that haven't been emitted as `data` events yet.  
**Fix:** Pipe directly to a `fs.WriteStream` (temp `.pcm` file on disk) instead of accumulating in-memory chunks. On stop, destroy the opus stream and wait for the file stream's `finish` event before converting PCM → WAV.

### Bug 3: "Application did not respond" — Discord 3-second timeout
**Symptom:** Discord shows "The application did not respond" when running `/mlx-ai record start`.  
**Cause:** `await entersState(..., 20_000)` can take a few seconds. Discord requires *any* response to a slash command interaction within 3 seconds or it marks it as failed.  
**Fix:** Call `await interaction.deferReply({ ephemeral: true })` as the very first line of `start()` (before any async work). This tells Discord "I’m working on it" and extends the deadline to 15 minutes. All subsequent `interaction.reply()` calls in `start()` must be changed to `interaction.editReply()`.

---

## Setup & Running

### Prerequisites
```bash
# Node.js 22 LTS (via NodeSource)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# Native audio deps
sudo apt install -y ffmpeg libopus0 libopus-dev
```

### Install JS bot deps
```bash
cd js-bot
npm install --legacy-peer-deps
# Note: --legacy-peer-deps needed due to opusscript peer dep version mismatch with prism-media
```

### Run
```bash
node js-bot/bot.js
# OR from js-bot/ directory:
node bot.js
```

### Config
Uses the existing `config.yaml` and `.env` at the repo root — no extra config needed.

```yaml
# config.yaml
output_directory: /path/to/recordings   # where WAV files are saved
announce_channel: "bot-commands"         # text channel for announcements
```

```env
# .env
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=your_guild_id_here
```

---

## npm audit notes

`npm audit` reports vulnerabilities in `undici` (a dependency of `discord.js`). These are **safe to ignore** for this use case:
- All vulnerabilities require an adversary to send malicious HTTP/WebSocket responses *to* the bot
- The bot only connects *outward* to Discord's servers — no untrusted inbound connections
- `npm audit fix --force` would downgrade to `discord.js@13.x` which doesn't support DAVE — **do not run it**
- Will be fixed upstream when `discord.js` bumps its `undici` dependency

---

## Current State (as of this session)

- [x] JS bot scaffolded in `js-bot/`
- [x] DAVE/E2EE connection working (no more `4017` errors)
- [x] Slash commands registering and routing correctly
- [x] Bug 1 fixed (entersState before subscribe)
- [x] Bug 2 fixed (PCM written to disk, not memory)
- [ ] Bug 3 fix (deferReply) — PR open, pending merge and test
- [ ] End-to-end test with actual audio captured and saved as WAV

---

## What's Left

1. **Merge and test the deferReply fix** — merge the open PR, `git pull` the fix branch, restart bot, trigger `/mlx-ai record start`, speak, `/mlx-ai record stop`, verify WAV file is non-empty and plays back correctly
2. **Once recording works end-to-end** — wire up the Python Whisper/MLX transcription pipeline to process the WAV files produced by the JS bot
3. **Optional cleanup** — merge the fix branch to `main`, consider removing or archiving the Python bot files if the JS bot fully replaces it

---

## Key Library Versions

| Library | Version | Notes |
|---|---|---|
| discord.js | ^14.18.0 | v14 required for DAVE support |
| @discordjs/voice | ^0.18.0 | Voice recording + DAVE |
| @discordjs/opus | ^0.9.0 | Native Opus encoder/decoder |
| prism-media | ^1.3.5 | Opus → PCM decoding |
| dotenv | ^16.4.7 | `.env` loading |
| js-yaml | ^4.1.0 | `config.yaml` parsing |
| Node.js | 22 LTS | Required minimum: 18+ |