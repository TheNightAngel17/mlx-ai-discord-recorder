#!/usr/bin/env python3
"""
app.py — Always-on FastAPI service for RAG queries against vectorized D&D session transcripts.

Config is loaded once on startup. The ChromaDB client, embedding provider, and chat
provider are initialised at startup and reused across requests — eliminating the
cold-start overhead of the old spawn-per-query approach.

Start with:
    uvicorn app:app --host 0.0.0.0 --port 8100

Endpoints:
    POST /api/query    — Ask a natural-language question about recorded sessions
    GET  /api/sessions — List all indexed session names from ChromaDB
    GET  /api/health   — Health check: ChromaDB status and provider info
"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import chromadb
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from providers import get_chat_provider, get_embedding_provider
from rag import COLLECTION_NAME, query_rag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config.yaml from the repo root (one level up from py-query/)."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        logger.error("config.yaml not found at %s", config_path)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Lifespan — load config, initialise providers and ChromaDB once on startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise all shared state before the server starts accepting requests."""
    # Load .env from repo root so API keys are available
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)

    config = load_config()
    app.state.config = config

    embedding_provider_name = config.get("embedding_provider", "ollama")
    embedding_model = config.get("embedding_model", "nomic-embed-text")
    chat_provider_name = config.get("chat_provider", "ollama")
    chat_model = config.get("chat_model", "llama3")

    logger.info(
        "Initialising providers: embedding=%s/%s  chat=%s/%s",
        embedding_provider_name, embedding_model,
        chat_provider_name, chat_model,
    )

    # Initialise providers (factory functions call sys.exit on bad config)
    app.state.providers_ready = False
    app.state.embedding_provider = None
    app.state.chat_provider = None
    try:
        app.state.embedding_provider = get_embedding_provider(config)
        app.state.chat_provider = get_chat_provider(config)
        app.state.providers_ready = True
        logger.info("Providers ready")
    except SystemExit:
        logger.error(
            "Provider initialisation failed — check config.yaml and .env. "
            "Query endpoint will return 503 until this is fixed."
        )

    # Open ChromaDB
    app.state.chroma_ready = False
    app.state.chroma_client = None
    app.state.chroma_collection = None

    db_path = Path(config.get("vector_db_directory", "./vectordb"))
    if not db_path.exists():
        logger.warning(
            "Vector DB directory not found: %s — run py-process/vectorize.py first", db_path
        )
    else:
        try:
            client = chromadb.PersistentClient(path=str(db_path))
            existing = [c.name for c in client.list_collections()]
            if COLLECTION_NAME not in existing:
                logger.warning(
                    "ChromaDB collection '%s' not found — run py-process/vectorize.py first",
                    COLLECTION_NAME,
                )
            else:
                collection = client.get_collection(COLLECTION_NAME)
                app.state.chroma_client = client
                app.state.chroma_collection = collection
                app.state.chroma_ready = True
                logger.info("ChromaDB ready: %d chunks indexed", collection.count())
        except Exception as exc:
            logger.error("Failed to open ChromaDB: %s", exc)

    yield
    # No explicit cleanup needed — chromadb.PersistentClient manages its own connection


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MLX AI RAG Query API",
    description=(
        "Always-on RAG query service for D&D session transcripts. "
        "Providers and ChromaDB are loaded once at startup for fast per-request latency."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    """Request body for POST /api/query."""

    question: str = Field(..., min_length=1, description="The question to ask")
    session: str | None = Field(
        None, description="Restrict the search to a specific session folder name"
    )
    top_k: int = Field(5, ge=1, le=20, description="Number of transcript chunks to retrieve")
    show_sources: bool = Field(
        False, description="Include retrieved source chunks in the response"
    )


class SourceChunk(BaseModel):
    """A single retrieved transcript chunk."""

    session: str
    start: str
    end: str
    speakers: str
    text: str
    distance: float


class Timings(BaseModel):
    """Per-stage timing breakdown for a RAG query."""

    embed_s: float
    retrieval_s: float
    chat_s: float
    total_s: float


class QueryResponse(BaseModel):
    """Response body for POST /api/query."""

    answer: str
    sources: list[SourceChunk]
    timings: Timings


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/query", response_model=QueryResponse)
async def query_endpoint(request: QueryRequest):
    """
    Run a RAG query and return the LLM answer with optional source citations.

    The embedding provider, chat provider, and ChromaDB collection are all
    pre-initialised at startup so there is no cold-start overhead.
    """
    if not app.state.providers_ready:
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM providers are not ready. "
                "Check config.yaml and .env, then restart the API."
            ),
        )
    if not app.state.chroma_ready:
        raise HTTPException(
            status_code=503,
            detail=(
                "ChromaDB is not ready. "
                "Run py-process/vectorize.py to index sessions first."
            ),
        )

    logger.info(
        "RAG query: question=%r  session=%s  top_k=%d",
        request.question,
        request.session or "all",
        request.top_k,
    )

    try:
        result = query_rag(
            question=request.question,
            config=app.state.config,
            session_filter=request.session,
            top_k=request.top_k,
            embedding_provider=app.state.embedding_provider,
            chat_provider=app.state.chat_provider,
            chroma_client=app.state.chroma_client,
        )
    except SystemExit:
        raise HTTPException(
            status_code=503,
            detail="RAG query failed — check API server logs for details.",
        )
    except Exception as exc:
        logger.error("Unexpected error during RAG query: %s", exc)
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")

    t = result["timings"]
    logger.info(
        "RAG query complete: embed=%.2fs  retrieval=%.2fs  chat=%.2fs  total=%.2fs",
        t["embed_s"], t["retrieval_s"], t["chat_s"], t["total_s"],
    )

    sources = result["sources"] if request.show_sources else []
    return QueryResponse(
        answer=result["answer"],
        sources=[SourceChunk(**src) for src in sources],
        timings=Timings(**result["timings"]),
    )


@app.get("/api/sessions")
async def list_sessions():
    """
    List all indexed session names from ChromaDB.

    Returns a sorted list of unique session folder names that have been
    vectorized and are available for querying.
    """
    if not app.state.chroma_ready:
        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "ChromaDB is not ready. "
                    "Run py-process/vectorize.py to index sessions first."
                ),
                "sessions": [],
                "count": 0,
            },
        )

    results = app.state.chroma_collection.get(include=["metadatas"])
    sessions = sorted(
        set(
            meta.get("session_name", "unknown")
            for meta in results["metadatas"]
        )
    )
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/api/health")
async def health():
    """
    Health check — returns readiness status, ChromaDB stats, and provider info.

    Useful for monitoring and for the Discord bot to verify the API is up
    before attempting queries.
    """
    config = app.state.config
    chunk_count = 0
    if app.state.chroma_ready and app.state.chroma_collection is not None:
        try:
            chunk_count = app.state.chroma_collection.count()
        except Exception:
            pass

    return {
        "status": "ok",
        "providers_ready": app.state.providers_ready,
        "chroma_ready": app.state.chroma_ready,
        "chroma_chunks": chunk_count,
        "embedding_provider": config.get("embedding_provider", "ollama"),
        "embedding_model": config.get("embedding_model", "nomic-embed-text"),
        "chat_provider": config.get("chat_provider", "ollama"),
        "chat_model": config.get("chat_model", "llama3"),
    }
