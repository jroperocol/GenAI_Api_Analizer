"""Streamlit app for GoAI API Documentation Interpreter."""

from __future__ import annotations

import base64
import io
import json
import time
import zipfile
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from extractor import ExtractionError, extract_apis_with_openai
from file_ingestion import SUPPORTED_TYPES, extract_text_from_uploads
from i18n import t
from models import AnalysisPayload, ApiRecord, ApiStatus, AuthType, HttpMethod, ParamField
from postman_exporter import build_postman_collection_and_env
from security import contains_likely_credentials, mask_secret, mask_sensitive_headers, sanitize_analysis_payload
from test_runner import RequestExecutionError, execute_test_request
from validator import validate_analysis, validate_api

st.set_page_config(page_title="GoAI API Documentation Interpreter", layout="wide")

LANG_NAMES = {"en": "English", "es": "Español", "pt": "Português"}
METHOD_COLORS = {"GET": "#34d399", "POST": "#60a5fa", "PUT": "#f59e0b", "PATCH": "#a78bfa", "DELETE": "#f87171"}
SENSITIVE_HINTS = ("token", "key", "secret", "pass", "cookie", "auth", "credential")


def apply_custom_theme_css() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: radial-gradient(circle at 5% 10%, #1c2442 0%, #0b1022 35%, #070b18 100%); color: #e2e8f0; }
        .main .block-container {padding-top: 1rem; max-width: 1500px;}
        .goai-card { background: rgba(15, 23, 42, 0.68); border: 1px solid rgba(148, 163, 184, 0.22); border-radius: 16px; padding: .8rem 1rem; margin-bottom: .6rem; }
        .section-label {font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: #93c5fd; font-weight: 700; margin-bottom: .4rem;}
        .badge { display:inline-block; border-radius:999px; padding:.15rem .5rem; font-size:.68rem; font-weight:700; margin-right:.3rem; border:1px solid rgba(148,163,184,.35);}
        .toolbar { background: rgba(15,23,42,.72); border: 1px solid rgba(148,163,184,.2); border-radius: 14px; padding: .65rem .8rem; margin-bottom: .6rem; }
        .chip { display:inline-block; padding:.2rem .55rem; border-radius:999px; font-size:.7rem; border:1px solid rgba(148,163,184,.4); margin-right:.35rem; margin-bottom:.35rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_state() -> None:
    defaults = {
        "analysis": AnalysisPayload(),
        "apis": [],
        "selected_api_id": None,
        "active_env": "raw",
        "lang": "en",
        "allow_sensitive_extraction": False,
        "last_response": None,
        "last_response_api_id": None,
        "analysis_error": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value



def _api_label(api: ApiRecord) -> str:
    method = api.method.value if api.method else "-"
    return f"{method} | {api.name or api.id}"



def _get_selected_api(apis: List[ApiRecord]) -> ApiRecord | None:
    if not apis:
        return None
    selected = next((a for a in apis if a.id == st.session_state.selected_api_id), None)
    return selected or apis[0]



def _compose_url(api: ApiRecord, env: str = "raw") -> str:
    if env == "qa" and api.endpoint.qa:
        return api.endpoint.qa
    if env == "prd" and api.endpoint.prd:
        return api.endpoint.prd
    if api.endpoint.raw:
        return api.endpoint.raw
    if api.endpoint.base_url and api.endpoint.path:
        return f"{api.endpoint.base_url.rstrip('/')}/{api.endpoint.path.lstrip('/')}"
    return api.endpoint.base_url or api.endpoint.path or ""



def _param_editor(api_id: str, key: str, data: List[ParamField]) -> List[ParamField]:
    frame = pd.DataFrame([item.model_dump() for item in data]) if data else pd.DataFrame(columns=["name", "value", "required", "sensitive", "variable_name", "description"])
    updated = st.data_editor(frame, num_rows="dynamic", use_container_width=True, key=f"{api_id}_{key}")
    return [ParamField(**row) for row in updated.to_dict(orient="records") if row.get("name")]





def _find_header(api: ApiRecord, header_name: str) -> Optional[ParamField]:
    for header in api.headers:
        if (header.name or "").lower() == header_name.lower():
            return header
    return None


def _upsert_header(api: ApiRecord, name: str, value: str, sensitive: bool = False) -> None:
    existing = _find_header(api, name)
    if existing:
        existing.value = value
        existing.sensitive = sensitive or existing.sensitive
    else:
        api.headers.append(
            ParamField(name=name, value=value, required=True, sensitive=sensitive, variable_name=name.lower().replace("-", "_"))
        )


def _extract_basic_from_headers(api: ApiRecord) -> Tuple[str, str]:
    auth = _find_header(api, "Authorization")
    if not auth or not auth.value:
        return "", ""
    val = str(auth.value).strip()
    if not val.lower().startswith("basic "):
        return "", ""
    token = val.split(" ", 1)[1].strip()
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        if ":" in decoded:
            user, pwd = decoded.split(":", 1)
            return user, pwd
    except Exception:
        return "", ""
    return "", ""


def _extract_token_from_headers(api: ApiRecord) -> Tuple[str, str, str]:
    auth = _find_header(api, "Authorization")
    if auth and auth.value:
        text = str(auth.value).strip()
        if " " in text:
            prefix, token = text.split(" ", 1)
            return "Authorization", prefix, token
        return "Authorization", "", text

    for header in api.headers:
        if header.name and header.value and _is_sensitive_param(header):
            return header.name, "", str(header.value)
    return "Authorization", "Bearer", ""


def _sync_auth_to_headers(api: ApiRecord, auth_type: AuthType, fields: Dict[str, str]) -> None:
    if auth_type == AuthType.BASIC:
        user = fields.get("username", "").strip()
        password = fields.get("password", "")
        if user or password:
            token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("utf-8")
            _upsert_header(api, "Authorization", f"Basic {token}", sensitive=True)
    elif auth_type == AuthType.TOKEN:
        header_name = fields.get("header_name", "Authorization").strip() or "Authorization"
        prefix = fields.get("prefix", "").strip()
        token_value = fields.get("token", "").strip()
        if token_value:
            value = f"{prefix} {token_value}".strip() if prefix else token_value
            _upsert_header(api, header_name, value, sensitive=True)



def _sync_headers_table_from_effective_headers(api: ApiRecord, effective_headers: Dict[str, str]) -> None:
    """Ensure header table values reflect known effective header values."""
    normalized = {k.strip(): str(v) for k, v in effective_headers.items() if k and str(v).strip() != ""}

    existing_by_name = {(h.name or "").lower(): h for h in api.headers if h.name}
    for name, value in normalized.items():
        key = name.lower()
        if key in existing_by_name:
            existing_by_name[key].value = value
        else:
            api.headers.append(
                ParamField(
                    name=name,
                    value=value,
                    required=False,
                    sensitive=any(tok in key for tok in SENSITIVE_HINTS),
                    variable_name=name.lower().replace("-", "_"),
                )
            )


def _build_effective_headers(api: ApiRecord, auth_payload: Dict[str, str]) -> Dict[str, str]:
    """Build effective headers from current request configuration."""
    effective: Dict[str, str] = {}

    for h in api.headers:
        if h.name and h.value is not None and str(h.value).strip() != "":
            effective[h.name] = str(h.value)

    if api.body.content_type:
        effective["Content-Type"] = api.body.content_type

    if api.auth.type == AuthType.BASIC:
        user = auth_payload.get("username", "").strip()
        pwd = auth_payload.get("password", "")
        if user or pwd:
            token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("utf-8")
            effective["Authorization"] = f"Basic {token}"

    elif api.auth.type == AuthType.TOKEN:
        header_name = auth_payload.get("header_name", "Authorization").strip() or "Authorization"
        prefix = auth_payload.get("prefix", "").strip()
        token = auth_payload.get("token", "").strip()
        if token:
            effective[header_name] = f"{prefix} {token}".strip() if prefix else token

    return effective


def _headers_mirror_dataframe(api: ApiRecord, effective_headers: Dict[str, str]) -> pd.DataFrame:
    rows = []
    by_name = {(h.name or "").lower(): h for h in api.headers if h.name}
    for name, value in effective_headers.items():
        item = by_name.get(name.lower())
        source = "ui_override"
        if item and item.description:
            source = "extracted_header"
        if name.lower() == "content-type" and api.body.content_type:
            source = "inferred_content_type"
        rows.append(
            {
                "name": name,
                "value": value,
                "required": bool(item.required) if item else False,
                "sensitive": bool(item.sensitive) if item else any(tok in name.lower() for tok in SENSITIVE_HINTS),
                "variable_name": (item.variable_name if item else name.lower().replace("-", "_")) or "",
                "description": item.description if item else "",
                "source_mapping": source,
            }
        )
    return pd.DataFrame(rows)


def _auth_payload_from_state(api: ApiRecord) -> Dict[str, str]:
    """Read current auth field values from session widgets, falling back to header-derived defaults."""
    payload: Dict[str, str] = {}
    if api.auth.type == AuthType.BASIC:
        default_user, default_pass = _extract_basic_from_headers(api)
        payload["username"] = str(st.session_state.get(f"basic_user_{api.id}", default_user))
        payload["password"] = str(st.session_state.get(f"basic_pass_{api.id}", default_pass))
    elif api.auth.type == AuthType.TOKEN:
        default_header, default_prefix, default_token = _extract_token_from_headers(api)
        payload["header_name"] = str(st.session_state.get(f"token_header_{api.id}", default_header or "Authorization"))
        payload["prefix"] = str(st.session_state.get(f"token_prefix_{api.id}", default_prefix or "Bearer"))
        payload["token"] = str(st.session_state.get(f"token_value_{api.id}", default_token))
    return payload


def _refresh_effective_headers(api: ApiRecord) -> Dict[str, str]:
    """Synchronize auth-derived headers into api.headers and return current effective headers."""
    payload = _auth_payload_from_state(api)
    if payload:
        _sync_auth_to_headers(api, api.auth.type, payload)
    effective = _build_effective_headers(api, payload)
    _sync_headers_table_from_effective_headers(api, effective)
    return effective
def _progress_update(progress_bar, status_box, start: int, target: int, msg: str) -> int:
    for p in range(start, target + 1):
        progress_bar.progress(p)
        status_box.info(msg)
        time.sleep(0.006)
    return target



def _analysis_error_message(lang: str, err: str) -> str:
    text = err.lower()
    if "invalid value" in text or "input_text" in text:
        return t(lang, "analysis_failed_payload")
    if "api key" in text or "unauthorized" in text:
        return t(lang, "analysis_failed_key")
    return t(lang, "analysis_failed_generic")



def _is_sensitive_param(field: ParamField) -> bool:
    name = f"{field.name} {field.variable_name or ''} {field.description or ''}".lower()
    return field.sensitive or any(token in name for token in SENSITIVE_HINTS)



def _summarize_tokens(api: ApiRecord, allow_sensitive: bool) -> str:
    values = []
    for h in api.headers:
        if h.value is None or not h.name:
            continue
        if _is_sensitive_param(h):
            values.append(f"{h.name}={h.value if allow_sensitive else mask_secret(str(h.value))}")
    return "; ".join(values) if values else "-"



def _detect_body_type(api: ApiRecord) -> str:
    ct = (api.body.content_type or "").lower()
    if not api.body.required and api.body.example in (None, "", {}, []):
        return "none"
    if "json" in ct:
        return "json"
    if "x-www-form-urlencoded" in ct:
        return "form-urlencoded"
    if "multipart/form-data" in ct:
        return "multipart"
    if ct:
        return "raw"
    return "none"



def _missing_critical_fields(api: ApiRecord) -> str:
    missing: List[str] = []
    full_url = _compose_url(api, st.session_state.active_env)

    if not api.method:
        missing.append("method")
    if not full_url:
        missing.append("url")
    if api.auth.type is None or api.auth.type == AuthType.UNKNOWN:
        missing.append("auth")

    if api.body.required and (api.body.example in (None, "", {}, []) and not api.body.schema):
        missing.append("body")

    req_headers_missing = any((h.required and (h.value is None or str(h.value).strip() == "")) for h in api.headers)
    if req_headers_missing:
        missing.append("required_headers")

    return ", ".join(dict.fromkeys(missing)) if missing else "READY"



def _build_summary_df(api: ApiRecord, allow_sensitive: bool) -> pd.DataFrame:
    headers = ", ".join([h.name for h in api.headers if h.name]) or "-"
    row = {
        "API Name": api.name or api.id,
        "HTTP Method": api.method.value if api.method else "",
        "Full Endpoint URL": _compose_url(api, st.session_state.active_env),
        "Auth Type": api.auth.type.value,
        "Headers": headers,
        "Body Type": _detect_body_type(api),
        "Tokens / Keys": _summarize_tokens(api, allow_sensitive),
        "Missing Critical Fields": _missing_critical_fields(api),
    }
    return pd.DataFrame([row])



def _run_analysis(lang: str, api_key: str, uploaded_files, raw_text: str, allow_sensitive: bool) -> None:
    st.session_state.analysis_error = ""
    with st.container():
        st.markdown(f'<div class="goai-card"><div class="section-label">{t(lang, "analysis_progress_title")}</div>', unsafe_allow_html=True)
        pbar = st.progress(0)
        status = st.empty()
        st.markdown("</div>", unsafe_allow_html=True)

    p = 0
    try:
        p = _progress_update(pbar, status, p, 10, t(lang, "analysis_stage_10"))
        if not api_key.strip():
            raise ExtractionError(t(lang, "analyze_missing_key"))
        if not raw_text.strip() and not uploaded_files:
            raise ExtractionError(t(lang, "analyze_missing_input"))

        p = _progress_update(pbar, status, p, 20, t(lang, "analysis_stage_20"))
        file_text, source_files, warnings = extract_text_from_uploads(uploaded_files or [])
        p = _progress_update(pbar, status, p, 35, t(lang, "analysis_stage_35"))

        combined_text = "\n\n".join(part for part in [file_text, raw_text] if part.strip())
        if not combined_text.strip():
            raise ExtractionError(t(lang, "analyze_missing_input"))

        if contains_likely_credentials(combined_text):
            st.warning(t(lang, "sensitive_detected"))
        for warning in warnings:
            st.warning(warning)

        p = _progress_update(pbar, status, p, 50, t(lang, "analysis_stage_50"))
        p = _progress_update(pbar, status, p, 65, t(lang, "analysis_stage_65"))
        payload = extract_apis_with_openai(api_key=api_key.strip(), doc_text=combined_text, allow_sensitive=allow_sensitive)

        p = _progress_update(pbar, status, p, 85, t(lang, "analysis_stage_85"))
        payload.document_analysis.source_files = source_files
        payload = sanitize_analysis_payload(payload, allow_sensitive=allow_sensitive)

        p = _progress_update(pbar, status, p, 95, t(lang, "analysis_stage_95"))
        validated = validate_analysis(payload)
        _progress_update(pbar, status, p, 100, t(lang, "analysis_stage_100"))

        st.session_state.analysis = validated
        st.session_state.apis = validated.document_analysis.apis
        selected = _get_selected_api(st.session_state.apis)
        st.session_state.selected_api_id = selected.id if selected else None
        st.success(t(lang, "detected_apis").format(count=validated.document_analysis.api_count))
    except Exception as exc:  # noqa: BLE001
        st.session_state.analysis_error = _analysis_error_message(lang, str(exc))
        status.error(t(lang, "analysis_stage_failed"))
        st.error(st.session_state.analysis_error)
        with st.expander(t(lang, "show_technical_details")):
            st.code(str(exc))



def _build_send_payload(api: ApiRecord, body_text: str) -> Dict[str, Dict[str, str] | str]:
    headers = {h.name: str(h.value) for h in api.headers if h.name and h.value is not None and str(h.value).strip() != ""}
    query: Dict[str, str] = {}
    for q in api.query_params:
        if q.name and str(q.value or "").strip() != "":
            query[q.name] = str(q.value)
        elif q.name and q.required:
            query[q.name] = ""
    return {"headers": headers, "query": query, "body": body_text}



def _render_response_block(lang: str, response: Dict | None) -> None:
    if not response:
        st.info(t(lang, "no_response"))
        return
    st.markdown(
        f'<div class="toolbar"><span class="chip">{t(lang, "status_code")}: <b>{response.get("status_code", "-")}</b></span>'
        f'<span class="chip">{t(lang, "response_time")}: <b>{response.get("response_time_ms", "-")} ms</b></span>'
        f'<span class="chip">{t(lang, "response_size")}: <b>{response.get("response_size_bytes", "-")} B</b></span></div>',
        unsafe_allow_html=True,
    )
    st.caption(t(lang, "response_headers"))
    st.json(mask_sensitive_headers(response.get("headers", {})))
    st.caption(t(lang, "response_body"))
    body = response.get("body", "")
    try:
        st.json(json.loads(body))
    except Exception:  # noqa: BLE001
        st.code(body)



def _test_api(lang: str, api: ApiRecord, method: str, url: str, body_text: str, timeout: int = 30) -> None:
    payload = _build_send_payload(api, body_text)
    try:
        result = execute_test_request(
            method=method,
            url=url,
            headers=payload["headers"],
            params=payload["query"],
            body=payload["body"],
            timeout_seconds=timeout,
        )
        api.status = ApiStatus.TESTED_OK if 200 <= result["status_code"] < 400 else ApiStatus.TESTED_FAILED
        st.session_state.last_response = result
        st.session_state.last_response_api_id = api.id
        st.success(t(lang, "request_sent_ok"))
    except RequestExecutionError as exc:
        api.status = ApiStatus.TESTED_FAILED
        st.error(f"{t(lang, 'request_failed')}: {exc}")



def _collect_session_values(apis: List[ApiRecord], include_sensitive: bool) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for api in apis:
        if api.endpoint.base_url:
            values["base_url"] = api.endpoint.base_url
        if api.body.content_type:
            values["content_type"] = api.body.content_type

        for h in api.headers:
            if not h.name or h.value is None:
                continue
            key = h.variable_name or h.name.lower().replace("-", "_")
            if include_sensitive or not _is_sensitive_param(h):
                values[key] = str(h.value)

        for q in api.query_params:
            if q.name and q.value is not None:
                values[q.variable_name or q.name.lower()] = str(q.value)

        for p in api.path_params:
            if p.name and p.value is not None:
                values[p.name] = str(p.value)
    return values



def _render_request_tab(lang: str, api: ApiRecord) -> None:
    methods = [m.value for m in HttpMethod]
    c1, c2, c3, c4 = st.columns([1, 3.2, 1, 1.3])

    method = c1.selectbox(t(lang, "http_method"), methods, index=methods.index(api.method.value if api.method else "GET"), key=f"method_{api.id}")
    api.method = HttpMethod(method)
    env = c3.selectbox(t(lang, "environment"), ["raw", "qa", "prd"], index=["raw", "qa", "prd"].index(st.session_state.active_env), key=f"env_{api.id}")
    st.session_state.active_env = env
    final_url = c2.text_input(t(lang, "final_url"), value=_compose_url(api, env), key=f"url_{api.id}")
    api.endpoint.raw = final_url
    run_now = c4.button(t(lang, "test_api"), use_container_width=True, type="primary", key=f"send_{api.id}")

    api.name = st.text_input(t(lang, "api_name"), value=api.name, key=f"name_{api.id}")
    api.description = st.text_area(t(lang, "description"), value=api.description, key=f"desc_{api.id}", height=70)

    tab_auth, tab_params, tab_headers, tab_body = st.tabs([t(lang, "auth_tab"), t(lang, "params_tab"), t(lang, "headers_tab"), t(lang, "body_tab")])
    with tab_auth:
        auth_values = [a.value for a in AuthType]
        api.auth.type = AuthType(st.selectbox(t(lang, "auth_type"), auth_values, index=auth_values.index(api.auth.type.value), key=f"auth_{api.id}"))
        api.auth.notes = st.text_input(t(lang, "auth_notes"), value=api.auth.notes, key=f"auth_notes_{api.id}")

        if api.auth.type == AuthType.BASIC:
            default_user, default_pass = _extract_basic_from_headers(api)
            c_user, c_pass = st.columns(2)
            basic_user = c_user.text_input(t(lang, "auth_username"), value=default_user, key=f"basic_user_{api.id}")
            basic_pass = c_pass.text_input(t(lang, "auth_password"), value=default_pass, type="default" if st.session_state.allow_sensitive_extraction else "password", key=f"basic_pass_{api.id}")
            auth_payload = {"username": basic_user, "password": basic_pass}
            

        elif api.auth.type == AuthType.TOKEN:
            default_header, default_prefix, default_token = _extract_token_from_headers(api)
            c_h, c_p, c_t = st.columns([1.3, 1, 2])
            header_name = c_h.text_input(t(lang, "auth_header_name"), value=default_header, key=f"token_header_{api.id}")
            token_prefix = c_p.text_input(t(lang, "auth_token_prefix"), value=default_prefix or "Bearer", key=f"token_prefix_{api.id}")
            token_value = c_t.text_input(t(lang, "auth_token_value"), value=default_token, type="default" if st.session_state.allow_sensitive_extraction else "password", key=f"token_value_{api.id}")
            auth_payload = {"header_name": header_name, "prefix": token_prefix, "token": token_value}
            

        elif api.auth.type == AuthType.UNKNOWN:
            st.warning(t(lang, "auth_unknown_warning"))


    with tab_params:
        st.caption(t(lang, "path_params"))
        api.path_params = _param_editor(api.id, "path_params", api.path_params)
        st.caption(t(lang, "query_params"))
        api.query_params = _param_editor(api.id, "query_params", api.query_params)

    with tab_headers:
        st.caption(t(lang, "headers_source_of_truth"))
        # Read-only mirror table populated from effective request headers.
        pass

    with tab_body:
        api.body.required = st.checkbox(t(lang, "body_required"), value=api.body.required, key=f"body_req_{api.id}")
        ct_options = ["", "application/json", "application/x-www-form-urlencoded", "multipart/form-data", "text/plain"]
        api.body.content_type = st.selectbox(t(lang, "content_type"), ct_options, index=ct_options.index(api.body.content_type or ""), key=f"ct_{api.id}") or None
        body_text = st.text_area(
            t(lang, "request_body"),
            value=json.dumps(api.body.example, indent=2) if isinstance(api.body.example, (dict, list)) else (api.body.example or ""),
            height=190,
            key=f"body_{api.id}",
        )

    if body_text.strip():
        try:
            api.body.example = json.loads(body_text)
        except json.JSONDecodeError:
            api.body.example = body_text
    else:
        api.body.example = None

    effective_headers = _refresh_effective_headers(api)

    with tab_headers:
        mirror_df = _headers_mirror_dataframe(api, effective_headers)
        st.dataframe(mirror_df, use_container_width=True, hide_index=True)

    validate_api(api)

    if run_now:
        _refresh_effective_headers(api)
        _test_api(lang, api, method, final_url, body_text, timeout=30)

    live = st.session_state.last_response if st.session_state.last_response_api_id == api.id else None
    _render_response_block(lang, live)



def _render_response_tab(lang: str, api: ApiRecord) -> None:
    resp = st.session_state.last_response if st.session_state.last_response_api_id == api.id else None
    _render_response_block(lang, resp)



def _render_export_tab(lang: str, apis: List[ApiRecord], selected_api: ApiRecord) -> None:
    sensitive_mode = st.session_state.allow_sensitive_extraction
    st.info(
        f"{t(lang, 'sensitive_export_mode') if sensitive_mode else t(lang, 'safe_export_mode')} — "
        f"{t(lang, 'credentials_included') if sensitive_mode else t(lang, 'credentials_omitted')}"
    )
    if sensitive_mode:
        st.warning(t(lang, "sensitive_export_warning"))

    selected_ids = st.multiselect(t(lang, "select_api_export"), options=[a.id for a in apis], default=[selected_api.id])
    selected_apis = [a for a in st.session_state["apis"] if a.id in selected_ids]

    if not selected_apis:
        st.error(t(lang, "no_api_selected"))
        return

    for export_api in selected_apis:
        _refresh_effective_headers(export_api)
    session_values = _collect_session_values(selected_apis, include_sensitive=sensitive_mode)
    collection_json, environment_json = build_postman_collection_and_env(
        selected_apis,
        include_current_values=True,
        include_sensitive_values=sensitive_mode,
        session_values=session_values,
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("collection.postman_collection.json", collection_json)
        zf.writestr("environment.postman_environment.json", environment_json)
    zip_buffer.seek(0)

    st.download_button(t(lang, "download_collection"), collection_json, "collection.postman_collection.json", "application/json", use_container_width=True)
    st.download_button(t(lang, "download_environment"), environment_json, "environment.postman_environment.json", "application/json", use_container_width=True)
    st.download_button(t(lang, "download_all"), zip_buffer.getvalue(), "postman_export_bundle.zip", "application/zip", use_container_width=True)


apply_custom_theme_css()
_init_state()

left_col, right_col = st.columns([0.32, 0.68], gap="large")

with left_col:
    lang = st.selectbox(t("en", "language"), ["en", "es", "pt"], format_func=lambda c: LANG_NAMES[c])
    st.session_state.lang = lang

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f"### {t(lang, 'title')}")
    st.caption(t(lang, "subtitle"))
    st.caption(t(lang, "powered"))
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-label">{t(lang, "session_panel")}</div>', unsafe_allow_html=True)
    api_key = st.text_input(t(lang, "api_key"), type="password", key="openai_api_key_input")
    st.caption(t(lang, "api_key_note"))
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-label">{t(lang, "doc_input")}</div>', unsafe_allow_html=True)
    st.session_state.allow_sensitive_extraction = st.checkbox(
        t(lang, "extract_sensitive_toggle"),
        value=st.session_state.allow_sensitive_extraction,
        help=t(lang, "extract_sensitive_help"),
    )
    st.caption(t(lang, "sensitive_enabled") if st.session_state.allow_sensitive_extraction else t(lang, "sensitive_disabled"))
    uploaded_files = st.file_uploader(t(lang, "upload_files"), type=SUPPORTED_TYPES, accept_multiple_files=True)
    raw_text = st.text_area(t(lang, "paste_text"), height=120)
    if st.button(t(lang, "analyze"), type="primary", use_container_width=True):
        _run_analysis(lang, api_key, uploaded_files, raw_text, st.session_state.allow_sensitive_extraction)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    apis_state: List[ApiRecord] = st.session_state.get("apis", [])
    st.write(f"{t(lang, 'detected_apis_nav')}: **{len(apis_state)}**")
    if apis_state:
        selected = _get_selected_api(apis_state)
        st.caption(f"{t(lang, 'selected_api')}: {selected.name or selected.id}")
    st.markdown("</div>", unsafe_allow_html=True)

with right_col:
    apis: List[ApiRecord] = st.session_state.get("apis", [])
    if not apis:
        st.info(t(lang, "no_apis_workspace"))
    else:
        selected = _get_selected_api(apis)
        labels = {_api_label(api): api.id for api in apis}
        current_label = next((lbl for lbl, aid in labels.items() if aid == selected.id), list(labels)[0])
        chosen = st.selectbox(t(lang, "api_selector"), list(labels.keys()), index=list(labels).index(current_label))
        st.session_state.selected_api_id = labels[chosen]
        selected = _get_selected_api(apis)
        _refresh_effective_headers(selected)

        method = selected.method.value if selected.method else "-"
        color = METHOD_COLORS.get(method, "#94a3b8")
        st.markdown(
            f'<div class="toolbar"><span class="badge" style="background:{color}22;border-color:{color}88;color:{color};">{method}</span>'
            f'<span class="badge">{selected.auth.type.value}</span><span class="badge">{selected.status.value}</span>'
            f'<div style="margin-top:.35rem;font-size:1rem;font-weight:700;">{selected.name or selected.id}</div></div>',
            unsafe_allow_html=True,
        )

        st.caption(t(lang, "api_summary_label"))
        st.dataframe(_build_summary_df(selected, st.session_state.allow_sensitive_extraction), use_container_width=True, hide_index=True)

        request_tab, response_tab, export_tab = st.tabs([t(lang, "request_tab"), t(lang, "response_tab"), t(lang, "export_tab")])
        with request_tab:
            _render_request_tab(lang, selected)
        with response_tab:
            _render_response_tab(lang, selected)
        with export_tab:
            _render_export_tab(lang, apis, selected)
