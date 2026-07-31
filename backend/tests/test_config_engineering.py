import os
import secrets
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class BackendConfigEngineeringTests(unittest.TestCase):
    def setUp(self):
        self.original_env = {
            key: os.environ.get(key)
            for key in [
                "KG_ENV",
                "KG_DATABASE_HOST",
                "KG_DATABASE_PORT",
                "KG_DATABASE_NAME",
                "KG_DATABASE_USER",
                "KG_DATABASE_PASSWORD",
                "KG_JWT_SECRET_KEY",
            ]
        }
        os.environ["KG_DATABASE_PASSWORD"] = secrets.token_urlsafe(24)
        os.environ["KG_JWT_SECRET_KEY"] = secrets.token_urlsafe(32)

    def tearDown(self):
        from app.core.config import get_settings

        get_settings.cache_clear()
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_settings_read_environment_database_values(self):
        from app.core.config import get_settings

        get_settings.cache_clear()
        os.environ["KG_ENV"] = "test"
        os.environ["KG_DATABASE_HOST"] = "db.internal"
        os.environ["KG_DATABASE_PORT"] = "15432"
        os.environ["KG_DATABASE_NAME"] = "kg_test"
        os.environ["KG_DATABASE_USER"] = "kg_user"
        database_password = secrets.token_urlsafe(24)
        os.environ["KG_DATABASE_PASSWORD"] = database_password
        os.environ["KG_JWT_SECRET_KEY"] = secrets.token_urlsafe(32)

        settings = get_settings()

        self.assertEqual(settings.environment, "test")
        self.assertEqual(settings.database_host, "db.internal")
        self.assertEqual(settings.database_port, 15432)
        self.assertEqual(settings.database_name, "kg_test")
        self.assertEqual(settings.database_user, "kg_user")
        self.assertEqual(settings.database_password, database_password)

    def test_database_url_uses_postgresql_asyncpg(self):
        from app.core.config import get_settings
        from app.core.database import build_database_url

        get_settings.cache_clear()
        os.environ["KG_DATABASE_HOST"] = "db.internal"
        os.environ["KG_DATABASE_NAME"] = "kg_test"
        os.environ["KG_DATABASE_USER"] = "kg_user"
        database_password = secrets.token_urlsafe(24)
        os.environ["KG_DATABASE_PASSWORD"] = database_password
        os.environ["KG_JWT_SECRET_KEY"] = secrets.token_urlsafe(32)

        url = build_database_url(get_settings())

        self.assertEqual(
            url,
            f"postgresql+asyncpg://kg_user:{database_password}@db.internal:5432/kg_test",
        )

    def test_missing_database_password_fails_closed(self):
        from app.core.config import get_settings

        os.environ.pop("KG_DATABASE_PASSWORD", None)
        get_settings.cache_clear()

        with self.assertRaisesRegex(RuntimeError, "KG_DATABASE_PASSWORD"):
            get_settings()

    def test_blank_jwt_secret_fails_closed(self):
        from app.core.config import get_settings

        os.environ["KG_JWT_SECRET_KEY"] = "   "
        get_settings.cache_clear()

        with self.assertRaisesRegex(RuntimeError, "KG_JWT_SECRET_KEY"):
            get_settings()

    def test_request_id_header_is_added_to_health_response(self):
        from app.main import create_app

        client = TestClient(create_app())
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["x-request-id"])

    def test_project_dependency_and_alembic_files_exist(self):
        root = Path(__file__).resolve().parents[1]
        pyproject = root / "pyproject.toml"
        alembic_ini = root / "alembic.ini"
        alembic_env = root / "app" / "migrations" / "env.py"
        test_script = root / "scripts" / "test-backend.ps1"

        self.assertIn("fastapi", pyproject.read_text(encoding="utf-8"))
        self.assertIn("sqlalchemy", pyproject.read_text(encoding="utf-8"))
        self.assertIn("asyncpg", pyproject.read_text(encoding="utf-8"))
        self.assertIn("script_location = app/migrations", alembic_ini.read_text(encoding="utf-8"))
        self.assertIn("target_metadata = Base.metadata", alembic_env.read_text(encoding="utf-8"))
        self.assertIn("python -m unittest", test_script.read_text(encoding="utf-8"))
