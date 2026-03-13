"""Security helpers for masking, detection, and sensitive-field scrubbing."""

from __future__ import annotations

import re
from typing import Dict

from models import AnalysisPayload

SUSPECT_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization|cookie)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]
SENSITIVE_KEYWORDS = {"authorization", "api-key", "x-api-key", "token", "password", "secret", "cookie"}


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
        if key.lower() in SENSITIVE_KEYWORDS:
            masked[key] = mask_secret(val)
        else:
            masked[key] = val
    return masked


def is_sensitive_key(name: str) -> bool:
    return any(keyword in (name or "").lower() for keyword in SENSITIVE_KEYWORDS)


def sanitize_analysis_payload(payload: AnalysisPayload, allow_sensitive: bool) -> AnalysisPayload:
    """Strip secret values from payload when sensitive extraction is not allowed."""
    if allow_sensitive:
        return payload

    for api in payload.document_analysis.apis:
        for header in api.headers:
            if header.sensitive or is_sensitive_key(header.name) or is_sensitive_key(header.variable_name or ""):
                header.value = "<REDACTED>"
                header.sensitive = True
        for env_var in api.environment_variables:
            if env_var.sensitive or is_sensitive_key(env_var.key):
                env_var.initial_value = "<REDACTED>"
                env_var.current_value = "<REDACTED>"
                env_var.sensitive = True
        if isinstance(api.body.example, str) and contains_likely_credentials(api.body.example):
            api.body.example = "<REDACTED>"
    return payload
