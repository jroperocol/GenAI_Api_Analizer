"""Export selected API records into Postman collection and environment JSON."""

from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from models import ApiRecord

SENSITIVE_KEYS = {"authorization", "token", "password", "api_key", "x-api-key", "secret", "username"}


def _postman_url(raw_url: str) -> Dict:
    parsed = urlparse(raw_url)
    host_parts = parsed.netloc.split(".") if parsed.netloc else ["{{base_url}}"]
    path_parts = [part for part in parsed.path.split("/") if part]
    return {
        "raw": raw_url,
        "protocol": parsed.scheme or "https",
        "host": host_parts,
        "path": path_parts,
        "query": [{"key": k, "value": v} for k, v in [q.split("=", 1) if "=" in q else (q, "") for q in parsed.query.split("&") if q]],
    }


def build_postman_collection_and_env(
    apis: List[ApiRecord],
    include_current_values: bool = False,
    session_values: Optional[Dict[str, str]] = None,
) -> Tuple[str, str]:
    used_env_keys: Set[str] = {"base_url"}
    items = []
    session_values = session_values or {}

    for api in apis:
        base_url = api.endpoint.base_url or "{{base_url}}"
        final_path = api.endpoint.path or api.endpoint.raw
        raw_url = f"{base_url.rstrip('/')}/{final_path.lstrip('/')}" if final_path else base_url
        header_entries = []
        for header in api.headers:
            value = header.value or ""
            if header.variable_name:
                used_env_keys.add(header.variable_name)
                value = f"{{{{{header.variable_name}}}}}"
            header_entries.append({"key": header.name, "value": value, "type": "text"})

        query_entries = []
        for query in api.query_params:
            q_val = query.value or ""
            if query.variable_name:
                used_env_keys.add(query.variable_name)
                q_val = f"{{{{{query.variable_name}}}}}"
            query_entries.append({"key": query.name, "value": q_val, "disabled": not query.required})

        request_name = f"{(api.method.value if api.method else 'GET')} - {api.name or api.id}"
        request_obj = {
            "name": request_name,
            "request": {
                "method": api.method.value if api.method else "GET",
                "header": header_entries,
                "url": _postman_url(raw_url),
            },
            "response": [],
        }

        if query_entries:
            request_obj["request"]["url"]["query"] = query_entries

        if api.body.example is not None:
            raw_body = api.body.example if isinstance(api.body.example, str) else json.dumps(api.body.example, indent=2)
            request_obj["request"]["body"] = {"mode": "raw", "raw": raw_body}

        items.append(request_obj)

    collection = {
        "info": {
            "name": "GoAI API Export",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "_postman_id": str(uuid.uuid4()),
        },
        "item": items,
    }

    env_values = []
    for key in sorted(used_env_keys):
        value = "<REPLACE_ME>"
        if include_current_values and key in session_values:
            value = session_values[key]
        env_values.append({"key": key, "value": value, "enabled": True})

    environment = {
        "id": str(uuid.uuid4()),
        "name": "GoAI Environment",
        "values": env_values,
        "_postman_variable_scope": "environment",
        "_postman_exported_at": "",
        "_postman_exported_using": "GoAI API Documentation Interpreter",
    }

    return json.dumps(collection, indent=2), json.dumps(environment, indent=2)
