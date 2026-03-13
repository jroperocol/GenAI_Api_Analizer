"""Validation and status assignment rules for GoFlow compatibility."""

from __future__ import annotations

from typing import List

from models import AnalysisPayload, ApiIssue, ApiRecord, ApiStatus, AuthType, ErrorSeverity

SUPPORTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
UNSUPPORTED_AUTH_MARKERS = ["oauth", "sigv4", "mtls", "signature", "refresh flow"]


def _has_code(issues: List[ApiIssue], code: str) -> bool:
    return any(i.code == code for i in issues)


def _add_issue(issues: List[ApiIssue], issue: ApiIssue) -> None:
    if not _has_code(issues, issue.code):
        issues.append(issue)


def validate_api(api: ApiRecord) -> ApiRecord:
    """Apply required validation and status logic to a single API."""
    errors = list(api.errors)

    if not api.endpoint.raw and not api.endpoint.path:
        _add_issue(errors, ApiIssue(code="MISSING_ENDPOINT", message="Endpoint is missing", severity=ErrorSeverity.BLOCKING))

    if api.method is None or api.method.value not in SUPPORTED_METHODS:
        _add_issue(
            errors,
            ApiIssue(code="MISSING_METHOD", message="HTTP method is missing or unsupported", severity=ErrorSeverity.BLOCKING),
        )

    auth_notes = (api.auth.notes or "").lower()
    explicit_unsupported_auth = api.auth.type == AuthType.UNKNOWN and any(
        marker in auth_notes for marker in UNSUPPORTED_AUTH_MARKERS
    )

    if explicit_unsupported_auth:
        api.auth.goflow_supported = False
        _add_issue(
            errors,
            ApiIssue(
                code="AUTH_UNSUPPORTED",
                message="Authentication type unsupported for GoFlow V1",
                severity=ErrorSeverity.BLOCKING,
            ),
        )
    elif api.auth.type == AuthType.UNKNOWN:
        api.auth.goflow_supported = False
        _add_issue(
            errors,
            ApiIssue(code="MISSING_AUTH", message="Authentication details missing", severity=ErrorSeverity.NON_BLOCKING),
        )

    if not api.headers:
        _add_issue(
            errors,
            ApiIssue(code="MISSING_HEADERS", message="Required headers may be missing", severity=ErrorSeverity.NON_BLOCKING),
        )

    if api.body.required and not api.body.schema and not api.body.example:
        _add_issue(
            errors,
            ApiIssue(code="MISSING_BODY_SCHEMA", message="Required body has no schema/example", severity=ErrorSeverity.NON_BLOCKING),
        )

    if api.body.required and not api.body.content_type:
        _add_issue(
            errors,
            ApiIssue(code="MISSING_CONTENT_TYPE", message="Required body content type missing", severity=ErrorSeverity.NON_BLOCKING),
        )

    api.errors = errors

    if any(err.severity == ErrorSeverity.BLOCKING and err.code == "AUTH_UNSUPPORTED" for err in errors):
        api.status = ApiStatus.UNSUPPORTED
    elif any(err.severity == ErrorSeverity.BLOCKING for err in errors):
        api.status = ApiStatus.BLOCKED
    elif errors:
        api.status = ApiStatus.NEEDS_REVIEW
    else:
        api.status = ApiStatus.READY

    return api


def validate_analysis(payload: AnalysisPayload) -> AnalysisPayload:
    """Validate all APIs in analysis payload."""
    payload.document_analysis.apis = [validate_api(api) for api in payload.document_analysis.apis]
    payload.document_analysis.api_count = len(payload.document_analysis.apis)
    return payload
