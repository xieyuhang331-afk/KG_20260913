import importlib.util
import unittest
from pathlib import Path


class SqlAlchemyMappingTests(unittest.TestCase):
    def setUp(self):
        from app.core.database import is_sqlalchemy_available

        if not is_sqlalchemy_available():
            self.skipTest("SQLAlchemy dependency is not installed in this Python environment")

    def test_d51_registers_core_specs_as_sqlalchemy_tables(self):
        from app.core.database import Base
        from app.core.sqlalchemy_mapping import map_core_model_classes

        mapped = map_core_model_classes()

        self.assertEqual(
            set(mapped),
            {
                "platform_org",
                "tenant",
                "tenant_attachment",
                "tenant_review_log",
                "user",
                "health_profile",
                "health_indicator",
                "message",
                "operation_log",
            },
        )
        self.assertIn("platform_org", Base.metadata.tables)
        self.assertIn("tenant", Base.metadata.tables)
        self.assertIn("tenant_review_log", Base.metadata.tables)
        self.assertIn("health_indicator", Base.metadata.tables)
        self.assertIn("operation_log", Base.metadata.tables)
        self.assertEqual(Base.metadata.tables["platform_org"].c.org_code.type.length, 50)
        self.assertTrue(Base.metadata.tables["platform_org"].c.org_code.unique)
        self.assertEqual(Base.metadata.tables["tenant"].c.tenant_code.type.length, 32)
        self.assertTrue(Base.metadata.tables["tenant"].c.tenant_code.unique)
        self.assertFalse(Base.metadata.tables["user"].c.phone.nullable)
        self.assertEqual(str(Base.metadata.tables["health_profile"].c.medical_history.type), "JSONB")
        self.assertTrue(Base.metadata.tables["health_indicator"].info["hypertable"])
        self.assertEqual(Base.metadata.tables["health_indicator"].info["hypertable_time_column"], "recorded_at")

    def test_d51_maps_core_model_classes_to_tables(self):
        from sqlalchemy import inspect

        from app.core.sqlalchemy_mapping import map_core_model_classes
        from app.modules.auth.models import User
        from app.modules.system.models import OperationLog, PlatformOrg
        from app.modules.tenant.models import Tenant, TenantReviewLog
        from app.modules.user_health.models import HealthIndicator

        map_core_model_classes()

        self.assertEqual(inspect(PlatformOrg).local_table.name, "platform_org")
        self.assertEqual(inspect(User).local_table.name, "user")
        self.assertEqual(inspect(Tenant).local_table.name, "tenant")
        self.assertEqual(inspect(TenantReviewLog).local_table.name, "tenant_review_log")
        self.assertEqual(inspect(OperationLog).local_table.name, "operation_log")
        self.assertEqual(inspect(HealthIndicator).local_table.name, "health_indicator")
        self.assertEqual(inspect(HealthIndicator).primary_key[0].name, "id")

    def test_d51_preserves_core_indexes_in_sqlalchemy_metadata(self):
        from app.core.database import Base
        from app.core.sqlalchemy_mapping import map_core_model_classes

        map_core_model_classes()

        tenant_indexes = {index.name for index in Base.metadata.tables["tenant"].indexes}
        tenant_review_indexes = {index.name for index in Base.metadata.tables["tenant_review_log"].indexes}
        indicator_indexes = {index.name for index in Base.metadata.tables["health_indicator"].indexes}
        message_indexes = {index.name for index in Base.metadata.tables["message"].indexes}
        operation_log_indexes = {index.name for index in Base.metadata.tables["operation_log"].indexes}

        self.assertIn("idx_tenant_type_status", tenant_indexes)
        self.assertIn("idx_tenant_review_log_tenant", tenant_review_indexes)
        self.assertIn("idx_hi_user_time", indicator_indexes)
        self.assertIn("idx_msg_session", message_indexes)
        self.assertIn("idx_operation_log_module_object", operation_log_indexes)

    def test_a4_5_1_tenant_org_fk_resolves_to_platform_org_metadata(self):
        from app.core.database import Base
        from app.core.model_specs import get_core_table_specs
        from app.core.sqlalchemy_mapping import map_core_model_classes
        from app.modules.models import get_core_model_classes

        map_core_model_classes()

        self.assertIn("platform_org", get_core_table_specs())
        self.assertIn("platform_org", get_core_model_classes())
        self.assertIn("platform_org", Base.metadata.tables)

        tenant = Base.metadata.tables["tenant"]
        org_id = tenant.c.org_id
        self.assertEqual(next(iter(org_id.foreign_keys)).column.table.name, "platform_org")

    def test_a4_5_2_postgresql_enum_columns_are_mapped_as_native_enums(self):
        from app.core.database import Base
        from app.core.sqlalchemy_mapping import map_core_model_classes

        map_core_model_classes()

        tenant_status = Base.metadata.tables["tenant"].c.status.type
        user_role = Base.metadata.tables["user"].c.role.type
        user_status = Base.metadata.tables["user"].c.status.type

        self.assertEqual(tenant_status.name, "tenant_status")
        self.assertEqual(user_role.name, "user_role")
        self.assertEqual(user_status.name, "user_status")

    def test_d51_alembic_env_loads_sqlalchemy_core_tables(self):
        from app.core.database import Base

        root = Path(__file__).resolve().parents[1]
        env_path = root / "app" / "migrations" / "env.py"
        spec = importlib.util.spec_from_file_location("kg_alembic_env_d51", env_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertIs(module.target_metadata, Base.metadata)
        self.assertIn("platform_org", module.target_metadata.tables)
        self.assertIn("tenant", module.target_metadata.tables)
        self.assertIn("tenant_review_log", module.target_metadata.tables)
        self.assertIn("health_indicator", module.target_metadata.tables)
        self.assertIn("operation_log", module.target_metadata.tables)
