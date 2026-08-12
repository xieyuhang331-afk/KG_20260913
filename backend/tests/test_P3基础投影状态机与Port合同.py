import inspect

from app.modules.health_projection import ports as health_ports
from app.modules.organization_projection import ports as organization_ports


def test_Module_A只暴露纯计算Port且无状态机运行入口():
    assert inspect.isclass(organization_ports.OrganizationProjectionCorePort)
    assert inspect.isclass(health_ports.HealthProjectionCorePort)
    forbidden = {
        "Builder", "Repository", "UnitOfWork", "Session", "Engine", "Shadow",
        "READY", "ACTIVE", "resume", "heartbeat", "lease", "outcome",
    }
    symbols = set(dir(organization_ports)) | set(dir(health_ports))
    assert not any(any(token.lower() in symbol.lower() for token in forbidden) for symbol in symbols)
