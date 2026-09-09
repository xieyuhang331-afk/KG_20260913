from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import hmac
import json
from typing import Awaitable, Callable, Mapping
from uuid import UUID

from app.core.认证配置校验 import purpose_signing_key

from .domain import (
    MilestoneCode,
    MilestoneStatus,
    TransferStatus,
    build_milestone_windows,
    business_date,
    closing_readiness,
    next_transfer_status,
)


class ServiceFulfillmentError(RuntimeError):
    pass


class CommitOutcome(str, Enum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


_PAGE_CURSOR_DOMAIN = b"slice7-service-fulfillment-page-cursor:v1:\x00"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _page_cursor_key() -> bytes:
    try:
        return purpose_signing_key("KG_SLICE7_CURSOR_SIGNING_KEY")
    except ValueError:
        raise ServiceFulfillmentError("DEPENDENCY_UNAVAILABLE") from None


def _page_filters(value: Mapping[str, str | None] | None) -> dict[str, str | None]:
    filters = dict(value or {})
    if not set(filters).issubset({"risk", "status"}) or any(
        item is not None and not isinstance(item, str) for item in filters.values()
    ):
        raise ServiceFulfillmentError("INVALID_REQUEST")
    return {key: filters[key] for key in sorted(filters)}


def encode_page_cursor(
    cursor_id: UUID,
    *,
    snapshot_ceiling: UUID,
    resource: str,
    scope_id: UUID | None,
    actor_user_id: int,
    actor_role: str,
    actor_tenant_id: int | None,
    filters: Mapping[str, str | None] | None = None,
) -> str:
    raw = json.dumps(
        {
            "actor_role": actor_role,
            "actor_tenant_id": actor_tenant_id,
            "actor_user_id": actor_user_id,
            "cursor_id": str(cursor_id),
            "filters": _page_filters(filters),
            "resource": resource,
            "snapshot_ceiling": str(snapshot_ceiling),
            "scope_id": None if scope_id is None else str(scope_id),
            "v": 1,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    signature = hmac.new(
        _page_cursor_key(),
        _PAGE_CURSOR_DOMAIN + raw,
        hashlib.sha256,
    ).digest()
    return f"{_base64url(raw)}.{_base64url(signature)}"


def decode_page_cursor(
    cursor: str | None,
    *,
    resource: str,
    scope_id: UUID | None,
    actor_user_id: int,
    actor_role: str,
    actor_tenant_id: int | None,
    filters: Mapping[str, str | None] | None = None,
) -> tuple[UUID | None, UUID | None]:
    if cursor is None:
        return None, None
    try:
        encoded_payload, encoded_signature = cursor.split(".")
        raw = base64.b64decode(
            (encoded_payload + "=" * (-len(encoded_payload) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        signature = base64.b64decode(
            (encoded_signature + "=" * (-len(encoded_signature) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        if _base64url(raw) != encoded_payload or _base64url(signature) != encoded_signature:
            raise ValueError
        expected = hmac.new(
            _page_cursor_key(),
            _PAGE_CURSOR_DOMAIN + raw,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw)
        if set(payload) != {
            "actor_role",
            "actor_tenant_id",
            "actor_user_id",
            "cursor_id",
            "filters",
            "resource",
            "snapshot_ceiling",
            "scope_id",
            "v",
        } or payload["v"] != 1:
            raise ValueError
        expected_scope = None if scope_id is None else str(scope_id)
        if (
            payload["resource"] != resource
            or payload["scope_id"] != expected_scope
            or payload["actor_user_id"] != actor_user_id
            or payload["actor_role"] != actor_role
            or payload["actor_tenant_id"] != actor_tenant_id
            or payload["filters"] != _page_filters(filters)
        ):
            raise ValueError
        cursor_id = UUID(payload["cursor_id"])
        snapshot_ceiling = UUID(payload["snapshot_ceiling"])
        if (
            cursor_id.version != 7
            or snapshot_ceiling.version != 7
            or cursor_id > snapshot_ceiling
        ):
            raise ValueError
        return cursor_id, snapshot_ceiling
    except (
        AttributeError,
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise ServiceFulfillmentError("INVALID_REQUEST") from None


async def commit_with_confirmation(
    session,
    *,
    confirm: Callable[[], Awaitable[CommitOutcome]],
) -> CommitOutcome:
    try:
        await session.commit()
        return CommitOutcome.COMMITTED
    except BaseException as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        try:
            await session.rollback()
        except Exception:
            pass
        try:
            outcome = await confirm()
        except Exception:
            outcome = CommitOutcome.UNKNOWN
        if outcome is CommitOutcome.COMMITTED:
            return outcome
        if outcome is CommitOutcome.NOT_COMMITTED:
            raise ServiceFulfillmentError("COMMIT_NOT_COMMITTED") from None
        raise ServiceFulfillmentError("COMMIT_OUTCOME_UNKNOWN") from None


def _json_value(value: object) -> object:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, UUID):
        return str(value)
    if type(value) in (datetime, date):
        return value.isoformat()
    if type(value) is Decimal:
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    return value


def digest_hex(value: object) -> str:
    encoded = json.dumps(
        _json_value(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _datetime_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _date_value(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


class ServiceFulfillmentService:
    def __init__(self, repository, clock, id_factory: Callable[[], UUID]) -> None:
        self.repository = repository
        self.clock = clock
        self.id_factory = id_factory

    async def _authorized(
        self,
        operation: str,
        target_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int | None,
    ) -> dict:
        authority = await self.repository.authority(
            operation, target_id, actor_user_id, actor_role, actor_tenant_id
        )
        if authority is None:
            raise ServiceFulfillmentError("RESOURCE_NOT_FOUND")
        error = authority.get("error_code")
        if error:
            raise ServiceFulfillmentError(str(error))
        return authority

    async def _mutation(
        self,
        *,
        operation: str,
        target_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int | None,
        idempotency_key: str,
        request: Mapping[str, object],
        request_digest: str | None = None,
        authority_operation: str | None = None,
        authority_target_id: UUID | None = None,
    ) -> dict:
        resolved_authority_operation = authority_operation or operation
        resolved_authority_target_id = authority_target_id or target_id
        authority = await self._authorized(
            resolved_authority_operation,
            resolved_authority_target_id,
            actor_user_id,
            actor_role,
            actor_tenant_id,
        )
        request_digest = request_digest or digest_hex(request)
        replay = await self.repository.replay(
            actor_user_id, operation, idempotency_key, request_digest
        )
        if replay is not None:
            return replay
        mutation_request = dict(request)
        if operation == "ACTIVATE_CYCLE":
            mutation_request["milestone_ids"] = {
                code.value: self.id_factory() for code in MilestoneCode
            }
        if operation == "COORDINATE_TRANSFER_CLOSE":
            mutation_request["handoff_id"] = self.id_factory()
            mutation_request["handoff_scope_digest"] = digest_hex(
                authority["transfer_requested_scope"]
            )
        now = self.clock.now()
        response_id = target_id if operation == "CREATE_EXPORT" else self.id_factory()
        response = self._expected_response(
            operation=operation,
            target_id=target_id,
            operation_id=response_id,
            authority=authority,
            request=mutation_request,
            occurred_at=now,
        )
        payload = {
            **mutation_request,
            "operation_id": response_id,
            "operation": operation,
            "target_id": target_id,
            "actor_user_id": actor_user_id,
            "actor_scope": str(actor_user_id),
            "actor_role": actor_role,
            "actor_tenant_id": actor_tenant_id,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "authority": authority,
            "authority_digest": digest_hex(authority),
            "occurred_at": now,
            "business_date": business_date(now),
            "audit_id": self.id_factory(),
            "event_id": self.id_factory(),
            "receipt_id": self.id_factory(),
            "lifecycle_event_id": self.id_factory(),
            "response": response,
        }
        if authority_operation is not None:
            payload["authority_operation"] = authority_operation
        if authority_target_id is not None:
            payload["authority_target_id"] = authority_target_id
        payload["expected_response_digest"] = digest_hex(response)
        return await self.repository.mutate(operation, payload)

    @staticmethod
    def _expected_response(
        *,
        operation: str,
        target_id: UUID,
        operation_id: UUID,
        authority: Mapping[str, object],
        request: Mapping[str, object],
        occurred_at: datetime,
    ) -> dict[str, object]:
        if operation == "COMPLETE_MILESTONE":
            return {
                "milestone_id": target_id,
                "service_case_id": authority["service_case_id"],
                "code": authority["milestone_code"],
                "window_start": authority["window_start"],
                "window_end": authority["window_end"],
                "status": "COMPLETED",
                "completed_at": occurred_at,
                "record_summary": request["record_summary"],
                "version": int(authority["milestone_version"]) + 1,
            }
        if operation == "ACTIVATE_CYCLE":
            windows = request["windows"]
            milestone_ids = request["milestone_ids"]
            milestones = [
                {
                    "milestone_id": milestone_ids[code],
                    "service_case_id": target_id,
                    "code": code,
                    "window_start": windows[code]["start"],
                    "window_end": windows[code]["end"],
                    "status": "DUE" if code == "D0" else "PENDING",
                    "completed_at": None,
                    "record_summary": None,
                    "version": 1,
                }
                for code in ("D0", "D7", "D14", "D21", "D28")
            ]
            return {
                "service_case_id": target_id,
                "lifecycle_status": "ACTIVE",
                "risk_flag": None,
                "active_plan_id": authority["active_plan_id"],
                "cycle_anchor_at": request["activated_at"],
                "current_schedule_version": 1,
                "milestones": milestones,
                "open_high_risk_count": int(authority.get("high_risk_count", 0)),
                "closing_readiness": "BLOCKED_BY_MISSING_MILESTONE",
                "version": int(authority.get("service_case_version", 1)),
            }
        if operation == "CREATE_CLOSING_ASSESSMENT":
            return {
                "closing_assessment_id": operation_id,
                "service_case_id": target_id,
                "assessment_id": request["assessment_id"],
                "final_retest_evidence": request["final_retest_evidence"],
                "created_at": occurred_at,
                "version": 1,
            }
        if operation == "CREATE_SUMMARY":
            return {
                "summary_id": operation_id,
                "service_case_id": target_id,
                "assessment_id": request["assessment_id"],
                "final_retest_evidence": request["final_retest_evidence"],
                "milestone_outcomes": request["milestone_outcomes"],
                "safety_follow_up": request["safety_follow_up"],
                "next_step": request["next_step"],
                "created_at": occurred_at,
                "viewed_at": None,
                "version": 1,
            }
        if operation == "CREATE_TRANSFER":
            return {
                "transfer_id": operation_id,
                "source_service_case_id": target_id,
                "source_tenant_id": authority["tenant_public_id"],
                "target_tenant_id": request["target_tenant_id"],
                "status": "REQUESTED_BY_USER",
                "requested_scope": request["requested_scope"],
                "target_decision": None,
                "source_closure_status": None,
                "scope_confirmed_at": None,
                "transferred_at": None,
                "version": 1,
            }
        if operation in {
            "CANCEL_TRANSFER",
            "CONFIRM_TRANSFER_SCOPE",
            "START_REVIEW_TRANSFER",
            "ACCEPT_TRANSFER",
            "REJECT_TRANSFER",
            "SOURCE_CLOSE_TRANSFER",
            "COORDINATE_TRANSFER_CLOSE",
        }:
            requested_status = {
                "CANCEL_TRANSFER": "CANCELLED_BY_USER",
                "CONFIRM_TRANSFER_SCOPE": "USER_SCOPE_CONFIRMED",
                "START_REVIEW_TRANSFER": "NEW_INSTITUTION_REVIEWING",
                "ACCEPT_TRANSFER": "ACCEPTED",
                "REJECT_TRANSFER": "REJECTED_BY_NEW_INSTITUTION",
                "SOURCE_CLOSE_TRANSFER": "OLD_INSTITUTION_CLOSING",
                "COORDINATE_TRANSFER_CLOSE": "TRANSFERRED",
            }[operation]
            return {
                "transfer_id": target_id,
                "source_service_case_id": authority["service_case_id"],
                "source_tenant_id": authority["tenant_public_id"],
                "target_tenant_id": authority["transfer_target_tenant_id"],
                "status": requested_status,
                "requested_scope": request.get(
                    "exact_scope", authority["transfer_requested_scope"]
                ),
                "target_decision": request.get(
                    "reason_code", authority.get("transfer_target_decision")
                ),
                "source_closure_status": request.get(
                    "risk_disposition", authority.get("transfer_source_closure_status")
                ),
                "scope_confirmed_at": occurred_at
                if operation == "CONFIRM_TRANSFER_SCOPE"
                else authority.get("transfer_scope_confirmed_at"),
                "transferred_at": occurred_at
                if operation == "COORDINATE_TRANSFER_CLOSE"
                else authority.get("transfer_transferred_at"),
                "version": int(authority["transfer_version"]) + 1,
            }
        if operation == "LINK_CONTINUATION_CASE":
            return {
                "handoff_id": authority["handoff_id"],
                "transfer_id": target_id,
                "source_service_case_id": authority["service_case_id"],
                "source_tenant_id": authority["tenant_public_id"],
                "target_tenant_id": authority["transfer_target_tenant_id"],
                "subject_member_id": authority["subject_member_id"],
                "authorized_scope": authority["transfer_requested_scope"],
                "status": "CONTINUATION_CASE_LINKED",
                "created_at": authority["handoff_created_at"],
                "linked_enrollment_id": request["new_enrollment_id"],
                "linked_service_case_id": request["new_service_case_id"],
                "linked_at": occurred_at,
                "version": int(authority["handoff_version"]) + 1,
            }
        if operation == "AUTHORIZE_PROXY_MAJOR":
            return {
                "authorization_id": operation_id,
                "proxy_grant_id": target_id,
                "principal_member_id": authority["principal_member_id"],
                "proxy_member_id": authority["proxy_member_id"],
                "authorization_document_version_id": request["authorization_document_version_id"],
                "witness_decision_id": request["witness_decision_id"],
                "permission_codes": request["permission_codes"],
                "granted_by": authority["actor_user_id"],
                "valid_from": occurred_at,
                "valid_until": request.get("valid_until"),
                "revoked_at": None,
                "version": 1,
            }
        if operation == "REVOKE_PROXY_MAJOR":
            return {
                "authorization_id": target_id,
                "proxy_grant_id": authority["proxy_grant_id"],
                "principal_member_id": authority["principal_member_id"],
                "proxy_member_id": authority["proxy_member_id"],
                "authorization_document_version_id": authority["authorization_document_version_id"],
                "witness_decision_id": authority["witness_decision_id"],
                "permission_codes": authority["permission_codes"],
                "granted_by": authority["granted_by"],
                "valid_from": authority["valid_from"],
                "valid_until": authority.get("valid_until"),
                "revoked_at": occurred_at,
                "version": int(authority["authorization_version"]) + 1,
            }
        if operation == "CREATE_EXPORT":
            return {
                "export_id": operation_id,
                "subject_member_id": authority["subject_member_id"],
                "status": "REQUESTED",
                "requested_scope": request["requested_scope"],
                "requested_at": occurred_at,
                "ready_at": None,
                "expires_at": None,
                "downloaded_at": None,
                "version": 1,
            }
        if operation == "CANCEL_EXPORT":
            return {
                "export_id": target_id,
                "subject_member_id": authority["subject_member_id"],
                "status": "CANCELLED",
                "requested_scope": authority["export_requested_scope"],
                "requested_at": authority["export_requested_at"],
                "ready_at": authority.get("export_ready_at"),
                "expires_at": authority.get("export_expires_at"),
                "downloaded_at": authority.get("export_downloaded_at"),
                "version": int(authority["export_version"]) + 1,
            }
        if operation == "EXPORT_DOWNLOAD_ACCESS":
            return {
                "access_id": operation_id,
                "export_id": target_id,
                "private_file_id": authority["private_file_id"],
                "expires_at": occurred_at + timedelta(minutes=5),
                "filename": "personal-data-export.zip",
                "content_type": "application/zip",
            }
        if operation == "ACK_SUMMARY":
            return {
                "summary_id": target_id,
                "service_case_id": authority["service_case_id"],
                "assessment_id": authority["summary_assessment_id"],
                "final_retest_evidence": authority["summary_content"][
                    "final_retest_evidence"
                ],
                "milestone_outcomes": authority["summary_content"][
                    "milestone_outcomes"
                ],
                "safety_follow_up": authority["summary_content"]["safety_follow_up"],
                "next_step": authority["summary_content"]["next_step"],
                "created_at": authority["summary_created_at"],
                "viewed_at": occurred_at,
                "version": int(authority["summary_version"]),
            }
        lifecycle = {
            "PAUSE_CASE": "PAUSED",
            "RESUME_CASE": "ACTIVE",
            "WITHDRAW_CASE": "WITHDRAWN_BY_USER",
            "TERMINATE_CASE": "TERMINATED_BY_INSTITUTION",
            "UNABLE_TO_CONTACT": "UNABLE_TO_CONTACT",
            "SAFETY_TERMINATE": "SAFETY_TERMINATED",
        }.get(operation, authority.get("case_status", "ACTIVE"))
        statuses = {
            MilestoneCode(str(row["code"])): MilestoneStatus(str(row["status"]))
            for row in authority.get("milestones", ())
        }
        readiness = (
            closing_readiness(
                statuses,
                closing_assessment_complete=bool(
                    authority.get("closing_assessment_complete")
                ),
                summary_complete=bool(authority.get("summary_complete")),
                user_acknowledged=bool(authority.get("summary_acknowledged")),
                open_high_risk_count=int(authority.get("high_risk_count", 0)),
            ).value
            if len(statuses) == len(MilestoneCode)
            else "BLOCKED_BY_MISSING_MILESTONE"
        )
        return {
            "service_case_id": authority["service_case_id"],
            "lifecycle_status": lifecycle,
            "risk_flag": "AT_RISK" if int(authority.get("high_risk_count", 0)) > 0 else None,
            "active_plan_id": authority["active_plan_id"],
            "cycle_anchor_at": authority["cycle_anchor_at"],
            "current_schedule_version": authority["schedule_version"],
            "milestones": authority["milestones"],
            "open_high_risk_count": authority["high_risk_count"],
            "closing_readiness": readiness,
            "version": int(authority.get("service_case_version", 1)) + 1,
        }

    async def activate_cycle(
        self,
        *,
        service_case_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int,
        idempotency_key: str,
        expected_version: int,
    ) -> dict:
        authority = await self._authorized(
            "ACTIVATE_CYCLE", service_case_id, actor_user_id, actor_role, actor_tenant_id
        )
        activated_at = _datetime_value(authority.get("plan_activated_at"))
        if activated_at is None:
            raise ServiceFulfillmentError("ACTIVE_PLAN_REQUIRED")
        windows = build_milestone_windows(activated_at)
        return await self._mutation(
            operation="ACTIVATE_CYCLE",
            target_id=service_case_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            actor_tenant_id=actor_tenant_id,
            idempotency_key=idempotency_key,
            request={
                "expected_version": expected_version,
                "activated_at": activated_at,
                "windows": {
                    code.value: {"start": window.start, "end": window.end}
                    for code, window in windows.items()
                },
            },
        )

    async def complete_milestone(
        self,
        *,
        milestone_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int,
        idempotency_key: str,
        expected_version: int,
        record_summary: Mapping[str, str],
        evidence_refs: tuple[UUID, ...],
    ) -> dict:
        authority = await self._authorized(
            "COMPLETE_MILESTONE", milestone_id, actor_user_id, actor_role, actor_tenant_id
        )
        if authority.get("case_status") != "ACTIVE":
            raise ServiceFulfillmentError("CASE_STATE_CONFLICT")
        today = business_date(self.clock.now())
        window_start = _date_value(authority.get("window_start"))
        window_end = _date_value(authority.get("window_end"))
        if window_start is None or window_end is None or not (
            window_start <= today <= window_end
        ):
            raise ServiceFulfillmentError("MILESTONE_WINDOW_CLOSED")
        return await self._mutation(
            operation="COMPLETE_MILESTONE",
            target_id=milestone_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            actor_tenant_id=actor_tenant_id,
            idempotency_key=idempotency_key,
            request={
                "expected_version": expected_version,
                "record_summary": dict(record_summary),
                "evidence_refs": evidence_refs,
            },
        )

    async def case_transition(
        self,
        *,
        operation: str,
        service_case_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int | None,
        idempotency_key: str,
        request: Mapping[str, object],
    ) -> dict:
        authority = await self._authorized(
            operation,
            service_case_id,
            actor_user_id,
            actor_role,
            actor_tenant_id,
        )
        current_status = str(authority.get("case_status", "ACTIVE"))
        if operation == "PAUSE_CASE" and current_status != "ACTIVE":
            raise ServiceFulfillmentError("CASE_STATE_CONFLICT")
        if operation == "RESUME_CASE" and current_status != "PAUSED":
            raise ServiceFulfillmentError("CASE_STATE_CONFLICT")
        if operation in {"CREATE_CLOSING_ASSESSMENT", "CREATE_SUMMARY", "COMPLETE_CASE"}:
            statuses = {
                MilestoneCode(str(row["code"])): MilestoneStatus(str(row["status"]))
                for row in authority.get("milestones", ())
            }
            if len(statuses) != len(MilestoneCode) or any(
                status is not MilestoneStatus.COMPLETED for status in statuses.values()
            ):
                raise ServiceFulfillmentError("CLOSURE_PREREQUISITE_MISSING")
        if operation in {"CREATE_CLOSING_ASSESSMENT", "CREATE_SUMMARY"} and current_status != "CLOSING":
            raise ServiceFulfillmentError("CASE_STATE_CONFLICT")
        if operation == "CREATE_CLOSING_ASSESSMENT":
            if str(authority.get("latest_assessment_id")) != str(request.get("assessment_id")):
                raise ServiceFulfillmentError("CLOSURE_PREREQUISITE_MISSING")
        if operation == "CREATE_SUMMARY":
            if not authority.get("closing_assessment_complete"):
                raise ServiceFulfillmentError("CLOSURE_PREREQUISITE_MISSING")
        if operation == "COMPLETE_CASE":
            readiness = closing_readiness(
                statuses,
                closing_assessment_complete=bool(
                    authority.get("closing_assessment_complete")
                ),
                summary_complete=bool(authority.get("summary_complete")),
                user_acknowledged=bool(authority.get("summary_acknowledged")),
                open_high_risk_count=int(authority.get("high_risk_count", 0)),
            )
            if readiness.value == "BLOCKED_BY_HIGH_RISK":
                raise ServiceFulfillmentError("HIGH_RISK_BLOCKS_COMPLETION")
            if readiness.value != "READY_TO_CLOSE":
                raise ServiceFulfillmentError("CLOSURE_PREREQUISITE_MISSING")
        if operation == "ACK_SUMMARY":
            statuses = {
                MilestoneCode(str(row["code"])): MilestoneStatus(str(row["status"]))
                for row in authority.get("milestones", ())
            }
            readiness = closing_readiness(
                statuses,
                closing_assessment_complete=bool(
                    authority.get("closing_assessment_complete")
                ),
                summary_complete=bool(authority.get("summary_complete")),
                user_acknowledged=True,
                open_high_risk_count=int(authority.get("high_risk_count", 0)),
            )
            if current_status != "CLOSING" or readiness.value != "READY_TO_CLOSE":
                raise ServiceFulfillmentError("CLOSURE_PREREQUISITE_MISSING")
        return await self._mutation(
            operation=operation,
            target_id=service_case_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            actor_tenant_id=actor_tenant_id,
            idempotency_key=idempotency_key,
            request=request,
        )

    async def transfer_transition(
        self,
        *,
        operation: str,
        transfer_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int | None,
        idempotency_key: str,
        request: Mapping[str, object],
    ) -> dict:
        request_digest = digest_hex(request)
        replay = await self.repository.replay(
            actor_user_id, operation, idempotency_key, request_digest
        )
        if replay is not None:
            return replay
        authority = await self._authorized(
            operation,
            transfer_id,
            actor_user_id,
            actor_role,
            actor_tenant_id,
        )
        if operation == "CREATE_TRANSFER":
            if not authority.get("service_ready"):
                raise ServiceFulfillmentError("CURRENTNESS_FORBIDDEN")
        else:
            requested = {
                "CANCEL_TRANSFER": TransferStatus.CANCELLED_BY_USER,
                "CONFIRM_TRANSFER_SCOPE": TransferStatus.USER_SCOPE_CONFIRMED,
                "START_REVIEW_TRANSFER": TransferStatus.NEW_INSTITUTION_REVIEWING,
                "ACCEPT_TRANSFER": TransferStatus.ACCEPTED,
                "REJECT_TRANSFER": TransferStatus.REJECTED_BY_NEW_INSTITUTION,
                "SOURCE_CLOSE_TRANSFER": TransferStatus.OLD_INSTITUTION_CLOSING,
                "COORDINATE_TRANSFER_CLOSE": TransferStatus.TRANSFERRED,
            }.get(operation)
            try:
                current = TransferStatus(str(authority["transfer_status"]))
                if requested is not None:
                    next_transfer_status(current, requested)
            except (KeyError, ValueError):
                raise ServiceFulfillmentError("TRANSFER_STATE_CONFLICT") from None
            if operation == "CONFIRM_TRANSFER_SCOPE" and tuple(
                request.get("exact_scope", ())
            ) != tuple(authority.get("transfer_requested_scope", ())):
                raise ServiceFulfillmentError("TRANSFER_STATE_CONFLICT")
            if operation == "ACCEPT_TRANSFER":
                if not authority.get("target_service_ready"):
                    raise ServiceFulfillmentError("CURRENTNESS_FORBIDDEN")
                if request.get("service_label") not in tuple(
                    authority.get("target_service_tags", ())
                ):
                    raise ServiceFulfillmentError("TRANSFER_STATE_CONFLICT")
            if operation == "LINK_CONTINUATION_CASE":
                case_id = request.get("new_service_case_id")
                if not isinstance(case_id, UUID):
                    raise ServiceFulfillmentError("INVALID_REQUEST")
                new_case = await self._authorized(
                    "VALIDATE_CONTINUATION_CASE",
                    case_id,
                    actor_user_id,
                    actor_role,
                    actor_tenant_id,
                )
                if (
                    str(new_case.get("tenant_public_id"))
                    != str(authority.get("transfer_target_tenant_id"))
                    or str(new_case.get("subject_member_id"))
                    != str(authority.get("subject_member_id"))
                    or not new_case.get("service_ready")
                    or not new_case.get("assignment_current")
                    or not new_case.get("therapist_current")
                    or not new_case.get("consent_current")
                ):
                    raise ServiceFulfillmentError("CURRENTNESS_FORBIDDEN")
                request = {**request, "new_enrollment_id": new_case["new_enrollment_id"]}
        return await self._mutation(
            operation=operation,
            target_id=transfer_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            actor_tenant_id=actor_tenant_id,
            idempotency_key=idempotency_key,
            request=request,
            request_digest=request_digest,
        )

    async def major_authorization_transition(
        self,
        *,
        operation: str,
        target_id: UUID,
        actor_user_id: int,
        actor_role: str,
        idempotency_key: str,
        request: Mapping[str, object],
    ) -> dict:
        return await self._mutation(
            operation=operation,
            target_id=target_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            actor_tenant_id=None,
            idempotency_key=idempotency_key,
            request=request,
        )

    async def export_transition(
        self,
        *,
        operation: str,
        export_or_member_id: UUID | None,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int | None,
        idempotency_key: str,
        request: Mapping[str, object],
    ) -> dict:
        target_id = (
            self.id_factory()
            if operation == "CREATE_EXPORT"
            else export_or_member_id
        )
        if target_id is None:
            raise ServiceFulfillmentError("NOT_FOUND")
        requested_subject = request.get("subject_member_id")
        if requested_subject is not None and not isinstance(requested_subject, UUID):
            raise ServiceFulfillmentError("INVALID_REQUEST")
        authority_operation = (
            "CREATE_EXPORT_FOR_SUBJECT"
            if operation == "CREATE_EXPORT" and requested_subject is not None
            else operation
        )
        authority_target_id = (
            requested_subject
            if operation == "CREATE_EXPORT" and requested_subject is not None
            else target_id
        )
        authority = await self._authorized(
            authority_operation,
            authority_target_id,
            actor_user_id,
            actor_role,
            actor_tenant_id,
        )
        if operation == "CANCEL_EXPORT" and authority.get("export_status") not in {
            "REQUESTED",
            "GENERATING",
        }:
            raise ServiceFulfillmentError("EXPORT_NOT_READY")
        if operation == "EXPORT_DOWNLOAD_ACCESS":
            if request.get("step_up_verified") is not True:
                raise ServiceFulfillmentError("STEP_UP_FORBIDDEN")
            if authority.get("export_status") != "READY":
                raise ServiceFulfillmentError("EXPORT_NOT_READY")
        return await self._mutation(
            operation=operation,
            target_id=target_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            actor_tenant_id=actor_tenant_id,
            idempotency_key=idempotency_key,
            request=request,
            authority_operation=(
                authority_operation if authority_operation != operation else None
            ),
            authority_target_id=(
                authority_target_id if authority_target_id != target_id else None
            ),
        )

    async def mark_overdue(self, worker_id: str, limit: int = 100) -> list[dict]:
        work = await self.repository.claim_work("MILESTONE_OVERDUE", worker_id, limit)
        results: list[dict] = []
        for row in work:
            now = self.clock.now()
            response = {
                "milestone_id": row["milestone_id"],
                "service_case_id": row["service_case_id"],
                "code": row["code"],
                "window_start": row["window_start"],
                "window_end": row["window_end"],
                "status": "MISSED",
                "completed_at": None,
                "record_summary": None,
                "version": int(row["version"]) + 1,
            }
            request_digest = digest_hex(
                {
                    "milestone_id": row["milestone_id"],
                    "expected_version": row["version"],
                    "occurred_at": now,
                }
            )
            results.append(
                await self.repository.mutate(
                    "MARK_MISSED",
                    {
                        "operation": "MARK_MISSED",
                        "target_id": row["milestone_id"],
                        "operation_id": self.id_factory(),
                        "lifecycle_event_id": self.id_factory(),
                        "actor_user_id": 0,
                        "actor_scope": worker_id,
                        "actor_role": "export_worker",
                        "actor_tenant_id": None,
                        "idempotency_key": (
                            f"MARK_MISSED:{row['milestone_id']}:{row['version']}"
                        ),
                        "request_digest": request_digest,
                        "expected_version": row["version"],
                        "worker_id": worker_id,
                        "occurred_at": now,
                        "audit_id": self.id_factory(),
                        "event_id": self.id_factory(),
                        "receipt_id": self.id_factory(),
                        "response": response,
                        "expected_response_digest": digest_hex(response),
                    },
                )
            )
        return results


def completed_statuses() -> dict[MilestoneCode, MilestoneStatus]:
    return {code: MilestoneStatus.COMPLETED for code in MilestoneCode}
