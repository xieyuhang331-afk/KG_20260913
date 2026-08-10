import os

os.environ.setdefault("KG_JWT_SECRET_KEY", "test-only-jwt-secret-not-for-production")
os.environ.setdefault("KG_STEP_UP_JWT_SECRET_KEY", "test-only-step-up-secret-not-for-production")
os.environ.setdefault("KG_DATABASE_PASSWORD", "test-only-database-password")


def test_P3机构最小API闭环尚未实现():
    from app.main import create_app

    schema = create_app().openapi()
    paths = schema["paths"]
    required = {
        "/api/v1/platform/organizations",
        "/api/v1/platform/organizations/tree",
        "/api/v1/platform/organizations/{organization_id}",
        "/api/v1/platform/organizations/{organization_id}/tenants",
        "/api/v1/platform/organizations/{organization_id}/admin-candidates",
        "/api/v1/platform/organizations/{parent_id}/children/order",
        "/api/v1/platform/organizations/{organization_id}/activate",
        "/api/v1/platform/organizations/{organization_id}/deactivate",
        "/api/v1/organizations/me",
    }
    if not required <= set(paths):
        raise AssertionError("P3 Organization minimum API loop is not implemented")

    create_schema = schema["components"]["schemas"]["OrganizationCreateRequest"]
    assert "parent_expected_version" in create_schema["required"]
    assert create_schema["properties"]["parent_expected_version"]["minimum"] == 1


def test_组织错误响应固定detail_code():
    from app.modules.organization.api import organization_error_response
    from app.modules.organization.domain import OrganizationVersionConflict

    response = organization_error_response(OrganizationVersionConflict())
    assert response.status_code == 409
    assert response.body == b'{"detail":"ORGANIZATION_VERSION_CONFLICT"}'
