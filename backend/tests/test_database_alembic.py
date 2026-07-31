import importlib.util
import secrets
import unittest
from pathlib import Path


class DatabaseAlembicTests(unittest.TestCase):
    def test_metadata_uses_d41_naming_convention(self):
        from app.core.database import Base, get_database_metadata

        metadata = get_database_metadata()

        self.assertIs(metadata, Base.metadata)
        self.assertEqual(metadata.naming_convention["ix"], "ix_%(column_0_label)s")
        self.assertEqual(metadata.naming_convention["pk"], "pk_%(table_name)s")

    def test_async_engine_and_session_factory_are_sqlalchemy_objects_when_dependency_exists(self):
        from app.core.config import Settings
        from app.core.database import (
            create_async_engine_from_settings,
            create_session_factory,
            is_sqlalchemy_available,
        )

        settings = Settings(
            database_host="db.internal",
            database_name="kg_test",
            database_user="kg_user",
            database_password=secrets.token_urlsafe(24),
            jwt_secret_key=secrets.token_urlsafe(32),
        )

        if not is_sqlalchemy_available():
            with self.assertRaisesRegex(RuntimeError, "Install backend dependencies"):
                create_async_engine_from_settings(settings)
            return

        from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

        engine = create_async_engine_from_settings(settings)
        session_factory = create_session_factory(engine)

        self.assertIsInstance(engine, AsyncEngine)
        self.assertIsInstance(session_factory, async_sessionmaker)
        self.assertIn("postgresql+asyncpg://kg_user:***@db.internal:5432/kg_test", str(engine.url))

    def test_alembic_env_binds_target_metadata(self):
        root = Path(__file__).resolve().parents[1]
        env_path = root / "app" / "migrations" / "env.py"
        spec = importlib.util.spec_from_file_location("kg_alembic_env", env_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        from app.core.database import Base

        self.assertIs(module.target_metadata, Base.metadata)

    def test_empty_d49_migration_exists(self):
        root = Path(__file__).resolve().parents[1]
        migration = root / "app" / "migrations" / "versions" / "20260727_0001_empty_database_baseline.py"
        text = migration.read_text(encoding="utf-8")

        self.assertIn("revision = \"20260727_0001\"", text)
        self.assertIn("PostgreSQL 16 + TimescaleDB", text)
        self.assertIn("def upgrade() -> None:", text)
        self.assertIn("def downgrade() -> None:", text)
