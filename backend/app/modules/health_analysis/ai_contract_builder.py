from __future__ import annotations

from typing import Any

from app.modules.health_analysis.schemas import (
    AIDataCompleteness,
    AIHealthInputContract,
    AILatestIndicator,
    AIProfileContext,
    AISafetyPolicy,
    AISourceRefs,
    AITrendContext,
    AITrendPoint,
    AITrendWindow,
    AIUserContext,
    HealthSummary,
    HealthTrend,
)


def _get_value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _build_user_context(*, user: Any, health_summary: HealthSummary) -> AIUserContext:
    return AIUserContext(
        user_id=_get_value(user, "id", health_summary.user.user_id),
        status=_get_value(user, "status", health_summary.user.status),
        tenant_id=_get_value(user, "tenant_id", health_summary.user.tenant_id),
    )


def _build_profile_context(health_summary: HealthSummary) -> AIProfileContext:
    profile = health_summary.profile
    return AIProfileContext(
        gender=profile.gender,
        birth_date=profile.birth_date,
        height=profile.height,
        weight=profile.weight,
        blood_type=profile.blood_type,
        has_medical_history=profile.has_medical_history,
        has_allergy_history=profile.has_allergy_history,
        has_family_history=profile.has_family_history,
        has_symptoms=profile.has_symptoms,
    )


def _build_latest_indicators(health_summary: HealthSummary) -> list[AILatestIndicator]:
    return [
        AILatestIndicator(
            indicator_type=indicator.indicator_type,
            display_name=indicator.display_name,
            category=indicator.category,
            value=indicator.value,
            unit=indicator.unit,
            source=indicator.source,
            recorded_at=indicator.recorded_at,
        )
        for indicator in health_summary.latest_indicators
    ]


def _build_trend_context(health_trend: HealthTrend) -> AITrendContext | None:
    if not health_trend.points:
        return None

    recorded_at_values = [point.recorded_at for point in health_trend.points]
    start_at = min(recorded_at_values)
    end_at = max(recorded_at_values)

    return AITrendContext(
        indicator_type=health_trend.indicator_type,
        display_name=health_trend.display_name,
        category=health_trend.category,
        unit=health_trend.unit,
        window=AITrendWindow(
            start_at=start_at,
            end_at=end_at,
            days=(end_at.date() - start_at.date()).days,
        ),
        points=[
            AITrendPoint(
                value=point.value,
                recorded_at=point.recorded_at,
                source=point.source,
            )
            for point in health_trend.points
        ],
    )


def _build_trend_contexts(health_trends: list[HealthTrend]) -> list[AITrendContext]:
    trend_contexts = []
    for health_trend in health_trends:
        trend_context = _build_trend_context(health_trend)
        if trend_context is not None:
            trend_contexts.append(trend_context)
    return trend_contexts


def _build_data_completeness(health_summary: HealthSummary) -> AIDataCompleteness:
    completeness = health_summary.data_completeness
    return AIDataCompleteness(
        profile_completed=completeness.profile_completed,
        indicator_count=completeness.indicator_count,
        standard_indicator_count=completeness.standard_indicator_count,
        missing_indicator_types=completeness.missing_indicator_types,
    )


def _build_source_refs() -> AISourceRefs:
    return AISourceRefs(
        source_tables=["user", "health_profile", "health_indicator"],
        summary_source="HealthSummary",
        trend_source="HealthTrend",
        indicator_ids=[],
    )


def _build_safety_policy() -> AISafetyPolicy:
    return AISafetyPolicy(
        no_diagnosis=True,
        no_prescription=True,
        no_treatment_plan=True,
        no_risk_prediction=True,
        no_health_score=True,
    )


def build_ai_health_input_contract(
    *,
    user: Any,
    health_profile: Any | None,
    health_summary: HealthSummary,
    health_trends: list[HealthTrend],
) -> AIHealthInputContract:
    return AIHealthInputContract(
        user_context=_build_user_context(user=user, health_summary=health_summary),
        profile_context=_build_profile_context(health_summary),
        latest_indicators=_build_latest_indicators(health_summary),
        trend_context=_build_trend_contexts(health_trends),
        data_completeness=_build_data_completeness(health_summary),
        source_refs=_build_source_refs(),
        safety_policy=_build_safety_policy(),
    )
