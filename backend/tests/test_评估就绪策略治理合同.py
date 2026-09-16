from __future__ import annotations

import ast
import asyncio
import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.database import get_slice5_rule_governance_writer_session
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.assessment_readiness import api as governance_api
from app.modules.assessment_readiness.policy_governance import govern_readiness_policy
from app.modules.assessment_readiness.schemas import ReadinessPolicyCreateRequest
from app.modules.health_assessment.service import HealthAssessmentError

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND
    / "app"
    / "migrations"
    / "versions"
    / "20260916_0049_评估就绪策略治理闭合边界.py"
)
GOVERNANCE = BACKEND / "app" / "modules" / "assessment_readiness" / "policy_governance.py"
API = BACKEND / "app" / "modules" / "assessment_readiness" / "api.py"
MODELS = BACKEND / "app" / "modules" / "assessment_readiness" / "models.py"
SCHEMAS = BACKEND / "app" / "modules" / "assessment_readiness" / "schemas.py"
REPOSITORY = BACKEND / "app" / "modules" / "assessment_readiness" / "repository.py"
MAIN = BACKEND / "app" / "main.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_0049必须是0048的唯一子Revision() -> None:
    assert MIGRATION.is_file(), "ASSESSMENT_READINESS_GOVERNANCE_MIGRATION_MISSING"
    source = _source(MIGRATION)
    tree = ast.parse(source)
    assignments = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    assert assignments.get("revision") == "20260916_0049"
    assert assignments.get("down_revision") == "20260915_0048"


def test_治理模块与路由必须存在且只通过闭合Repository调用数据库() -> None:
    assert GOVERNANCE.is_file()
    assert API.is_file()
    source = _source(REPOSITORY)
    assert "readiness_policy_governance_v1" in source
    assert "readiness_policy_governance_confirm_v1" in source
    assert "INSERT INTO public.assessment_readiness_policy_version" not in source
    assert "UPDATE public.assessment_readiness_policy_version" not in source
    assert "DELETE FROM public.assessment_readiness_policy_version" not in source


def test_DTO不得接受actor与professionally_approved快照() -> None:
    source = _source(SCHEMAS)
    assert "extra=\"forbid\"" in source
    assert "actor_user_id" not in source
    assert "actor_role" not in source
    assert "professionally_approved:" not in source


def test_状态机必须显式包含APPROVED并禁止审核后静默保持IN_REVIEW() -> None:
    source = _source(GOVERNANCE)
    for status in (
        "DRAFT",
        "IN_REVIEW",
        "NEEDS_CORRECTION",
        "APPROVED",
        "PUBLISHED",
        "SUSPENDED",
        "RETIRED",
    ):
        assert status in source
    assert '"REVIEW_APPROVE": "APPROVED"' in source


def test_I2_I3_I4数据库最后门必须关闭currentness_NULL与未发布退役旁路() -> None:
    source = _source(MIGRATION)
    assert "u.exited_at IS NULL" in source
    assert "u.deletion_requested_at IS NULL" in source
    assert "value_payload->>'expected_version' IS NULL" in source
    assert "value_payload->>'reason_code' IS NULL" in source
    assert "value_policy.row_version IS DISTINCT FROM" in source
    assert source.count("value_policy.author_user_id IS DISTINCT FROM") == 2
    assert "value_policy.status NOT IN ('PUBLISHED','SUSPENDED')" in source
    assert "value_policy.status NOT IN ('APPROVED','PUBLISHED','SUSPENDED')" not in source


def test_confirmation历史链SUBMIT状态矩阵必须与writer仅DRAFT保持一致() -> None:
    source = _source(MIGRATION)
    confirm = source[source.index("readiness_policy_governance_confirm_v1") :]
    assert "WHEN 'READINESS_POLICY_SUBMIT' THEN" in confirm
    submit_branch = confirm.split("WHEN 'READINESS_POLICY_SUBMIT' THEN", 1)[1].split(
        "WHEN 'READINESS_POLICY_REVIEW_APPROVE' THEN", 1
    )[0]
    assert "value_chain_snapshot->>2='DRAFT'" in submit_branch
    assert "NEEDS_CORRECTION" not in submit_branch


def test_Migration必须闭合传播收据目标摘要与同操作去重() -> None:
    source = _source(MIGRATION)
    assert "READINESS_POLICY_CHANGED" in source
    assert "operation_receipt_id" in source
    assert "target_set_digest" in source
    assert "target_count" in source
    assert "service_case_id" in source
    assert "uq_readiness_policy_propagation_operation_case" in source
    assert "slice4_recompute_candidate_v1" not in source


def test_confirm必须依赖持久收据而不是重新枚举或仅比较count() -> None:
    source = _source(MIGRATION)
    confirm = source[source.index("readiness_policy_governance_confirm_v1") :]
    assert "target_set_digest" in confirm
    assert "operation_receipt_id" in confirm
    assert "target_count" in confirm
    assert "service_case" not in confirm


def test_I1完整前后像必须由数据库事实复算且caller摘要不能作为证明() -> None:
    service = _source(GOVERNANCE)
    migration = _source(MIGRATION)
    confirm = migration[migration.index("readiness_policy_governance_confirm_v1") :]

    for domain in (
        "ASSESSMENT_READINESS_POLICY_PREIMAGE_V1",
        "ASSESSMENT_READINESS_POLICY_POSTIMAGE_V1",
        "ASSESSMENT_READINESS_POLICY_TARGET_SET_V1",
    ):
        assert domain in migration
    assert "postimage_digest, _ = digest_box.digest" not in service
    assert 'confirmation["preimage_digest"] = result["preimage_digest"]' in service
    assert 'confirmation["postimage_digest"] = result["postimage_digest"]' in service
    assert "value_policy_postimage" in migration
    assert "value_target_postimage" in migration
    assert "value_computed_postimage" in migration
    assert "value_computed_postimage=value_receipt_postimage" in confirm
    assert "octet_length(value_receipt_postimage)=32" not in confirm


def test_I1目标集合必须核对不可变payload且忽略正常投递状态字段() -> None:
    source = _source(MIGRATION)
    confirm = source[source.index("readiness_policy_governance_confirm_v1") :]
    for immutable in (
        "aggregate_type",
        "aggregate_ref",
        "event_type",
        "payload_digest",
        "payload_json",
        "created_at",
    ):
        assert immutable in confirm
    for mutable in (
        "status",
        "attempts",
        "lease_owner",
        "lease_until",
        "delivered_at",
    ):
        assert f"o.{mutable}" not in confirm


def test_Migration必须使用既有治理角色且不授予底表DML() -> None:
    source = _source(MIGRATION)
    assert "KG_SLICE5_RULE_GOVERNANCE_WRITER_ROLE" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog, pg_temp" in source
    assert "GRANT EXECUTE" in source
    assert "GRANT INSERT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT DELETE" not in source


def test_downgrade必须在任何REVOKE或DDL前拒绝破坏治理历史() -> None:
    source = _source(MIGRATION)
    downgrade = source[source.index("def downgrade") :]
    preflight = downgrade.index("ASSESSMENT_READINESS_GOVERNANCE_DOWNGRADE_BLOCKED")
    first_destructive = min(
        offset
        for token in ("REVOKE ", "DROP ", "op.drop_", "op.execute(\"ALTER TABLE")
        if (offset := downgrade.find(token)) >= 0
    )
    assert preflight < first_destructive


def test_路由必须注册且不得进入旧Slice5规则路由() -> None:
    api = _source(API)
    main = _source(MAIN)
    assert "/assessment-readiness-policies" in api
    assert "assessment_readiness.api" in main
    assert "app.include_router(assessment_readiness_router)" in main


def test_ORM状态约束必须与0049数据库状态一致() -> None:
    models = _source(MODELS)
    for status in (
        "DRAFT",
        "IN_REVIEW",
        "NEEDS_CORRECTION",
        "APPROVED",
        "PUBLISHED",
        "SUSPENDED",
        "RETIRED",
    ):
        assert status in models
    assert "row_version>=1" in models


def test_Migration内容边界必须闭合且旧行不阻断upgrade() -> None:
    source = _source(MIGRATION)
    assert "postgresql_not_valid=True" in source
    assert '[sa.text("(1)")]' in source
    for contract in (
        "jsonb_array_elements(value_payload->'content'->'required_profile_sections')",
        "jsonb_array_elements(value_payload->'content'->'required_indicators')",
        "i - ARRAY['indicator_code','max_age_days']::TEXT[]",
        "SELF_REPORTED','VERIFIED','REVIEWED",
        "count(DISTINCT i->>'indicator_code')",
    ):
        assert contract in source


def test_DTO未知actor与专业批准字段必须被拒绝() -> None:
    payload = {
        "version_no": 1,
        "content": {
            "required_profile_sections": ["BASE_PROFILE"],
            "required_indicators": [{"indicator_code": "SYNTHETIC_SIGNAL", "max_age_days": 30}],
            "allowed_states": ["VERIFIED"],
            "projection_version": 2,
            "rule_version": "synthetic-v1",
        },
        "approval_evidence_ref": "synthetic-package-v1",
        "approval_package_digest": "a" * 64,
    }
    for name, value in (("actor_user_id", 1), ("actor_role", "sys_admin"), ("professionally_approved", True)):
        with pytest.raises(ValidationError):
            ReadinessPolicyCreateRequest.model_validate({**payload, name: value})


class _DigestBox:
    def digest(self, domain: str, value: object) -> tuple[bytes, str]:
        return hashlib.sha256(f"{domain}:{value!r}".encode()).digest(), "synthetic-k1"


class _Session:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.rollback_count = 0

    async def commit(self) -> None:
        if self.error is not None:
            raise self.error

    async def rollback(self) -> None:
        self.rollback_count += 1


class _ConfirmationContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


class _Repository:
    confirmation = "UNKNOWN"

    def __init__(self, session) -> None:
        self._session = session

    async def govern_readiness_policy(self, _operation: str, payload: dict) -> dict:
        return {
            "policy_version_id": payload["policy_version_id"],
            "operation_receipt_id": payload["operation_receipt_id"],
            "_audit_id": payload["audit_id"],
            "status": "DRAFT",
            "_policy_preimage": ["ABSENT", payload["policy_version_id"]],
            "preimage_digest": "a" * 64,
            "postimage_digest": "b" * 64,
        }

    async def confirm_readiness_policy(self, _payload: dict) -> str:
        return self.confirmation


class _ReplayReceiptRepository(_Repository):
    original_receipt_id = "00000000-0000-0000-0000-00000000f001"
    original_audit_id = "00000000-0000-0000-0000-00000000f002"
    confirmation_payload: dict | None = None

    async def govern_readiness_policy(self, _operation: str, payload: dict) -> dict:
        result = await super().govern_readiness_policy(_operation, payload)
        result["operation_receipt_id"] = self.original_receipt_id
        result["_audit_id"] = self.original_audit_id
        return result

    async def confirm_readiness_policy(self, payload: dict) -> str:
        type(self).confirmation_payload = dict(payload)
        return "COMMITTED"


class _CapturingRepository(_Repository):
    def __init__(self) -> None:
        super().__init__(_Session())
        self.payloads: list[dict] = []

    async def govern_readiness_policy(self, operation: str, payload: dict) -> dict:
        self.payloads.append({"operation": operation, **payload})
        return await super().govern_readiness_policy(operation, payload)


def _run_commit_confirmation(outcome: str) -> dict:
    _Repository.confirmation = outcome
    writer = _Repository(_Session(RuntimeError("synthetic commit uncertainty")))
    confirmation_session = object()

    def factory() -> _ConfirmationContext:
        return _ConfirmationContext(confirmation_session)

    return asyncio.run(
        govern_readiness_policy(
            writer,
            operation="CREATE",
            actor_user_id=1,
            actor_role="expert",
            idempotency_key="synthetic-key",
            content={
                "required_profile_sections": ["BASE_PROFILE"],
                "required_indicators": [{"indicator_code": "SYNTHETIC_SIGNAL"}],
                "allowed_states": ["VERIFIED"],
                "projection_version": 2,
                "rule_version": "synthetic-v1",
            },
            version_no=1,
            approval_evidence_ref="synthetic-package-v1",
            approval_package_digest="a" * 64,
            confirmation_session_factory=factory,
            secrets=_DigestBox(),
        )
    )


def test_commit异常后必须以独立confirm三态收敛() -> None:
    assert _run_commit_confirmation("COMMITTED")["status"] == "DRAFT"
    with pytest.raises(HealthAssessmentError, match="DEPENDENCY_UNAVAILABLE"):
        _run_commit_confirmation("NOT_COMMITTED")
    with pytest.raises(HealthAssessmentError, match="COMMIT_OUTCOME_UNKNOWN"):
        _run_commit_confirmation("UNKNOWN")


def test_非CREATE重放commit不明必须使用可信writer首次收据定位() -> None:
    _ReplayReceiptRepository.confirmation_payload = None
    writer = _ReplayReceiptRepository(_Session(RuntimeError("synthetic commit uncertainty")))

    def factory() -> _ConfirmationContext:
        return _ConfirmationContext(object())

    result = asyncio.run(
        govern_readiness_policy(
            writer,
            operation="SUSPEND",
            actor_user_id=7,
            actor_role="sys_admin",
            idempotency_key="synthetic-replayed-key",
            policy_version_id=UUID("00000000-0000-0000-0000-00000000a001"),
            expected_version=4,
            reason_code="POLICY_SAFETY_REVIEW_REQUIRED",
            confirmation_session_factory=factory,
            secrets=_DigestBox(),
        )
    )

    assert result["operation_receipt_id"] == _ReplayReceiptRepository.original_receipt_id
    assert _ReplayReceiptRepository.confirmation_payload is not None
    assert (
        _ReplayReceiptRepository.confirmation_payload["operation_receipt_id"]
        == _ReplayReceiptRepository.original_receipt_id
    )
    assert (
        _ReplayReceiptRepository.confirmation_payload["audit_id"]
        == _ReplayReceiptRepository.original_audit_id
    )


def test_同一客户端幂等键必须保持数据库键稳定且请求变化只改变摘要() -> None:
    repository = _CapturingRepository()

    async def run() -> None:
        for version_no in (1, 2):
            await govern_readiness_policy(
                repository,
                operation="CREATE",
                actor_user_id=1,
                actor_role="expert",
                idempotency_key="synthetic-key",
                content={
                    "required_profile_sections": ["BASE_PROFILE"],
                    "required_indicators": [{"indicator_code": "SYNTHETIC_SIGNAL"}],
                    "allowed_states": ["VERIFIED"],
                    "projection_version": 2,
                    "rule_version": "synthetic-v1",
                },
                version_no=version_no,
                approval_evidence_ref="synthetic-package-v1",
                approval_package_digest="a" * 64,
                secrets=_DigestBox(),
            )

    asyncio.run(run())
    assert repository.payloads[0]["idempotency_key"] == repository.payloads[1]["idempotency_key"]
    assert repository.payloads[0]["request_digest"] != repository.payloads[1]["request_digest"]


def test_相同客户端幂等键必须按actor隔离数据库键() -> None:
    repository = _CapturingRepository()

    async def run() -> None:
        for actor_user_id in (1, 2):
            await govern_readiness_policy(
                repository,
                operation="CREATE",
                actor_user_id=actor_user_id,
                actor_role="expert",
                idempotency_key="synthetic-key",
                content={
                    "required_profile_sections": ["BASE_PROFILE"],
                    "required_indicators": [{"indicator_code": "SYNTHETIC_SIGNAL"}],
                    "allowed_states": ["VERIFIED"],
                    "projection_version": 2,
                    "rule_version": "synthetic-v1",
                },
                version_no=1,
                approval_evidence_ref="synthetic-package-v1",
                approval_package_digest="a" * 64,
                secrets=_DigestBox(),
            )

    asyncio.run(run())
    assert repository.payloads[0]["idempotency_key"] != repository.payloads[1]["idempotency_key"]


def test_API必须把commit_unknown翻译为安全503(monkeypatch) -> None:
    async def fail(*_args, **_kwargs):
        raise HealthAssessmentError("COMMIT_OUTCOME_UNKNOWN")

    async def factory(*_args):
        return object()

    monkeypatch.setattr(governance_api, "govern_readiness_policy", fail)
    monkeypatch.setattr(governance_api, "get_slice5_session_factory", factory)
    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            governance_api._run(
                "CREATE", CurrentUser(id=1, role="expert"), object(), "synthetic-key"
            )
        )
    assert captured.value.status_code == 503
    assert captured.value.detail == {
        "code": "COMMIT_OUTCOME_UNKNOWN",
        "message": "request rejected",
    }
    assert captured.value.headers == {"Cache-Control": "no-store"}


def test_真实API必须保持认证角色优先并在任何写入前拒绝发布(monkeypatch) -> None:
    calls: list[str] = []

    async def writer():
        yield object()

    async def factory(*_args):
        return object()

    async def govern(*_args, **_kwargs):
        calls.append("govern")
        return {
            "policy_version_id": "00000000-0000-0000-0000-000000000001",
            "version_no": 1,
            "status": "PUBLISHED",
            "required_profile_sections": ["BASE_PROFILE"],
            "required_indicators": [{"indicator_code": "SYNTHETIC_SIGNAL"}],
            "allowed_states": ["VERIFIED"],
            "projection_version": 2,
            "rule_version": "synthetic-v1",
            "approval_evidence_ref": "synthetic-package-v1",
            "approval_package_digest": "a" * 64,
            "policy_digest": "b" * 64,
            "effective_from": None,
            "suspended_at": None,
            "retired_at": None,
            "row_version": 4,
        }

    monkeypatch.setattr(governance_api, "get_slice5_session_factory", factory)
    monkeypatch.setattr(governance_api, "govern_readiness_policy", govern)
    app = FastAPI()
    app.include_router(governance_api.router)
    app.dependency_overrides[get_slice5_rule_governance_writer_session] = writer
    path = "/api/v1/platform/assessment-readiness-policies/00000000-0000-0000-0000-000000000001/publish"
    payload = {"expected_version": 3, "reason_code": "APPROVED_POLICY_RELEASE"}
    headers = {"Idempotency-Key": "synthetic-key"}

    assert TestClient(app).post(path, json=payload, headers=headers).status_code == 401

    async def expert() -> CurrentUser:
        return CurrentUser(id=1, role="expert")

    app.dependency_overrides[get_current_user_from_jwt] = expert
    assert TestClient(app).post(path, json=payload, headers=headers).status_code == 403

    async def admin() -> CurrentUser:
        return CurrentUser(id=2, role="sys_admin")

    app.dependency_overrides[get_current_user_from_jwt] = admin
    response = TestClient(app).post(path, json=payload, headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "POLICY_MEDICAL_APPROVAL_REQUIRED",
        "message": "request rejected",
    }
    assert response.headers["cache-control"] == "no-store"
    assert calls == []
