import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal


def _key(value: int) -> str:
    return base64.b64encode(bytes([value]) * 32).decode("ascii")


def _source(**changes):
    from app.modules.health_fact_mapping.domain import HealthIndicatorSourceSnapshot

    values = dict(
        legacy_indicator_id=7,
        user_id=9,
        indicator_type="weight",
        value=Decimal("65.20"),
        unit="kg",
        source="APP",
        recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        created_at=datetime(2026, 8, 1, 0, 0, 1, tzinfo=timezone.utc),
        legacy_batch_id=None,
    )
    values.update(changes)
    return HealthIndicatorSourceSnapshot(**values)


def test_健康Legacy映射领域尚未实现():
    from app.modules.health_fact.domain import HealthFactDigestKeyring
    from app.modules.health_fact_mapping.domain import prepare_health_mapping

    keyring = HealthFactDigestKeyring.from_base64(
        current_key_id="k1", encoded_keys={"k1": _key(1)}
    )
    prepared = prepare_health_mapping(
        source=_source(),
        mapping_version=1,
        batch_id="00000000-0000-0000-0000-000000000001",
        keyring=keyring,
    )
    assert prepared.mapping.disposition == "MAPPED"
    assert prepared.mapping.reason_code == "MAPPED_EXACT"
    assert prepared.fact_draft is not None
    assert prepared.fact_draft.producer_event_key == (
        "legacy-health-indicator:v1:7:2026-08-01T00:00:00.000000Z"
    )


def test_STORE_DEVICE_REPORT缺失权威identity只进入REVIEW_REQUIRED():
    from app.modules.health_fact.domain import HealthFactDigestKeyring
    from app.modules.health_fact_mapping.domain import prepare_health_mapping

    keyring = HealthFactDigestKeyring.from_base64(
        current_key_id="k1", encoded_keys={"k1": _key(1)}
    )
    for source_type in ("STORE", "DEVICE", "REPORT"):
        prepared = prepare_health_mapping(
            source=_source(source=source_type),
            mapping_version=1,
            batch_id="00000000-0000-0000-0000-000000000001",
            keyring=keyring,
        )
        assert prepared.mapping.disposition == "REVIEW_REQUIRED"
        assert prepared.mapping.reason_code == "SOURCE_AUTHORITY_UNVERIFIED"
        assert prepared.fact_draft is None


def test_第16项与单位不匹配不得创建fact():
    from app.modules.health_fact.domain import HealthFactDigestKeyring
    from app.modules.health_fact_mapping.domain import prepare_health_mapping

    keyring = HealthFactDigestKeyring.from_base64(
        current_key_id="k1", encoded_keys={"k1": _key(1)}
    )
    unknown = prepare_health_mapping(
        source=_source(indicator_type="custom_metric"),
        mapping_version=1,
        batch_id="00000000-0000-0000-0000-000000000001",
        keyring=keyring,
    )
    wrong_unit = prepare_health_mapping(
        source=_source(unit="lb"),
        mapping_version=1,
        batch_id="00000000-0000-0000-0000-000000000001",
        keyring=keyring,
    )
    assert (unknown.mapping.reason_code, unknown.fact_draft) == ("INDICATOR_NOT_OPEN", None)
    assert (wrong_unit.mapping.reason_code, wrong_unit.fact_draft) == ("UNIT_MISMATCH", None)


def test_Health_semantic_fingerprint不包含legacy_batch_id():
    from app.modules.health_fact.domain import HealthFactDigestKeyring
    from app.modules.health_fact_mapping.domain import health_source_fingerprint

    keyring = HealthFactDigestKeyring.from_base64(
        current_key_id="k1", encoded_keys={"k1": _key(1)}
    )
    first = health_source_fingerprint(
        source=_source(legacy_batch_id="00000000-0000-0000-0000-000000000001"),
        mapping_version=1,
        keyring=keyring,
        key_id="k1",
    )
    second = health_source_fingerprint(
        source=_source(legacy_batch_id="00000000-0000-0000-0000-000000000002"),
        mapping_version=1,
        keyring=keyring,
        key_id="k1",
    )
    assert first == second


def test_Health_fingerprint底层拒绝naive时间且offset归一():
    import pytest
    from app.modules.health_fact.domain import HealthFactDigestKeyring
    from app.modules.health_fact_mapping.domain import HealthLegacyMappingUnavailable, health_source_fingerprint

    keyring = HealthFactDigestKeyring.from_base64(current_key_id="k1", encoded_keys={"k1": _key(1)})
    for source in (_source(recorded_at=datetime(2026, 8, 1)), _source(created_at=datetime(2026, 8, 1))):
        with pytest.raises(HealthLegacyMappingUnavailable):
            health_source_fingerprint(source=source, mapping_version=1, keyring=keyring, key_id="k1")
    utc = _source(recorded_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    offset = _source(recorded_at=datetime(2026, 8, 1, 8, tzinfo=timezone(timedelta(hours=8))))
    assert health_source_fingerprint(source=utc, mapping_version=1, keyring=keyring, key_id="k1") == health_source_fingerprint(source=offset, mapping_version=1, keyring=keyring, key_id="k1")
