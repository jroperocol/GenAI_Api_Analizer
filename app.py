"""Streamlit app for GoAI API Documentation Interpreter."""

from __future__ import annotations

import json
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
from validator import validate_analysis

st.set_page_config(page_title="GoAI API Documentation Interpreter", layout="wide")

LANG_NAMES = {"en": "English", "es": "Español", "pt": "Português"}


def _init_state() -> None:
    if "analysis" not in st.session_state:
        st.session_state.analysis = AnalysisPayload()
    if "selected_api_id" not in st.session_state:
        st.session_state.selected_api_id = None
    if "test_result_by_api" not in st.session_state:
        st.session_state.test_result_by_api = {}



def _compose_url(api: ApiRecord) -> str:
    if api.endpoint.raw:
        return api.endpoint.raw
    if api.endpoint.base_url and api.endpoint.path:
        return f"{api.endpoint.base_url.rstrip('/')}/{api.endpoint.path.lstrip('/')}"
    return api.endpoint.base_url or api.endpoint.path or ""



def _param_table_editor(api_id: str, label: str, items: List[ParamField]) -> List[ParamField]:
    frame = (
        pd.DataFrame([item.model_dump() for item in items])
        if items
        else pd.DataFrame(columns=["name", "value", "required", "sensitive", "variable_name", "description"])
    )
    updated = st.data_editor(frame, num_rows="dynamic", use_container_width=True, key=f"{api_id}_{label}")
    cleaned: List[ParamField] = []
    for row in updated.to_dict(orient="records"):
        if row.get("name"):
            cleaned.append(ParamField(**row))
    return cleaned



def _api_rows(apis: List[ApiRecord], selected_id: str | None) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for api in apis:
        rows.append(
            {
                "Select": api.id == selected_id,
                "API Name": api.name,
                "Method": api.method.value if api.method else "",
                "Endpoint summary": api.endpoint.raw or api.endpoint.path,
                "Auth type": api.auth.type.value,
                "GoFlow compatible": api.auth.goflow_supported,
                "Status": api.status.value,
                "Errors count": len(api.errors),
                "Warnings count": len(api.warnings),
                "id": api.id,
            }
        )
    return pd.DataFrame(rows)


_init_state()

lang = st.selectbox(t("en", "language"), options=["en", "es", "pt"], format_func=lambda value: LANG_NAMES[value])
st.title(t(lang, "title"))
st.caption(t(lang, "subtitle"))
st.caption(t(lang, "powered"))

with st.container(border=True):
    st.subheader(t(lang, "session_panel"))
    api_key = st.text_input(t(lang, "api_key"), type="password", key="openai_api_key_input")
    st.info(t(lang, "api_key_note"))

with st.container(border=True):
    st.subheader(t(lang, "doc_input"))
    uploaded_files = st.file_uploader(t(lang, "upload_files"), type=SUPPORTED_TYPES, accept_multiple_files=True)
    raw_text = st.text_area(t(lang, "paste_text"), height=160)

    if st.button(t(lang, "analyze"), type="primary"):
        file_text, source_files, ingest_warnings = extract_text_from_uploads(uploaded_files or [])
        combined_text = "\n\n".join(part for part in [file_text, raw_text] if part.strip())

        if not combined_text.strip():
            st.error(t(lang, "analyze_missing_input"))
        elif not api_key.strip():
            st.error(t(lang, "analyze_missing_key"))
        else:
            if contains_likely_credentials(combined_text):
                st.warning(t(lang, "sensitive_detected"))
            for warning_text in ingest_warnings:
                st.warning(warning_text)
            try:
                analysis = extract_apis_with_openai(api_key=api_key.strip(), doc_text=combined_text)
                analysis.document_analysis.source_files = source_files
                st.session_state.analysis = validate_analysis(analysis)
                first_api = next(iter(st.session_state.analysis.document_analysis.apis), None)
                st.session_state.selected_api_id = first_api.id if first_api else None
                st.success(t(lang, "detected_apis").format(count=st.session_state.analysis.document_analysis.api_count))
            except ExtractionError as exc:
                st.error(f"{t(lang, 'analysis_failed')}: {exc}")

analysis: AnalysisPayload = st.session_state.analysis
apis: List[ApiRecord] = analysis.document_analysis.apis

with st.container(border=True):
    st.subheader(t(lang, "analysis_results"))
    if not apis:
        st.write(t(lang, "no_apis"))
    else:
        df = _api_rows(apis, st.session_state.selected_api_id)
        edited = st.data_editor(
            df,
            hide_index=True,
            column_config={"Select": st.column_config.CheckboxColumn(required=True)},
            disabled=[col for col in df.columns if col != "Select"],
            key="analysis_table",
        )
        selected_rows = edited[edited["Select"]]
        if not selected_rows.empty:
            st.session_state.selected_api_id = selected_rows.iloc[0]["id"]

selected_api = next((item for item in apis if item.id == st.session_state.selected_api_id), None)
if selected_api is not None:
    with st.container(border=True):
        st.subheader(t(lang, "api_detail"))
        selected_api.name = st.text_input("name", value=selected_api.name)
        selected_api.description = st.text_area("description", value=selected_api.description, height=80)

        col_a, col_b, col_c = st.columns(3)
        selected_api.endpoint.base_url = col_a.text_input("base_url", value=selected_api.endpoint.base_url)
        selected_api.endpoint.path = col_b.text_input("path", value=selected_api.endpoint.path)
        selected_api.endpoint.raw = col_c.text_input("endpoint raw", value=selected_api.endpoint.raw)

        col_d, col_e = st.columns(2)
        selected_api.endpoint.qa = col_d.text_input("qa url", value=selected_api.endpoint.qa or "") or None
        selected_api.endpoint.prd = col_e.text_input("prd url", value=selected_api.endpoint.prd or "") or None

        method_values = [method.value for method in HttpMethod]
        auth_values = [auth.value for auth in AuthType]
        current_method = selected_api.method.value if selected_api.method else HttpMethod.GET.value
        col_f, col_g = st.columns(2)
        selected_api.method = HttpMethod(
            col_f.selectbox("method", options=method_values, index=method_values.index(current_method))
        )
        selected_api.auth.type = AuthType(
            col_g.selectbox("auth type", options=auth_values, index=auth_values.index(selected_api.auth.type.value))
        )
        selected_api.auth.notes = st.text_area("auth notes", value=selected_api.auth.notes, height=80)

        selected_api.headers = _param_table_editor(selected_api.id, "headers", selected_api.headers)
        selected_api.path_params = _param_table_editor(selected_api.id, "path_params", selected_api.path_params)
        selected_api.query_params = _param_table_editor(selected_api.id, "query_params", selected_api.query_params)

        selected_api.body.content_type = st.text_input("body content type", value=selected_api.body.content_type or "") or None
        body_default = (
            json.dumps(selected_api.body.example, indent=2)
            if isinstance(selected_api.body.example, (dict, list))
            else (selected_api.body.example or "")
        )
        body_example_text = st.text_area("body example / body editor", value=body_default, height=120)
        selected_api.body.example = body_example_text or None

        st.text_area("missing_fields", value="\n".join(selected_api.missing_fields), height=80, disabled=True)
        st.text_area(
            "errors",
            value="\n".join(f"{err.code}: {err.message}" for err in selected_api.errors),
            height=80,
            disabled=True,
        )
        st.text_area(
            "warnings",
            value="\n".join(f"{warn.code}: {warn.message}" for warn in selected_api.warnings),
            height=80,
            disabled=True,
        )
        st.text_area(
            "source evidence excerpts",
            value="\n\n".join(f"{src.file_name}: {src.excerpt}" for src in selected_api.source_evidence),
            height=120,
            disabled=True,
        )

    with st.container(border=True):
        st.subheader(t(lang, "test_runner"))
        method_options = [method.value for method in HttpMethod]
        selected_method = st.selectbox(t(lang, "http_method"), options=method_options, index=method_options.index(selected_api.method.value))
        final_url = st.text_input(t(lang, "final_url"), value=_compose_url(selected_api))

        headers_default = {header.name: header.value or "" for header in selected_api.headers}
        query_default = {param.name: param.value or "" for param in selected_api.query_params}

        headers_json = st.text_area(t(lang, "headers_json"), value=json.dumps(headers_default, indent=2), height=100)
        query_json = st.text_area(t(lang, "query_json"), value=json.dumps(query_default, indent=2), height=100)
        body_input = st.text_area(t(lang, "request_body"), value=selected_api.body.example or "", height=120)
        timeout = st.number_input(t(lang, "timeout"), min_value=1, max_value=120, value=20)

        if st.button(t(lang, "run_test")):
            try:
                headers = json.loads(headers_json) if headers_json.strip() else {}
                params = json.loads(query_json) if query_json.strip() else {}
                result = execute_test_request(
                    method=selected_method,
                    url=final_url,
                    headers=headers,
                    params=params,
                    body=body_input,
                    timeout_seconds=int(timeout),
                )
                st.session_state.test_result_by_api[selected_api.id] = result
                selected_api.status = ApiStatus.TESTED_OK if 200 <= result["status_code"] < 400 else ApiStatus.TESTED_FAILED
            except (json.JSONDecodeError, RequestExecutionError) as exc:
                selected_api.status = ApiStatus.TESTED_FAILED
                st.error(f"{t(lang, 'request_failed')}: {exc}")

        test_result = st.session_state.test_result_by_api.get(selected_api.id)
        if test_result:
            st.write(f"{t(lang, 'status_code')}: {test_result['status_code']}")
            st.write(f"{t(lang, 'response_time')}: {test_result['response_time_ms']} ms")
            st.json(mask_sensitive_headers(test_result["headers"]))
            st.code(test_result["body"])

    with st.container(border=True):
        st.subheader(t(lang, "export"))
        include_values = st.checkbox(t(lang, "include_session_values"), value=False)
        st.caption(t(lang, "safe_export_note"))
        selected_ids = st.multiselect(t(lang, "select_api_export"), options=[api.id for api in apis], default=[api.id for api in apis])

        if st.button(t(lang, "export_selected")):
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

                st.download_button(
                    t(lang, "download_collection"),
                    data=collection_json,
                    file_name="collection.postman_collection.json",
                    mime="application/json",
                )
                st.download_button(
                    t(lang, "download_environment"),
                    data=environment_json,
                    file_name="environment.postman_environment.json",
                    mime="application/json",
                )
