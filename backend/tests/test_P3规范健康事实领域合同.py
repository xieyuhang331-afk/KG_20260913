import base64
from dataclasses import fields
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.modules.health_fact.domain import (
    CATALOG_V1,
    CanonicalHealthFact,
    CanonicalHealthFactDraft,
    HealthFactCatalogUnknown,
    HealthFactDigestKeyring,
    HealthFactRequestInvalid,
    HealthFactUnavailable,
    prepare_fact,
    semantic_lock_key,
    source_identity_digests,
    verify_payload,
)


def _key(value: int) -> str:
    return base64.b64encode(bytes([value]) * 32).decode("ascii")


def _keyring(current: str = "k1") -> HealthFactDigestKeyring:
    return HealthFactDigestKeyring.from_base64(
        current_key_id=current,
        encoded_keys={"k1": _key(1), "k2": _key(2)},
    )


def _draft(**changes) -> CanonicalHealthFactDraft:
    values = dict(
        subject_user_id=7,
        indicator_code="systolic_bp",
        numeric_value=Decimal("120.50"),
        unit="mmHg",
        measured_at=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
        source_type="APP",
        source_identity="fictional-source-一",
        producer_event_key="event-1",
    )
    values.update(changes)
    return CanonicalHealthFactDraft(**values)


def test_规范健康事实领域尚未实现():
    assert len(CATALOG_V1) == 15
    fact = prepare_fact(_draft(), _keyring())
    assert fact.value_kind == "NUMERIC"
    assert fact.numeric_value == Decimal("120.50")
    assert fact.catalog_version == 1


def test_V1领域和持久化实体没有TEXT_COMPOSITE或原始source_identity字段():
    names = {field.name for field in fields(CanonicalHealthFact)}
    assert "text_value" not in names
    assert "composite_value" not in names
    assert "source_identity" not in names
    assert "source_identity_digest" in names


@pytest.mark.parametrize(
    ("indicator", "unit"),
    list(CATALOG_V1.items()),
)
def test_V1精确15项NUMERIC目录和canonical单位(indicator, unit):
    assert prepare_fact(_draft(indicator_code=indicator, unit=unit), _keyring()).unit == unit


def test_第16项和错误单位与超过两位小数全部fail_closed():
    with pytest.raises(HealthFactCatalogUnknown):
        prepare_fact(_draft(indicator_code="unapproved"), _keyring())
    with pytest.raises(HealthFactRequestInvalid):
        prepare_fact(_draft(unit="kPa"), _keyring())
    with pytest.raises(HealthFactRequestInvalid):
        prepare_fact(_draft(numeric_value=Decimal("1.234")), _keyring())


def test_HMAC分域且轮换后可用stored_key验证旧事实():
    draft = _draft()
    old = prepare_fact(draft, _keyring("k1"))
    new = prepare_fact(draft, _keyring("k2"))
    assert old.source_identity_digest != old.payload_digest
    assert old.source_identity_digest != new.source_identity_digest
    assert verify_payload(stored=old, draft=draft, keyring=_keyring("k2"))


def test_缺失current或stored_key必须fail_closed():
    with pytest.raises(HealthFactUnavailable):
        HealthFactDigestKeyring.from_base64(
            current_key_id="missing", encoded_keys={"k1": _key(1)}
        )
    old = prepare_fact(_draft(), _keyring("k1"))
    current_only = HealthFactDigestKeyring.from_base64(
        current_key_id="k2", encoded_keys={"k2": _key(2)}
    )
    with pytest.raises(HealthFactUnavailable):
        verify_payload(stored=old, draft=_draft(), keyring=current_only)


def test_HMAC_keyring_JSON重复key_id必须fail_closed():
    duplicate = '{"k1":"%s","k1":"%s"}' % (_key(1), _key(2))
    with pytest.raises(HealthFactUnavailable) as captured:
        HealthFactDigestKeyring.from_json(
            current_key_id="k1", keyring_json=duplicate
        )
    assert str(captured.value) == "Health fact digest keyring is unavailable"
    assert captured.value.__cause__ is None


def test_semantic_lock不随HMAC_current_key变化():
    assert semantic_lock_key(source_type="APP", producer_event_key="event-1") == semantic_lock_key(
        source_type="APP", producer_event_key="event-1"
    )
    assert set(source_identity_digests(source_identity="source", keyring=_keyring())) == {
        "k1",
        "k2",
    }
