from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest


MEMBER = UUID("00000000-0000-7000-8000-000000000401")


def _fact(
    n, source, state="VERIFIED", superseded=False, context=None,
    indicator="weight", value=Decimal("70"), unit="kg",
):
    from app.modules.health_projection.domain import HealthCurrentFactV2
    return HealthCurrentFactV2(n, UUID(f"00000000-0000-7000-8000-{n:012d}"), MEMBER, None, indicator, value, unit, datetime(2026, 8, 20, 1, tzinfo=timezone.utc), datetime(2026, 8, 20, 2, tzinfo=timezone.utc), source, state, 7, superseded, measurement_context=context)


def _item(status=7):
    from app.modules.projection_read.domain import HealthProjectionCoverageItem
    return HealthProjectionCoverageItem("weight", 3, status, 3, 3, "b" * 64, "c" * 64)


def _token(status=7):
    from app.modules.projection_read.domain import HealthProjectionCoverageToken
    return HealthProjectionCoverageToken(MEMBER, "10:20:", 2, "a" * 64, (_item(status),))


def _evidence(generation_id, generation_no, status=7):
    from app.modules.projection_read.domain import ReadyHealthProjectionEvidence
    return ReadyHealthProjectionEvidence(generation_id, generation_no, 2, "health-daily-selection-v2", MEMBER, "10:20:", "a" * 64, (_item(status),))


def test_D16_D17_ModuleE只见projection_read端口且resolver无generation输入():
    import inspect
    from app.modules.projection_read.ports import HealthProjectionCoverageAuthorityPort, LatestReadyHealthProjectionResolverPort, MemberHealthProjectionReadPort
    assert hasattr(HealthProjectionCoverageAuthorityPort, "capture") and hasattr(MemberHealthProjectionReadPort, "list_current_facts")
    assert "generation_id" not in inspect.signature(LatestReadyHealthProjectionResolverPort.resolve).parameters


def test_v2正式目录包含获批测量场景所需的血糖和血脂指标():
    from app.modules.projection_read.service import INDICATOR_CATALOG_V2

    assert {
        "fasting_glucose", "postprandial_glucose_2h", "hba1c",
        "total_cholesterol", "triglyceride", "hdl_c", "ldl_c",
    } <= INDICATOR_CATALOG_V2


def test_D18_DEVICE争议与被修正事实不进入v2且STORE优先():
    from app.modules.health_projection.domain import select_window_winner_v2
    from app.modules.organization_projection.domain import ProjectionSourceInvalid
    assert select_window_winner_v2((_fact(1, "APP"), _fact(2, "REPORT"), _fact(3, "STORE"))).source_type == "STORE"
    with pytest.raises(ProjectionSourceInvalid):
        select_window_winner_v2((_fact(1, "APP"), _fact(4, "DEVICE")))
    assert select_window_winner_v2((_fact(1, "APP"), _fact(3, "STORE", "DISPUTED"))).source_type == "APP"
    assert select_window_winner_v2((_fact(1, "APP"), _fact(3, "STORE", superseded=True))).source_type == "APP"


def test_D18_D19_D33_只选双水位完整一致的最大唯一READY():
    from app.modules.projection_read.domain import ProjectionReadUnavailable
    from app.modules.projection_read.service import resolve_latest_ready_generation
    assert resolve_latest_ready_generation(coverage_token=_token(), candidates=(_evidence(10, 1), _evidence(11, 2))).generation_id == 11
    assert resolve_latest_ready_generation(coverage_token=_token(8), candidates=(_evidence(11, 2),)) is None
    with pytest.raises(ProjectionReadUnavailable):
        resolve_latest_ready_generation(coverage_token=_token(), candidates=(_evidence(10, 2), _evidence(11, 2)))


def test_评估输入capture_resolve_read必须共享一个repeatable_read事务():
    import inspect
    from app.modules.projection_read.service import AssessmentProjectionSnapshotService

    source = inspect.getsource(AssessmentProjectionSnapshotService.resolve_and_read)
    assert source.count('_reader("health_reader", MemberHealthProjectionReadRepository)') == 1
    for operation in ("repo.coverage", "repo.ready_candidates", "repo.facts"):
        assert operation in source


def test_v2_builder生成Member事实和窗口选择且不接受DEVICE():
    import inspect
    from app.modules.health_projection.domain import build_health_projection_rows_v2
    from app.modules.health_projection.service import HealthProjectionBuilderV2
    from app.modules.organization_projection.domain import ProjectionSourceInvalid

    rows, selections = build_health_projection_rows_v2(
        facts=(_fact(1, "APP"), _fact(2, "REPORT")), digest_key=b"k" * 32
    )
    assert {row.subject_member_id for row in rows} == {MEMBER}
    assert {row.rule_version for row in selections} == {"health-daily-selection-v2"}
    with pytest.raises(ProjectionSourceInvalid):
        build_health_projection_rows_v2(facts=(_fact(3, "DEVICE"),), digest_key=b"k" * 32)
    source = inspect.getsource(HealthProjectionBuilderV2.start)
    assert "capture_v2_sources" in source
    assert HealthProjectionBuilderV2.__mro__[1].__name__ == "HealthProjectionBuilder"


def test_v2_builder接受获批测量场景所需的血糖和血脂指标():
    from app.modules.health_projection.domain import build_health_projection_rows_v2

    approved = (
        ("postprandial_glucose_2h", "OGTT_2H_VENOUS"),
        ("total_cholesterol", "FASTING_LAB"),
        ("triglyceride", "FASTING_LAB"),
        ("hdl_c", "FASTING_LAB"),
        ("ldl_c", "FASTING_LAB"),
    )
    rows, _ = build_health_projection_rows_v2(
        facts=tuple(
            _fact(
                index, "APP", context=context, indicator=indicator,
                value=Decimal("1.00"), unit="mmol/L",
            )
            for index, (indicator, context) in enumerate(approved, 10)
        ),
        digest_key=b"k" * 32,
    )
    assert {row.indicator_code for row in rows} == {
        indicator for indicator, _ in approved
    }


def test_v2投影事实保留显式测量场景并纳入行摘要():
    from app.modules.health_projection.domain import build_health_projection_rows_v2

    without_context, _ = build_health_projection_rows_v2(
        facts=(_fact(1, "APP"),), digest_key=b"k" * 32
    )
    with_context, _ = build_health_projection_rows_v2(
        facts=(_fact(1, "APP", context="OFFICE"),), digest_key=b"k" * 32
    )
    assert without_context[0].measurement_context is None
    assert with_context[0].measurement_context == "OFFICE"
    assert without_context[0].row_digest != with_context[0].row_digest


def test_最终整改_B_v2三水位ShadowEvidence可通过正式门禁():
    from app.modules.health_projection.service import build_health_shadow_evidence

    evidence = build_health_shadow_evidence(
        generation_id=41,
        projection_version=2,
        high_watermark={
            "max_fact_id": 0,
            "max_status_event_seq": 0,
            "source_snapshot": "1:1:",
        },
        digest_key_id="test-key",
        generation_input_digest="a" * 64,
        current_facts=(),
        mappings=(),
        projected_rows=(),
        selections=(),
        visibility_rows=(),
        digest_key=b"k" * 32,
    )

    assert evidence.rule_version == "health-daily-selection-v2"
    assert evidence.blocker_count == 0
    assert evidence.review_required_count == 0


def test_最终整改_B_status_event水位或Shadow证据不一致不得累积READY门禁():
    from app.modules.organization_projection.service import next_shadow_success_count

    previous = type("Run", (), {
        "run_sequence": 1,
        "source_count": 1,
        "status_event_count": 1,
        "evidence_digest": "a" * 64,
    })()
    drifted = type("Run", (), {
        "run_sequence": 2,
        "source_count": 1,
        "status_event_count": 2,
        "evidence_digest": "a" * 64,
    })()

    assert next_shadow_success_count(
        passed=True,
        previous_count=1,
        previous_run=previous,
        current_run=drifted,
    ) == 1


def test_最终整改_B_生产Worker必须执行两轮Shadow和ReadyGate():
    import inspect
    from app.tasks import slice4_health_data_tasks as tasks

    source = inspect.getsource(tasks._build_projection_v2)
    assert "ProjectionShadowService" in source
    assert "ProjectionReadyGate" in source
    assert source.count("shadow_complete") >= 1
    assert "mark_ready" in source


@pytest.mark.asyncio
async def test_并发Generation切换不得跨快照拼接评估输入(monkeypatch):
    from contextlib import asynccontextmanager
    from app.modules.projection_read import service

    class Repository:
        async def coverage(self, subject_member_id, indicators):
            return "snapshot-before-switch", (_item(),)

        async def ready_candidates(self, subject_member_id, indicators):
            return (_evidence(12, 3),)

        async def facts(self, **kwargs):
            raise AssertionError("mismatched generation must not be read")

    @asynccontextmanager
    async def reader(kind, repository_class):
        yield Repository()

    monkeypatch.setattr(service, "_reader", reader)
    coverage, resolved, page = await service.AssessmentProjectionSnapshotService().resolve_and_read(
        subject_member_id=MEMBER,
        indicator_codes=("weight",),
        measured_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
        measured_to=datetime(2026, 8, 22, tzinfo=timezone.utc),
        limit=20,
    )

    assert coverage.source_snapshot == "snapshot-before-switch"
    assert resolved is None
    assert page is None


@pytest.mark.asyncio
async def test_v2生产Builder确定性失败写入FAILED且提交未知不误判(monkeypatch):
    from app.modules.organization_projection.domain import ProjectionSourceInvalid
    from app.tasks import slice4_health_data_tasks as tasks

    builder_id = UUID("00000000-0000-7000-8000-000000000402")
    operation_id = UUID("00000000-0000-7000-8000-000000000403")
    generation = type("Generation", (), {
        "status": "BUILDING", "builder_id": str(builder_id),
        "lease_epoch": 0, "version": 1,
    })()
    checkpoint = type("Checkpoint", (), {
        "remaining_count": 1, "checkpoint_digest": "a" * 64,
    })()
    failed = []

    class Repository:
        async def get_generation(self, generation_id):
            return generation

        async def get_checkpoint(self, generation_id):
            return checkpoint

    class UnitOfWork:
        def __init__(self, *args, **kwargs):
            self.repository = Repository()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self, **kwargs):
            return 41

        async def build_page(self, **kwargs):
            raise ProjectionSourceInvalid("synthetic invalid source")

        async def fail(self, **kwargs):
            failed.append(kwargs)

    class Bind:
        async def connect(self):
            raise AssertionError("session lock is owned by the fake builder")

    class Factory:
        kw = {"bind": Bind()}

    async def factory(kind):
        return Factory()

    class Keyring:
        @classmethod
        def from_json(cls, *args):
            return cls()

    monkeypatch.setattr(tasks, "get_projection_session_factory", factory)
    monkeypatch.setattr(tasks, "ProjectionUnitOfWork", UnitOfWork)
    monkeypatch.setattr(tasks, "HealthProjectionBuilderV2", Builder)
    monkeypatch.setattr(tasks, "ProjectionDigestKeyring", Keyring)
    monkeypatch.setattr(tasks, "get_settings", lambda: type("Settings", (), {
        "health_projection_digest_current_key_id": "test-key",
        "health_projection_digest_keyring_json": "{}",
    })())

    with pytest.raises(ProjectionSourceInvalid):
        await tasks._build_projection_v2(
            generation_no=1,
            builder_id=builder_id,
            operation_id=operation_id,
        )

    assert len(failed) == 1
    assert failed[0]["failure_code"] == "PROJECTION_SOURCE_INVALID"
    assert failed[0]["operation_id"] == tasks._projection_operation_id(
        operation_id, "fail"
    )
    assert tasks._projection_failure_code(
        tasks.ProjectionCommitOutcomeUnknown("unknown")
    ) is None
