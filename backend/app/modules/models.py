from __future__ import annotations

from app.core.model_specs import get_core_table_specs


def import_core_models():
    from app.modules.auth import models as auth_models
    from app.modules.system import models as system_models
    from app.modules.tenant import models as tenant_models
    from app.modules.user_health import models as user_health_models

    return {
        "auth": auth_models,
        "tenant": tenant_models,
        "user_health": user_health_models,
        "system": system_models,
        "specs": get_core_table_specs(),
    }


def get_core_model_classes():
    import_core_models()

    from app.modules.auth.models import User
    from app.modules.system.models import Message, OperationLog, PlatformOrg
    from app.modules.tenant.models import Tenant, TenantAttachment, TenantReviewLog
    from app.modules.user_health.models import HealthIndicator, HealthProfile

    return {
        "platform_org": PlatformOrg,
        "tenant": Tenant,
        "tenant_attachment": TenantAttachment,
        "tenant_review_log": TenantReviewLog,
        "user": User,
        "health_profile": HealthProfile,
        "health_indicator": HealthIndicator,
        "message": Message,
        "operation_log": OperationLog,
    }
