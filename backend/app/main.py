from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.database import dispose_database_runtimes
from app.core.middleware import add_request_middleware
from app.core.responses import ok_response
from app.modules.auth.api import auth_router
from app.modules.auth.api import router as user_auth_router
from app.modules.health_analysis.api import internal_router as health_analysis_internal_router
from app.modules.health_analysis.api import router as health_analysis_router
from app.modules.health_assessment.api import (
    routers as slice5_routers,
    strip_slice5_validation_responses,
)
from app.modules.health_plan.api import (
    routers as slice6_routers,
    strip_slice6_validation_responses,
)
from app.modules.service_fulfillment.api import (
    consume_personal_data_export_download,
    routers as slice7_routers,
    strip_slice7_validation_responses,
)
from app.modules.organization.api import router as organization_router
from app.modules.institution_onboarding.api import onboarding_router, platform_router
from app.modules.member_enrollment.api import (
    routers as member_enrollment_routers,
    strip_member_enrollment_validation_responses,
)
from app.modules.private_file.api import router as private_file_router
from app.modules.private_file.service import authorize_generated_export_access
from app.modules.private_file.storage import build_private_object_store
from app.modules.therapist_qualification.api import (
    institution_router as therapist_institution_router,
    platform_router as therapist_platform_router,
    strip_therapist_validation_responses,
    therapist_router,
)
from app.modules.registry import get_module_registry
from app.modules.review.api import router as review_router
from app.modules.tenant.api import router as tenant_router
from app.modules.user_health.api import (
    formal_routers as slice4_formal_routers,
    router as user_health_router,
    strip_slice4_validation_responses,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await dispose_database_runtimes()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.service_name,
        version=settings.version,
        lifespan=lifespan,
        openapi_tags=[
            {"name": module.slug, "description": module.name}
            for module in get_module_registry()
        ],
    )
    app.state.kg_modules = get_module_registry()
    app.state.private_object_store = build_private_object_store(
        backend=settings.file_storage_backend,
        root=settings.private_file_storage_root,
    )
    app.state.slice7_export_access_authorizer = authorize_generated_export_access
    app.state.slice7_export_download_consumer = consume_personal_data_export_download
    add_request_middleware(app)
    app.include_router(auth_router)
    app.include_router(user_auth_router)
    app.include_router(tenant_router)
    app.include_router(review_router)
    app.include_router(user_health_router)
    app.include_router(health_analysis_router)
    app.include_router(health_analysis_internal_router)
    app.include_router(organization_router)
    app.include_router(platform_router)
    app.include_router(onboarding_router)
    app.include_router(private_file_router)
    app.include_router(therapist_institution_router)
    app.include_router(therapist_router)
    app.include_router(therapist_platform_router)
    for member_enrollment_router in member_enrollment_routers:
        app.include_router(member_enrollment_router)
    for slice4_router in slice4_formal_routers:
        app.include_router(slice4_router)
    for slice5_router in slice5_routers:
        app.include_router(slice5_router)
    for slice6_router in slice6_routers:
        app.include_router(slice6_router)
    for slice7_router in slice7_routers:
        app.include_router(slice7_router)
    default_openapi = app.openapi

    def therapist_aware_openapi():
        return strip_slice7_validation_responses(
            strip_slice6_validation_responses(
                strip_slice5_validation_responses(
                    strip_slice4_validation_responses(
                        strip_member_enrollment_validation_responses(
                            strip_therapist_validation_responses(default_openapi())
                        )
                    )
                )
            )
        )

    app.openapi = therapist_aware_openapi

    @app.get("/health", tags=["system"])
    async def health_check() -> dict:
        return ok_response(
            {
                "service": settings.service_name,
                "status": "ok",
                "version": settings.version,
            }
        )

    return app


app = create_app()
