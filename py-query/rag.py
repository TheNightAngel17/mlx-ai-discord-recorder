#!/usr/bin/env python3
"""
rag.py — Core RAG (Retrieval-Augmented Generation) logic.

Shared between the CLI query tool and the future web UI.

The main entry point is `query_rag()`, which:
  1. Embeds the question using the configured embedding provider.
  2. Queries ChromaDB for the top-k most similar transcript chunks.
  3. Builds a system prompt with the retrieved context.
  4. Calls the configured chat provider to generate an answer.
  5. Returns the answer and source metadata.

IMPORTANT: The embedding provider used at query time must match the one used
when the transcripts were vectorized (py-process/vectorize.py). Mixing providers
will produce garbage similarity scores. If you switch embedding_provider, re-run
vectorize.py --force on all sessions.
"""

import sys
import time

import chromadb

from providers import (
    ChatProvider,
    EmbeddingProvider,
    get_chat_provider,
    get_embedding_provider,
)
from categories import resolve_category
from chroma_client import get_chroma_client


def _format_seconds(s: float) -> str:
    """Format a seconds value as HH:MM:SS."""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:05.2f}"


def _build_system_prompt(chunks: list[dict], display_name: str = "session") -> str:
    """
    Build the LLM system prompt from retrieved transcript chunks.

    Each chunk dict should have keys: session, start, end, speakers, text.
    ``display_name`` is the category's display name (e.g. "D&D Session", "Work Meeting"),
    used to make the assistant's framing category-aware.
    """
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(
            f"[Source {i}]\n"
            f"Session: {chunk['session']}\n"
            f"Time: {chunk['start']} → {chunk['end']}\n"
            f"Speakers: {chunk['speakers']}\n"
            f"Transcript:\n{chunk['text']}"
        )

    context_block = "\n\n---\n\n".join(context_parts)

    return (
        f'You are a helpful assistant that answers questions about recorded sessions of type '
        f'"{display_name}" based solely on the provided transcript excerpts. '
        "When answering, cite the session name and timestamps of the relevant sources. "
        "If the answer cannot be found in the provided context, say so clearly.\n\n"
        "=== TRANSCRIPT CONTEXT ===\n\n"
        f"{context_block}\n\n"
        "=== END OF CONTEXT ==="
    )


def query_rag(
    question: str,
    config: dict,
    session_filter: str | None = None,
    top_k: int = 5,
    embedding_provider: EmbeddingProvider | None = None,
    chat_provider: ChatProvider | None = None,
    chroma_client: chromadb.ClientAPI | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> dict:
    """
    Run a RAG query against the ChromaDB vector database.

    Args:
        question:             The user's question.
        config:               Loaded config.yaml dict.
        session_filter:       Optional session name to restrict the search to.
        top_k:                Number of chunks to retrieve (default: 5).
        embedding_provider:   Pre-initialised embedding provider. If None, one is
                              created from config on each call (CLI path).
        chat_provider:        Pre-initialised chat provider. If None, one is
                              created from config on each call (CLI path).
        chroma_client:        Pre-initialised ChromaDB client. If None, a new
                              PersistentClient is opened on each call (CLI path).
        category:             Category to search (selects the collection). Defaults to the
                              built-in "dnd" category when omitted.
        subcategory:          Optional sub-category tag to restrict the search to.

    Returns:
        {
            "answer": str,
            "sources": [
                {
                    "session": str,
                    "start": str,
                    "end": str,
                    "speakers": str,
                    "text": str,
                    "distance": float,
                },
                ...
            ],
            "timings": {
                "embed_s": float,
                "retrieval_s": float,
                "chat_s": float,
                "total_s": float,
            }
        }
    """
    # --- Resolve category → collection ---
    category_meta = resolve_category(config, category)
    collection_name = category_meta["collection_name"]

    # --- Open or reuse ChromaDB ---
    if chroma_client is None:
        chroma_client = get_chroma_client(config)

    existing = [c.name for c in chroma_client.list_collections()]
    if collection_name not in existing:
        print(
            f"Error: ChromaDB collection '{collection_name}' (category "
            f"'{category_meta['category_name']}') does not exist.\n"
            "Have you vectorized any sessions of this category yet?",
            file=sys.stderr,
        )
        sys.exit(1)

    collection = chroma_client.get_collection(collection_name)
    total = collection.count()
    if total == 0:
        print(
            f"Error: ChromaDB collection '{collection_name}' is empty. "
            "Run py-process/vectorize.py to index some sessions first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Embed the question ---
    if embedding_provider is None:
        embedding_provider = get_embedding_provider(config)
    t0 = time.perf_counter()
    query_embedding = embedding_provider.embed(question)
    embed_time = time.perf_counter() - t0

    # --- Query ChromaDB ---
    filters = []
    if session_filter:
        filters.append({"session_name": session_filter})
    if subcategory:
        filters.append({"subcategory": subcategory})
    if not filters:
        where = None
    elif len(filters) == 1:
        where = filters[0]
    else:
        where = {"$and": filters}
    n_results = min(top_k, total)

    t0 = time.perf_counter()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    retrieval_time = time.perf_counter() - t0

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    # --- Build source list ---
    sources = []
    chunks_for_prompt = []
    for doc, meta, dist in zip(docs, metas, distances):
        start_str = _format_seconds(meta.get("start_time", 0))
        end_str = _format_seconds(meta.get("end_time", 0))
        sources.append(
            {
                "session": meta.get("session_name", "unknown"),
                "start": start_str,
                "end": end_str,
                "speakers": meta.get("speakers", "unknown"),
                "text": doc,
                "distance": dist,
            }
        )
        chunks_for_prompt.append(
            {
                "session": meta.get("session_name", "unknown"),
                "start": start_str,
                "end": end_str,
                "speakers": meta.get("speakers", "unknown"),
                "text": doc,
            }
        )

    # --- Generate answer ---
    system_prompt = _build_system_prompt(chunks_for_prompt, category_meta["display_name"])
    if chat_provider is None:
        chat_provider = get_chat_provider(config)
    t0 = time.perf_counter()
    answer = chat_provider.chat(system_prompt=system_prompt, user_message=question)
    chat_time = time.perf_counter() - t0

    return {
        "answer": answer,
        "sources": sources,
        "timings": {
            "embed_s": embed_time,
            "retrieval_s": retrieval_time,
            "chat_s": chat_time,
            "total_s": embed_time + retrieval_time + chat_time,
        },
    }
