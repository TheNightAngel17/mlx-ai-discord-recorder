# Copilot Instructions — MLX AI Discord Recorder

## Project Overview

This is a **Discord voice recording bot** with an AI post-processing pipeline. It records per-user audio from Discord voice channels, transcribes with Whisper, vectorizes into ChromaDB, and answers natural-language questions via RAG.

### Architecture

```
js-bot/       → Node.js Discord bot (discord.js + @discordjs/voice, DAVE/E2EE)
py-process/   → Python processing pipeline (Whisper, pydub, ChromaDB, Ollama)
py-query/     → Python RAG query service (multi-provider LLM: Ollama, OpenAI, Anthropic)
```

The JS bot is the only runtime process. It spawns Python scripts as child processes via `child_process.spawn()`.

---

## Language & Framework Conventions

### JavaScript (js-bot/)

- **Runtime:** Node.js 18+, CommonJS (`require()` / `module.exports`)
- **Style:** `"use strict"` at the top of every file
- **Imports:** All `require()` calls go at the **top of the file** — never inline inside functions or callbacks
- **Naming:** camelCase for variables/functions, PascalCase for classes
- **Async:** Use `async/await` — avoid raw `.then()` chains
- **Logging:** Use the `makeLogger(module)` factory from `bot.js`. Format: `YYYY-MM-DD HH:MM:SS [LEVEL] module: message`
- **Discord interactions:** Always reply with `ephemeral: true` for command responses. Use `deferReply()` for anything that might take more than 3 seconds.
- **Error handling:** Wrap Discord operations in try/catch. Log errors with `logger.error()` and reply to the user with a human-readable message. Never leak stack traces to Discord.
- **No new dependencies** without explicit justification. The bot intentionally avoids heavy frameworks.

### Python (py-process/, py-query/)

- **Runtime:** Python 3.11+
- **Style:** Follow PEP 8. Use type hints on function signatures.
- **Imports:** Standard library → third-party → local, separated by blank lines
- **Naming:** snake_case for variables/functions, PascalCase for classes, UPPER_SNAKE_CASE for constants
- **Logging:** Use Python's `logging` module with the pattern: `logger = logging.getLogger(__name__)`
- **Docstrings:** Use triple-quote docstrings on all public functions and classes. Include parameter descriptions for non-obvious arguments.
- **Path handling:** Always use `pathlib.Path` — never string concatenation for paths
- **Config loading:** Read from `../config.yaml` (relative to script location) using `PyYAML`. Never hardcode paths or settings.
- **No vendor SDKs.** The provider layer in `py-query/providers.py` uses raw `requests` calls — no `openai`, `anthropic`, or `langchain` packages.

---

## Configuration & Secrets

### Rules

1. **Secrets** (`DISCORD_TOKEN`, `GUILD_ID`, API keys) go in `.env` — **never** in `config.yaml`, code, or logs
2. **Non-secret settings** go in `config.yaml` at the repo root
3. `.env` is listed in `.gitignore`. If you add a new secret variable:
   - Add a commented-out placeholder to `.env.example`
   - Document it in the root `README.md` Configuration section
   - Document it in the relevant sub-project README
4. If you add a new `config.yaml` field:
   - Add it to `config.yaml` with a sensible default and a comment explaining it
   - Document it in the root `README.md` Configuration table
   - Document it in the relevant sub-project README's Configuration Reference table
5. **Use generic paths in documentation and examples** — write `./recordings` not `D:/mlx-ai-recordings`

---

## Security Scrutiny Checklist

Apply this checklist whenever modifying code that touches secrets, user input, external APIs, or file I/O.

### Secrets & Credentials

- [ ] **No secrets in source.** API keys, tokens, and passwords must come from `.env` via `process.env` (JS) or `os.environ` / `dotenv` (Python) — never hardcoded or logged.
- [ ] **No secrets in logs.** Ensure `logger.info()`, `logger.debug()`, etc. never print tokens, API keys, or user credentials. Audit any new logging statement that touches auth-related variables.
- [ ] **No secrets in error messages.** Discord replies (`interaction.reply()`, `interaction.editReply()`) must never contain tokens, API keys, file system paths, or stack traces.
- [ ] **`.env.example` stays safe.** Only placeholder values (`your_bot_token_here`, `sk-...`) — never real credentials.

### User Input & Injection

- [ ] **Sanitize Discord input used in file paths.** The `sanitiseName()` function in `recorder.js` strips non-word characters. Any new code that uses Discord-provided strings (usernames, session names, channel names) in file paths **must** sanitize them.
- [ ] **No shell injection.** Python scripts are spawned via `child_process.spawn()` with argument arrays — never `exec()` or template-string shell commands. Maintain this pattern. In Python, `subprocess.run()` uses list arguments — never `shell=True`.
- [ ] **Validate slash command inputs.** Discord enforces types at the gateway level, but still check for unexpected `null`/`undefined` values before using them in file operations or subprocess arguments.
- [ ] **Validate config.yaml values.** Don't assume `config.yaml` is well-formed. Check for `undefined`/`None` and fall back to defaults.

### File System

- [ ] **No path traversal.** When constructing file paths from user input (session names, usernames), ensure the resolved path stays within the configured `output_directory`. Watch for `../` sequences that survive sanitization.
- [ ] **Respect `.gitignore` boundaries.** The `recordings/` directory, `.env`, `node_modules/`, `__pycache__/`, and `.venv/` are gitignored. Never commit these.
- [ ] **Temp file cleanup.** `recorder.js` writes `.pcm` temp files and deletes them after WAV conversion. Any new temp file pattern must clean up on both success and failure paths.

### Network & API Calls

- [ ] **API keys in headers only.** Cloud provider API keys are passed in HTTP headers (`Authorization: Bearer`, `x-api-key`) — never in query strings or request bodies.
- [ ] **Timeouts on HTTP requests.** Any new `requests.post()` or `fetch()` call should include a timeout to avoid hanging indefinitely.
- [ ] **No new outbound endpoints** without documenting them in the relevant README. The current outbound targets are: Discord Gateway, Discord REST API, Ollama (`localhost:11434`), OpenAI API, Anthropic API.

### Process Spawning

- [ ] **Spawn, never exec.** Use `spawn()` (JS) or `subprocess.run()` with a list (Python). Never use `exec()`, `execSync()`, `shell=True`, or backtick-interpolated shell strings.
- [ ] **Spawned processes inherit `process.env`.** This is intentional (API keys need to flow to Python). But never pass individual secrets as CLI arguments — they'd be visible in `ps` output.

---

## Documentation Requirements

Documentation is a **first-class deliverable** in this project. Every code change must include corresponding documentation updates.

### When to Update Documentation

| What changed | Update |
|---|---|
| New or modified slash command | Root `README.md` (Quick Start + Command Reference if present), `js-bot/README.md` (Slash Command Reference) |
| New or modified Python script / CLI args | `py-process/README.md` or `py-query/README.md` (Scripts section + args table) |
| New `config.yaml` field | `config.yaml` (comment), root `README.md` (Configuration table), relevant sub-project README |
| New `.env` variable | `.env.example`, root `README.md` (Configuration table), relevant sub-project README |
| New npm or pip dependency | `package.json` or `requirements.txt`, relevant sub-project README (Dependencies table) |
| New file added to the project | Root `README.md` (Project Structure tree), relevant sub-project README (File Descriptions table) |
| Bug fix or behaviour change | `CHANGELOG.md` under `[Unreleased]` |
| Any user-visible change | `CHANGELOG.md` under `[Unreleased]` |

### Documentation Prompts

Detailed formatting rules and update procedures live in:

- **`.github/prompts/updateReadme.prompt.md`** — Standardized README structure, cross-linking rules, formatting conventions
- **`.github/prompts/updateChangelog.prompt.md`** — Keep a Changelog v1.1.0 format with custom hash/HR structure

Reference these prompts when updating documentation. They are the source of truth for formatting.

### Documentation Style

- Use **GitHub-Flavored Markdown** (GFM)
- Provide **both Bash and PowerShell** command examples in the root README
- Use tables for structured data (dependencies, config fields, command parameters)
- Wrap inline references in backticks: `config.yaml`, `transcribe.py`, `/mlx-ai record start`
- Use relative links between READMEs: `../README.md`, `./py-process/README.md`
- Every sub-project README must have navigation links back to root and to sibling sub-projects

---

## Code Quality Standards

### Before Completing Any Change

1. **Imports are organized.** JS: all `require()` at file top. Python: stdlib → third-party → local.
2. **No unused imports or variables.** Remove dead code — don't comment it out.
3. **Functions have documentation.** JS: JSDoc with `@param` / `@returns`. Python: docstrings with parameter descriptions.
4. **Error paths are handled.** Every `await` on a Discord operation should be in a try/catch. Every `subprocess.run()` should check the return code.
5. **Concurrency guards are respected.** The bot enforces single-instance for recording, post-processing, merge, and vectorize. New operations that should not run concurrently must follow the same `isRunning` flag pattern.
6. **No hardcoded values.** Anything configurable goes in `config.yaml` with a default.
7. **Consistent logging.** Use the project's logging patterns — don't `console.log()` in JS or bare `print()` in Python (except for CLI output in `query.py`).

### Naming Conventions

| Context | Convention | Example |
|---|---|---|
| JS variables / functions | camelCase | `sessionName`, `mergeAudio()` |
| JS classes | PascalCase | `Recorder`, `PostProcessor` |
| Python variables / functions | snake_case | `session_name`, `merge_audio()` |
| Python classes | PascalCase | `OllamaChat`, `EmbeddingProvider` |
| Python constants | UPPER_SNAKE_CASE | `SAMPLE_RATE`, `FRAME_BYTES` |
| Config keys | snake_case | `output_directory`, `whisper_model` |
| Slash commands | kebab-case | `/mlx-ai merge-audio start` |
| Session folders | `YYYYMMDD_HHMMSS_<name>` | `20260330_143000_Campaign1` |

---

## Architecture Decisions to Preserve

These are intentional design choices. Don't change them without discussion.

1. **JS bot, not Python bot.** py-cord does not support Discord's DAVE/E2EE protocol. `discord.js` + `@discordjs/voice` does. This is not negotiable while Discord enforces DAVE.
2. **Spawn Python as child processes.** The JS bot calls Python scripts via `child_process.spawn()`. Do not rewrite the pipeline in JavaScript or embed a Python interpreter.
3. **No vendor LLM SDKs.** `providers.py` uses raw HTTP `requests` against Ollama, OpenAI, and Anthropic REST APIs. Do not add `openai`, `anthropic`, `langchain`, or similar SDK packages.
4. **Single ChromaDB collection** (`dnd_sessions`). All sessions share one collection with metadata filtering — not one collection per session.
5. **Single bot replica.** Discord's gateway is stateful. The bot must run as exactly one process — no horizontal scaling, no load balancers.
6. **Silence padding in recorder.** `SilencePadTransform` in `recorder.js` keeps per-user WAVs time-aligned. Removing it breaks multi-user transcript alignment.

---

## Common Pitfalls

- **Changing `embedding_provider` or `embedding_model`** after vectorizing breaks RAG queries. Always warn users to re-run `python py-process/vectorize.py --all --force` when these change.
- **Discord interaction timeouts.** You have 3 seconds to respond to an interaction. Use `deferReply()` for anything involving file I/O, spawning processes, or network calls.
- **Opus stream end behaviour.** `EndBehaviorType.Manual` is required for continuous recording. `AfterSilence` or `AfterInactivity` will stop streams prematurely during natural pauses in speech.
- **WAV header size limits.** WAV uses a 32-bit size field (max ~4 GB). Multi-hour recordings can exceed this. Monitor for extremely long sessions.
- **Python virtual environment detection.** `postProcessor.js` and `queryHandler.js` check for `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix) before falling back to system `python`. If the venv detection path changes, update both files.
