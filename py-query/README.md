# py-query — RAG Query Service

A persistent HTTP API and CLI tool for asking natural-language questions about your recorded and vectorized D&D sessions. Built with FastAPI; ChromaDB and LLM providers are loaded once at startup to eliminate cold-start latency.

> 🐳 **Docker:** runs as the `py-query` service. See [DOCKER.md](../DOCKER.md).

> **↩️ Back to [main README](../README.md)** | See also: [js-bot](../js-bot/README.md) · [py-process](../py-process/README.md)

---

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Running the API Server](#running-the-api-server)
  - [API Reference](#api-reference)
  - [CLI Usage](#cli-usage)
  - [Discord Usage](#discord-usage)
- [Provider Configuration](#provider-configuration)
- [Example Output](#example-output)
- [File Descriptions](#file-descriptions)
- [Dependencies](#dependencies)

---

## Overview

`py-query` reads from the ChromaDB vector database populated by [`py-process/vectorize.py`](../py-process/README.md#vectorizepy--transcript-vectorization), retrieves the most relevant transcript chunks, and uses a configurable LLM to synthesize an answer — with source citations pointing back to the session name and timestamps.

The primary interface is the **FastAPI HTTP service** (`app.py`), which keeps ChromaDB and the LLM providers loaded in memory so every Discord query is served with no cold-start overhead. A standalone CLI (`query.py`) is also available for direct use.

**How it works:**

1. **Embeds your question** using the configured embedding provider (Ollama, OpenAI, or Voyage)
2. **Retrieves the top-k most relevant transcript chunks** from ChromaDB
3. **Generates an answer** using a configurable chat provider (Ollama, OpenAI, or Anthropic), grounded in the retrieved context
4. **Returns the answer** and, optionally, the source chunks with session name, timestamps, speakers, and similarity score

> ℹ️ `py-query` is **read-only** — it never writes to ChromaDB. It is safe to run at any time, even while `py-process` is actively vectorizing.

---

## Prerequisites

- **Python 3.11+** — [Download](https://www.python.org/downloads/)
- **ChromaDB data** — You must have run [`py-process/vectorize.py`](../py-process/README.md#vectorizepy--transcript-vectorization) at least once to index sessions
- **A running LLM backend** (depending on your provider configuration):
  - **Ollama** — [Download](https://ollama.com/) and pull models:
    ```bash
    ollama pull nomic-embed-text   # embedding model
    ollama pull llama3.2           # chat model
    ```
  - **OpenAI** — Set `OPENAI_API_KEY` in your `.env` file
  - **Anthropic** — Set `ANTHROPIC_API_KEY` in your `.env` file
  - **Voyage AI** — Set `VOYAGE_API_KEY` in your `.env` file

---

## Installation

### Bash

```bash
cd py-query
pip install -r requirements.txt
```

### PowerShell

```powershell
cd py-query
pip install -r requirements.txt
```

---

## Configuration

All settings are in the root `config.yaml`. The fields relevant to `py-query`:

| Key | Default | Description |
|-----|---------|-------------|
| `vector_db_directory` | `./vectordb` | Must match the path used by `vectorize.py` |
| `embedding_provider` | `ollama` | Embedding backend: `ollama`, `openai`, or `voyage` |
| `embedding_model` | `nomic-embed-text` | Model name for the embedding provider |
| `chat_provider` | `ollama` | Chat backend: `ollama`, `openai`, or `anthropic` |
| `chat_model` | `llama3.2` | Model name for the chat provider |
| `ollama_base_url` | `http://localhost:11434` | Ollama API URL (used when provider is `ollama`) |
| `query_api_host` | `0.0.0.0` | Host the API server binds to (all interfaces) |
| `query_api_port` | `8100` | Port the API server listens on |

### API Keys

Cloud provider API keys go in the root `.env` file — **never** in `config.yaml`:

```dotenv
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...
```

> ⚠️ **Warning:** The `embedding_provider` and `embedding_model` used at query time **must match** what was used when sessions were vectorized. If you change these, re-run `python py-process/vectorize.py --all --force --reset-collection`.

---

## Usage

### Running the API Server

Start the always-on FastAPI service before using Discord `/mlx-ai query` commands:

#### Bash

```bash
cd py-query
uvicorn app:app --host 0.0.0.0 --port 8100
```

#### PowerShell

```powershell
cd py-query
uvicorn app:app --host 0.0.0.0 --port 8100
```

The server loads config, initialises providers, and opens ChromaDB once at startup. After that, each query is handled in-process with no cold-start cost.

> 💡 Keep this terminal open (or run the process in the background / as a system service) while the Discord bot is running so that `/mlx-ai ask` commands are always available.

---

### API Reference

Base URL: `http://localhost:8100`

#### `POST /api/query`

Ask a question about recorded sessions.

**Request body (JSON):**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | `string` | ✅ | — | The question to ask |
| `session` | `string` | ❌ | `null` | Restrict search to a specific session folder name |
| `top_k` | `integer` | ❌ | `5` | Number of transcript chunks to retrieve (1–20) |
| `show_sources` | `boolean` | ❌ | `false` | Include retrieved source chunks in the response |

**Response body (JSON):**

```json
{
  "answer": "When the party entered the cave, they were ambushed by two goblin scouts...",
  "sources": [
    {
      "session": "20260330_143000_test",
      "start": "00:42:15",
      "end": "00:45:30",
      "speakers": "TheNightAngel17, PlayerTwo",
      "text": "TheNightAngel17: You enter the cave and see two goblins...",
      "distance": 0.1823
    }
  ],
  "timings": {
    "embed_s": 0.21,
    "retrieval_s": 0.04,
    "chat_s": 3.87,
    "total_s": 4.12
  }
}
```

> ℹ️ `sources` is always an empty array when `show_sources` is `false`.

**Example (Bash):**

```bash
curl -s -X POST http://localhost:8100/api/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What happened when the party entered the cave?", "top_k": 5, "show_sources": true}' \
  | python -m json.tool
```

**Example (PowerShell):**

```powershell
Invoke-RestMethod -Uri http://localhost:8100/api/query `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"question": "What happened when the party entered the cave?", "top_k": 5, "show_sources": true}'
```

---

#### `GET /api/sessions`

List all indexed session names from ChromaDB.

**Response body (JSON):**

```json
{
  "sessions": ["20260330_143000_test", "20260401_190000_CampaignSession2"],
  "count": 2
}
```

**Example:**

```bash
curl -s http://localhost:8100/api/sessions | python -m json.tool
```

---

#### `GET /api/health`

Health check — returns ChromaDB status and provider info.

**Response body (JSON):**

```json
{
  "status": "ok",
  "providers_ready": true,
  "chroma_ready": true,
  "chroma_chunks": 142,
  "embedding_provider": "ollama",
  "embedding_model": "nomic-embed-text",
  "chat_provider": "ollama",
  "chat_model": "llama3.2"
}
```

**Example:**

```bash
curl -s http://localhost:8100/api/health | python -m json.tool
```

---

### CLI Usage

```bash
# Basic question
python query.py "What happened when the party entered the cave?"

# Filter to a specific session
python query.py "Who attacked the dragon?" --session 20260330_143000_test

# Show source chunks alongside the answer
python query.py "What treasure did the party find?" --show-sources

# Retrieve more context chunks
python query.py "Describe the final boss fight" --top-k 10 --show-sources
```

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `question` | positional | ✅ | — | The question to ask |
| `--session <name>` | optional | ❌ | All sessions | Restrict search to a specific session |
| `--top-k <n>` | optional | ❌ | `5` | Number of chunks to retrieve |
| `--show-sources` | flag | ❌ | `false` | Print retrieved chunks after the answer |

### Discord Usage

```
/mlx-ai ask question:What happened when the party entered the cave?
/mlx-ai ask question:Who attacked the dragon? session:20260330_143000_test top_k:10 show_sources:true
```

> ⚠️ The `py-query/app.py` API server must be running for Discord queries to work. See [Running the API Server](#running-the-api-server).

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `question` | ✅ | — | The question to ask |
| `session` | ❌ | All sessions | Restrict to a specific session |
| `top_k` | ❌ | `5` | Number of chunks to retrieve (1–20) |
| `show_sources` | ❌ | `false` | Include source chunks in the reply |

---

## Provider Configuration

### Supported Combinations

| `embedding_provider` | `chat_provider` | Notes |
|---------------------|-----------------|-------|
| `ollama` | `ollama` | Fully local — no internet required |
| `ollama` | `openai` | Local embeddings, cloud chat |
| `ollama` | `anthropic` | Local embeddings, Claude for answers |
| `openai` | `openai` | Fully cloud-based |
| `openai` | `anthropic` | OpenAI embeddings, Claude for answers |
| `openai` | `ollama` | Cloud embeddings, local chat |
| `voyage` | `ollama` | Voyage embeddings, local chat |
| `voyage` | `openai` | Voyage embeddings, OpenAI chat |
| `voyage` | `anthropic` | Voyage embeddings, Claude for answers |
| `anthropic` | _any_ | ❌ **Not supported** — Anthropic has no embedding API |

### How Providers Work

The provider abstraction layer (`providers.py`) uses a simple factory pattern:

- `get_embedding_provider(config)` → returns an `EmbeddingProvider` instance (Ollama, OpenAI, or Voyage)
- `get_chat_provider(config)` → returns a `ChatProvider` instance (Ollama, OpenAI, or Anthropic)

All providers use raw HTTP requests via the `requests` library — no vendor SDKs required.

---

## Example Output

```
Embedding : ollama / nomic-embed-text
Chat      : ollama / llama3.2
Top-k     : 5

Embedding question and retrieving context...
Timings   : embed=0.21s  retrieval=0.04s  chat=3.87s  total=4.12s

============================================================
ANSWER
============================================================
When the party entered the cave, they were immediately ambushed by two goblin
scouts. Aragorn led the charge while Legolas provided covering fire from the
entrance. The goblins were defeated quickly, but not before one triggered an
alarm horn, alerting the rest of the lair deeper inside.

[Source: 20260330_143000_test, 00:42:15 → 00:45:30]

============================================================
SOURCES (5 chunks retrieved)
============================================================
[1] Session : 20260330_143000_test
    Time    : 00:42:15 → 00:45:30
    Speakers: TheNightAngel17, PlayerTwo
    Distance: 0.1823
    Text    :
      TheNightAngel17: You enter the cave and see two goblins guarding the entrance.
      PlayerTwo: I want to attack the nearest goblin.
```

---

## File Descriptions

| File | Description |
|------|-------------|
| `query.py` | **CLI entry point.** Loads `.env` and `config.yaml`, parses CLI arguments, calls `query_rag()`, and prints formatted results (answer, timings, optional sources). Available for standalone CLI use; the Discord bot queries via the `app.py` HTTP API rather than spawning this script. |
| `rag.py` | **Core RAG logic.** The `query_rag()` function orchestrates the full pipeline: embed the question → query ChromaDB for top-k chunks → build a grounded system prompt → call the chat provider → return `{answer, sources, timings}`. |
| `providers.py` | **LLM provider abstraction.** Defines `EmbeddingProvider` and `ChatProvider` abstract base classes with concrete implementations for Ollama, OpenAI, and Anthropic. Factory functions `get_embedding_provider()` and `get_chat_provider()` instantiate the correct provider based on `config.yaml`. Uses raw HTTP requests — no vendor SDKs. |

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| [`chromadb`](https://pypi.org/project/chromadb/) | ≥0.4.0 | Read-only access to the ChromaDB vector database |
| [`requests`](https://pypi.org/project/requests/) | ≥2.31.0 | HTTP client for Ollama, OpenAI, and Anthropic APIs |
| [`PyYAML`](https://pypi.org/project/PyYAML/) | ≥6.0 | Parse `config.yaml` configuration file |
| [`python-dotenv`](https://pypi.org/project/python-dotenv/) | ≥1.0.0 | Load `.env` file for API keys |
| [`fastapi`](https://pypi.org/project/fastapi/) | ≥0.111.0 | HTTP API framework for the always-on query service |
| [`uvicorn[standard]`](https://pypi.org/project/uvicorn/) | ≥0.29.0 | ASGI server to run the FastAPI application |
