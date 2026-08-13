import inspect

from app.modules.health_projection import ports as health_ports
from app.modules.organization_projection import ports as organization_ports


def test_Module_A与Module_B只暴露冻结Port且无Module_C入口():
    assert inspect.isclass(organization_ports.OrganizationProjectionCorePort)
    assert inspect.isclass(health_ports.HealthProjectionCorePort)
    assert inspect.isclass(organization_ports.OrganizationProjectionBuilderRepositoryPort)
    assert inspect.isclass(health_ports.HealthProjectionBuilderRepositoryPort)
    forbidden = {
        "Shadow", "READY", "ACTIVE", "Cutover", "PublicApi",
    }
    symbols = set(dir(organization_ports)) | set(dir(health_ports))
    assert not any(any(token.lower() in symbol.lower() for token in forbidden) for symbol in symbols)
