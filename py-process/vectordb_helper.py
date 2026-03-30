#!/usr/bin/env python3
"""
vectordb_helper.py — Inspect, search, and manage the ChromaDB vector database.

Usage:
    python vectordb_helper.py                                      # Summary of all sessions
    python vectordb_helper.py --session <name>                     # Show chunks for one session
    python vectordb_helper.py --search <query>                     # Semantic search (requires Ollama)
    python vectordb_helper.py --search <query> --session <name>    # Search within a session
    python vectordb_helper.py --search <query> --limit 10          # Return more results
    python vectordb_helper.py --delete-session <name>              # Delete one session's chunks
    python vectordb_helper.py --clear-all                          # Wipe the entire collection
    python vectordb_helper.py --list-sessions                      # Just list session names
"""

import argparse
import sys
from pathlib import Path

import yaml
import chromadb
import requests

COLLECTION_NAME = "dnd_sessions"


def load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def format_seconds(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:05.2f}"


def embed_text(text: str, ollama_base_url: str, embedding_model: str) -> list[float]:
    url = f"{ollama_base_url.rstrip('/')}/api/embeddings"
    try:
        resp = requests.post(url, json={"model": embedding_model, "prompt": text}, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to Ollama at {ollama_base_url}. Is Ollama running?", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if "embedding" not in data:
        print(f"Error: Ollama response missing 'embedding': {data}", file=sys.stderr)
        sys.exit(1)
    return data["embedding"]


def main():
    parser = argparse.ArgumentParser(description="Inspect and manage the ChromaDB vector database.")
    parser.add_argument("--session", default=None, help="Filter by session name")
    parser.add_argument("--search", default=None, help="Semantic search query (requires Ollama)")
    parser.add_argument("--limit", type=int, default=5, help="Max results for search (default: 5)")
    parser.add_argument("--list-sessions", action="store_true", help="List all session names and exit")
    parser.add_argument("--delete-session", default=None, metavar="SESSION", help="Delete all chunks for a specific session")
    parser.add_argument("--clear-all", action="store_true", help="Wipe the entire collection (all sessions)")
    args = parser.parse_args()

    config = load_config()
    vector_db_directory = config.get("vector_db_directory", "./vectordb")
    ollama_base_url = config.get("ollama_base_url", "http://localhost:11434")
    embedding_model = config.get("embedding_model", "nomic-embed-text")

    print(f"Vector DB: {vector_db_directory}")
    print(f"Collection: {COLLECTION_NAME}")
    print()

    client = chromadb.PersistentClient(path=vector_db_directory)

    # Check if collection exists
    existing = [c.name for c in client.list_collections()]
    if COLLECTION_NAME not in existing:
        print("Collection does not exist yet — nothing has been vectorized.")
        sys.exit(0)

    collection = client.get_collection(COLLECTION_NAME)
    total = collection.count()
    print(f"Total chunks in DB: {total}")

    if total == 0 and not args.clear_all:
        print("The collection is empty — nothing has been vectorized yet.")
        sys.exit(0)

    # ── Clear all ─────────────────────────────────────────────────────────────
    if args.clear_all:
        confirm = input(f"\n⚠️  This will delete ALL {total} chunk(s) in the collection. Type YES to confirm: ")
        if confirm.strip() == "YES":
            client.delete_collection(COLLECTION_NAME)
            print("Collection deleted.")
        else:
            print("Aborted.")
        return

    # ── Delete one session ────────────────────────────────────────────────────
    if args.delete_session:
        results = collection.get(where={"session_name": args.delete_session})
        ids = results["ids"]
        if not ids:
            print(f"No chunks found for session '{args.delete_session}'.")
            sys.exit(0)
        confirm = input(f"\n⚠️  Delete {len(ids)} chunk(s) for session '{args.delete_session}'? Type YES to confirm: ")
        if confirm.strip() == "YES":
            collection.delete(ids=ids)
            print(f"Deleted {len(ids)} chunk(s) for session '{args.delete_session}'.")
        else:
            print("Aborted.")
        return

    # ── List sessions only ────────────────────────────────────────────────────
    if args.list_sessions:
        all_docs = collection.get(include=["metadatas"])
        sessions = sorted({m.get("session_name", "unknown") for m in all_docs["metadatas"]})
        print(f"Sessions ({len(sessions)}):")
        for s in sessions:
            print(f"  {s}")
        return

    # ── Summary mode (no search query) ───────────────────────────────────────
    if not args.search:
        all_docs = collection.get(include=["metadatas"])
        sessions: dict[str, list[dict]] = {}
        for meta in all_docs["metadatas"]:
            sn = meta.get("session_name", "unknown")
            sessions.setdefault(sn, []).append(meta)

        if args.session:
            sessions = {k: v for k, v in sessions.items() if k == args.session}
            if not sessions:
                print(f"No data found for session '{args.session}'")
                sys.exit(0)

        print(f"Sessions stored: {len(sessions)}")
        print()

        for session_name, chunks in sorted(sessions.items()):
            start_times = [c.get("start_time", 0) for c in chunks]
            end_times   = [c.get("end_time", 0)   for c in chunks]
            all_speakers = set()
            for c in chunks:
                for sp in c.get("speakers", "").split(", "):
                    if sp:
                        all_speakers.add(sp)

            print(f"  Session : {session_name}")
            print(f"  Chunks  : {len(chunks)}")
            print(f"  Span    : {format_seconds(min(start_times))} → {format_seconds(max(end_times))}")
            print(f"  Speakers: {', '.join(sorted(all_speakers)) or 'unknown'}")
            print()

        print("Tips:")
        print("  --search \"<query>\"          Semantic search")
        print("  --list-sessions             List session names only")
        print("  --delete-session <name>     Remove one session's chunks")
        print("  --clear-all                 Wipe the entire collection")
        return

    # ── Semantic search mode ──────────────────────────────────────────────────
    print(f"Embedding query via Ollama ({embedding_model})...")
    query_embedding = embed_text(args.search, ollama_base_url, embedding_model)

    where = {"session_name": args.session} if args.session else None

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(args.limit, total),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    ids       = results["ids"][0]
    docs      = results["documents"][0]
    metas     = results["metadatas"][0]
    distances = results["distances"][0]

    print(f"\nTop {len(ids)} result(s) for: \"{args.search}\"\n")
    print("=" * 60)

    for i, (doc_id, doc, meta, dist) in enumerate(zip(ids, docs, metas, distances), 1):
        session  = meta.get("session_name", "?")
        start    = format_seconds(meta.get("start_time", 0))
        end      = format_seconds(meta.get("end_time", 0))
        speakers = meta.get("speakers", "?")

        print(f"[{i}] {doc_id}")
        print(f"    Session : {session}")
        print(f"    Time    : {start} → {end}")
        print(f"    Speakers: {speakers}")
        print(f"    Distance: {dist:.4f}")
        print(f"    Text    :")
        for line in doc.split("\n"):
            print(f"      {line}")
        print()


if __name__ == "__main__":
    main()
