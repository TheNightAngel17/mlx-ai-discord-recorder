#!/usr/bin/env python3
"""
providers.py — Provider abstraction layer for embeddings and chat completions.

Supports multiple LLM backends via a common interface:
  - Embedding: ollama, openai, voyage
  - Chat:      ollama, openai, anthropic

NOTE: Anthropic does NOT offer an embedding API. If embedding_provider is set
to 'anthropic', a clear error is raised. Use 'ollama', 'openai', or 'voyage' for embeddings.

API keys for cloud providers are loaded from environment variables (set via .env):
  OPENAI_API_KEY     — required when using openai embedding or chat provider
  ANTHROPIC_API_KEY  — required when using anthropic chat provider
  VOYAGE_API_KEY     — required when using voyage embedding provider
"""

import os
import sys
from abc import ABC, abstractmethod

import requests


# ---------------------------------------------------------------------------
# Abstract base classes
# ---------------------------------------------------------------------------

class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embed a text string and return the embedding vector."""


class ChatProvider(ABC):
    """Abstract base class for chat/completion providers."""

    @abstractmethod
    def chat(self, system_prompt: str, user_message: str) -> str:
        """Send a chat request and return the assistant's response text."""


# ---------------------------------------------------------------------------
# Embedding implementations
# ---------------------------------------------------------------------------

class OllamaEmbedding(EmbeddingProvider):
    """Embedding via a local Ollama instance."""

    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def embed(self, text: str) -> list[float]:
        url = f"{self.base_url}/api/embeddings"
        try:
            resp = requests.post(
                url,
                json={"model": self.model, "prompt": text},
                timeout=60,
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(
                f"Error: Could not connect to Ollama at {self.base_url}. Is Ollama running?",
                file=sys.stderr,
            )
            sys.exit(1)
        except requests.exceptions.HTTPError as exc:
            print(f"Error: Ollama embeddings API returned an error: {exc}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("Error: Ollama embeddings API request timed out.", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        if "embedding" not in data:
            print(
                f"Error: Ollama response missing 'embedding' field: {data}",
                file=sys.stderr,
            )
            sys.exit(1)
        return data["embedding"]


class OpenAIEmbedding(EmbeddingProvider):
    """Embedding via the OpenAI embeddings API."""

    _API_URL = "https://api.openai.com/v1/embeddings"

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    def embed(self, text: str) -> list[float]:
        try:
            resp = requests.post(
                self._API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": text},
                timeout=60,
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print("Error: Could not connect to OpenAI API.", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as exc:
            print(f"Error: OpenAI embeddings API returned an error: {exc}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("Error: OpenAI embeddings API request timed out.", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError):
            print(
                f"Error: Unexpected response from OpenAI embeddings API: {data}",
                file=sys.stderr,
            )
            sys.exit(1)


class VoyageAIEmbedding(EmbeddingProvider):
    """Embedding via the Voyage AI embeddings API."""

    _API_URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    def embed(self, text: str) -> list[float]:
        try:
            resp = requests.post(
                self._API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": [text]},
                timeout=60,
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print("Error: Could not connect to Voyage AI API.", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as exc:
            print(f"Error: Voyage AI embeddings API returned an error: {exc}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("Error: Voyage AI embeddings API request timed out.", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        try:
            return data["data"][0]["embedding"]
        except (KeyError, IndexError):
            print(
                f"Error: Unexpected response from Voyage AI embeddings API: {data}",
                file=sys.stderr,
            )
            sys.exit(1)


# ---------------------------------------------------------------------------
# Chat implementations
# ---------------------------------------------------------------------------

class OllamaChat(ChatProvider):
    """Chat completions via a local Ollama instance."""

    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def chat(self, system_prompt: str, user_message: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print(
                f"Error: Could not connect to Ollama at {self.base_url}. Is Ollama running?",
                file=sys.stderr,
            )
            sys.exit(1)
        except requests.exceptions.HTTPError as exc:
            print(f"Error: Ollama chat API returned an error: {exc}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("Error: Ollama chat API request timed out.", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        try:
            return data["message"]["content"]
        except KeyError:
            print(
                f"Error: Unexpected response from Ollama chat API: {data}",
                file=sys.stderr,
            )
            sys.exit(1)


class OpenAIChat(ChatProvider):
    """Chat completions via the OpenAI chat completions API."""

    _API_URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    def chat(self, system_prompt: str, user_message: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        try:
            resp = requests.post(
                self._API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print("Error: Could not connect to OpenAI API.", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as exc:
            print(f"Error: OpenAI chat API returned an error: {exc}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("Error: OpenAI chat API request timed out.", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            print(
                f"Error: Unexpected response from OpenAI chat API: {data}",
                file=sys.stderr,
            )
            sys.exit(1)


class AnthropicChat(ChatProvider):
    """Chat completions via the Anthropic Messages API."""

    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    def chat(self, system_prompt: str, user_message: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }
        try:
            resp = requests.post(
                self._API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self._API_VERSION,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            print("Error: Could not connect to Anthropic API.", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as exc:
            print(f"Error: Anthropic API returned an error: {exc}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.Timeout:
            print("Error: Anthropic API request timed out.", file=sys.stderr)
            sys.exit(1)

        data = resp.json()
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError):
            print(
                f"Error: Unexpected response from Anthropic API: {data}",
                file=sys.stderr,
            )
            sys.exit(1)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def get_embedding_provider(config: dict) -> EmbeddingProvider:
    """
    Instantiate and return the configured embedding provider.

    Reads `embedding_provider` from config (default: 'ollama').
    Supported values: 'ollama', 'openai', 'voyage'

    NOTE: Anthropic does not offer an embedding API. Attempting to use
    'anthropic' as the embedding provider will raise an error. Use
    'ollama', 'openai', or 'voyage' for embeddings.
    """
    provider = config.get("embedding_provider", "ollama").lower()
    model = config.get("embedding_model", "nomic-embed-text")

    if provider == "ollama":
        base_url = config.get("ollama_base_url", "http://localhost:11434")
        return OllamaEmbedding(model=model, base_url=base_url)

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print(
                "Error: OPENAI_API_KEY environment variable is not set. "
                "Add it to your .env file.",
                file=sys.stderr,
            )
            sys.exit(1)
        return OpenAIEmbedding(model=model, api_key=api_key)

    if provider == "voyage":
        api_key = os.environ.get("VOYAGE_API_KEY", "")
        if not api_key:
            print(
                "Error: VOYAGE_API_KEY environment variable is not set. "
                "Add it to your .env file.",
                file=sys.stderr,
            )
            sys.exit(1)
        return VoyageAIEmbedding(model=model, api_key=api_key)

    if provider == "anthropic":
        print(
            "Error: Anthropic does not offer an embedding API. "
            "Use 'ollama', 'openai', or 'voyage' for embeddings (set embedding_provider in config.yaml).",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Error: Unknown embedding_provider '{provider}'. "
        "Supported values: ollama, openai, voyage",
        file=sys.stderr,
    )
    sys.exit(1)


def get_chat_provider(config: dict) -> ChatProvider:
    """
    Instantiate and return the configured chat provider.

    Reads `chat_provider` from config (default: 'ollama').
    Supported values: 'ollama', 'openai', 'anthropic'
    """
    provider = config.get("chat_provider", "ollama").lower()
    model = config.get("chat_model", "llama3")

    if provider == "ollama":
        base_url = config.get("ollama_base_url", "http://localhost:11434")
        return OllamaChat(model=model, base_url=base_url)

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print(
                "Error: OPENAI_API_KEY environment variable is not set. "
                "Add it to your .env file.",
                file=sys.stderr,
            )
            sys.exit(1)
        return OpenAIChat(model=model, api_key=api_key)

    if provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print(
                "Error: ANTHROPIC_API_KEY environment variable is not set. "
                "Add it to your .env file.",
                file=sys.stderr,
            )
            sys.exit(1)
        return AnthropicChat(model=model, api_key=api_key)

    print(
        f"Error: Unknown chat_provider '{provider}'. "
        "Supported values: ollama, openai, anthropic",
        file=sys.stderr,
    )
    sys.exit(1)
