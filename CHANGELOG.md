# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- `/mlx-ai transcribe start <session_name> [model] [language]` slash command — triggers Whisper transcription from Discord by spawning the Python script as a child process.
- `/mlx-ai transcribe status` slash command — check if a transcription is currently running.
- `js-bot/transcriber.js` — Transcriber module that manages the Python subprocess, enforces one-transcription-at-a-time, and posts results/errors back to Discord.
- `py-transcribe/transcribe.py` — Whisper-based transcription tool that reads per-user WAV files from a session folder and produces timestamped per-user transcripts plus a combined chronological transcript.
  - CLI flags: `--model` (tiny/base/small/medium/large), `--language` (auto-detect by default).
- `py-transcribe/requirements.txt` — Python dependencies for the transcription pipeline.
- Voice connection state-change and error logging in `recorder.js` for debugging connection issues.
- `sodium-native` ^5.1.0 dependency — required by `@discordjs/voice` as the voice encryption backend.
- `py-transcribe/` directory — placeholder for the upcoming Whisper/MLX transcription pipeline.

### Moved

- Archived the original Python bot (`bot.py`, `cogs/`, `Dockerfile`, `.dockerignore`, `requirements.txt`) into `py-bot_old/` for historical reference. The Python bot is non-functional due to Discord's DAVE E2EE requirement and has been fully replaced by the JS bot.

### Changed

- Upgraded `@discordjs/voice` from ^0.18.0 to ^0.19.2 to gain DAVE (Discord Audio & Video Encryption) E2EE support. Version 0.18.0 did not implement the DAVE protocol, causing the voice connection to loop between `signalling` and `connecting` states indefinitely and never reach `Ready`.

### Fixed

- Voice connection timeout ("Timed out waiting for voice connection to be ready") caused by two missing pieces:
  1. `@discordjs/voice` 0.18.0 had no DAVE protocol support — upgraded to 0.19.2 which bundles `@snazzah/davey` for the DAVE handshake.
  2. No encryption library was installed — added `sodium-native` so the voice connection can complete its crypto negotiation.
- Per-user WAV files now include silence padding for gaps when the user isn't speaking. Previously, Discord's voice activity detection caused silent periods to be dropped entirely, collapsing the timeline and making multi-user transcripts misaligned. A `SilencePadTransform` stream now tracks wall-clock time from session start and injects zero-filled PCM data for any gap longer than one frame (20ms).
