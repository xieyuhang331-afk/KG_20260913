import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TenantReviewDecisionApiTests(unittest.TestCase):
    def _headers(
        self,
        role: str = "super_admin",
        *,
        province: str | None = None,
        city: str | None = None,
    ) -> dict:
        from app.core.security import create_access_token

        claims = {"sub": "100", "role": role}
        if province is not None:
            claims["province"] = province
        if city is not None:
            claims["city"] = city
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _client(self, session):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        async def fake_session():
            yield session

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app)

    def _tenant(self, *, status: str = "pending", province: str = "GD", city: str = "GZ"):
        return SimpleNamespace(
            id=501,
            name="Kanglin Guangzhou Store",
            status=status,
            province=province,
            city=city,
            reviewed_by=None,
            reviewed_at=None,
            approved_at=None,
            reject_reason=None,
            grade="standard",
        )

    def _patch_writes(self, tenant, *, operation_side_effect=None):
        operation_mock = AsyncMock(side_effect=operation_side_effect) if operation_side_effect else AsyncMock()
        return (
            patch("app.modules.review.service.get_tenant_for_review_update", new=AsyncMock(return_value=tenant)),
            patch("app.modules.review.service.apply_tenant_review_decision", new=AsyncMock(side_effect=lambda session, tenant, **kwargs: self._apply_decision(tenant, **kwargs))),
            patch("app.modules.review.service.create_tenant_review_log", new=AsyncMock()),
            patch("app.modules.review.service.create_operation_log", new=operation_mock),
            operation_mock,
        )

    def _patch_notification_builder(self, calls: list[str]):
        def build_intent(*, tenant, action):
            calls.append(f"notification:{action}")
            return SimpleNamespace(event=f"tenant_onboarding_{action}")

        return patch("app.modules.review.service.build_tenant_review_notification_intent", side_effect=build_intent, create=True)

    def _apply_decision(self, tenant, **kwargs):
        for key, value in kwargs.items():
            setattr(tenant, key, value)
        return tenant

    def test_router_registers_review_decision_routes(self):
        session = SimpleNamespace()
        response = self._client(session).get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/api/v1/reviews/tenants/{tenant_id}/approve", response.json()["paths"])
        self.assertIn("/api/v1/reviews/tenants/{tenant_id}/reject", response.json()["paths"])
        self.assertIn("post", response.json()["paths"]["/api/v1/reviews/tenants/{tenant_id}/approve"])
        self.assertIn("post", response.json()["paths"]["/api/v1/reviews/tenants/{tenant_id}/reject"])

    def test_approve_pending_tenant_sets_active_and_writes_logs(self):
        calls: list[str] = []
        session = SimpleNamespace(commit=AsyncMock(side_effect=lambda: calls.append("commit")), rollback=AsyncMock())
        tenant = self._tenant()
        get_patch, apply_patch, review_log_patch, operation_log_patch, _ = self._patch_writes(tenant)

        with get_patch as get_mock, apply_patch as apply_mock, review_log_patch as review_log_mock, operation_log_patch as operation_log_mock, self._patch_notification_builder(calls) as notification_mock:
            response = self._client(session).post(
                "/api/v1/reviews/tenants/501/approve",
                json={"comment": "approved", "grade": "flagship"},
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "active")
        get_mock.assert_awaited_once()
        self.assertEqual(apply_mock.await_args.kwargs["status"], "active")
        self.assertEqual(apply_mock.await_args.kwargs["reviewed_by"], 100)
        self.assertIsNone(apply_mock.await_args.kwargs["reject_reason"])
        self.assertEqual(apply_mock.await_args.kwargs["grade"], "flagship")
        self.assertEqual(review_log_mock.await_args.kwargs["action"], "approved")
        self.assertEqual(operation_log_mock.await_args.kwargs["action"], "tenant_onboarding_approved")
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
        notification_mock.assert_called_once()
        self.assertEqual(notification_mock.call_args.kwargs["action"], "approved")
        self.assertEqual(calls, ["commit", "notification:approved"])

    def test_reject_pending_tenant_sets_rejected_and_writes_logs(self):
        calls: list[str] = []
        session = SimpleNamespace(commit=AsyncMock(side_effect=lambda: calls.append("commit")), rollback=AsyncMock())
        tenant = self._tenant()
        get_patch, apply_patch, review_log_patch, operation_log_patch, _ = self._patch_writes(tenant)

        with get_patch, apply_patch as apply_mock, review_log_patch as review_log_mock, operation_log_patch as operation_log_mock, self._patch_notification_builder(calls) as notification_mock:
            response = self._client(session).post(
                "/api/v1/reviews/tenants/501/reject",
                json={"reason": "license image is unclear"},
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "rejected")
        self.assertEqual(apply_mock.await_args.kwargs["status"], "rejected")
        self.assertEqual(apply_mock.await_args.kwargs["reject_reason"], "license image is unclear")
        self.assertIsNone(apply_mock.await_args.kwargs["approved_at"])
        self.assertEqual(review_log_mock.await_args.kwargs["action"], "rejected")
        self.assertEqual(operation_log_mock.await_args.kwargs["action"], "tenant_onboarding_rejected")
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
        notification_mock.assert_called_once()
        self.assertEqual(notification_mock.call_args.kwargs["action"], "rejected")
        self.assertEqual(calls, ["commit", "notification:rejected"])

    def test_missing_tenant_returns_404(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch("app.modules.review.service.get_tenant_for_review_update", new=AsyncMock(return_value=None)), self._patch_notification_builder([]) as notification_mock:
            response = self._client(session).post(
                "/api/v1/reviews/tenants/999/approve",
                json={},
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Tenant not found")
        session.commit.assert_not_awaited()
        session.rollback.assert_not_awaited()
        notification_mock.assert_not_called()

    def test_non_pending_tenant_returns_409(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch("app.modules.review.service.get_tenant_for_review_update", new=AsyncMock(return_value=self._tenant(status="active"))), self._patch_notification_builder([]) as notification_mock:
            response = self._client(session).post(
                "/api/v1/reviews/tenants/501/approve",
                json={},
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Tenant application is not pending")
        session.commit.assert_not_awaited()
        session.rollback.assert_not_awaited()
        notification_mock.assert_not_called()

    def test_non_review_role_returns_403(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with self._patch_notification_builder([]) as notification_mock:
            response = self._client(session).post(
                "/api/v1/reviews/tenants/501/approve",
                json={},
                headers=self._headers("org_admin", province="GD", city="GZ"),
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Forbidden")
        session.commit.assert_not_awaited()
        notification_mock.assert_not_called()

    def test_review_decision_requires_jwt(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with self._patch_notification_builder([]) as notification_mock:
            response = self._client(session).post(
                "/api/v1/reviews/tenants/501/approve",
                json={},
            )

        self.assertEqual(response.status_code, 401)
        session.commit.assert_not_awaited()
        notification_mock.assert_not_called()

    def test_region_admin_cannot_review_outside_scope(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with patch("app.modules.review.service.get_tenant_for_review_update", new=AsyncMock(return_value=self._tenant(province="ZJ", city="HZ"))), self._patch_notification_builder([]) as province_notification_mock:
            province_response = self._client(session).post(
                "/api/v1/reviews/tenants/501/approve",
                json={},
                headers=self._headers("province_admin", province="GD"),
            )
        with patch("app.modules.review.service.get_tenant_for_review_update", new=AsyncMock(return_value=self._tenant(province="GD", city="SZ"))), self._patch_notification_builder([]) as city_notification_mock:
            city_response = self._client(session).post(
                "/api/v1/reviews/tenants/501/reject",
                json={"reason": "outside city"},
                headers=self._headers("city_admin", province="GD", city="GZ"),
            )

        self.assertEqual(province_response.status_code, 403)
        self.assertEqual(city_response.status_code, 403)
        session.commit.assert_not_awaited()
        province_notification_mock.assert_not_called()
        city_notification_mock.assert_not_called()

    def test_reject_missing_reason_returns_422(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        with self._patch_notification_builder([]) as notification_mock:
            response = self._client(session).post(
                "/api/v1/reviews/tenants/501/reject",
                json={},
                headers=self._headers("super_admin"),
            )

        self.assertEqual(response.status_code, 422)
        notification_mock.assert_not_called()

    def test_operation_log_failure_rolls_back(self):
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        tenant = self._tenant()
        get_patch, apply_patch, review_log_patch, operation_log_patch, _ = self._patch_writes(
            tenant,
            operation_side_effect=RuntimeError("operation log failed"),
        )

        with get_patch, apply_patch, review_log_patch, operation_log_patch, self._patch_notification_builder([]) as notification_mock:
            with self.assertRaises(RuntimeError):
                self._client(session).post(
                    "/api/v1/reviews/tenants/501/approve",
                    json={},
                    headers=self._headers("super_admin"),
                )

        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
        notification_mock.assert_not_called()
