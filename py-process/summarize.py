#!/usr/bin/env python3
"""
summarize.py — Generate structured session summaries using an LLM and store them in ChromaDB.

Reads _combined_transcript.txt for a session, sends it to the configured chat provider,
and generates a structured summary with key moments, NPCs, locations, and items.

Outputs (written to the session folder):
  _session_summary.json  — Structured data (narrative, key moments, entities)
  _session_summary.md    — Human-readable markdown

Also stores the summary narrative and each key moment as ChromaDB chunks for
improved cross-session RAG query quality.

For long transcripts that exceed the configured ``summary_max_input_tokens`` limit, a
map-reduce strategy is used: the transcript is split into chunks, each chunk is summarised
independently (map), then all partial summaries are combined into a single final summary
(reduce).  Short transcripts that fit within the limit use a single-pass approach.

Usage:
    python summarize.py <session_name>
    python summarize.py --all

Examples:
    python summarize.py 20260330_143000_Campaign1_Session4
    python summarize.py --all
"""

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Path bootstrap — allow importing providers.py from py-query/
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "py-query"))

import chromadb  # noqa: E402
from providers import get_chat_provider, get_embedding_provider  # noqa: E402

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


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

def _build_system_prompt(summary_max_tokens: int) -> str:
    """
    Build the system prompt for a full (single-pass or reduce) summarization request.

    Args:
        summary_max_tokens: Approximate token budget for the LLM response.
    """
    return f"""\
You are a session chronicler for a tabletop role-playing game (TTRPG). You will be given a \
full session transcript and must produce a structured JSON summary.

Your response MUST be valid JSON (no markdown fences, no extra text) matching this schema exactly:

{{
  "narrative_summary": "<2-4 paragraph narrative summary of the session>",
  "key_moments": [
    {{
      "timestamp": <float seconds from session start, or 0 if unknown>,
      "description": "<one sentence description>",
      "category": "<one of: combat, plot_reveal, npc_introduction, funny_moment, decision_point>",
      "context": "<1-2 sentences of surrounding context>"
    }}
  ],
  "npcs": [
    {{"name": "<name>", "context": "<brief description>"}}
  ],
  "locations": [
    {{"name": "<name>", "context": "<brief description>"}}
  ],
  "items": [
    {{"name": "<name>", "context": "<brief description>"}}
  ]
}}

Rules:
- Aim to keep your total response under approximately {summary_max_tokens} tokens.
- Include 3–10 key moments. Focus on the most impactful events.
- Key moment categories: combat, plot_reveal, npc_introduction, funny_moment, decision_point
- List only NPCs, locations, and items that were meaningfully discussed.
- Timestamps: use the start time in seconds from the transcript line nearest to the event. \
Use 0 if unknown.
- Output ONLY the JSON object. Do not include markdown code fences or any other text.
"""


def _build_chunk_system_prompt() -> str:
    """
    Build the system prompt used in the **map** phase of map-reduce summarization.

    Each transcript chunk is summarised independently with this prompt.  The resulting
    partial summaries are later merged by a reduce call using _build_system_prompt().
    """
    return """\
You are a session chronicler for a tabletop role-playing game (TTRPG). You will be given a \
PARTIAL section of a longer session transcript and must produce a structured JSON partial summary.

Your response MUST be valid JSON (no markdown fences, no extra text) matching this schema exactly:

{
  "partial_narrative": "<1-2 paragraph narrative summary of events in this section>",
  "key_moments": [
    {
      "timestamp": <float seconds from session start, or 0 if unknown>,
      "description": "<one sentence description>",
      "category": "<one of: combat, plot_reveal, npc_introduction, funny_moment, decision_point>",
      "context": "<1-2 sentences of surrounding context>"
    }
  ],
  "npcs": [
    {"name": "<name>", "context": "<brief description>"}
  ],
  "locations": [
    {"name": "<name>", "context": "<brief description>"}
  ],
  "items": [
    {"name": "<name>", "context": "<brief description>"}
  ]
}

Rules:
- Include up to 5 key moments from this section only. Focus on the most impactful events.
- Key moment categories: combat, plot_reveal, npc_introduction, funny_moment, decision_point
- List only NPCs, locations, and items that appear in this section.
- Timestamps: use the start time in seconds from the transcript line nearest to the event.
- Output ONLY the JSON object. Do not include markdown code fences or any other text.
"""


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

# Matches lines like: [00:00:01.200 --> 00:00:04.500]  username: text
_LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s+(\S+?):\s+(.+)$"
)


def _ts_to_seconds(ts: str) -> float:
    """Convert HH:MM:SS.mmm timestamp string to seconds as a float."""
    h, m, rest = ts.split(":")
    s, ms = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def read_transcript(path: Path) -> tuple[str, list[str]]:
    """
    Read _combined_transcript.txt and return the raw text and sorted list of speaker names.

    Args:
        path: Path to _combined_transcript.txt

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
# LLM response parsing helpers
# ---------------------------------------------------------------------------

def _strip_markdown_fences(raw: str) -> str:
    """Strip accidental markdown code fences that some models add despite instructions."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()
    return raw


def _parse_json_response(raw: str, context: str = "") -> dict:
    """
    Parse an LLM JSON response, exiting with a descriptive error on failure.

    Args:
        raw:     Raw text returned by the LLM.
        context: Optional description of where this response came from (for error messages).
    """
    raw = _strip_markdown_fences(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        label = f" ({context})" if context else ""
        print(f"  Error: LLM returned invalid JSON{label}: {exc}", file=sys.stderr)
        print(f"  Raw response (first 500 chars): {raw[:500]}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Transcript chunking
# ---------------------------------------------------------------------------

def _chunk_transcript(text: str, max_chars: int) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars* characters, splitting only on
    newline boundaries so that no transcript line is cut mid-sentence.

    Args:
        text:      Full transcript text.
        max_chars: Maximum number of characters per chunk.

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


def _map_chunk(
    chunk: str,
    chunk_idx: int,
    total_chunks: int,
    provider,
) -> dict:
    """
    Summarise a single transcript chunk (map phase).

    Args:
        chunk:        Partial transcript text for this chunk.
        chunk_idx:    0-based chunk index (for progress display).
        total_chunks: Total number of chunks (for progress display).
        provider:     Instantiated chat provider.

    Returns:
        Parsed partial summary dict (keys: partial_narrative, key_moments, npcs,
        locations, items).
    """
    system_prompt = _build_chunk_system_prompt()
    print(
        f"  Map chunk {chunk_idx + 1}/{total_chunks} "
        f"({len(chunk):,} chars)…",
        end=" ",
        flush=True,
    )
    t0 = time.time()
    raw = _call_provider(provider, system_prompt, chunk)
    elapsed = time.time() - t0
    print(f"done ({elapsed:.1f}s)")
    return _parse_json_response(raw, context=f"chunk {chunk_idx + 1}")


def _reduce_chunks(
    partial_summaries: list[dict],
    provider,
    summary_max_tokens: int,
) -> dict:
    """
    Combine all partial chunk summaries into a single final summary (reduce phase).

    The partial summaries are serialised as JSON and fed back to the LLM with the
    full structured output prompt so the result conforms to the final schema.

    Args:
        partial_summaries: List of dicts returned by _map_chunk().
        provider:          Instantiated chat provider.
        summary_max_tokens: Token budget hint for the LLM response.

    Returns:
        Final summary dict matching the schema defined in _build_system_prompt().
    """
    combined_text = json.dumps(
        {
            "instruction": (
                "The following are partial summaries from sequential sections of a single "
                "TTRPG session transcript. Combine them into one cohesive final summary."
            ),
            "partial_summaries": partial_summaries,
        },
        indent=2,
        ensure_ascii=False,
    )
    system_prompt = _build_system_prompt(summary_max_tokens)
    print(
        f"  Reduce: combining {len(partial_summaries)} partial summaries "
        f"({len(combined_text):,} chars)…",
        end=" ",
        flush=True,
    )
    t0 = time.time()
    raw = _call_provider(provider, system_prompt, combined_text)
    elapsed = time.time() - t0
    print(f"done ({elapsed:.1f}s)")
    return _parse_json_response(raw, context="reduce")


def generate_summary(transcript_text: str, config: dict) -> dict:
    """
    Send the transcript to the configured chat provider and return the parsed JSON summary.

    **Short transcripts** (≤ ``summary_max_input_tokens × 4`` characters) are sent in a
    single pass.  **Long transcripts** are processed with a map-reduce strategy: the text is
    split into non-overlapping chunks on newline boundaries, each chunk is summarised
    independently (map), and all partial summaries are combined into one final structured
    output (reduce).

    An Ollama-specific warning is emitted when the transcript exceeds 32 K characters
    (~8 K tokens) to remind users that Ollama's default context window is small.

    Args:
        transcript_text: Full text of _combined_transcript.txt
        config: Parsed config.yaml dict

    Returns:
        Parsed summary dict matching the JSON schema defined in _build_system_prompt()
    """
    summary_max_tokens = int(config.get("summary_max_tokens", 2000))
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
        system_prompt = _build_system_prompt(summary_max_tokens)
        print(f"  Sending transcript to {chat_label} (single pass)…", end=" ", flush=True)
        t0 = time.time()
        raw = _call_provider(provider, system_prompt, transcript_text)
        print(f"done ({time.time() - t0:.1f}s)")
        return _parse_json_response(raw, context="single pass")

    # ------------------------------------------------------------------
    # Map-reduce path (transcript is too long for a single call)
    # ------------------------------------------------------------------
    chunks = _chunk_transcript(transcript_text, max_input_chars)
    print(
        f"  Transcript ({len(transcript_text):,} chars) exceeds {max_input_chars:,}-char limit — "
        f"using map-reduce over {len(chunks)} chunk(s)."
    )
    print(f"  Provider: {chat_label}")

    partial_summaries = [
        _map_chunk(chunk, idx, len(chunks), provider)
        for idx, chunk in enumerate(chunks)
    ]

    return _reduce_chunks(partial_summaries, provider, summary_max_tokens)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_json_summary(data: dict, path: Path) -> None:
    """Write the structured summary dict to _session_summary.json."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _seconds_to_hms(seconds: float) -> str:
    """Format a seconds value as HH:MM:SS for human-readable display."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


_CATEGORY_EMOJI: dict[str, str] = {
    "combat": "⚔️",
    "plot_reveal": "🔮",
    "npc_introduction": "🧙",
    "funny_moment": "😄",
    "decision_point": "🎲",
}


def write_markdown_summary(data: dict, session_name: str, path: Path) -> None:
    """
    Write a human-readable markdown summary to _session_summary.md.

    Args:
        data:         Parsed summary dict returned by generate_summary()
        session_name: Session folder name used as the document title
        path:         Destination file path
    """
    lines: list[str] = []

    lines.append(f"# Session Summary — {session_name}")
    lines.append("")

    # Narrative summary
    lines.append("## Summary")
    lines.append("")
    lines.append(data.get("narrative_summary", "_No summary available._"))
    lines.append("")

    # Key moments
    key_moments = data.get("key_moments", [])
    if key_moments:
        lines.append("## Key Moments")
        lines.append("")
        for km in key_moments:
            emoji = _CATEGORY_EMOJI.get(km.get("category", ""), "•")
            ts = _seconds_to_hms(float(km.get("timestamp", 0)))
            cat = km.get("category", "").replace("_", " ").title()
            lines.append(f"- **[{ts}]** {emoji} _{cat}_ — {km.get('description', '')}")
            ctx = km.get("context", "")
            if ctx:
                lines.append(f"  > {ctx}")
        lines.append("")

    # NPCs
    npcs = data.get("npcs", [])
    if npcs:
        lines.append("## NPCs")
        lines.append("")
        for npc in npcs:
            lines.append(f"- **{npc.get('name', '')}** — {npc.get('context', '')}")
        lines.append("")

    # Locations
    locations = data.get("locations", [])
    if locations:
        lines.append("## Locations")
        lines.append("")
        for loc in locations:
            lines.append(f"- **{loc.get('name', '')}** — {loc.get('context', '')}")
        lines.append("")

    # Items
    items = data.get("items", [])
    if items:
        lines.append("## Items & Artifacts")
        lines.append("")
        for item in items:
            lines.append(f"- **{item.get('name', '')}** — {item.get('context', '')}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

COLLECTION_NAME = "dnd_sessions"


def get_chroma_collection(vector_db_directory: str):
    """Return (or create) the persistent ChromaDB collection."""
    client = chromadb.PersistentClient(path=vector_db_directory)
    return client.get_or_create_collection(COLLECTION_NAME)


def vectorize_summary(
    session_name: str,
    data: dict,
    speakers: list[str],
    collection,
    config: dict,
) -> None:
    """
    Store the narrative summary and each key moment as ChromaDB chunks.

    Documents added:
      - One ``type=summary`` chunk containing the full narrative summary text.
        Metadata: {type, session_name, speakers, key_moment_count}
      - One ``type=key_moment`` chunk per key moment.
        Metadata: {type, session_name, category, timestamp}

    Args:
        session_name: Session folder name used as the ChromaDB document key prefix
        data:         Parsed summary dict from generate_summary()
        speakers:     Sorted list of unique speaker names in the transcript
        collection:   Open ChromaDB collection
        config:       Parsed config.yaml dict (used to instantiate the embedding provider)
    """
    embedding_provider = get_embedding_provider(config)

    ids: list[str] = []
    embeddings: list[list[float]] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    # --- Narrative summary chunk ---
    narrative = data.get("narrative_summary", "")
    if narrative:
        print("  Embedding narrative summary…", end=" ", flush=True)
        t0 = time.time()
        emb = embedding_provider.embed(narrative)
        print(f"done ({time.time() - t0:.1f}s)")

        ids.append(f"{session_name}__summary")
        embeddings.append(emb)
        documents.append(narrative)
        metadatas.append(
            {
                "type": "summary",
                "session_name": session_name,
                "speakers": ", ".join(speakers),
                "key_moment_count": len(data.get("key_moments", [])),
            }
        )

    # --- Key moment chunks ---
    key_moments = data.get("key_moments", [])
    for idx, km in enumerate(key_moments):
        description = km.get("description", "")
        context = km.get("context", "")
        doc_text = description
        if context:
            doc_text = f"{description}\n\nContext: {context}"
        if not doc_text.strip():
            continue

        print(f"  Embedding key moment {idx + 1}/{len(key_moments)}…", end=" ", flush=True)
        t0 = time.time()
        emb = embedding_provider.embed(doc_text)
        print(f"done ({time.time() - t0:.1f}s)")

        ids.append(f"{session_name}__key_moment_{idx:04d}")
        embeddings.append(emb)
        documents.append(doc_text)
        metadatas.append(
            {
                "type": "key_moment",
                "session_name": session_name,
                "category": km.get("category", ""),
                "timestamp": float(km.get("timestamp", 0)),
            }
        )

    if ids:
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        print(f"  Stored {len(ids)} summary chunk(s) in ChromaDB collection '{COLLECTION_NAME}'.")


# ---------------------------------------------------------------------------
# Core per-session function
# ---------------------------------------------------------------------------

def summarize_session(session_name: str, session_dir: Path, config: dict) -> None:
    """
    Generate a session summary for one session and store results in ChromaDB.

    Reads _combined_transcript.txt, calls the configured LLM chat provider,
    writes _session_summary.json and _session_summary.md, then upserts the
    summary and key moments into ChromaDB.

    Args:
        session_name: The session folder name (used as ChromaDB document key prefix)
        session_dir:  Full path to the session folder
        config:       Parsed config.yaml dict
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

    print(f"  Transcript: {len(transcript_text)} chars, {len(speakers)} speaker(s)")

    # Generate summary via LLM
    data = generate_summary(transcript_text, config)

    # Write outputs
    json_path = session_dir / "_session_summary.json"
    md_path = session_dir / "_session_summary.md"

    write_json_summary(data, json_path)
    print(f"  Written: {json_path.name}")

    write_markdown_summary(data, session_name, md_path)
    print(f"  Written: {md_path.name}")

    # Vectorize summary and key moments into ChromaDB
    vector_db_directory = config.get("vector_db_directory", "./vectordb")
    collection = get_chroma_collection(vector_db_directory)
    vectorize_summary(session_name, data, speakers, collection, config)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the summarize.py CLI."""
    parser = argparse.ArgumentParser(
        description="Generate structured session summaries and store them in ChromaDB."
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
