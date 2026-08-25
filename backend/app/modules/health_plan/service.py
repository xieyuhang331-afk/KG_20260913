from __future__ import annotations

import asyncio
import base64
import binascii
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import hmac
import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping
from uuid import UUID

from app.core.config import get_settings
from app.modules.health_plan.domain import EligibilityFacts, deterministic_plan_content, eligibility_result
from app.modules.health_plan.schemas import (
    PlanGenerationEligibilityDTO,
    PlanReviewDetailDTO,
    PlanReviewListItemDTO,
    ReviewHistoryEntryDTO,
    ReviewPlanSummaryDTO,
    ReviewSummaryDTO,
    review_reason_codes_match,
)


class HealthPlanError(RuntimeError):
    pass


class CommitOutcome(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


_REVIEW_CURSOR_DOMAIN = b"slice6-health-plan-review-cursor:v1:\x00"
_REVIEW_STATUSES = {"PENDING", "CLAIMED", "APPROVED", "NEEDS_CORRECTION", "REJECTED"}


def _review_cursor_key() -> bytes:
    return get_settings().jwt_secret_key.encode("utf-8")


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def encode_review_cursor(review_id: UUID, status: str | None) -> str:
    raw = json.dumps(
        {"review_id": str(review_id), "status": status, "v": 1},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload = _base64url(raw)
    signature = hmac.new(_review_cursor_key(), _REVIEW_CURSOR_DOMAIN + raw, sha256).digest()
    encoded_signature = _base64url(signature)
    return f"{payload}.{encoded_signature}"


def decode_review_cursor(cursor: str | None, status: str | None) -> UUID | None:
    if cursor is None:
        return None
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
        expected = hmac.new(_review_cursor_key(), _REVIEW_CURSOR_DOMAIN + raw, sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(raw)
        if set(payload) != {"review_id", "status", "v"} or payload["v"] != 1:
            raise ValueError
        if payload["status"] != status or (status is not None and status not in _REVIEW_STATUSES):
            raise ValueError
        review_id = UUID(payload["review_id"])
        if review_id.version != 7:
            raise ValueError
        return review_id
    except (
        AttributeError,
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise HealthPlanError("INVALID_REQUEST") from None


def _review_plan_binding(review: Mapping[str, object], plan: Mapping[str, object]) -> None:
    if (
        UUID(str(review.get("plan_id"))) != UUID(str(plan.get("plan_id")))
        or UUID(str(review.get("service_case_id"))) != UUID(str(plan.get("service_case_id")))
        or int(review.get("plan_version_no", 0)) != int(plan.get("version_no", -1))
    ):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise HealthPlanError("DEPENDENCY_UNAVAILABLE") from None
    else:
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    if parsed.utcoffset() is None:
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    return parsed.astimezone(timezone.utc)


def _review_postimage(review: Mapping[str, object]) -> tuple[object, ...]:
    try:
        return (
            UUID(str(review["review_id"])),
            UUID(str(review["request_id"])),
            UUID(str(review["plan_id"])),
            UUID(str(review["service_case_id"])),
            str(review["status"]),
            int(review["plan_version_no"]),
            _timestamp(review.get("claimed_at")),
            _timestamp(review.get("decided_at")),
            int(review["version"]),
        )
    except (KeyError, TypeError, ValueError):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE") from None


def _plan_postimage(plan: Mapping[str, object]) -> dict[str, object]:
    fields = {
        "plan_id",
        "service_case_id",
        "subject_member_id",
        "tenant_id",
        "version_no",
        "status",
        "template_code",
        "template_version",
        "overall_risk_level",
        "created_at",
        "updated_at",
        "version",
        "module_summaries",
        "goals",
        "stages",
        "milestones",
        "sop_items",
        "contraindication_codes",
        "user_message_codes",
        "therapist_action_codes",
        "review_summary",
        "user_decision_summary",
        "explanations",
    }
    if not fields.issubset(plan):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    return {field: _json_value(plan[field]) for field in fields}


def _review_summary_matches(
    review: Mapping[str, object], plan: Mapping[str, object]
) -> bool:
    raw_summary = plan.get("review_summary")
    if not isinstance(raw_summary, Mapping):
        return False
    try:
        summary = ReviewSummaryDTO.model_validate(raw_summary)
    except (TypeError, ValueError):
        return False
    status = str(review.get("status"))
    claimed_at = _timestamp(review.get("claimed_at"))
    decided_at = _timestamp(review.get("decided_at"))
    decision_codes = tuple(summary.decision_codes)
    if summary.status != status or summary.decided_at != decided_at:
        return False
    if status == "PENDING":
        return claimed_at is None and decided_at is None and not decision_codes
    if status == "CLAIMED":
        return claimed_at is not None and decided_at is None and not decision_codes
    if status not in {"APPROVED", "NEEDS_CORRECTION", "REJECTED"}:
        return False
    return (
        claimed_at is not None
        and decided_at is not None
        and review_reason_codes_match(status, decision_codes)
    )


def _review_plan_status_matches(review_status: str, plan_status: str) -> bool:
    allowed = {
        "PENDING": {"IN_REVIEW"},
        "CLAIMED": {"IN_REVIEW"},
        "NEEDS_CORRECTION": {"SUPERSEDED"},
        "REJECTED": {"REJECTED"},
        "APPROVED": {
            "USER_DECISION_PENDING",
            "NEEDS_EXPLANATION",
            "ACTIVE",
            "DECLINED",
            "SUPERSEDED",
        },
    }
    return plan_status in allowed.get(review_status, set())


def review_list_item(
    review: Mapping[str, object], plan: Mapping[str, object]
) -> PlanReviewListItemDTO:
    _review_plan_binding(review, plan)
    return PlanReviewListItemDTO.model_validate(
        {
            **{key: review.get(key) for key in PlanReviewListItemDTO.model_fields},
            "overall_risk_level": plan.get("overall_risk_level"),
        }
    )


_VERSION_FIELDS = {
    "template_code": "TEMPLATE_CHANGED",
    "template_version": "TEMPLATE_CHANGED",
    "overall_risk_level": "OVERALL_RISK_CHANGED",
    "module_summaries": "MODULE_SUMMARIES_CHANGED",
    "goals": "GOALS_CHANGED",
    "stages": "STAGES_CHANGED",
    "milestones": "MILESTONES_CHANGED",
    "sop_items": "SOP_ITEMS_CHANGED",
    "contraindication_codes": "CONTRAINDICATIONS_CHANGED",
    "user_message_codes": "USER_MESSAGES_CHANGED",
    "therapist_action_codes": "THERAPIST_ACTIONS_CHANGED",
}


def _version_diff_codes(
    plan: Mapping[str, object], previous_plan: Mapping[str, object] | None
) -> tuple[str, ...]:
    if previous_plan is None:
        return ("INITIAL_VERSION",)
    if (
        UUID(str(previous_plan.get("service_case_id")))
        != UUID(str(plan.get("service_case_id")))
        or int(previous_plan.get("version_no", 0)) >= int(plan.get("version_no", 0))
    ):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    return tuple(
        sorted(
            {
                code
                for field, code in _VERSION_FIELDS.items()
                if previous_plan.get(field) != plan.get(field)
            }
        )
    )


def review_detail_dto(
    review: Mapping[str, object],
    plan: Mapping[str, object],
    previous_plan: Mapping[str, object] | None,
    *,
    confirmed_review: Mapping[str, object],
    confirmed_plan: Mapping[str, object],
    assessment_context: Mapping[str, object],
    plan_history: tuple[Mapping[str, object], ...],
    review_history: tuple[Mapping[str, object], ...],
) -> PlanReviewDetailDTO:
    if _review_postimage(review) != _review_postimage(confirmed_review):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    if _plan_postimage(plan) != _plan_postimage(confirmed_plan):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    item = review_list_item(review, plan)
    plan_module_summaries = tuple(
        sorted(
            f"{module['module_code']}_{module['risk_level']}"
            for module in plan.get("module_summaries", ())
        )
    )
    evidence = assessment_context.get("input_evidence")
    assessment_modules = assessment_context.get("module_results")
    if (
        not isinstance(evidence, Mapping)
        or not isinstance(assessment_modules, (list, tuple))
        or evidence.get("profile_revision_ref") is None
        or evidence.get("watermark_status") != "CURRENT"
        or assessment_context.get("overall_risk") != plan.get("overall_risk_level")
    ):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    try:
        UUID(str(evidence["profile_revision_ref"]))
        assessment_module_summaries = tuple(
            sorted(
                f"{module['module_code']}_{module['risk_level']}"
                for module in assessment_modules
                if isinstance(module, Mapping)
            )
        )
    except (KeyError, TypeError, ValueError):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE") from None
    if (
        len(assessment_module_summaries) != len(assessment_modules)
        or assessment_module_summaries != plan_module_summaries
    ):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    review_summary = plan.get("review_summary")
    user_summary = plan.get("user_decision_summary")
    if not isinstance(review_summary, Mapping) or not isinstance(user_summary, Mapping):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    case_id = UUID(str(plan["service_case_id"]))
    current_version = int(plan["version_no"])
    plans_by_version: dict[int, Mapping[str, object]] = {}
    for history_plan in plan_history:
        version_no = int(history_plan.get("version_no", 0))
        if (
            UUID(str(history_plan.get("service_case_id"))) != case_id
            or version_no < 1
            or version_no > current_version
            or version_no in plans_by_version
        ):
            raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
        plans_by_version[version_no] = history_plan
    if set(plans_by_version) != set(range(1, current_version + 1)):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    reviews_by_version: dict[int, Mapping[str, object]] = {}
    for history_review in review_history:
        version_no = int(history_review.get("plan_version_no", 0))
        if (
            UUID(str(history_review.get("service_case_id"))) != case_id
            or version_no not in plans_by_version
            or version_no in reviews_by_version
        ):
            raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
        reviews_by_version[version_no] = history_review
    if set(reviews_by_version) != set(plans_by_version):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    current_history_review = reviews_by_version[current_version]
    if _review_postimage(current_history_review) != _review_postimage(confirmed_review):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    if _plan_postimage(plans_by_version[current_version]) != _plan_postimage(confirmed_plan):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    history: list[ReviewHistoryEntryDTO] = []
    for version_no in sorted(plans_by_version):
        history_plan = plans_by_version[version_no]
        history_review = reviews_by_version[version_no]
        _review_plan_binding(history_review, history_plan)
        if not _review_summary_matches(history_review, history_plan) or not _review_plan_status_matches(
            str(history_review["status"]), str(history_plan["status"])
        ):
            raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
        history.append(
            ReviewHistoryEntryDTO(
                action="PLAN_GENERATED",
                plan_version_no=version_no,
                occurred_at=history_plan["created_at"],
            )
        )
        if history_review.get("claimed_at") is not None:
            history.append(
                ReviewHistoryEntryDTO(
                    action="REVIEW_CLAIMED",
                    plan_version_no=version_no,
                    occurred_at=history_review["claimed_at"],
                )
            )
        if history_review.get("decided_at") is not None:
            history.append(
                ReviewHistoryEntryDTO(
                    action="REVIEW_DECIDED",
                    plan_version_no=version_no,
                    occurred_at=history_review["decided_at"],
                )
            )
    plan_summary = ReviewPlanSummaryDTO(
        plan_status=plan["status"],
        template_code=plan["template_code"],
        template_version=plan["template_version"],
        module_summaries=plan_module_summaries,
        goals=tuple(plan.get("goals", ())),
        stages=tuple(plan.get("stages", ())),
        milestones=tuple(plan.get("milestones", ())),
        sop_items=tuple(plan.get("sop_items", ())),
        contraindication_codes=tuple(plan.get("contraindication_codes", ())),
        user_message_codes=tuple(plan.get("user_message_codes", ())),
        therapist_action_codes=tuple(plan.get("therapist_action_codes", ())),
    )
    return PlanReviewDetailDTO(
        **item.model_dump(),
        customer_summary_codes=(
            "ASSESSMENT_INPUT_CURRENT",
            "PROFILE_CONTEXT_INCLUDED",
        ),
        assessment_summary_codes=(
            f"OVERALL_RISK_{plan['overall_risk_level']}",
            *assessment_module_summaries,
        ),
        plan_summary=plan_summary,
        version_diff_codes=_version_diff_codes(plan, previous_plan),
        history=tuple(history),
        reason_codes=tuple(review_summary.get("decision_codes") or ()),
        user_decision=user_summary.get("decision"),
    )


def _json_value(value: object) -> object:
    if is_dataclass(value):
        return _json_value(asdict(value))
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return value.isoformat()
    if type(value) is Decimal:
        return str(value)
    if type(value) is bytes:
        return value.hex()
    if isinstance(value, (dict, MappingProxyType)):
        return {str(key): _json_value(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_json_value(item) for item in value]
    return value


def canonical_json(value: object) -> bytes:
    return json.dumps(
        _json_value(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def digest_hex(value: object) -> str:
    return sha256(canonical_json(value)).hexdigest()


def _authority_facts(authority: Mapping[str, object]) -> EligibilityFacts:
    return EligibilityFacts(
        tenant_service_ready=authority.get("tenant_service_ready") is True,
        service_case_current=authority.get("service_case_current") is True,
        consent_current=authority.get("consent_current") is True,
        primary_therapist_current=authority.get("primary_therapist_current") is True,
        assembly_ready=authority.get("assembly_ready") is True,
        assessment_completed=authority.get("assessment_completed") is True,
        assessment_disputed=authority.get("assessment_disputed") is True,
        assessment_superseded=authority.get("assessment_superseded") is True,
        high_risk_blocking=authority.get("high_risk_blocking") is True,
        published_template_available=authority.get("published_template_available") is True,
        active_generation_exists=authority.get("active_generation_exists") is True,
        active_plan_conflict=authority.get("active_plan_conflict") is True,
    )


def _uuid(value: object) -> UUID:
    result = value if type(value) is UUID else UUID(str(value))
    if result.version != 7:
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    return result


async def get_generation_eligibility(
    repository,
    *,
    service_case_id: UUID,
    actor_user_id: int,
    actor_role: str,
    evaluated_at: datetime,
) -> PlanGenerationEligibilityDTO:
    if actor_role not in {"org_admin", "org_operator"}:
        raise HealthPlanError("FORBIDDEN")
    authority = await repository.generation_authority(service_case_id, actor_user_id, actor_role)
    if authority is None:
        raise HealthPlanError("SERVICE_CASE_NOT_FOUND")
    result = eligibility_result(_authority_facts(authority))
    return PlanGenerationEligibilityDTO(
        service_case_id=_uuid(authority["service_case_id"]),
        eligible=result.eligible,
        blocking_codes=result.blocking_codes,
        current_assessment_id=(
            _uuid(authority["assessment_id"]) if authority.get("assessment_id") else None
        ),
        assessment_version=(
            int(authority["assessment_version"]) if authority.get("assessment_version") else None
        ),
        published_template_version_id=(
            _uuid(authority["template_version_id"])
            if authority.get("template_version_id")
            else None
        ),
        active_generation_request_id=(
            _uuid(authority["active_generation_request_id"])
            if authority.get("active_generation_request_id")
            else None
        ),
        active_plan_id=_uuid(authority["active_plan_id"]) if authority.get("active_plan_id") else None,
        expected_service_case_version=int(authority["service_case_version"]),
        evaluated_at=evaluated_at,
    )


async def request_plan_generation(
    repository,
    *,
    service_case_id: UUID,
    expected_service_case_version: int,
    actor_user_id: int,
    actor_role: str,
    idempotency_key: str,
    now: datetime,
    id_factory: Callable[[], UUID],
) -> dict:
    request_identity = {
        "operation": "PLAN_GENERATION_REQUEST",
        "service_case_id": str(service_case_id),
        "expected_service_case_version": expected_service_case_version,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
    }
    request_digest = digest_hex(request_identity)
    replay = await repository.generation_replay(actor_user_id, idempotency_key, request_digest)
    if replay is not None:
        return replay

    eligibility = await get_generation_eligibility(
        repository,
        service_case_id=service_case_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        evaluated_at=now,
    )
    if eligibility.expected_service_case_version != expected_service_case_version:
        raise HealthPlanError("STALE_VERSION")
    if not eligibility.eligible:
        raise HealthPlanError(eligibility.blocking_codes[0])

    authority = await repository.generation_authority(service_case_id, actor_user_id, actor_role)
    if authority is None:
        raise HealthPlanError("SERVICE_CASE_NOT_FOUND")
    if int(authority["service_case_version"]) != expected_service_case_version:
        raise HealthPlanError("STALE_VERSION")

    request_id, audit_id, event_id, receipt_id = (id_factory() for _ in range(4))
    response = {
        "request_id": request_id,
        "service_case_id": service_case_id,
        "status": "REQUESTED",
        "current_plan_id": None,
        "current_plan_version": None,
        "failure_code": None,
        "created_at": now,
        "updated_at": now,
        "version": 1,
    }
    payload = {
        "request_id": request_id,
        "service_case_id": service_case_id,
        "subject_member_id": _uuid(authority["subject_member_id"]),
        "tenant_id": int(authority["tenant_id"]),
        "assessment_id": _uuid(authority["assessment_id"]),
        "assessment_version": int(authority["assessment_version"]),
        "assembly_id": _uuid(authority["assembly_id"]),
        "template_version_id": _uuid(authority["template_version_id"]),
        "template_version": int(authority["template_version"]),
        "expected_service_case_version": expected_service_case_version,
        "initiated_by": actor_user_id,
        "initiated_role": actor_role,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "authority_digest": digest_hex(
            {
                key: str(value) if type(value) is UUID else value
                for key, value in sorted(authority.items())
            }
        ),
        "audit_id": audit_id,
        "event_id": event_id,
        "receipt_id": receipt_id,
        "created_at": now,
        "response": response,
        "postimage_digest": digest_hex(
            {
                key: str(value) if type(value) in {UUID, datetime} else value
                for key, value in response.items()
            }
        ),
    }
    return await repository.create_generation_request(payload)


def generate_plan_content(
    template: Mapping[str, object], module_results: Mapping[str, str]
) -> tuple[Mapping[str, object], str]:
    content = deterministic_plan_content(template, module_results)
    return content, digest_hex(dict(content))


async def govern_template(
    repository,
    *,
    operation: str,
    template_version_id: UUID,
    template_payload: Mapping[str, object] | None,
    expected_version: int | None,
    actor_user_id: int,
    actor_role: str,
    idempotency_key: str,
    now: datetime,
    id_factory: Callable[[], UUID],
) -> dict:
    if operation not in {"CREATE", "PUBLISH", "RETIRE"}:
        raise HealthPlanError("INVALID_REQUEST")
    operation_name = f"PLAN_TEMPLATE_{operation}"
    request = {
        "operation": operation,
        "template": dict(template_payload or {}),
        "expected_version": expected_version,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
    }
    if operation != "CREATE":
        request["template_version_id"] = template_version_id
    request_digest = digest_hex(request)
    replay = await repository.mutation_replay(
        actor_user_id, operation_name, idempotency_key, request_digest
    )
    if replay is not None:
        return replay

    if operation == "CREATE":
        if template_payload is None:
            raise HealthPlanError("INVALID_REQUEST")
        content = {
            key: _json_value(template_payload[key])
            for key in (
                "applicable_modules",
                "goals_by_module",
                "stage_codes",
                "milestone_codes",
                "sop_codes",
                "contraindication_codes",
                "user_message_codes",
                "therapist_action_codes",
            )
        }
        response = {
            "template_version_id": template_version_id,
            "template_code": template_payload["template_code"],
            "version_no": await repository.next_template_version(
                str(template_payload["template_code"])
            ),
            "status": "DRAFT",
            "content": content,
            "medical_approval_ref": template_payload["medical_approval_ref"],
            "created_at": now,
            "published_at": None,
            "retired_at": None,
            "version": 1,
        }
    else:
        current = await repository.template_detail(template_version_id)
        if current is None:
            raise HealthPlanError("PLAN_NOT_FOUND")
        if expected_version is None or int(current["version"]) != expected_version:
            raise HealthPlanError("STALE_VERSION")
        required_status = "DRAFT" if operation == "PUBLISH" else "PUBLISHED"
        if current["status"] != required_status:
            raise HealthPlanError("STALE_VERSION")
        response = {
            "template_version_id": template_version_id,
            "template_code": current["template_code"],
            "version_no": int(current["version_no"]),
            "status": "PUBLISHED" if operation == "PUBLISH" else "RETIRED",
            "content": current["content"],
            "medical_approval_ref": current["medical_approval_ref"],
            "created_at": current["created_at"],
            "published_at": now if operation == "PUBLISH" else current["published_at"],
            "retired_at": now if operation == "RETIRE" else current["retired_at"],
            "version": expected_version + 1,
        }

    audit_id, event_id, receipt_id = (id_factory() for _ in range(3))
    return await repository.govern_template(
        operation,
        {
            **dict(template_payload or {}),
            **(
                {"content_digest": digest_hex(response["content"])}
                if operation == "CREATE"
                else {}
            ),
            "template_version_id": template_version_id,
            "expected_version": expected_version,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "audit_id": audit_id,
            "event_id": event_id,
            "receipt_id": receipt_id,
            "occurred_at": now,
            "response": response,
            "postimage_digest": digest_hex(response),
        },
    )


def classify_commit_outcome(
    *, expected: Mapping[str, object], actual: Mapping[str, object] | None, preimage: Mapping[str, object] | None
) -> CommitOutcome:
    if actual is not None and dict(actual) == dict(expected):
        return CommitOutcome.COMMITTED
    if actual is None and preimage is not None:
        return CommitOutcome.NOT_COMMITTED
    return CommitOutcome.UNKNOWN


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
            raise RuntimeError("DEPENDENCY_UNAVAILABLE") from None
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN") from None


async def review_plan(
    repository,
    *,
    review_id: UUID,
    decision: str,
    reason_codes: tuple[str, ...],
    expected_version: int,
    actor_user_id: int,
    actor_role: str,
    idempotency_key: str,
    now: datetime,
    id_factory: Callable[[], UUID],
) -> dict:
    if actor_role != "expert":
        raise HealthPlanError("FORBIDDEN")
    if not review_reason_codes_match(decision, reason_codes):
        raise HealthPlanError("INVALID_REQUEST")
    operation = "PLAN_REVIEW_DECISION"
    request_digest = digest_hex(
        {
            "operation": operation,
            "review_id": review_id,
            "decision": decision,
            "reason_codes": reason_codes,
            "expected_version": expected_version,
            "actor_user_id": actor_user_id,
        }
    )
    replay = await repository.mutation_replay(
        actor_user_id, operation, idempotency_key, request_digest
    )
    if replay is not None:
        return replay
    audit_id, event_id, receipt_id = (id_factory() for _ in range(3))
    response = {"review_id": review_id, "status": decision, "version": expected_version + 1}
    payload = {
        "review_id": review_id,
        "decision": decision,
        "reason_codes": reason_codes,
        "expected_version": expected_version,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "audit_id": audit_id,
        "event_id": event_id,
        "receipt_id": receipt_id,
        "occurred_at": now,
        "response": response,
        "postimage_digest": digest_hex(response),
    }
    return await repository.transition_review("DECIDE", payload)


async def claim_plan_review(
    repository,
    *,
    review_id: UUID,
    expected_version: int,
    actor_user_id: int,
    actor_role: str,
    idempotency_key: str,
    now: datetime,
    id_factory: Callable[[], UUID],
) -> dict:
    if actor_role != "expert":
        raise HealthPlanError("FORBIDDEN")
    operation = "PLAN_REVIEW_CLAIM"
    request_digest = digest_hex(
        {
            "operation": operation,
            "review_id": review_id,
            "expected_version": expected_version,
            "actor_user_id": actor_user_id,
        }
    )
    replay = await repository.mutation_replay(
        actor_user_id, operation, idempotency_key, request_digest
    )
    if replay is not None:
        return replay
    audit_id, event_id, receipt_id = (id_factory() for _ in range(3))
    response = {
        "review_id": review_id,
        "status": "CLAIMED",
        "version": expected_version + 1,
    }
    return await repository.transition_review(
        "CLAIM",
        {
            "review_id": review_id,
            "expected_version": expected_version,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "audit_id": audit_id,
            "event_id": event_id,
            "receipt_id": receipt_id,
            "occurred_at": now,
            "response": response,
            "postimage_digest": digest_hex(response),
        },
    )


async def decide_plan(
    repository,
    *,
    plan_id: UUID,
    decision: str,
    expected_version: int,
    actor_user_id: int,
    actor_role: str,
    actor_context: str,
    proxy_grant_id: UUID | None,
    idempotency_key: str,
    now: datetime,
    id_factory: Callable[[], UUID],
) -> dict:
    if actor_role != "member" or actor_context not in {"AUTO", "SELF", "PROXY"}:
        raise HealthPlanError("USER_DECISION_FORBIDDEN")
    if actor_context == "PROXY" and proxy_grant_id is None:
        raise HealthPlanError("USER_DECISION_FORBIDDEN")
    statuses = {"ACCEPT": "ACTIVE", "NEEDS_EXPLANATION": "NEEDS_EXPLANATION", "DECLINE": "DECLINED"}
    if decision not in statuses:
        raise HealthPlanError("INVALID_REQUEST")
    operation = "PLAN_USER_DECISION"
    request_digest = digest_hex(
        {
            "operation": operation,
            "plan_id": plan_id,
            "decision": decision,
            "expected_version": expected_version,
            "actor_user_id": actor_user_id,
            "actor_context": actor_context,
            "proxy_grant_id": proxy_grant_id,
        }
    )
    replay = await repository.mutation_replay(
        actor_user_id, operation, idempotency_key, request_digest
    )
    if replay is not None:
        return replay
    decision_id, audit_id, event_id, receipt_id = (id_factory() for _ in range(4))
    response = {
        "decision_id": decision_id,
        "plan_id": plan_id,
        "decision": decision,
        "status": statuses[decision],
        "version": expected_version + 1,
    }
    payload = {
        "decision_id": decision_id,
        "plan_id": plan_id,
        "decision": decision,
        "expected_version": expected_version,
        "actor_user_id": actor_user_id,
        "actor_role": actor_role,
        "actor_context": actor_context,
        "proxy_grant_id": proxy_grant_id,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "audit_id": audit_id,
        "event_id": event_id,
        "receipt_id": receipt_id,
        "occurred_at": now,
        "response": response,
        "postimage_digest": digest_hex(response),
    }
    return await repository.decide_plan(payload)


async def explain_plan(
    repository,
    *,
    plan_id: UUID,
    explanation_codes: tuple[str, ...],
    expected_version: int,
    actor_user_id: int,
    actor_role: str,
    idempotency_key: str,
    now: datetime,
    id_factory: Callable[[], UUID],
) -> dict:
    if actor_role != "therapist" or not explanation_codes:
        raise HealthPlanError("FORBIDDEN")
    operation = "PLAN_EXPLANATION"
    request_digest = digest_hex(
        {
            "operation": operation,
            "plan_id": plan_id,
            "explanation_codes": explanation_codes,
            "expected_version": expected_version,
            "actor_user_id": actor_user_id,
        }
    )
    replay = await repository.mutation_replay(
        actor_user_id, operation, idempotency_key, request_digest
    )
    if replay is not None:
        return replay
    explanation_id, audit_id, event_id, receipt_id = (id_factory() for _ in range(4))
    response = {
        "plan_id": plan_id,
        "status": "USER_DECISION_PENDING",
        "version": expected_version + 1,
    }
    return await repository.explain_plan(
        {
            "plan_id": plan_id,
            "explanation_id": explanation_id,
            "explanation_codes": explanation_codes,
            "expected_version": expected_version,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "audit_id": audit_id,
            "event_id": event_id,
            "receipt_id": receipt_id,
            "occurred_at": now,
            "response": response,
            "postimage_digest": digest_hex(response),
        }
    )


async def execute_generation(
    repository,
    *,
    request_id: UUID,
    lease_owner: str,
    now: datetime,
    id_factory: Callable[[], UUID],
) -> dict:
    claimed = await repository.claim_generation(request_id, lease_owner)
    if claimed is None:
        raise HealthPlanError("ACTIVE_GENERATION_EXISTS")
    source = await repository.generation_input(request_id)
    if source is None:
        raise HealthPlanError("ASSESSMENT_INPUT_NOT_READY")
    template = source.get("template")
    module_results = source.get("module_results")
    if not isinstance(template, Mapping) or not isinstance(module_results, Mapping):
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    try:
        version_no = int(source.get("next_version_no", 1))
    except (TypeError, ValueError) as exc:
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE") from exc
    if version_no < 1:
        raise HealthPlanError("DEPENDENCY_UNAVAILABLE")
    supersedes_plan_id = (
        _uuid(source["supersedes_plan_id"])
        if source.get("supersedes_plan_id") is not None
        else None
    )
    content, content_digest = generate_plan_content(template, module_results)
    plan_id, review_id, audit_id, event_id = (id_factory() for _ in range(4))
    response = {
        "request_id": request_id,
        "status": "IN_REVIEW",
        "current_plan_id": plan_id,
        "current_plan_version": version_no,
        "failure_code": None,
        "updated_at": now,
    }
    return await repository.complete_generation(
        {
            "request_id": request_id,
            "plan_id": plan_id,
            "review_id": review_id,
            "audit_id": audit_id,
            "event_id": event_id,
            "version_no": version_no,
            "supersedes_plan_id": supersedes_plan_id,
            "content": dict(content),
            "content_digest": content_digest,
            "created_at": now,
            "response": response,
            "event_digest": digest_hex(
                {
                    "event_type": "HEALTH_PLAN_GENERATED",
                    "request_id": request_id,
                    "plan_id": plan_id,
                }
            ),
        }
    )
