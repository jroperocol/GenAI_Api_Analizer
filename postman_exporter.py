"""Export selected API records into Postman collection and environment JSON."""

from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from models import ApiRecord
from security import is_sensitive_key


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
    include_sensitive_values: bool = False,
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
            var_name = header.variable_name or header.name.lower().replace("-", "_")
            used_env_keys.add(var_name)

            is_sensitive = header.sensitive or is_sensitive_key(header.name) or is_sensitive_key(var_name)
            if is_sensitive and not include_sensitive_values:
                session_values.setdefault(var_name, "<REPLACE_ME>")
            elif header.value:
                session_values[var_name] = header.value

            header_entries.append({"key": header.name, "value": f"{{{{{var_name}}}}}", "type": "text"})

        query_entries = []
        for query in api.query_params:
            var_name = query.variable_name or query.name.lower()
            used_env_keys.add(var_name)
            if query.value:
                session_values[var_name] = query.value
            query_entries.append({"key": query.name, "value": f"{{{{{var_name}}}}}", "disabled": not query.required})

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
        sensitive = is_sensitive_key(key)
        value = "<REPLACE_ME>"
        if include_current_values and key in session_values:
            if sensitive and not include_sensitive_values:
                value = "<REPLACE_ME>"
            else:
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
