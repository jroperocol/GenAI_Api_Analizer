"""Prompt builders for OpenAI extraction."""

from __future__ import annotations


def build_extraction_prompt(doc_text: str, allow_sensitive: bool = False) -> str:
    """Create constrained prompt for JSON extraction."""
    sensitive_rule = (
        "Sensitive extraction is ENABLED: if credentials/tokens/keys are explicitly present in the documentation, "
        "preserve them exactly in relevant auth/header/body fields."
        if allow_sensitive
        else "Sensitive extraction is DISABLED: do NOT output real secrets. Replace credential values with null, blank, or <REDACTED>."
    )

    return f"""
You are an API documentation extraction engine.
Return ONLY valid JSON with no markdown fences and no explanatory text.
Never invent values. If unknown, set null, empty string, empty array, or empty object according to schema.
Only extract endpoints explicitly stated in provided documentation text.

Output schema root keys:
{{
  "document_analysis": {{
    "source_files": [],
    "api_count": 0,
    "apis": [
      {{
        "id": "api_1",
        "name": "",
        "description": "",
        "status": "needs_review",
        "method": "GET|POST|PUT|PATCH|DELETE|null",
        "endpoint": {{"raw":"", "qa":null, "prd":null, "base_url":"", "path":""}},
        "auth": {{"type":"basic|token|no_auth|unknown", "goflow_supported":true, "required":true, "location":"header", "notes":""}},
        "headers": [{{"name":"", "value":null, "required":false, "sensitive":false, "variable_name":null, "description":null}}],
        "path_params": [],
        "query_params": [],
        "body": {{"required":false, "content_type":null, "schema":{{}}, "example":null}},
        "example_response": null,
        "environment_variables": [{{"key":"", "initial_value":"", "current_value":"", "sensitive":false}}],
        "missing_fields": [],
        "errors": [{{"code":"", "message":"", "severity":"blocking|non_blocking"}}],
        "warnings": [{{"code":"", "message":"", "severity":"blocking|non_blocking"}}],
        "source_evidence": [{{"file_name":"", "excerpt":""}}]
      }}
    ]
  }}
}}

Rules:
- Detect only real endpoints. No guessing.
- Classify auth; unsupported auth schemes must be auth.type="unknown" and goflow_supported=false.
- Include missing_fields, errors, warnings.
- Separate QA and PRD URLs when present.
- Detect body content type.
- Map path/query/header/body parameters.
- Status should initially be needs_review unless clearly blocked/unsupported.
- {sensitive_rule}

Documentation text:
{doc_text}
""".strip()
