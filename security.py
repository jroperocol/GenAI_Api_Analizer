"""Security helpers for masking and sensitive value detection."""

from __future__ import annotations

import re
from typing import Dict

SUSPECT_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def contains_likely_credentials(text: str) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in SUSPECT_PATTERNS)


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible)}{value[-visible:]}"


def mask_sensitive_headers(headers: Dict[str, str]) -> Dict[str, str]:
    masked = {}
    for key, val in headers.items():
        if key.lower() in {"authorization", "x-api-key", "api-key"}:
            masked[key] = mask_secret(val)
        else:
            masked[key] = val
    return masked
