"""OpenAI-backed extraction pipeline."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from openai import OpenAI
from pydantic import ValidationError

from models import AnalysisPayload
from prompts import build_extraction_prompt


class ExtractionError(RuntimeError):
    """Raised when extraction fails."""


def _strip_json_fences(text: str) -> str:
    """Handle occasional fenced JSON wrappers from LLM responses."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def _load_json_from_response(raw_text: str) -> Dict[str, Any]:
    """Safely parse JSON, including common wrapper artifacts."""
    cleaned = _strip_json_fences(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def extract_apis_with_openai(api_key: str, doc_text: str, model: str = "gpt-4.1-mini") -> AnalysisPayload:
    """Run extraction and return validated payload."""
    if not api_key:
        raise ExtractionError("OpenAI API key is required.")
    if not doc_text.strip():
        raise ExtractionError("No documentation text to analyze.")

    client = OpenAI(api_key=api_key)
    prompt = build_extraction_prompt(doc_text)
    try:
        response = client.responses.create(
            model=model,
            input=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            temperature=0,
        )
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"OpenAI request failed: {exc}") from exc

    raw_text = (response.output_text or "").strip()
    if not raw_text:
        raise ExtractionError("Model returned an empty response.")

    try:
        parsed = _load_json_from_response(raw_text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"Model did not return valid JSON: {exc}") from exc

    try:
        payload = AnalysisPayload.model_validate(parsed)
    except ValidationError as exc:
        raise ExtractionError(f"Model JSON did not match schema: {exc}") from exc

    payload.document_analysis.api_count = len(payload.document_analysis.apis)
    return payload
