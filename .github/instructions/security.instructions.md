---
applyTo: "**"
---

# Security — Copilot Instructions

Apply this checklist to every change that touches secrets, user input, external APIs, or file I/O.

## Secrets & Credentials

- [ ] **No secrets in source.** API keys, tokens, and passwords must come from `.env` via `process.env` (JS) or `os.environ` / `python-dotenv` (Python). Never hardcode or log them.
- [ ] **No secrets in logs.** Audit every new `logger.info()` / `logger.debug()` call that touches auth-related variables — they must not print tokens, API keys, or credentials.
- [ ] **No secrets in Discord replies.** `interaction.reply()` / `interaction.editReply()` must never contain tokens, API keys, file system paths, or stack traces.
- [ ] **`.env.example` stays safe.** Only placeholder values (`your_bot_token_here`, `sk-...`) — never real credentials.

## User Input & Injection

- [ ] **Sanitize Discord input used in file paths.** The `sanitiseName()` function in `recorder.js` strips non-word characters. Any new code that uses Discord-provided strings (usernames, session names, channel names) in file paths **must** call `sanitiseName()` or an equivalent sanitizer.
- [ ] **No path traversal.** When building paths from user input, ensure the resolved path stays inside the configured `output_directory`. Check for `../` sequences that could survive sanitization.
- [ ] **No shell injection.** JS: use `child_process.spawn()` with an argument array — never `exec()`, `execSync()`, or template-string shell commands. Python: use `subprocess.run()` with a list — never `shell=True`.
- [ ] **Validate slash command inputs.** Discord enforces types at the gateway, but still check for unexpected `null` / `undefined` before using values in file operations or subprocess arguments.
- [ ] **Validate `config.yaml` values.** Don't assume the config is well-formed. Check for `undefined` / `None` and fall back to safe defaults.

## File System

- [ ] **Respect `.gitignore` boundaries.** The `recordings/` directory, `.env`, `node_modules/`, `__pycache__/`, and `.venv/` are gitignored. Never commit these.
- [ ] **Temp file cleanup.** `recorder.js` deletes `.pcm` temp files after WAV conversion. Any new temp-file pattern must clean up on both success and failure paths.

## Network & API Calls

- [ ] **API keys in headers only.** Pass keys via `Authorization: Bearer` or `x-api-key` headers — never in query strings or request bodies.
- [ ] **Timeouts on HTTP requests.** Every new `requests.post()` / `fetch()` call must include an explicit timeout.
- [ ] **No undocumented outbound endpoints.** Current targets: Discord Gateway, Discord REST API, Ollama (`localhost:11434`), OpenAI API, Anthropic API. Document any new endpoint in the relevant README.

## Process Spawning

- [ ] **Spawn, never exec.** JS: `child_process.spawn()`. Python: `subprocess.run()` with a list. Never use `exec()`, `execSync()`, or `shell=True`.
- [ ] **Secrets flow via environment, not CLI args.** Spawned processes inherit `process.env` intentionally, but secrets must never be passed as positional or named CLI arguments (they appear in `ps` output).
