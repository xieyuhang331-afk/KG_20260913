import asyncio
from datetime import datetime, timedelta, timezone

import pytest


def test_Organization批次同ID恢复使用anti_join且不跳过较小失败行():
    from app.modules.organization_mapping.service import OrganizationMappingBatchService

    class Control:
        started = None
        completed = None
        pages = [[1, 3], [2], []]
        locks = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def acquire_batch_lock(self, version): self.locks += 1; return True
        async def get_batch_started(self, batch_id): return self.started
        async def get_batch_completed(self, batch_id): return self.completed
        async def add_batch_started(self, payload): self.started = payload
        async def list_unprocessed(self, **kwargs): return self.pages.pop(0)
        async def count_remaining(self, **kwargs): return 0
        async def count_dispositions(self, **kwargs): return {"MAPPED": 3}
        async def add_batch_completed(self, payload): self.completed = payload
    class Mapper:
        seen = []
        async def map_one(self, *, legacy_tenant_id, batch_id):
            self.seen.append(legacy_tenant_id)
            mapping=type("M",(),{"disposition":"MAPPED"})()
            return type("R",(),{"mapping":mapping})()

    control=Control(); mapper=Mapper()
    service=OrganizationMappingBatchService(control_factory=lambda:control,item_service=mapper)
    result=asyncio.run(service.run(batch_id="00000000-0000-0000-0000-000000000001",high_watermark={"max_tenant_id":3},config_hash="a"*64,page_size=2))
    assert mapper.seen == [1,3,2]
    assert result["counts"] == {"MAPPED":3}
    assert control.started["high_watermark"] == {"max_tenant_id":3}
    assert "summary_hash" in control.completed


def test_Health批次不同batch也由domain_version锁互斥():
    from app.modules.health_fact_mapping.domain import HealthLegacyMappingUnavailable
    from app.modules.health_fact_mapping.service import HealthMappingBatchService

    class Control:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return False
        async def acquire_batch_lock(self,version): return False
    service=HealthMappingBatchService(control_factory=Control,item_service=object())
    with pytest.raises(HealthLegacyMappingUnavailable,match="already running"):
        asyncio.run(service.run(batch_id="00000000-0000-0000-0000-000000000002",high_watermark={"max_recorded_at":"2026-01-01T00:00:00.000000Z","max_id":9},config_hash="b"*64,page_size=50))


def test_Shadow摘要只包含分级计数和安全Hash():
    from app.modules.organization_mapping.service import build_organization_shadow_summary
    from app.modules.health_fact_mapping.service import build_health_shadow_summary

    org=build_organization_shadow_summary(blocker=1,review_required=2,informational=3)
    health=build_health_shadow_summary(blocker=0,review_required=4,informational=5)
    assert set(org) == {"blocker","review_required","informational","summary_hash"}
    assert set(health) == set(org)
    assert len(org["summary_hash"]) == len(health["summary_hash"]) == 64
    assert "source" not in str(org).lower() and "value" not in str(health).lower()


def test_high_watermark_canonical_round_trip与offset归一化():
    from app.modules.organization_mapping.domain import normalize_organization_high_watermark
    from app.modules.health_fact_mapping.domain import normalize_health_high_watermark

    assert normalize_organization_high_watermark({"max_tenant_id": 123}) == {"max_tenant_id": 123}
    offset = timezone(timedelta(hours=8))
    value = normalize_health_high_watermark(
        {"max_recorded_at": datetime(2026, 8, 11, 20, 34, 56, 123456, tzinfo=offset), "max_id": 456}
    )
    assert value == {"max_id": 456, "max_recorded_at": "2026-08-11T12:34:56.123456Z"}


@pytest.mark.parametrize(
    "value",
    [
        {"max_tenant_id": True},
        {"max_tenant_id": "1"},
        {"max_tenant_id": 1.0},
        {"max_tenant_id": None},
        {"max_tenant_id": 0},
        {},
        {"max_tenant_id": 1, "extra": 2},
        [1],
        (1,),
    ],
)
def test_Organization_high_watermark非法结构fail_closed(value):
    from app.modules.organization_mapping.domain import OrganizationMappingUnavailable, normalize_organization_high_watermark
    with pytest.raises(OrganizationMappingUnavailable):
        normalize_organization_high_watermark(value)


@pytest.mark.parametrize(
    "value",
    [
        {"max_recorded_at": datetime(2026, 1, 1), "max_id": 1},
        {"max_recorded_at": None, "max_id": 1},
        {"max_recorded_at": "invalid", "max_id": 1},
        {"max_recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "max_id": True},
        {"max_recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "max_id": "1"},
        {"max_recorded_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "max_id": 1, "extra": 2},
        [datetime(2026, 1, 1, tzinfo=timezone.utc), 1],
    ],
)
def test_Health_high_watermark非法结构fail_closed(value):
    from app.modules.health_fact_mapping.domain import HealthLegacyMappingUnavailable, normalize_health_high_watermark
    with pytest.raises(HealthLegacyMappingUnavailable):
        normalize_health_high_watermark(value)


def test_Health_high_watermark_JSONB键顺序不影响语义比较():
    from app.modules.health_fact_mapping.domain import health_high_watermarks_equal
    first = {"max_id": 7, "max_recorded_at": "2026-08-11T12:34:56.123456Z"}
    second = {"max_recorded_at": "2026-08-11T12:34:56.123456Z", "max_id": 7}
    assert health_high_watermarks_equal(first, second)
    assert not health_high_watermarks_equal(first, {**second, "max_id": 8})


def test_Organization批次恢复使用全高水位计数且completed幂等():
    from app.modules.organization_mapping.service import OrganizationMappingBatchService

    completed = {
        "domain": "organization",
        "batch_id": "00000000-0000-0000-0000-000000000001",
        "mapping_version": 1,
        "high_watermark": {"max_tenant_id": 3},
        "config_hash": "a" * 64,
        "counts": {"MAPPED": 3},
    }
    from app.modules.organization_mapping.service import _safe_summary_hash
    completed["summary_hash"] = _safe_summary_hash(completed)

    class Control:
        added = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def acquire_batch_lock(self, version): return True
        async def get_batch_started(self, batch_id): return {k:v for k,v in completed.items() if k not in {"counts","summary_hash"}}
        async def get_batch_completed(self, batch_id): return completed
        async def list_unprocessed(self, **kwargs): return []
        async def count_remaining(self, **kwargs): return 0
        async def count_dispositions(self, **kwargs): return {"MAPPED": 3}
        async def add_batch_started(self, payload): self.added += 1
        async def add_batch_completed(self, payload): self.added += 1
    control = Control()
    result = asyncio.run(OrganizationMappingBatchService(control_factory=lambda: control, item_service=object()).run(
        batch_id=completed["batch_id"], high_watermark=completed["high_watermark"], config_hash="a"*64, page_size=2
    ))
    assert result == completed
    assert control.added == 0


def test_真实Shadow_Validator读取source_mapping_canonical并分级():
    import base64
    import json
    from app.modules.organization_mapping.domain import (
        OrganizationLegacyMapping,
        OrganizationMappingDigestKeyring,
        OrganizationSourceSnapshot,
        organization_source_fingerprint,
    )
    from app.modules.organization_mapping.service import OrganizationShadowValidator

    source = OrganizationSourceSnapshot(
        1, 2,
        {"id":2,"parent_id":1,"org_type":"province","status":"active","version":1},
        ancestors=({"id":1,"parent_id":None,"org_type":"headquarter","status":"active","version":1},),
    )
    keyring = OrganizationMappingDigestKeyring.from_json(
        current_key_id="k1",
        keyring_json=json.dumps({"k1": base64.b64encode(b"a"*32).decode()}),
    )
    mapping = OrganizationLegacyMapping(
        1, 2, 2, 1, "00000000-0000-0000-0000-000000000001",
        organization_source_fingerprint(source=source,mapping_version=1,keyring=keyring,key_id="k1"),
        "k1", "MAPPED", "MAPPED_EXACT", 7,
    )
    class Repo:
        calls=[]
        async def list_shadow_sources(self, high_watermark): self.calls.append("source"); return [source]
        async def list_shadow_mappings(self, high_watermark, mapping_version): self.calls.append("mapping"); return [mapping]
        async def get_shadow_canonical(self, organization_id): self.calls.append("canonical"); return source.target
    repo=Repo()
    summary=asyncio.run(OrganizationShadowValidator(repository=repo,keyring=keyring).validate({"max_tenant_id":1}))
    assert repo.calls == ["source","mapping","canonical"]
    assert summary["blocker"] == summary["review_required"] == summary["informational"] == 0


def test_Shadow_mapping存在但source缺失必须BLOCKER():
    from app.modules.organization_mapping.service import OrganizationShadowValidator
    class Repo:
        async def list_shadow_sources(self, high_watermark): return []
        async def list_shadow_mappings(self, high_watermark, mapping_version):
            return [type("Mapping", (), {"legacy_tenant_id": 1})()]
    summary = asyncio.run(
        OrganizationShadowValidator(repository=Repo(), keyring=object()).validate(
            {"max_tenant_id": 1}
        )
    )
    assert summary["blocker"] == 1
    assert summary["informational"] == 0


def test_Health_Shadow必须核验received_at边界():
    from app.modules.health_fact_mapping.service import _fact_matches_source
    draft = type("Draft", (), {
        "subject_user_id": 1, "indicator_code": "weight", "numeric_value": 1,
        "unit": "kg", "measured_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "source_type": "APP", "producer_event_key": "event",
    })()
    fact = type("Fact", (), {**draft.__dict__, "received_at": datetime(2026, 1, 2, tzinfo=timezone.utc)})()
    assert not _fact_matches_source(
        fact, draft, expected_received_at=datetime(2026, 1, 3, tzinfo=timezone.utc)
    )


@pytest.mark.parametrize("kind", ["organization", "health"])
def test_BatchControl数据库异常必须脱敏为领域Unavailable(kind):
    if kind == "organization":
        from app.modules.organization_mapping.domain import OrganizationMappingUnavailable as Unavailable
        from app.modules.organization_mapping.repository import OrganizationMappingBatchControl as Control
        watermark = {"max_tenant_id": 1}
    else:
        from app.modules.health_fact_mapping.domain import HealthLegacyMappingUnavailable as Unavailable
        from app.modules.health_fact_mapping.repository import HealthMappingBatchControl as Control
        watermark = {"max_recorded_at": "2026-01-01T00:00:00.000000Z", "max_id": 1}

    class FailingConnection:
        async def execute(self, statement): raise RuntimeError("vendor detail")
        async def commit(self): pass
        def in_transaction(self): return False
    control = Control(engine=object())
    control._connection = FailingConnection()
    calls = (
        lambda: control.acquire_batch_lock(1),
        lambda: control.get_batch_started("batch"),
        lambda: control.get_batch_completed("batch"),
        lambda: control.list_unprocessed(mapping_version=1, high_watermark=watermark, limit=1),
        lambda: control.count_remaining(mapping_version=1, high_watermark=watermark),
        lambda: control.count_dispositions(mapping_version=1, high_watermark=watermark),
        lambda: control.add_batch_started({"batch_id": "batch", "high_watermark": watermark}),
        lambda: control.add_batch_completed({"batch_id": "batch", "high_watermark": watermark}),
    )
    for call in calls:
        with pytest.raises(Unavailable) as captured:
            asyncio.run(call())
        assert captured.value.__cause__ is None and captured.value.__context__ is None


@pytest.mark.parametrize("kind", ["organization", "health"])
def test_BatchControl_Cancellation原样传播(kind):
    if kind == "organization":
        from app.modules.organization_mapping.repository import OrganizationMappingBatchControl as Control
    else:
        from app.modules.health_fact_mapping.repository import HealthMappingBatchControl as Control
    class CancelledConnection:
        async def execute(self, statement): raise asyncio.CancelledError()
    control = Control(engine=object())
    control._connection = CancelledConnection()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(control.acquire_batch_lock(1))
