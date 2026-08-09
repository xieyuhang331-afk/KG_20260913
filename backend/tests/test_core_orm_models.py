import importlib.util
import json
import unittest
from pathlib import Path


class CoreOrmModelTests(unittest.TestCase):
    def test_d50_core_specs_cover_first_six_tables(self):
        from app.core.model_specs import get_core_table_specs
        from app.modules.models import import_core_models

        governance_map = Path(__file__).resolve().parents[2] / "docs" / "governance" / "ddl-module-map.yaml"
        governance = json.loads(governance_map.read_text(encoding="utf-8"))
        self.assertEqual(governance["schema"], "kg_20260727.ddl_module_map.v1")
        self.assertEqual(governance["project"], "KG_20260727")
        self.assertEqual(governance["status"], "mapped")

        import_core_models()
        specs = get_core_table_specs()

        self.assertEqual(
            set(specs),
            {
                "platform_org",
                "tenant",
                "tenant_attachment",
                "tenant_review_log",
                "user",
                "health_profile",
                "health_indicator",
                "detection_report",
                "message",
                "operation_log",
            },
        )
        self.assertEqual(specs["platform_org"].module, "system")
        self.assertEqual(specs["user"].module, "auth")
        self.assertEqual(specs["tenant"].module, "tenant")
        self.assertEqual(specs["tenant_attachment"].module, "tenant")
        self.assertEqual(specs["health_profile"].module, "user_health")
        self.assertEqual(specs["health_indicator"].module, "user_health")
        self.assertEqual(specs["detection_report"].module, "user_health")
        self.assertEqual(specs["message"].module, "system")
        self.assertEqual(specs["tenant_review_log"].module, "tenant")
        self.assertEqual(specs["operation_log"].module, "system")
        governed_tables = set(specs) - {"operation_log"}
        self.assertTrue(governed_tables <= set(governance["table_to_module"]))

    def test_d50_core_specs_preserve_ddl_fields_and_constraints(self):
        from app.core.model_specs import get_core_table_specs
        from app.modules.models import import_core_models

        import_core_models()
        specs = get_core_table_specs()

        tenant = specs["tenant"]
        self.assertEqual(tenant.column("org_id").foreign_key, "platform_org.id")
        self.assertEqual(tenant.column("tenant_code").ddl_type, "VARCHAR(32)")
        self.assertTrue(tenant.column("tenant_code").unique)
        self.assertFalse(tenant.column("name").nullable)
        self.assertEqual(tenant.column("reviewed_by").foreign_key, "user.id")

        user = specs["user"]
        self.assertEqual(user.column("phone").ddl_type, "VARCHAR(11)")
        self.assertTrue(user.column("phone").unique)
        self.assertEqual(user.column("tenant_id").foreign_key, "tenant.id")
        self.assertEqual(user.column("role").ddl_type, "user_role")
        self.assertEqual(user.column("status").ddl_type, "user_status")

        profile = specs["health_profile"]
        self.assertEqual(profile.column("user_id").foreign_key, "user.id")
        self.assertTrue(profile.column("user_id").unique)
        self.assertEqual(profile.column("medical_history").ddl_type, "JSONB")

        indicator = specs["health_indicator"]
        self.assertTrue(indicator.hypertable)
        self.assertEqual(indicator.hypertable_time_column, "recorded_at")
        self.assertEqual(indicator.column("source").default, "APP")

        report = specs["detection_report"]
        self.assertEqual(report.column("user_id").foreign_key, "user.id")
        self.assertEqual(report.column("store_id").foreign_key, "tenant.id")
        self.assertEqual(report.column("report_data").ddl_type, "JSONB")

        message = specs["message"]
        self.assertEqual(message.column("session_id").foreign_key, "interpretation_session.id")
        self.assertEqual(message.column("sender_id").foreign_key, "user.id")
        self.assertEqual(message.column("is_read").default, "FALSE")

        tenant_review = specs["tenant_review_log"]
        self.assertEqual(tenant_review.column("tenant_id").foreign_key, "tenant.id")
        self.assertEqual(tenant_review.column("reviewer_id").foreign_key, "user.id")
        self.assertEqual(tenant_review.column("action").ddl_type, "VARCHAR(20)")

        operation_log = specs["operation_log"]
        self.assertEqual(operation_log.column("operator_id").foreign_key, "user.id")
        self.assertEqual(operation_log.column("payload").ddl_type, "JSONB")

        platform_org = specs["platform_org"]
        self.assertEqual(platform_org.column("parent_id").foreign_key, "platform_org.id")
        self.assertEqual(platform_org.column("org_code").ddl_type, "VARCHAR(50)")
        self.assertTrue(platform_org.column("org_code").unique)

    def test_d50_core_specs_preserve_indexes(self):
        from app.core.model_specs import get_core_table_specs
        from app.modules.models import import_core_models

        import_core_models()
        specs = get_core_table_specs()

        self.assertIn(("idx_tenant_type_status", ("type", "status")), specs["tenant"].indexes)
        self.assertIn(("idx_user_tenant", ("tenant_id",)), specs["user"].indexes)
        self.assertIn(("idx_hi_user_time", ("user_id", "recorded_at DESC")), specs["health_indicator"].indexes)
        self.assertIn(
            ("idx_detection_report_user_time", ("user_id", "detection_time DESC", "id DESC")),
            specs["detection_report"].indexes,
        )
        self.assertIn(("idx_msg_session", ("session_id", "created_at")), specs["message"].indexes)
        self.assertIn(("idx_tenant_review_log_tenant", ("tenant_id",)), specs["tenant_review_log"].indexes)
        self.assertIn(
            ("idx_operation_log_module_object", ("module", "object_type", "object_id")),
            specs["operation_log"].indexes,
        )

    def test_d50_alembic_env_imports_core_models(self):
        root = Path(__file__).resolve().parents[1]
        env_path = root / "app" / "migrations" / "env.py"
        spec = importlib.util.spec_from_file_location("kg_alembic_env_d50", env_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        from app.core.database import Base
        from app.core.model_specs import get_core_table_specs

        self.assertIs(module.target_metadata, Base.metadata)
        self.assertIn("tenant", get_core_table_specs())
