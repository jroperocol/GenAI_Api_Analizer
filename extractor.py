"""OpenAI-backed extraction pipeline."""

from __future__ import annotations

import json
from typing import Any, Dict

from openai import OpenAI
from pydantic import ValidationError

from models import AnalysisPayload
from prompts import build_extraction_prompt


class ExtractionError(RuntimeError):
    """Raised when extraction fails."""


def extract_apis_with_openai(api_key: str, doc_text: str, model: str = "gpt-4.1-mini") -> AnalysisPayload:
    """Run extraction and return validated payload."""
    if not api_key:
        raise ExtractionError("OpenAI API key is required.")
    if not doc_text.strip():
        raise ExtractionError("No documentation text to analyze.")

    client = OpenAI(api_key=api_key)
    prompt = build_extraction_prompt(doc_text)
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        temperature=0,
    )

    raw_text = response.output_text.strip()
    try:
        parsed: Dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Model did not return valid JSON: {exc}") from exc

    try:
        return AnalysisPayload.model_validate(parsed)
    except ValidationError as exc:
        raise ExtractionError(f"Model JSON did not match schema: {exc}") from exc
