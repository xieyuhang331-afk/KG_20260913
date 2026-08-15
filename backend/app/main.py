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
from app.modules.organization.api import router as organization_router
from app.modules.institution_onboarding.api import onboarding_router, platform_router
from app.modules.private_file.api import router as private_file_router
from app.modules.registry import get_module_registry
from app.modules.review.api import router as review_router
from app.modules.tenant.api import router as tenant_router
from app.modules.user_health.api import router as user_health_router


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
