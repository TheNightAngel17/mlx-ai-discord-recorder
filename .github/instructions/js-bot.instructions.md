---
applyTo: "js-bot/**"
---

# JS Bot — Copilot Instructions

## Language & Runtime

- **Runtime:** Node.js 18+, CommonJS (`require()` / `module.exports`)
- **Style:** `"use strict"` at the top of every file
- **Imports:** All `require()` calls go at the **top of the file** — never inline inside functions or callbacks
- **Async:** Use `async/await` — avoid raw `.then()` chains
- **No new dependencies** without explicit justification. The bot intentionally avoids heavy frameworks.

## Naming Conventions

| Context | Convention | Example |
|---|---|---|
| Variables / functions | camelCase | `sessionName`, `mergeAudio()` |
| Classes | PascalCase | `Recorder`, `PostProcessor` |
| Slash commands | kebab-case | `/mlx-ai session`, `/mlx-ai ask` |
| Session folders | `YYYYMMDD_HHMMSS_<name>` | `20260330_143000_Campaign1` |

## Logging

Use the `makeLogger(module)` factory from `bot.js`. Format: `YYYY-MM-DD HH:MM:SS [LEVEL] module: message`. Never use bare `console.log()`.

## Discord Interactions

- Always reply with `ephemeral: true` for command responses.
- Use `deferReply()` for anything that might take more than 3 seconds (file I/O, spawning processes, network calls). You have 3 seconds before Discord times out.
- Wrap every Discord `await` in try/catch. Log errors with `logger.error()` and reply to the user with a human-readable message. **Never** leak stack traces or file system paths to Discord.

## Code Quality Checklist

1. All `require()` statements at file top — no inline imports.
2. No unused imports or variables. Remove dead code — don't comment it out.
3. Document public functions with JSDoc (`@param` / `@returns`).
4. Every `await` on a Discord operation must be in a try/catch.
5. Concurrency guards: the bot enforces single-instance for recording, post-processing, merge, and vectorize. New operations that must not run concurrently must follow the same `isRunning` flag pattern.
6. No hardcoded values — anything configurable goes in `config.yaml`.
7. Consistent logging — use `makeLogger`, not `console.log`.

## Architecture Decisions (do not change without discussion)

- **JS bot, not Python bot.** py-cord does not support Discord's DAVE/E2EE protocol. `discord.js` + `@discordjs/voice` does. This is non-negotiable while Discord enforces DAVE.
- **Spawn Python as child processes.** Call Python scripts via `child_process.spawn()` with argument arrays. Never use `exec()`, `execSync()`, or template-string shell commands.
- **Spawned processes inherit `process.env`.** This is intentional (API keys flow to Python). Never pass secrets as CLI arguments — they'd appear in `ps` output.
- **Silence padding must be preserved.** `SilencePadTransform` in `recorder.js` keeps per-user WAVs time-aligned. Removing it breaks multi-user transcript alignment.
- **`EndBehaviorType.Manual` is required** for continuous recording. `AfterSilence` / `AfterInactivity` will stop streams prematurely during natural speech pauses.

## Common Pitfalls

- **Interaction timeouts:** 3-second deadline. Always `deferReply()` before any I/O or subprocess.
- **WAV header size limits:** WAV uses a 32-bit size field (~4 GB max). Monitor for very long sessions.
- **Python venv detection:** `postProcessor.js` and `queryHandler.js` check `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix) before falling back to system `python`. Update both files if the venv path logic changes.
