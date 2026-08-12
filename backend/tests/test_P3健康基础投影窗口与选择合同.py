from datetime import UTC, datetime
from decimal import Decimal
import json

import pytest

from app.modules.health_projection.domain import (
    HealthCurrentFact,
    ProjectionSourceInvalid,
    build_health_projection_rows,
    business_window,
    select_window_winner,
)
from app.modules.health_fact.domain import CATALOG_V1


PUBLIC_VECTOR_KEY = bytes(range(32))


def test_Catalog_V1精确包含15项NUMERIC及canonical单位():
    assert dict(CATALOG_V1) == {
        "systolic_bp": "mmHg",
        "diastolic_bp": "mmHg",
        "heart_rate": "bpm",
        "fasting_glucose": "mmol/L",
        "postprandial_glucose_2h": "mmol/L",
        "hba1c": "%",
        "total_cholesterol": "mmol/L",
        "triglyceride": "mmol/L",
        "hdl_c": "mmol/L",
        "ldl_c": "mmol/L",
        "weight": "kg",
        "bmi": "kg/m2",
        "uric_acid": "umol/L",
        "spo2": "%",
        "bone_density_t_score": "T-score",
    }


def _fact(fid, source, measured, received, *, code="systolic_bp", value="120.50", unit="mmHg"):
    return HealthCurrentFact(fid, 42, code, Decimal(value), unit, measured, received, source)


def test_上海自然日零点属于下一日():
    start, end, day = business_window(datetime(2026, 8, 11, 16, 0, tzinfo=UTC))
    assert day.isoformat() == "2026-08-12"
    assert start == datetime(2026, 8, 11, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 12, 16, 0, tzinfo=UTC)


def test_winner按来源测量接收和id依次裁决():
    measured = datetime(2026, 8, 12, 0, 30, tzinfo=UTC)
    earlier = datetime(2026, 8, 12, 0, 29, tzinfo=UTC)
    facts = [
        _fact(9, "APP", measured, measured),
        _fact(8, "REPORT", measured, measured),
        _fact(7, "STORE", measured, measured),
        _fact(1, "DEVICE", earlier, measured),
        _fact(2, "DEVICE", measured, earlier),
        _fact(3, "DEVICE", measured, measured),
        _fact(4, "DEVICE", measured, measured),
    ]
    assert select_window_winner(facts).id == 4


def test_全部current事实保留并生成唯一日选择():
    measured = datetime(2026, 8, 12, 0, 30, tzinfo=UTC)
    rows, selections = build_health_projection_rows(
        facts=[_fact(1, "APP", measured, measured), _fact(2, "DEVICE", measured, measured)],
        digest_key=PUBLIC_VECTOR_KEY,
    )
    assert [row.fact_id for row in rows] == [1, 2]
    assert len(selections) == 1
    assert selections[0].winner_fact_id == 2


def test_健康事实和选择摘要公开向量逐字节匹配():
    fact = _fact(
        501,
        "DEVICE",
        datetime(2026, 8, 12, 0, 30, tzinfo=UTC),
        datetime(2026, 8, 12, 0, 31, tzinfo=UTC),
    )
    rows, selections = build_health_projection_rows(facts=[fact], digest_key=PUBLIC_VECTOR_KEY)
    fact_payload = {
        "fact_id": 501,
        "subject_user_id": 42,
        "indicator_code": "systolic_bp",
        "numeric_value": "120.50",
        "unit": "mmHg",
        "measured_at": "2026-08-12T00:30:00.000000Z",
        "received_at": "2026-08-12T00:31:00.000000Z",
        "source_type": "DEVICE",
        "business_day": "2026-08-12",
        "window_start_utc": "2026-08-11T16:00:00.000000Z",
        "window_end_utc": "2026-08-12T16:00:00.000000Z",
    }
    selection_payload = {
        "subject_user_id": 42,
        "indicator_code": "systolic_bp",
        "business_day": "2026-08-12",
        "winner_fact_id": 501,
        "rule_version": "health-daily-selection-v1",
    }
    assert json.dumps(fact_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == (
        '{"business_day":"2026-08-12","fact_id":501,"indicator_code":"systolic_bp",'
        '"measured_at":"2026-08-12T00:30:00.000000Z","numeric_value":"120.50",'
        '"received_at":"2026-08-12T00:31:00.000000Z","source_type":"DEVICE",'
        '"subject_user_id":42,"unit":"mmHg","window_end_utc":"2026-08-12T16:00:00.000000Z",'
        '"window_start_utc":"2026-08-11T16:00:00.000000Z"}'
    )
    assert json.dumps(selection_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == (
        '{"business_day":"2026-08-12","indicator_code":"systolic_bp",'
        '"rule_version":"health-daily-selection-v1","subject_user_id":42,"winner_fact_id":501}'
    )
    assert rows[0].row_digest.upper() == "0D832AA7F898CC4FCAA5FB3635A661E197AEF3972AE32EA2AEF46816C511DC9A"
    assert selections[0].selection_digest.upper() == "A4BE8530CA978E697F93F8B8CA9FCF28CA946F46A9EFDE49A757820ED5B96D1E"


@pytest.mark.parametrize(
    "fact",
    [
        _fact(1, "UNKNOWN", datetime.now(UTC), datetime.now(UTC)),
        _fact(1, "APP", datetime.now(UTC), datetime.now(UTC), code="not-approved"),
        _fact(1, "APP", datetime.now(UTC), datetime.now(UTC), unit="kPa"),
        _fact(1, "APP", datetime.now(UTC), datetime.now(UTC), value="120.501"),
        _fact(1, "APP", datetime(2026, 1, 1), datetime.now(UTC)),
    ],
)
def test_未知来源第16项错误单位精度和naive时间均拒绝(fact):
    with pytest.raises(ProjectionSourceInvalid):
        build_health_projection_rows(facts=[fact], digest_key=PUBLIC_VECTOR_KEY)
