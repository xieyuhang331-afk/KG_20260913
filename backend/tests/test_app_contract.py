import unittest

from fastapi.testclient import TestClient


class BackendAppContractTests(unittest.TestCase):
    def test_health_check_returns_standard_response(self):
        from app.main import create_app

        client = TestClient(create_app())
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "service": "KG_20260727",
                    "status": "ok",
                    "version": "0.1.0",
                },
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_module_registry_matches_d40_baseline(self):
        from app.modules.registry import get_module_registry

        modules = get_module_registry()
        self.assertEqual(
            [module.slug for module in modules],
            [
                "auth",
                "tenant",
                "institution_onboarding",
                "private_file",
                "user_health",
                "template",
                "plan",
                "ai",
                "product",
                "order",
                "review",
                "service_exec",
                "member",
                "system",
            ],
        )
        self.assertIn("/ws", modules[-1].route_prefixes)

    def test_settings_expose_d47_infrastructure_targets(self):
        from app.core.config import get_settings

        settings = get_settings()

        self.assertEqual(settings.database_driver, "postgresql+asyncpg")
        self.assertEqual(settings.async_runtime, "Celery + RabbitMQ")
        self.assertEqual(settings.file_storage_backend, "local_filesystem")
        self.assertEqual(
            settings.celery_queues,
            ("ai", "judgment", "ocr", "report", "settlement", "notification"),
        )
