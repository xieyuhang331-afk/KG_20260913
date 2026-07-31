import importlib.util
import unittest
from pathlib import Path


class TenantOnboardingModelTests(unittest.TestCase):
    def test_f001_registers_tenant_review_and_operation_log_specs(self):
        from app.core.model_specs import get_core_table_specs
        from app.modules.models import import_core_models

        import_core_models()
        specs = get_core_table_specs()

        self.assertIn("tenant_review_log", specs)
        self.assertIn("operation_log", specs)

        tenant_review = specs["tenant_review_log"]
        self.assertEqual(tenant_review.module, "tenant")
        self.assertEqual(tenant_review.column("tenant_id").foreign_key, "tenant.id")
        self.assertEqual(tenant_review.column("reviewer_id").foreign_key, "user.id")
        self.assertFalse(tenant_review.column("action").nullable)
        self.assertEqual(tenant_review.column("grade").ddl_type, "VARCHAR(20)")
        self.assertEqual(tenant_review.column("comment").ddl_type, "TEXT")
        self.assertEqual(tenant_review.column("created_at").default, "NOW()")

        operation_log = specs["operation_log"]
        self.assertEqual(operation_log.module, "system")
        self.assertEqual(operation_log.column("operator_id").foreign_key, "user.id")
        self.assertFalse(operation_log.column("module").nullable)
        self.assertFalse(operation_log.column("object_type").nullable)
        self.assertFalse(operation_log.column("action").nullable)
        self.assertEqual(operation_log.column("payload").ddl_type, "JSONB")
        self.assertEqual(operation_log.column("created_at").default, "NOW()")

    def test_f001_preserves_indexes_for_new_audit_tables(self):
        from app.core.model_specs import get_core_table_specs
        from app.modules.models import import_core_models

        import_core_models()
        specs = get_core_table_specs()

        self.assertIn(
            ("idx_tenant_review_log_tenant", ("tenant_id",)),
            specs["tenant_review_log"].indexes,
        )
        self.assertIn(
            ("idx_tenant_review_log_reviewer", ("reviewer_id",)),
            specs["tenant_review_log"].indexes,
        )
        self.assertIn(
            ("idx_operation_log_operator", ("operator_id",)),
            specs["operation_log"].indexes,
        )
        self.assertIn(
            ("idx_operation_log_module_object", ("module", "object_type", "object_id")),
            specs["operation_log"].indexes,
        )

    def test_f001_sqlalchemy_metadata_contains_new_audit_tables(self):
        from app.core.database import Base, is_sqlalchemy_available

        if not is_sqlalchemy_available():
            self.skipTest("SQLAlchemy dependency is not installed in this Python environment")

        from app.core.sqlalchemy_mapping import map_core_model_classes

        mapped = map_core_model_classes()

        self.assertIn("tenant_review_log", mapped)
        self.assertIn("operation_log", mapped)
        self.assertIn("tenant_review_log", Base.metadata.tables)
        self.assertIn("operation_log", Base.metadata.tables)

        tenant_review = Base.metadata.tables["tenant_review_log"]
        operation_log = Base.metadata.tables["operation_log"]

        self.assertTrue(tenant_review.c.tenant_id.foreign_keys)
        self.assertTrue(tenant_review.c.reviewer_id.foreign_keys)
        self.assertTrue(operation_log.c.operator_id.foreign_keys)
        self.assertEqual(str(operation_log.c.payload.type), "JSONB")

        tenant_review_indexes = {index.name for index in tenant_review.indexes}
        operation_log_indexes = {index.name for index in operation_log.indexes}
        self.assertIn("idx_tenant_review_log_tenant", tenant_review_indexes)
        self.assertIn("idx_operation_log_module_object", operation_log_indexes)

    def test_f001_alembic_env_loads_new_audit_tables(self):
        from app.core.database import Base

        root = Path(__file__).resolve().parents[1]
        env_path = root / "app" / "migrations" / "env.py"
        spec = importlib.util.spec_from_file_location("kg_alembic_env_f001", env_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertIs(module.target_metadata, Base.metadata)
        self.assertIn("tenant_review_log", module.target_metadata.tables)
        self.assertIn("operation_log", module.target_metadata.tables)

    def test_f001_migration_revision_exists_for_audit_tables(self):
        root = Path(__file__).resolve().parents[1]
        migration = root / "app" / "migrations" / "versions" / "20260728_0003_f001_tenant_onboarding_models.py"

        self.assertTrue(migration.is_file())
        text = migration.read_text(encoding="utf-8")
        self.assertIn('revision = "20260728_0003"', text)
        self.assertIn('down_revision = "20260728_0002_core_baseline"', text)
        self.assertIn("tenant_review_log", text)
        self.assertIn("operation_log", text)
        self.assertIn("def upgrade() -> None:", text)
        self.assertIn("def downgrade() -> None:", text)
