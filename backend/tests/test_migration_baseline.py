import importlib.util
import unittest
from pathlib import Path


class MigrationBaselineTests(unittest.TestCase):
    def setUp(self):
        self.versions = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions"

    def _load_revision(self, filename: str):
        path = self.versions / filename
        self.assertTrue(path.is_file(), f"missing migration file: {path.name}")
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module, path.read_text(encoding="utf-8")

    def test_task_1_5_revision_chain_inserts_core_baseline_before_f001(self):
        empty, _ = self._load_revision("20260727_0001_empty_database_baseline.py")
        core, _ = self._load_revision("20260728_0002_core_tenant_user_baseline.py")
        f001, _ = self._load_revision("20260728_0003_f001_tenant_onboarding_models.py")
        attachment, _ = self._load_revision("20260728_0004_tenant_attachment.py")

        self.assertEqual(empty.revision, "20260727_0001")
        self.assertIsNone(empty.down_revision)
        self.assertEqual(core.revision, "20260728_0002_core_baseline")
        self.assertEqual(core.down_revision, "20260727_0001")
        self.assertEqual(f001.revision, "20260728_0003")
        self.assertEqual(f001.down_revision, "20260728_0002_core_baseline")
        self.assertEqual(attachment.revision, "20260728_0004")
        self.assertEqual(attachment.down_revision, "20260728_0003")

    def test_task_1_5_core_baseline_defines_only_required_enums_and_tables(self):
        _, text = self._load_revision("20260728_0002_core_tenant_user_baseline.py")

        for expected in (
            "user_role",
            "tenant_status",
            "user_status",
            '"platform_org"',
            '"tenant"',
            '"user"',
        ):
            self.assertIn(expected, text)

        for excluded in (
            "health_profile",
            "health_indicator",
            "message",
            "interpretation_session",
            "health_plan",
        ):
            self.assertNotIn(excluded, text)

    def test_task_1_5_core_baseline_handles_tenant_user_foreign_key_cycle(self):
        _, text = self._load_revision("20260728_0002_core_tenant_user_baseline.py")

        tenant_create = text.index('"tenant",')
        user_create = text.index('"user",')
        reviewed_by_fk = text.index('op.create_foreign_key("fk_tenant_reviewed_by_user"')

        self.assertLess(tenant_create, user_create)
        self.assertLess(user_create, reviewed_by_fk)
        self.assertIn('["reviewed_by"]', text)
        self.assertIn('"tenant", "user"', text)
        self.assertIn('["id"]', text)

    def test_task_1_5_f001_revision_depends_on_core_baseline(self):
        _, text = self._load_revision("20260728_0003_f001_tenant_onboarding_models.py")

        self.assertIn('revision = "20260728_0003"', text)
        self.assertIn('down_revision = "20260728_0002_core_baseline"', text)
        self.assertIn("tenant_review_log", text)
        self.assertIn("operation_log", text)

    def test_a4_tenant_attachment_revision_depends_on_f001(self):
        _, text = self._load_revision("20260728_0004_tenant_attachment.py")

        self.assertIn('revision = "20260728_0004"', text)
        self.assertIn('down_revision = "20260728_0003"', text)
        self.assertIn("tenant_attachment", text)
        self.assertIn("tenant_id", text)
        self.assertIn("tenant.id", text)
        self.assertIn("file_type", text)
        self.assertIn("file_url", text)

    def test_f002_health_profile_revision_depends_on_tenant_attachment(self):
        module, text = self._load_revision("20260728_0005_f002_health_profile.py")

        self.assertEqual(module.revision, "20260728_0005")
        self.assertEqual(module.down_revision, "20260728_0004")
        self.assertIn('"health_profile"', text)

    def test_f002_health_profile_revision_only_creates_health_profile(self):
        _, text = self._load_revision("20260728_0005_f002_health_profile.py")

        self.assertEqual(text.count("op.create_table("), 1)
        self.assertIn('"health_profile"', text)
        for excluded in ("health_indicator", "medication_record", "detection_report"):
            self.assertNotIn(excluded, text)

    def test_f003_health_indicator_revision_depends_on_health_profile(self):
        module, text = self._load_revision("20260728_0006_f003_health_indicator.py")

        self.assertEqual(module.revision, "20260728_0006")
        self.assertEqual(module.down_revision, "20260728_0005")
        self.assertIn('"health_indicator"', text)

    def test_f003_health_indicator_revision_only_creates_health_indicator(self):
        _, text = self._load_revision("20260728_0006_f003_health_indicator.py")

        self.assertEqual(text.count("op.create_table("), 1)
        self.assertIn('"health_indicator"', text)
        for excluded in ("detection_report", "assessment_report", "health_plan", "device_data_raw"):
            self.assertNotIn(excluded, text)

    def test_f003_health_indicator_revision_defines_timescale_contract(self):
        _, text = self._load_revision("20260728_0006_f003_health_indicator.py")

        self.assertIn('sa.PrimaryKeyConstraint("id", "recorded_at")', text)
        self.assertIn('sa.ForeignKeyConstraint(["user_id"], ["user.id"])', text)
        self.assertIn("ck_health_indicator_source", text)
        self.assertIn("APP", text)
        self.assertIn("STORE", text)
        self.assertIn("DEVICE", text)
        self.assertIn("REPORT", text)
        self.assertIn("create_hypertable", text)
        self.assertIn("idx_hi_user_time", text)
        self.assertIn("idx_hi_type_time", text)
