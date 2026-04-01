---
applyTo: "**"
---

# Documentation & Configuration — Copilot Instructions

## Documentation Is a First-Class Deliverable

Every code change must include the corresponding documentation updates listed in the table below.

| What changed | Required updates |
|---|---|
| New or modified slash command | Root `README.md` (Quick Start + Command Reference), `js-bot/README.md` (Slash Command Reference) |
| New or modified Python script / CLI args | `py-process/README.md` or `py-query/README.md` (Scripts section + args table) |
| New `config.yaml` field | `config.yaml` (inline comment), root `README.md` (Configuration table), relevant sub-project README |
| New `.env` variable | `.env.example` (placeholder only), root `README.md` (Configuration table), relevant sub-project README |
| New npm or pip dependency | `package.json` / `requirements.txt`, relevant sub-project README (Dependencies table) |
| New file added to the project | Root `README.md` (Project Structure tree), relevant sub-project README (File Descriptions table) |

## Configuration & Secrets Rules

1. **Secrets** (`DISCORD_TOKEN`, `GUILD_ID`, API keys) go in `.env` — **never** in `config.yaml`, source code, or logs.
2. **Non-secret settings** go in `config.yaml` at the repo root.
3. `.env` is gitignored. For any new secret variable:
   - Add a commented-out placeholder to `.env.example` (e.g., `# DISCORD_TOKEN=your_bot_token_here`)
   - Document it in the root `README.md` Configuration section
   - Document it in the relevant sub-project README
4. For any new `config.yaml` field:
   - Add it with a sensible default and an inline comment explaining it
   - Document it in the root `README.md` Configuration table
   - Document it in the relevant sub-project README's Configuration Reference table
5. **Use generic paths** in all documentation and examples — write `./recordings`, not `D:/mlx-ai-recordings`.

## Documentation Style

- Use **GitHub-Flavored Markdown** (GFM).
- Provide **both Bash and PowerShell** command examples in the root README.
- Use tables for structured data (dependencies, config fields, command parameters).
- Wrap inline file/command references in backticks: `config.yaml`, `transcribe.py`, `/mlx-ai record start`.
- Use relative links between READMEs: `../README.md`, `./py-process/README.md`.
- Every sub-project README must include navigation links back to root and to sibling sub-projects.

## Documentation Prompt Reference

Detailed formatting rules and update procedures live in:

- **`.github/prompts/updateReadme.prompt.md`** — Standardized README structure, cross-linking rules, formatting conventions

Always reference `.github/prompts/updateReadme.prompt.md` (the update README prompt) when updating `README.md` files; it is the source of truth for formatting.
