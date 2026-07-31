import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


class FakeF001Session:
    def __init__(self, store):
        self.store = store
        self.commit_count = 0
        self.rollback_count = 0

    async def commit(self):
        self.commit_count += 1
        self.store.events.append("commit")

    async def rollback(self):
        self.rollback_count += 1
        self.store.events.append("rollback")


class InMemoryF001Store:
    def __init__(self):
        self.now = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        self.next_tenant_id = 501
        self.next_attachment_id = 9001
        self.tenants = {}
        self.attachments = {}
        self.review_logs = []
        self.operation_logs = []
        self.notifications = []
        self.events = []
        self.session = FakeF001Session(self)

    async def tenant_exists_by_credit_code(self, session, credit_code):
        return any(tenant.credit_code == credit_code for tenant in self.tenants.values())

    async def create_tenant_application_record(self, session, *, tenant_data, attachments):
        tenant = SimpleNamespace(
            id=self.next_tenant_id,
            short_name=None,
            grade=None,
            reviewed_by=None,
            reviewed_at=None,
            approved_at=None,
            reject_reason=None,
            created_at=self.now,
            updated_at=self.now,
            **tenant_data,
        )
        self.next_tenant_id += 1
        self.tenants[tenant.id] = tenant

        records = []
        for attachment in attachments:
            record = SimpleNamespace(
                id=self.next_attachment_id,
                tenant_id=tenant.id,
                file_type=attachment.file_type,
                file_url=attachment.file_url,
                created_at=self.now,
            )
            self.next_attachment_id += 1
            records.append(record)
        self.attachments[tenant.id] = records
        return tenant, records

    async def list_pending_tenant_applications(self, session, *, province, city, keyword, page, page_size):
        rows = []
        for tenant in self.tenants.values():
            if tenant.status != "pending":
                continue
            if province is not None and tenant.province != province:
                continue
            if city is not None and tenant.city != city:
                continue
            if keyword and keyword not in tenant.name and keyword not in tenant.tenant_code and keyword not in tenant.credit_code:
                continue
            rows.append(
                {
                    "tenant_id": tenant.id,
                    "tenant_code": tenant.tenant_code,
                    "name": tenant.name,
                    "type": tenant.type,
                    "credit_code": tenant.credit_code,
                    "province": tenant.province,
                    "city": tenant.city,
                    "district": tenant.district,
                    "contact_name": tenant.contact_name,
                    "contact_phone": tenant.contact_phone,
                    "status": tenant.status,
                    "submitted_at": tenant.created_at,
                    "attachment_count": len(self.attachments.get(tenant.id, [])),
                }
            )
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

    async def get_tenant_application_detail(self, session, tenant_id):
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            return None
        return {
            "tenant": {
                "id": tenant.id,
                "tenant_code": tenant.tenant_code,
                "name": tenant.name,
                "short_name": tenant.short_name,
                "type": tenant.type,
                "credit_code": tenant.credit_code,
                "license_no": tenant.license_no,
                "license_image": tenant.license_image,
                "legal_person_name": tenant.legal_person_name,
                "province": tenant.province,
                "city": tenant.city,
                "district": tenant.district,
                "address": tenant.address,
                "grade": tenant.grade,
                "contact_name": tenant.contact_name,
                "contact_phone": tenant.contact_phone,
                "contact_email": tenant.contact_email,
                "status": tenant.status,
                "reviewed_by": tenant.reviewed_by,
                "reviewed_at": tenant.reviewed_at,
                "reject_reason": tenant.reject_reason,
                "approved_at": tenant.approved_at,
                "created_at": tenant.created_at,
                "updated_at": tenant.updated_at,
            },
            "attachments": [
                {
                    "id": attachment.id,
                    "file_type": attachment.file_type,
                    "file_url": attachment.file_url,
                    "created_at": attachment.created_at,
                }
                for attachment in self.attachments.get(tenant_id, [])
            ],
        }

    async def get_tenant_for_review_update(self, session, tenant_id):
        return self.tenants.get(tenant_id)

    async def apply_tenant_review_decision(self, session, tenant, **kwargs):
        for key, value in kwargs.items():
            setattr(tenant, key, value)
        self.events.append(f"tenant_update:{kwargs['status']}")
        return tenant

    async def create_tenant_review_log(self, session, **kwargs):
        log = SimpleNamespace(created_at=self.now, **kwargs)
        self.review_logs.append(log)
        self.events.append(f"review_log:{kwargs['action']}")
        return log

    async def create_operation_log(self, session, **kwargs):
        log = SimpleNamespace(created_at=self.now, **kwargs)
        self.operation_logs.append(log)
        self.events.append(f"operation_log:{kwargs['action']}")
        return log

    async def get_tenant_application_status(self, session, tenant_id):
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            return None
        return {
            "tenant_id": tenant.id,
            "tenant_code": tenant.tenant_code,
            "name": tenant.name,
            "org_id": tenant.org_id,
            "status": tenant.status,
            "submitted_at": tenant.created_at,
            "reviewed_at": tenant.reviewed_at,
            "approved_at": tenant.approved_at,
            "reject_reason": tenant.reject_reason,
        }

    def build_notification_intent(self, *, tenant, action):
        event = f"tenant_onboarding_{action}"
        intent = SimpleNamespace(
            event=event,
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            recipient_scope="tenant_org_admin",
            title=event,
            content=tenant.reject_reason if action == "rejected" else "approved",
            payload={
                "tenant_id": tenant.id,
                "status": tenant.status,
                "action": action,
                "reject_reason": tenant.reject_reason,
            },
        )
        self.notifications.append(intent)
        self.events.append(f"notification:{action}")
        return intent


class F001TenantOnboardingE2ETests(unittest.TestCase):
    def _payload(self, *, credit_code="91330100MA00000123", province="ZJ", city="HZ"):
        return {
            "name": f"Kanglin {city} Store",
            "type": "health_store",
            "credit_code": credit_code,
            "license_no": "LIC-20260728",
            "license_image": "mock://license.png",
            "legal_person_name": "Alice",
            "province": province,
            "city": city,
            "district": "XH",
            "address": "No.100 Wensan Road",
            "contact_name": "Bob",
            "contact_phone": "13800138000",
            "contact_email": "ops@example.com",
            "attachments": [
                {
                    "file_type": "business_license",
                    "file_url": "mock://license.png",
                }
            ],
        }

    def _headers(
        self,
        role,
        *,
        user_id=100,
        org_id=None,
        province=None,
        city=None,
    ):
        from app.core.security import create_access_token

        claims = {"sub": str(user_id), "role": role}
        if org_id is not None:
            claims["org_id"] = org_id
        if province is not None:
            claims["province"] = province
        if city is not None:
            claims["city"] = city
        token = create_access_token(claims)
        return {"Authorization": f"Bearer {token}"}

    def _client(self, store):
        from app.core.database import get_db_session
        from app.main import create_app

        app = create_app()

        async def fake_session():
            yield store.session

        app.dependency_overrides[get_db_session] = fake_session
        return TestClient(app)

    def _patched_store(self, store):
        stack = ExitStack()
        stack.enter_context(patch("app.modules.tenant.service.tenant_exists_by_credit_code", new=store.tenant_exists_by_credit_code))
        stack.enter_context(patch("app.modules.tenant.service.create_tenant_application_record", new=store.create_tenant_application_record))
        stack.enter_context(patch("app.modules.tenant.service.get_tenant_application_status", new=store.get_tenant_application_status))
        stack.enter_context(patch("app.modules.review.service.list_pending_tenant_applications", new=store.list_pending_tenant_applications))
        stack.enter_context(patch("app.modules.review.service.get_tenant_application_detail", new=store.get_tenant_application_detail))
        stack.enter_context(patch("app.modules.review.service.get_tenant_for_review_update", new=store.get_tenant_for_review_update))
        stack.enter_context(patch("app.modules.review.service.apply_tenant_review_decision", new=store.apply_tenant_review_decision))
        stack.enter_context(patch("app.modules.review.service.create_tenant_review_log", new=store.create_tenant_review_log))
        stack.enter_context(patch("app.modules.review.service.create_operation_log", new=store.create_operation_log))
        stack.enter_context(patch("app.modules.review.service.build_tenant_review_notification_intent", new=store.build_notification_intent))
        return stack

    def _submit_application(self, client, *, org_id=20, province="ZJ", city="HZ", credit_code="91330100MA00000123"):
        return client.post(
            "/api/v1/tenants",
            json=self._payload(credit_code=credit_code, province=province, city=city),
            headers=self._headers("org_admin", user_id=100, org_id=org_id),
        )

    def test_e001_approve_happy_path_verifies_full_f001_chain(self):
        store = InMemoryF001Store()
        client = self._client(store)

        with self._patched_store(store):
            submit_response = self._submit_application(client)
            tenant_id = submit_response.json()["data"]["id"]

            queue_response = client.get(
                "/api/v1/reviews/queue/tenant",
                headers=self._headers("super_admin", user_id=1),
            )
            detail_response = client.get(
                f"/api/v1/reviews/tenants/{tenant_id}",
                headers=self._headers("super_admin", user_id=1),
            )
            approve_response = client.post(
                f"/api/v1/reviews/tenants/{tenant_id}/approve",
                json={"comment": "approved", "grade": "flagship"},
                headers=self._headers("super_admin", user_id=1),
            )
            status_response = client.get(
                f"/api/v1/tenants/{tenant_id}/application-status",
                headers=self._headers("org_admin", user_id=100, org_id=20),
            )

        self.assertEqual(submit_response.status_code, 200)
        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(status_response.status_code, 200)

        tenant = store.tenants[tenant_id]
        self.assertEqual(tenant.org_id, 20)
        self.assertEqual(tenant.status, "active")
        self.assertEqual(tenant.reviewed_by, 1)
        self.assertIsNotNone(tenant.reviewed_at)
        self.assertIsNotNone(tenant.approved_at)
        self.assertIsNone(tenant.reject_reason)

        self.assertEqual(queue_response.json()["data"]["total"], 1)
        self.assertEqual(queue_response.json()["data"]["items"][0]["status"], "pending")
        self.assertEqual(queue_response.json()["data"]["items"][0]["attachment_count"], 1)
        self.assertEqual(detail_response.json()["data"]["status"]["current"], "pending")
        self.assertEqual(detail_response.json()["data"]["attachments"][0]["file_type"], "business_license")
        self.assertEqual(approve_response.json()["data"]["status"], "active")
        self.assertEqual(status_response.json()["data"]["status"], "active")
        self.assertNotIn("org_id", status_response.json()["data"])

        self.assertEqual(len(store.review_logs), 1)
        self.assertEqual(store.review_logs[0].action, "approved")
        self.assertEqual(store.review_logs[0].reviewer_id, 1)
        self.assertEqual(store.review_logs[0].grade, "flagship")
        self.assertEqual(len(store.operation_logs), 1)
        self.assertEqual(store.operation_logs[0].action, "tenant_onboarding_approved")
        self.assertEqual(store.operation_logs[0].payload["status"], "active")
        self.assertEqual(len(store.notifications), 1)
        self.assertEqual(store.notifications[0].event, "tenant_onboarding_approved")
        self.assertEqual(
            store.events[-5:],
            [
                "tenant_update:active",
                "review_log:approved",
                "operation_log:tenant_onboarding_approved",
                "commit",
                "notification:approved",
            ],
        )

    def test_e002_reject_path_verifies_reason_logs_and_status_readback(self):
        store = InMemoryF001Store()
        client = self._client(store)

        with self._patched_store(store):
            submit_response = self._submit_application(
                client,
                org_id=21,
                credit_code="91330100MA00000999",
            )
            tenant_id = submit_response.json()["data"]["id"]

            missing_reason_response = client.post(
                f"/api/v1/reviews/tenants/{tenant_id}/reject",
                json={},
                headers=self._headers("super_admin", user_id=1),
            )
            reject_response = client.post(
                f"/api/v1/reviews/tenants/{tenant_id}/reject",
                json={"reason": "license image is unclear"},
                headers=self._headers("super_admin", user_id=1),
            )
            status_response = client.get(
                f"/api/v1/tenants/{tenant_id}/application-status",
                headers=self._headers("org_admin", user_id=100, org_id=21),
            )

        self.assertEqual(missing_reason_response.status_code, 422)
        self.assertEqual(reject_response.status_code, 200)
        self.assertEqual(status_response.status_code, 200)

        tenant = store.tenants[tenant_id]
        self.assertEqual(tenant.status, "rejected")
        self.assertEqual(tenant.reviewed_by, 1)
        self.assertIsNotNone(tenant.reviewed_at)
        self.assertIsNone(tenant.approved_at)
        self.assertEqual(tenant.reject_reason, "license image is unclear")
        self.assertEqual(status_response.json()["data"]["status"], "rejected")
        self.assertEqual(status_response.json()["data"]["reject_reason"], "license image is unclear")

        self.assertEqual(len(store.review_logs), 1)
        self.assertEqual(store.review_logs[0].action, "rejected")
        self.assertEqual(store.review_logs[0].comment, "license image is unclear")
        self.assertEqual(len(store.operation_logs), 1)
        self.assertEqual(store.operation_logs[0].action, "tenant_onboarding_rejected")
        self.assertEqual(len(store.notifications), 1)
        self.assertEqual(store.notifications[0].event, "tenant_onboarding_rejected")
        self.assertEqual(store.notifications[0].payload["reject_reason"], "license image is unclear")

    def test_e003_non_pending_tenant_cannot_be_reviewed_again(self):
        store = InMemoryF001Store()
        client = self._client(store)

        with self._patched_store(store):
            submit_response = self._submit_application(client)
            tenant_id = submit_response.json()["data"]["id"]
            first_approve = client.post(
                f"/api/v1/reviews/tenants/{tenant_id}/approve",
                json={},
                headers=self._headers("super_admin", user_id=1),
            )
            second_approve = client.post(
                f"/api/v1/reviews/tenants/{tenant_id}/approve",
                json={},
                headers=self._headers("super_admin", user_id=1),
            )
            reject_after_active = client.post(
                f"/api/v1/reviews/tenants/{tenant_id}/reject",
                json={"reason": "late reject"},
                headers=self._headers("super_admin", user_id=1),
            )

            rejected_submit = self._submit_application(
                client,
                org_id=20,
                credit_code="91330100MA00000888",
            )
            rejected_tenant_id = rejected_submit.json()["data"]["id"]
            first_reject = client.post(
                f"/api/v1/reviews/tenants/{rejected_tenant_id}/reject",
                json={"reason": "bad license"},
                headers=self._headers("super_admin", user_id=1),
            )
            approve_after_rejected = client.post(
                f"/api/v1/reviews/tenants/{rejected_tenant_id}/approve",
                json={},
                headers=self._headers("super_admin", user_id=1),
            )
            second_reject = client.post(
                f"/api/v1/reviews/tenants/{rejected_tenant_id}/reject",
                json={"reason": "again"},
                headers=self._headers("super_admin", user_id=1),
            )

        self.assertEqual(first_approve.status_code, 200)
        self.assertEqual(first_reject.status_code, 200)
        self.assertEqual(second_approve.status_code, 409)
        self.assertEqual(reject_after_active.status_code, 409)
        self.assertEqual(approve_after_rejected.status_code, 409)
        self.assertEqual(second_reject.status_code, 409)
        self.assertEqual(len(store.review_logs), 2)
        self.assertEqual(len(store.operation_logs), 2)
        self.assertEqual(len(store.notifications), 2)

    def test_e004_org_admin_application_status_blocks_cross_org_idor(self):
        store = InMemoryF001Store()
        client = self._client(store)

        with self._patched_store(store):
            own_response = self._submit_application(
                client,
                org_id=20,
                credit_code="91330100MA00000111",
            )
            other_response = self._submit_application(
                client,
                org_id=30,
                credit_code="91330100MA00000222",
            )
            own_tenant_id = own_response.json()["data"]["id"]
            other_tenant_id = other_response.json()["data"]["id"]

            own_status = client.get(
                f"/api/v1/tenants/{own_tenant_id}/application-status",
                headers=self._headers("org_admin", user_id=100, org_id=20),
            )
            cross_org_status = client.get(
                f"/api/v1/tenants/{other_tenant_id}/application-status",
                headers=self._headers("org_admin", user_id=100, org_id=20),
            )
            missing_org_status = client.get(
                f"/api/v1/tenants/{own_tenant_id}/application-status",
                headers=self._headers("org_admin", user_id=100),
            )
            member_status = client.get(
                f"/api/v1/tenants/{own_tenant_id}/application-status",
                headers=self._headers("member", user_id=100, org_id=20),
            )

        self.assertEqual(own_status.status_code, 200)
        self.assertEqual(own_status.json()["data"]["status"], "pending")
        self.assertNotIn("org_id", own_status.json()["data"])
        self.assertEqual(cross_org_status.status_code, 403)
        self.assertEqual(missing_org_status.status_code, 403)
        self.assertEqual(member_status.status_code, 403)

    def test_e005_province_and_city_admin_region_scope_is_enforced(self):
        store = InMemoryF001Store()
        client = self._client(store)

        with self._patched_store(store):
            zj_response = self._submit_application(
                client,
                org_id=20,
                province="ZJ",
                city="HZ",
                credit_code="91330100MA00000333",
            )
            js_response = self._submit_application(
                client,
                org_id=21,
                province="JS",
                city="NJ",
                credit_code="91330100MA00000444",
            )
            nb_response = self._submit_application(
                client,
                org_id=22,
                province="ZJ",
                city="NB",
                credit_code="91330100MA00000555",
            )
            zj_tenant_id = zj_response.json()["data"]["id"]
            js_tenant_id = js_response.json()["data"]["id"]
            nb_tenant_id = nb_response.json()["data"]["id"]

            province_queue = client.get(
                "/api/v1/reviews/queue/tenant",
                headers=self._headers("province_admin", user_id=2, province="ZJ"),
            )
            city_queue = client.get(
                "/api/v1/reviews/queue/tenant",
                headers=self._headers("city_admin", user_id=3, province="ZJ", city="HZ"),
            )
            province_cross_detail = client.get(
                f"/api/v1/reviews/tenants/{js_tenant_id}",
                headers=self._headers("province_admin", user_id=2, province="ZJ"),
            )
            province_cross_approve = client.post(
                f"/api/v1/reviews/tenants/{js_tenant_id}/approve",
                json={},
                headers=self._headers("province_admin", user_id=2, province="ZJ"),
            )
            city_cross_detail = client.get(
                f"/api/v1/reviews/tenants/{nb_tenant_id}",
                headers=self._headers("city_admin", user_id=3, province="ZJ", city="HZ"),
            )
            city_cross_reject = client.post(
                f"/api/v1/reviews/tenants/{nb_tenant_id}/reject",
                json={"reason": "outside city"},
                headers=self._headers("city_admin", user_id=3, province="ZJ", city="HZ"),
            )
            city_own_approve = client.post(
                f"/api/v1/reviews/tenants/{zj_tenant_id}/approve",
                json={},
                headers=self._headers("city_admin", user_id=3, province="ZJ", city="HZ"),
            )

        self.assertEqual(province_queue.status_code, 200)
        self.assertEqual({item["tenant_id"] for item in province_queue.json()["data"]["items"]}, {zj_tenant_id, nb_tenant_id})
        self.assertEqual(city_queue.status_code, 200)
        self.assertEqual([item["tenant_id"] for item in city_queue.json()["data"]["items"]], [zj_tenant_id])
        self.assertEqual(province_cross_detail.status_code, 403)
        self.assertEqual(province_cross_approve.status_code, 403)
        self.assertEqual(city_cross_detail.status_code, 403)
        self.assertEqual(city_cross_reject.status_code, 403)
        self.assertEqual(city_own_approve.status_code, 200)

        self.assertEqual(store.tenants[js_tenant_id].status, "pending")
        self.assertEqual(store.tenants[nb_tenant_id].status, "pending")
        self.assertEqual(store.tenants[zj_tenant_id].status, "active")
        self.assertEqual(len(store.review_logs), 1)
        self.assertEqual(len(store.operation_logs), 1)
        self.assertEqual(len(store.notifications), 1)
