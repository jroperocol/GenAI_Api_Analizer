"""Streamlit app for GoAI API Documentation Interpreter."""

from __future__ import annotations

import json
import time
from typing import Dict, List

import pandas as pd
import streamlit as st

from extractor import ExtractionError, extract_apis_with_openai
from file_ingestion import SUPPORTED_TYPES, extract_text_from_uploads
from i18n import t
from models import AnalysisPayload, ApiRecord, ApiStatus, AuthType, HttpMethod, ParamField
from postman_exporter import build_postman_collection_and_env
from security import contains_likely_credentials, mask_sensitive_headers, sanitize_analysis_payload
from test_runner import RequestExecutionError, execute_test_request
from validator import validate_analysis, validate_api

st.set_page_config(page_title="GoAI API Documentation Interpreter", layout="wide")

LANG_NAMES = {"en": "English", "es": "Español", "pt": "Português"}
METHOD_COLORS = {"GET": "#34d399", "POST": "#60a5fa", "PUT": "#f59e0b", "PATCH": "#a78bfa", "DELETE": "#f87171"}


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
        .stTabs [data-baseweb="tab"] { background: rgba(15,23,42,.6); border: 1px solid rgba(100,116,139,.35); border-radius: 10px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_state() -> None:
    defaults = {
        "analysis": AnalysisPayload(),
        "selected_api_id": None,
        "test_result_by_api": {},
        "active_env": "raw",
        "lang": "en",
        "analysis_running": False,
        "analysis_error": "",
        "analysis_error_details": "",
        "allow_sensitive_extraction": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _api_label(api: ApiRecord) -> str:
    method = api.method.value if api.method else "-"
    name = api.name or api.id
    return f"{method} | {name}"


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


def _progress_update(progress_bar, status_box, percent: int, message: str, start: int) -> int:
    for p in range(start, percent + 1):
        progress_bar.progress(p)
        status_box.info(message)
        time.sleep(0.008)
    return percent


def _sanitize_analysis_error(lang: str, error_text: str) -> str:
    lower = error_text.lower()
    if "invalid value" in lower or "input_text" in lower:
        return t(lang, "analysis_failed_payload")
    if "unauthorized" in lower or "api key" in lower:
        return t(lang, "analysis_failed_key")
    return t(lang, "analysis_failed_generic")


def _run_analysis_with_progress(lang: str, api_key: str, uploaded_files, raw_text: str, allow_sensitive: bool) -> None:
    wrap = st.container()
    with wrap:
        st.markdown(f'<div class="goai-card"><div class="section-label">{t(lang, "analysis_progress_title")}</div>', unsafe_allow_html=True)
        progress_bar = st.progress(0)
        status_box = st.empty()
        st.markdown("</div>", unsafe_allow_html=True)

    st.session_state.analysis_running = True
    st.session_state.analysis_error = ""
    st.session_state.analysis_error_details = ""
    p = 0

    try:
        p = _progress_update(progress_bar, status_box, 10, t(lang, "analysis_stage_10"), p)
        if not api_key.strip():
            raise ExtractionError(t(lang, "analyze_missing_key"))
        if not raw_text.strip() and not uploaded_files:
            raise ExtractionError(t(lang, "analyze_missing_input"))

        p = _progress_update(progress_bar, status_box, 20, t(lang, "analysis_stage_20"), p)
        file_text, source_files, ingest_warnings = extract_text_from_uploads(uploaded_files or [])
        p = _progress_update(progress_bar, status_box, 35, t(lang, "analysis_stage_35"), p)

        combined_text = "\n\n".join(part for part in [file_text, raw_text] if part.strip())
        if not combined_text.strip():
            raise ExtractionError(t(lang, "analyze_missing_input"))

        if contains_likely_credentials(combined_text):
            st.warning(t(lang, "sensitive_detected"))
        for warning_text in ingest_warnings:
            st.warning(warning_text)

        p = _progress_update(progress_bar, status_box, 50, t(lang, "analysis_stage_50"), p)
        p = _progress_update(progress_bar, status_box, 65, t(lang, "analysis_stage_65"), p)
        analysis = extract_apis_with_openai(api_key=api_key.strip(), doc_text=combined_text, allow_sensitive=allow_sensitive)

        p = _progress_update(progress_bar, status_box, 85, t(lang, "analysis_stage_85"), p)
        analysis.document_analysis.source_files = source_files
        analysis = sanitize_analysis_payload(analysis, allow_sensitive=allow_sensitive)

        p = _progress_update(progress_bar, status_box, 95, t(lang, "analysis_stage_95"), p)
        validated = validate_analysis(analysis)
        p = _progress_update(progress_bar, status_box, 100, t(lang, "analysis_stage_100"), p)

        st.session_state.analysis = validated
        selected = _get_selected_api(validated.document_analysis.apis)
        st.session_state.selected_api_id = selected.id if selected else None
        st.success(t(lang, "detected_apis").format(count=validated.document_analysis.api_count))
    except Exception as exc:  # noqa: BLE001
        st.session_state.analysis_error = _sanitize_analysis_error(lang, str(exc))
        st.session_state.analysis_error_details = str(exc)
        status_box.error(t(lang, "analysis_stage_failed"))
        st.error(st.session_state.analysis_error)
        with st.expander(t(lang, "show_technical_details")):
            st.code(st.session_state.analysis_error_details)
    finally:
        st.session_state.analysis_running = False


def _send_request_for_api(lang: str, api: ApiRecord, method: str, url: str, headers_json: str, query_json: str, body_input: str, timeout: int) -> None:
    try:
        headers = json.loads(headers_json) if headers_json.strip() else {}
        params = json.loads(query_json) if query_json.strip() else {}
        if not isinstance(headers, dict) or not isinstance(params, dict):
            raise ValueError("Headers and query must be JSON objects.")

        result = execute_test_request(method=method, url=url, headers={str(k): str(v) for k, v in headers.items()}, params={str(k): str(v) for k, v in params.items()}, body=body_input, timeout_seconds=int(timeout))
        st.session_state.test_result_by_api[api.id] = result
        api.status = ApiStatus.TESTED_OK if 200 <= result["status_code"] < 400 else ApiStatus.TESTED_FAILED
        st.success(t(lang, "request_sent_ok"))
    except (json.JSONDecodeError, ValueError, RequestExecutionError) as exc:
        api.status = ApiStatus.TESTED_FAILED
        st.error(f"{t(lang, 'request_failed')}: {exc}")


def _render_request_tab(lang: str, api: ApiRecord) -> None:
    method_values = [m.value for m in HttpMethod]
    auth_values = [a.value for a in AuthType]

    a, b, c, d = st.columns([1, 3, 1, 1.2])
    current_method = api.method.value if api.method else HttpMethod.GET.value
    selected_method = a.selectbox(t(lang, "http_method"), method_values, index=method_values.index(current_method), key=f"method_{api.id}")
    api.method = HttpMethod(selected_method)

    st.session_state.active_env = c.selectbox(t(lang, "environment"), ["raw", "qa", "prd"], index=["raw", "qa", "prd"].index(st.session_state.active_env), key=f"env_{api.id}")
    final_url = b.text_input(t(lang, "final_url"), value=_compose_url(api, st.session_state.active_env), key=f"url_{api.id}")
    api.endpoint.raw = final_url

    test_btn = d.button(t(lang, "test_api"), use_container_width=True, type="primary", key=f"test_api_btn_{api.id}")

    api.name = st.text_input(t(lang, "api_name"), value=api.name, key=f"api_name_{api.id}")
    api.description = st.text_area(t(lang, "description"), value=api.description, height=70, key=f"api_desc_{api.id}")

    auth_tab, params_tab, headers_tab, body_tab = st.tabs([t(lang, "auth_tab"), t(lang, "params_tab"), t(lang, "headers_tab"), t(lang, "body_tab")])

    with auth_tab:
        c1, c2 = st.columns([1, 2])
        api.auth.type = AuthType(c1.selectbox(t(lang, "auth_type"), auth_values, index=auth_values.index(api.auth.type.value), key=f"auth_type_{api.id}"))
        api.auth.notes = c2.text_input(t(lang, "auth_notes"), value=api.auth.notes, key=f"auth_notes_{api.id}")
        if not api.auth.goflow_supported:
            st.warning(t(lang, "auth_unsupported_warning"))

    with params_tab:
        st.caption(t(lang, "path_params"))
        api.path_params = _param_editor(api.id, "path_params", api.path_params)
        st.caption(t(lang, "query_params"))
        api.query_params = _param_editor(api.id, "query_params", api.query_params)

    with headers_tab:
        api.headers = _param_editor(api.id, "headers", api.headers)

    with body_tab:
        api.body.required = st.checkbox(t(lang, "body_required"), value=api.body.required, key=f"body_req_{api.id}")
        api.body.content_type = st.selectbox(t(lang, "content_type"), ["application/json", "application/x-www-form-urlencoded", "text/plain", ""], index=["application/json", "application/x-www-form-urlencoded", "text/plain", ""].index(api.body.content_type or ""), key=f"content_type_{api.id}") or None
        body_text = st.text_area(t(lang, "request_body"), value=json.dumps(api.body.example, indent=2) if isinstance(api.body.example, (dict, list)) else (api.body.example or ""), height=180, key=f"body_editor_{api.id}")
        if body_text.strip():
            try:
                api.body.example = json.loads(body_text)
            except json.JSONDecodeError:
                api.body.example = body_text
        else:
            api.body.example = None

    headers_default = {h.name: h.value or "" for h in api.headers}
    query_default = {q.name: q.value or "" for q in api.query_params}
    headers_json = json.dumps(headers_default, indent=2)
    query_json = json.dumps(query_default, indent=2)
    body_payload = json.dumps(api.body.example, indent=2) if isinstance(api.body.example, (dict, list)) else (api.body.example or "")

    if test_btn:
        _send_request_for_api(lang, api, selected_method, final_url, headers_json, query_json, body_payload, 20)

    validate_api(api)


def _render_response_tab(lang: str, api: ApiRecord) -> None:
    result = st.session_state.test_result_by_api.get(api.id)
    if not result:
        st.info(t(lang, "no_response"))
        return

    body_text = result.get("body", "")
    response_size = result.get("response_size_bytes", len(body_text.encode("utf-8")))
    st.markdown(
        f'<div class="toolbar"><span class="chip">{t(lang,"status_code")}: <b>{result.get("status_code", "-")}</b></span>'
        f'<span class="chip">{t(lang,"response_time")}: <b>{result.get("response_time_ms", "-")} ms</b></span>'
        f'<span class="chip">{t(lang,"response_size")}: <b>{response_size} bytes</b></span></div>',
        unsafe_allow_html=True,
    )
    st.caption(t(lang, "response_headers"))
    st.json(mask_sensitive_headers(result.get("headers", {})))
    st.caption(t(lang, "response_body"))
    st.code(body_text or "", language="json")


def _collect_session_values(apis: List[ApiRecord], include_sensitive: bool) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for api in apis:
        if api.endpoint.base_url:
            values["base_url"] = api.endpoint.base_url
        for h in api.headers:
            if h.value:
                var_name = h.variable_name or h.name.lower().replace("-", "_")
                if include_sensitive or not h.sensitive:
                    values[var_name] = h.value
    return values


def _render_export_tab(lang: str, apis: List[ApiRecord], selected_api: ApiRecord) -> None:
    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    sensitive_mode = st.session_state.allow_sensitive_extraction
    mode_label = t(lang, "sensitive_export_mode") if sensitive_mode else t(lang, "safe_export_mode")
    mode_desc = t(lang, "credentials_included") if sensitive_mode else t(lang, "credentials_omitted")
    st.info(f"{mode_label} — {mode_desc}")

    include_values = st.checkbox(t(lang, "include_current_values"), value=True)
    selected_ids = st.multiselect(t(lang, "select_api_export"), options=[a.id for a in apis], default=[selected_api.id])

    if sensitive_mode:
        st.warning(t(lang, "sensitive_export_warning"))

    if st.button(t(lang, "prepare_export"), type="primary"):
        selected_apis = [a for a in apis if a.id in selected_ids]
        if not selected_apis:
            st.error(t(lang, "no_api_selected"))
        else:
            session_values = _collect_session_values(selected_apis, include_sensitive=sensitive_mode)
            collection_json, environment_json = build_postman_collection_and_env(
                selected_apis,
                include_current_values=include_values,
                include_sensitive_values=sensitive_mode,
                session_values=session_values,
            )
            st.download_button(t(lang, "download_collection"), data=collection_json, file_name="collection.postman_collection.json", mime="application/json", use_container_width=True)
            st.download_button(t(lang, "download_environment"), data=environment_json, file_name="environment.postman_environment.json", mime="application/json", use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)


apply_custom_theme_css()
_init_state()
analysis: AnalysisPayload = st.session_state.analysis
apis: List[ApiRecord] = analysis.document_analysis.apis

left_col, right_col = st.columns([0.33, 0.67], gap="large")

with left_col:
    lang = st.selectbox(t("en", "language"), ["en", "es", "pt"], format_func=lambda code: LANG_NAMES[code])
    st.session_state.lang = lang

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f"### {t(lang, 'title')}")
    st.caption(t(lang, "subtitle"))
    st.caption(t(lang, "powered"))
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-label">{t(lang, "session_panel")}</div>', unsafe_allow_html=True)
    api_key = st.text_input(t(lang, "api_key"), type="password", key="openai_api_key_input")
    st.caption(t(lang, "api_key_note"))
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-label">{t(lang, "doc_input")}</div>', unsafe_allow_html=True)
    st.session_state.allow_sensitive_extraction = st.checkbox(t(lang, "extract_sensitive_toggle"), value=st.session_state.allow_sensitive_extraction, help=t(lang, "extract_sensitive_help"))
    st.caption(t(lang, "sensitive_enabled") if st.session_state.allow_sensitive_extraction else t(lang, "sensitive_disabled"))
    uploaded_files = st.file_uploader(t(lang, "upload_files"), type=SUPPORTED_TYPES, accept_multiple_files=True)
    raw_text = st.text_area(t(lang, "paste_text"), height=120)
    if st.button(t(lang, "analyze"), type="primary", use_container_width=True):
        _run_analysis_with_progress(lang, api_key, uploaded_files, raw_text, st.session_state.allow_sensitive_extraction)
        apis = st.session_state.analysis.document_analysis.apis
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f"{t(lang,'detected_apis_nav')}: **{len(apis)}**")
    if apis:
        selected = _get_selected_api(apis)
        st.caption(f"{t(lang, 'selected_api')}: {selected.name or selected.id}")
    st.markdown('</div>', unsafe_allow_html=True)

with right_col:
    if not apis:
        st.markdown('<div class="goai-card">', unsafe_allow_html=True)
        st.info(t(lang, "no_apis_workspace"))
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        selected_api = _get_selected_api(apis)
        options = { _api_label(api): api.id for api in apis }
        selected_label = next((label for label, aid in options.items() if aid == selected_api.id), list(options.keys())[0])
        chosen_label = st.selectbox(t(lang, "api_selector"), options=list(options.keys()), index=list(options.keys()).index(selected_label))
        st.session_state.selected_api_id = options[chosen_label]
        selected_api = _get_selected_api(apis)

        method = selected_api.method.value if selected_api.method else "-"
        color = METHOD_COLORS.get(method, "#94a3b8")
        st.markdown(
            f'<div class="toolbar"><span class="badge" style="background:{color}22;border-color:{color}88;color:{color};">{method}</span>'
            f'<span class="badge">{selected_api.auth.type.value}</span><span class="badge">{selected_api.status.value}</span>'
            f'<div style="margin-top:.35rem;font-size:1rem;font-weight:700;">{selected_api.name or selected_api.id}</div></div>',
            unsafe_allow_html=True,
        )

        request_tab, response_tab, export_tab = st.tabs([t(lang, "request_tab"), t(lang, "response_tab"), t(lang, "export_tab")])
        with request_tab:
            _render_request_tab(lang, selected_api)
        with response_tab:
            _render_response_tab(lang, selected_api)
        with export_tab:
            _render_export_tab(lang, apis, selected_api)
