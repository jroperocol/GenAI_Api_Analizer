"""Manual API execution helpers."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import requests


class RequestExecutionError(RuntimeError):
    """Raised when request execution fails."""


def _split_cookies(headers: Dict[str, str]) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    cookie_header = headers.get("Cookie") or headers.get("cookie")
    if not cookie_header:
        return cookies

    for part in cookie_header.split(";"):
        chunk = part.strip()
        if not chunk:
            continue
        if "=" in chunk:
            key, val = chunk.split("=", 1)
            cookies[key.strip()] = val.strip()
    return cookies


def execute_test_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    params: Dict[str, str],
    body: Optional[str],
    timeout_seconds: int = 30,
) -> Dict[str, Any]:
    """Execute a request with requests and return response metadata/body."""
    if not url:
        raise RequestExecutionError("URL is required to run a test request.")

    prepared_headers = {k: v for k, v in headers.items() if k and v is not None and str(v) != ""}
    cookies = _split_cookies(prepared_headers)

    request_kwargs: Dict[str, Any] = {
        "headers": prepared_headers,
        "params": {k: v for k, v in params.items() if k and (v != "" or v is not None)},
        "cookies": cookies,
        "timeout": timeout_seconds,
    }

    content_type = (prepared_headers.get("Content-Type") or prepared_headers.get("content-type") or "").lower()

    if body:
        if "application/json" in content_type:
            try:
                request_kwargs["json"] = json.loads(body)
            except json.JSONDecodeError as exc:
                raise RequestExecutionError(f"Invalid JSON body: {exc}") from exc
        elif "application/x-www-form-urlencoded" in content_type:
            try:
                parsed_body = json.loads(body)
                if isinstance(parsed_body, dict):
                    request_kwargs["data"] = parsed_body
                else:
                    request_kwargs["data"] = body
            except json.JSONDecodeError:
                request_kwargs["data"] = body
        elif "multipart/form-data" in content_type:
            try:
                parsed_body = json.loads(body)
                if isinstance(parsed_body, dict):
                    request_kwargs["data"] = parsed_body
                else:
                    request_kwargs["data"] = body
            except json.JSONDecodeError:
                request_kwargs["data"] = body
        else:
            request_kwargs["data"] = body

    started = time.perf_counter()
    try:
        resp = requests.request(method=method.upper(), url=url, **request_kwargs)
    except requests.RequestException as exc:
        raise RequestExecutionError(str(exc)) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response_text = resp.text

    return {
        "status_code": resp.status_code,
        "response_time_ms": elapsed_ms,
        "response_size_bytes": len(response_text.encode("utf-8")),
        "headers": dict(resp.headers),
        "body": response_text,
    }
