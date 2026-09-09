from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.database import get_slice5_rule_governance_writer_session
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.health_assessment import api, schemas, service


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "app" / "migrations" / "versions"


def _approved_payload() -> dict:
    factory = getattr(schemas, "approved_medical_rule_payload_v1", None)
    assert factory is not None, "MedicalRulePayloadV1 authoritative factory is missing"
    value = factory()
    return value.model_dump(mode="json")


def _rule_detail_payload(*, payload: dict, version: int = 1) -> dict:
    return {
        "rule_set_version_id": UUID("018f0000-0000-7000-8000-000000000071"),
        "version_no": 1,
        "status": "DRAFT",
        "module_metadata": (
            "BLOOD_PRESSURE_CARDIOVASCULAR",
            "GLUCOSE_METABOLISM",
            "LIPID_METABOLISM",
            "WEIGHT_ABDOMINAL_OBESITY",
        ),
        "author": 71,
        "reviewer": None,
        "approval_state": "PENDING",
        "effective_from": None,
        "suspended_at": None,
        "retired_at": None,
        "version": version,
        "rule_set_code": "CN_ADULT_BASELINE_V1",
        "typed_rule_payload": payload,
        "approval_evidence_ref": "synthetic-medical-double-sign",
    }


def _platform_rule_client(monkeypatch) -> TestClient:
    from app.core.middleware import add_request_middleware

    app = FastAPI()
    add_request_middleware(app)
    for router in api.routers:
        app.include_router(router)

    async def expert():
        return CurrentUser(id=71, role="expert")

    async def writer():
        yield object()

    app.dependency_overrides[get_current_user_from_jwt] = expert
    app.dependency_overrides[get_slice5_rule_governance_writer_session] = writer
    monkeypatch.setenv("KG_JWT_SECRET_KEY", "slice5-hotfix-synthetic-key-0000000000000000")
    monkeypatch.setenv("KG_SLICE5_CURSOR_SIGNING_KEY", "slice5-cursor-synthetic-key-0000000000000000")
    monkeypatch.setenv("KG_SLICE5_PUBLIC_REFERENCE_HMAC_KEY", "slice5-reference-synthetic-key-0000000000000")
    return TestClient(app, raise_server_exceptions=False)


def test_R01_R05_医学规则PayloadV1闭合且冻结获批目录() -> None:
    model = getattr(schemas, "MedicalRulePayloadV1DTO", None)
    assert model is not None
    schema = model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "rule_set_code",
        "modules",
    }
    serialized = json.dumps(schema, ensure_ascii=True)
    assert '"additionalProperties": false' in serialized
    assert '"additionalProperties": true' not in serialized

    payload = _approved_payload()
    assert payload["schema_version"] == "SLICE5_MEDICAL_RULE_PAYLOAD_V1"
    assert payload["rule_set_code"] == "CN_ADULT_BASELINE_V1"
    assert [item["module_code"] for item in payload["modules"]] == [
        "BLOOD_PRESSURE_CARDIOVASCULAR",
        "GLUCOSE_METABOLISM",
        "LIPID_METABOLISM",
        "WEIGHT_ABDOMINAL_OBESITY",
    ]
    included = [rule["rule_id"] for module in payload["modules"] for rule in module["included_rules"]]
    deferred = [rule["rule_id"] for module in payload["modules"] for rule in module["deferred_rules"]]
    golden = {ref for module in payload["modules"] for ref in module["golden_case_refs"]}
    assert len(included) == len(set(included)) == 35
    assert len(deferred) == len(set(deferred)) == 23
    assert len(golden) == 21
    assert all(rule["enabled"] is False for module in payload["modules"] for rule in module["deferred_rules"])


def test_R03_R05_未知字段延期启用和Golden引用漂移均拒绝() -> None:
    model = getattr(schemas, "MedicalRulePayloadV1DTO", None)
    assert model is not None
    payload = _approved_payload()

    with pytest.raises(ValidationError):
        model.model_validate({**payload, "free_expression": "value > 0"})

    changed = json.loads(json.dumps(payload))
    changed["modules"][0]["deferred_rules"][0]["enabled"] = True
    with pytest.raises(ValidationError):
        model.model_validate(changed)

    changed = json.loads(json.dumps(payload))
    changed["modules"][0]["golden_case_refs"][0] = "GC-UNKNOWN"
    with pytest.raises(ValidationError):
        model.model_validate(changed)


def test_R01_R02_Create与Detail使用同一闭合PayloadV1() -> None:
    create_schema = schemas.RuleSetCreateRequest.model_json_schema()
    detail_schema = schemas.RuleSetVersionDetailDTO.model_json_schema()
    assert create_schema["properties"]["typed_rule_payload"] == {
        "$ref": "#/$defs/MedicalRulePayloadV1DTO"
    }
    assert detail_schema["properties"]["typed_rule_payload"] == {
        "$ref": "#/$defs/MedicalRulePayloadV1DTO"
    }


@pytest.mark.parametrize(
    ("decision", "reason_code", "valid"),
    (
        ("APPROVE", "MEDICAL_CONTENT_APPROVED", True),
        ("APPROVE", "RULE_CONTENT_CORRECTION_REQUIRED", False),
        ("NEEDS_CORRECTION", "RULE_CONTENT_CORRECTION_REQUIRED", True),
        ("NEEDS_CORRECTION", "MEDICAL_CONTENT_APPROVED", False),
        ("NEEDS_CORRECTION", "UNKNOWN", False),
        ("NEEDS_CORRECTION", "", False),
    ),
)
def test_R09_审核决定与原因码精确绑定(decision: str, reason_code: str, valid: bool) -> None:
    values = {"expected_version": 1, "decision": decision, "reason_code": reason_code}
    if valid:
        assert schemas.RuleReviewRequest.model_validate(values).reason_code == reason_code
    else:
        with pytest.raises(ValidationError):
            schemas.RuleReviewRequest.model_validate(values)


@pytest.mark.parametrize(
    ("operation", "reason_code", "valid"),
    (
        ("PUBLISH", "DOUBLE_SIGNED_BASELINE_RELEASE", True),
        ("SUSPEND", "MEDICAL_SAFETY_REVIEW_REQUIRED", True),
        ("RESUME", "MEDICAL_SAFETY_REVIEW_CLEARED", True),
        ("RETIRE", "SUPERSEDED_BY_APPROVED_VERSION", True),
        ("SUSPEND", "MEDICAL_SAFETY_REVIEW_CLEARED", False),
        ("RETIRE", "UNKNOWN", False),
        ("PUBLISH", "", False),
    ),
)
def test_R10_治理操作与原因码精确绑定(operation: str, reason_code: str, valid: bool) -> None:
    model = schemas.RulePublishRequest if operation == "PUBLISH" else schemas.RuleGovernanceRequest
    values = {"expected_version": 1, "operation": operation, "reason_code": reason_code}
    if operation == "PUBLISH":
        values["effective_from"] = "2026-08-30T00:00:00+00:00"
    if valid:
        assert model.model_validate(values).reason_code == reason_code
    else:
        with pytest.raises(ValidationError):
            model.model_validate(values)


def test_R06_R07_草稿更新合同包含版本与闭合Payload() -> None:
    model = getattr(schemas, "RuleSetDraftUpdateRequest", None)
    assert model is not None
    schema = model.model_json_schema()
    assert set(schema["required"]) == {
        "expected_version",
        "typed_rule_payload",
        "medical_content_digest",
        "approval_evidence_ref",
    }
    source = Path(api.__file__).read_text(encoding="utf-8")
    assert '"/assessment-rule-sets/{version_id}/draft"' in source
    assert 'operation="UPDATE_DRAFT"' in source or '"UPDATE_DRAFT"' in source


def test_R06_PATCH草稿真实路由使用内部统一确认会话(monkeypatch) -> None:
    typed_payload = _approved_payload()
    digest = __import__("hashlib").sha256(
        json.dumps(
            typed_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    row = _rule_detail_payload(payload=typed_payload)
    calls: list[dict] = []

    async def rule_detail(repository, version_id):
        return row

    async def govern(operation, version_id, expected_version, key, actor, writer, extra=None):
        calls.append(
            {
                "operation": operation,
                "version_id": version_id,
                "expected_version": expected_version,
                "key": key,
                "extra": extra,
            }
        )
        return schemas.RuleSetVersionDetailDTO.model_validate(
            {
                **api._rule(row, detail=True).model_dump(),
                "version": 2,
                "approval_evidence_ref": extra["approval_evidence_ref"],
            }
        )

    monkeypatch.setattr(api, "_rule_detail", rule_detail)
    monkeypatch.setattr(api, "_govern", govern)
    with _platform_rule_client(monkeypatch) as client:
        response = client.patch(
            f"/api/v1/platform/assessment-rule-sets/{row['rule_set_version_id']}/draft",
            headers={"Idempotency-Key": "slice5-draft-route-0001"},
            json={
                "expected_version": 1,
                "typed_rule_payload": typed_payload,
                "medical_content_digest": digest,
                "approval_evidence_ref": "synthetic-medical-double-sign-v2",
            },
        )

    assert response.status_code == 200, response.json()
    assert response.json()["version"] == 2
    assert response.json()["approval_evidence_ref"] == "synthetic-medical-double-sign-v2"
    assert calls == [
        {
            "operation": "UPDATE_DRAFT",
            "version_id": row["rule_set_version_id"],
            "expected_version": 1,
            "key": "slice5-draft-route-0001",
            "extra": {
                "current_status": "DRAFT",
                "typed_rule_payload": typed_payload,
                "content_digest": digest,
                "approval_evidence_ref": "synthetic-medical-double-sign-v2",
            },
        }
    ]


@pytest.mark.parametrize("confirmed", (True, False))
def test_R24_POST创建真实路由传入unknown_commit确认工厂(monkeypatch, confirmed: bool) -> None:
    typed_payload = _approved_payload()
    digest = __import__("hashlib").sha256(
        json.dumps(
            typed_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    expected_factory = object()
    calls: list[dict] = []

    async def session_factory(role):
        assert role == "rule_governance_writer"
        return expected_factory

    async def govern(repository, **kwargs):
        calls.append(kwargs)
        assert kwargs["confirmation_session_factory"] is expected_factory
        if not confirmed:
            class CommitFailureSession:
                async def commit(self):
                    raise RuntimeError("synthetic database failure")

                async def rollback(self):
                    return None

            async def confirm():
                return service.CommitOutcome.UNKNOWN

            await service.commit_with_confirmation(
                CommitFailureSession(), confirm=confirm
            )
        return _rule_detail_payload(payload=typed_payload)

    monkeypatch.setattr(api, "get_slice5_session_factory", session_factory)
    monkeypatch.setattr(api, "govern_rule_set", govern)
    with _platform_rule_client(monkeypatch) as client:
        response = client.post(
            "/api/v1/platform/assessment-rule-sets",
            headers={"Idempotency-Key": "slice5-create-route-0001"},
            json={
                "rule_set_code": "CN_ADULT_BASELINE_V1",
                "version_no": 1,
                "typed_rule_payload": typed_payload,
                "medical_content_digest": digest,
                "approval_evidence_ref": "synthetic-medical-double-sign",
            },
        )

    assert len(calls) == 1
    if confirmed:
        assert response.status_code == 201, response.json()
        assert response.json()["status"] == "DRAFT"
    else:
        assert response.status_code == 503
        body = response.json()
        assert set(body) == {
            "code", "message", "request_id", "retryable", "field_errors",
        }
        assert body["code"] == "COMMIT_OUTCOME_UNKNOWN"
        assert body["message"] == "request rejected"
        assert UUID(body["request_id"]).version == 7
        assert body["retryable"] is False
        assert body["field_errors"] == []
        assert response.headers["Cache-Control"] == "no-store, private"
        assert response.headers["Pragma"] == "no-cache"


def test_R11_R12_公开人员引用不含内部整数或实名PII() -> None:
    public_ref = getattr(schemas, "PublicUserRefDTO", None)
    assert public_ref is not None
    properties = public_ref.model_json_schema()["properties"]
    assert set(properties) == {"public_user_ref", "display_name", "role_label"}
    assert properties["public_user_ref"]["type"] == "string"

    for dto in (schemas.RuleSetVersionDTO, schemas.RuleSetVersionDetailDTO):
        fields = dto.model_fields
        assert "author" not in fields
        assert "reviewer" not in fields
        assert "author_ref" in fields
        assert "reviewer_ref" in fields
    assert "assignee" not in schemas.HighRiskTaskDTO.model_fields
    assert "assignee_ref" in schemas.HighRiskTaskDTO.model_fields


def test_R13_R15_签名游标不可解析并绑定范围(monkeypatch) -> None:
    encoder = getattr(service, "encode_slice5_cursor", None)
    decoder = getattr(service, "decode_slice5_cursor", None)
    assert encoder is not None and decoder is not None
    monkeypatch.setenv("KG_SLICE5_CURSOR_SIGNING_KEY", "slice5-cursor-synthetic-key-0000000000000000")
    cursor_id = UUID("018f0000-0000-7000-8000-000000000001")
    scope = {
        "kind": "high-risk-task",
        "role": "org_admin",
        "tenant_id": 7,
        "status": "OPEN",
    }
    cursor = encoder(cursor_id, scope)
    assert str(cursor_id) not in cursor
    assert decoder(cursor, scope) == cursor_id
    with pytest.raises(service.HealthAssessmentError, match="INVALID_REQUEST"):
        decoder(cursor[:-1] + ("A" if cursor[-1] != "A" else "B"), scope)
    with pytest.raises(service.HealthAssessmentError, match="INVALID_REQUEST"):
        decoder(cursor, {**scope, "tenant_id": 8})


def test_R13_OpenAPI游标为opaque_string而非UUID() -> None:
    source = Path(api.__file__).read_text(encoding="utf-8")
    assert "cursor: UuidV7 | None" not in source
    assert "cursor: str | None" in source


def test_R06_R23_R24_Migration新增受限UPDATE_DRAFT且不修改历史() -> None:
    candidates = sorted(MIGRATIONS.glob("20260830_0033*_slice5*_hotfix.py"))
    assert len(candidates) == 1
    source = candidates[0].read_text(encoding="utf-8")
    assert 'revision = "20260830_0033"' in source
    assert 'down_revision = "20260827_0032"' in source
    assert "slice5_rule_governance_v2" in source
    assert "slice5_rule_governance_confirm_v1" in source
    assert "UPDATE_DRAFT" in source
    assert "slice5_audit" in source
    assert "slice5_outbox" in source
    assert "slice5_idempotency" in source
    assert "postimage_digest" in source
    assert "REVOKE ALL" in source and "FROM PUBLIC" in source
    assert "GRANT EXECUTE" in source
    assert "GRANT SELECT ON TABLE public.\"user\"" not in source
    assert "GRANT UPDATE ON TABLE public.assessment_rule_set_version" not in source
    assert "AND r.digest_key_id='SHA256_V1'" in source
    assert "AND r.reviewer_user_id IS NULL" in source
    assert "AND r.effective_from IS NULL" in source
    assert "AND r.suspended_at IS NULL" in source
    assert "AND r.retired_at IS NULL" in source
    assert "AND r.created_at=(value_payload->>'created_at')::timestamptz" in source
    assert "OR a.occurred_at=(value_payload->>'created_at')::timestamptz" in source
    assert "OR o.created_at=(value_payload->>'created_at')::timestamptz" in source
    assert "OR i.created_at=(value_payload->>'created_at')::timestamptz" in source


def test_R11_Migration安全投影不读取或公开实名() -> None:
    candidates = sorted(MIGRATIONS.glob("20260830_0033*_slice5*_hotfix.py"))
    assert len(candidates) == 1
    source = candidates[0].read_text(encoding="utf-8")
    assert "slice5_rule_set_governance_read_v2" in source
    assert "slice5_high_risk_task_read_v2" in source
    assert "real_name" not in source
    assert "therapist_profile" in source
    assert "display_name" in source


def test_R05_Migration延期规则enabled缺失或为true均fail_closed() -> None:
    candidates = sorted(MIGRATIONS.glob("20260830_0033*_slice5*_hotfix.py"))
    assert len(candidates) == 1
    source = candidates[0].read_text(encoding="utf-8")
    assert "NOT (d ? 'enabled') OR d->'enabled'<>'false'::jsonb" in source


class _CommitFails:
    async def commit(self):
        raise ConnectionError("synthetic unknown commit")

    async def rollback(self):
        return None


class _GovernanceSecrets:
    def digest(self, domain, value):
        return bytes([len(domain)]) * 32, "slice5-test-k1"


class _GovernanceRepository:
    outcome = "UNKNOWN"
    mutation_calls = 0
    confirmation_calls: list[dict] = []

    def __init__(self, session):
        self.session = session

    @classmethod
    def reset(cls, outcome: str = "UNKNOWN") -> None:
        cls.outcome = outcome
        cls.mutation_calls = 0
        cls.confirmation_calls = []

    async def govern_rule_set(self, operation, payload):
        type(self).mutation_calls += 1
        return payload["response"]

    async def confirm_rule_governance(self, payload):
        type(self).confirmation_calls.append(payload)
        return self.outcome


@asynccontextmanager
async def _confirmation_factory():
    yield object()


@pytest.mark.asyncio
async def test_R24_规则治理未知提交不得自动重放() -> None:
    _GovernanceRepository.reset()
    repository = _GovernanceRepository(_CommitFails())
    with pytest.raises(RuntimeError, match="COMMIT_OUTCOME_UNKNOWN"):
        await service.govern_rule_set(
            repository,
            operation="CREATE",
            actor_user_id=71,
            actor_role="expert",
            idempotency_key="slice5-rule-unknown-commit",
            details={
                "version_no": 1,
                "typed_rule_payload": _approved_payload(),
                "content_digest": "ab" * 32,
                "approval_evidence_ref": "synthetic",
            },
            occurred_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
            secrets=_GovernanceSecrets(),
            confirmation_session_factory=_confirmation_factory,
        )
    assert _GovernanceRepository.mutation_calls == 1
    assert len(_GovernanceRepository.confirmation_calls) == 1


@pytest.mark.asyncio
async def test_R25_CREATE确认Payload复用原mutation完整不可变事实() -> None:
    _GovernanceRepository.reset("COMMITTED")
    repository = _GovernanceRepository(_CommitFails())
    typed_rule_payload = _approved_payload()
    result = await service.govern_rule_set(
        repository,
        operation="CREATE",
        actor_user_id=71,
        actor_role="expert",
        idempotency_key="slice5-rule-confirmed-commit",
        details={
            "version_no": 1,
            "typed_rule_payload": typed_rule_payload,
            "content_digest": "ab" * 32,
            "approval_evidence_ref": None,
        },
        occurred_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        secrets=_GovernanceSecrets(),
        confirmation_session_factory=_confirmation_factory,
    )

    assert result["status"] == "DRAFT"
    assert _GovernanceRepository.mutation_calls == 1
    assert len(_GovernanceRepository.confirmation_calls) == 1
    confirmation = _GovernanceRepository.confirmation_calls[0]
    assert {
        "rule_set_code": confirmation["rule_set_code"],
        "version_no": confirmation["version_no"],
        "author_user_id": confirmation["author_user_id"],
        "actor_role": confirmation["actor_role"],
        "typed_rule_payload": confirmation["typed_rule_payload"],
        "content_digest": confirmation["content_digest"],
        "approval_evidence_ref": confirmation["approval_evidence_ref"],
        "digest_key_id": confirmation["digest_key_id"],
    } == {
        "rule_set_code": "CN_ADULT_BASELINE_V1",
        "version_no": 1,
        "author_user_id": 71,
        "actor_role": "expert",
        "typed_rule_payload": typed_rule_payload,
        "content_digest": "ab" * 32,
        "approval_evidence_ref": None,
        "digest_key_id": "slice5-test-k1",
    }
    assert confirmation["receipt_id"].version == 7
    assert confirmation["created_at"] == datetime(2026, 8, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "error_code"),
    (("NOT_COMMITTED", "DEPENDENCY_UNAVAILABLE"), ("UNKNOWN", "COMMIT_OUTCOME_UNKNOWN")),
)
async def test_R19_CREATE未提交或未知均不得自动重放(outcome: str, error_code: str) -> None:
    _GovernanceRepository.reset(outcome)
    repository = _GovernanceRepository(_CommitFails())
    with pytest.raises(RuntimeError, match=error_code):
        await service.govern_rule_set(
            repository,
            operation="CREATE",
            actor_user_id=71,
            actor_role="expert",
            idempotency_key=f"slice5-rule-{outcome.lower()}",
            details={
                "version_no": 1,
                "typed_rule_payload": _approved_payload(),
                "content_digest": "ab" * 32,
                "approval_evidence_ref": "synthetic",
            },
            occurred_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
            secrets=_GovernanceSecrets(),
            confirmation_session_factory=_confirmation_factory,
        )
    assert _GovernanceRepository.mutation_calls == 1
    assert len(_GovernanceRepository.confirmation_calls) == 1
