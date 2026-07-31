from __future__ import annotations

from fastapi import HTTPException

from app.modules.health_analysis.ai_contract_builder import build_ai_health_input_contract
from app.modules.health_analysis.ai_contract_validator import validate_ai_health_input_contract
from app.modules.health_analysis.repository import get_summary_profile, get_summary_user
from app.modules.health_analysis.schemas import AIHealthInputContract, HealthTrend
from app.modules.health_analysis.service import get_health_summary, get_health_trend


async def _get_trends_for_contract(session, *, user_id: int, indicator_types: list[str]) -> list[HealthTrend]:
    trends = []
    for indicator_type in indicator_types:
        trend = await get_health_trend(session, user_id=user_id, indicator_type=indicator_type)
        if trend is not None:
            trends.append(trend)
    return trends


async def get_ai_health_input_contract(session, *, user_id: int) -> AIHealthInputContract:
    user = await get_summary_user(session, user_id=user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    health_profile = await get_summary_profile(session, user_id=user_id)
    health_summary = await get_health_summary(session, user_id=user_id)
    indicator_types = [indicator.indicator_type for indicator in health_summary.latest_indicators]
    health_trends = await _get_trends_for_contract(session, user_id=user_id, indicator_types=indicator_types)

    contract = build_ai_health_input_contract(
        user=user,
        health_profile=health_profile,
        health_summary=health_summary,
        health_trends=health_trends,
    )
    if not validate_ai_health_input_contract(contract):
        raise HTTPException(status_code=500, detail="Invalid AI input contract")
    return contract
