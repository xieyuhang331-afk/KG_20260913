from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
API_PATH = BACKEND_ROOT / "app" / "modules" / "member_enrollment" / "api.py"
MEMBER_REPOSITORY_PATH = (
    BACKEND_ROOT / "app" / "modules" / "member_enrollment" / "repository.py"
)
AUTH_REPOSITORY_PATH = BACKEND_ROOT / "app" / "modules" / "auth" / "repository.py"
MIGRATIONS_PATH = BACKEND_ROOT / "app" / "migrations" / "versions"

READ_ROUTES = {
    "identity_reviews",
    "identity_review",
}
WRITE_ROUTES = {
    "claim_review",
    "platform_decision",
    "create_document",
    "publish_document",
    "retire_document",
}
PII_ROUTES = {"pii_access"}
ALL_REVIEWER_ROUTES = READ_ROUTES | WRITE_ROUTES | PII_ROUTES

WRITE_FUNCTION = "slice3_platform_reviewer_write_currentness_v1"
CREDENTIAL_FUNCTION = "auth_user_credential_material_v1"
MIGRATION_SUFFIX = "_平台实名审核Reviewer当前性受限读取.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, name: str) -> str:
    source = _source(path)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None, "G2_REVIEWER_TEST_SOURCE_SEGMENT_MISSING"
            return segment
    pytest.fail("G2_REVIEWER_TEST_TARGET_FUNCTION_MISSING", pytrace=False)


def _assert_order(source: str, *markers: str) -> None:
    positions = tuple(source.find(marker) for marker in markers)
    assert all(position >= 0 for position in positions), "G2_REVIEWER_CALL_MISSING"
    assert positions == tuple(sorted(positions)), "G2_REVIEWER_CALL_ORDER_INVALID"


@pytest.fixture
def synthetic_write_trace() -> tuple[str, ...]:
    """Non-database sentinel proving the test's order helper is executable."""
    return (
        "idempotency_lock",
        "reviewer_currentness_lock",
        "business_target_lock",
        "mutation",
        "receipt_audit_outbox",
        "commit",
    )


def test_测试规格自身闭合且八路由互斥(
    synthetic_write_trace: tuple[str, ...],
) -> None:
    assert len(ALL_REVIEWER_ROUTES) == 8
    assert not (READ_ROUTES & WRITE_ROUTES)
    assert not (READ_ROUTES & PII_ROUTES)
    assert not (WRITE_ROUTES & PII_ROUTES)
    _assert_order(";".join(synthetic_write_trace), *synthetic_write_trace)


def test_RD1两个纯读路由只复用0040入口与平台角色校验() -> None:
    for name in sorted(READ_ROUTES):
        route = _function_source(API_PATH, name)
        assert "get_current_user_from_jwt" in route, "G2_REVIEWER_0040_ADMISSION_MISSING"
        assert "_require_platform(actor)" in route, "G2_REVIEWER_PLATFORM_GATE_MISSING"
        assert "_current_reviewer" not in route, "G2_REVIEWER_DUPLICATE_CURRENTNESS_PRESENT"
        assert "Depends(get_db_session)" not in route, "G2_REVIEWER_BASE_AUTHORITY_PRESENT"


def test_五个写路由在同一writer事务先复核Reviewer再执行Mutation() -> None:
    for name in sorted(WRITE_ROUTES):
        route = _function_source(API_PATH, name)
        assert "Depends(get_member_identity_review_writer_session)" in route
        assert "platform_reviewer_write_currentness" in route, (
            "G2_REVIEWER_WRITE_CURRENTNESS_MISSING"
        )
        _assert_order(
            route,
            "_begin_mutation",
            "platform_reviewer_write_currentness",
            "_service(session)",
        )
        assert "_current_reviewer" not in route, "G2_REVIEWER_OLD_HELPER_PRESENT"


def test_RD3_PII路由使用最小凭据材料且由0022二次复核() -> None:
    route = _function_source(API_PATH, "pii_access")
    assert "get_reviewer_credential_material" in route, (
        "G2_REVIEWER_CREDENTIAL_MATERIAL_MISSING"
    )
    assert "verify_password" in route, "G2_REVIEWER_PASSWORD_VERIFICATION_MISSING"
    assert "reviewer_credential_proof" in route, "G2_REVIEWER_PROOF_MISSING"
    assert "platform_reviewer_write_currentness" in route, (
        "G2_REVIEWER_PII_WRITER_CURRENTNESS_MISSING"
    )
    _assert_order(
        route,
        "get_reviewer_credential_material",
        "_begin_mutation",
        "platform_reviewer_write_currentness",
        "_service(session)",
    )
    assert 'writer_reviewer["id"] != reviewer.id' in route
    assert 'writer_reviewer["updated_at"] != reviewer.updated_at' in route
    assert "_current_reviewer" not in route, "G2_REVIEWER_OLD_HELPER_PRESENT"
    assert 'reviewer["password_hash"]' not in route, "G2_REVIEWER_RAW_MAPPING_HASH_PRESENT"


def test_仓储仅调用两个闭合函数且密码Hash不可repr() -> None:
    member_repository = _source(MEMBER_REPOSITORY_PATH)
    auth_repository = _source(AUTH_REPOSITORY_PATH)
    assert WRITE_FUNCTION in member_repository, "G2_REVIEWER_WRITE_REPOSITORY_MISSING"
    assert "platform_reviewer_write_currentness" in member_repository
    assert CREDENTIAL_FUNCTION in auth_repository, "G2_REVIEWER_CREDENTIAL_REPOSITORY_MISSING"
    assert "class ReviewerCredentialMaterial" in auth_repository
    credential_class = auth_repository.split("class ReviewerCredentialMaterial", 1)[1]
    credential_class = credential_class.split("\n\n", 1)[0]
    assert re.search(
        r"password_hash\s*:\s*str\s*=\s*field\(repr=False\)", credential_class
    ), "G2_REVIEWER_PASSWORD_HASH_REPR_UNSAFE"


def test_候选Migration只有两个强类型函数且不预占Revision() -> None:
    migrations = sorted(MIGRATIONS_PATH.glob(f"*{MIGRATION_SUFFIX}"))
    assert len(migrations) == 1, "G2_REVIEWER_MIGRATION_NOT_IMPLEMENTED"
    source = _source(migrations[0])
    assert source.count(f"CREATE FUNCTION public.{WRITE_FUNCTION}(") == 1
    assert source.count(f"CREATE FUNCTION public.{CREDENTIAL_FUNCTION}(") == 1
    assert source.count("CREATE FUNCTION public.") == 2
    assert re.search(
        rf"{WRITE_FUNCTION}\s*\(\s*p_reviewer_user_id BIGINT\s*\)", source
    )
    assert re.search(
        rf"{CREDENTIAL_FUNCTION}\s*\(\s*p_reviewer_user_id BIGINT\s*\)", source
    )
    lowered = source.lower()
    for forbidden in ("json", "jsonb", "array", "execute format("):
        assert forbidden not in lowered, "G2_REVIEWER_OPEN_PAYLOAD_FORBIDDEN"


def test_0048线性Revision与两个函数固定返回投影() -> None:
    migrations = sorted(MIGRATIONS_PATH.glob(f"*{MIGRATION_SUFFIX}"))
    assert len(migrations) == 1, "G2_REVIEWER_MIGRATION_NOT_IMPLEMENTED"
    source = _source(migrations[0])
    assert 'revision = "20260915_0048"' in source
    assert 'down_revision = "20260914_0047"' in source
    write_projection = (
        "id BIGINT", "role public.user_role", "status public.user_status",
        "tenant_id BIGINT", "exited_at TIMESTAMPTZ",
        "deletion_requested_at TIMESTAMPTZ", "updated_at TIMESTAMPTZ",
    )
    credential_projection = (
        "id BIGINT", "password_hash VARCHAR(255)", *write_projection[1:],
    )
    write_body = source.split(f"CREATE FUNCTION public.{WRITE_FUNCTION}", 1)[1]
    write_body = write_body.split("END $$", 1)[0]
    credential_body = source.split(
        f"CREATE FUNCTION public.{CREDENTIAL_FUNCTION}", 1
    )[1].split("END $$", 1)[0]
    assert all(field in write_body for field in write_projection)
    assert all(field in credential_body for field in credential_projection)
    assert "password_hash" not in write_body
    assert "version" not in write_body.lower()
    assert "version" not in credential_body.lower()


def test_两个函数执行角色闭合且Public与底表权限保持拒绝() -> None:
    migrations = sorted(MIGRATIONS_PATH.glob(f"*{MIGRATION_SUFFIX}"))
    assert len(migrations) == 1, "G2_REVIEWER_MIGRATION_NOT_IMPLEMENTED"
    source = _source(migrations[0])
    for signature in (WRITE_FUNCTION, CREDENTIAL_FUNCTION):
        assert "REVOKE ALL ON FUNCTION" in source
        assert signature in source
    assert "session_user" in source
    assert "KG_DATABASE_USER" in source
    assert "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE" in source
    assert "KG_IDENTITY_APPLICATION_DATABASE_URL" in source
    assert "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL" in source
    assert "SET search_path = pg_catalog" in source
    assert "GRANT SELECT ON TABLE public.\"user\"" not in source
    assert "GRANT UPDATE ON TABLE public.\"user\"" not in source
    assert "CREATE ROLE" not in source.upper()


def test_八路由保留401_403_503安全错误合同() -> None:
    api = _source(API_PATH)
    for path in (
        "/api/v1/platform/member-identity-reviews",
        "/api/v1/platform/member-identity-reviews/{review_id}",
        "/api/v1/platform/member-identity-reviews/{review_id}/claim",
        "/api/v1/platform/member-identity-reviews/{review_id}/pii-access",
        "/api/v1/platform/member-identity-reviews/{review_id}/decision",
        "/api/v1/platform/consent-documents",
        "/api/v1/platform/consent-documents/{document_version_id}/publish",
        "/api/v1/platform/consent-documents/{document_version_id}/retire",
    ):
        route_literal = path.removeprefix("/api/v1/platform")
        assert route_literal in api, "G2_REVIEWER_ROUTE_MISSING"
    assert '"AUTHENTICATION_REQUIRED","ACCESS_TOKEN_STALE"' in api
    assert '"REVIEWER_CURRENTNESS_FORBIDDEN":403' in api
    assert '"DEPENDENCY_UNAVAILABLE":503' in api


def test_旧ReviewerHelper与public_user底表依赖必须退役() -> None:
    api = _source(API_PATH)
    assert "async def _current_reviewer" not in api, "G2_REVIEWER_OLD_HELPER_PRESENT"
    assert (
        'SELECT id,role,status,tenant_id,password_hash,1::bigint AS version,'
        not in api
    ), "G2_REVIEWER_PUBLIC_USER_DIRECT_SELECT_PRESENT"


def test_平台角色继续只允许无tenant无org的super_admin() -> None:
    gate = _function_source(API_PATH, "_require_platform")
    assert 'actor.role != "super_admin"' in gate
    assert "actor.tenant_id is not None" in gate
    assert "actor.org_id is not None" in gate
    for forbidden in ("province_admin", "city_admin", "org_admin"):
        assert forbidden not in gate, "G2_REVIEWER_ROLE_SCOPE_EXPANDED"


def test_写路由不把RD1纯读窗口扩展到Mutation或PII() -> None:
    for name in sorted(WRITE_ROUTES):
        route = _function_source(API_PATH, name)
        assert "platform_reviewer_write_currentness" in route, (
            "G2_REVIEWER_WRITE_CURRENTNESS_MISSING"
        )
    pii_route = _function_source(API_PATH, "pii_access")
    assert "reviewer_credential_proof" in pii_route
    assert "platform_reviewer_write_currentness" in pii_route
    assert "get_reviewer_credential_material" in pii_route, (
        "G2_REVIEWER_CREDENTIAL_MATERIAL_MISSING"
    )


def test_凭据材料字段闭合且不得含手机号PII或TOTP资料() -> None:
    auth_repository = _source(AUTH_REPOSITORY_PATH)
    assert "class ReviewerCredentialMaterial" in auth_repository, (
        "G2_REVIEWER_CREDENTIAL_REPOSITORY_MISSING"
    )
    credential_class = auth_repository.split("class ReviewerCredentialMaterial", 1)[1]
    credential_class = credential_class.split("\n\n", 1)[0]
    expected = {
        "id", "password_hash", "role", "status", "tenant_id", "exited_at",
        "deletion_requested_at", "updated_at",
    }
    tree = ast.parse("class ReviewerCredentialMaterial" + credential_class)
    body = tree.body[0].body
    actual = {
        node.target.id
        for node in body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert actual == expected, "G2_REVIEWER_CREDENTIAL_PROJECTION_OPEN"
    for forbidden in (
        "phone", "real_name", "id_card", "totp", "ciphertext", "nonce", "key_id",
    ):
        assert forbidden not in credential_class.lower(), (
            "G2_REVIEWER_CREDENTIAL_PROJECTION_SENSITIVE"
        )
