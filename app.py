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
from security import contains_likely_credentials, mask_sensitive_headers
from test_runner import RequestExecutionError, execute_test_request
from validator import validate_analysis, validate_api

st.set_page_config(page_title="GoAI API Documentation Interpreter", layout="wide")

LANG_NAMES = {"en": "English", "es": "Español", "pt": "Português"}
METHOD_COLORS = {
    "GET": "#34d399",
    "POST": "#60a5fa",
    "PUT": "#f59e0b",
    "PATCH": "#a78bfa",
    "DELETE": "#f87171",
}


def apply_custom_theme_css() -> None:
    """Inject custom dark theme styles."""
    st.markdown(
        """
        <style>
        .stApp {
            background: radial-gradient(circle at 5% 10%, #1c2442 0%, #0b1022 35%, #070b18 100%);
            color: #e2e8f0;
        }
        .main .block-container {padding-top: 1rem; max-width: 1500px;}
        [data-testid="stVerticalBlock"] > [style*="flex-direction: column;"] > [data-testid="stVerticalBlock"] { gap: .6rem; }
        .goai-card {
            background: rgba(15, 23, 42, 0.68);
            border: 1px solid rgba(148, 163, 184, 0.22);
            border-radius: 16px;
            padding: 0.8rem 1rem;
            box-shadow: 0 8px 30px rgba(15, 23, 42, 0.30);
            backdrop-filter: blur(8px);
            margin-bottom: .6rem;
        }
        .goai-title {font-size: 1.1rem; font-weight: 700; color: #f8fafc;}
        .goai-subtitle {font-size: .82rem; color: #94a3b8;}
        .goai-caption {font-size: .74rem; color: #60a5fa; margin-top: .2rem;}
        .section-label {font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: #93c5fd; font-weight: 700; margin-bottom: .4rem;}
        .badge {
            display: inline-block; border-radius: 999px; padding: .15rem .5rem;
            font-size: .68rem; font-weight: 700; margin-right: .3rem;
            border: 1px solid rgba(148,163,184,.35);
        }
        .api-item {
            background: rgba(15,23,42,.78); border: 1px solid rgba(100,116,139,.35); border-radius: 12px;
            padding: .5rem .65rem; margin-bottom: .45rem;
        }
        .api-item.active {border-color: #60a5fa; box-shadow: 0 0 0 1px rgba(96,165,250,.45) inset;}
        .toolbar {
            background: rgba(15,23,42,.72); border: 1px solid rgba(148,163,184,.2);
            border-radius: 14px; padding: .65rem .8rem; margin-bottom: .6rem;
        }
        .chip {
            display:inline-block; padding:.2rem .55rem; border-radius:999px; font-size:.7rem;
            border:1px solid rgba(148,163,184,.4); margin-right:.35rem; margin-bottom:.35rem;
        }
        .stTabs [data-baseweb="tab-list"] {gap: .25rem;}
        .stTabs [data-baseweb="tab"] {
            background: rgba(15,23,42,.6); border: 1px solid rgba(100,116,139,.35); border-radius: 10px;
            padding: .35rem .8rem; height: 2.1rem;
        }
        .stTabs [aria-selected="true"] {border-color:#60a5fa !important;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_state() -> None:
    if "analysis" not in st.session_state:
        st.session_state.analysis = AnalysisPayload()
    if "selected_api_id" not in st.session_state:
        st.session_state.selected_api_id = None
    if "test_result_by_api" not in st.session_state:
        st.session_state.test_result_by_api = {}
    if "active_env" not in st.session_state:
        st.session_state.active_env = "raw"
    if "analysis_running" not in st.session_state:
        st.session_state.analysis_running = False
    if "analysis_error" not in st.session_state:
        st.session_state.analysis_error = ""
    if "analysis_error_details" not in st.session_state:
        st.session_state.analysis_error_details = ""


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


def _api_rows(apis: List[ApiRecord], selected_id: str | None) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for api in apis:
        rows.append(
            {
                "Select": api.id == selected_id,
                "API Name": api.name or api.id,
                "Method": api.method.value if api.method else "",
                "Auth": api.auth.type.value,
                "Status": api.status.value,
                "Errors": len(api.errors),
                "Warnings": len(api.warnings),
                "id": api.id,
            }
        )
    return pd.DataFrame(rows)


def _parse_json_dict(raw: str, fallback: Dict[str, str]) -> Dict[str, str]:
    if not raw.strip():
        return fallback
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("JSON object expected.")
    return {str(k): "" if v is None else str(v) for k, v in parsed.items()}


def _param_editor(api_id: str, key: str, data: List[ParamField]) -> List[ParamField]:
    frame = pd.DataFrame([item.model_dump() for item in data]) if data else pd.DataFrame(columns=["name", "value", "required", "sensitive", "variable_name", "description"])
    updated = st.data_editor(frame, num_rows="dynamic", use_container_width=True, key=f"{api_id}_{key}")
    cleaned: List[ParamField] = []
    for row in updated.to_dict(orient="records"):
        if row.get("name"):
            cleaned.append(ParamField(**row))
    return cleaned


def _render_branding(lang: str) -> None:
    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="goai-title">◉ {t(lang, "title")}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="goai-subtitle">{t(lang, "subtitle")}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="goai-caption">{t(lang, "powered")}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def _status_badge(status: str) -> str:
    colors = {
        "ready": "#22c55e",
        "needs_review": "#f59e0b",
        "blocked": "#ef4444",
        "unsupported": "#ef4444",
        "not_tested": "#64748b",
        "tested_ok": "#22c55e",
        "tested_failed": "#ef4444",
    }
    color = colors.get(status, "#64748b")
    return f'<span class="badge" style="background:{color}22;border-color:{color}66;color:{color};">{status}</span>'


def _render_api_selector(lang: str, apis: List[ApiRecord]) -> None:
    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-label">{t(lang, "detected_apis_nav")}</div>', unsafe_allow_html=True)
    if not apis:
        st.caption(t(lang, "no_apis"))
        st.markdown('</div>', unsafe_allow_html=True)
        return

    for api in apis:
        method = api.method.value if api.method else "-"
        method_color = METHOD_COLORS.get(method, "#94a3b8")
        is_active = api.id == st.session_state.selected_api_id
        css_class = "api-item active" if is_active else "api-item"

        st.markdown(
            (
                f'<div class="{css_class}">'
                f'<span class="badge" style="background:{method_color}22;border-color:{method_color}88;color:{method_color};">{method}</span>'
                f'<span class="badge">{api.auth.type.value}</span>'
                f"{_status_badge(api.status.value)}"
                f'<div style="margin-top:.35rem;font-size:.86rem;font-weight:600;">{api.name or api.id}</div>'
                f'<div style="font-size:.72rem;color:#94a3b8;">{api.endpoint.path or api.endpoint.raw or "-"}</div>'
                '</div>'
            ),
            unsafe_allow_html=True,
        )
        if st.button(t(lang, "select_api"), key=f"select_{api.id}", use_container_width=True):
            st.session_state.selected_api_id = api.id
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


def _progress_update(progress_bar, status_box, percent: int, message: str, smooth_from: int | None = None) -> None:
    start = smooth_from if smooth_from is not None else percent
    step = 1 if percent >= start else -1
    for p in range(start, percent + step, step):
        progress_bar.progress(p)
        status_box.info(message)
        time.sleep(0.01)


def _sanitize_analysis_error(error_text: str) -> str:
    lower = error_text.lower()
    if "invalid value" in lower or "input_text" in lower:
        return t(st.session_state.get("lang", "en"), "analysis_failed_payload")
    if "api key" in lower or "unauthorized" in lower:
        return t(st.session_state.get("lang", "en"), "analysis_failed_key")
    return t(st.session_state.get("lang", "en"), "analysis_failed_generic")


def _run_analysis_with_progress(lang: str, api_key: str, uploaded_files, raw_text: str) -> None:
    progress_wrap = st.container()
    with progress_wrap:
        st.markdown('<div class="goai-card">', unsafe_allow_html=True)
        st.markdown(f'<div class="section-label">{t(lang, "analysis_progress_title")}</div>', unsafe_allow_html=True)
        progress_bar = st.progress(0)
        status_box = st.empty()
        st.markdown('</div>', unsafe_allow_html=True)

    st.session_state.analysis_running = True
    st.session_state.analysis_error = ""
    st.session_state.analysis_error_details = ""
    current_progress = 0

    try:
        _progress_update(progress_bar, status_box, 0, t(lang, "analysis_stage_0"), current_progress)
        current_progress = 0
        _progress_update(progress_bar, status_box, 10, t(lang, "analysis_stage_10"), current_progress)
        current_progress = 10

        if not api_key.strip():
            raise ExtractionError(t(lang, "analyze_missing_key"))
        if not raw_text.strip() and not uploaded_files:
            raise ExtractionError(t(lang, "analyze_missing_input"))

        _progress_update(progress_bar, status_box, 20, t(lang, "analysis_stage_20"), current_progress)
        current_progress = 20
        file_text, source_files, ingest_warnings = extract_text_from_uploads(uploaded_files or [])

        _progress_update(progress_bar, status_box, 35, t(lang, "analysis_stage_35"), current_progress)
        current_progress = 35
        combined_text = "\n\n".join(part for part in [file_text, raw_text] if part.strip())

        if not combined_text.strip():
            raise ExtractionError(t(lang, "analyze_missing_input"))

        if contains_likely_credentials(combined_text):
            st.warning(t(lang, "sensitive_detected"))

        for warning_text in ingest_warnings:
            st.warning(warning_text)

        _progress_update(progress_bar, status_box, 50, t(lang, "analysis_stage_50"), current_progress)
        current_progress = 50
        _progress_update(progress_bar, status_box, 65, t(lang, "analysis_stage_65"), current_progress)
        current_progress = 65
        analysis = extract_apis_with_openai(api_key=api_key.strip(), doc_text=combined_text)

        _progress_update(progress_bar, status_box, 85, t(lang, "analysis_stage_85"), current_progress)
        current_progress = 85
        analysis.document_analysis.source_files = source_files

        _progress_update(progress_bar, status_box, 95, t(lang, "analysis_stage_95"), current_progress)
        current_progress = 95
        validated = validate_analysis(analysis)

        _progress_update(progress_bar, status_box, 100, t(lang, "analysis_stage_100"), current_progress)
        st.session_state.analysis = validated
        first_api = next(iter(validated.document_analysis.apis), None)
        st.session_state.selected_api_id = first_api.id if first_api else None
        st.success(t(lang, "detected_apis").format(count=validated.document_analysis.api_count))
    except Exception as exc:  # noqa: BLE001
        st.session_state.analysis_error = _sanitize_analysis_error(str(exc))
        st.session_state.analysis_error_details = str(exc)
        progress_bar.progress(current_progress)
        status_box.error(t(lang, "analysis_stage_failed"))
        st.error(st.session_state.analysis_error)
        with st.expander(t(lang, "show_technical_details")):
            st.code(st.session_state.analysis_error_details)
    finally:
        st.session_state.analysis_running = False


def _render_request_tab(lang: str, selected_api: ApiRecord) -> None:
    method_values = [method.value for method in HttpMethod]
    auth_values = [auth.value for auth in AuthType]

    tool_a, tool_b, tool_c = st.columns([1, 3, 1.3])
    current_method = selected_api.method.value if selected_api.method else HttpMethod.GET.value
    selected_api.method = HttpMethod(tool_a.selectbox(t(lang, "http_method"), method_values, index=method_values.index(current_method)))

    env_choice = tool_c.selectbox(t(lang, "environment"), options=["raw", "qa", "prd"], index=["raw", "qa", "prd"].index(st.session_state.active_env))
    st.session_state.active_env = env_choice
    endpoint_url = tool_b.text_input(t(lang, "final_url"), value=_compose_url(selected_api, env_choice))

    selected_api.endpoint.raw = endpoint_url

    selected_api.name = st.text_input(t(lang, "api_name"), value=selected_api.name)
    selected_api.description = st.text_area(t(lang, "description"), value=selected_api.description, height=70)

    top_tabs = st.tabs([t(lang, "auth_tab"), t(lang, "params_tab"), t(lang, "headers_tab"), t(lang, "body_tab")])

    with top_tabs[0]:
        col1, col2 = st.columns([1.1, 2])
        selected_api.auth.type = AuthType(col1.selectbox(t(lang, "auth_type"), options=auth_values, index=auth_values.index(selected_api.auth.type.value)))
        selected_api.auth.notes = col2.text_input(t(lang, "auth_notes"), value=selected_api.auth.notes)

        if selected_api.auth.type == AuthType.BASIC:
            basic_user = st.text_input("username", value="")
            basic_pass = st.text_input("password", value="", type="password")
            if basic_user:
                selected_api.headers = [h for h in selected_api.headers if h.name.lower() not in {"username", "password"}] + [
                    ParamField(name="username", value=basic_user, sensitive=False, required=True, variable_name="username"),
                    ParamField(name="password", value=basic_pass, sensitive=True, required=True, variable_name="password"),
                ]
        elif selected_api.auth.type == AuthType.TOKEN:
            token_header = st.text_input(t(lang, "token_header"), value="Authorization")
            token_value = st.text_input(t(lang, "token_value"), value="", type="password")
            if token_value:
                selected_api.headers = [h for h in selected_api.headers if h.name.lower() != token_header.lower()] + [
                    ParamField(name=token_header, value=token_value, sensitive=True, required=True, variable_name="token")
                ]

        if not selected_api.auth.goflow_supported:
            st.warning(t(lang, "auth_unsupported_warning"))

    with top_tabs[1]:
        st.caption(t(lang, "path_params"))
        selected_api.path_params = _param_editor(selected_api.id, "path_params", selected_api.path_params)
        st.caption(t(lang, "query_params"))
        selected_api.query_params = _param_editor(selected_api.id, "query_params", selected_api.query_params)

    with top_tabs[2]:
        selected_api.headers = _param_editor(selected_api.id, "headers", selected_api.headers)

    with top_tabs[3]:
        selected_api.body.required = st.checkbox(t(lang, "body_required"), value=selected_api.body.required)
        selected_api.body.content_type = st.selectbox(
            t(lang, "content_type"),
            options=["application/json", "application/x-www-form-urlencoded", "text/plain", ""],
            index=["application/json", "application/x-www-form-urlencoded", "text/plain", ""].index(selected_api.body.content_type or ""),
        ) or None

        if selected_api.body.content_type == "application/x-www-form-urlencoded":
            form_rows = pd.DataFrame(selected_api.body.example if isinstance(selected_api.body.example, list) else [], columns=["key", "value"])
            form_updated = st.data_editor(form_rows, num_rows="dynamic", use_container_width=True, key=f"{selected_api.id}_form_body")
            selected_api.body.example = form_updated.to_dict(orient="records")
        else:
            body_text = st.text_area(
                t(lang, "request_body"),
                value=json.dumps(selected_api.body.example, indent=2) if isinstance(selected_api.body.example, (dict, list)) else (selected_api.body.example or ""),
                height=210,
            )
            if body_text.strip():
                try:
                    selected_api.body.example = json.loads(body_text)
                except json.JSONDecodeError:
                    selected_api.body.example = body_text
            else:
                selected_api.body.example = None

    validate_api(selected_api)


def _render_response_tab(lang: str, selected_api: ApiRecord) -> None:
    method_options = [method.value for method in HttpMethod]
    default_method = selected_api.method.value if selected_api.method else HttpMethod.GET.value
    test_method = st.selectbox(t(lang, "http_method"), options=method_options, index=method_options.index(default_method), key=f"test_method_{selected_api.id}")

    final_url = st.text_input(t(lang, "final_url"), value=_compose_url(selected_api, st.session_state.active_env), key=f"test_url_{selected_api.id}")

    headers_default = {header.name: header.value or "" for header in selected_api.headers}
    query_default = {param.name: param.value or "" for param in selected_api.query_params}

    col1, col2 = st.columns(2)
    headers_json = col1.text_area(t(lang, "headers_json"), value=json.dumps(headers_default, indent=2), height=140, key=f"headers_{selected_api.id}")
    query_json = col2.text_area(t(lang, "query_json"), value=json.dumps(query_default, indent=2), height=140, key=f"query_{selected_api.id}")
    body_input = st.text_area(t(lang, "request_body"), value=json.dumps(selected_api.body.example, indent=2) if isinstance(selected_api.body.example, (dict, list)) else (selected_api.body.example or ""), height=160, key=f"body_{selected_api.id}")
    timeout = st.number_input(t(lang, "timeout"), min_value=1, max_value=120, value=20, key=f"timeout_{selected_api.id}")

    btn_col1, btn_col2 = st.columns([1, 1])
    if btn_col1.button(t(lang, "run_test"), use_container_width=True, type="primary", key=f"run_{selected_api.id}"):
        try:
            headers = _parse_json_dict(headers_json, headers_default)
            params = _parse_json_dict(query_json, query_default)
            result = execute_test_request(
                method=test_method,
                url=final_url,
                headers=headers,
                params=params,
                body=body_input,
                timeout_seconds=int(timeout),
            )
            st.session_state.test_result_by_api[selected_api.id] = result
            selected_api.status = ApiStatus.TESTED_OK if 200 <= result["status_code"] < 400 else ApiStatus.TESTED_FAILED
        except (json.JSONDecodeError, ValueError, RequestExecutionError) as exc:
            selected_api.status = ApiStatus.TESTED_FAILED
            st.error(f"{t(lang, 'request_failed')}: {exc}")

    if btn_col2.button(t(lang, "clear_response"), use_container_width=True, key=f"clear_{selected_api.id}"):
        st.session_state.test_result_by_api.pop(selected_api.id, None)

    result = st.session_state.test_result_by_api.get(selected_api.id)
    if not result:
        st.info(t(lang, "no_response"))
        return

    body_text = result.get("body", "")
    response_size = result.get("response_size_bytes", len(body_text.encode("utf-8")))
    status = result.get("status_code", "-")
    time_ms = result.get("response_time_ms", "-")

    st.markdown(
        (
            f'<div class="toolbar">'
            f'<span class="chip">{t(lang, "status_code")}: <b>{status}</b></span>'
            f'<span class="chip">{t(lang, "response_time")}: <b>{time_ms} ms</b></span>'
            f'<span class="chip">{t(lang, "response_size")}: <b>{response_size} bytes</b></span>'
            f'</div>'
        ),
        unsafe_allow_html=True,
    )

    st.caption(t(lang, "response_headers"))
    st.json(mask_sensitive_headers(result.get("headers", {})))
    st.caption(t(lang, "response_body"))
    st.code(body_text or "", language="json")


def _render_export_tab(lang: str, apis: List[ApiRecord], selected_api: ApiRecord) -> None:
    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.caption(t(lang, "safe_export_note"))
    include_values = st.checkbox(t(lang, "include_session_values"), value=False)

    default_selection = [selected_api.id] if selected_api else [api.id for api in apis]
    selected_ids = st.multiselect(t(lang, "select_api_export"), options=[api.id for api in apis], default=default_selection)

    summary = _api_rows([api for api in apis if api.id in selected_ids], st.session_state.selected_api_id)
    if not summary.empty:
        st.dataframe(summary.drop(columns=["Select", "id"]), use_container_width=True, height=180)

    if st.button(t(lang, "prepare_export"), type="primary"):
        selected_apis = [api for api in apis if api.id in selected_ids]
        if not selected_apis:
            st.error(t(lang, "no_api_selected"))
        else:
            session_values: Dict[str, str] = {}
            for api in selected_apis:
                if api.endpoint.base_url:
                    session_values["base_url"] = api.endpoint.base_url
                for header in api.headers:
                    if header.variable_name and header.value:
                        session_values[header.variable_name] = header.value

            collection_json, environment_json = build_postman_collection_and_env(
                selected_apis,
                include_current_values=include_values,
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
    lang = st.selectbox(t("en", "language"), options=["en", "es", "pt"], format_func=lambda value: LANG_NAMES[value])
    st.session_state.lang = lang
    _render_branding(lang)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-label">{t(lang, "session_panel")}</div>', unsafe_allow_html=True)
    api_key = st.text_input(t(lang, "api_key"), type="password", key="openai_api_key_input")
    st.caption(t(lang, "api_key_note"))
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="goai-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="section-label">{t(lang, "doc_input")}</div>', unsafe_allow_html=True)
    uploaded_files = st.file_uploader(t(lang, "upload_files"), type=SUPPORTED_TYPES, accept_multiple_files=True)
    raw_text = st.text_area(t(lang, "paste_text"), height=120)
    if st.button(t(lang, "analyze"), type="primary", use_container_width=True):
        _run_analysis_with_progress(lang, api_key, uploaded_files, raw_text)
        analysis = st.session_state.analysis
        apis = analysis.document_analysis.apis
    st.markdown('</div>', unsafe_allow_html=True)

    _render_api_selector(lang, apis)

with right_col:
    if not apis:
        st.markdown('<div class="goai-card">', unsafe_allow_html=True)
        st.info(t(lang, "no_apis_workspace"))
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        selected_api = next((item for item in apis if item.id == st.session_state.selected_api_id), apis[0])
        st.session_state.selected_api_id = selected_api.id

        st.markdown('<div class="toolbar">', unsafe_allow_html=True)
        method = selected_api.method.value if selected_api.method else "-"
        method_color = METHOD_COLORS.get(method, "#94a3b8")
        st.markdown(
            (
                f'<span class="badge" style="background:{method_color}22;border-color:{method_color}88;color:{method_color};">{method}</span>'
                f'<span class="badge">{selected_api.auth.type.value}</span>'
                f"{_status_badge(selected_api.status.value)}"
                f'<div style="margin-top:.35rem;font-size:1rem;font-weight:700;">{selected_api.name or selected_api.id}</div>'
                f'<div style="font-size:.78rem;color:#94a3b8;">{selected_api.description or (selected_api.endpoint.path or selected_api.endpoint.raw or "")}</div>'
            ),
            unsafe_allow_html=True,
        )
        st.markdown('</div>', unsafe_allow_html=True)

        request_tab, response_tab, export_tab = st.tabs([t(lang, "request_tab"), t(lang, "response_tab"), t(lang, "export_tab")])
        with request_tab:
            _render_request_tab(lang, selected_api)
        with response_tab:
            _render_response_tab(lang, selected_api)
        with export_tab:
            _render_export_tab(lang, apis, selected_api)
