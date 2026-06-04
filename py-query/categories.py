#!/usr/bin/env python3
"""
categories.py — File-based store for session "categories" (Python side).

A category is a session "style" (D&D, work meeting, event planning, …). Each one is a
pair of files in the categories directory:
    <category_name>.json   — {category_name, display_name, collection_name}
    <category_name>.md     — the summary system prompt (markdown-output instructions)

This module is the Python counterpart of js-bot/categories.js. The Discord bot owns
writing categories; the processing (py-process) and query (py-query) services only read
them — to resolve a session's ChromaDB collection and load its summary prompt. Keep the
slug rules, default category, and file layout in sync with the JS module.

The module lives in py-query/ so it is importable by:
  - py-query/rag.py and py-query/app.py (same directory), and
  - py-process/vectorize.py and py-process/summarize.py, which already
    `sys.path.insert(0, str(... / "py-query"))`.
"""

import json
import re
import sys
from pathlib import Path

# Built-in category that reproduces the original D&D behaviour.
DEFAULT_CATEGORY = "dnd"
# Synthesized fallback used when even the dnd definition is missing on disk.
_DEFAULT_META = {
    "category_name": "dnd",
    "display_name": "D&D Session",
    "collection_name": "dnd_sessions",
}

# Repo root = two levels up from this file (py-query/categories.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent


def categories_dir(config: dict) -> Path:
    """Resolve the categories directory; relative paths are anchored to the repo root."""
    configured = (config or {}).get("categories_directory", "./categories")
    p = Path(configured)
    if not p.is_absolute():
        p = (_REPO_ROOT / p).resolve()
    return p


def slugify(raw: str) -> str:
    """Sanitize a raw category name into a filesystem-safe slug (mirrors the JS rule)."""
    slug = re.sub(r"[^\w-]", "_", str(raw or "").strip().lower())
    slug = slug.strip("_")
    return (slug or "category")[:64]


def list_categories(config: dict) -> list[dict]:
    """Return all defined categories, sorted by display name."""
    d = categories_dir(config)
    if not d.exists():
        return []
    cats = []
    for json_path in d.glob("*.json"):
        cat = load_category(config, json_path.stem)
        if cat:
            cats.append(cat)
    cats.sort(key=lambda c: (c.get("display_name") or c.get("category_name") or "").lower())
    return cats


def load_category(config: dict, name: str) -> dict | None:
    """Load one category's metadata, or None if it does not exist."""
    slug = slugify(name)
    json_path = categories_dir(config) / f"{slug}.json"
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "category_name": data.get("category_name") or slug,
        "display_name": data.get("display_name") or data.get("category_name") or slug,
        "collection_name": data.get("collection_name") or f"{slug}_sessions",
    }


def resolve_category(config: dict, name: str | None) -> dict:
    """
    Resolve a category name to its metadata, always returning a usable dict.

    Falls back to the built-in ``dnd`` category (and warns) when the requested category
    is missing — this keeps old sessions and old vectors working without migration.
    """
    if name:
        cat = load_category(config, name)
        if cat:
            return cat
        print(
            f"  Warning: category '{name}' not found — falling back to '{DEFAULT_CATEGORY}'.",
            file=sys.stderr,
        )
    return load_category(config, DEFAULT_CATEGORY) or dict(_DEFAULT_META)


def load_prompt(config: dict, name: str) -> str | None:
    """Load a category's summary prompt (.md contents), or None if missing."""
    slug = slugify(name)
    md_path = categories_dir(config) / f"{slug}.md"
    try:
        return md_path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_session_category(session_dir: Path) -> tuple[str, str | None]:
    """
    Read (category, subcategory) from a session's _session.metadata.json.

    Returns (DEFAULT_CATEGORY, None) when the file is missing or has no category — so
    pre-feature sessions resolve to the built-in dnd category.
    """
    meta_path = Path(session_dir) / "_session.metadata.json"
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_CATEGORY, None
    category = data.get("category") or DEFAULT_CATEGORY
    subcategory = data.get("subcategory") or None
    return category, subcategory
