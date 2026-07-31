import unittest

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


class CurrentUserPermissionTests(unittest.TestCase):
    def test_get_current_user_parses_identity_headers(self):
        from app.core.security import CurrentUser, get_current_user

        app = FastAPI()

        @app.get("/whoami")
        async def whoami(current_user: CurrentUser = Depends(get_current_user)):
            return {
                "id": current_user.id,
                "role": current_user.role,
                "tenant_id": current_user.tenant_id,
                "org_id": current_user.org_id,
                "province": current_user.province,
                "city": current_user.city,
            }

        response = TestClient(app).get(
            "/whoami",
            headers={
                "x-user-id": "101",
                "x-user-role": "org_admin",
                "x-tenant-id": "202",
                "x-org-id": "303",
                "x-user-province": "ZJ",
                "x-user-city": "HZ",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": 101,
                "role": "org_admin",
                "tenant_id": 202,
                "org_id": 303,
                "province": "ZJ",
                "city": "HZ",
            },
        )

    def test_get_current_user_requires_identity_headers(self):
        from app.core.security import CurrentUser, get_current_user

        app = FastAPI()

        @app.get("/whoami")
        async def whoami(current_user: CurrentUser = Depends(get_current_user)):
            return {"id": current_user.id}

        response = TestClient(app).get("/whoami")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")

    def test_get_current_user_rejects_unsupported_role(self):
        from app.core.security import CurrentUser, get_current_user

        app = FastAPI()

        @app.get("/whoami")
        async def whoami(current_user: CurrentUser = Depends(get_current_user)):
            return {"role": current_user.role}

        response = TestClient(app).get(
            "/whoami",
            headers={"x-user-id": "101", "x-user-role": "SA"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Unsupported role")

    def test_get_current_user_rejects_invalid_numeric_headers(self):
        from app.core.security import CurrentUser, get_current_user

        app = FastAPI()

        @app.get("/whoami")
        async def whoami(current_user: CurrentUser = Depends(get_current_user)):
            return {"id": current_user.id}

        response = TestClient(app).get(
            "/whoami",
            headers={"x-user-id": "not-a-number", "x-user-role": "org_admin"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid current user header")

    def test_org_admin_can_submit_tenant_application(self):
        from app.core.permissions import can_submit_tenant_application
        from app.core.security import CurrentUser

        self.assertTrue(can_submit_tenant_application(CurrentUser(id=1, role="org_admin", tenant_id=10)))
        self.assertFalse(can_submit_tenant_application(CurrentUser(id=2, role="member", tenant_id=10)))
        self.assertFalse(can_submit_tenant_application(CurrentUser(id=3, role="therapist", tenant_id=10)))

    def test_platform_roles_can_review_tenant_application(self):
        from app.core.permissions import can_review_tenant_application
        from app.core.security import CurrentUser

        self.assertTrue(can_review_tenant_application(CurrentUser(id=1, role="super_admin")))
        self.assertTrue(can_review_tenant_application(CurrentUser(id=2, role="province_admin")))
        self.assertTrue(can_review_tenant_application(CurrentUser(id=3, role="city_admin")))
        self.assertFalse(can_review_tenant_application(CurrentUser(id=4, role="org_admin", tenant_id=10)))
        self.assertFalse(can_review_tenant_application(CurrentUser(id=5, role="member", tenant_id=10)))
        self.assertFalse(can_review_tenant_application(CurrentUser(id=6, role="therapist", tenant_id=10)))

    def test_tenant_access_uses_current_user_tenant_scope(self):
        from app.core.permissions import can_access_tenant
        from app.core.security import CurrentUser

        self.assertTrue(can_access_tenant(CurrentUser(id=1, role="super_admin"), 99))
        self.assertTrue(can_access_tenant(CurrentUser(id=2, role="org_admin", tenant_id=10), 10))
        self.assertFalse(can_access_tenant(CurrentUser(id=3, role="org_admin", tenant_id=10), 11))
        self.assertFalse(can_access_tenant(CurrentUser(id=4, role="member", tenant_id=10), 10))
        self.assertFalse(can_access_tenant(CurrentUser(id=5, role="org_admin"), 10))

    def test_region_access_uses_platform_role_scope(self):
        from app.core.permissions import can_access_region
        from app.core.security import CurrentUser

        self.assertTrue(can_access_region(CurrentUser(id=1, role="super_admin"), province="浙江省", city="杭州市"))
        self.assertTrue(
            can_access_region(
                CurrentUser(id=2, role="province_admin", province="浙江省"),
                province="浙江省",
                city="杭州市",
            )
        )
        self.assertFalse(
            can_access_region(
                CurrentUser(id=3, role="province_admin", province="江苏省"),
                province="浙江省",
                city="杭州市",
            )
        )
        self.assertTrue(
            can_access_region(
                CurrentUser(id=4, role="city_admin", province="浙江省", city="杭州市"),
                province="浙江省",
                city="杭州市",
            )
        )
        self.assertFalse(
            can_access_region(
                CurrentUser(id=5, role="city_admin", province="浙江省", city="宁波市"),
                province="浙江省",
                city="杭州市",
            )
        )
        self.assertFalse(can_access_region(CurrentUser(id=6, role="org_admin", tenant_id=10), province="浙江省", city="杭州市"))

    def test_only_member_can_access_own_user_resource(self):
        from fastapi import HTTPException

        from app.core.permissions import ensure_can_access_own_user_resource, ensure_is_member
        from app.core.security import CurrentUser

        member = CurrentUser(id=10, role="member")
        ensure_is_member(member)
        ensure_can_access_own_user_resource(member, 10)

        denied_cases = [
            (CurrentUser(id=10, role="member"), 11),
            (CurrentUser(id=20, role="org_admin"), 20),
            (CurrentUser(id=30, role="super_admin"), 30),
            (CurrentUser(id=40, role="province_admin"), 40),
            (CurrentUser(id=50, role="city_admin"), 50),
        ]

        for user, target_user_id in denied_cases:
            with self.subTest(role=user.role, target_user_id=target_user_id):
                with self.assertRaises(HTTPException) as context:
                    ensure_can_access_own_user_resource(user, target_user_id)
                self.assertEqual(context.exception.status_code, 403)
                self.assertEqual(context.exception.detail, "Forbidden")

    def test_non_member_roles_are_rejected_by_member_guard(self):
        from fastapi import HTTPException

        from app.core.permissions import ensure_is_member
        from app.core.security import CurrentUser

        for role in ("org_admin", "super_admin", "province_admin", "city_admin"):
            with self.subTest(role=role):
                with self.assertRaises(HTTPException) as context:
                    ensure_is_member(CurrentUser(id=1, role=role))
                self.assertEqual(context.exception.status_code, 403)
                self.assertEqual(context.exception.detail, "Forbidden")

    def test_dependency_override_can_supply_current_user(self):
        from app.core.security import CurrentUser, get_current_user

        app = FastAPI()

        @app.get("/whoami")
        async def whoami(current_user: CurrentUser = Depends(get_current_user)):
            return {"id": current_user.id, "role": current_user.role, "tenant_id": current_user.tenant_id}

        async def override_current_user():
            return CurrentUser(id=77, role="org_admin", tenant_id=88)

        app.dependency_overrides[get_current_user] = override_current_user
        response = TestClient(app).get("/whoami")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": 77, "role": "org_admin", "tenant_id": 88})
