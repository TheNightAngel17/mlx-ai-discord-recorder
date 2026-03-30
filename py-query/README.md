# py-query — CLI RAG Query Tool

A command-line RAG (Retrieval-Augmented Generation) tool that lets you ask natural-language questions about your recorded and vectorized D&D sessions.

`py-query` reads from the ChromaDB vector database populated by `py-process/vectorize.py`, retrieves the most relevant transcript chunks, and uses a configurable LLM to synthesize an answer — with source citations pointing back to the session name and timestamps.

---

## What It Does

1. **Embeds your question** using the configured embedding provider (Ollama or OpenAI).
2. **Retrieves the top-k most relevant transcript chunks** from ChromaDB.
3. **Generates an answer** using a configurable chat provider (Ollama, OpenAI, or Anthropic), grounded in the retrieved context.
4. **Prints the answer** and, optionally, the source chunks with session name, timestamps, speakers, and similarity score.

---

## Prerequisites

- **Python 3.11+**
- **ChromaDB data** — you must have run `py-process/vectorize.py` at least once to index your sessions.
- **A running LLM backend** (depending on which providers you configure):
  - **Ollama** — install from <https://ollama.com/> and pull your models:
    ```bash
    ollama pull nomic-embed-text   # default embedding model
    ollama pull llama3             # default chat model
    ```
  - **OpenAI** — set `OPENAI_API_KEY` in your `.env` file.
  - **Anthropic** — set `ANTHROPIC_API_KEY` in your `.env` file.

---

## Installation

```bash
cd py-query
pip install -r requirements.txt
```

---

## Configuration

All settings are in the root `config.yaml`. The relevant fields for `py-query` are:

```yaml
# Vector DB location (must match what vectorize.py used)
vector_db_directory: D:/mlx-ai-vectordb

# ── Embedding provider ─────────────────────────────────────────────────────
# MUST match the provider used when the sessions were vectorized.
# Changing this without re-vectorizing will produce garbage results.
#
# Supported: ollama | openai
# NOTE: Anthropic does NOT offer an embedding API.
embedding_provider: ollama
embedding_model: nomic-embed-text   # e.g. nomic-embed-text, text-embedding-3-small

# ── Chat provider ──────────────────────────────────────────────────────────
# The LLM used to generate the final answer. Can be changed freely.
#
# Supported: ollama | openai | anthropic
chat_provider: ollama
chat_model: llama3                  # e.g. llama3, gpt-4o, claude-sonnet-4-20250514

# ── Ollama settings (only used when provider is "ollama") ──────────────────
ollama_base_url: http://localhost:11434
```

### API Keys

Cloud provider API keys go in the root `.env` file — **never** in `config.yaml`:

```dotenv
# Only needed if using openai or anthropic providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### Provider Combinations

| `embedding_provider` | `chat_provider` | Notes |
|---|---|---|
| `ollama` | `ollama` | Fully local — no internet required |
| `ollama` | `openai` | Local embeddings, cloud chat |
| `ollama` | `anthropic` | Local embeddings, Claude for answers |
| `openai` | `openai` | Fully cloud-based |
| `openai` | `anthropic` | OpenAI embeddings, Claude for answers |
| `openai` | `ollama` | Cloud embeddings, local chat |
| `anthropic` | `*` | ❌ **Not supported** — Anthropic has no embedding API |

> ⚠️ **Warning:** The embedding provider used at query time **must match** the one used when you ran `vectorize.py`. If you switch `embedding_provider`, re-run `python vectorize.py --all --force` to re-index with the new provider.

---

## Usage

```bash
# Basic question
python query.py "What happened when the party entered the cave?"

# Filter to a specific session
python query.py "Who attacked the dragon?" --session 20260330_033020_test

# Show retrieved source chunks after the answer
python query.py "What treasure did the party find?" --show-sources

# Retrieve more context (default is 5 chunks)
python query.py "Describe the final boss fight" --top-k 10 --show-sources
```

### Arguments

| Argument | Type | Description |
|---|---|---|
| `question` | positional | The question to ask (required) |
| `--session NAME` | optional | Restrict search to a specific session folder name |
| `--top-k N` | optional | Number of chunks to retrieve (default: `5`) |
| `--show-sources` | flag | Print retrieved chunks alongside the answer |

---

## Example Output

```
Embedding : ollama / nomic-embed-text
Chat      : ollama / llama3
Top-k     : 5

Embedding question and retrieving context...
============================================================
ANSWER
============================================================
When the party entered the cave, they were immediately ambushed by two goblin
scouts. Aragorn led the charge while Legolas provided covering fire from the
entrance. The goblins were defeated quickly, but not before one of them
triggered an alarm horn, alerting the rest of the goblin lair deeper inside.

[Source: 20260330_033020_test, 00:42:15 → 00:45:30]

============================================================
SOURCES (5 chunks retrieved)
============================================================
[1] Session : 20260330_033020_test
    Time    : 00:42:15 → 00:45:30
    Speakers: TheNightAngel17, PlayerTwo
    Distance: 0.1823
    Text    :
      TheNightAngel17: You enter the cave and see two goblins guarding the entrance.
      PlayerTwo: I want to attack the nearest goblin.
      ...
```

---

## Relationship to Other Services

```
js-bot/          → Records Discord voice to per-user WAV files
py-process/      → Transcribes WAVs, merges audio, vectorizes transcripts into ChromaDB
py-query/        → Queries ChromaDB + LLM to answer questions about the sessions (this service)
```

`py-query` is **read-only** — it never writes to ChromaDB. It is safe to run at any time, even while `py-process` is actively vectorizing.
