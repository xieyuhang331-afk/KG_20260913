from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.database import get_db_session, get_session_factory
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.core.接口合同 import error_response
from app.modules.organization.domain import OrganizationError
from app.modules.organization.schemas import (
    MyOrganizationResponse,
    OrganizationAdminCandidatePage,
    OrganizationAdminMutationResponse,
    OrganizationChildrenOrderRequest,
    OrganizationChildrenOrderResponse,
    OrganizationCreateRequest,
    OrganizationDetailResponse,
    OrganizationPatchRequest,
    OrganizationStateRequest,
    OrganizationStateResponse,
    OrganizationTenantPage,
    OrganizationTreeResponse,
)
from app.modules.organization.service import (
    change_status,
    create_organization,
    get_detail,
    get_my_organization,
    list_admin_candidates,
    list_tenants,
    list_tree,
    patch_organization,
    reorder_children,
)


def organization_error_response(request, error: OrganizationError):
    return error_response(request, error.status_code, error.code)


class OrganizationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except OrganizationError as exc:
                return organization_error_response(request, exc)
            except RequestValidationError:
                return error_response(request, 422, "ORGANIZATION_REQUEST_INVALID")
            except HTTPException as exc:
                if exc.status_code == 401:
                    return error_response(request, 401, "AUTHENTICATION_REQUIRED", headers={"WWW-Authenticate": "Bearer"})
                if exc.status_code == 503:
                    return error_response(request, 503, "DEPENDENCY_UNAVAILABLE", retryable=True)
                if exc.status_code == 403:
                    return error_response(request, 403, "ORGANIZATION_SCOPE_FORBIDDEN")
                raise
            except Exception:
                return error_response(request, 500, "INTERNAL_ERROR")

        return handler


_AUTHENTICATION_RESPONSES = {
    status: {
        "description": "Request rejected",
        "headers": {
            "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
            "Cache-Control": {"schema": {"type": "string", "enum": ["no-store, private"]}},
            "Pragma": {"schema": {"type": "string", "const": "no-cache"}},
            **({"WWW-Authenticate": {"schema": {"type": "string", "enum": ["Bearer"]}}} if status == 401 else {}),
        },
        "content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/ErrorResponseDTO"},
            "examples": {
                "authentication" if status in {401, 503} else "rejected": {"value": {
                    "code": {
                        400: "ORGANIZATION_REQUEST_INVALID",
                        401: "AUTHENTICATION_REQUIRED",
                        403: "ORGANIZATION_SCOPE_FORBIDDEN",
                        404: "ORGANIZATION_NOT_FOUND",
                        409: "ORGANIZATION_STATE_CONFLICT",
                        422: "ORGANIZATION_REQUEST_INVALID",
                        500: "INTERNAL_ERROR",
                        503: "DEPENDENCY_UNAVAILABLE",
                    }[status],
                    "message": "request rejected", "request_id": "01990000-0000-7000-8000-000000000201",
                    "retryable": status == 503, "field_errors": [],
                }}
            },
        }},
    }
    for status in (400, 401, 403, 404, 409, 422, 500, 503)
}

router = APIRouter(tags=["organization"], route_class=OrganizationRoute, responses=_AUTHENTICATION_RESPONSES)


@router.get("/api/v1/platform/organizations/tree", response_model=OrganizationTreeResponse)
async def organization_tree(
    include_archived: bool = False,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await list_tree(session, current_user=current_user, include_archived=include_archived)


@router.get(
    "/api/v1/platform/organizations/{organization_id}",
    response_model=OrganizationDetailResponse,
)
async def organization_detail(
    organization_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await get_detail(session, current_user=current_user, organization_id=organization_id)


@router.get(
    "/api/v1/platform/organizations/{organization_id}/tenants",
    response_model=OrganizationTenantPage,
)
async def organization_tenants(
    organization_id: int,
    include_descendants: bool = True,
    cursor: str | None = Query(default=None, max_length=1024),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await list_tenants(
        session, current_user=current_user, organization_id=organization_id,
        include_descendants=include_descendants, cursor=cursor, page_size=page_size,
    )


@router.get(
    "/api/v1/platform/organizations/{organization_id}/admin-candidates",
    response_model=OrganizationAdminCandidatePage,
)
async def organization_admin_candidates(
    organization_id: int,
    cursor: str | None = Query(default=None, max_length=1024),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await list_admin_candidates(
        session, current_user=current_user, organization_id=organization_id,
        cursor=cursor, page_size=page_size,
    )


@router.post(
    "/api/v1/platform/organizations",
    response_model=OrganizationAdminMutationResponse,
    status_code=201,
)
async def organization_create(
    payload: OrganizationCreateRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await create_organization(
        session, current_user=current_user, payload=payload,
        confirmation_factory_provider=get_session_factory,
    )


@router.patch(
    "/api/v1/platform/organizations/{organization_id}",
    response_model=OrganizationAdminMutationResponse,
)
async def organization_patch(
    organization_id: int,
    payload: OrganizationPatchRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await patch_organization(
        session, current_user=current_user, organization_id=organization_id,
        payload=payload, confirmation_factory_provider=get_session_factory,
    )


@router.put(
    "/api/v1/platform/organizations/{parent_id}/children/order",
    response_model=OrganizationChildrenOrderResponse,
)
async def organization_children_order(
    parent_id: int,
    payload: OrganizationChildrenOrderRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await reorder_children(
        session, current_user=current_user, parent_id=parent_id, payload=payload,
        confirmation_factory_provider=get_session_factory,
    )


@router.post(
    "/api/v1/platform/organizations/{organization_id}/activate",
    response_model=OrganizationStateResponse,
)
async def organization_activate(
    organization_id: int,
    payload: OrganizationStateRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await change_status(
        session, current_user=current_user, organization_id=organization_id,
        payload=payload, target_status="active", confirmation_factory_provider=get_session_factory,
    )


@router.post(
    "/api/v1/platform/organizations/{organization_id}/deactivate",
    response_model=OrganizationStateResponse,
)
async def organization_deactivate(
    organization_id: int,
    payload: OrganizationStateRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await change_status(
        session, current_user=current_user, organization_id=organization_id,
        payload=payload, target_status="inactive", confirmation_factory_provider=get_session_factory,
    )


@router.get("/api/v1/organizations/me", response_model=MyOrganizationResponse)
async def my_organization(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
):
    return await get_my_organization(session, current_user=current_user)
