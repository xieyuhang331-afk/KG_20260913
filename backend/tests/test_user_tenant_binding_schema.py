import unittest
from datetime import datetime, timezone

from pydantic import ValidationError


class UserTenantBindingSchemaTests(unittest.TestCase):
    def test_tenant_binding_request_accepts_positive_tenant_id(self):
        from app.modules.auth.schemas import TenantBindingRequest

        payload = TenantBindingRequest(tenant_id=1)

        self.assertEqual(payload.tenant_id, 1)

    def test_tenant_binding_request_rejects_invalid_tenant_id(self):
        from app.modules.auth.schemas import TenantBindingRequest

        with self.assertRaises(ValidationError):
            TenantBindingRequest(tenant_id=0)

    def test_tenant_binding_request_requires_tenant_id(self):
        from app.modules.auth.schemas import TenantBindingRequest

        with self.assertRaises(ValidationError):
            TenantBindingRequest()

    def test_tenant_binding_response_contains_binding_summary(self):
        from app.modules.auth.schemas import TenantBindingResponse

        bound_at = datetime(2026, 7, 29, tzinfo=timezone.utc)

        response = TenantBindingResponse(
            user_id=1001,
            tenant_id=501,
            tenant_code="TACTIVE001",
            tenant_name="Kanglin West Lake Store",
            bound_at=bound_at,
        )

        self.assertEqual(response.user_id, 1001)
        self.assertEqual(response.tenant_id, 501)
        self.assertEqual(response.tenant_code, "TACTIVE001")
        self.assertEqual(response.tenant_name, "Kanglin West Lake Store")
        self.assertEqual(response.bound_at, bound_at)
