"""Export selected API records into Postman collection and environment JSON."""

from __future__ import annotations

import json
import re
import uuid
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from models import ApiRecord
from security import is_sensitive_key

PATH_PARAM_PATTERNS = [re.compile(r"\{([^}]+)\}"), re.compile(r":([A-Za-z0-9_\-]+)")]


def _postman_url(raw_url: str) -> Dict:
    parsed = urlparse(raw_url)
    host_parts = parsed.netloc.split(".") if parsed.netloc else ["{{base_url}}"]
    path_parts = [part for part in parsed.path.split("/") if part]
    return {
        "raw": raw_url,
        "protocol": parsed.scheme or "https",
        "host": host_parts,
        "path": path_parts,
        "query": [
            {"key": k, "value": v}
            for k, v in [q.split("=", 1) if "=" in q else (q, "") for q in parsed.query.split("&") if q]
        ],
    }


def _body_mode(content_type: str | None) -> str:
    ct = (content_type or "").lower()
    if "application/x-www-form-urlencoded" in ct:
        return "urlencoded"
    if "multipart/form-data" in ct:
        return "formdata"
    return "raw"


def _header_var_name(name: str, existing: Optional[str]) -> str:
    if existing:
        return existing
    return name.lower().replace("-", "_").strip()


def _apply_path_param_variables(path_or_url: str, path_values: Dict[str, str], use_literals: bool) -> str:
    rendered = path_or_url
    for key, value in path_values.items():
        replacement = value if use_literals and value else f"{{{{{key}}}}}"
        rendered = rendered.replace(f"{{{key}}}", replacement)
        rendered = rendered.replace(f":{key}", replacement)
    return rendered


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
        raw_target = api.endpoint.path or api.endpoint.raw

        # Path params are always exported as variables when names are known.
        path_param_values: Dict[str, str] = {}
        for p in api.path_params:
            if not p.name:
                continue
            used_env_keys.add(p.name)
            path_param_values[p.name] = p.value or ""
            if p.value is not None:
                session_values[p.name] = p.value

        literal_mode = include_current_values and include_sensitive_values
        variable_path = _apply_path_param_variables(raw_target, path_param_values, use_literals=literal_mode)
        raw_url = f"{base_url.rstrip('/')}/{variable_path.lstrip('/')}" if variable_path else base_url

        header_entries = []
        for header in api.headers:
            if not header.name:
                continue
            var_name = _header_var_name(header.name, header.variable_name)
            if not var_name:
                continue
            used_env_keys.add(var_name)

            sensitive = header.sensitive or is_sensitive_key(header.name) or is_sensitive_key(var_name)
            header_value = ""
            if header.value is not None:
                header_value = str(header.value)

            if header_value and (include_sensitive_values or not sensitive):
                session_values[var_name] = header_value
            elif sensitive and not include_sensitive_values:
                session_values.setdefault(var_name, "<REPLACE_ME>")

            use_literal = include_current_values and header_value and (include_sensitive_values or not sensitive)
            resolved = header_value if use_literal else f"{{{{{var_name}}}}}"
            header_entries.append({"key": header.name, "value": resolved, "type": "text"})

        # Ensure content-type can be exported even if not listed as a header row.
        if api.body.content_type and not any(h.get("key", "").lower() == "content-type" for h in header_entries):
            used_env_keys.add("content_type")
            session_values.setdefault("content_type", api.body.content_type)
            ct_value = api.body.content_type if (include_current_values and include_sensitive_values and api.body.content_type) else "{{content_type}}"
            header_entries.append({"key": "Content-Type", "value": ct_value, "type": "text"})

        query_entries = []
        for query in api.query_params:
            if not query.name:
                continue
            var_name = (query.variable_name or query.name.lower()).strip()
            if not var_name:
                continue
            used_env_keys.add(var_name)
            q_value = "" if query.value is None else str(query.value)
            if query.value is not None:
                session_values[var_name] = q_value
            use_literal = include_current_values and q_value != ""
            query_entries.append({"key": query.name, "value": q_value if use_literal else f"{{{{{var_name}}}}}", "disabled": not query.required})

        request_obj = {
            "name": f"{(api.method.value if api.method else 'GET')} - {api.name or api.id}",
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
            mode = _body_mode(api.body.content_type)
            if mode == "urlencoded" and isinstance(api.body.example, dict):
                request_obj["request"]["body"] = {
                    "mode": "urlencoded",
                    "urlencoded": [{"key": k, "value": str(v), "type": "text"} for k, v in api.body.example.items()],
                }
            elif mode == "formdata" and isinstance(api.body.example, dict):
                request_obj["request"]["body"] = {
                    "mode": "formdata",
                    "formdata": [{"key": k, "value": str(v), "type": "text"} for k, v in api.body.example.items()],
                }
            else:
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
        if key in session_values:
            if include_current_values and (include_sensitive_values or not sensitive):
                value = session_values[key]
            elif include_current_values and sensitive and not include_sensitive_values:
                value = "<REPLACE_ME>"
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
