#!/usr/bin/env python3
"""
summarize.py — Generate a session summary using an LLM and store it in ChromaDB.

Reads _combined_transcript.txt for a session, looks up the session's category (from
_session.metadata.json), loads that category's summary prompt, sends the transcript to the
configured chat provider, and writes the model's Markdown response straight to
_session_summary.md.

The summary format is fully controlled by the category's editable prompt (see
categories/<name>.md), so different session styles (D&D, work meeting, event planning, …)
produce different summaries. The summary text is also embedded as one chunk in the
category's ChromaDB collection for cross-session RAG.

For long transcripts that exceed ``summary_max_input_tokens``, a map-reduce strategy is
used: each chunk is reduced to concise Markdown notes (map), then all notes are combined
into one final summary using the category prompt (reduce). Short transcripts use a single
pass.

Usage:
    python summarize.py <session_name>
    python summarize.py --all

Examples:
    python summarize.py 20260330_143000_Campaign1_Session4
    python summarize.py --all
"""

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Path bootstrap — allow importing providers.py / categories.py from py-query/
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "py-query"))

from providers import get_chat_provider, get_embedding_provider  # noqa: E402
from categories import load_prompt, read_session_category, resolve_category  # noqa: E402
from chroma_client import get_chroma_client  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-process/)."""
    config_path = _REPO_ROOT / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Ollama's default context window is small (~4–8K tokens). Warn users when the
# transcript is large enough that results may be silently truncated by the model.
_OLLAMA_CONTEXT_WARN_CHARS = 32_000   # ≈ 8K tokens at 4 chars/token

# Map-phase instruction: turn one transcript section into concise notes. The final
# formatting is applied by the category prompt during the reduce phase.
_MAP_NOTES_INSTRUCTION = (
    "You are summarizing ONE section of a longer session transcript. Produce concise "
    "Markdown bullet notes capturing the key events, decisions, names, places, items, and "
    "noteworthy moments in this section, including approximate timestamps where helpful. "
    "Do not write a title or top-level headings — output only the bullet notes for this section."
)

# Used only when a category's prompt file is missing on disk.
_FALLBACK_PROMPT = (
    "You are a session summarizer. Given a session transcript, produce a clear, "
    "well-structured summary in GitHub-flavored Markdown. Begin with a top-level heading "
    "naming the session, then a narrative overview, followed by the key points, decisions, "
    "and notable moments. Output only the Markdown document — no code fences."
)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

# Matches lines like: [00:00:01.200 --> 00:00:04.500]  username: text
_LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s+(\S+?):\s+(.+)$"
)


def read_transcript(path: Path) -> tuple[str, list[str]]:
    """
    Read _combined_transcript.txt and return the raw text and sorted list of speaker names.

    Returns:
        Tuple of (transcript_text, speakers) where transcript_text is the full file content
        and speakers is a sorted list of unique usernames found in the transcript.
    """
    speakers: set[str] = set()
    lines: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            m = _LINE_RE.match(line)
            if m:
                _, _, username, _ = m.groups()
                speakers.add(username)
            if line:
                lines.append(line)
    return "\n".join(lines), sorted(speakers)


# ---------------------------------------------------------------------------
# LLM response helpers
# ---------------------------------------------------------------------------

def _strip_markdown_fences(raw: str) -> str:
    """
    Strip an outer ``` fence that some models wrap the whole Markdown document in.

    Only removes a leading ```/```markdown line and the matching trailing ``` when the
    entire response is wrapped — inline/code fences inside the summary are left intact.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    return text


# ---------------------------------------------------------------------------
# Transcript chunking
# ---------------------------------------------------------------------------

def _chunk_transcript(text: str, max_chars: int) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars* characters, splitting only on
    newline boundaries so that no transcript line is cut mid-sentence.

    Returns:
        List of non-empty chunk strings.
    """
    if max_chars <= 0:
        return [text]

    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line)
        # If a single line exceeds max_chars, it must be kept as its own chunk.
        if current_len + line_len > max_chars and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append("".join(current))

    return [c for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# LLM summarization — single-pass and map-reduce
# ---------------------------------------------------------------------------

def _call_provider(provider, system_prompt: str, user_message: str) -> str:
    """Call the chat provider and return the raw response string."""
    return provider.chat(system_prompt, user_message)


def _map_chunk(chunk: str, chunk_idx: int, total_chunks: int, provider) -> str:
    """Reduce a single transcript chunk to concise Markdown notes (map phase)."""
    print(
        f"  Map chunk {chunk_idx + 1}/{total_chunks} ({len(chunk):,} chars)…",
        end=" ",
        flush=True,
    )
    t0 = time.time()
    raw = _call_provider(provider, _MAP_NOTES_INSTRUCTION, chunk)
    print(f"done ({time.time() - t0:.1f}s)")
    return _strip_markdown_fences(raw)


def _reduce_notes(
    notes: list[str],
    provider,
    category_prompt: str,
    session_name: str,
) -> str:
    """Combine per-section notes into one final summary using the category prompt (reduce)."""
    combined = "\n\n".join(
        f"--- Section {i + 1} ---\n{n}" for i, n in enumerate(notes)
    )
    user_message = (
        f"Session name: {session_name}\n\n"
        "The following are notes captured from sequential sections of a single session "
        "transcript, in order. Combine them into one cohesive summary by following the "
        "instructions above.\n\n"
        f"{combined}"
    )
    print(
        f"  Reduce: combining {len(notes)} section note(s) ({len(combined):,} chars)…",
        end=" ",
        flush=True,
    )
    t0 = time.time()
    raw = _call_provider(provider, category_prompt, user_message)
    print(f"done ({time.time() - t0:.1f}s)")
    return _strip_markdown_fences(raw)


def generate_summary(
    transcript_text: str,
    config: dict,
    category_prompt: str,
    session_name: str,
) -> str:
    """
    Send the transcript to the configured chat provider and return the Markdown summary.

    **Short transcripts** (≤ ``summary_max_input_tokens × 4`` characters) are sent in a
    single pass with the category prompt. **Long transcripts** use map-reduce: each chunk is
    reduced to notes (map), then all notes are combined with the category prompt (reduce).

    Args:
        transcript_text: Full text of _combined_transcript.txt
        config:          Parsed config.yaml dict
        category_prompt: The category's summary prompt (system prompt)
        session_name:    Session folder name (passed to the model for the title)

    Returns:
        The finished Markdown summary string.
    """
    summary_max_input_tokens = int(config.get("summary_max_input_tokens", 32000))
    max_input_chars = summary_max_input_tokens * 4  # 1 token ≈ 4 characters

    chat_provider_name = config.get("chat_provider", "ollama")
    chat_label = f"{chat_provider_name} ({config.get('chat_model', 'llama3')})"

    # Ollama context-window advisory
    if chat_provider_name == "ollama" and len(transcript_text) > _OLLAMA_CONTEXT_WARN_CHARS:
        print(
            f"  Warning: Transcript is {len(transcript_text):,} chars "
            f"(~{len(transcript_text) // 4:,} tokens). Ollama defaults to a small context "
            f"window (often 4–8K tokens). If summaries are incomplete, add "
            f"`PARAMETER num_ctx {summary_max_input_tokens}` to your Ollama model's Modelfile or "
            f"use a cloud provider instead.",
            file=sys.stderr,
        )

    provider = get_chat_provider(config)

    # ------------------------------------------------------------------
    # Single-pass path (transcript fits within the configured input limit)
    # ------------------------------------------------------------------
    if len(transcript_text) <= max_input_chars:
        user_message = f"Session name: {session_name}\n\nTranscript:\n{transcript_text}"
        print(f"  Sending transcript to {chat_label} (single pass)…", end=" ", flush=True)
        t0 = time.time()
        raw = _call_provider(provider, category_prompt, user_message)
        print(f"done ({time.time() - t0:.1f}s)")
        return _strip_markdown_fences(raw)

    # ------------------------------------------------------------------
    # Map-reduce path (transcript is too long for a single call)
    # ------------------------------------------------------------------
    chunks = _chunk_transcript(transcript_text, max_input_chars)
    print(
        f"  Transcript ({len(transcript_text):,} chars) exceeds {max_input_chars:,}-char limit — "
        f"using map-reduce over {len(chunks)} chunk(s)."
    )
    print(f"  Provider: {chat_label}")

    notes = [
        _map_chunk(chunk, idx, len(chunks), provider)
        for idx, chunk in enumerate(chunks)
    ]

    return _reduce_notes(notes, provider, category_prompt, session_name)


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def get_chroma_collection(config: dict, collection_name: str):
    """Return (or create) a ChromaDB collection by name."""
    client = get_chroma_client(config)
    return client.get_or_create_collection(collection_name)


def store_summary_chunk(
    session_name: str,
    summary_md: str,
    collection,
    config: dict,
    category: str,
    subcategory: str | None,
) -> None:
    """
    Embed the summary Markdown as a single ``type=summary`` chunk in the category collection.

    Metadata: {type, session_name, category, subcategory}.
    """
    text = (summary_md or "").strip()
    if not text:
        return

    embedding_provider = get_embedding_provider(config)
    print("  Embedding summary…", end=" ", flush=True)
    t0 = time.time()
    emb = embedding_provider.embed(text)
    print(f"done ({time.time() - t0:.1f}s)")

    collection.upsert(
        ids=[f"{session_name}__summary"],
        embeddings=[emb],
        documents=[text],
        metadatas=[
            {
                "type": "summary",
                "session_name": session_name,
                "category": category,
                "subcategory": subcategory or "",
            }
        ],
    )
    print(f"  Stored summary in ChromaDB collection '{collection.name}'.")


# ---------------------------------------------------------------------------
# Core per-session function
# ---------------------------------------------------------------------------

def summarize_session(session_name: str, session_dir: Path, config: dict) -> None:
    """
    Generate a Markdown session summary for one session and store it in ChromaDB.

    Reads _combined_transcript.txt and the session's category, calls the configured LLM chat
    provider with that category's prompt, writes _session_summary.md, then upserts the summary
    into the category's ChromaDB collection.
    """
    combined_path = session_dir / "_combined_transcript.txt"
    if not combined_path.exists():
        print(
            f"  Error: _combined_transcript.txt not found in {session_dir}",
            file=sys.stderr,
        )
        return

    transcript_text, speakers = read_transcript(combined_path)
    if not transcript_text.strip():
        print(
            f"  Warning: _combined_transcript.txt is empty for {session_name}",
            file=sys.stderr,
        )
        return

    # Resolve the session's category → prompt + collection
    category_name, subcategory = read_session_category(session_dir)
    category = resolve_category(config, category_name)
    category_prompt = load_prompt(config, category["category_name"]) or _FALLBACK_PROMPT

    print(
        f"  Category: {category['category_name']} "
        f"(collection: {category['collection_name']}"
        f"{f', sub-category: {subcategory}' if subcategory else ''})"
    )
    print(f"  Transcript: {len(transcript_text)} chars, {len(speakers)} speaker(s)")

    # Generate the Markdown summary via the LLM
    summary_md = generate_summary(transcript_text, config, category_prompt, session_name)

    # Write the summary markdown
    md_path = session_dir / "_session_summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(summary_md.rstrip() + "\n")
    print(f"  Written: {md_path.name}")

    # Store the summary in the category's ChromaDB collection
    collection = get_chroma_collection(config, category["collection_name"])
    store_summary_chunk(
        session_name,
        summary_md,
        collection,
        config,
        category["category_name"],
        subcategory,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the summarize.py CLI."""
    parser = argparse.ArgumentParser(
        description="Generate a Markdown session summary and store it in ChromaDB."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "session",
        nargs="?",
        help="Session folder name (e.g. 20260330_033020_test)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Summarize every session folder in the output directory.",
    )
    args = parser.parse_args()

    config = load_config()
    output_dir = Path(config.get("output_directory", "./recordings"))

    if args.all:
        if not output_dir.exists():
            print(f"Error: Output directory not found: {output_dir}", file=sys.stderr)
            sys.exit(1)
        sessions = sorted([d.name for d in output_dir.iterdir() if d.is_dir()])
        if not sessions:
            print(f"No session folders found in {output_dir}", file=sys.stderr)
            sys.exit(0)
    else:
        sessions = [args.session]

    print(f"Sessions:  {len(sessions)}")
    print()

    for session_name in sessions:
        session_dir = output_dir / session_name
        if not session_dir.exists():
            print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
            if not args.all:
                sys.exit(1)
            continue

        print(f"Session: {session_name}")
        summarize_session(
            session_name=session_name,
            session_dir=session_dir,
            config=config,
        )
        print()

    print("Done.")


if __name__ == "__main__":
    main()
