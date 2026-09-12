# ruff: noqa: B008 -- FastAPI dependency declarations are evaluated at route registration.
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.database import (
    get_institution_onboarding_reader_session,
    get_institution_onboarding_writer_session,
    get_institution_review_writer_session,
    get_slice1_session_factory,
)
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.uuid_generator import Uuid7Generator
from app.core.接口合同 import error_response

from .repository import (
    DirectInstitutionOnboardingRepository,
    DirectInstitutionOnboardingRepositoryError,
)
from .schemas import (
    AdminHandoffActivationDTO,
    AdminHandoffActivationRequest,
    AdminHandoffCreateRequest,
    AdminHandoffCredentialDTO,
    ComplianceDecisionRequest,
    DirectActivationRequest,
    DirectComplianceDTO,
    DirectComplianceRequest,
    DirectCreateRequest,
    DirectCredentialDTO,
    DirectOnboardingDTO,
    DirectOnboardingPageDTO,
    UuidV7,
    VersionedStepUpRequest,
)
from .service import (
    SAFE_UNAVAILABLE,
    DirectInstitutionCrypto,
    DirectOnboardingSecrets,
    accepted_totp_step,
    account_phone_claim_digest,
    admin_handoff_activation_request_digest,
    admin_handoff_activation_request_digest_candidates,
    admin_handoff_create_request_digest,
    admin_handoff_create_request_digest_candidates,
    admin_handoff_regenerate_request_digest,
    admin_handoff_regenerate_request_digest_candidates,
    build_admin_handoff_activation_mutation,
    build_admin_handoff_create_mutation,
    build_admin_handoff_regenerate_mutation,
    build_compliance_decision_mutation,
    build_compliance_mutation,
    build_direct_activation_mutation,
    build_direct_create_mutation,
    build_direct_regenerate_mutation,
    build_direct_revoke_mutation,
    credential_digest_candidates,
    decode_direct_onboarding_cursor,
    direct_activation_request_digest,
    direct_activation_request_digest_candidates,
    direct_compliance_decision_request_digest,
    direct_compliance_decision_request_digest_candidates,
    direct_create_request_digest,
    direct_create_request_digest_candidates,
    direct_regenerate_request_digest,
    direct_regenerate_request_digest_candidates,
    direct_revoke_request_digest,
    direct_revoke_request_digest_candidates,
    encode_direct_onboarding_cursor,
    license_no_aad,
    open_totp_secret,
    platform_admin_totp_aad,
)

_STATUS = {
    "UNAUTHENTICATED": 401,
    "ROLE_FORBIDDEN": 403,
    "STEP_UP_FORBIDDEN": 403,
    "DIRECT_ONBOARDING_NOT_FOUND": 404,
    "STALE_VERSION": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "ADMIN_PHONE_OCCUPIED": 409,
    "DIRECT_ONBOARDING_STATE_CONFLICT": 409,
    "ONE_TIME_CREDENTIAL_ALREADY_ISSUED": 409,
    "INVALID_REQUEST": 422,
    "RATE_LIMITED": 429,
    "DEPENDENCY_UNAVAILABLE": 503,
    "COMMIT_OUTCOME_UNKNOWN": 503,
}
_COMMON = ("UNAUTHENTICATED", "INVALID_REQUEST", "DEPENDENCY_UNAVAILABLE")


def _codes(*extra: str) -> tuple[str, ...]:
    return _COMMON + extra


DIRECT_ONBOARDING_ROUTE_ERROR_CODES = {
    ("POST", "/api/v1/platform/direct-institution-onboardings"): _codes(
        "ROLE_FORBIDDEN", "STEP_UP_FORBIDDEN", "ADMIN_PHONE_OCCUPIED",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
        "ONE_TIME_CREDENTIAL_ALREADY_ISSUED",
    ),
    ("GET", "/api/v1/platform/direct-institution-onboardings"): _codes(
        "ROLE_FORBIDDEN",
    ),
    ("GET", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}"): _codes(
        "ROLE_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND",
    ),
    ("POST", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/activation-credential:regenerate"): _codes(
        "ROLE_FORBIDDEN", "STEP_UP_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND",
        "STALE_VERSION", "DIRECT_ONBOARDING_STATE_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
        "ONE_TIME_CREDENTIAL_ALREADY_ISSUED",
    ),
    ("POST", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}:revoke"): _codes(
        "ROLE_FORBIDDEN", "STEP_UP_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND",
        "STALE_VERSION", "DIRECT_ONBOARDING_STATE_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
    ),
    ("POST", "/api/v1/institution-onboarding/direct-activate"): _codes(
        "STALE_VERSION", "ADMIN_PHONE_OCCUPIED", "DIRECT_ONBOARDING_STATE_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
    ),
    ("GET", "/api/v1/institution-onboarding/direct-compliance"): _codes(
        "ROLE_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND",
    ),
    ("PUT", "/api/v1/institution-onboarding/direct-compliance"): _codes(
        "ROLE_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND", "STALE_VERSION",
        "DIRECT_ONBOARDING_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT",
        "COMMIT_OUTCOME_UNKNOWN",
    ),
    ("POST", "/api/v1/institution-onboarding/direct-compliance:submit"): _codes(
        "ROLE_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND", "STALE_VERSION",
        "DIRECT_ONBOARDING_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT",
        "COMMIT_OUTCOME_UNKNOWN",
    ),
    ("POST", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/compliance-decision"): _codes(
        "ROLE_FORBIDDEN", "STEP_UP_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND",
        "STALE_VERSION", "DIRECT_ONBOARDING_STATE_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
    ),
    ("POST", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs"): _codes(
        "ROLE_FORBIDDEN", "STEP_UP_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND",
        "STALE_VERSION", "ADMIN_PHONE_OCCUPIED", "DIRECT_ONBOARDING_STATE_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
        "ONE_TIME_CREDENTIAL_ALREADY_ISSUED",
    ),
    ("POST", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs/{handoff_id}:regenerate"): _codes(
        "ROLE_FORBIDDEN", "STEP_UP_FORBIDDEN", "DIRECT_ONBOARDING_NOT_FOUND",
        "STALE_VERSION", "DIRECT_ONBOARDING_STATE_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
        "ONE_TIME_CREDENTIAL_ALREADY_ISSUED",
    ),
    ("POST", "/api/v1/institution-onboarding/admin-handoffs/activate"): _codes(
        "STALE_VERSION", "ADMIN_PHONE_OCCUPIED", "DIRECT_ONBOARDING_STATE_CONFLICT",
        "IDEMPOTENCY_CONFLICT", "COMMIT_OUTCOME_UNKNOWN", "RATE_LIMITED",
    ),
}


class DirectOnboardingError(ValueError):
    pass


class DirectOnboardingRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            key = (next(iter(self.methods)), self.path)
            try:
                return await original(request)
            except RequestValidationError:
                return error_response(request, 422, "INVALID_REQUEST")
            except HTTPException as exc:
                code = "UNAUTHENTICATED" if exc.status_code == 401 else (
                    exc.detail if type(exc.detail) is str else None
                )
                if code not in DIRECT_ONBOARDING_ROUTE_ERROR_CODES[key]:
                    code = "INVALID_REQUEST" if exc.status_code < 500 else "DEPENDENCY_UNAVAILABLE"
                status = _STATUS[code]
                return error_response(
                    request,
                    status,
                    code,
                    retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                    headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
                )
            except (DirectOnboardingError, DirectInstitutionOnboardingRepositoryError) as exc:
                code = exc.args[0] if len(exc.args) == 1 else None
                if code not in DIRECT_ONBOARDING_ROUTE_ERROR_CODES[key]:
                    return error_response(request, 500, "INTERNAL_ERROR")
                status = _STATUS[code]
                return error_response(
                    request,
                    status,
                    code,
                    retryable=status == 503 and code != "COMMIT_OUTCOME_UNKNOWN",
                )
            except Exception:
                return error_response(
                    request,
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    retryable=True,
                )

        return handler


def _router(prefix: str, tag: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])
    router.route_class = DirectOnboardingRoute
    return router


platform_router = _router("/api/v1/platform", "direct-institution-platform")
onboarding_router = _router(
    "/api/v1/institution-onboarding", "direct-institution-onboarding"
)
routers = (platform_router, onboarding_router)

IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
]
OpaqueCursor = Annotated[str | None, Query(min_length=16, max_length=512)]
_NO_STORE = {"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}}


def strip_direct_onboarding_validation_responses(
    schema: dict[str, Any],
) -> dict[str, Any]:
    for (method, path), codes in DIRECT_ONBOARDING_ROUTE_ERROR_CODES.items():
        operation = schema["paths"][path][method.lower()]
        operation["x-symbolic-error-codes"] = list(codes)
        operation.get("responses", {}).pop("422", None)
        operation["responses"]["422"] = {
            "description": "Request rejected",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponseDTO"}
                }
            },
        }
    return schema


async def _materialize_reader_rows(reader_session, rows) -> list[dict[str, Any]]:
    materialized = [dict(row) for row in rows]
    await reader_session.rollback()
    return materialized


async def _commit_compliance(
    session,
    *,
    operation: str,
    confirmation: dict[str, Any],
) -> None:
    try:
        await session.commit()
        return
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        with suppress(Exception):
            await session.rollback()
    try:
        factory = get_slice1_session_factory("onboarding_writer")
        async with factory() as confirmation_session:
            repository = DirectInstitutionOnboardingRepository(confirmation_session)
            if operation == "COMPLIANCE_SAVE":
                result = await repository.compliance_save_commit_confirm(confirmation)
            else:
                result = await repository.compliance_submit_commit_confirm(confirmation)
    except Exception:
        raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN") from None
    outcome = None if result is None else result.get("outcome")
    if outcome == "COMMITTED":
        return
    if outcome == "NOT_COMMITTED":
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE")
    raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN")


async def _commit_review(
    session,
    *,
    confirmation: dict[str, Any],
    credential_delivery: bool,
) -> None:
    try:
        await session.commit()
        return
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        with suppress(Exception):
            await session.rollback()
    try:
        factory = get_slice1_session_factory("review_writer")
        async with factory() as confirmation_session:
            result = await DirectInstitutionOnboardingRepository(
                confirmation_session
            ).review_commit_confirm(confirmation)
    except Exception:
        raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN") from None
    outcome = None if result is None else result.get("outcome")
    if outcome == "COMMITTED":
        if credential_delivery:
            raise DirectOnboardingError("ONE_TIME_CREDENTIAL_ALREADY_ISSUED")
        return
    if outcome == "NOT_COMMITTED":
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE")
    raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN")


async def _commit_activation(session, *, confirmation: dict[str, Any]) -> None:
    try:
        await session.commit()
        return
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        with suppress(Exception):
            await session.rollback()
    try:
        factory = get_slice1_session_factory("onboarding_writer")
        async with factory() as confirmation_session:
            result = await DirectInstitutionOnboardingRepository(
                confirmation_session
            ).activation_commit_confirm(confirmation)
    except Exception:
        raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN") from None
    outcome = None if result is None else result.get("outcome")
    if outcome == "COMMITTED":
        return
    if outcome == "NOT_COMMITTED":
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE")
    raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN")


async def _commit_handoff_activation(
    session, *, confirmation: dict[str, Any]
) -> None:
    try:
        await session.commit()
        return
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        with suppress(Exception):
            await session.rollback()
    try:
        factory = get_slice1_session_factory("onboarding_writer")
        async with factory() as confirmation_session:
            result = await DirectInstitutionOnboardingRepository(
                confirmation_session
            ).handoff_activation_commit_confirm(confirmation)
    except Exception:
        raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN") from None
    outcome = None if result is None else result.get("outcome")
    if outcome == "COMMITTED":
        return
    if outcome == "NOT_COMMITTED":
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE")
    raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN")


async def _commit_step_up_failure(
    session,
    *,
    failure_id,
    actor_user_id: int,
    operation_id,
    request_digest_value: str,
) -> None:
    try:
        await session.commit()
        return
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        with suppress(Exception):
            await session.rollback()
    try:
        factory = get_slice1_session_factory("review_writer")
        async with factory() as confirmation_session:
            result = await DirectInstitutionOnboardingRepository(
                confirmation_session
            ).step_up_failure_commit_confirm(
                failure_id=failure_id,
                actor_user_id=actor_user_id,
                operation_id=operation_id,
                request_digest=request_digest_value,
            )
    except Exception:
        raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN") from None
    outcome = None if result is None else result.get("outcome")
    if outcome == "COMMITTED":
        return
    if outcome == "NOT_COMMITTED":
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE")
    raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN")


async def _mutate_compliance(
    *,
    operation: str,
    payload: DirectComplianceRequest,
    idempotency_key: str,
    current_user: CurrentUser,
    reader_session,
    writer_session,
) -> dict[str, Any]:
    _require_role(current_user, "org_admin")
    if current_user.tenant_id is None:
        raise DirectOnboardingError("ROLE_FORBIDDEN")
    reader = DirectInstitutionOnboardingRepository(reader_session)
    rows = await reader.read_current_compliance(actor_user_id=current_user.id)
    rows = await _materialize_reader_rows(reader_session, rows)
    if not rows:
        raise DirectOnboardingError("DIRECT_ONBOARDING_NOT_FOUND")
    try:
        envelope, confirmation = build_compliance_mutation(
            operation=operation,
            request=payload,
            current=rows[0],
            stored_licenses=rows,
            actor_user_id=current_user.id,
            actor_tenant_id=current_user.tenant_id,
            idempotency_key=idempotency_key,
            id_factory=Uuid7Generator().generate,
        )
    except ValueError as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {
            "INVALID_REQUEST",
            "STALE_VERSION",
            "DIRECT_ONBOARDING_STATE_CONFLICT",
        }:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    except RuntimeError as error:
        if error.args == (SAFE_UNAVAILABLE,):
            raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
        raise
    writer = DirectInstitutionOnboardingRepository(writer_session)
    replay_values = {
        "actor_user_id": current_user.id,
        "actor_scope": envelope["actor_scope"],
        "idempotency_key": idempotency_key,
        "request_digest": envelope["request_digest"],
        "request_digest_candidates": envelope["request_digest_candidates"],
    }
    if operation == "COMPLIANCE_SAVE":
        replay = await writer.compliance_save_replay(**replay_values)
        if replay is None:
            await writer.save_compliance(
                {key: value for key, value in envelope.items() if key != "request_digest_candidates"}
            )
    else:
        replay = await writer.compliance_submit_replay(**replay_values)
        if replay is None:
            await writer.submit_compliance(
                {key: value for key, value in envelope.items() if key != "request_digest_candidates"}
            )
    if replay is None:
        await _commit_compliance(
            writer_session,
            operation=operation,
            confirmation=confirmation,
        )
    current_rows = await reader.read_current_compliance(actor_user_id=current_user.id)
    return _compliance_projection(current_rows)


def _require_role(current_user: CurrentUser, role: str) -> None:
    if current_user.role != role:
        raise DirectOnboardingError("ROLE_FORBIDDEN")


def _public_onboarding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"snapshot_ceiling", "has_more"}
    }


def _direct_crypto() -> DirectInstitutionCrypto:
    secrets = DirectOnboardingSecrets()
    return DirectInstitutionCrypto(
        pii_current_key_id=secrets.pii_key_id,
        pii_keys=secrets.pii_keys,
        digest_current_key_id=secrets.digest_key_id,
        digest_keys=secrets.digest_keys,
    )


def _compliance_projection(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise DirectOnboardingError("DIRECT_ONBOARDING_NOT_FOUND")
    first = rows[0]
    revision_id = first["revision_id"]
    licenses: list[dict[str, Any]] = []
    crypto: DirectInstitutionCrypto | None = None
    for row in rows:
        if row.get("license_id") is None:
            continue
        license_number = None
        if row.get("license_no_ciphertext") is not None:
            crypto = crypto or _direct_crypto()
            try:
                license_number = crypto.open_text(
                    bytes(row["license_no_ciphertext"]),
                    key_id=row["license_no_key_id"],
                    aad=license_no_aad(
                        first["tenant_public_id"],
                        first["onboarding_id"],
                        revision_id,
                        row["license_id"],
                    ),
                )
            except (RuntimeError, ValueError):
                raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
        licenses.append(
            {
                "license_id": row["license_id"],
                "license_type": row["license_type"],
                "license_no": license_number,
                "private_file_id": row["private_file_id"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
            }
        )
    return {
        "onboarding_id": first["onboarding_id"],
        "revision_id": revision_id,
        "revision_no": first["revision_no"],
        "status": first["status"],
        "institution_name": first["institution_name"],
        "institution_type": first["institution_type"],
        "administrative_region_id": first["administrative_region_id"],
        "institution_code": first["institution_code"],
        "service_tags": list(first["service_tags"]),
        "licenses": licenses,
        "correction_fields": list(first["correction_fields"]),
        "version": first["root_version"],
    }


@platform_router.post(
    "/direct-institution-onboardings",
    response_model=DirectCredentialDTO,
    status_code=201,
    responses={201: {"headers": _NO_STORE}},
)
async def create_direct_onboarding(
    payload: DirectCreateRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_review_writer_session),
):
    _require_role(current_user, "super_admin")
    response.headers["Cache-Control"] = "no-store"
    actor_scope = f"user:{current_user.id}:platform"
    try:
        request_digest_value = direct_create_request_digest(payload)
        request_digest_values = direct_create_request_digest_candidates(payload)
        phone_digest_key_id, phone_digest = account_phone_claim_digest(
            payload.admin_phone
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay_envelope = {
        "actor_scope": actor_scope,
        "operation": "CREATE",
        "idempotency_key": idempotency_key,
        "request_digest": request_digest_value,
        "request_digest_candidates": request_digest_values,
    }
    repository = DirectInstitutionOnboardingRepository(session)
    replay = await repository.review_replay(replay_envelope)
    if replay is not None:
        raise DirectOnboardingError("ONE_TIME_CREDENTIAL_ALREADY_ISSUED")
    step_up = await repository.create_step_up_begin(
        actor_user_id=current_user.id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
        phone_digest_key_id=phone_digest_key_id,
        phone_digest=phone_digest,
    )
    if step_up is None:
        raise DirectOnboardingError("STEP_UP_FORBIDDEN")
    replay = await repository.review_replay(replay_envelope)
    if replay is not None:
        await session.rollback()
        raise DirectOnboardingError("ONE_TIME_CREDENTIAL_ALREADY_ISSUED")
    try:
        profile_ciphertext = base64.b64decode(
            step_up["profile_secret_ciphertext"], validate=True
        )
        secret = open_totp_secret(
            profile_ciphertext,
            key_id=step_up["profile_key_id"],
            aad=platform_admin_totp_aad(current_user.id),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    now = datetime.now(UTC)
    accepted_step = accepted_totp_step(secret, payload.totp_code, at=now)
    if accepted_step is None:
        operation_id = Uuid7Generator().generate()
        failure_id = Uuid7Generator().generate()
        failure = await repository.create_step_up_failure(
            actor_user_id=current_user.id,
            actor_scope=actor_scope,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            request_digest=request_digest_value,
            observed_profile_version=int(step_up["profile_version"]),
            failed_time_step=int(now.timestamp()) // 30,
            failure_id=failure_id,
            phone_digest_key_id=phone_digest_key_id,
            phone_digest=phone_digest,
        )
        if failure is not None:
            await _commit_step_up_failure(
                session,
                failure_id=failure_id,
                actor_user_id=current_user.id,
                operation_id=operation_id,
                request_digest_value=request_digest_value,
            )
        raise DirectOnboardingError("STEP_UP_FORBIDDEN")
    try:
        envelope, confirmation, activation_code = build_direct_create_mutation(
            request=payload,
            actor_user_id=current_user.id,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except ValueError as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {"INVALID_REQUEST", "STEP_UP_FORBIDDEN"}:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    except RuntimeError:
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    result = await repository.create(envelope)
    if (
        result is None
        or str(result.get("onboarding_id")) != envelope["onboarding_id"]
        or str(result.get("tenant_id")) != envelope["tenant_public_id"]
        or str(result.get("credential_id")) != envelope["credential_id"]
        or result.get("credential_delivery_state") != "ISSUED"
        or result.get("status") != "PENDING_ACTIVATION"
        or result.get("version") != 1
        or result.get("credential_expires_at") is None
    ):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE")
    await _commit_review(
        session,
        confirmation=confirmation,
        credential_delivery=True,
    )
    return {
        "onboarding_id": result["onboarding_id"],
        "tenant_id": result["tenant_id"],
        "institution_code": envelope["institution_code"],
        "institution_name": payload.institution_name,
        "institution_type": payload.institution_type,
        "administrative_region_id": payload.administrative_region_id,
        "status": result["status"],
        "compliance_due_at": None,
        "current_revision_id": None,
        "version": result["version"],
        "credential_id": result["credential_id"],
        "activation_code": activation_code,
        "credential_expires_at": result["credential_expires_at"],
    }


@platform_router.get(
    "/direct-institution-onboardings", response_model=DirectOnboardingPageDTO
)
async def list_direct_onboardings(
    cursor: OpaqueCursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_onboarding_reader_session),
):
    _require_role(current_user, "super_admin")
    cursor_id = None
    ceiling_id = None
    if cursor is not None:
        try:
            cursor_id, ceiling_id = decode_direct_onboarding_cursor(
                cursor,
                actor_user_id=current_user.id,
                status=None,
            )
        except ValueError:
            raise DirectOnboardingError("INVALID_REQUEST") from None
    rows = await DirectInstitutionOnboardingRepository(session).read_rows(
        actor_user_id=current_user.id,
        resource="LIST",
        cursor_id=cursor_id,
        ceiling_id=ceiling_id,
        limit=limit,
        status=None,
    )
    next_cursor = None
    if rows and rows[-1]["has_more"]:
        next_cursor = encode_direct_onboarding_cursor(
            rows[-1]["onboarding_id"],
            ceiling_id=rows[-1]["snapshot_ceiling"],
            actor_user_id=current_user.id,
            status=None,
        )
    return {
        "items": [_public_onboarding(row) for row in rows],
        "next_cursor": next_cursor,
    }


@platform_router.get(
    "/direct-institution-onboardings/{onboarding_id}",
    response_model=DirectOnboardingDTO,
)
async def get_direct_onboarding(
    onboarding_id: UuidV7,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_onboarding_reader_session),
):
    _require_role(current_user, "super_admin")
    rows = await DirectInstitutionOnboardingRepository(session).read_rows(
        actor_user_id=current_user.id,
        resource="DETAIL",
        target_id=onboarding_id,
        limit=1,
    )
    if not rows:
        raise DirectOnboardingError("DIRECT_ONBOARDING_NOT_FOUND")
    return _public_onboarding(rows[0])


@platform_router.post(
    "/direct-institution-onboardings/{onboarding_id}/activation-credential:regenerate",
    response_model=DirectCredentialDTO,
    status_code=201,
    responses={201: {"headers": _NO_STORE}},
)
async def regenerate_activation_credential(
    onboarding_id: UuidV7,
    payload: VersionedStepUpRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    reader_session=Depends(get_institution_onboarding_reader_session),
    writer_session=Depends(get_institution_review_writer_session),
):
    _require_role(current_user, "super_admin")
    response.headers["Cache-Control"] = "no-store"
    actor_scope = f"user:{current_user.id}:platform"
    try:
        request_digest_value = direct_regenerate_request_digest(
            onboarding_id, payload
        )
        request_digest_values = direct_regenerate_request_digest_candidates(
            onboarding_id, payload
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay_envelope = {
        "actor_scope": actor_scope,
        "operation": "REGENERATE",
        "idempotency_key": idempotency_key,
        "request_digest": request_digest_value,
        "request_digest_candidates": request_digest_values,
    }
    writer = DirectInstitutionOnboardingRepository(writer_session)
    replay = await writer.review_replay(replay_envelope)
    if replay is not None:
        raise DirectOnboardingError("ONE_TIME_CREDENTIAL_ALREADY_ISSUED")
    rows = await DirectInstitutionOnboardingRepository(reader_session).read_rows(
        actor_user_id=current_user.id,
        resource="DETAIL",
        target_id=onboarding_id,
        limit=1,
    )
    rows = await _materialize_reader_rows(reader_session, rows)
    if not rows:
        raise DirectOnboardingError("DIRECT_ONBOARDING_NOT_FOUND")
    current = _public_onboarding(rows[0])
    step_up = await writer.regenerate_step_up_begin(
        actor_user_id=current_user.id,
        onboarding_id=onboarding_id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
    )
    if step_up is None:
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    try:
        secret = open_totp_secret(
            base64.b64decode(step_up["profile_secret_ciphertext"], validate=True),
            key_id=step_up["profile_key_id"],
            aad=platform_admin_totp_aad(current_user.id),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    now = datetime.now(UTC)
    accepted_step = accepted_totp_step(secret, payload.totp_code, at=now)
    if accepted_step is None:
        operation_id = Uuid7Generator().generate()
        failure_id = Uuid7Generator().generate()
        failure = await writer.regenerate_step_up_failure(
            actor_user_id=current_user.id,
            onboarding_id=onboarding_id,
            actor_scope=actor_scope,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            request_digest=request_digest_value,
            observed_profile_version=int(step_up["profile_version"]),
            failed_time_step=int(now.timestamp()) // 30,
            failure_id=failure_id,
        )
        if failure is not None:
            await _commit_step_up_failure(
                writer_session,
                failure_id=failure_id,
                actor_user_id=current_user.id,
                operation_id=operation_id,
                request_digest_value=request_digest_value,
            )
        raise DirectOnboardingError("STEP_UP_FORBIDDEN")
    try:
        envelope, confirmation, activation_code = build_direct_regenerate_mutation(
            onboarding_id=onboarding_id,
            request=payload,
            current=current,
            step_up=step_up,
            actor_user_id=current_user.id,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except ValueError as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {"INVALID_REQUEST", "STALE_VERSION", "STEP_UP_FORBIDDEN", "DIRECT_ONBOARDING_STATE_CONFLICT"}:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    except RuntimeError:
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay = await writer.review_replay(replay_envelope)
    if replay is not None:
        await writer_session.rollback()
        raise DirectOnboardingError("ONE_TIME_CREDENTIAL_ALREADY_ISSUED")
    result = await writer.regenerate(envelope)
    if (
        result is None
        or str(result.get("onboarding_id")) != str(onboarding_id)
        or result.get("status") != "PENDING_ACTIVATION"
        or result.get("version") != payload.expected_version + 1
        or str(result.get("credential_id")) != envelope["new_credential_id"]
        or result.get("credential_delivery_state") != "ISSUED"
        or result.get("credential_expires_at") is None
    ):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    await _commit_review(
        writer_session,
        confirmation=confirmation,
        credential_delivery=True,
    )
    return {
        **current,
        "status": result["status"],
        "version": result["version"],
        "credential_id": result["credential_id"],
        "activation_code": activation_code,
        "credential_expires_at": result["credential_expires_at"],
    }


@platform_router.post(
    "/direct-institution-onboardings/{onboarding_id}:revoke",
    response_model=DirectOnboardingDTO,
)
async def revoke_direct_onboarding(
    onboarding_id: UuidV7,
    payload: VersionedStepUpRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    reader_session=Depends(get_institution_onboarding_reader_session),
    writer_session=Depends(get_institution_review_writer_session),
):
    _require_role(current_user, "super_admin")
    actor_scope = f"user:{current_user.id}:platform"
    try:
        request_digest_value = direct_revoke_request_digest(onboarding_id, payload)
        request_digest_values = direct_revoke_request_digest_candidates(
            onboarding_id, payload
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay_envelope = {
        "actor_scope": actor_scope,
        "operation": "REVOKE",
        "idempotency_key": idempotency_key,
        "request_digest": request_digest_value,
        "request_digest_candidates": request_digest_values,
    }
    writer = DirectInstitutionOnboardingRepository(writer_session)
    replay = await writer.review_replay(replay_envelope)
    rows = await DirectInstitutionOnboardingRepository(reader_session).read_rows(
        actor_user_id=current_user.id,
        resource="DETAIL",
        target_id=onboarding_id,
        limit=1,
    )
    rows = await _materialize_reader_rows(reader_session, rows)
    if not rows:
        raise DirectOnboardingError("DIRECT_ONBOARDING_NOT_FOUND")
    current = _public_onboarding(rows[0])
    if replay is not None:
        if current["status"] != "REVOKED_BEFORE_ACTIVATION":
            raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN")
        return current
    step_up = await writer.revoke_step_up_begin(
        actor_user_id=current_user.id,
        onboarding_id=onboarding_id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
    )
    if step_up is None:
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    try:
        secret = open_totp_secret(
            base64.b64decode(step_up["profile_secret_ciphertext"], validate=True),
            key_id=step_up["profile_key_id"],
            aad=platform_admin_totp_aad(current_user.id),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    now = datetime.now(UTC)
    accepted_step = accepted_totp_step(secret, payload.totp_code, at=now)
    if accepted_step is None:
        operation_id = Uuid7Generator().generate()
        failure_id = Uuid7Generator().generate()
        failure = await writer.revoke_step_up_failure(
            actor_user_id=current_user.id,
            onboarding_id=onboarding_id,
            actor_scope=actor_scope,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            request_digest=request_digest_value,
            observed_profile_version=int(step_up["profile_version"]),
            failed_time_step=int(now.timestamp()) // 30,
            failure_id=failure_id,
        )
        if failure is not None:
            await _commit_step_up_failure(
                writer_session,
                failure_id=failure_id,
                actor_user_id=current_user.id,
                operation_id=operation_id,
                request_digest_value=request_digest_value,
            )
        raise DirectOnboardingError("STEP_UP_FORBIDDEN")
    try:
        envelope, confirmation = build_direct_revoke_mutation(
            onboarding_id=onboarding_id,
            request=payload,
            current=current,
            step_up=step_up,
            actor_user_id=current_user.id,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except ValueError as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {
            "INVALID_REQUEST",
            "STALE_VERSION",
            "STEP_UP_FORBIDDEN",
            "DIRECT_ONBOARDING_STATE_CONFLICT",
        }:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay = await writer.review_replay(replay_envelope)
    if replay is not None:
        await writer_session.rollback()
        rows = await DirectInstitutionOnboardingRepository(reader_session).read_rows(
            actor_user_id=current_user.id,
            resource="DETAIL",
            target_id=onboarding_id,
            limit=1,
        )
        if not rows or rows[0].get("status") != "REVOKED_BEFORE_ACTIVATION":
            raise DirectOnboardingError("COMMIT_OUTCOME_UNKNOWN")
        return _public_onboarding(rows[0])
    result = await writer.revoke(envelope)
    if (
        result is None
        or str(result.get("onboarding_id")) != str(onboarding_id)
        or result.get("status") != "REVOKED_BEFORE_ACTIVATION"
        or result.get("version") != payload.expected_version + 1
    ):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    await _commit_review(
        writer_session,
        confirmation=confirmation,
        credential_delivery=False,
    )
    return {
        **current,
        "status": result["status"],
        "version": result["version"],
    }


@onboarding_router.post("/direct-activate", response_model=DirectOnboardingDTO)
async def activate_direct_onboarding(
    payload: DirectActivationRequest,
    idempotency_key: IdempotencyKey,
    session=Depends(get_institution_onboarding_writer_session),
):
    try:
        request_digest_value = direct_activation_request_digest(payload)
        request_digest_values = direct_activation_request_digest_candidates(payload)
        credential_value_digests = credential_digest_candidates(
            payload.activation_code
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    repository = DirectInstitutionOnboardingRepository(session)
    replay = await repository.activation_replay(
        credential_id=payload.credential_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
        request_digest_candidates=request_digest_values,
    )
    if replay is not None:
        return replay
    authority = await repository.activation_authority(
        onboarding_id=payload.onboarding_id,
        credential_id=payload.credential_id,
        credential_digests=credential_value_digests,
    )
    if authority is None:
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    accepted_step = accepted_totp_step(
        payload.totp_secret,
        payload.totp_code,
        at=datetime.now(UTC),
    )
    if accepted_step is None:
        raise DirectOnboardingError("INVALID_REQUEST")
    try:
        envelope, confirmation = build_direct_activation_mutation(
            request=payload,
            authority=authority,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except ValueError as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {
            "INVALID_REQUEST",
            "STEP_UP_FORBIDDEN",
            "DIRECT_ONBOARDING_STATE_CONFLICT",
        }:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    except RuntimeError:
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay = await repository.activation_replay(
        credential_id=payload.credential_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
        request_digest_candidates=request_digest_values,
    )
    if replay is not None:
        await session.rollback()
        return replay
    result = await repository.activate(envelope)
    if (
        result is None
        or str(result.get("onboarding_id")) != str(payload.onboarding_id)
        or str(result.get("tenant_id")) != str(authority["tenant_public_id"])
        or result.get("status") != "ACTIVE_COMPLIANCE_PENDING"
        or result.get("version") != payload.expected_version + 1
    ):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    await _commit_activation(session, confirmation=confirmation)
    return result


@onboarding_router.get("/direct-compliance", response_model=DirectComplianceDTO)
async def get_direct_compliance(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_institution_onboarding_reader_session),
):
    _require_role(current_user, "org_admin")
    rows = await DirectInstitutionOnboardingRepository(
        session
    ).read_current_compliance(actor_user_id=current_user.id)
    return _compliance_projection(rows)


@onboarding_router.put("/direct-compliance", response_model=DirectComplianceDTO)
async def put_direct_compliance(
    payload: DirectComplianceRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    reader_session=Depends(get_institution_onboarding_reader_session),
    writer_session=Depends(get_institution_onboarding_writer_session),
):
    return await _mutate_compliance(
        operation="COMPLIANCE_SAVE",
        payload=payload,
        idempotency_key=idempotency_key,
        current_user=current_user,
        reader_session=reader_session,
        writer_session=writer_session,
    )


@onboarding_router.post(
    "/direct-compliance:submit", response_model=DirectComplianceDTO
)
async def submit_direct_compliance(
    payload: DirectComplianceRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    reader_session=Depends(get_institution_onboarding_reader_session),
    writer_session=Depends(get_institution_onboarding_writer_session),
):
    return await _mutate_compliance(
        operation="COMPLIANCE_SUBMIT",
        payload=payload,
        idempotency_key=idempotency_key,
        current_user=current_user,
        reader_session=reader_session,
        writer_session=writer_session,
    )


@platform_router.post(
    "/direct-institution-onboardings/{onboarding_id}/compliance-decision",
    response_model=DirectComplianceDTO,
)
async def decide_direct_compliance(
    onboarding_id: UuidV7,
    payload: ComplianceDecisionRequest,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    reader_session=Depends(get_institution_onboarding_reader_session),
    writer_session=Depends(get_institution_review_writer_session),
):
    _require_role(current_user, "super_admin")
    actor_scope = f"user:{current_user.id}:platform"
    try:
        request_digest_value = direct_compliance_decision_request_digest(
            onboarding_id, payload
        )
        request_digest_values = direct_compliance_decision_request_digest_candidates(
            onboarding_id, payload
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    rows = await DirectInstitutionOnboardingRepository(reader_session).read_rows(
        actor_user_id=current_user.id,
        resource="DETAIL",
        target_id=onboarding_id,
        limit=1,
    )
    rows = await _materialize_reader_rows(reader_session, rows)
    if not rows:
        raise DirectOnboardingError("DIRECT_ONBOARDING_NOT_FOUND")
    replay_envelope = {
        "actor_scope": actor_scope,
        "operation": "COMPLIANCE_DECIDE",
        "idempotency_key": idempotency_key,
        "request_digest": request_digest_value,
        "request_digest_candidates": request_digest_values,
    }
    writer = DirectInstitutionOnboardingRepository(writer_session)
    replay = await writer.review_replay(replay_envelope)
    if replay is not None:
        return replay
    step_up = await writer.compliance_decide_step_up_begin(
        actor_user_id=current_user.id,
        onboarding_id=onboarding_id,
        revision_id=payload.revision_id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
    )
    if step_up is None:
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    try:
        secret = open_totp_secret(
            base64.b64decode(step_up["profile_secret_ciphertext"], validate=True),
            key_id=step_up["profile_key_id"],
            aad=platform_admin_totp_aad(current_user.id),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    now = datetime.now(UTC)
    accepted_step = accepted_totp_step(secret, payload.totp_code, at=now)
    if accepted_step is None:
        operation_id = Uuid7Generator().generate()
        failure_id = Uuid7Generator().generate()
        failure = await writer.compliance_decide_step_up_failure(
            actor_user_id=current_user.id,
            onboarding_id=onboarding_id,
            revision_id=payload.revision_id,
            actor_scope=actor_scope,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            request_digest=request_digest_value,
            observed_profile_version=int(step_up["profile_version"]),
            failed_time_step=int(now.timestamp()) // 30,
            failure_id=failure_id,
        )
        if failure is not None:
            await _commit_step_up_failure(
                writer_session,
                failure_id=failure_id,
                actor_user_id=current_user.id,
                operation_id=operation_id,
                request_digest_value=request_digest_value,
            )
        raise DirectOnboardingError("STEP_UP_FORBIDDEN")
    try:
        envelope, confirmation = build_compliance_decision_mutation(
            onboarding_id=onboarding_id,
            request=payload,
            authority=step_up,
            actor_user_id=current_user.id,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except ValueError as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {
            "INVALID_REQUEST",
            "STALE_VERSION",
            "STEP_UP_FORBIDDEN",
            "DIRECT_ONBOARDING_STATE_CONFLICT",
        }:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    except RuntimeError:
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay = await writer.review_replay(replay_envelope)
    if replay is not None:
        await writer_session.rollback()
        return replay
    result = await writer.decide_compliance(envelope)
    if (
        result is None
        or str(result.get("onboarding_id")) != str(onboarding_id)
        or str(result.get("revision_id")) != str(payload.revision_id)
        or result.get("status")
        != (
            "COMPLIANCE_APPROVED"
            if payload.decision == "APPROVE"
            else "COMPLIANCE_NEEDS_CORRECTION"
        )
        or result.get("version") != payload.expected_version + 1
    ):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    await _commit_review(
        writer_session,
        confirmation=confirmation,
        credential_delivery=False,
    )
    return result


@platform_router.post(
    "/direct-institution-onboardings/{onboarding_id}/admin-handoffs",
    response_model=AdminHandoffCredentialDTO,
    status_code=201,
    responses={201: {"headers": _NO_STORE}},
)
async def create_admin_handoff(
    onboarding_id: UuidV7,
    payload: AdminHandoffCreateRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    writer_session=Depends(get_institution_review_writer_session),
):
    _require_role(current_user, "super_admin")
    response.headers["Cache-Control"] = "no-store"
    actor_scope = f"user:{current_user.id}:platform"
    try:
        request_digest_value = admin_handoff_create_request_digest(
            onboarding_id, payload
        )
        request_digest_values = admin_handoff_create_request_digest_candidates(
            onboarding_id, payload
        )
        phone_digest_key_id, phone_digest = account_phone_claim_digest(
            payload.new_phone
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay_envelope = {
        "actor_scope": actor_scope,
        "operation": "HANDOFF_CREATE",
        "idempotency_key": idempotency_key,
        "request_digest": request_digest_value,
        "request_digest_candidates": request_digest_values,
    }
    writer = DirectInstitutionOnboardingRepository(writer_session)
    if await writer.review_replay(replay_envelope) is not None:
        raise DirectOnboardingError("ONE_TIME_CREDENTIAL_ALREADY_ISSUED")
    step_up = await writer.handoff_create_step_up_begin(
        actor_user_id=current_user.id,
        onboarding_id=onboarding_id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
        phone_digest_key_id=phone_digest_key_id,
        phone_digest=phone_digest,
    )
    if step_up is None:
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    try:
        secret = open_totp_secret(
            base64.b64decode(step_up["profile_secret_ciphertext"], validate=True),
            key_id=step_up["profile_key_id"],
            aad=platform_admin_totp_aad(current_user.id),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    now = datetime.now(UTC)
    accepted_step = accepted_totp_step(secret, payload.totp_code, at=now)
    if accepted_step is None:
        operation_id = Uuid7Generator().generate()
        failure_id = Uuid7Generator().generate()
        failure = await writer.handoff_create_step_up_failure(
            actor_user_id=current_user.id,
            onboarding_id=onboarding_id,
            actor_scope=actor_scope,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            request_digest=request_digest_value,
            observed_profile_version=int(step_up["profile_version"]),
            failed_time_step=int(now.timestamp()) // 30,
            failure_id=failure_id,
            phone_digest_key_id=phone_digest_key_id,
            phone_digest=phone_digest,
        )
        if failure is not None:
            await _commit_step_up_failure(
                writer_session,
                failure_id=failure_id,
                actor_user_id=current_user.id,
                operation_id=operation_id,
                request_digest_value=request_digest_value,
            )
        raise DirectOnboardingError("STEP_UP_FORBIDDEN")
    try:
        envelope, confirmation, activation_code = build_admin_handoff_create_mutation(
            onboarding_id=onboarding_id,
            request=payload,
            authority=step_up,
            actor_user_id=current_user.id,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except (RuntimeError, ValueError) as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {"INVALID_REQUEST", "STALE_VERSION", "DIRECT_ONBOARDING_STATE_CONFLICT"}:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    result = await writer.create_handoff(envelope)
    if (
        result is None
        or str(result.get("handoff_id")) != envelope["handoff_id"]
        or result.get("status") != "ISSUED"
        or result.get("version") != 1
        or str(result.get("credential_id")) != envelope["credential_id"]
        or result.get("credential_expires_at") is None
    ):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    await _commit_review(writer_session, confirmation=confirmation, credential_delivery=True)
    return {
        "onboarding_id": result["onboarding_id"],
        "handoff_id": result["handoff_id"],
        "credential_id": result["credential_id"],
        "activation_code": activation_code,
        "credential_expires_at": result["credential_expires_at"],
        "status": result["status"],
        "version": result["version"],
    }


@platform_router.post(
    "/direct-institution-onboardings/{onboarding_id}/admin-handoffs/{handoff_id}:regenerate",
    response_model=AdminHandoffCredentialDTO,
    status_code=201,
    responses={201: {"headers": _NO_STORE}},
)
async def regenerate_admin_handoff(
    onboarding_id: UuidV7,
    handoff_id: UuidV7,
    payload: VersionedStepUpRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    writer_session=Depends(get_institution_review_writer_session),
):
    _require_role(current_user, "super_admin")
    response.headers["Cache-Control"] = "no-store"
    actor_scope = f"user:{current_user.id}:platform"
    try:
        request_digest_value = admin_handoff_regenerate_request_digest(
            onboarding_id, handoff_id, payload
        )
        request_digest_values = admin_handoff_regenerate_request_digest_candidates(
            onboarding_id, handoff_id, payload
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay_envelope = {
        "actor_scope": actor_scope,
        "operation": "HANDOFF_REGENERATE",
        "idempotency_key": idempotency_key,
        "request_digest": request_digest_value,
        "request_digest_candidates": request_digest_values,
    }
    writer = DirectInstitutionOnboardingRepository(writer_session)
    if await writer.review_replay(replay_envelope) is not None:
        raise DirectOnboardingError("ONE_TIME_CREDENTIAL_ALREADY_ISSUED")
    step_up = await writer.handoff_regenerate_step_up_begin(
        actor_user_id=current_user.id,
        handoff_id=handoff_id,
        actor_scope=actor_scope,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
    )
    if step_up is None or str(step_up.get("onboarding_id")) != str(onboarding_id):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    try:
        secret = open_totp_secret(
            base64.b64decode(step_up["profile_secret_ciphertext"], validate=True),
            key_id=step_up["profile_key_id"],
            aad=platform_admin_totp_aad(current_user.id),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    now = datetime.now(UTC)
    accepted_step = accepted_totp_step(secret, payload.totp_code, at=now)
    if accepted_step is None:
        operation_id = Uuid7Generator().generate()
        failure_id = Uuid7Generator().generate()
        failure = await writer.handoff_regenerate_step_up_failure(
            actor_user_id=current_user.id,
            handoff_id=handoff_id,
            actor_scope=actor_scope,
            idempotency_key=idempotency_key,
            operation_id=operation_id,
            request_digest=request_digest_value,
            observed_profile_version=int(step_up["profile_version"]),
            failed_time_step=int(now.timestamp()) // 30,
            failure_id=failure_id,
        )
        if failure is not None:
            await _commit_step_up_failure(
                writer_session,
                failure_id=failure_id,
                actor_user_id=current_user.id,
                operation_id=operation_id,
                request_digest_value=request_digest_value,
            )
        raise DirectOnboardingError("STEP_UP_FORBIDDEN")
    try:
        envelope, confirmation, activation_code = build_admin_handoff_regenerate_mutation(
            onboarding_id=onboarding_id,
            handoff_id=handoff_id,
            request=payload,
            authority=step_up,
            actor_user_id=current_user.id,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except (RuntimeError, ValueError) as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {"INVALID_REQUEST", "STALE_VERSION", "DIRECT_ONBOARDING_STATE_CONFLICT"}:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    result = await writer.regenerate_handoff(envelope)
    if (
        result is None
        or str(result.get("handoff_id")) != str(handoff_id)
        or result.get("status") != "ISSUED"
        or result.get("version") != payload.expected_version + 1
        or str(result.get("credential_id")) != envelope["new_credential_id"]
        or result.get("credential_expires_at") is None
    ):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    await _commit_review(writer_session, confirmation=confirmation, credential_delivery=True)
    return {
        "onboarding_id": result["onboarding_id"],
        "handoff_id": result["handoff_id"],
        "credential_id": result["credential_id"],
        "activation_code": activation_code,
        "credential_expires_at": result["credential_expires_at"],
        "status": result["status"],
        "version": result["version"],
    }


@onboarding_router.post(
    "/admin-handoffs/activate", response_model=AdminHandoffActivationDTO
)
async def activate_admin_handoff(
    payload: AdminHandoffActivationRequest,
    idempotency_key: IdempotencyKey,
    session=Depends(get_institution_onboarding_writer_session),
):
    try:
        request_digest_value = admin_handoff_activation_request_digest(payload)
        request_digest_values = admin_handoff_activation_request_digest_candidates(
            payload
        )
        credential_value_digests = credential_digest_candidates(
            payload.activation_code
        )
    except (RuntimeError, ValueError):
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    repository = DirectInstitutionOnboardingRepository(session)
    replay = await repository.handoff_activation_replay(
        credential_id=payload.credential_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
        request_digest_candidates=request_digest_values,
    )
    if replay is not None:
        return replay
    authority = await repository.handoff_activation_authority(
        onboarding_id=payload.onboarding_id,
        handoff_id=payload.handoff_id,
        credential_id=payload.credential_id,
        credential_digests=credential_value_digests,
    )
    if authority is None:
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    accepted_step = accepted_totp_step(
        payload.totp_secret, payload.totp_code, at=datetime.now(UTC)
    )
    if accepted_step is None:
        raise DirectOnboardingError("INVALID_REQUEST")
    try:
        envelope, confirmation = build_admin_handoff_activation_mutation(
            request=payload,
            authority=authority,
            idempotency_key=idempotency_key,
            accepted_totp_step=accepted_step,
            id_factory=Uuid7Generator().generate,
        )
    except (RuntimeError, ValueError) as error:
        code = error.args[0] if len(error.args) == 1 else None
        if code in {"INVALID_REQUEST", "DIRECT_ONBOARDING_STATE_CONFLICT"}:
            raise DirectOnboardingError(code) from None
        raise DirectOnboardingError("DEPENDENCY_UNAVAILABLE") from None
    replay = await repository.handoff_activation_replay(
        credential_id=payload.credential_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest_value,
        request_digest_candidates=request_digest_values,
    )
    if replay is not None:
        await session.rollback()
        return replay
    result = await repository.activate_handoff(envelope)
    if (
        result is None
        or str(result.get("onboarding_id")) != str(payload.onboarding_id)
        or str(result.get("handoff_id")) != str(payload.handoff_id)
        or result.get("status") != "ACTIVATED"
        or result.get("version") != payload.expected_version + 1
    ):
        raise DirectOnboardingError("DIRECT_ONBOARDING_STATE_CONFLICT")
    await _commit_handoff_activation(session, confirmation=confirmation)
    return result
