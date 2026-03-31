# Update CHANGELOG

## Purpose

This prompt is used to programmatically update the `CHANGELOG.md` file at the root of the MLX AI Discord Recorder project. Use it after completing a set of changes that should be documented.

## Format

The changelog follows [Keep a Changelog v1.1.0](https://keepachangelog.com/en/1.1.0/) with the following customizations:

1. **Horizontal rule (`---`)** before each version entry header (`##`)
2. **Git hash** between the version header and the first section header, formatted as:
   ```
   > _Hash: <full_40_char_commit_hash>_
   ```

## Structure

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

> _Hash: <current_HEAD_hash>_

### Added
- Description of new features

### Changed
- Description of changes to existing features

### Fixed
- Description of bug fixes

### Removed
- Description of removed features

---

## [x.y.z] - YYYY-MM-DD

> _Hash: <commit_hash_at_release>_

### Added
- ...
```

## Section Types (in this order when present)

- `### Added` — New features or capabilities
- `### Changed` — Changes to existing functionality
- `### Deprecated` — Features that will be removed in a future release
- `### Removed` — Features that were removed
- `### Fixed` — Bug fixes
- `### Security` — Security-related changes

Only include sections that have entries. Do not include empty sections.

## Changelog Entry Guidelines

- Start each entry with a **dash and space** (`- `)
- Begin with the **component or area** in bold if it helps clarity (e.g., `- **js-bot/** — added...`)
- Keep entries concise but informative — one to two sentences max
- Group related changes into a single entry when they are part of the same feature
- Don't go too deep into implementation details — focus on what changed from a user/developer perspective
- Reference file names or commands in backticks: `transcribe.py`, `/mlx-ai record start`

## Git Hash Management

### The `[Unreleased]` Hash

The `[Unreleased]` section always has a hash representing the **last known commit state** when the changelog was last updated. This serves as a reference point for generating diffs.

### How to Use Hashes for Diffs

To understand what changed between changelog updates:

```bash
# Compare the [Unreleased] hash to current HEAD
git diff <unreleased_hash> HEAD

# Compare two released versions
git diff <older_version_hash> <newer_version_hash>
```

### Update Process

When updating the changelog:

1. **Read the current `CHANGELOG.md`** to find the latest hash
2. **Determine the diff range:**
   - If `[Unreleased]` exists and has a hash → use that as the old hash
   - If `[Unreleased]` does not exist → use the latest versioned entry's hash
3. **Run the diff** to see what changed:
   ```bash
   git diff <old_hash> HEAD
   ```
4. **Update the `[Unreleased]` section** with new entries based on the diff
5. **Update the `[Unreleased]` hash** to the current `HEAD` commit hash:
   ```bash
   git rev-parse HEAD
   ```

### When Creating a Release

When cutting a release version:

1. Rename `[Unreleased]` to `[x.y.z] - YYYY-MM-DD`
2. Keep the hash that was in `[Unreleased]` — it now belongs to the release
3. Add a new `[Unreleased]` section above with the current `HEAD` hash
4. The new `[Unreleased]` section starts empty (no subsections until there are changes)

## Example: Full Changelog

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

> _Hash: abc123def456789..._

### Added
- New feature X

---

## [1.0.0] - 2026-03-30

> _Hash: 138a862583563980dfd8e98425f5da902bd938d2_

### Added
- Initial release with recording, transcription, and RAG query support

### Fixed
- Voice connection DAVE/E2EE compatibility
```

## Reminders

- Always use the **full 40-character commit hash**, not the short form
- The date format is `YYYY-MM-DD`
- Keep the preamble text (`# Changelog`, format note) exactly as-is
- The `---` horizontal rule goes **before** each `## [version]` header
- After updating, verify the hash is correct by running `git rev-parse HEAD`
