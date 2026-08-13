from __future__ import annotations

from app.core.model_specs import get_core_table_specs


def import_core_models():
    from app.modules.auth import models as auth_models
    from app.modules.system import models as system_models
    from app.modules.tenant import models as tenant_models
    from app.modules.user_health import models as user_health_models
    from app.modules.health_fact import models as health_fact_models
    from app.modules.health_fact_mapping import models as health_fact_mapping_models
    from app.modules.organization_mapping import models as organization_mapping_models
    from app.modules.organization_projection import models as organization_projection_models
    from app.modules.health_projection import models as health_projection_models

    return {
        "auth": auth_models,
        "tenant": tenant_models,
        "user_health": user_health_models,
        "system": system_models,
        "health_fact": health_fact_models,
        "health_fact_mapping": health_fact_mapping_models,
        "organization_mapping": organization_mapping_models,
        "organization_projection": organization_projection_models,
        "health_projection": health_projection_models,
        "specs": get_core_table_specs(),
    }


def get_core_model_classes():
    import_core_models()

    from app.modules.auth.models import User
    from app.modules.system.models import Message, OperationLog, PlatformOrg
    from app.modules.tenant.models import Tenant, TenantAttachment, TenantReviewLog
    from app.modules.user_health.models import DetectionReport, HealthIndicator, HealthProfile

    return {
        "platform_org": PlatformOrg,
        "tenant": Tenant,
        "tenant_attachment": TenantAttachment,
        "tenant_review_log": TenantReviewLog,
        "user": User,
        "health_profile": HealthProfile,
        "health_indicator": HealthIndicator,
        "detection_report": DetectionReport,
        "message": Message,
        "operation_log": OperationLog,
    }
