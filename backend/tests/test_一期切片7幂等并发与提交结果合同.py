import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID

from app.modules.service_fulfillment.domain import SyntheticBusinessClock
from app.modules.service_fulfillment.schemas import (
    DataExportCreateRequest,
    DownloadAccessRequest,
)
from app.modules.service_fulfillment.service import (
    CommitOutcome,
    ServiceFulfillmentError,
    ServiceFulfillmentService,
    _json_value,
    commit_with_confirmation,
)


MILESTONE_ID = UUID("0198f1c0-0000-7000-8000-000000000001")
TASK_SOURCE = (
    Path(__file__).parents[1]
    / "app/tasks/slice7_service_fulfillment_tasks.py"
).read_text(encoding="utf-8")


class SequentialIds:
    def __init__(self) -> None:
        self.value = 10

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"0198f1c0-0000-7000-8000-{self.value:012d}")


class FakeRepository:
    def __init__(self) -> None:
        self.receipts = {}
        self.mutations = []
        self.authority_calls = []
        self.authority_value = {
            "case_status": "ACTIVE",
            "service_case_id": UUID("0198f1c0-0000-7000-8000-000000000002"),
            "milestone_code": "D0",
            "window_start": date(2026, 8, 25),
            "window_end": date(2026, 8, 29),
            "milestone_version": 1,
            "therapist_current": True,
        }

    async def authority(self, *args):
        self.authority_calls.append(args)
        return dict(self.authority_value) if self.authority_value is not None else None

    async def replay(self, actor_user_id, operation, idempotency_key, request_digest):
        key = (actor_user_id, operation, idempotency_key)
        prior = self.receipts.get(key)
        if prior is None:
            return None
        if prior[0] != request_digest:
            raise ServiceFulfillmentError("IDEMPOTENCY_CONFLICT")
        return prior[1]

    async def mutate(self, operation, payload):
        response = {
            "milestone_id": payload.get("target_id", payload.get("milestone_id")),
            "status": "COMPLETED" if operation == "COMPLETE_MILESTONE" else operation,
            "version": 2,
        }
        key = (payload["actor_user_id"], operation, payload["idempotency_key"])
        self.receipts[key] = (payload["request_digest"], response)
        self.mutations.append(dict(payload))
        return response

    async def claim_work(self, *args):
        return []


class AmbiguousCommitSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1
        raise RuntimeError("connection closed")

    async def rollback(self) -> None:
        self.rollback_calls += 1


def test_C_UUID序列化边界支持标准库与asyncpg子类且未知对象仍失败() -> None:
    identifier = "0198f1c0-0000-7000-8000-000000000001"
    values = {
        "stdlib": UUID(identifier),
        "asyncpg": AsyncpgUUID(identifier),
    }
    assert json.loads(json.dumps(_json_value(values))) == {
        "stdlib": identifier,
        "asyncpg": identifier,
    }

    class UnknownValue:
        pass

    with pytest.raises(TypeError):
        json.dumps(_json_value({"value": UnknownValue()}))


@pytest.mark.asyncio
async def test_D05_D06_相同请求稳定重放且不同请求固定冲突() -> None:
    repository = FakeRepository()
    service = ServiceFulfillmentService(
        repository,
        SyntheticBusinessClock(datetime(2026, 8, 25, tzinfo=timezone.utc)),
        SequentialIds(),
    )
    kwargs = dict(
        milestone_id=MILESTONE_ID,
        actor_user_id=9,
        actor_role="therapist",
        actor_tenant_id=3,
        idempotency_key="0198f1c0-0000-7000-8000-000000000099",
        expected_version=1,
        record_summary={"EXECUTION_STATUS": "COMPLETED"},
        evidence_refs=(),
    )
    first = await service.complete_milestone(**kwargs)
    second = await service.complete_milestone(**kwargs)
    assert first == second
    assert len(repository.mutations) == 1

    with pytest.raises(ServiceFulfillmentError, match="IDEMPOTENCY_CONFLICT"):
        await service.complete_milestone(
            **{**kwargs, "record_summary": {"EXECUTION_STATUS": "DIFFERENT"}}
        )
    assert len(repository.mutations) == 1


@pytest.mark.asyncio
async def test_D09_D10_Currentness失败和暂停状态均零写入() -> None:
    repository = FakeRepository()
    service = ServiceFulfillmentService(
        repository,
        SyntheticBusinessClock(datetime(2026, 8, 25, tzinfo=timezone.utc)),
        SequentialIds(),
    )
    repository.authority_value = {"error_code": "CURRENTNESS_FORBIDDEN"}
    with pytest.raises(ServiceFulfillmentError, match="CURRENTNESS_FORBIDDEN"):
        await service.complete_milestone(
            milestone_id=MILESTONE_ID,
            actor_user_id=9,
            actor_role="therapist",
            actor_tenant_id=3,
            idempotency_key="key-currentness",
            expected_version=1,
            record_summary={"EXECUTION_STATUS": "COMPLETED"},
            evidence_refs=(),
        )
    repository.authority_value = {
        "case_status": "PAUSED",
        "window_start": date(2026, 8, 25),
        "window_end": date(2026, 8, 29),
    }
    with pytest.raises(ServiceFulfillmentError, match="CASE_STATE_CONFLICT"):
        await service.complete_milestone(
            milestone_id=MILESTONE_ID,
            actor_user_id=9,
            actor_role="therapist",
            actor_tenant_id=3,
            idempotency_key="key-paused",
            expected_version=1,
            record_summary={"EXECUTION_STATUS": "COMPLETED"},
            evidence_refs=(),
        )
    assert repository.mutations == []


@pytest.mark.asyncio
async def test_C01_CREATE_EXPORT全链路只使用一个真实ExportId() -> None:
    repository = FakeRepository()
    repository.authority_value = {
        "subject_member_id": UUID("0198f1c0-0000-7000-8000-000000000091"),
    }
    member_id = UUID("0198f1c0-0000-7000-8000-000000000092")
    export_id = UUID("0198f1c0-0000-7000-8000-000000000011")
    service = ServiceFulfillmentService(
        repository,
        SyntheticBusinessClock(datetime(2026, 8, 26, tzinfo=timezone.utc)),
        SequentialIds(),
    )

    await service.export_transition(
        operation="CREATE_EXPORT",
        export_or_member_id=member_id,
        actor_user_id=9,
        actor_role="member",
        actor_tenant_id=None,
        idempotency_key="slice7-c-export-id-0001",
        request={"requested_scope": ("ASSESSMENT",), "reason": "PERSONAL_ARCHIVE"},
    )

    mutation = repository.mutations[0]
    assert mutation["target_id"] == export_id
    assert mutation["operation_id"] == export_id
    assert mutation["response"]["export_id"] == export_id


@pytest.mark.asyncio
async def test_C02_未知提交只确认后像且不自动重放Mutation() -> None:
    committed_session = AmbiguousCommitSession()

    async def committed() -> CommitOutcome:
        return CommitOutcome.COMMITTED

    assert (
        await commit_with_confirmation(committed_session, confirm=committed)
        is CommitOutcome.COMMITTED
    )
    assert committed_session.commit_calls == 1
    assert committed_session.rollback_calls == 1

    unknown_session = AmbiguousCommitSession()

    async def unknown() -> CommitOutcome:
        return CommitOutcome.UNKNOWN

    with pytest.raises(RuntimeError, match="^COMMIT_OUTCOME_UNKNOWN$"):
        await commit_with_confirmation(unknown_session, confirm=unknown)
    assert unknown_session.commit_calls == 1
    assert unknown_session.rollback_calls == 1

    not_committed_session = AmbiguousCommitSession()

    async def not_committed() -> CommitOutcome:
        return CommitOutcome("NOT_COMMITTED")

    with pytest.raises(RuntimeError, match="^COMMIT_NOT_COMMITTED$"):
        await commit_with_confirmation(
            not_committed_session, confirm=not_committed
        )
    assert not_committed_session.commit_calls == 1
    assert not_committed_session.rollback_calls == 1


def test_C03_Worker_READY未知提交只查精确后像且不重放绑定Mutation() -> None:
    generate = TASK_SOURCE[
        TASK_SOURCE.index("async def _generate_export") : TASK_SOURCE.index(
            "def _export_failure_code"
        )
    ]
    assert "commit_with_confirmation(" in generate
    assert "confirm_export_ready(" in generate
    assert "CommitOutcome.COMMITTED" in generate
    assert generate.count("bind_export_artifact(") == 1


def test_D02_MarkMissed批次未知提交确认最后一个完整后像且不重放() -> None:
    mark_overdue = TASK_SOURCE[
        TASK_SOURCE.index("async def _mark_overdue") : TASK_SOURCE.index(
            "async def _generate_export"
        )
    ]
    assert "commit_with_confirmation(" in mark_overdue
    assert "confirm_mutation_outcome(" in mark_overdue
    assert mark_overdue.count("service.mark_overdue(") == 1


def test_D03_一次性下载访问必须绑定导出版本并在数据库写入前拒绝漂移() -> None:
    with pytest.raises(ValueError):
        DownloadAccessRequest(reason="PERSONAL_ARCHIVE")

    request = DownloadAccessRequest(
        reason="PERSONAL_ARCHIVE",
        expected_version=3,
    )
    assert request.expected_version == 3

    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    branch = migration[
        migration.index("ELSIF operation_name='EXPORT_DOWNLOAD_ACCESS'") :
        migration.index('ELSE RAISE EXCEPTION \'INVALID_REQUEST\'')
    ]
    assert "current_authority->>'export_version'" in branch
    assert "value->>'expected_version'" in branch
    assert "RAISE EXCEPTION 'STALE_VERSION'" in branch


def test_D04_确认转机构Scope的未知提交必须核验追加式Revision完整后像() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    start = migration.index('        "slice7_mutation_confirm_v1",')
    confirmation = migration[
        start : migration.index('        "slice7_read_one_v1",', start)
    ]
    assert "ELSIF operation_name='CONFIRM_TRANSFER_SCOPE'" in confirmation
    branch = confirmation[
        confirmation.index("ELSIF operation_name='CONFIRM_TRANSFER_SCOPE'") :
        confirmation.index("ELSIF operation_name='COORDINATE_TRANSFER_CLOSE'")
    ]
    assert "service_transfer_scope_revision" in branch
    assert "scope_revision_id=(value->>'operation_id')::uuid" in branch
    assert "scope_digest=decode(value->>'request_digest','hex')" in branch


def test_D05_TRANSFERRED未知提交必须同时核验旧Case终态Enrollment撤销及唯一Handoff() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    start = migration.index('        "slice7_mutation_confirm_v1",')
    confirmation = migration[
        start : migration.index('        "slice7_read_one_v1",', start)
    ]
    branch = confirmation[
        confirmation.index("ELSIF operation_name='COORDINATE_TRANSFER_CLOSE'") :
        confirmation.index("ELSIF operation_name='LINK_CONTINUATION_CASE'")
    ]
    for token in (
        "service_case_lifecycle_event",
        "service_enrollment",
        "status='REVOKED'",
        "lifecycle_event_id",
        "handoff_id",
        "handoff_scope_digest",
        "created_at",
    ):
        assert token in branch


def test_D06_导出恢复逐次重验本人或代理重大权限Currentness() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    start = migration.index('        "slice7_export_recover_v1",')
    function = migration[
        start : migration.index('        "slice7_export_cleanup_claim_v1",', start)
    ]
    assert "EXISTS(SELECT 1 FROM identity.user_member_self_link" in function
    assert (
        "slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT')"
        in function
    )
    assert "JOIN identity.user_member_self_link l ON l.user_ref=u.id AND l.member_id=e.subject_member_id" not in function


def test_D07_Worker读取和READY绑定均逐次重验导出请求人Currentness() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    boundaries = (
        ("slice7_worker_claim_v1", "slice7_export_claim_v1"),
        ("slice7_export_claim_v1", "slice7_export_snapshot_v1"),
        ("slice7_export_snapshot_v1", "slice7_export_source_file_v1"),
        ("slice7_export_source_file_v1", "slice7_export_private_file_register_v1"),
        ("slice7_export_private_file_register_v1", "slice7_export_private_file_snapshot_v1"),
        ("slice7_export_artifact_bind_v1", "slice7_export_ready_confirm_v1"),
    )
    for name, next_name in boundaries:
        start = migration.index(f'        "{name}",')
        function = migration[
            start : migration.index(f'        "{next_name}",', start)
        ]
        assert "requested_by" in function, name
        assert "user_member_self_link" in function, name
        assert "PERSONAL_DATA_EXPORT" in function, name


def test_D08_孤儿清理使用Target与Action去重而不是错误比较AuditId() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    start = migration.index('        "slice7_export_cleanup_claim_v1",')
    function = migration[
        start : migration.index('        "slice7_export_cleanup_complete_v1",', start)
    ]
    assert "a.target_id=e.export_id AND a.action='EXPORT_FILE_CLEANED'" in function
    assert "a.audit_id=e.export_id" not in function


def test_D09_Currentness长期失效的导出请求由孤儿清理受控取消() -> None:
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
    ).read_text(encoding="utf-8")
    start = migration.index('        "slice7_export_cleanup_claim_v1",')
    function = migration[
        start : migration.index('        "slice7_export_cleanup_complete_v1",', start)
    ]
    for token in (
        "stale_requester",
        "interval '24 hours'",
        "status IN ('REQUESTED','GENERATING')",
        "THEN 'CANCELLED'",
        "PERSONAL_DATA_EXPORT",
    ):
        assert token in function


@pytest.mark.asyncio
async def test_D01_代理导出显式绑定Subject且每次消费精确重大权限() -> None:
    subject_member_id = UUID("0198f1c0-0000-7000-8000-000000000081")
    request = DataExportCreateRequest(
        subject_member_id=subject_member_id,
        requested_scope=("ASSESSMENT",),
        reason="PERSONAL_ARCHIVE",
    )
    repository = FakeRepository()
    repository.authority_value = {"subject_member_id": subject_member_id}
    service = ServiceFulfillmentService(
        repository,
        SyntheticBusinessClock(datetime(2026, 8, 26, tzinfo=timezone.utc)),
        SequentialIds(),
    )

    await service.export_transition(
        operation="CREATE_EXPORT",
        export_or_member_id=None,
        actor_user_id=9,
        actor_role="member",
        actor_tenant_id=None,
        idempotency_key="slice7-proxy-export-0001",
        request=request.model_dump(),
    )

    assert repository.authority_calls[0][:2] == (
        "CREATE_EXPORT_FOR_SUBJECT",
        subject_member_id,
    )
    mutation = repository.mutations[0]
    assert mutation["authority_operation"] == "CREATE_EXPORT_FOR_SUBJECT"
    assert mutation["authority_target_id"] == subject_member_id
    assert mutation["target_id"] == mutation["operation_id"]

    repository.authority_value = {"error_code": "PROXY_PERMISSION_FORBIDDEN"}
    with pytest.raises(ServiceFulfillmentError, match="PROXY_PERMISSION_FORBIDDEN"):
        await service.export_transition(
            operation="CREATE_EXPORT",
            export_or_member_id=None,
            actor_user_id=9,
            actor_role="member",
            actor_tenant_id=None,
            idempotency_key="slice7-proxy-export-denied-0001",
            request=request.model_dump(),
        )
    assert len(repository.mutations) == 1
