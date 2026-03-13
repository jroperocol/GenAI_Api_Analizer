"""Pydantic models for normalized API documentation extraction schema."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ApiStatus(str, Enum):
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    NOT_TESTED = "not_tested"
    TESTED_OK = "tested_ok"
    TESTED_FAILED = "tested_failed"


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class AuthType(str, Enum):
    BASIC = "basic"
    TOKEN = "token"
    NO_AUTH = "no_auth"
    UNKNOWN = "unknown"


class ErrorSeverity(str, Enum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"


class ApiIssue(BaseModel):
    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.NON_BLOCKING


class ParamField(BaseModel):
    name: str
    value: Optional[str] = None
    required: bool = False
    sensitive: bool = False
    variable_name: Optional[str] = None
    description: Optional[str] = None


class BodyDefinition(BaseModel):
    required: bool = False
    content_type: Optional[str] = None
    schema: Dict[str, Any] = Field(default_factory=dict)
    example: Optional[Any] = None


class EndpointDefinition(BaseModel):
    raw: str = ""
    qa: Optional[str] = None
    prd: Optional[str] = None
    base_url: str = ""
    path: str = ""


class AuthDefinition(BaseModel):
    type: AuthType = AuthType.UNKNOWN
    goflow_supported: bool = True
    required: bool = True
    location: str = "header"
    notes: str = ""


class EnvironmentVariable(BaseModel):
    key: str
    initial_value: str = ""
    current_value: str = ""
    sensitive: bool = False


class SourceEvidence(BaseModel):
    file_name: str = ""
    excerpt: str = ""


class ApiRecord(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    status: ApiStatus = ApiStatus.NEEDS_REVIEW
    method: Optional[HttpMethod] = None
    endpoint: EndpointDefinition = Field(default_factory=EndpointDefinition)
    auth: AuthDefinition = Field(default_factory=AuthDefinition)
    headers: List[ParamField] = Field(default_factory=list)
    path_params: List[ParamField] = Field(default_factory=list)
    query_params: List[ParamField] = Field(default_factory=list)
    body: BodyDefinition = Field(default_factory=BodyDefinition)
    example_response: Optional[Any] = None
    environment_variables: List[EnvironmentVariable] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    errors: List[ApiIssue] = Field(default_factory=list)
    warnings: List[ApiIssue] = Field(default_factory=list)
    source_evidence: List[SourceEvidence] = Field(default_factory=list)


class DocumentAnalysis(BaseModel):
    source_files: List[str] = Field(default_factory=list)
    api_count: int = 0
    apis: List[ApiRecord] = Field(default_factory=list)


class AnalysisPayload(BaseModel):
    document_analysis: DocumentAnalysis = Field(default_factory=DocumentAnalysis)
