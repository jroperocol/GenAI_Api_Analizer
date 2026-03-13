"""Manual API execution helpers."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests


class RequestExecutionError(RuntimeError):
    """Raised when request execution fails."""


def execute_test_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    params: Dict[str, str],
    body: Optional[str],
    timeout_seconds: int = 20,
) -> Dict[str, Any]:
    """Execute a request with requests library and return structured response details."""
    if not url:
        raise RequestExecutionError("URL is required to run a test request.")

    request_kwargs: Dict[str, Any] = {
        "headers": headers,
        "params": params,
        "timeout": timeout_seconds,
    }

    if body:
        content_type = headers.get("Content-Type", "")
        if "application/json" in content_type:
            request_kwargs["data"] = body
        elif "application/x-www-form-urlencoded" in content_type:
            request_kwargs["data"] = params
        else:
            request_kwargs["data"] = body

    started = time.perf_counter()
    try:
        resp = requests.request(method=method.upper(), url=url, **request_kwargs)
    except requests.RequestException as exc:
        raise RequestExecutionError(str(exc)) from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    return {
        "status_code": resp.status_code,
        "response_time_ms": elapsed_ms,
        "headers": dict(resp.headers),
        "body": resp.text,
    }
