from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "app/migrations/versions/20260909_0040_认证主体与当前身份受限读取.py"
)


def test_0040线性新增两个认证受限读取接口且不授予基础表权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260909_0040"' in source
    assert 'down_revision = "20260906_0039"' in source
    assert "auth_login_subject_v1(p_phone VARCHAR(11))" in source
    assert "auth_user_currentness_v1(p_user_id BIGINT)" in source
    assert source.count("SECURITY DEFINER") == 2
    assert source.count("SET search_path = pg_catalog") == 2
    assert "session_user" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert 'GRANT SELECT ON TABLE public."user"' not in source
    assert "GRANT SELECT ON TABLE public.tenant" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT DELETE" not in source


def test_登录受限接口只返回冻结字段并拒绝非法参数() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    login = source.split("CREATE FUNCTION public.auth_login_subject_v1", 1)[1].split(
        "CREATE FUNCTION public.auth_user_currentness_v1", 1
    )[0]
    for field in (
        "id",
        "phone",
        "password_hash",
        "role",
        "status",
        "tenant_id",
        "exited_at",
        "deletion_requested_at",
        "tenant_org_id",
    ):
        assert field in login
    for forbidden in (
        "real_name",
        "id_card",
        "gender",
        "birth_date",
        "avatar_url",
        "specialties",
    ):
        assert forbidden not in login
    assert "p_phone IS NULL" in login
    assert "length(p_phone) <> 11" in login
    assert "p_phone !~ '^1[0-9]{{10}}$'" in login
    assert "SELECT *" not in login.upper()


def test_当前身份接口只返回令牌失效所需字段并验证正整数ID() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    currentness = source.split(
        "CREATE FUNCTION public.auth_user_currentness_v1", 1
    )[1].split("def downgrade", 1)[0]
    for field in (
        "id",
        "role",
        "tenant_id",
        "status",
        "exited_at",
        "deletion_requested_at",
        "tenant_org_id",
    ):
        assert field in currentness
    assert "p_user_id IS NULL OR p_user_id <= 0" in currentness
    assert "password_hash" not in currentness
    assert "phone" not in currentness


def test_Repository通过受限函数返回内部DTO且repr不暴露密码摘要() -> None:
    from app.modules.auth import repository

    login_source = inspect.getsource(repository.get_user_by_phone)
    currentness_source = inspect.getsource(repository.get_user_currentness)
    assert "auth_login_subject_v1" in login_source
    assert "auth_user_currentness_v1" in currentness_source
    assert "select(User" not in login_source + currentness_source
    assert "outerjoin(Tenant" not in login_source + currentness_source
    subject = repository.AuthenticationSubject(
        id=1,
        phone="19900000000",
        password_hash="synthetic-secret-digest",
        role="member",
        status="active",
        tenant_id=None,
        exited_at=None,
        deletion_requested_at=None,
        tenant_org_id=None,
    )
    assert "synthetic-secret-digest" not in repr(subject)


def test_Repository将真实SQLAlchemy_RowMapping严格转换为内部DTO() -> None:
    from app.modules.auth import repository

    engine = create_engine("sqlite://")
    with engine.connect() as connection:

        class Session:
            async def execute(self, _statement):
                return connection.execute(
                    text(
                        "SELECT 1 AS id, '19900000000' AS phone, "
                        "'synthetic-secret-digest' AS password_hash, "
                        "'member' AS role, 'active' AS status, "
                        "NULL AS tenant_id, NULL AS exited_at, "
                        "NULL AS deletion_requested_at, NULL AS tenant_org_id"
                    )
                )

        subject = asyncio.run(repository.get_user_by_phone(Session(), "19900000000"))

    assert type(subject) is repository.AuthenticationSubject
    assert "synthetic-secret-digest" not in repr(subject)


@pytest.mark.parametrize(
    ("query", "loader"),
    (
        (
            "SELECT 1 AS id, 'member' AS role, NULL AS tenant_id, "
            "'active' AS status, NULL AS exited_at, "
            "NULL AS deletion_requested_at, NULL AS tenant_org_id WHERE 0",
            "currentness",
        ),
        (
            "SELECT 1 AS id, '19900000000' AS phone, "
            "'synthetic-secret-digest' AS password_hash, 'member' AS role, "
            "'active' AS status, NULL AS tenant_id, NULL AS exited_at, "
            "NULL AS deletion_requested_at, NULL AS tenant_org_id WHERE 0",
            "login",
        ),
    ),
)
def test_Repository真实SQLAlchemy空结果严格返回None(query: str, loader: str) -> None:
    from app.modules.auth import repository

    engine = create_engine("sqlite://")
    with engine.connect() as connection:

        class Session:
            async def execute(self, _statement):
                return connection.execute(text(query))

        result = asyncio.run(
            repository.get_user_currentness(Session(), 1)
            if loader == "currentness"
            else repository.get_user_by_phone(Session(), "19900000000")
        )

    assert result is None


@pytest.mark.parametrize(
    "query",
    (
        "SELECT 1 AS id, '19900000000' AS phone, 'member' AS role, "
        "'active' AS status, NULL AS tenant_id, NULL AS exited_at, "
        "NULL AS deletion_requested_at, NULL AS tenant_org_id",
        "SELECT 1 AS id, '19900000000' AS phone, "
        "'synthetic-secret-digest' AS password_hash, 'member' AS role, "
        "'active' AS status, NULL AS tenant_id, NULL AS exited_at, "
        "NULL AS deletion_requested_at, NULL AS tenant_org_id, "
        "'must-not-leak' AS unexpected_field",
    ),
)
def test_Repository真实SQLAlchemy缺列或多列时FailClosed且不回显记录(query: str) -> None:
    from app.modules.auth import repository

    engine = create_engine("sqlite://")
    with engine.connect() as connection:

        class Session:
            async def execute(self, _statement):
                return connection.execute(text(query))

        with pytest.raises(TypeError) as caught:
            asyncio.run(repository.get_user_by_phone(Session(), "19900000000"))

    public = str(caught.value)
    assert "synthetic-secret-digest" not in public
    assert "must-not-leak" not in public


def test_Login使用受限结果携带的租户组织上下文且不直接查询Tenant() -> None:
    from app.modules.auth import service

    source = inspect.getsource(service)
    assert "user.tenant_org_id" in source
    assert "async def _tenant_org_id" in source
    assert "select(Tenant.org_id)" not in source


@pytest.mark.parametrize("request_name", ("register", "login"))
@pytest.mark.parametrize(
    "phone",
    (
        "1٢٣٤٥٦٧٨٩٠١",
        "1２３４５６７８９０１",
        "1२३४५६७८९०१",
        "12345٦٧٨٩٠١",
        "1234567890",
        "22345678901",
        " 12345678901",
        "12345678901 ",
        "12345678901\n",
    ),
)
def test_注册与登录手机号只接受ASCII数字完整匹配(request_name: str, phone: str) -> None:
    from pydantic import ValidationError

    from app.modules.auth.schemas import AuthLoginRequest, UserRegisterRequest

    request_type = (
        UserRegisterRequest if request_name == "register" else AuthLoginRequest
    )
    with pytest.raises(ValidationError):
        request_type(phone=phone, password="Synthetic-Password")


def test_注册与登录手机号合同共用ASCII模式且合法值通过() -> None:
    from fastapi import FastAPI

    from app.modules.auth.api import auth_router, router
    from app.modules.auth.schemas import AuthLoginRequest, UserRegisterRequest

    expected_pattern = r"^1[0-9]{10}$"
    assert (
        UserRegisterRequest.model_json_schema()["properties"]["phone"]["pattern"]
        == expected_pattern
    )
    assert (
        AuthLoginRequest.model_json_schema()["properties"]["phone"]["pattern"]
        == expected_pattern
    )
    assert UserRegisterRequest(
        phone="12345678901", password="Synthetic-Password"
    ).phone == "12345678901"
    assert AuthLoginRequest(
        phone="12345678901", password="Synthetic-Password"
    ).phone == "12345678901"

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(router)
    openapi_schemas = app.openapi()["components"]["schemas"]
    assert openapi_schemas["AuthLoginRequest"]["properties"]["phone"]["pattern"] == expected_pattern
    assert openapi_schemas["UserRegisterRequest"]["properties"]["phone"]["pattern"] == expected_pattern
