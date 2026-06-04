#!/usr/bin/env python3
"""
query.py — CLI entry point for RAG queries against vectorized D&D session transcripts.

Usage:
    python query.py "What happened when the party entered the cave?"
    python query.py "Who attacked the dragon?" --session 20260330_033020_test
    python query.py "What spells were cast?" --top-k 10 --show-sources

Examples:
    # Ask a question using default provider settings from config.yaml
    python query.py "What did the rogue do in the tavern?"

    # Filter to a specific session
    python query.py "Did anyone die?" --session 20260330_033020_test

    # Show the retrieved source chunks alongside the answer
    python query.py "What treasure did the party find?" --show-sources

    # Retrieve more context chunks
    python query.py "Describe the final boss fight" --top-k 10 --show-sources
"""

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from rag import query_rag


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-query/)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config.yaml not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Load .env from repo root before anything else
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)

    parser = argparse.ArgumentParser(
        description="Query vectorized D&D session transcripts using RAG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python query.py "What happened in the cave?"
  python query.py "Who attacked the dragon?" --session 20260330_test
  python query.py "Describe the final boss fight" --top-k 10 --show-sources
        """,
    )
    parser.add_argument(
        "question",
        help="The question to ask about the recorded sessions.",
    )
    parser.add_argument(
        "--session",
        default=None,
        metavar="NAME",
        help="Restrict the search to a specific session folder name.",
    )
    parser.add_argument(
        "--category",
        default=None,
        metavar="NAME",
        help="Category to search (selects the collection). Defaults to 'dnd'.",
    )
    parser.add_argument(
        "--subcategory",
        default=None,
        metavar="NAME",
        help="Restrict the search to a specific sub-category within the category.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        metavar="N",
        help="Number of transcript chunks to retrieve (default: 5).",
    )
    parser.add_argument(
        "--show-sources",
        action="store_true",
        help="Print the retrieved source chunks after the answer.",
    )
    args = parser.parse_args()

    config = load_config()

    # Print provider info so the user knows which backend is active
    embedding_provider = config.get("embedding_provider", "ollama")
    embedding_model = config.get("embedding_model", "nomic-embed-text")
    chat_provider = config.get("chat_provider", "ollama")
    chat_model = config.get("chat_model", "llama3")

    print(f"Embedding : {embedding_provider} / {embedding_model}")
    print(f"Chat      : {chat_provider} / {chat_model}")
    print(f"Category  : {args.category or 'dnd'}")
    if args.subcategory:
        print(f"Sub-cat   : {args.subcategory}")
    if args.session:
        print(f"Session   : {args.session}")
    print(f"Top-k     : {args.top_k}")
    print()

    print("Embedding question and retrieving context...")
    result = query_rag(
        question=args.question,
        config=config,
        session_filter=args.session,
        top_k=args.top_k,
        category=args.category,
        subcategory=args.subcategory,
    )

    # --- Timing summary ---
    t = result["timings"]
    print(
        f"Timings   : embed={t['embed_s']:.2f}s  "
        f"retrieval={t['retrieval_s']:.2f}s  "
        f"chat={t['chat_s']:.2f}s  "
        f"total={t['total_s']:.2f}s"
    )
    print()
    print("=" * 60)
    print("ANSWER")
    print("=" * 60)
    print(result["answer"])
    print()

    if args.show_sources:
        print("=" * 60)
        print(f"SOURCES ({len(result['sources'])} chunks retrieved)")
        print("=" * 60)
        for i, src in enumerate(result["sources"], 1):
            print(f"[{i}] Session : {src['session']}")
            print(f"    Time    : {src['start']} → {src['end']}")
            print(f"    Speakers: {src['speakers']}")
            print(f"    Distance: {src['distance']:.4f}")
            print(f"    Text    :")
            for line in src["text"].split("\n"):
                print(f"      {line}")
            print()


if __name__ == "__main__":
    main()
