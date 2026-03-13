"""Streamlit app for GoAI API Documentation Interpreter."""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from extractor import ExtractionError, extract_apis_with_openai
from file_ingestion import SUPPORTED_TYPES, extract_text_from_uploads
from i18n import t
from models import AnalysisPayload, ApiIssue, ApiRecord, ApiStatus, AuthType, ErrorSeverity, HttpMethod, ParamField
from postman_exporter import build_postman_collection_and_env
from security import contains_likely_credentials, mask_sensitive_headers
from test_runner import RequestExecutionError, execute_test_request
from validator import validate_analysis

st.set_page_config(page_title="GoAI API Documentation Interpreter", layout="wide")


def _init_state() -> None:
    if "analysis" not in st.session_state:
        st.session_state.analysis = AnalysisPayload()
    if "selected_api_id" not in st.session_state:
        st.session_state.selected_api_id = None
    if "test_result" not in st.session_state:
        st.session_state.test_result = None


_init_state()

lang = st.selectbox("Language", options=["en", "es", "pt"], format_func=lambda x: {"en": "English", "es": "Español", "pt": "Português"}[x])

st.title(t(lang, "title"))
st.caption(t(lang, "subtitle"))
st.caption("Powered by GoAI")

with st.container(border=True):
    st.subheader(t(lang, "session_panel"))
    api_key = st.text_input(t(lang, "api_key"), type="password", value="")
    st.info(t(lang, "api_key_note"))

with st.container(border=True):
    st.subheader(t(lang, "doc_input"))
    uploaded_files = st.file_uploader(
        t(lang, "upload_files"),
        type=SUPPORTED_TYPES,
        accept_multiple_files=True,
    )
    raw_text = st.text_area(t(lang, "paste_text"), height=150)

    if st.button(t(lang, "analyze"), type="primary"):
        file_text, source_files, ingest_warnings = extract_text_from_uploads(uploaded_files or [])
        combined_text = "\n\n".join(part for part in [file_text, raw_text] if part.strip())

        if not combined_text.strip():
            st.error("Please upload files or paste text for analysis.")
        elif not api_key:
            st.error("OpenAI API key is required.")
        else:
            if contains_likely_credentials(combined_text):
                st.warning(t(lang, "sensitive_detected"))
            for warn in ingest_warnings:
                st.warning(warn)
            try:
                analysis = extract_apis_with_openai(api_key=api_key, doc_text=combined_text)
                analysis.document_analysis.source_files = source_files
                st.session_state.analysis = validate_analysis(analysis)
                if st.session_state.analysis.document_analysis.apis:
                    st.session_state.selected_api_id = st.session_state.analysis.document_analysis.apis[0].id
                st.success(f"Detected {st.session_state.analysis.document_analysis.api_count} APIs.")
            except ExtractionError as exc:
                st.error(f"Analysis failed: {exc}")

analysis: AnalysisPayload = st.session_state.analysis
apis: List[ApiRecord] = analysis.document_analysis.apis

with st.container(border=True):
    st.subheader(t(lang, "analysis_results"))
    if not apis:
        st.write("No APIs analyzed yet.")
    else:
        rows = []
        for api in apis:
            rows.append(
                {
                    "Select": api.id == st.session_state.selected_api_id,
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

        df = pd.DataFrame(rows)
        edited = st.data_editor(
            df,
            hide_index=True,
            column_config={"Select": st.column_config.CheckboxColumn(required=True)},
            disabled=[c for c in df.columns if c not in {"Select"}],
            key="api_table_editor",
        )
        selected_rows = edited[edited["Select"] == True]
        if not selected_rows.empty:
            st.session_state.selected_api_id = selected_rows.iloc[0]["id"]

selected_api = next((api for api in apis if api.id == st.session_state.selected_api_id), None)

if selected_api:
    with st.container(border=True):
        st.subheader(t(lang, "api_detail"))
        selected_api.name = st.text_input("name", value=selected_api.name)
        selected_api.description = st.text_area("description", value=selected_api.description, height=80)
        c1, c2, c3 = st.columns(3)
        selected_api.endpoint.base_url = c1.text_input("base_url", value=selected_api.endpoint.base_url)
        selected_api.endpoint.path = c2.text_input("path", value=selected_api.endpoint.path)
        selected_api.endpoint.raw = c3.text_input("endpoint raw", value=selected_api.endpoint.raw)
        c4, c5 = st.columns(2)
        selected_api.endpoint.qa = c4.text_input("qa url", value=selected_api.endpoint.qa or "") or None
        selected_api.endpoint.prd = c5.text_input("prd url", value=selected_api.endpoint.prd or "") or None

        c6, c7 = st.columns(2)
        selected_api.method = HttpMethod(c6.selectbox("method", options=[m.value for m in HttpMethod], index=[m.value for m in HttpMethod].index(selected_api.method.value) if selected_api.method else 0))
        selected_api.auth.type = AuthType(c7.selectbox("auth type", options=[a.value for a in AuthType], index=[a.value for a in AuthType].index(selected_api.auth.type.value)))
        selected_api.auth.notes = st.text_area("auth notes", value=selected_api.auth.notes, height=80)

        def table_editor(label: str, items: List[ParamField]) -> List[ParamField]:
            frame = pd.DataFrame([i.model_dump() for i in items]) if items else pd.DataFrame(columns=["name", "value", "required", "sensitive", "variable_name", "description"])
            updated = st.data_editor(frame, num_rows="dynamic", use_container_width=True, key=f"{selected_api.id}_{label}")
            cleaned: List[ParamField] = []
            for row in updated.to_dict(orient="records"):
                if row.get("name"):
                    cleaned.append(ParamField(**row))
            return cleaned

        selected_api.headers = table_editor("headers", selected_api.headers)
        selected_api.path_params = table_editor("path_params", selected_api.path_params)
        selected_api.query_params = table_editor("query_params", selected_api.query_params)

        selected_api.body.content_type = st.text_input("body content type", value=selected_api.body.content_type or "") or None
        body_example_text = st.text_area(
            "body example / body editor",
            value=json.dumps(selected_api.body.example, indent=2) if isinstance(selected_api.body.example, (dict, list)) else (selected_api.body.example or ""),
            height=120,
        )
        selected_api.body.example = body_example_text or None

        st.text_area("missing_fields", value="\n".join(selected_api.missing_fields), height=80)
        st.text_area("errors", value="\n".join([f"{e.code}: {e.message}" for e in selected_api.errors]), height=80)
        st.text_area("warnings", value="\n".join([f"{w.code}: {w.message}" for w in selected_api.warnings]), height=80)
        st.text_area(
            "source evidence excerpts",
            value="\n\n".join([f"{s.file_name}: {s.excerpt}" for s in selected_api.source_evidence]),
            height=120,
        )

    with st.container(border=True):
        st.subheader(t(lang, "test_runner"))
        method = st.selectbox("HTTP Method", options=[m.value for m in HttpMethod], index=[m.value for m in HttpMethod].index(selected_api.method.value if selected_api.method else "GET"))
        default_url = selected_api.endpoint.raw or f"{selected_api.endpoint.base_url.rstrip('/')}/{selected_api.endpoint.path.lstrip('/')}".strip("/")
        final_url = st.text_input("Final URL", value=default_url)

        headers_default = {h.name: h.value or "" for h in selected_api.headers}
        query_default = {q.name: q.value or "" for q in selected_api.query_params}
        headers_json = st.text_area("Headers (JSON)", value=json.dumps(headers_default, indent=2), height=100)
        query_json = st.text_area("Query Params (JSON)", value=json.dumps(query_default, indent=2), height=100)
        body_value = st.text_area("Request Body", value=selected_api.body.example or "", height=120)
        timeout = st.number_input(t(lang, "timeout"), min_value=1, max_value=120, value=20)

        if st.button(t(lang, "run_test")):
            try:
                headers = json.loads(headers_json) if headers_json.strip() else {}
                params = json.loads(query_json) if query_json.strip() else {}
                result = execute_test_request(
                    method=method,
                    url=final_url,
                    headers=headers,
                    params=params,
                    body=body_value,
                    timeout_seconds=int(timeout),
                )
                st.session_state.test_result = result
                selected_api.status = ApiStatus.TESTED_OK if 200 <= result["status_code"] < 400 else ApiStatus.TESTED_FAILED
            except (json.JSONDecodeError, RequestExecutionError) as exc:
                selected_api.status = ApiStatus.TESTED_FAILED
                st.error(f"Request failed: {exc}")

        if st.session_state.test_result:
            result = st.session_state.test_result
            st.write(f"Status code: {result['status_code']}")
            st.write(f"Response time: {result['response_time_ms']} ms")
            st.json(mask_sensitive_headers(result["headers"]))
            st.code(result["body"])

    with st.container(border=True):
        st.subheader(t(lang, "export"))
        include_values = st.checkbox(t(lang, "include_session_values"), value=False)
        st.caption(t(lang, "safe_export_note"))

        selected_ids = st.multiselect("APIs to export", options=[a.id for a in apis], default=[a.id for a in apis])

        if st.button(t(lang, "export_selected")):
            selected_apis = [a for a in apis if a.id in selected_ids]
            if not selected_apis:
                st.error("No APIs selected.")
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
                    "Download collection.postman_collection.json",
                    data=collection_json,
                    file_name="collection.postman_collection.json",
                    mime="application/json",
                )
                st.download_button(
                    "Download environment.postman_environment.json",
                    data=environment_json,
                    file_name="environment.postman_environment.json",
                    mime="application/json",
                )
