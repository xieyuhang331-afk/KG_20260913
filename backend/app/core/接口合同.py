"""Only core contracts; module-owned Response bodies remain module-owned."""

import re
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.core.security import get_current_user_from_jwt


class FieldErrorDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    code: Literal["REQUIRED", "INVALID_VALUE"]


class ErrorResponseDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: Literal["request rejected"]
    request_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    retryable: bool
    field_errors: list[FieldErrorDTO] = Field(max_length=8)


# Exact source-owned HTTPException values, never vendor exception text.
_HTTP_ERRORS = {
    (401, "Authentication required"): "AUTHENTICATION_REQUIRED",
    (401, "Invalid credentials"): "INVALID_CREDENTIALS",
    (401, "Invalid or expired token"): "ACCESS_TOKEN_INVALID",
    (401, "Invalid token claims"): "ACCESS_TOKEN_INVALID",
    (403, "Unsupported role"): "ROLE_UNSUPPORTED",
    (400, "Invalid current user header"): "AUTH_CONTEXT_INVALID",
    (403, "User is not active"): "USER_INACTIVE",
    (409, "User is not active"): "USER_INACTIVE",
    (403, "Login context is not configured"): "LOGIN_CONTEXT_NOT_CONFIGURED",
    (503, "Authentication service unavailable"): "AUTHENTICATION_UNAVAILABLE",
    (409, "User already exists"): "USER_EXISTS",
    (404, "User not found"): "USER_NOT_FOUND",
    (403, "Forbidden"): "FORBIDDEN",
    (409, "Health profile is required"): "HEALTH_PROFILE_REQUIRED",
    (409, "Health profile already exists"): "HEALTH_PROFILE_EXISTS",
    (404, "Health profile not found"): "HEALTH_PROFILE_NOT_FOUND",
    (422, "Only APP source is allowed for member write API"): "HEALTH_INDICATOR_SOURCE_INVALID",
    (422, "Unknown indicator_type"): "HEALTH_INDICATOR_TYPE_INVALID",
    (422, "Invalid time range"): "HEALTH_INDICATOR_TIME_RANGE_INVALID",
    (409, "Tenant application already exists"): "TENANT_APPLICATION_EXISTS",
    (404, "Tenant application not found"): "TENANT_APPLICATION_NOT_FOUND",
    (404, "Tenant not found"): "TENANT_NOT_FOUND",
    (409, "Tenant application is not pending"): "TENANT_APPLICATION_NOT_PENDING",
    (404, "Identity review subject not found"): "IDENTITY_REVIEW_SUBJECT_NOT_FOUND",
    (409, "Identity review decision conflict"): "IDENTITY_REVIEW_CONFLICT",
    (503, "Identity review service unavailable"): "IDENTITY_REVIEW_UNAVAILABLE",
    (401, "Invalid re-authentication"): "REAUTHENTICATION_INVALID",
    (401, "Invalid or expired step-up"): "STEP_UP_INVALID",
    (429, "Re-authentication rate limited"): "REAUTHENTICATION_RATE_LIMITED",
    (409, "Identity verification submission conflict"): "IDENTITY_VERIFICATION_CONFLICT",
    (429, "Identity verification submission rate limited"): "IDENTITY_VERIFICATION_RATE_LIMITED",
    (503, "Identity verification service unavailable"): "IDENTITY_VERIFICATION_UNAVAILABLE",
}
_SYMBOLIC_HTTP_ERRORS = {
    401: ("ACCESS_TOKEN_STALE", "TOTP_REQUIRED_OR_INVALID"),
    403: ("MEMBER_DETECTION_REPORT_ACCESS_DENIED",),
    404: ("DETECTION_REPORT_NOT_FOUND",),
    409: ("HEALTH_PROFILE_VERSION_CONFLICT", "HEALTH_INDICATOR_UNIT_INCONSISTENT",
          "DETECTION_REPORT_CONTENT_INCONSISTENT", "HEALTH_DATA_UNAVAILABLE"),
    410: ("LEGACY_DISABLED_FOR_PILOT",),
    422: ("HEALTH_INDICATOR_CURSOR_INVALID", "HEALTH_INDICATOR_TIME_RANGE_INVALID",
          "DETECTION_REPORT_QUERY_INVALID"),
    429: ("AUTH_RATE_LIMITED",),
    503: ("AUTHORITY_UNAVAILABLE", "HEALTH_DATA_UNAVAILABLE", "DETECTION_REPORT_UNAVAILABLE",
          "HEALTH_PROFILE_OUTCOME_UNKNOWN", "AUTH_RATE_LIMIT_CAPACITY", "AUTH_RATE_LIMIT_UNAVAILABLE"),
}
_HTTP_ERRORS.update({
    (status, code): code for status, codes in _SYMBOLIC_HTTP_ERRORS.items() for code in codes
})
_DEFAULT_ERRORS = {
    400: "INVALID_REQUEST", 401: "AUTHENTICATION_REQUIRED", 403: "FORBIDDEN",
    404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 410: "GONE",
    413: "CONTENT_TOO_LARGE", 422: "VALIDATION_FAILED", 429: "RATE_LIMITED",
    500: "INTERNAL_ERROR", 503: "DEPENDENCY_UNAVAILABLE",
}
_RETRYABLE_ERRORS = frozenset({
    "AUTHENTICATION_UNAVAILABLE", "IDENTITY_REVIEW_UNAVAILABLE",
    "IDENTITY_VERIFICATION_UNAVAILABLE", "AUTHORITY_UNAVAILABLE",
})


def safe_log_error_code(value: object) -> str | None:
    if type(value) is str and value in {
        *_HTTP_ERRORS.values(), *_DEFAULT_ERRORS.values(), "REQUEST_REJECTED",
        "REQUEST_CANCELLED", "REQUEST_STREAM_FAILED",
    }:
        return value
    return None


def _safe_headers(original: dict | None, status: int) -> dict[str, str]:
    headers = {"Cache-Control": "no-store, private", "Pragma": "no-cache"}
    for name, value in (original or {}).items():
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        lowered = name.lower()
        if lowered == "www-authenticate" and value == "Bearer":
            headers["WWW-Authenticate"] = "Bearer"
        elif lowered == "retry-after" and re.fullmatch(r"[0-9]{1,6}", value):
            headers["Retry-After"] = value
        elif lowered == "cache-control" and value == "no-store, private, max-age=0":
            headers["Cache-Control"] = value
        elif lowered == "vary" and all(
            part.strip() in {"Authorization", "Origin", "Accept", "Accept-Encoding"}
            for part in value.split(",")
        ):
            headers["Vary"] = ", ".join(dict.fromkeys(part.strip() for part in value.split(",")))
        elif lowered == "allow" and status == 405:
            methods = [part.strip().upper() for part in value.split(",")]
            allowed = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
            if methods and len(methods) == len(set(methods)) and all(method in allowed for method in methods):
                headers["Allow"] = ", ".join(methods)
    return headers


def error_response(
    request: Request, status: int, code: str, *, retryable: bool = False,
    field_errors: list[FieldErrorDTO] | None = None, headers: dict | None = None,
) -> JSONResponse:
    request.state.error_code = code
    request.state.error_retryable = retryable
    body = ErrorResponseDTO(
        code=code, message="request rejected", request_id=request.state.request_id, retryable=retryable,
        field_errors=field_errors or [],
    )
    return JSONResponse(body.model_dump(), status_code=status, headers=_safe_headers(headers, status))


async def core_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    key = (exc.status_code, exc.detail) if type(exc.detail) is str else None
    registered = _HTTP_ERRORS.get(key)
    code = registered or _DEFAULT_ERRORS.get(exc.status_code, "REQUEST_REJECTED")
    return error_response(
        request, exc.status_code, code,
        retryable=registered in _RETRYABLE_ERRORS, headers=exc.headers,
    )


def _known_validation_fields(request: Request) -> set[tuple[str, ...]]:
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return set()
    known = set()
    for location, params in (
        ("body", route.dependant.body_params), ("query", route.dependant.query_params),
        ("path", route.dependant.path_params), ("header", route.dependant.header_params),
    ):
        for param in params:
            model = param.field_info.annotation
            if location == "body" and isinstance(model, type) and issubclass(model, BaseModel):
                for name, field in model.model_fields.items():
                    alias = field.alias or name
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", alias):
                        known.add(("body", alias))
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", param.alias):
                known.add((location, param.alias))
    return known


async def core_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    known = _known_validation_fields(request)
    fields = {}
    for error in exc.errors():
        location = tuple(error.get("loc", ()))
        if location in known:
            name = ".".join(location)
            fields[name] = FieldErrorDTO(
                field=name, code="REQUIRED" if error.get("type") == "missing" else "INVALID_VALUE",
            )
        if len(fields) == 8:
            break
    return error_response(request, 422, "VALIDATION_FAILED", field_errors=list(fields.values()))


def install_error_contract(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, core_http_error)
    app.add_exception_handler(RequestValidationError, core_validation_error)

_PUBLIC_OPERATIONS = frozenset({
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/users/register"),
    ("POST", "/api/v1/institution-onboarding/activate"),
    ("POST", "/api/v1/therapist-onboarding/activate"),
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
})
_ADDITIONAL_CREDENTIALS = {
    ("GET", "/api/v1/reviews/users/{user_id}/identity"): ("IdentityReviewStepUp", "X-Identity-Review-Step-Up"),
    ("POST", "/api/v1/family/data-exports"): ("RecentAccessStepUp", "X-Step-Up-Token"),
    ("POST", "/api/v1/family/data-exports/{export_id}/download-access"): ("RecentAccessStepUp", "X-Step-Up-Token"),
    ("GET", "/api/v1/private-files/{file_id}/content"): ("PrivateFileAccess", "X-Private-File-Access"),
}


def _uses_access(dependant) -> bool:
    return dependant.call is get_current_user_from_jwt or any(
        _uses_access(child) for child in dependant.dependencies
    )


def _express_core_errors(route: APIRoute, operation: dict, *, access: bool) -> None:
    # These three auth classes own only validation, not HTTPException. Other
    # custom route classes retain their existing body/response schema contracts.
    from app.modules.auth.api import (
        IdentityVerificationSubmissionRoute,
        _LoginInputRoute,
        _RegistrationInputRoute,
    )

    if type(route) not in {APIRoute, _LoginInputRoute, _RegistrationInputRoute, IdentityVerificationSubmissionRoute}:
        return
    responses = operation["responses"]
    statuses = {key for key in responses if key.isdigit() and 400 <= int(key) <= 599}
    statuses.add("500")
    if access:
        statuses.update({"401", "503"})
    for status in sorted(statuses):
        if status == "422" and type(route) is not APIRoute:
            continue
        if status == "410" and route.path in {
            "/api/v1/users/{user_id}/identity", "/api/v1/users/{user_id}/tenant-binding",
        }:
            continue
        response = responses.setdefault(status, {"description": "Request rejected"})
        response.setdefault("content", {})["application/json"] = {
            "schema": {"$ref": "#/components/schemas/ErrorResponseDTO"},
        }
        response.setdefault("headers", {}).update({
            "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
            "Cache-Control": {"schema": {"type": "string", "enum": [
                "no-store, private", "no-store, private, max-age=0",
            ]}},
            "Pragma": {"schema": {"type": "string", "const": "no-cache"}},
        })


def express_security_contract(app: FastAPI, schema: dict) -> dict:
    error_schema = ErrorResponseDTO.model_json_schema(ref_template="#/components/schemas/{model}")
    models = schema.setdefault("components", {}).setdefault("schemas", {})
    models.update(error_schema.pop("$defs", {}))
    models["ErrorResponseDTO"] = error_schema
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["AccessBearer"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    for name, header in _ADDITIONAL_CREDENTIALS.values():
        schemes[name] = {"type": "apiKey", "in": "header", "name": header}
    classified = set()
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        for method in route.methods:
            key = (method, route.path_format)
            access = _uses_access(route.dependant)
            if key in _PUBLIC_OPERATIONS:
                if access:
                    raise RuntimeError("API_SECURITY_CLASSIFICATION_INVALID")
                requirement = []
            elif access:
                requirement = [{"AccessBearer": []}]
                if key in _ADDITIONAL_CREDENTIALS:
                    name, _ = _ADDITIONAL_CREDENTIALS[key]
                    requirement[0][name] = []
            else:
                raise RuntimeError("API_SECURITY_CLASSIFICATION_INVALID")
            schema["paths"][route.path_format][method.lower()]["security"] = requirement
            _express_core_errors(
                route.original_route, schema["paths"][route.path_format][method.lower()], access=access,
            )
            classified.add(key)
    if not _PUBLIC_OPERATIONS.issubset(classified) or not _ADDITIONAL_CREDENTIALS.keys() <= classified:
        raise RuntimeError("API_SECURITY_CLASSIFICATION_INVALID")
    return schema
