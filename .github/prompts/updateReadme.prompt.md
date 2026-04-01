# Update README Files

## Purpose

This prompt is used to programmatically update the README.md files throughout the MLX AI Discord Recorder project. Use it whenever you need to update documentation after making code changes.

## README Locations

The project has **four** README files that must be kept in sync:

| File | Scope |
|------|-------|
| `/README.md` | Root — full project overview, installation, configuration |
| `/js-bot/README.md` | Discord bot — slash commands, behaviour, JS dependencies |
| `/py-process/README.md` | Processing pipeline — transcription, merging, vectorization |
| `/py-query/README.md` | RAG query service — CLI usage, provider configuration |

## Standardized Structure

### Root README (`/README.md`)

Must include the following sections **in this order**:

1. **Title & Description** — One-line project summary
2. **Table of Contents** — Links to all sections
3. **Features** — Bulleted list with emoji prefixes
4. **Architecture Overview** — ASCII diagram of the system
5. **Prerequisites** — Table of required software with install links
   - Installation commands for **both Bash and PowerShell**
6. **Installation** — Step-by-step setup
   - Clone, .env setup, config review, npm install, pip install
   - **Both Bash and PowerShell** commands for every step
7. **Configuration** — Tables for `.env` and `config.yaml` fields
8. **Quick Start** — Minimal steps to get running
9. **Discord Developer Portal Setup** — Step-by-step bot setup
10. **AI Pipeline** — Visual flow diagram + table of steps
11. **Project Structure** — Directory tree with file descriptions
12. **Sub-Project Documentation** — Table linking to sub-READMEs
13. **License**

### Sub-Project READMEs (`/js-bot/`, `/py-process/`, `/py-query/`)

Must include the following sections **in this order**:

1. **Title & Description** — One-line description of this sub-project
2. **Navigation Links** — Back link to root README + links to sibling sub-projects
   - Format: `> **↩️ Back to [main README](../README.md)** | See also: [other](../other/README.md)`
3. **Table of Contents**
4. **Overview** (if needed) — What this component does
5. **Prerequisites** — With links to main README for shared prereqs
6. **Installation** — With both Bash and PowerShell commands
7. **Script/Command Reference** — Each script or command group gets:
   - Description of what it does
   - Usage syntax
   - **Terminal example** (actual CLI command)
   - **Discord example** (actual slash command, if applicable)
   - Arguments/parameters table
8. **Configuration Reference** — Table of relevant `config.yaml` fields
9. **Output Structure** (if applicable) — Directory tree showing output files
10. **Dependencies** — Table with package name, version, and purpose
11. **File Descriptions** — Table describing each source file

## Formatting Rules

- Use **GitHub-Flavored Markdown** (GFM)
- Use `---` horizontal rules between major sections
- Use tables for structured data (dependencies, config fields, commands)
- Use fenced code blocks with language hints (`bash`, `powershell`, `yaml`, `python`, etc.)
- Terminal commands: provide **both Bash and PowerShell** variants in the root README; sub-project READMEs should include both where the commands differ
- Use emoji sparingly and only in the Features list
- Wrap inline code references in backticks: `config.yaml`, `transcribe.py`, etc.
- Use relative links for cross-referencing between READMEs (e.g. `../README.md`, `./py-process/README.md`)
- Section headers should use `##` for top-level and `###` for sub-sections

## Cross-Linking Rules

- Every sub-project README must link back to the root README
- Every sub-project README must link to sibling sub-project READMEs
- Root README must have a "Sub-Project Documentation" table linking to all sub-READMEs
- When referencing a script from another sub-project, link to its specific section:
  - `[vectorize.py](../py-process/README.md#vectorizepy--transcript-vectorization)`

## Content Guidelines

- **Be comprehensive** — document every command, argument, config field, and dependency
- **Show don't tell** — include terminal examples and Discord command examples
- **Explain "why"** — not just "what" (e.g., why JavaScript instead of Python for the bot)
- **Keep config examples generic** — use `./recordings` not `D:/mlx-ai-recordings` in examples
- **Document defaults** — every config field should show its default value
- **Include warnings** — especially for the embedding provider consistency requirement

## Update Process

When updating READMEs after code changes:

1. **Read the changed files** to understand what's new or modified
2. **Check all four READMEs** for sections that need updating
3. **Update the root README** first (it's the source of truth for project-wide info)
4. **Update affected sub-project READMEs** for detailed changes
5. **Verify cross-links** still point to valid section anchors
