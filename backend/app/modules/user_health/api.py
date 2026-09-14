from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import (
    get_db_session,
    get_health_fact_writer_session,
    get_health_record_writer_session,
    get_member_enrollment_reader_session,
    get_session_factory,
    get_slice4_clinical_reader_session,
    get_slice4_identity_authority_session,
    get_slice4_institution_reader_session,
)
from app.core.permissions import ensure_can_access_own_user_resource, ensure_is_member
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.接口合同 import error_response
from app.modules.assessment_readiness.repository import AssessmentReadinessRepository
from app.modules.assessment_readiness.schemas import AssessmentReadinessDTO
from app.modules.assessment_readiness.service import read_current_readiness
from app.modules.member_enrollment.identity_authority import (
    Slice4IdentitySummaryAuthority,
)
from app.modules.member_enrollment.repository import MemberEnrollmentRepository
from app.modules.projection_read.domain import (
    ProjectionInvalidRequest,
    ProjectionReadUnavailable,
)
from app.modules.projection_read.service import (
    INDICATOR_CATALOG_V2,
    HealthProjectionCoverageAuthorityService,
    LatestReadyHealthProjectionResolverService,
    MemberHealthProjectionReadService,
)
from app.modules.user_health.repository import (
    Slice4HealthRecordRepository,
    UserHealthRepositoryError,
)
from app.modules.user_health.schemas import (
    DetectionReportDetailDTO,
    DetectionReportDTO,
    DetectionReportPageDTO,
    DetectionReportType,
    FormalDetectionReportCreateRequest,
    FormalHealthProfileSnapshotRequest,
    HealthFactBatchDTO,
    HealthFactBatchRequest,
    HealthFactCorrectionRequest,
    HealthFactDTO,
    HealthFactStateRequest,
    HealthIndicatorBatchCreateRequest,
    HealthIndicatorLatestDTO,
    HealthIndicatorPageDTO,
    HealthIndicatorTrendDTO,
    HealthIndicatorTrendPointDTO,
    HealthProfileCreateRequest,
    HealthProfileDTO,
    InstitutionHealthRecordDTO,
    MemberSelfHealthProfileWriteRequest,
)
from app.modules.user_health.service import (
    UserHealthError,
    correct_formal_health_fact,
    create_formal_detection_report,
    create_formal_health_facts,
    create_formal_profile_root,
    create_health_indicators,
    create_health_profile,
    get_health_profile,
    get_latest_health_indicators,
    get_member_self_detection_report_service,
    get_member_self_health_profile,
    get_member_self_latest_health_indicators,
    list_health_indicators,
    list_member_self_detection_reports_service,
    list_member_self_health_indicators,
    put_member_self_health_profile,
    read_formal_detection_report,
    read_formal_detection_reports,
    read_formal_health_fact,
    read_formal_health_profile,
    transition_formal_health_fact_state,
)


class LegacyUserHealthRoute(APIRoute):
    """Translate only known persistence failures for the 11 compatibility routes."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except UserHealthRepositoryError as exc:
                if exc.args == ("DEPENDENCY_UNAVAILABLE",):
                    return error_response(
                        request,
                        503,
                        "DEPENDENCY_UNAVAILABLE",
                        retryable=True,
                    )
                raise
            except DBAPIError:
                return error_response(
                    request,
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    retryable=True,
                )

        return handler


router = APIRouter(
    prefix="/api/v1/users",
    tags=["user_health"],
    route_class=LegacyUserHealthRoute,
)


@router.get("/me/detection-reports")
async def list_member_self_detection_reports(
    report_type: DetectionReportType | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await list_member_self_detection_reports_service(
        session,
        user_id=current_user.id,
        report_type=report_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        cursor=cursor,
    )
    return ok_response(result.model_dump(mode="json"))


@router.get("/me/detection-reports/{report_id}")
async def get_member_self_detection_report(
    report_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await get_member_self_detection_report_service(
        session,
        user_id=current_user.id,
        report_id=report_id,
    )
    return ok_response(result.model_dump(mode="json"))


@router.get("/me/health-indicators")
async def list_member_self_health_indicators_api(
    indicator_type: str | None = Query(default=None, max_length=30),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None, max_length=512),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await list_member_self_health_indicators(
        session,
        user_id=current_user.id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
        cursor=cursor,
    )
    return ok_response(result.model_dump(mode="json"))


@router.get("/me/health-indicators/latest")
async def get_member_self_latest_health_indicators_api(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await get_member_self_latest_health_indicators(session, user_id=current_user.id)
    return ok_response(result.model_dump(mode="json"))


@router.get("/me/health-profile")
async def get_member_self_health_profile_api(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await get_member_self_health_profile(
        session,
        user_id=current_user.id,
    )
    return ok_response(result.model_dump(mode="json"))


@router.put("/me/health-profile")
async def put_member_self_health_profile_api(
    payload: MemberSelfHealthProfileWriteRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    result = await put_member_self_health_profile(
        session,
        confirmation_session_factory_provider=get_session_factory,
        user_id=current_user.id,
        payload=payload,
    )
    return ok_response(result.model_dump(mode="json"))


@router.post("/{user_id}/health-profile", status_code=201)
async def create_health_profile_api(
    user_id: int,
    payload: HealthProfileCreateRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await create_health_profile(session, user_id=user_id, payload=payload)
    return ok_response(result.model_dump(mode="json"))


@router.get("/{user_id}/health-profile")
async def get_health_profile_api(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await get_health_profile(session, user_id=user_id)
    return ok_response(result.model_dump(mode="json"))


@router.post("/{user_id}/health-indicators", status_code=201)
async def create_health_indicators_api(
    user_id: int,
    payload: HealthIndicatorBatchCreateRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    if any(indicator.source != "APP" for indicator in payload.indicators):
        raise HTTPException(status_code=422, detail="Only APP source is allowed for member write API")

    result = await create_health_indicators(session, user_id=user_id, payload=payload)
    return ok_response([indicator.model_dump(mode="json") for indicator in result])


@router.get("/{user_id}/health-indicators")
async def list_health_indicators_api(
    user_id: int,
    indicator_type: str | None = Query(default=None, max_length=30),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await list_health_indicators(
        session,
        user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    return ok_response([indicator.model_dump(mode="json") for indicator in result])


@router.get("/{user_id}/health-indicators/latest")
async def get_latest_health_indicators_api(
    user_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await get_latest_health_indicators(session, user_id=user_id)
    return ok_response([indicator.model_dump(mode="json") for indicator in result])


# Slice 4 formal APIs intentionally live beside the legacy user-keyed routes so the
# old contract remains byte-for-byte addressable while the member-first boundary is
# independently versioned and testable.
_CODE_STATUS = {
    "AUTHENTICATION_REQUIRED": 401,
    "INVALID_REQUEST": 400,
    "INDICATOR_CATALOG_UNKNOWN": 400,
    "INDICATOR_UNIT_INVALID": 400,
    "DEVICE_SOURCE_FORBIDDEN": 400,
    "MEASURED_AT_INVALID": 400,
    "PROFILE_SNAPSHOT_INVALID": 400,
    "REPORT_ATTACHMENT_COUNT_INVALID": 400,
    "ACTOR_CURRENTNESS_FORBIDDEN": 403,
    "PROXY_PERMISSION_FORBIDDEN": 403,
    "THERAPIST_SCOPE_FORBIDDEN": 403,
    "INSTITUTION_SCOPE_FORBIDDEN": 403,
    "SERVICE_CASE_NOT_FOUND": 404,
    "HEALTH_PROFILE_NOT_FOUND": 404,
    "DETECTION_REPORT_NOT_FOUND": 404,
    "HEALTH_FACT_NOT_FOUND": 404,
    "PRIVATE_FILE_NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "STATE_CONFLICT": 409,
    "PROXY_GRANT_INVALID": 409,
    "CONSENT_REQUIRED": 409,
    "PRIVATE_FILE_NOT_CLEAN": 409,
    "PRIVATE_FILE_BIND_CONFLICT": 409,
    "HEALTH_FACT_CORRECTION_CONFLICT": 409,
    "PROJECTION_SYNC_PENDING": 409,
    "OPERATION_RATE_LIMITED": 429,
    "DEPENDENCY_UNAVAILABLE": 503,
    "COMMIT_OUTCOME_UNKNOWN": 503,
    "PROJECTION_READ_UNAVAILABLE": 503,
}


def _family_error_catalog(*, proxy: bool) -> dict[tuple[str, str], tuple[str, ...]]:
    prefix = (
        "/api/v1/family/proxy-enrollments/{enrollment_id}"
        if proxy
        else "/api/v1/family"
    )
    extra = ("PROXY_PERMISSION_FORBIDDEN", "PROXY_GRANT_INVALID") if proxy else ()
    values = {
        ("GET", f"{prefix}/health-profile"): (
            "ACTOR_CURRENTNESS_FORBIDDEN", "HEALTH_PROFILE_NOT_FOUND", "DEPENDENCY_UNAVAILABLE",
        ),
        ("PUT", f"{prefix}/health-profile"): (
            "INVALID_REQUEST", "PROFILE_SNAPSHOT_INVALID", "ACTOR_CURRENTNESS_FORBIDDEN",
            "HEALTH_PROFILE_NOT_FOUND", "VERSION_CONFLICT", "COMMIT_OUTCOME_UNKNOWN",
            "DEPENDENCY_UNAVAILABLE",
        ),
        ("POST", f"{prefix}/detection-reports"): (
            "INVALID_REQUEST", "MEASURED_AT_INVALID", "REPORT_ATTACHMENT_COUNT_INVALID",
            "ACTOR_CURRENTNESS_FORBIDDEN", "PRIVATE_FILE_NOT_FOUND", "PRIVATE_FILE_NOT_CLEAN",
            "PRIVATE_FILE_BIND_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", f"{prefix}/detection-reports"): (
            "INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", f"{prefix}/detection-reports/{{report_id}}"): (
            "ACTOR_CURRENTNESS_FORBIDDEN", "DETECTION_REPORT_NOT_FOUND", "DEPENDENCY_UNAVAILABLE",
        ),
        ("POST", f"{prefix}/health-indicators"): (
            "INVALID_REQUEST", "INDICATOR_CATALOG_UNKNOWN", "INDICATOR_UNIT_INVALID",
            "DEVICE_SOURCE_FORBIDDEN", "MEASURED_AT_INVALID", "ACTOR_CURRENTNESS_FORBIDDEN",
            "COMMIT_OUTCOME_UNKNOWN", "DEPENDENCY_UNAVAILABLE",
        ),
        ("POST", f"{prefix}/health-facts/{{fact_ref}}/corrections"): (
            "INVALID_REQUEST", "INDICATOR_UNIT_INVALID", "ACTOR_CURRENTNESS_FORBIDDEN",
            "HEALTH_FACT_NOT_FOUND", "HEALTH_FACT_CORRECTION_CONFLICT", "STATE_CONFLICT",
            "COMMIT_OUTCOME_UNKNOWN", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", f"{prefix}/health-indicators/history"): (
            "INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "PROJECTION_SYNC_PENDING",
            "PROJECTION_READ_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", f"{prefix}/health-indicators/latest"): (
            "INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "PROJECTION_SYNC_PENDING",
            "PROJECTION_READ_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", f"{prefix}/health-indicators/trends"): (
            "INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "PROJECTION_SYNC_PENDING",
            "PROJECTION_READ_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE",
        ),
    }
    return {key: tuple(dict.fromkeys(("AUTHENTICATION_REQUIRED", *codes, *extra))) for key, codes in values.items()}


def _therapist_codes(*codes: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            ("AUTHENTICATION_REQUIRED", *codes, "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_SCOPE_FORBIDDEN", "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "DEPENDENCY_UNAVAILABLE")
        )
    )


_THERAPIST_PREFIX = "/api/v1/therapist/service-cases/{case_id}"
_INSTITUTION_PREFIX = "/api/v1/institution/service-cases/{case_id}"
SLICE4_ROUTE_ERROR_CODES = {
    **_family_error_catalog(proxy=False),
    **_family_error_catalog(proxy=True),
    ("GET", f"{_THERAPIST_PREFIX}/health-profile"): _therapist_codes("HEALTH_PROFILE_NOT_FOUND"),
    ("GET", f"{_THERAPIST_PREFIX}/detection-reports"): _therapist_codes("INVALID_REQUEST"),
    ("GET", f"{_THERAPIST_PREFIX}/detection-reports/{{report_id}}"): _therapist_codes("DETECTION_REPORT_NOT_FOUND"),
    ("POST", f"{_THERAPIST_PREFIX}/health-indicators"): _therapist_codes(
        "INVALID_REQUEST", "INDICATOR_CATALOG_UNKNOWN", "INDICATOR_UNIT_INVALID",
        "DEVICE_SOURCE_FORBIDDEN", "MEASURED_AT_INVALID", "DETECTION_REPORT_NOT_FOUND",
        "PRIVATE_FILE_NOT_CLEAN", "COMMIT_OUTCOME_UNKNOWN",
    ),
    ("POST", f"{_THERAPIST_PREFIX}/health-facts/{{fact_ref}}/verify"): _therapist_codes(
        "INVALID_REQUEST", "HEALTH_FACT_NOT_FOUND", "STATE_CONFLICT", "VERSION_CONFLICT",
        "COMMIT_OUTCOME_UNKNOWN",
    ),
    ("POST", f"{_THERAPIST_PREFIX}/health-facts/{{fact_ref}}/dispute"): _therapist_codes(
        "INVALID_REQUEST", "HEALTH_FACT_NOT_FOUND", "STATE_CONFLICT", "VERSION_CONFLICT",
        "COMMIT_OUTCOME_UNKNOWN",
    ),
    ("POST", f"{_THERAPIST_PREFIX}/health-facts/{{fact_ref}}/corrections"): _therapist_codes(
        "INVALID_REQUEST", "INDICATOR_UNIT_INVALID", "HEALTH_FACT_NOT_FOUND",
        "HEALTH_FACT_CORRECTION_CONFLICT", "STATE_CONFLICT", "COMMIT_OUTCOME_UNKNOWN",
    ),
    ("GET", f"{_THERAPIST_PREFIX}/health-indicators/history"): _therapist_codes(
        "INVALID_REQUEST", "PROJECTION_SYNC_PENDING", "PROJECTION_READ_UNAVAILABLE",
    ),
    ("GET", f"{_THERAPIST_PREFIX}/health-indicators/latest"): _therapist_codes(
        "INVALID_REQUEST", "PROJECTION_SYNC_PENDING", "PROJECTION_READ_UNAVAILABLE",
    ),
    ("GET", f"{_THERAPIST_PREFIX}/health-indicators/trends"): _therapist_codes(
        "INVALID_REQUEST", "PROJECTION_SYNC_PENDING", "PROJECTION_READ_UNAVAILABLE",
    ),
    ("GET", f"{_INSTITUTION_PREFIX}/health-record"): (
        "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "INSTITUTION_SCOPE_FORBIDDEN",
        "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "DEPENDENCY_UNAVAILABLE",
    ),
    ("GET", f"{_INSTITUTION_PREFIX}/detection-reports"): (
        "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "INSTITUTION_SCOPE_FORBIDDEN",
        "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "DEPENDENCY_UNAVAILABLE",
    ),
    ("GET", f"{_INSTITUTION_PREFIX}/health-indicators/latest"): (
        "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "INSTITUTION_SCOPE_FORBIDDEN",
        "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "PROJECTION_SYNC_PENDING",
        "PROJECTION_READ_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE",
    ),
    ("GET", "/api/v1/service-cases/{case_id}/assessment-readiness"): (
        "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN",
        "PROXY_PERMISSION_FORBIDDEN", "THERAPIST_SCOPE_FORBIDDEN",
        "INSTITUTION_SCOPE_FORBIDDEN", "SERVICE_CASE_NOT_FOUND",
        "DEPENDENCY_UNAVAILABLE",
    ),
}

_SLICE4_HTTP_ERROR_CONTRACT_ROUTES = {
    ("GET", f"{_INSTITUTION_PREFIX}/health-record"),
    ("GET", f"{_INSTITUTION_PREFIX}/detection-reports"),
    ("GET", f"{_INSTITUTION_PREFIX}/health-indicators/latest"),
    ("GET", "/api/v1/service-cases/{case_id}/assessment-readiness"),
}


class Slice4Route(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            key = (next(iter(self.methods)), self.path)
            try:
                return await original(request)
            except RequestValidationError:
                status_code = 422 if key in _SLICE4_HTTP_ERROR_CONTRACT_ROUTES else 400
                return error_response(request, status_code, "INVALID_REQUEST")
            except HTTPException as exc:
                allowed = SLICE4_ROUTE_ERROR_CODES[key]
                detail = exc.detail
                code = detail.get("code") if isinstance(detail, dict) else detail if isinstance(detail, str) else None
                if exc.status_code == 401:
                    return error_response(request, 401, "AUTHENTICATION_REQUIRED", headers={"WWW-Authenticate": "Bearer"})
                if exc.status_code == 503 and code not in allowed:
                    code = "DEPENDENCY_UNAVAILABLE"
                if type(code) is not str or code not in allowed:
                    code = "INVALID_REQUEST" if "INVALID_REQUEST" in allowed else "DEPENDENCY_UNAVAILABLE"
                status = _CODE_STATUS[code]
                return error_response(
                    request, status, code,
                    retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                )
            except (UserHealthError, UserHealthRepositoryError) as exc:
                code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
                if code not in SLICE4_ROUTE_ERROR_CODES[key]:
                    return error_response(request, 500, "INTERNAL_ERROR")
                status = _CODE_STATUS[code]
                return error_response(
                    request, status, code,
                    retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                )
            except Exception:
                return error_response(request, 500, "INTERNAL_ERROR")

        return handler


def strip_slice4_validation_responses(schema: dict[str, object]) -> dict[str, object]:
    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        return schema
    for (method, path), codes in SLICE4_ROUTE_ERROR_CODES.items():
        operation = paths.get(path, {}).get(method.lower())
        if not isinstance(operation, dict):
            continue
        responses = operation.get("responses", {})
        if isinstance(responses, dict) and (method, path) not in _SLICE4_HTTP_ERROR_CONTRACT_ROUTES:
            responses.pop("422", None)
        validation_status = (
            422 if (method, path) in _SLICE4_HTTP_ERROR_CONTRACT_ROUTES else 400
        )
        statuses = {
            *(str(_CODE_STATUS[code]) for code in codes),
            str(validation_status),
            "500",
        }
        for status in sorted(statuses, key=int):
            example_code = "INTERNAL_ERROR" if status == "500" else next(
                (code for code in codes if _CODE_STATUS[code] == int(status)),
                "INVALID_REQUEST",
            )
            response = operation.setdefault("responses", {}).setdefault(status, {"description": "Request rejected"})
            examples = {"rejected": {"value": {
                "code": example_code,
                "message": "request rejected", "request_id": "01990000-0000-7000-8000-000000000201",
                "retryable": status == "503" and example_code != "COMMIT_OUTCOME_UNKNOWN", "field_errors": [],
            }}}
            if status in {"401", "503"}:
                authentication_code = (
                    "AUTHENTICATION_REQUIRED"
                    if status == "401" and "AUTHENTICATION_REQUIRED" in codes
                    else "UNAUTHENTICATED" if status == "401" else "DEPENDENCY_UNAVAILABLE"
                )
                examples["authentication"] = {"value": {
                    "code": authentication_code,
                    "message": "request rejected", "request_id": "01990000-0000-7000-8000-000000000201",
                    "retryable": status == "503", "field_errors": [],
                }}
            response.setdefault("content", {})["application/json"] = {
                "schema": {"$ref": "#/components/schemas/ErrorResponseDTO"},
                "examples": examples,
            }
            headers = response.setdefault("headers", {})
            headers.update({
                "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
                "Cache-Control": {"schema": {"type": "string", "enum": ["no-store, private"]}},
                "Pragma": {"schema": {"type": "string", "const": "no-cache"}},
            })
            if status == "401":
                headers["WWW-Authenticate"] = {"schema": {"type": "string", "enum": ["Bearer"]}}
            if status in {"429", "503"}:
                headers["Retry-After"] = {"schema": {"type": "integer", "minimum": 0}}
        operation["x-symbolic-error-codes"] = list(codes)
    return schema


family_health_router = APIRouter(prefix="/api/v1/family", tags=["slice4-health"], route_class=Slice4Route)
therapist_health_router = APIRouter(prefix="/api/v1/therapist", tags=["slice4-health"], route_class=Slice4Route)
institution_health_router = APIRouter(prefix="/api/v1/institution", tags=["slice4-health"], route_class=Slice4Route)
readiness_router = APIRouter(prefix="/api/v1", tags=["slice4-readiness"], route_class=Slice4Route)
formal_routers = (family_health_router, therapist_health_router, institution_health_router, readiness_router)

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def _formal_error(code: str) -> HTTPException:
    return HTTPException(status_code=_CODE_STATUS[code], detail={"code": code, "message": "request rejected"})


async def _formal_pending():
    raise _formal_error("DEPENDENCY_UNAVAILABLE")


def _fact_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if set(payload) != {"measured_at", "fact_ref"}:
            raise ValueError
        measured_at = datetime.fromisoformat(payload["measured_at"])
        fact_ref = UUID(payload["fact_ref"])
        if measured_at.utcoffset() is None or fact_ref.version != 7:
            raise ValueError
        return measured_at, fact_ref
    except Exception:
        raise _formal_error("INVALID_REQUEST") from None


def _fact_cursor_text(measured_at: datetime, fact_ref: UUID) -> str:
    body = json.dumps(
        {"fact_ref": str(fact_ref), "measured_at": measured_at.isoformat()},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def _fact_dto(item) -> HealthFactDTO:
    return HealthFactDTO(
        fact_ref=item.fact_ref,
        indicator_code=item.indicator_code,
        value=item.numeric_value,
        unit=item.unit,
        measured_at=item.measured_at,
        received_at=item.received_at,
        source=item.source_type,
        verification_state=item.verification_state,
    )


async def _projection_rows(
    *, subject_member_id: UUID, indicator_codes: tuple[str, ...],
    limit: int, cursor: str | None = None,
):
    parsed_cursor = _fact_cursor(cursor)
    measured_to = datetime.now(timezone.utc)
    measured_from = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        token = await HealthProjectionCoverageAuthorityService().capture(
            subject_member_id=subject_member_id,
            required_indicator_codes=indicator_codes,
        )
        resolved = await LatestReadyHealthProjectionResolverService().resolve(
            coverage_token=token
        )
        if resolved is None:
            raise _formal_error("PROJECTION_SYNC_PENDING")
        page = await MemberHealthProjectionReadService().list_current_facts(
            resolved_generation=resolved,
            subject_member_id=subject_member_id,
            indicator_codes=indicator_codes,
            measured_from=measured_from,
            measured_to=measured_to,
            limit=limit + 1,
            cursor=parsed_cursor,
        )
        return page.items
    except HTTPException:
        raise
    except ProjectionInvalidRequest:
        raise _formal_error("INVALID_REQUEST") from None
    except ProjectionReadUnavailable:
        raise _formal_error("PROJECTION_READ_UNAVAILABLE") from None
    except Exception:
        raise _formal_error("PROJECTION_READ_UNAVAILABLE") from None


def _requested_indicators(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result = tuple(values) if values else tuple(sorted(INDICATOR_CATALOG_V2))
    if len(set(result)) != len(result) or not set(result).issubset(INDICATOR_CATALOG_V2):
        raise _formal_error("INVALID_REQUEST")
    return result


def _history_dto(rows, limit: int) -> HealthIndicatorPageDTO:
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit and items:
        next_cursor = _fact_cursor_text(items[-1].measured_at, items[-1].fact_ref)
    return HealthIndicatorPageDTO(
        items=[_fact_dto(item) for item in items], next_cursor=next_cursor
    )


def _latest_dto(rows, indicators: tuple[str, ...]) -> HealthIndicatorLatestDTO:
    selected = {}
    for row in rows:
        selected.setdefault(row.indicator_code, row)
    return HealthIndicatorLatestDTO(
        items=[_fact_dto(selected[code]) for code in indicators if code in selected]
    )


def _trend_dto(rows, indicator_code: str, limit: int) -> HealthIndicatorTrendDTO:
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit and items:
        next_cursor = _fact_cursor_text(items[-1].measured_at, items[-1].fact_ref)
    unit = items[0].unit if items else {
        "height": "cm", "weight": "kg", "waist": "cm",
        "systolic_bp": "mmHg", "diastolic_bp": "mmHg",
        "heart_rate": "bpm", "fasting_glucose": "mmol/L",
        "postprandial_glucose_2h": "mmol/L", "hba1c": "%",
        "total_cholesterol": "mmol/L", "triglyceride": "mmol/L",
        "hdl_c": "mmol/L", "ldl_c": "mmol/L",
    }[indicator_code]
    return HealthIndicatorTrendDTO(
        indicator_code=indicator_code,
        unit=unit,
        points=[
            HealthIndicatorTrendPointDTO(measured_at=item.measured_at, value=item.numeric_value)
            for item in reversed(items)
        ],
        next_cursor=next_cursor,
    )


def _report_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if value is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if set(payload) != {"measured_at", "report_id"}:
            raise ValueError
        measured_at = datetime.fromisoformat(payload["measured_at"])
        report_id = UUID(payload["report_id"])
        if measured_at.utcoffset() is None or report_id.version != 7:
            raise ValueError
        return measured_at, report_id
    except Exception:
        raise _formal_error("INVALID_REQUEST") from None


def _report_cursor_text(measured_at: datetime, report_id: UUID) -> str:
    body = json.dumps(
        {"measured_at": measured_at.isoformat(), "report_id": str(report_id)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def _institution_report_dto(row: dict) -> DetectionReportDTO:
    return DetectionReportDTO(
        report_id=UUID(str(row["report_id"])),
        subject_ref=UUID(str(row["subject_ref"])),
        report_type=row["report_type"],
        measured_at=row["measured_at"],
        received_at=row["received_at"],
        status=row["report_status"],
        source=row["source_type"],
        attachment_count=row["attachment_count"],
        structured_indicator_codes=(),
        supersedes_report_id=(
            UUID(str(row["supersedes_report_id"]))
            if row["supersedes_report_id"] is not None
            else None
        ),
        version=row["report_version"],
        created_at=row["report_created_at"],
    )


async def _family_member(
    authority,
    subject_authority: Slice4HealthRecordRepository,
    actor: CurrentUser,
) -> UUID:
    if actor.role != "member" or actor.tenant_id is not None or actor.org_id is not None:
        raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
    row = (
        await authority.execute(
            text(
                'SELECT u.role,u.status,u.tenant_id '
                'FROM public."user" u WHERE u.id=:user_id FOR SHARE OF u'
            ),
            {"user_id": actor.id},
        )
    ).mappings().one_or_none()
    if (
        row is None
        or row["role"] != "member"
        or row["status"] != "active"
        or row["tenant_id"] is not None
    ):
        raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
    subject = await subject_authority.subject_authority(
        actor_user_id=actor.id,
        actor_context="SELF",
    )
    if subject is None or subject["subject_user_id"] != actor.id:
        raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
    return UUID(str(subject["subject_member_id"]))


async def _family_health_scope(
    authority,
    enrollment_reader,
    subject_authority: Slice4HealthRecordRepository,
    actor: CurrentUser,
    *,
    enrollment_id: UUID | None,
    permission: str,
) -> dict[str, object]:
    member_id = await _family_member(authority, subject_authority, actor)
    repository = MemberEnrollmentRepository(enrollment_reader)
    if enrollment_id is None:
        rows = await repository.safe_view_rows(
            "slice3_family_enrollment_read_v1",
            predicates={"subject_member_id": member_id},
            order="enrollment_id",
            cursor_id=UUID(int=0),
            limit=101,
        )
        candidates = [
            row
            for row in rows
            if UUID(str(row["subject_member_id"])) == member_id
            and row["status"] == "CASE_CREATED"
            and row["service_case_id"] is not None
        ]
        if len(candidates) != 1:
            raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
        row = candidates[0]
        context = "SELF"
    else:
        row = await repository.family_enrollment_detail(member_id, enrollment_id, limit=2)
        proxy = None if row is None else row["proxy"]
        if row is None or proxy is None or row["service_case_id"] is None:
            raise _formal_error("PROXY_GRANT_INVALID")
        permissions = tuple(proxy.get("permission_codes") or ())
        if permission not in permissions:
            raise _formal_error("PROXY_PERMISSION_FORBIDDEN")
        context = {
            "DAILY_VIEW": "PROXY_DAILY_VIEW",
            "DAILY_INPUT": "PROXY_DAILY_INPUT",
            "REPORT_UPLOAD": "PROXY_REPORT_UPLOAD",
        }[permission]
    subject = await subject_authority.subject_authority(
        actor_user_id=actor.id,
        actor_context=context,
        enrollment_id=UUID(str(row["enrollment_id"])),
        service_case_id=UUID(str(row["service_case_id"])),
    )
    if (
        subject is None
        or UUID(str(subject["subject_member_id"])) != UUID(str(row["subject_member_id"]))
    ):
        raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
    return {
        "actor_context": context,
        "subject_member_id": UUID(str(row["subject_member_id"])),
        "service_case_id": UUID(str(row["service_case_id"])),
        "enrollment_id": UUID(str(row["enrollment_id"])),
    }


def _proxy_path(enrollment_id: UUID = Path()) -> UUID:
    return enrollment_id


def _family_routes(prefix: str):
    router = family_health_router

    async def get_profile(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
        identity=Depends(get_slice4_identity_authority_session),
    ) -> HealthProfileDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority,
            enrollment_reader,
            Slice4HealthRecordRepository(clinical),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_VIEW",
        )
        return await read_formal_health_profile(
            Slice4HealthRecordRepository(clinical),
            Slice4IdentitySummaryAuthority(identity),
            actor_user_id=current_user.id,
            **scope,
        )

    async def put_profile(
        request: Request,
        payload: FormalHealthProfileSnapshotRequest,
        idempotency_key: IdempotencyKey,
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        writer=Depends(get_health_record_writer_session),
        clinical=Depends(get_slice4_clinical_reader_session),
        identity=Depends(get_slice4_identity_authority_session),
    ) -> HealthProfileDTO:
        try:
            enrollment_id = request.path_params.get("enrollment_id")
            scope = await _family_health_scope(
                authority,
                enrollment_reader,
                Slice4HealthRecordRepository(writer),
                current_user,
                enrollment_id=UUID(enrollment_id) if enrollment_id else None,
                permission="DAILY_INPUT",
            )
            repository = Slice4HealthRecordRepository(writer)
            await create_formal_profile_root(
                repository,
                Slice4IdentitySummaryAuthority(identity),
                actor_user_id=current_user.id,
                payload=payload,
                idempotency_key=idempotency_key,
                **scope,
            )
            await writer.commit()
            return await read_formal_health_profile(
                Slice4HealthRecordRepository(clinical),
                Slice4IdentitySummaryAuthority(identity),
                actor_user_id=current_user.id,
                **scope,
            )
        except Exception:
            await writer.rollback()
            raise

    async def create_report(
        request: Request,
        payload: FormalDetectionReportCreateRequest,
        idempotency_key: IdempotencyKey,
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        writer=Depends(get_health_record_writer_session),
        clinical=Depends(get_slice4_clinical_reader_session),
    ) -> DetectionReportDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority,
            enrollment_reader,
            Slice4HealthRecordRepository(writer),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="REPORT_UPLOAD",
        )
        try:
            created = await create_formal_detection_report(
                Slice4HealthRecordRepository(writer),
                actor_user_id=current_user.id,
                payload=payload,
                idempotency_key=idempotency_key,
                **scope,
            )
            await writer.commit()
        except Exception:
            await writer.rollback()
            raise
        detail = await read_formal_detection_report(
            Slice4HealthRecordRepository(clinical),
            actor_user_id=current_user.id,
            report_id=UUID(str(created["report_id"])),
            **scope,
        )
        return DetectionReportDTO(**detail.model_dump(exclude={"attachments"}))

    async def list_reports(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        cursor: str | None = Query(None, max_length=512),
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
    ) -> DetectionReportPageDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority,
            enrollment_reader,
            Slice4HealthRecordRepository(clinical),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_VIEW",
        )
        return await read_formal_detection_reports(
            Slice4HealthRecordRepository(clinical),
            actor_user_id=current_user.id,
            limit=limit,
            cursor=cursor,
            **scope,
        )

    async def report_detail(
        request: Request,
        report_id: UUID,
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
    ) -> DetectionReportDetailDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority,
            enrollment_reader,
            Slice4HealthRecordRepository(clinical),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_VIEW",
        )
        return await read_formal_detection_report(
            Slice4HealthRecordRepository(clinical),
            actor_user_id=current_user.id,
            report_id=report_id,
            **scope,
        )

    async def create_facts(
        request: Request,
        payload: HealthFactBatchRequest,
        idempotency_key: IdempotencyKey,
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
        writer=Depends(get_health_fact_writer_session),
    ) -> HealthFactBatchDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority,
            enrollment_reader,
            Slice4HealthRecordRepository(clinical),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_INPUT",
        )
        subject = await Slice4HealthRecordRepository(clinical).subject_authority(
            actor_user_id=current_user.id,
            actor_context=scope["actor_context"],
            enrollment_id=scope["enrollment_id"],
            service_case_id=scope["service_case_id"],
        )
        if subject is None:
            raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
        try:
            result = await create_formal_health_facts(
                Slice4HealthRecordRepository(writer),
                actor_user_id=current_user.id,
                payload=payload,
                idempotency_key=idempotency_key,
                subject_user_id=subject["subject_user_id"],
                **scope,
            )
            await writer.commit()
            return result
        except Exception:
            await writer.rollback()
            raise

    async def correct_fact(
        request: Request,
        fact_ref: UUID,
        payload: HealthFactCorrectionRequest,
        idempotency_key: IdempotencyKey,
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
        writer=Depends(get_health_fact_writer_session),
    ) -> HealthFactDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        clinical_repository = Slice4HealthRecordRepository(clinical)
        scope = await _family_health_scope(
            authority,
            enrollment_reader,
            clinical_repository,
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_INPUT",
        )
        subject = await clinical_repository.subject_authority(
            actor_user_id=current_user.id,
            actor_context=scope["actor_context"],
            enrollment_id=scope["enrollment_id"],
            service_case_id=scope["service_case_id"],
        )
        fact_row, _ = await read_formal_health_fact(
            clinical_repository,
            actor_user_id=current_user.id,
            fact_ref=fact_ref,
            **scope,
        )
        if subject is None:
            raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
        try:
            result = await correct_formal_health_fact(
                Slice4HealthRecordRepository(writer),
                actor_user_id=current_user.id,
                subject_user_id=subject["subject_user_id"],
                fact_row=fact_row,
                payload=payload,
                idempotency_key=idempotency_key,
                **scope,
            )
            await writer.commit()
            return result
        except Exception:
            await writer.rollback()
            raise

    async def history(
        request: Request, limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None, max_length=512), indicator_code: str | None = None,
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
    ) -> HealthIndicatorPageDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority, enrollment_reader, Slice4HealthRecordRepository(clinical),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_VIEW",
        )
        indicators = _requested_indicators((indicator_code,) if indicator_code else ())
        rows = await _projection_rows(
            subject_member_id=scope["subject_member_id"], indicator_codes=indicators,
            limit=limit, cursor=cursor,
        )
        return _history_dto(rows, limit)

    async def latest(
        request: Request, indicator_codes: list[str] = Query(default=[]),
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
    ) -> HealthIndicatorLatestDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority, enrollment_reader, Slice4HealthRecordRepository(clinical),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_VIEW",
        )
        indicators = _requested_indicators(indicator_codes)
        rows = await _projection_rows(
            subject_member_id=scope["subject_member_id"], indicator_codes=indicators,
            limit=199,
        )
        return _latest_dto(rows, indicators)

    async def trends(
        request: Request, indicator_code: str, limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None, max_length=512),
        current_user: CurrentUser = Depends(get_current_user_from_jwt),
        authority=Depends(get_db_session),
        enrollment_reader=Depends(get_member_enrollment_reader_session),
        clinical=Depends(get_slice4_clinical_reader_session),
    ) -> HealthIndicatorTrendDTO:
        enrollment_id = request.path_params.get("enrollment_id")
        scope = await _family_health_scope(
            authority, enrollment_reader, Slice4HealthRecordRepository(clinical),
            current_user,
            enrollment_id=UUID(enrollment_id) if enrollment_id else None,
            permission="DAILY_VIEW",
        )
        indicators = _requested_indicators((indicator_code,))
        rows = await _projection_rows(
            subject_member_id=scope["subject_member_id"], indicator_codes=indicators,
            limit=limit, cursor=cursor,
        )
        return _trend_dto(rows, indicator_code, limit)

    dependencies = [Depends(_proxy_path)] if prefix else None
    router.add_api_route(f"{prefix}/health-profile", get_profile, methods=["GET"], response_model=HealthProfileDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/health-profile", put_profile, methods=["PUT"], response_model=HealthProfileDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/detection-reports", create_report, methods=["POST"], status_code=201, response_model=DetectionReportDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/detection-reports", list_reports, methods=["GET"], response_model=DetectionReportPageDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/detection-reports/{{report_id}}", report_detail, methods=["GET"], response_model=DetectionReportDetailDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/health-indicators", create_facts, methods=["POST"], status_code=201, response_model=HealthFactBatchDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/health-facts/{{fact_ref}}/corrections", correct_fact, methods=["POST"], status_code=201, response_model=HealthFactDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/health-indicators/history", history, methods=["GET"], response_model=HealthIndicatorPageDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/health-indicators/latest", latest, methods=["GET"], response_model=HealthIndicatorLatestDTO, dependencies=dependencies)
    router.add_api_route(f"{prefix}/health-indicators/trends", trends, methods=["GET"], response_model=HealthIndicatorTrendDTO, dependencies=dependencies)


_family_routes("")
_family_routes("/proxy-enrollments/{enrollment_id}")


async def _therapist_transition(
    case_id,
    fact_ref,
    payload,
    current_user,
    clinical,
    writer,
    target_state,
) -> HealthFactDTO:
    clinical_repository = Slice4HealthRecordRepository(clinical)
    subject = await clinical_repository.subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    fact_row, _ = await read_formal_health_fact(
        clinical_repository,
        actor_user_id=current_user.id,
        actor_context="THERAPIST",
        subject_member_id=UUID(str(subject["subject_member_id"])),
        service_case_id=case_id,
        enrollment_id=None,
        fact_ref=fact_ref,
    )
    try:
        result = await transition_formal_health_fact_state(
            Slice4HealthRecordRepository(writer),
            actor_user_id=current_user.id,
            service_case_id=case_id,
            fact_row=fact_row,
            target_state=target_state,
            expected_version=payload.expected_version,
            reason_code=payload.reason_code,
        )
        await writer.commit()
        return result
    except Exception:
        await writer.rollback()
        raise


@therapist_health_router.get("/service-cases/{case_id}/health-profile", response_model=HealthProfileDTO)
async def therapist_profile(
    case_id: UUID,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
    identity=Depends(get_slice4_identity_authority_session),
) -> HealthProfileDTO:
    repository = Slice4HealthRecordRepository(clinical)
    subject = await repository.subject_authority(
        actor_user_id=current_user.id,
        actor_context="THERAPIST",
        service_case_id=case_id,
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    return await read_formal_health_profile(
        repository,
        Slice4IdentitySummaryAuthority(identity),
        actor_user_id=current_user.id,
        actor_context="THERAPIST",
        subject_member_id=UUID(str(subject["subject_member_id"])),
        service_case_id=case_id,
        enrollment_id=None,
    )


@therapist_health_router.get("/service-cases/{case_id}/detection-reports", response_model=DetectionReportPageDTO)
async def therapist_reports(
    case_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
) -> DetectionReportPageDTO:
    repository = Slice4HealthRecordRepository(clinical)
    subject = await repository.subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    return await read_formal_detection_reports(
        repository,
        actor_user_id=current_user.id,
        actor_context="THERAPIST",
        subject_member_id=UUID(str(subject["subject_member_id"])),
        service_case_id=case_id,
        enrollment_id=None,
        limit=limit,
        cursor=cursor,
    )


@therapist_health_router.get("/service-cases/{case_id}/detection-reports/{report_id}", response_model=DetectionReportDetailDTO)
async def therapist_report(
    case_id: UUID,
    report_id: UUID,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
) -> DetectionReportDetailDTO:
    repository = Slice4HealthRecordRepository(clinical)
    subject = await repository.subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    return await read_formal_detection_report(
        repository,
        actor_user_id=current_user.id,
        actor_context="THERAPIST",
        subject_member_id=UUID(str(subject["subject_member_id"])),
        service_case_id=case_id,
        enrollment_id=None,
        report_id=report_id,
    )


@therapist_health_router.post("/service-cases/{case_id}/health-indicators", status_code=201, response_model=HealthFactBatchDTO)
async def therapist_create_facts(
    case_id: UUID,
    payload: HealthFactBatchRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
    writer=Depends(get_health_fact_writer_session),
) -> HealthFactBatchDTO:
    clinical_repository = Slice4HealthRecordRepository(clinical)
    subject = await clinical_repository.subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    for item in payload.items:
        if item.source_type == "REPORT":
            await read_formal_detection_report(
                clinical_repository,
                actor_user_id=current_user.id,
                actor_context="THERAPIST",
                subject_member_id=UUID(str(subject["subject_member_id"])),
                service_case_id=case_id,
                enrollment_id=None,
                report_id=item.report_id,
            )
    try:
        result = await create_formal_health_facts(
            Slice4HealthRecordRepository(writer),
            actor_user_id=current_user.id,
            actor_context="THERAPIST",
            subject_member_id=UUID(str(subject["subject_member_id"])),
            subject_user_id=subject["subject_user_id"],
            service_case_id=case_id,
            enrollment_id=UUID(int=0),
            payload=payload,
            idempotency_key=idempotency_key,
        )
        await writer.commit()
        return result
    except Exception:
        await writer.rollback()
        raise


@therapist_health_router.post("/service-cases/{case_id}/health-facts/{fact_ref}/verify", response_model=HealthFactDTO)
async def therapist_verify_fact(
    case_id: UUID,
    fact_ref: UUID,
    payload: HealthFactStateRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
    writer=Depends(get_health_fact_writer_session),
) -> HealthFactDTO:
    return await _therapist_transition(
        case_id, fact_ref, payload, current_user, clinical, writer, "VERIFIED"
    )


@therapist_health_router.post("/service-cases/{case_id}/health-facts/{fact_ref}/dispute", response_model=HealthFactDTO)
async def therapist_dispute_fact(
    case_id: UUID,
    fact_ref: UUID,
    payload: HealthFactStateRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
    writer=Depends(get_health_fact_writer_session),
) -> HealthFactDTO:
    return await _therapist_transition(
        case_id, fact_ref, payload, current_user, clinical, writer, "DISPUTED"
    )


@therapist_health_router.post("/service-cases/{case_id}/health-facts/{fact_ref}/corrections", status_code=201, response_model=HealthFactDTO)
async def therapist_correct_fact(
    case_id: UUID,
    fact_ref: UUID,
    payload: HealthFactCorrectionRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
    writer=Depends(get_health_fact_writer_session),
) -> HealthFactDTO:
    clinical_repository = Slice4HealthRecordRepository(clinical)
    subject = await clinical_repository.subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    fact_row, _ = await read_formal_health_fact(
        clinical_repository,
        actor_user_id=current_user.id,
        actor_context="THERAPIST",
        subject_member_id=UUID(str(subject["subject_member_id"])),
        service_case_id=case_id,
        enrollment_id=None,
        fact_ref=fact_ref,
    )
    try:
        result = await correct_formal_health_fact(
            Slice4HealthRecordRepository(writer),
            actor_user_id=current_user.id,
            actor_context="THERAPIST",
            subject_member_id=UUID(str(subject["subject_member_id"])),
            subject_user_id=subject["subject_user_id"],
            service_case_id=case_id,
            enrollment_id=None,
            fact_row=fact_row,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        await writer.commit()
        return result
    except Exception:
        await writer.rollback()
        raise


@therapist_health_router.get("/service-cases/{case_id}/health-indicators/history", response_model=HealthIndicatorPageDTO)
async def therapist_history(
    case_id: UUID, limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512), indicator_code: str | None = None,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
) -> HealthIndicatorPageDTO:
    subject = await Slice4HealthRecordRepository(clinical).subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    indicators = _requested_indicators((indicator_code,) if indicator_code else ())
    rows = await _projection_rows(
        subject_member_id=UUID(str(subject["subject_member_id"])),
        indicator_codes=indicators, limit=limit, cursor=cursor,
    )
    return _history_dto(rows, limit)


@therapist_health_router.get("/service-cases/{case_id}/health-indicators/latest", response_model=HealthIndicatorLatestDTO)
async def therapist_latest(
    case_id: UUID, indicator_codes: list[str] = Query(default=[]),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
) -> HealthIndicatorLatestDTO:
    subject = await Slice4HealthRecordRepository(clinical).subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    indicators = _requested_indicators(indicator_codes)
    rows = await _projection_rows(
        subject_member_id=UUID(str(subject["subject_member_id"])),
        indicator_codes=indicators, limit=199,
    )
    return _latest_dto(rows, indicators)


@therapist_health_router.get("/service-cases/{case_id}/health-indicators/trends", response_model=HealthIndicatorTrendDTO)
async def therapist_trends(
    case_id: UUID, indicator_code: str, limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    clinical=Depends(get_slice4_clinical_reader_session),
) -> HealthIndicatorTrendDTO:
    subject = await Slice4HealthRecordRepository(clinical).subject_authority(
        actor_user_id=current_user.id, actor_context="THERAPIST", service_case_id=case_id
    )
    if subject is None:
        raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
    indicators = _requested_indicators((indicator_code,))
    rows = await _projection_rows(
        subject_member_id=UUID(str(subject["subject_member_id"])),
        indicator_codes=indicators, limit=limit, cursor=cursor,
    )
    return _trend_dto(rows, indicator_code, limit)


async def _require_service_case(enrollment_reader, case_id: UUID) -> None:
    rows = await MemberEnrollmentRepository(enrollment_reader).safe_view_rows(
        "slice3_service_case_read_v1",
        predicates={"case_id": case_id},
        order="case_id",
        limit=2,
    )
    if len(rows) != 1:
        raise _formal_error("SERVICE_CASE_NOT_FOUND")


async def _institution_health_rows(
    institution,
    *,
    actor_user_id: int,
    service_case_id: UUID,
    resource: str,
    page: dict[str, object],
):
    try:
        return await Slice4HealthRecordRepository(institution).institution_health(
            actor_user_id=actor_user_id,
            service_case_id=service_case_id,
            resource=resource,
            page=page,
        )
    except Exception as exc:
        if "SLICE4_INSTITUTION_SCOPE_FORBIDDEN" in str(exc):
            raise _formal_error("INSTITUTION_SCOPE_FORBIDDEN") from None
        raise


def _require_institution_actor(current_user: CurrentUser) -> None:
    if current_user.role not in {"org_admin", "org_operator"}:
        raise _formal_error("INSTITUTION_SCOPE_FORBIDDEN")


@institution_health_router.get("/service-cases/{case_id}/health-record", response_model=InstitutionHealthRecordDTO)
async def institution_health_record(
    case_id: UUID,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    enrollment_reader=Depends(get_member_enrollment_reader_session),
    institution=Depends(get_slice4_institution_reader_session),
) -> InstitutionHealthRecordDTO:
    _require_institution_actor(current_user)
    await _require_service_case(enrollment_reader, case_id)
    rows = await _institution_health_rows(
        institution,
        actor_user_id=current_user.id,
        service_case_id=case_id,
        resource="HEALTH_RECORD",
        page={},
    )
    if len(rows) != 1:
        raise _formal_error("SERVICE_CASE_NOT_FOUND")
    row = rows[0]
    return InstitutionHealthRecordDTO(
        case_id=UUID(str(row["case_id"])),
        profile_completion_status="COMPLETE" if row["profile_complete"] else "INCOMPLETE",
        missing_section_codes=() if row["profile_complete"] else ("HEALTH_PROFILE",),
        indicator_codes=tuple(row["indicator_codes"]),
        indicator_states=dict(row["indicator_states"]),
        report_metadata_count=row["report_count"],
        readiness_status=row["readiness_status"],
        updated_at=row["profile_updated_at"],
    )


@institution_health_router.get("/service-cases/{case_id}/detection-reports", response_model=DetectionReportPageDTO)
async def institution_reports(
    case_id: UUID, limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=512),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    enrollment_reader=Depends(get_member_enrollment_reader_session),
    institution=Depends(get_slice4_institution_reader_session),
) -> DetectionReportPageDTO:
    _require_institution_actor(current_user)
    await _require_service_case(enrollment_reader, case_id)
    page: dict[str, object] = {"limit": limit + 1}
    marker = _report_cursor(cursor)
    if marker is not None:
        page.update(
            cursor_measured_at=marker[0].isoformat(),
            cursor_report_id=str(marker[1]),
        )
    rows = await _institution_health_rows(
        institution,
        actor_user_id=current_user.id,
        service_case_id=case_id,
        resource="DETECTION_REPORTS",
        page=page,
    )
    items = [_institution_report_dto(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit and items:
        next_cursor = _report_cursor_text(items[-1].measured_at, items[-1].report_id)
    return DetectionReportPageDTO(items=items, next_cursor=next_cursor)


@institution_health_router.get("/service-cases/{case_id}/health-indicators/latest", response_model=HealthIndicatorLatestDTO)
async def institution_latest(
    case_id: UUID, indicator_codes: list[str] = Query(default=[]),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    enrollment_reader=Depends(get_member_enrollment_reader_session),
    institution=Depends(get_slice4_institution_reader_session),
) -> HealthIndicatorLatestDTO:
    _require_institution_actor(current_user)
    await _require_service_case(enrollment_reader, case_id)
    rows = await _institution_health_rows(
        institution,
        actor_user_id=current_user.id,
        service_case_id=case_id,
        resource="HEALTH_RECORD",
        page={},
    )
    if len(rows) != 1:
        raise _formal_error("SERVICE_CASE_NOT_FOUND")
    indicators = _requested_indicators(indicator_codes)
    facts = await _projection_rows(
        subject_member_id=UUID(str(rows[0]["subject_ref"])),
        indicator_codes=indicators, limit=199,
    )
    return _latest_dto(facts, indicators)


@readiness_router.get("/service-cases/{case_id}/assessment-readiness", response_model=AssessmentReadinessDTO)
async def assessment_readiness(
    case_id: UUID,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    authority=Depends(get_db_session),
    enrollment_reader=Depends(get_member_enrollment_reader_session),
    clinical=Depends(get_slice4_clinical_reader_session),
    institution=Depends(get_slice4_institution_reader_session),
) -> AssessmentReadinessDTO:
    if current_user.role not in {"member", "therapist", "org_admin", "org_operator"}:
        raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
    await _require_service_case(enrollment_reader, case_id)
    if current_user.role == "member":
        actor_member_id = await _family_member(
            authority, Slice4HealthRecordRepository(clinical), current_user
        )
        rows = await MemberEnrollmentRepository(enrollment_reader).safe_view_rows(
            "slice3_family_enrollment_read_v1",
            predicates={"service_case_id": case_id}, order="enrollment_id", limit=2,
        )
        if len(rows) != 1:
            raise _formal_error("SERVICE_CASE_NOT_FOUND")
        row = rows[0]
        proxy = row["proxy"]
        allowed = UUID(str(row["subject_member_id"])) == actor_member_id
        if not allowed and proxy is not None:
            allowed = (
                UUID(str(proxy["proxy_member_id"])) == actor_member_id
                and "DAILY_VIEW" in proxy["permission_codes"]
            )
        if not allowed:
            raise _formal_error("PROXY_PERMISSION_FORBIDDEN")
        read_session = clinical
    elif current_user.role == "therapist":
        subject = await Slice4HealthRecordRepository(clinical).subject_authority(
            actor_user_id=current_user.id, actor_context="THERAPIST",
            service_case_id=case_id,
        )
        if subject is None:
            raise _formal_error("THERAPIST_SCOPE_FORBIDDEN")
        read_session = clinical
    elif current_user.role in {"org_admin", "org_operator"}:
        rows = await _institution_health_rows(
            institution,
            actor_user_id=current_user.id,
            service_case_id=case_id,
            resource="HEALTH_RECORD",
            page={},
        )
        if len(rows) != 1:
            raise _formal_error("INSTITUTION_SCOPE_FORBIDDEN")
        read_session = institution
    else:
        raise _formal_error("ACTOR_CURRENTNESS_FORBIDDEN")
    value = await read_current_readiness(
        AssessmentReadinessRepository(read_session), service_case_id=case_id
    )
    return AssessmentReadinessDTO.model_validate(value)
