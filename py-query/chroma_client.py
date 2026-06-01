"""
chroma_client.py — Factory for constructing a ChromaDB client from config.

In Docker, both py-process and py-query talk to a dedicated chromadb server
container via HttpClient.  The server owns the single authoritative HNSW index,
so there are no stale-index issues from multiple containers sharing the same
data volume.

Outside Docker (CLI / local dev), a PersistentClient is used instead.

Set ``chroma_server_host`` in config.yaml (or deploy/config.docker.yaml) to
enable server mode; omit it (or leave it null) to use the local PersistentClient.
"""

from pathlib import Path

import chromadb


def get_chroma_client(config: dict) -> chromadb.ClientAPI:
    """
    Return a ChromaDB client configured for either server mode or local mode.

    Server mode (``chroma_server_host`` is set in config):
        Returns ``chromadb.HttpClient(host, port)``.  The remote server owns the
        HNSW index, so queries always reflect the latest writes regardless of
        which container wrote them.

    Local mode (``chroma_server_host`` is absent or null):
        Returns ``chromadb.PersistentClient(path)``.  Suitable for single-process
        CLI use.  In a multi-container stack this will yield stale HNSW results,
        which is why the Docker stack runs a dedicated chromadb service.

    Args:
        config: Parsed config.yaml dict.

    Returns:
        A ``chromadb.ClientAPI`` instance.
    """
    host = config.get("chroma_server_host")
    if host:
        port = int(config.get("chroma_server_port", 8000))
        return chromadb.HttpClient(host=str(host), port=port)
    path = Path(config.get("vector_db_directory", "./vectordb"))
    return chromadb.PersistentClient(path=str(path))
