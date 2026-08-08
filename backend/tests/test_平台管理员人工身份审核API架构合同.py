import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "app/modules/review/api.py"
SCHEMAS = ROOT / "app/modules/review/schemas.py"
APPLICATION = ROOT / "app/modules/auth/manual_identity_review_application.py"


def test_人工身份审核API保持薄路由且不直接持久化():
    source = API.read_text(encoding="utf-8").lower()
    for forbidden in (
        "select(user)",
        "session.execute",
        "session.add",
        "session.commit",
        "registrationverifiedoutboxormmodel",
        "p1verificationtransitionwriter",
        "create_async_engine",
        "create_engine",
        "create_all",
        "id_card",
        "real_name",
        "provider_callback",
        "register_user",
    ):
        assert forbidden not in source


def test_人工身份审核API只复用现有JWT且不修改签发语义():
    tree = ast.parse(API.read_text(encoding="utf-8"))
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.core.security"
        for alias in node.names
    }
    assert imported_names == {"CurrentUser", "get_current_user_from_jwt"}
    source = API.read_text(encoding="utf-8")
    assert "create_access_token" not in source
    assert "decode_access_token" not in source


def test_无权JWT在SessionFactory构造前由共享Guard拒绝():
    source = API.read_text(encoding="utf-8")
    assert "def get_platform_identity_reviewer(" in source
    assert (
        "current_user: CurrentUser = Depends(get_platform_identity_reviewer)"
        in source
    )
    service_start = source.index(
        "def get_platform_admin_manual_identity_review_service("
    )
    service_end = source.index("\n\n", service_start)
    service_signature = source[service_start:service_end]
    assert "Depends(get_platform_identity_reviewer)" in service_signature


def test_人工身份审核请求响应Schema排除PII并禁止额外字段():
    tree = ast.parse(SCHEMAS.read_text(encoding="utf-8"))
    classes = {
        node.name: ast.unparse(node).lower()
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name.startswith("PlatformAdminManualIdentityReview")
    }
    source = "\n".join(classes.values())
    assert "configdict(extra='forbid')" in source
    for forbidden in (
        "id_card",
        "real_name",
        "phone",
        "password",
        "jwt",
        "database_url",
    ):
        assert forbidden not in source


def test_人工身份审核应用边界不依赖FastAPI数据库或任务运行时():
    tree = ast.parse(APPLICATION.read_text(encoding="utf-8"))
    imports = {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert {
        "fastapi",
        "sqlalchemy",
        "alembic",
        "asyncpg",
        "celery",
    }.isdisjoint(imports)
