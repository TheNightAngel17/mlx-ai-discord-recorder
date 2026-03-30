#!/usr/bin/env python3
"""
vectorize.py — Chunk, embed, and store session transcripts into a ChromaDB vector database.

Usage:
    python vectorize.py <session_name> [--force]
    python vectorize.py --all [--force]

Examples:
    python vectorize.py 20260330_033020_test
    python vectorize.py 20260330_033020_test --force
    python vectorize.py --all
    python vectorize.py --all --force
"""

import argparse
import re
import sys
import time
from pathlib import Path

import requests
import yaml
import chromadb


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-process/)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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


def parse_combined_transcript(path: Path) -> list[dict]:
    """
    Parse _combined_transcript.txt and return a list of segment dicts:
        {start: float, end: float, username: str, text: str}
    """
    segments = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            m = _LINE_RE.match(line)
            if m:
                start_ts, end_ts, username, text = m.groups()
                segments.append(
                    {
                        "start": _ts_to_seconds(start_ts),
                        "end": _ts_to_seconds(end_ts),
                        "username": username,
                        "text": text.strip(),
                    }
                )
    return segments


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_segments(segments: list[dict], chunk_minutes: float) -> list[dict]:
    """
    Group transcript segments into time-window chunks.

    Each chunk spans at most `chunk_minutes` minutes, starting from the first
    segment's start time.  Returns a list of chunk dicts:
        {
            text: str,          # full text of all lines in the window
            start_time: float,  # seconds
            end_time: float,    # seconds
            speakers: list[str] # unique usernames in the chunk
        }
    """
    if not segments:
        return []

    window_seconds = chunk_minutes * 60.0
    chunks = []
    chunk_start = segments[0]["start"]
    current_lines: list[str] = []
    current_speakers: set[str] = set()
    current_end = segments[0]["start"]

    for seg in segments:
        # Start a new chunk if this segment falls outside the current window
        if seg["start"] >= chunk_start + window_seconds:
            if current_lines:
                chunks.append(
                    {
                        "text": "\n".join(current_lines),
                        "start_time": chunk_start,
                        "end_time": current_end,
                        "speakers": sorted(current_speakers),
                    }
                )
            chunk_start = seg["start"]
            current_lines = []
            current_speakers = set()

        current_lines.append(f"{seg['username']}: {seg['text']}")
        current_speakers.add(seg["username"])
        current_end = seg["end"]

    # Flush the final chunk
    if current_lines:
        chunks.append(
            {
                "text": "\n".join(current_lines),
                "start_time": chunk_start,
                "end_time": current_end,
                "speakers": sorted(current_speakers),
            }
        )

    return chunks


# ---------------------------------------------------------------------------
# Embedding via Ollama
# ---------------------------------------------------------------------------

def embed_text(text: str, ollama_base_url: str, embedding_model: str) -> list[float]:
    """
    Call the Ollama embeddings API and return the embedding vector.

    POST <ollama_base_url>/api/embeddings
    Body: {"model": "<model>", "prompt": "<text>"}
    Response: {"embedding": [float, ...]}
    """
    url = f"{ollama_base_url.rstrip('/')}/api/embeddings"
    try:
        resp = requests.post(
            url,
            json={"model": embedding_model, "prompt": text},
            timeout=60,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(
            f"\nError: Could not connect to Ollama at {ollama_base_url}. "
            "Is Ollama running?",
            file=sys.stderr,
        )
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        print(f"\nError: Ollama API returned an error: {exc}", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("\nError: Ollama API request timed out.", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if "embedding" not in data:
        print(
            f"\nError: Ollama response missing 'embedding' field: {data}",
            file=sys.stderr,
        )
        sys.exit(1)
    return data["embedding"]


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

COLLECTION_NAME = "dnd_sessions"


def get_chroma_collection(vector_db_directory: str):
    """Return (or create) the persistent ChromaDB collection."""
    client = chromadb.PersistentClient(path=vector_db_directory)
    collection = client.get_or_create_collection(COLLECTION_NAME)
    return collection


def session_is_vectorized(collection, session_name: str) -> bool:
    """Return True if any documents for this session already exist in the collection."""
    results = collection.get(where={"session_name": session_name}, limit=1)
    return len(results["ids"]) > 0


def delete_session_documents(collection, session_name: str) -> None:
    """Remove all documents belonging to this session from the collection."""
    results = collection.get(where={"session_name": session_name})
    if results["ids"]:
        collection.delete(ids=results["ids"])


# ---------------------------------------------------------------------------
# Vectorize one session
# ---------------------------------------------------------------------------

def vectorize_session(
    session_name: str,
    session_dir: Path,
    collection,
    ollama_base_url: str,
    embedding_model: str,
    chunk_minutes: float,
    force: bool,
) -> None:
    """Chunk, embed, and store one session's combined transcript."""

    combined_path = session_dir / "_combined_transcript.txt"
    if not combined_path.exists():
        print(
            f"  Error: _combined_transcript.txt not found in {session_dir}",
            file=sys.stderr,
        )
        return

    # Skip if already vectorized (unless --force)
    if session_is_vectorized(collection, session_name):
        if force:
            print(f"  Re-indexing (--force): removing existing documents...")
            delete_session_documents(collection, session_name)
        else:
            print(
                f"  Already vectorized. Use --force to re-index."
            )
            return

    # Parse the transcript
    segments = parse_combined_transcript(combined_path)
    if not segments:
        print(f"  Warning: No transcript lines found in {combined_path}", file=sys.stderr)
        return

    # Chunk
    chunks = chunk_segments(segments, chunk_minutes)
    print(f"  Transcript: {len(segments)} lines -> {len(chunks)} chunks ({chunk_minutes}-min windows)")

    # Embed and store each chunk
    ids = []
    embeddings = []
    documents = []
    metadatas = []

    for idx, chunk in enumerate(chunks):
        chunk_id = f"{session_name}__chunk_{idx:04d}"
        print(f"  Embedding chunk {idx + 1}/{len(chunks)}...", end=" ", flush=True)
        t0 = time.time()
        embedding = embed_text(chunk["text"], ollama_base_url, embedding_model)
        elapsed = time.time() - t0
        print(f"done ({elapsed:.1f}s)")

        ids.append(chunk_id)
        embeddings.append(embedding)
        documents.append(chunk["text"])
        metadatas.append(
            {
                "session_name": session_name,
                "start_time": chunk["start_time"],
                "end_time": chunk["end_time"],
                "speakers": ", ".join(chunk["speakers"]),
            }
        )

    # Upsert all chunks in a single call
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )
    print(f"  Stored {len(chunks)} chunks in ChromaDB collection '{COLLECTION_NAME}'.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Chunk, embed, and store session transcripts into a ChromaDB vector database."
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
        help="Process every session folder in the output directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-index a session even if it has already been vectorized.",
    )
    args = parser.parse_args()

    # Load config
    config = load_config()

    output_dir = config.get("output_directory", "./recordings")
    vector_db_directory = config.get("vector_db_directory", "./vectordb")
    embedding_model = config.get("embedding_model", "nomic-embed-text")
    chunk_minutes = float(config.get("chunk_minutes", 3))
    ollama_base_url = config.get("ollama_base_url", "http://localhost:11434")

    output_path = Path(output_dir)

    # Resolve sessions to process
    if args.all:
        if not output_path.exists():
            print(f"Error: Output directory not found: {output_path}", file=sys.stderr)
            sys.exit(1)
        sessions = sorted(
            [d.name for d in output_path.iterdir() if d.is_dir()]
        )
        if not sessions:
            print(f"No session folders found in {output_path}", file=sys.stderr)
            sys.exit(0)
    else:
        sessions = [args.session]

    print(f"Vector DB: {vector_db_directory}")
    print(f"Embedding: {embedding_model} via {ollama_base_url}")
    print(f"Chunk size: {chunk_minutes} minutes")
    print(f"Sessions:  {len(sessions)}")
    print()

    # Open (or create) the ChromaDB collection once for all sessions
    collection = get_chroma_collection(vector_db_directory)

    for session_name in sessions:
        session_dir = output_path / session_name
        if not session_dir.exists():
            print(f"Error: Session directory not found: {session_dir}", file=sys.stderr)
            if not args.all:
                sys.exit(1)
            continue

        print(f"Session: {session_name}")
        vectorize_session(
            session_name=session_name,
            session_dir=session_dir,
            collection=collection,
            ollama_base_url=ollama_base_url,
            embedding_model=embedding_model,
            chunk_minutes=chunk_minutes,
            force=args.force,
        )
        print()

    print("Done.")


if __name__ == "__main__":
    main()
