import unittest
from types import SimpleNamespace


class TenantReviewNotificationIntentTests(unittest.TestCase):
    def _tenant(self, *, status: str, reject_reason: str | None = None):
        return SimpleNamespace(
            id=501,
            name="Kanglin Guangzhou Store",
            status=status,
            reject_reason=reject_reason,
        )

    def test_approved_tenant_review_builds_approved_notification_intent(self):
        from app.modules.system.notification_service import build_tenant_review_notification_intent

        intent = build_tenant_review_notification_intent(
            tenant=self._tenant(status="active"),
            action="approved",
        )

        self.assertEqual(intent.event, "tenant_onboarding_approved")
        self.assertEqual(intent.tenant_id, 501)
        self.assertEqual(intent.tenant_name, "Kanglin Guangzhou Store")
        self.assertEqual(intent.recipient_scope, "tenant_org_admin")
        self.assertEqual(intent.title, "门店入驻审核已通过")
        self.assertEqual(intent.content, "您的门店入驻申请已通过审核，门店状态已启用。")
        self.assertEqual(
            intent.payload,
            {
                "tenant_id": 501,
                "status": "active",
                "action": "approved",
            },
        )

    def test_rejected_tenant_review_builds_rejected_notification_intent(self):
        from app.modules.system.notification_service import build_tenant_review_notification_intent

        intent = build_tenant_review_notification_intent(
            tenant=self._tenant(status="rejected", reject_reason="license image is unclear"),
            action="rejected",
        )

        self.assertEqual(intent.event, "tenant_onboarding_rejected")
        self.assertEqual(intent.tenant_id, 501)
        self.assertEqual(intent.tenant_name, "Kanglin Guangzhou Store")
        self.assertEqual(intent.recipient_scope, "tenant_org_admin")
        self.assertEqual(intent.title, "门店入驻审核未通过")
        self.assertEqual(intent.content, "您的门店入驻申请未通过审核，原因：license image is unclear")
        self.assertEqual(
            intent.payload,
            {
                "tenant_id": 501,
                "status": "rejected",
                "action": "rejected",
                "reject_reason": "license image is unclear",
            },
        )

    def test_unknown_tenant_review_action_is_rejected(self):
        from app.modules.system.notification_service import build_tenant_review_notification_intent

        with self.assertRaises(ValueError):
            build_tenant_review_notification_intent(
                tenant=self._tenant(status="pending"),
                action="pending",
            )

