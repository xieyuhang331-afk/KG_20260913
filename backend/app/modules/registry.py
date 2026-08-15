from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleDefinition:
    slug: str
    name: str
    route_prefixes: tuple[str, ...]


MODULES: tuple[ModuleDefinition, ...] = (
    ModuleDefinition("auth", "认证与权限", ("/api/v1/auth",)),
    ModuleDefinition("tenant", "组织与门店", ("/api/v1/tenants", "/api/v1/org")),
    ModuleDefinition("institution_onboarding", "机构受控入驻", ("/api/v1/platform/institution-invitations", "/api/v1/platform/institution-reviews", "/api/v1/institution-onboarding")),
    ModuleDefinition("private_file", "私有文件", ("/api/v1/private-files",)),
    ModuleDefinition("user_health", "用户与健康档案", ("/api/v1/users", "/api/v1/health", "/api/v1/reports")),
    ModuleDefinition("template", "方案模板", ("/api/v1/templates",)),
    ModuleDefinition("plan", "方案管理", ("/api/v1/plans",)),
    ModuleDefinition("ai", "AI 引擎", ("/api/v1/ai",)),
    ModuleDefinition("product", "产品服务库", ("/api/v1/products", "/api/v1/services", "/api/v1/sales")),
    ModuleDefinition("order", "订单支付与结算", ("/api/v1/orders", "/api/v1/settlements")),
    ModuleDefinition("review", "审核队列", ("/api/v1/reviews",)),
    ModuleDefinition(
        "service_exec",
        "服务执行与 AI 核验",
        ("/api/v1/dispatch", "/api/v1/service-relationships", "/api/v1/check-in", "/api/v1/compliance"),
    ),
    ModuleDefinition("member", "会员体系", ("/api/v1/memberships",)),
    ModuleDefinition("system", "通知与系统配置", ("/api/v1/notifications", "/api/v1/config", "/ws")),
)


def get_module_registry() -> list[ModuleDefinition]:
    return list(MODULES)

