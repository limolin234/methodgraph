"""Dependency-free JSON client used by Hook and MCP adapters."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import load_config


class MethodGraphHTTPError(RuntimeError):
    pass


def request_json(method: str, path: str, payload: dict[str, Any] | None = None,
                 *, timeout: float | None = None) -> dict[str, Any]:
    config = load_config().client
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{config.server_url}{path}", data=body, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout or config.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MethodGraphHTTPError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MethodGraphHTTPError(str(exc)) from exc
    if not isinstance(result, dict):
        raise MethodGraphHTTPError("server returned a non-object JSON response")
    return result


def get_json(path: str) -> dict[str, Any]:
    return request_json("GET", path)


def post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("POST", path, payload)


def patch_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("PATCH", path, payload)


def delete_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return request_json("DELETE", path, payload)
