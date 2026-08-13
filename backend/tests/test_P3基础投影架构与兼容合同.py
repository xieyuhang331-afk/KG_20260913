from pathlib import Path


ROOT = Path(__file__).parents[1]
MODULES = ("organization_projection", "health_projection")


def test_Module_A与Module_B仅含冻结白名单文件():
    assert {
        path.relative_to(ROOT).as_posix()
        for module in MODULES
        for path in (ROOT / "app/modules" / module).glob("*.py")
    } == {
        "app/modules/organization_projection/__init__.py",
        "app/modules/organization_projection/domain.py",
        "app/modules/organization_projection/ports.py",
        "app/modules/organization_projection/models.py",
        "app/modules/organization_projection/repository.py",
        "app/modules/organization_projection/service.py",
        "app/modules/health_projection/__init__.py",
        "app/modules/health_projection/domain.py",
        "app/modules/health_projection/ports.py",
        "app/modules/health_projection/models.py",
        "app/modules/health_projection/repository.py",
        "app/modules/health_projection/service.py",
    }


def test_Module_A领域源码仍无数据库运行和门禁入口():
    forbidden = (
        "sqlalchemy", "database_url", "session", "engine", "repository", "unitofwork",
        "builder", "shadow", "projectionstatus", "mark_ready", "mark_active", "resume",
        "heartbeat", "lease", "outcome",
        "APIRouter", "alembic",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for module in MODULES
        for path in (ROOT / "app/modules" / module).glob("domain.py")
    ).lower()
    assert not any(token.lower() in source for token in forbidden)


def test_Module_B源码无Module_C与公开入口():
    forbidden = (
        "shadow", '"ready"', '"active"', "read_cutover", "cutover", "APIRouter",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for module in MODULES
        for path in (ROOT / "app/modules" / module).glob("*.py")
        if path.name != "domain.py"
    ).lower()
    assert not any(token.lower() in source for token in forbidden)


def test_Metadata与Migration_Head仅传播Module_B():
    models = (ROOT / "app/modules/models.py").read_text(encoding="utf-8")
    env = (ROOT / "app/migrations/env.py").read_text(encoding="utf-8")
    assert "organization_projection" in models + env
    assert "health_projection" in models + env
    assert (ROOT / "app/migrations/versions/20260813_0017_p3_basic_projection_builder_foundation.py").exists()


def test_P2健康读取路径没有依赖Module_A():
    for relative in (
        "app/modules/user_health/api.py",
        "app/modules/user_health/repository.py",
        "app/modules/health_analysis/api.py",
        "app/modules/health_analysis/repository.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "app.modules.organization_projection" not in source
        assert "app.modules.health_projection" not in source
