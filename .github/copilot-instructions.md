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

## Philosophy

- **Minimal dependencies.** Add new packages only when clearly justified. Prefer the standard library and existing tools.
- **Security by default.** Secrets in `.env` only. No shell injection. Sanitize all user input used in file paths. See [`.github/instructions/security.instructions.md`](.github/instructions/security.instructions.md).
- **Documentation is a deliverable.** Every code change ships with the corresponding README, CHANGELOG, and config updates. See [`.github/instructions/docs.instructions.md`](.github/instructions/docs.instructions.md).
- **Single bot replica.** Discord's gateway is stateful — never scale horizontally or add load balancers.
- **Spawn, never exec.** Python scripts are called via `child_process.spawn()` with argument arrays. No `shell=True` anywhere in the pipeline.

---

## Path-Based Instruction Files

Detailed, automatically-enforced rules live in `.github/instructions/`. Copilot applies each file only to the matching paths.

| File | Applies to | What it covers |
|---|---|---|
| [`js-bot.instructions.md`](.github/instructions/js-bot.instructions.md) | `js-bot/**` | Node.js conventions, Discord interaction rules, JS architecture decisions, common pitfalls |
| [`py-process.instructions.md`](.github/instructions/py-process.instructions.md) | `py-process/**` | Python conventions, pipeline architecture, processing pitfalls |
| [`py-query.instructions.md`](.github/instructions/py-query.instructions.md) | `py-query/**` | Python conventions, no-SDK rule, ChromaDB architecture, RAG pitfalls |
| [`docs.instructions.md`](.github/instructions/docs.instructions.md) | `**` | Documentation & configuration update requirements, style guide |
| [`security.instructions.md`](.github/instructions/security.instructions.md) | `**` | Security checklist: secrets, injection, file system, network, process spawning |

> For background on path-based Copilot review instructions, see the [GitHub docs](https://docs.github.com/en/copilot/using-github-copilot/github-copilot-in-the-cli-and-on-github/using-github-copilot-code-review/configuring-copilot-code-review#using-path-based-instructions).

---

## Where to Update Standards

| You want to change… | Edit this file |
|---|---|
| Repo-wide philosophy or architecture | This file (`copilot-instructions.md`) |
| JS bot coding rules | `.github/instructions/js-bot.instructions.md` |
| Python processing pipeline rules | `.github/instructions/py-process.instructions.md` |
| RAG query service rules | `.github/instructions/py-query.instructions.md` |
| Documentation / config / changelog rules | `.github/instructions/docs.instructions.md` |
| Security checklist | `.github/instructions/security.instructions.md` |
| README formatting or CHANGELOG format | `.github/prompts/updateReadme.prompt.md` / `.github/prompts/updateChangelog.prompt.md` |
