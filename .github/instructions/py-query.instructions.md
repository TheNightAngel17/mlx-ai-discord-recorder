---
applyTo: "py-query/**"
---

# Python RAG Query Service — Copilot Instructions

## Language & Runtime

- **Runtime:** Python 3.11+
- **Style:** Follow PEP 8. Use type hints on all function signatures.
- **Imports:** Standard library → third-party → local, separated by blank lines.
- **Naming:** snake_case for variables/functions, PascalCase for classes, UPPER_SNAKE_CASE for constants.
- **Logging:** `logger = logging.getLogger(__name__)`. CLI output in `query.py` may use `print()` for user-facing results — all other modules must use the logger.
- **Docstrings:** Triple-quote docstrings on all public functions and classes, with parameter descriptions for non-obvious arguments.
- **Path handling:** Always use `pathlib.Path` — never string concatenation for paths.
- **Config loading:** Read from `../config.yaml` (relative to script location) using `PyYAML`. Never hardcode paths or settings.

## Naming Conventions

| Context | Convention | Example |
|---|---|---|
| Variables / functions | snake_case | `query_collection()`, `build_prompt()` |
| Classes | PascalCase | `OllamaChat`, `EmbeddingProvider` |
| Constants | UPPER_SNAKE_CASE | `DEFAULT_MODEL`, `MAX_RESULTS` |
| Config keys | snake_case | `embedding_provider`, `ollama_model` |

## No Vendor LLM SDKs

`providers.py` uses raw `requests` calls against provider REST APIs. **Do not** add `openai`, `anthropic`, `langchain`, or any other LLM SDK package. If a new provider is needed, add it to `providers.py` using the existing pattern:

- API keys come from environment variables, never from `config.yaml`.
- Pass API keys in HTTP headers (`Authorization: Bearer` or `x-api-key`) — never in query strings or request bodies.
- Always include a timeout on `requests.post()` / `requests.get()` calls.
- Document the new outbound endpoint in `py-query/README.md`.

## Architecture Decisions (do not change without discussion)

- **One ChromaDB collection per category.** A query resolves its collection from the requested `category` (default `dnd`) via `categories.py` (`resolve_category` → `collection_name`); within a collection, `session_name`/`subcategory` metadata filter results. Do not hardcode collection names.
- **No vendor SDKs.** Raw `requests` only. Keeps the dependency surface minimal and auditable.
- **`subprocess.run()` with list arguments only.** Never `shell=True`.

## Code Quality Checklist

1. Imports organized: stdlib → third-party → local.
2. No unused imports or variables. Remove dead code.
3. All public functions and classes have docstrings.
4. Every `subprocess.run()` checks the return code.
5. Every HTTP call includes a timeout.
6. No hardcoded values — anything configurable goes in `config.yaml`.
7. Use `pathlib.Path` for all file and directory operations.

## Common Pitfalls

- **Changing `embedding_provider` or `embedding_model`** after vectorizing breaks RAG queries — embeddings stored in ChromaDB will no longer match the query embedding space. Always warn users to re-run `python py-process/vectorize.py --all --force`.
- **Config validation:** Don't assume `config.yaml` is well-formed. Check for `None` and fall back to safe defaults.
- **API key leakage:** Never log `Authorization` headers or the values of API key environment variables, even at debug level.
