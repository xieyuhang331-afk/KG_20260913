from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

VALID_ID_UPPER = "11010519491231002X"
VALID_ID_LOWER = "11010519491231002x"
MASKED_ID = "110105********002X"


def _jwt_headers(*, user_id: int = 42, role: str = "member") -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    from app.core.database import get_db_session
    from app.main import create_app

    app = create_app()

    async def fake_session():
        yield SimpleNamespace()

    app.dependency_overrides[get_db_session] = fake_session
    return TestClient(app)


def _identity_with_invalid_birth_date() -> str:
    body = "11010519990230001"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_codes = "10X98765432"
    return body + check_codes[
        sum(
            int(digit) * weight
            for digit, weight in zip(body, weights, strict=True)
        )
        % 11
    ]


def test_A2_R05_formal非法身份证HTTP422稳定脱敏且认证优先(caplog) -> None:
    invalid_values = (
        VALID_ID_UPPER[:-1],
        VALID_ID_UPPER[:-1] + "A",
        VALID_ID_UPPER[:-1] + "1",
        _identity_with_invalid_birth_date(),
        VALID_ID_LOWER[:-2] + "1x",
    )
    client = _client()
    payload = {
        "real_name": "合成人员",
        "id_card": "",
        "idempotency_key": "synthetic-invalid-identity-v1",
        "consent_version": "v1",
    }
    observations: dict[str, bool] = {}

    with patch(
        "app.modules.auth.api._identity_submission_service",
        side_effect=AssertionError("invalid identity reached the service"),
    ) as service_factory:
        for index, invalid_value in enumerate(invalid_values):
            payload["id_card"] = invalid_value
            response = client.put(
                "/api/v1/users/me/identity-verification",
                headers=_jwt_headers(),
                json=payload,
            )
            observations[f"case_{index}_status"] = response.status_code == 422
            observations[f"case_{index}_content_type"] = response.headers.get(
                "content-type", ""
            ).startswith("application/json")
            observations[f"case_{index}_cache"] = (
                response.headers.get("cache-control") == "no-store"
            )
            observations[f"case_{index}_body"] = response.json() == {
                "code": "IDENTITY_VERIFICATION_INPUT_INVALID",
                "message": "request rejected",
            }
            public_text = response.text
            observations[f"case_{index}_response_safe"] = not any(
                invalid_value[start : start + 6] in public_text
                for start in range(len(invalid_value) - 5)
            )

        payload["id_card"] = invalid_values[0]
        observations["missing_auth_401"] = (
            client.put(
                "/api/v1/users/me/identity-verification",
                json=payload,
            ).status_code
            == 401
        )
        observations["invalid_auth_401"] = (
            client.put(
                "/api/v1/users/me/identity-verification",
                headers={"Authorization": "Bearer malformed"},
                json=payload,
            ).status_code
            == 401
        )

    log_text = caplog.text
    observations["logs_safe"] = not any(
        invalid_value in log_text for invalid_value in invalid_values
    )
    observations["service_not_called"] = service_factory.call_count == 0

    failed = [name for name, passed in observations.items() if not passed]
    assert not failed, failed


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/v1/users/{user_id}/identity", "LEGACY_IDENTITY_ENDPOINT_RETIRED"),
        (
            "/api/v1/users/{user_id}/tenant-binding",
            "LEGACY_MEMBER_TENANT_BINDING_RETIRED",
        ),
    ],
)
def test_A2_R01_R02_旧入口OpenAPI明确deprecated与410(path: str, code: str) -> None:
    document = _client().get("/openapi.json").json()
    operation = document["paths"][path]["post"]

    assert operation["deprecated"] is True
    assert "410" in operation["responses"]
    assert "Cache-Control" in operation["responses"]["410"]["headers"]
    schema_ref = operation["responses"]["410"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    schema = document["components"]["schemas"][schema_ref.rsplit("/", 1)[-1]]
    assert code in repr(schema)


@pytest.mark.parametrize(
    ("path", "payload", "code"),
    [
        (
            "/api/v1/users/42/identity",
            {"real_name": "synthetic", "id_card": "110101199001011234"},
            "LEGACY_IDENTITY_ENDPOINT_RETIRED",
        ),
        (
            "/api/v1/users/42/tenant-binding",
            {"tenant_id": 7},
            "LEGACY_MEMBER_TENANT_BINDING_RETIRED",
        ),
    ],
)
def test_A2_R03_R04_R18_R19_R22_旧入口认证后稳定410且零写入(
    path: str, payload: dict[str, object], code: str
) -> None:
    missing = _client().post(path, json=payload)
    malformed = _client().post(
        path, json=payload, headers={"Authorization": "Bearer malformed"}
    )
    retired = _client().post(path, json=payload, headers=_jwt_headers())

    assert missing.status_code == 401
    assert malformed.status_code == 401
    assert retired.status_code == 410
    assert retired.headers["cache-control"] == "no-store"
    assert retired.json() == {"code": code, "message": "request rejected"}
    public = retired.text.lower()
    for forbidden in ("42", "synthetic", VALID_ID_UPPER.lower(), "tenant_id", "traceback"):
        assert forbidden not in public
    api_source = (
        Path(__file__).resolve().parents[1] / "app/modules/auth/api.py"
    ).read_text(encoding="utf-8")
    assert "await submit_user_identity(" not in api_source
    assert "await bind_user_tenant(" not in api_source


def test_A2_R03_R04_旧服务与仓储写入口同样fail_closed且零副作用() -> None:
    from fastapi import HTTPException

    from app.core.security import CurrentUser
    from app.modules.auth.repository import (
        update_user_identity,
        update_user_tenant_binding,
    )
    from app.modules.auth.schemas import TenantBindingRequest, UserIdentityRequest
    from app.modules.auth.service import bind_user_tenant, submit_user_identity

    class NoDatabaseSession:
        async def execute(self, _statement):
            raise AssertionError("retired service reached the database")

        async def flush(self):
            raise AssertionError("retired repository writer attempted to flush")

        async def commit(self):
            raise AssertionError("retired writer attempted to commit")

        async def rollback(self):
            raise AssertionError("retired writer attempted to rollback")

    async def exercise() -> None:
        session = NoDatabaseSession()
        current_user = CurrentUser(id=42, role="member")
        calls = (
            submit_user_identity(
                session,
                current_user,
                42,
                UserIdentityRequest(
                    real_name="synthetic",
                    id_card="110101199001011234",
                ),
            ),
            bind_user_tenant(
                session,
                current_user,
                42,
                TenantBindingRequest(tenant_id=7),
            ),
        )
        for call in calls:
            with pytest.raises(HTTPException) as caught:
                await call
            assert caught.value.status_code == 410

        identity_user = SimpleNamespace(
            real_name=None,
            id_card=None,
            verify_status=None,
        )
        tenant_user = SimpleNamespace(tenant_id=None)
        with pytest.raises(RuntimeError, match="LEGACY_IDENTITY_ENDPOINT_RETIRED"):
            await update_user_identity(
                session,
                identity_user,
                real_name="synthetic",
                id_card="110101199001011234",
                verify_status="submitted",
            )
        with pytest.raises(
            RuntimeError,
            match="LEGACY_MEMBER_TENANT_BINDING_RETIRED",
        ):
            await update_user_tenant_binding(session, tenant_user, tenant_id=7)
        assert (identity_user.real_name, identity_user.id_card, identity_user.verify_status) == (
            None,
            None,
            None,
        )
        assert tenant_user.tenant_id is None

    asyncio.run(exercise())


def test_A2_R05_R07_R09_共享身份证值对象规范化校验与掩码() -> None:
    from app.modules.auth.identity_document import (
        canonicalize_prc_resident_identity,
        mask_prc_resident_identity,
    )

    assert canonicalize_prc_resident_identity(f"  {VALID_ID_LOWER}  ") == VALID_ID_UPPER
    assert mask_prc_resident_identity(VALID_ID_LOWER) == MASKED_ID

    invalid_birth_body = "11010519990230001"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_codes = "10X98765432"
    invalid_birth = invalid_birth_body + check_codes[
        sum(
            int(digit) * weight
            for digit, weight in zip(invalid_birth_body, weights, strict=True)
        )
        % 11
    ]
    for value in ("110105194912310021", invalid_birth, "not-an-identity"):
        with pytest.raises(ValueError, match="IDENTITY_DOCUMENT_INVALID"):
            canonicalize_prc_resident_identity(value)


def test_A2_R05_正式账号与Slice3请求都返回同一canonical值() -> None:
    from app.modules.auth.schemas import IdentityVerificationSubmissionRequest
    from app.modules.member_enrollment.schemas import IdentitySubmissionRequest

    account = IdentityVerificationSubmissionRequest.model_validate(
        {
            "real_name": "张三",
            "id_card": f" {VALID_ID_LOWER} ",
            "idempotency_key": "synthetic-submit-v1",
            "consent_version": "v1",
        }
    )
    enrollment = IdentitySubmissionRequest.model_validate(
        {
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "张三",
            "id_number": f" {VALID_ID_LOWER} ",
            "expected_version": 1,
        }
    )

    assert account.id_card == enrollment.id_number == VALID_ID_UPPER


def test_A2_R06_R08_R14_R15_账号与Slice3指纹统一且不同证件保持隔离() -> None:
    from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets

    key = b"H" * 32
    crypto = IdentitySubmissionCrypto(
        encryption_key=b"E" * 32, hmac_key=key, key_id="synthetic-v1"
    )
    secrets = object.__new__(MemberEnrollmentSecrets)
    secrets.fingerprint_key_id = "synthetic-v1"
    secrets.fingerprint_keys = {"synthetic-v1": key}

    upper = crypto.digest("id-card", VALID_ID_UPPER)
    lower = crypto.digest("id-card", VALID_ID_LOWER)
    slice3_key_id, slice3_lower = secrets.identity_fingerprint(VALID_ID_LOWER)

    assert upper == lower == slice3_lower
    assert slice3_key_id == crypto.key_id
    assert crypto.digest("id-card", "110105194912310011") != upper


class _SubmissionRepository:
    def __init__(self) -> None:
        self.added: list[dict[str, object]] = []
        self.replay = None

    async def lock_user(self, user_ref: int):
        return SimpleNamespace(
            id=user_ref,
            role="member",
            status="active",
            tenant_id=None,
            verify_status="unverified",
        )

    async def find_by_idempotency(self, user_ref: int, digest: str):
        return self.replay

    async def find_latest(self, user_ref: int):
        return None

    async def count_since(self, user_ref: int, since: datetime) -> int:
        return 0

    async def mark_user_submitted(self, user_ref: int) -> None:
        return None

    async def add(self, **values):
        self.added.append(values)
        return SimpleNamespace(
            status="submitted",
            version=values["version"],
            id_card_masked=values["id_card_masked"],
            submitted_at=values["submitted_at"],
            decided_at=None,
            rejection_reason_code=None,
            content_digest=values["content_digest"],
        )

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _submission_service(repository: _SubmissionRepository):
    from app.modules.auth.identity_submission import IdentitySubmissionService
    from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto

    return IdentitySubmissionService(
        repository=repository,
        crypto=IdentitySubmissionCrypto(
            encryption_key=b"E" * 32,
            hmac_key=b"H" * 32,
            key_id="synthetic-v1",
        ),
        clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
    )


def _submission_request(card: str):
    return SimpleNamespace(
        real_name="张三",
        id_card=card,
        idempotency_key="same-synthetic-key",
        consent_version="v1",
    )


def test_A2_R10_R11_同一证件xX幂等且formal只写加密Submission() -> None:
    current_user = SimpleNamespace(id=42, role="member", tenant_id=None, org_id=None)
    initial = _SubmissionRepository()
    asyncio.run(
        _submission_service(initial).submit(
            current_user=current_user, request=_submission_request(VALID_ID_LOWER)
        )
    )
    stored = initial.added[0]
    assert stored["id_card_masked"] == MASKED_ID
    assert VALID_ID_UPPER not in repr(stored)
    assert VALID_ID_LOWER not in repr(stored)

    replay = _SubmissionRepository()
    replay.replay = SimpleNamespace(
        status="submitted",
        version=1,
        id_card_masked=stored["id_card_masked"],
        submitted_at=stored["submitted_at"],
        decided_at=None,
        rejection_reason_code=None,
        content_digest=stored["content_digest"],
    )
    result = asyncio.run(
        _submission_service(replay).submit(
            current_user=current_user, request=_submission_request(VALID_ID_UPPER)
        )
    )
    assert result.outcome == "REPLAYED"
    assert replay.added == []


def test_A2_R12_R13_无formal_submission不得制造verified事实() -> None:
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewConflict,
        PlatformAdminManualIdentityReviewRequest,
    )
    from app.modules.auth.manual_identity_review_repository import (
        SqlAlchemyManualIdentityReviewAuthorityPort,
    )

    request = PlatformAdminManualIdentityReviewRequest(
        idempotency_key="synthetic-manual-review-v1",
        decided_at=datetime(2026, 9, 1, tzinfo=UTC),
        evidence_digest="a" * 64,
    )
    port = SqlAlchemyManualIdentityReviewAuthorityPort(
        session_factory=object(),
        reviewer_subject_id=17,
        user_ref=42,
        request=request,
        authority_decision_id="manual-review-" + "c" * 64,
    )
    snapshot = SimpleNamespace(
        reviewer=SimpleNamespace(
            id=17,
            role="super_admin",
            status="active",
            tenant_id=None,
            org_id=None,
        ),
        subject=SimpleNamespace(
            id=42,
            role="member",
            status="active",
            tenant_id=None,
            verify_status="submitted",
        ),
        classification=SimpleNamespace(
            user_ref=42, account_class="natural_person", facts_version=1
        ),
        verification=None,
        submission=None,
    )

    with pytest.raises(PlatformAdminManualIdentityReviewConflict):
        port._build_decision(snapshot)


def test_A2_R16_R17_member历史tenant绑定fail_closed且其他角色边界未被重写() -> None:
    from app.modules.auth.identity_submission import (
        IdentitySubmissionForbidden,
        IdentitySubmissionService,
    )

    service = object.__new__(IdentitySubmissionService)
    with pytest.raises(IdentitySubmissionForbidden):
        service._require_member(
            SimpleNamespace(id=42, role="member", tenant_id=7, org_id=None)
        )
    for role in ("org_admin", "therapist"):
        with pytest.raises(IdentitySubmissionForbidden):
            service._require_member(
                SimpleNamespace(id=42, role=role, tenant_id=7, org_id=7)
            )


def test_A2_R20_正式身份GET与PUT均声明并返回no_store() -> None:
    class FakeService:
        async def status(self, **kwargs):
            return SimpleNamespace(
                status="pending",
                submission_version=None,
                id_card_masked=None,
                submitted_at=None,
                decided_at=None,
                rejection_reason_code=None,
                resubmit_available_at=None,
            )

        async def submit(self, **kwargs):
            return SimpleNamespace(
                status="submitted",
                submission_version=1,
                id_card_masked=MASKED_ID,
                submitted_at=datetime(2026, 9, 1, tzinfo=UTC),
                outcome="CREATED",
            )

    with patch("app.modules.auth.api._identity_submission_service", return_value=FakeService()):
        client = _client()
        get_response = client.get(
            "/api/v1/users/me/identity-verification", headers=_jwt_headers()
        )
        put_response = client.put(
            "/api/v1/users/me/identity-verification",
            headers=_jwt_headers(),
            json={
                "real_name": "张三",
                "id_card": VALID_ID_UPPER,
                "idempotency_key": "synthetic-submit-v1",
                "consent_version": "v1",
            },
        )

    assert get_response.status_code == put_response.status_code == 200
    assert get_response.headers["cache-control"] == "no-store"
    assert put_response.headers["cache-control"] == "no-store"
    openapi = client.get("/openapi.json").json()["paths"]
    for method in ("get", "put"):
        headers = openapi["/api/v1/users/me/identity-verification"][method][
            "responses"
        ]["200"]["headers"]
        assert "Cache-Control" in headers


def test_A2_R23_R28_A2_1无Migration且仅允许获批A2_2_Migration链() -> None:
    root = Path(__file__).resolve().parents[1]
    migrations = root / "app/migrations/versions"
    assert sorted(path.name for path in migrations.glob("*a2*")) == [
        "20260901_0034_a2_identity_inventory_closed_read_boundary.py",
        "20260902_0035_a2_identity_remediation_ledger.py",
    ]
    assert "20260902_0035" in (
        root / "tests/integration/conftest.py"
    ).read_text(encoding="utf-8")
