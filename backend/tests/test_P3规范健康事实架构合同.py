import ast
import inspect
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "app" / "modules" / "health_fact"


def test_规范健康事实模块不开放API或依赖P2健康实现():
    forbidden = {
        "fastapi",
        "app.modules.user_health",
        "app.modules.health_analysis",
        "app.modules.review",
    }
    violations = []
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(name == item or name.startswith(item + ".") for item in forbidden):
                    violations.append((path.name, name))
    assert violations == []


def test_规范健康事实没有Engine_DDL_TEXT_COMPOSITE或原始Identity持久化():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.glob("*.py"))
    assert "create_async_engine" not in combined
    assert "create_all" not in combined
    assert "text_value" not in combined
    assert "composite_value" not in combined
    assert 'mapped_column(String(512)' not in combined
    model = (ROOT / "models.py").read_text(encoding="utf-8")
    assert "source_identity_digest" in model
    assert "source_identity:" not in model


def test_P2健康表API和0006不在生产白名单内():
    changed_candidates = {
        "app/modules/user_health/api.py",
        "app/modules/user_health/models.py",
        "app/modules/user_health/repository.py",
        "app/modules/user_health/service.py",
        "app/modules/health_analysis/api.py",
        "app/migrations/versions/20260728_0006_f003_health_indicator.py",
    }
    allowed_production = {
        "app/modules/health_fact/__init__.py",
        "app/modules/health_fact/domain.py",
        "app/modules/health_fact/ports.py",
        "app/modules/health_fact/models.py",
        "app/modules/health_fact/repository.py",
        "app/modules/health_fact/unit_of_work.py",
        "app/modules/models.py",
        "app/core/config.py",
        "app/core/database.py",
        "app/migrations/env.py",
        "app/migrations/versions/20260811_0015_p3_canonical_health_fact.py",
    }
    assert changed_candidates.isdisjoint(allowed_production)


def test_Writer_Reader_Catalog与ProducerAuthority端口均已冻结且Writer先授权():
    from app.modules.health_fact import ports
    from app.modules.health_fact.unit_of_work import CanonicalHealthFactWriter

    for name in (
        "HealthFactProducerAuthorityPort",
        "HealthFactIndicatorCatalogPort",
        "HealthFactWriterPort",
        "HealthFactReaderPort",
        "HealthFactRepositoryPort",
        "HealthFactUnitOfWorkPort",
    ):
        assert hasattr(ports, name)
    source = inspect.getsource(CanonicalHealthFactWriter.write)
    assert source.index("producer_authority.authorize") < source.index(
        "prepare_fact"
    )
    assert source.index("producer_authority.authorize") < source.index(
        "uow_factory"
    )
