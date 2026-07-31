from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.modules.health_analysis.schemas import AIHealthInputContract


REQUIRED_CONTRACT_VERSION = "f004.ai_input.v1"
REQUIRED_SAFETY_POLICY_FLAGS = (
    "no_diagnosis",
    "no_prescription",
    "no_treatment_plan",
    "no_risk_prediction",
    "no_health_score",
)
FORBIDDEN_CONTRACT_FIELDS = {
    "health_score",
    "risk_level",
    "diagnosis",
    "recommendation",
    "prescription",
}


def _to_plain_data(contract: AIHealthInputContract | dict[str, Any]) -> dict[str, Any]:
    if isinstance(contract, AIHealthInputContract):
        return contract.model_dump(mode="json")
    return contract


def _contains_forbidden_field(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in FORBIDDEN_CONTRACT_FIELDS for key in value):
            return True
        return any(_contains_forbidden_field(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_field(item) for item in value)
    return False


def _has_valid_required_indicator_fields(contract: AIHealthInputContract) -> bool:
    return all(
        indicator.indicator_type and indicator.value is not None and indicator.unit
        for indicator in contract.latest_indicators
    )


def _has_valid_required_trend_fields(contract: AIHealthInputContract) -> bool:
    return all(trend.indicator_type and trend.points is not None for trend in contract.trend_context)


def _has_required_safety_policy(contract: AIHealthInputContract) -> bool:
    return all(getattr(contract.safety_policy, field) is True for field in REQUIRED_SAFETY_POLICY_FLAGS)


def validate_ai_health_input_contract(contract: AIHealthInputContract | dict[str, Any]) -> bool:
    data = _to_plain_data(contract)
    if _contains_forbidden_field(data):
        return False

    try:
        validated_contract = AIHealthInputContract.model_validate(data)
    except (TypeError, ValidationError):
        return False

    if validated_contract.contract_version != REQUIRED_CONTRACT_VERSION:
        return False
    if not _has_valid_required_indicator_fields(validated_contract):
        return False
    if not _has_valid_required_trend_fields(validated_contract):
        return False
    return _has_required_safety_policy(validated_contract)
