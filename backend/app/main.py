from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.core.database import dispose_database_runtimes, get_session_factory
from app.core.middleware import add_request_middleware
from app.core.readiness import HealthResponseDTO, ReadinessService, health_response
from app.core.responses import ok_response
from app.core.接口合同 import (
    ErrorResponseDTO,
    error_response,
    express_security_contract,
    install_error_contract,
)
from app.core.认证配置校验 import validated_auth_settings
from app.core.认证限流 import AuthRateLimiter
from app.modules.auth.api import auth_router
from app.modules.auth.api import router as user_auth_router
from app.modules.health_analysis.api import (
    internal_router as health_analysis_internal_router,
)
from app.modules.health_analysis.api import router as health_analysis_router
from app.modules.health_assessment.api import (
    routers as slice5_routers,
)
from app.modules.health_assessment.api import (
    strip_slice5_validation_responses,
)
from app.modules.health_plan.api import (
    routers as slice6_routers,
)
from app.modules.health_plan.api import (
    strip_slice6_validation_responses,
)
from app.modules.institution_onboarding.api import onboarding_router, platform_router
from app.modules.member_enrollment.api import (
    routers as member_enrollment_routers,
)
from app.modules.member_enrollment.api import (
    strip_member_enrollment_validation_responses,
)
from app.modules.organization.api import router as organization_router
from app.modules.private_file.api import router as private_file_router
from app.modules.private_file.service import authorize_generated_export_access
from app.modules.private_file.storage import build_private_object_store
from app.modules.registry import get_module_registry
from app.modules.review.api import router as review_router
from app.modules.service_fulfillment.api import (
    consume_personal_data_export_download,
    strip_slice7_validation_responses,
)
from app.modules.service_fulfillment.api import (
    routers as slice7_routers,
)
from app.modules.tenant.api import router as tenant_router
from app.modules.therapist_qualification.api import (
    institution_router as therapist_institution_router,
)
from app.modules.therapist_qualification.api import (
    platform_router as therapist_platform_router,
)
from app.modules.therapist_qualification.api import (
    strip_therapist_validation_responses,
    therapist_router,
)
from app.modules.user_health.api import (
    formal_routers as slice4_formal_routers,
)
from app.modules.user_health.api import (
    router as user_health_router,
)
from app.modules.user_health.api import (
    strip_slice4_validation_responses,
)

_HEALTH_RESPONSE_HEADERS = {
    "Cache-Control": {
        "schema": {"type": "string", "const": "no-store, private"}
    },
    "Pragma": {"schema": {"type": "string", "const": "no-cache"}},
    "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        try:
            await app.state.readiness_service.close()
        finally:
            await dispose_database_runtimes()


def create_app() -> FastAPI:
    settings = validated_auth_settings()
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
    app.state.auth_rate_limiter = AuthRateLimiter(settings.auth_rate_limit_hmac_key.encode("utf-8"))
    app.state.private_object_store = build_private_object_store(
        backend=settings.file_storage_backend,
        root=settings.private_file_storage_root,
    )
    app.state.readiness_service = ReadinessService(
        session_factory=lambda: get_session_factory()(),
        expected_database_role=settings.database_user,
        object_store=app.state.private_object_store,
    )
    app.state.slice7_export_access_authorizer = authorize_generated_export_access
    app.state.slice7_export_download_consumer = consume_personal_data_export_download
    add_request_middleware(app)
    install_error_contract(app)
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
        schema = strip_slice7_validation_responses(
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
        return express_security_contract(app, schema)

    app.openapi = therapist_aware_openapi

    @app.get("/health", tags=["system"], deprecated=True)
    async def health_check(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        return ok_response(
            {
                "service": settings.service_name,
                "status": "ok",
                "version": settings.version,
            }
        )

    @app.get(
        "/health/live",
        tags=["system"],
        response_model=HealthResponseDTO,
        responses={200: {"headers": _HEALTH_RESPONSE_HEADERS}},
    )
    async def health_live(response: Response) -> HealthResponseDTO:
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        return health_response("LIVE")

    @app.get(
        "/health/ready",
        tags=["system"],
        response_model=HealthResponseDTO,
        responses={
            200: {"headers": _HEALTH_RESPONSE_HEADERS},
            503: {"model": ErrorResponseDTO, "description": "Dependency unavailable"},
        },
    )
    async def health_ready(request: Request, response: Response):
        if not await request.app.state.readiness_service.ready():
            return error_response(
                request, 503, "DEPENDENCY_UNAVAILABLE", retryable=True
            )
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Pragma"] = "no-cache"
        return health_response("READY")

    return app


app = create_app()
