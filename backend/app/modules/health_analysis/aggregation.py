from __future__ import annotations

from typing import Any

from app.modules.health_analysis.schemas import (
    DataCompleteness,
    HealthSummary,
    HealthSummaryProfile,
    HealthSummaryUser,
    HealthTrend,
    LatestIndicatorSummary,
    TrendPoint,
)


STANDARD_INDICATORS: dict[str, dict[str, str]] = {
    "systolic_bp": {"display_name": "收缩压", "category": "blood_pressure", "default_unit": "mmHg"},
    "diastolic_bp": {"display_name": "舒张压", "category": "blood_pressure", "default_unit": "mmHg"},
    "heart_rate": {"display_name": "心率", "category": "cardiovascular", "default_unit": "bpm"},
    "fasting_glucose": {"display_name": "空腹血糖", "category": "blood_glucose", "default_unit": "mmol/L"},
    "postprandial_glucose_2h": {"display_name": "餐后2小时血糖", "category": "blood_glucose", "default_unit": "mmol/L"},
    "hba1c": {"display_name": "糖化血红蛋白", "category": "blood_glucose", "default_unit": "%"},
    "total_cholesterol": {"display_name": "总胆固醇", "category": "blood_lipid", "default_unit": "mmol/L"},
    "triglyceride": {"display_name": "甘油三酯", "category": "blood_lipid", "default_unit": "mmol/L"},
    "hdl_c": {"display_name": "高密度脂蛋白胆固醇", "category": "blood_lipid", "default_unit": "mmol/L"},
    "ldl_c": {"display_name": "低密度脂蛋白胆固醇", "category": "blood_lipid", "default_unit": "mmol/L"},
    "weight": {"display_name": "体重", "category": "body_composition", "default_unit": "kg"},
    "bmi": {"display_name": "BMI", "category": "body_composition", "default_unit": "kg/m2"},
    "uric_acid": {"display_name": "尿酸", "category": "metabolism", "default_unit": "umol/L"},
    "spo2": {"display_name": "血氧饱和度", "category": "respiratory", "default_unit": "%"},
    "bone_density_t_score": {"display_name": "骨密度T值", "category": "bone_health", "default_unit": "T-score"},
}


def _get_value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _build_profile_summary(health_profile: Any | None) -> HealthSummaryProfile:
    if health_profile is None:
        return HealthSummaryProfile(exists=False)

    return HealthSummaryProfile(
        exists=True,
        gender=_get_value(health_profile, "gender"),
        birth_date=_get_value(health_profile, "birth_date"),
        height=_get_value(health_profile, "height"),
        weight=_get_value(health_profile, "weight"),
        blood_type=_get_value(health_profile, "blood_type"),
        has_medical_history=_has_value(_get_value(health_profile, "medical_history")),
        has_allergy_history=_has_value(_get_value(health_profile, "allergy_history")),
        has_family_history=_has_value(_get_value(health_profile, "family_history")),
        has_symptoms=_has_value(_get_value(health_profile, "symptoms")),
        smoking=_get_value(health_profile, "smoking"),
        drinking=_get_value(health_profile, "drinking"),
        sleep_quality=_get_value(health_profile, "sleep_quality"),
        bowel_urination=_get_value(health_profile, "bowel_urination"),
    )


def _build_latest_indicator_summary(indicator: Any) -> LatestIndicatorSummary | None:
    indicator_type = _get_value(indicator, "indicator_type")
    indicator_spec = STANDARD_INDICATORS.get(indicator_type)
    if indicator_spec is None:
        return None

    quality_flags = []
    unit = _get_value(indicator, "unit")
    if unit != indicator_spec["default_unit"]:
        quality_flags.append("unit_mismatch")

    return LatestIndicatorSummary(
        indicator_type=indicator_type,
        display_name=indicator_spec["display_name"],
        category=indicator_spec["category"],
        value=_get_value(indicator, "value"),
        unit=unit,
        source=_get_value(indicator, "source"),
        recorded_at=_get_value(indicator, "recorded_at"),
        created_at=_get_value(indicator, "created_at"),
        quality_flags=quality_flags,
    )


def build_health_summary(*, user: Any, health_profile: Any | None, latest_indicators: list[Any]) -> HealthSummary:
    profile = _build_profile_summary(health_profile)

    latest_summaries = []
    for indicator in latest_indicators:
        summary = _build_latest_indicator_summary(indicator)
        if summary is not None:
            latest_summaries.append(summary)

    present_indicator_types = {indicator.indicator_type for indicator in latest_summaries}
    missing_indicator_types = [
        indicator_type for indicator_type in STANDARD_INDICATORS if indicator_type not in present_indicator_types
    ]
    indicator_updated_at = max((indicator.recorded_at for indicator in latest_summaries), default=None)

    return HealthSummary(
        user=HealthSummaryUser(
            user_id=_get_value(user, "id"),
            status=_get_value(user, "status"),
            tenant_id=_get_value(user, "tenant_id"),
        ),
        profile=profile,
        latest_indicators=latest_summaries,
        indicator_updated_at=indicator_updated_at,
        data_completeness=DataCompleteness(
            profile_completed=health_profile is not None
            and _has_value(_get_value(health_profile, "gender"))
            and _has_value(_get_value(health_profile, "birth_date")),
            indicator_count=len(present_indicator_types),
            standard_indicator_count=len(STANDARD_INDICATORS),
            missing_indicator_types=missing_indicator_types,
        ),
    )


def build_health_trend(*, indicator_type: str, indicators: list[Any]) -> HealthTrend:
    indicator_spec = STANDARD_INDICATORS.get(indicator_type)
    if indicator_spec is None:
        raise ValueError("Unknown indicator_type")

    points = [
        TrendPoint(
            value=_get_value(indicator, "value"),
            recorded_at=_get_value(indicator, "recorded_at"),
            source=_get_value(indicator, "source"),
        )
        for indicator in sorted(indicators, key=lambda item: _get_value(item, "recorded_at"))
    ]

    return HealthTrend(
        indicator_type=indicator_type,
        display_name=indicator_spec["display_name"],
        category=indicator_spec["category"],
        unit=indicator_spec["default_unit"],
        points=points,
    )
