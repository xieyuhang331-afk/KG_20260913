import inspect
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.institution_onboarding.models import InstitutionOnboardingAuditModel
from app.modules.institution_onboarding.models import InstitutionOnboardingIdempotencyModel
from app.modules.institution_onboarding.repository import InstitutionOnboardingRepository


class _Session:
    def __init__(self) -> None:
        self.statement = None

    def add(self, _value) -> None:
        raise AssertionError("audit persistence must not use ORM add")

    async def flush(self) -> None:
        raise AssertionError("audit persistence must not flush ORM state")

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(scalar_one=lambda: 41)


class _ReadSession:
    def __init__(self, value) -> None:
        self.value = value
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(scalar_one_or_none=lambda: self.value)


class _UpdateSession:
    def __init__(self) -> None:
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(rowcount=1)


@pytest.mark.asyncio
async def test_审计写入不使用隐式RETURNING且不需要读权限():
    session = _Session()
    repository = InstitutionOnboardingRepository(session)
    audit = InstitutionOnboardingAuditModel(
        actor_user_id=7,
        actor_role="super_admin",
        action="INVITATION_CREATE",
        object_type="INSTITUTION_INVITATION",
        object_id="018f47b0-7f00-7000-8000-000000000001",
        result="SUCCESS",
        reason_code=None,
        request_id="request-safe",
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )

    await repository.add_audit(audit)

    assert session.statement.table.name == "institution_onboarding_audit"
    assert session.statement._inline is True
    assert not session.statement._returning
    assert "audit_id" not in session.statement._values


@pytest.mark.asyncio
async def test_激活用户仅显式返回必需主键且回填身份():
    map_core_model_classes()
    session = _Session()
    repository = InstitutionOnboardingRepository(session)
    user = SimpleNamespace(
        id=None,
        phone="synthetic-mobile",
        password_hash="test-only",
        role="org_admin",
        status="active",
        tenant_id=None,
    )

    await repository.add_onboarding_user(user)

    assert session.statement.table.name == "user"
    assert tuple(column.name for column in session.statement._returning) == ("id",)
    assert {column.name for column in session.statement._values} == {
        "phone",
        "password_hash",
        "role",
        "status",
        "tenant_id",
    }
    assert user.id == 41


@pytest.mark.asyncio
async def test_审核读取邀请不要求额外更新权限():
    invitation = object()
    session = _ReadSession(invitation)
    repository = InstitutionOnboardingRepository(session)

    result = await repository.get_invitation("018f47b0-7f00-7000-8000-000000000001")

    assert result is invitation
    assert session.statement._for_update_arg is None


@pytest.mark.asyncio
async def test_审核创建租户仅显式返回必需主键():
    map_core_model_classes()
    session = _Session()
    repository = InstitutionOnboardingRepository(session)
    tenant = SimpleNamespace(
        id=None,
        org_id=4,
        tenant_code="KL-TEST",
        name="test",
        type="store",
        credit_code="test-credit",
        legal_person_name="test-person",
        province="test-region",
        city="test-region",
        district="test-region",
        address="test-address",
        contact_name="test-contact",
        contact_phone=None,
        contact_email="test@example.invalid",
        status="active",
        reviewed_by=7,
        reviewed_at=datetime(2026, 8, 14, tzinfo=UTC),
        approved_at=datetime(2026, 8, 14, tzinfo=UTC),
    )

    await repository.add_approved_tenant(tenant)

    assert session.statement.table.name == "tenant"
    assert tuple(column.name for column in session.statement._returning) == ("id",)
    assert {column.name for column in session.statement._values} == {
        "org_id", "tenant_code", "name", "type", "credit_code",
        "legal_person_name", "province", "city", "district", "address",
        "contact_name", "contact_phone", "contact_email", "status",
        "reviewed_by", "reviewed_at", "approved_at",
    }
    assert tenant.id == 41


@pytest.mark.asyncio
async def test_审核绑定用户租户不读取未授权整行():
    map_core_model_classes()
    session = _UpdateSession()
    repository = InstitutionOnboardingRepository(session)

    await repository.bind_user_tenant(user_id=7, tenant_id=41)

    assert session.statement.table.name == "user"
    assert not session.statement._returning
    assert {column.name for column in session.statement._values} == {"tenant_id"}


@pytest.mark.asyncio
async def test_幂等写入使用自然键且不需要代理键sequence():
    session = _Session()
    repository = InstitutionOnboardingRepository(session)
    value = InstitutionOnboardingIdempotencyModel(
        actor_scope="actor-safe",
        operation="REVIEW_DECISION",
        idempotency_key="key-safe",
        request_digest="0" * 64,
        response_payload={"status": "APPROVED"},
        created_at=datetime(2026, 8, 14, tzinfo=UTC),
    )

    await repository.add_idempotency(value)

    assert session.statement.table.name == "institution_onboarding_idempotency"
    assert session.statement._inline is True
    assert not session.statement._returning
    assert {column.name for column in session.statement._values} == {
        "actor_scope", "operation", "idempotency_key", "request_digest",
        "response_payload", "created_at",
    }


def test_许可证读取只投影公开绑定字段而不加载密文列():
    source = inspect.getsource(
        InstitutionOnboardingRepository.licenses_for_application
    )
    assert "select(InstitutionLicenseModel)" not in source
    assert "InstitutionLicenseModel.license_type" in source
    assert "InstitutionLicenseModel.private_file_id" in source
    assert "license_no_ciphertext" not in source
    assert "license_no_digest" not in source
