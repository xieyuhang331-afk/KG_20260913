from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.service_fulfillment.models import (
    ProxyMajorAuthorizationModel,
    ServiceTransferContinuationHandoffModel,
)
from app.modules.service_fulfillment.schemas import (
    ContinuationCaseLinkRequest,
    ProxyMajorAuthorizationCreateRequest,
)


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"
API = ROOT / "app/modules/service_fulfillment/api.py"
SERVICE = ROOT / "app/modules/service_fulfillment/service.py"
MEMBER_SERVICE = ROOT / "app/modules/member_enrollment/service.py"
MEMBER_REPOSITORY = ROOT / "app/modules/member_enrollment/repository.py"
MEMBER_PORTS = ROOT / "app/modules/member_enrollment/ports.py"

UUID7_A = UUID("0198f1c0-0000-7000-8000-000000000001")
UUID7_B = UUID("0198f1c0-0000-7000-8000-000000000002")


def test_整改B_handoff是独立追加式接续记录且不创建正式case() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert '"service_transfer_continuation_handoff"' in source
    assert "uq_service_transfer_continuation_handoff_transfer" in source
    assert "PENDING_TARGET_ENROLLMENT" in source
    assert "CONTINUATION_CASE_LINKED" in source
    assert "linked_enrollment_id" in source
    assert "linked_service_case_id" in source

    branch = source[source.index("operation_name='COORDINATE_TRANSFER_CLOSE'") :]
    branch = branch[: branch.index("ELSIF", 1)]
    assert "INSERT INTO public.service_transfer_continuation_handoff" in branch
    assert "INSERT INTO public.service_case(" not in branch
    assert "'TRANSFERRED'" in branch


def test_整改B_正式状态链和接续关联API均存在() -> None:
    source = API.read_text(encoding="utf-8")
    assert '"/service-transfers/{transfer_id}/start-review"' in source
    assert '"START_REVIEW_TRANSFER"' in source
    assert '"/service-transfers/{transfer_id}/continuation-handoff"' in source
    assert '"/service-transfers/{transfer_id}/continuation-case"' in source
    assert '"LINK_CONTINUATION_CASE"' in source


def test_整改B_handoff模型只允许一次关联() -> None:
    constraints = {
        constraint.name
        for constraint in ServiceTransferContinuationHandoffModel.__table__.constraints
        if constraint.name
    }
    assert "uq_service_transfer_continuation_handoff_transfer" in constraints
    assert any(
        name.endswith("ck_service_transfer_continuation_handoff_status")
        for name in constraints
    )
    request = ContinuationCaseLinkRequest(
        new_service_case_id=UUID7_A,
        expected_version=1,
    )
    assert request.new_service_case_id == UUID7_A


def test_整改B_重大代理权限必须逐项且禁止重复() -> None:
    assert ProxyMajorAuthorizationModel.__tablename__ == "proxy_major_authorization"
    request = ProxyMajorAuthorizationCreateRequest(
        proxy_grant_id=UUID7_A,
        authorization_document_version_id=UUID7_B,
        witness_decision_id=UUID("0198f1c0-0000-7000-8000-000000000003"),
        permission_codes=("SERVICE_TRANSFER", "PERSONAL_DATA_EXPORT"),
        valid_until=None,
    )
    assert request.permission_codes == ("SERVICE_TRANSFER", "PERSONAL_DATA_EXPORT")
    with pytest.raises(ValidationError, match="DUPLICATE_PERMISSION_CODE"):
        ProxyMajorAuthorizationCreateRequest(
            proxy_grant_id=UUID7_A,
            authorization_document_version_id=UUID7_B,
            witness_decision_id=UUID("0198f1c0-0000-7000-8000-000000000003"),
            permission_codes=("SERVICE_TRANSFER", "SERVICE_TRANSFER"),
            valid_until=None,
        )


def test_整改B_数据库按操作冻结source_target_currentness和重大权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for token in (
        "START_REVIEW_TRANSFER",
        "LINK_CONTINUATION_CASE",
        "AUTHORIZE_PROXY_MAJOR",
        "REVOKE_PROXY_MAJOR",
        "PLAN_DECISION",
        "SERVICE_WITHDRAW",
        "SERVICE_TRANSFER",
        "PERSONAL_DATA_EXPORT",
        "qualification_valid_until>=CURRENT_DATE",
        "readiness_status='SERVICE_READY'",
        "target_service_tags",
        "result->>'case_status' IN",
    ):
        assert token in source


def test_整改D_代理导出从创建到下载均实时消费精确重大权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE_EXPORT_FOR_SUBJECT" in source
    assert "authority_operation" in source
    assert "authority_target_id" in source
    export_authority = source[
        source.index("CREATE_EXPORT_FOR_SUBJECT") : source.index(
            "PERFORM 1 FROM public", source.index("CREATE_EXPORT_FOR_SUBJECT")
        )
    ]
    assert "PERSONAL_DATA_EXPORT" in export_authority
    assert "slice7_proxy_major_current_v1" in export_authority
    for operation in ("CANCEL_EXPORT", "EXPORT_DOWNLOAD_ACCESS", "READ_EXPORT"):
        assert operation in export_authority
    assert "target_ia.service_tags" not in source
    assert "target_ia.draft_payload->'service_tags'" in source
    assert "INSERT INTO public.proxy_major_authorization" in source
    assert "UPDATE public.proxy_major_authorization" not in source


def test_整改B_transferred终止旧入组且新case唯一性依赖最新生命周期() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    branch = source[source.index("operation_name='COORDINATE_TRANSFER_CLOSE'") :]
    branch = branch[: branch.index("ELSIF", 1)]
    assert "UPDATE public.service_enrollment SET status='REVOKED'" in branch
    assert "service_case_current_guard" in source
    assert "pg_advisory_xact_lock" in source
    assert "uq_service_case_active_subject" in source
    assert "DROP INDEX" in source
    assert "CREATE UNIQUE INDEX" in source
    assert "TRANSFERRED" in source


def test_整改B_实名复用V2必须精确接线且保持原claim不变() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    repository = MEMBER_REPOSITORY.read_text(encoding="utf-8")
    service = MEMBER_SERVICE.read_text(encoding="utf-8")
    ports = MEMBER_PORTS.read_text(encoding="utf-8")
    for token in (
        "slice7_identity_claim_reuse_v2",
        "IDENTITY_REUSE_MEMBER_MISMATCH",
        "IDENTITY_REUSE_FINGERPRINT_MISMATCH",
        "IDENTITY_REUSE_SOURCE_NOT_CURRENT",
        "outcome:='REUSED'",
        "existing.claim_id",
        "to_jsonb(existing)",
        "existing.slice3_revision_id",
        "existing.slice3_decision_id",
    ):
        assert token in migration
    assert "claim_or_reuse_identity_subject" in repository
    assert "slice7_identity_claim_reuse_v2" in repository
    assert "expected_fields" in repository
    assert "resolved_claim" in repository
    assert "claim_or_reuse_identity_subject" in service
    assert "claim_or_reuse_identity_subject" in ports
    assert "UPDATE identity.identity_subject_claim_registry" not in migration
    assert "DELETE FROM identity.identity_subject_claim_registry" not in migration


def test_整改B_复用authority仅限原Slice3审核writer且不扩权() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    branch = source[source.index("slice7_identity_claim_reuse_v2") :]
    assert "session_user" in branch
    assert "member_identity_review" in source.lower()
    assert "REVOKE ALL ON FUNCTION identity.slice7_identity_claim_reuse_v2" in source
    assert "GRANT EXECUTE ON FUNCTION identity.slice7_identity_claim_reuse_v2" in source
    assert "GRANT SELECT ON identity.identity_subject_claim_registry" not in source


def test_整改B_transfer相同请求先重放receipt再读取终态authority() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    branch = source[source.index("async def transfer_transition") :]
    branch = branch[: branch.index("async def major_authorization_transition")]
    assert "self.repository.replay" in branch
    assert branch.index("self.repository.replay") < branch.index("self._authorized")
