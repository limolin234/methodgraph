"""Small TOML configuration shared by the server and thin clients."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "methodgraph" / "config.toml"


@dataclass(frozen=True, slots=True)
class ClientConfig:
    server_url: str = "http://127.0.0.1:8765"
    timeout: float = 8.0


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    content_repo: str = "./methodgraph-content"
    database: str = "./methodgraph.db"
    push_remote: str = ""
    committer_name: str = "MethodGraph Server"
    committer_email: str = "methodgraph@localhost"


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    provider: str = "local"
    model: str = "Qwen/Qwen3-Embedding-4B"
    device: str | None = None
    base_url: str = ""
    api_key_env: str = ""
    timeout: float = 30.0
    batch_size: int = 32


@dataclass(frozen=True, slots=True)
class AppConfig:
    client: ClientConfig = ClientConfig()
    server: ServerConfig = ServerConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{key}] must be a TOML table")
    return value


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load TOML and then apply narrowly scoped environment overrides."""
    configured = Path(path or os.environ.get("METHODGRAPH_CONFIG", DEFAULT_CONFIG_PATH))
    data: dict[str, Any] = {}
    if configured.is_file():
        with configured.open("rb") as handle:
            data = tomllib.load(handle)
    client, server, embedding = (_section(data, name) for name in ("client", "server", "embedding"))

    server_url = os.environ.get("METHODGRAPH_SERVER_URL", client.get("server_url", "http://127.0.0.1:8765"))
    host = os.environ.get("METHODGRAPH_HOST", server.get("host", "127.0.0.1"))
    port = int(os.environ.get("METHODGRAPH_PORT", server.get("port", 8765)))
    content_repo = os.environ.get("METHODGRAPH_CONTENT_REPO", server.get("content_repo", "./methodgraph-content"))
    database = os.environ.get("METHODGRAPH_DB", server.get("database", "./methodgraph.db"))
    provider = os.environ.get("METHODGRAPH_EMBEDDING_PROVIDER", embedding.get("provider", "local"))
    model = os.environ.get("METHODGRAPH_EMBEDDING_MODEL", embedding.get("model", "Qwen/Qwen3-Embedding-4B"))
    if model.casefold() in {"none", "off", "false"}:
        provider = "none"
    if provider not in {"local", "openai_compatible", "none"}:
        raise ValueError("embedding.provider must be local, openai_compatible, or none")
    return AppConfig(
        client=ClientConfig(server_url=str(server_url).rstrip("/"), timeout=float(client.get("timeout", 8.0))),
        server=ServerConfig(
            host=str(host), port=port, content_repo=str(content_repo), database=str(database),
            push_remote=str(server.get("push_remote", "")),
            committer_name=str(server.get("committer_name", "MethodGraph Server")),
            committer_email=str(server.get("committer_email", "methodgraph@localhost")),
        ),
        embedding=EmbeddingConfig(
            provider=provider, model=str(model),
            device=os.environ.get("METHODGRAPH_EMBEDDING_DEVICE", embedding.get("device")),
            base_url=str(os.environ.get("METHODGRAPH_EMBEDDING_BASE_URL",
                                        embedding.get("base_url", ""))).rstrip("/"),
            api_key_env=str(os.environ.get("METHODGRAPH_EMBEDDING_API_KEY_ENV",
                                           embedding.get("api_key_env", ""))),
            timeout=float(embedding.get("timeout", 30.0)),
            batch_size=max(1, int(embedding.get("batch_size", 32))),
        ),
    )
