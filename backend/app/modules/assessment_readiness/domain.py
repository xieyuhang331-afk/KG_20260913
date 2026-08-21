from __future__ import annotations

from dataclasses import dataclass


def _codes(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


@dataclass(frozen=True, slots=True)
class AssessmentReadinessInputs:
    authorization_complete: bool
    profile_current: bool
    policy_available: bool
    missing_indicator_codes: tuple[str, ...]
    expired_indicator_codes: tuple[str, ...]
    disputed_indicator_codes: tuple[str, ...]
    projection_current: bool


@dataclass(frozen=True, slots=True)
class AssessmentReadinessResult:
    status: str
    reason_codes: tuple[str, ...]
    missing_indicator_codes: tuple[str, ...]
    expired_indicator_codes: tuple[str, ...]
    disputed_indicator_codes: tuple[str, ...]


def evaluate_readiness(inputs: AssessmentReadinessInputs) -> AssessmentReadinessResult:
    missing = _codes(inputs.missing_indicator_codes)
    expired = _codes(inputs.expired_indicator_codes)
    disputed = _codes(inputs.disputed_indicator_codes)
    reasons: list[str] = []
    if not inputs.authorization_complete:
        reasons.append("AUTHORIZATION_INCOMPLETE")
    if not inputs.profile_current:
        reasons.append("PROFILE_MISSING_OR_STALE")
    if not inputs.policy_available:
        reasons.append("POLICY_UNAVAILABLE")
    if missing:
        reasons.append("INDICATOR_MISSING")
    if expired:
        reasons.append("INDICATOR_EXPIRED")
    if disputed:
        status = "DISPUTED"
        reasons.insert(0, "INDICATOR_DISPUTED")
    elif reasons:
        status = "DATA_INSUFFICIENT"
    elif not inputs.projection_current:
        status = "DATA_SYNC_PENDING"
        reasons.append("PROJECTION_SYNC_PENDING")
    else:
        status = "ASSESSMENT_READY"
    return AssessmentReadinessResult(
        status=status,
        reason_codes=tuple(reasons),
        missing_indicator_codes=missing,
        expired_indicator_codes=expired,
        disputed_indicator_codes=disputed,
    )
