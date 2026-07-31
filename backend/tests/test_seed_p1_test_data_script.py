from __future__ import annotations

import importlib.util
import os
import secrets
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch


def load_seed_module():
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "seed_p1_test_data.py"
    spec = importlib.util.spec_from_file_location("seed_p1_test_data", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class P1SeedScriptTests(TestCase):
    def test_parse_args_supports_dry_run_and_reset(self):
        module = load_seed_module()

        args = module.parse_args(["--dry-run", "--reset"])

        self.assertTrue(args.dry_run)
        self.assertTrue(args.reset)

    def test_production_environment_is_blocked(self):
        module = load_seed_module()
        settings = Mock(environment="production")

        with self.assertRaises(SystemExit) as context:
            module.ensure_not_production(settings)

        self.assertEqual(context.exception.code, 2)

    def test_missing_seed_password_fails_closed(self):
        module = load_seed_module()

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, module.SEED_PASSWORD_ENV):
                module.get_seed_password()

    def test_build_seed_plan_includes_expected_steps(self):
        module = load_seed_module()

        plan = module.build_seed_plan(reset=False)

        self.assertEqual(
            plan,
            [
                "validate environment",
                "load database settings",
                "create async engine",
                "create async session factory",
                "generate test password hash",
                "plan platform_org seed",
                "plan user seed",
                "plan tenant seed",
                "plan tenant_attachment seed",
                "plan health_profile seed",
                "plan health_indicator seed",
                "plan tenant_binding seed",
            ],
        )

    def test_reset_plan_is_plan_only(self):
        module = load_seed_module()

        plan = module.build_seed_plan(reset=True)

        self.assertIn("plan reset platform_org/user seed data", plan)
        self.assertIn("plan user seed", plan)

    def test_prepare_seed_context_creates_session_factory_and_password_hash(self):
        module = load_seed_module()
        settings = Mock(environment="local")
        settings.database_driver = "postgresql+asyncpg"
        settings.database_user = "kg_app"
        settings.database_password = secrets.token_urlsafe(24)
        settings.database_host = "localhost"
        settings.database_port = 5432
        settings.database_name = "kg_20260727"
        engine = Mock(name="engine")
        session_factory = Mock(name="session_factory")
        seed_password = secrets.token_urlsafe(24)

        with (
            patch.dict(os.environ, {module.SEED_PASSWORD_ENV: seed_password}),
            patch.object(module, "get_settings", return_value=settings),
            patch.object(module, "create_async_engine_from_settings", return_value=engine) as engine_mock,
            patch.object(module, "create_session_factory", return_value=session_factory) as session_factory_mock,
        ):
            context = module.prepare_seed_context()

        engine_mock.assert_called_once_with(settings)
        session_factory_mock.assert_called_once_with(engine)
        self.assertIs(context.settings, settings)
        self.assertIs(context.engine, engine)
        self.assertIs(context.session_factory, session_factory)
        self.assertTrue(context.password_hash.startswith("pbkdf2_sha256$"))
        self.assertNotEqual(context.password_hash, seed_password)

    def test_main_dry_run_prints_execution_plan_without_inserting_data(self):
        module = load_seed_module()
        context = Mock()
        context.settings.environment = "local"
        context.database_url = (
            "postgresql+asyncpg://kg_app:"
            f"{secrets.token_urlsafe(24)}@localhost:5432/kg_20260727"
        )
        context.password_hash = "pbkdf2_sha256$hash"

        with (
            patch.object(module, "prepare_seed_context", return_value=context),
            patch("builtins.print") as print_mock,
        ):
            exit_code = module.main(["--dry-run"])

        self.assertEqual(exit_code, 0)
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn("P1 seed dry-run", printed)
        self.assertIn("plan user seed", printed)
        self.assertIn("No data was inserted", printed)


class FakeAsyncSession:
    def __init__(self):
        self.statements = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class P1SeedExecutionTests(IsolatedAsyncioTestCase):
    async def test_dry_run_does_not_open_session_or_write_data(self):
        module = load_seed_module()
        context = Mock()
        context.session_factory = Mock()

        result = await module.run_identity_seed(context, dry_run=True, reset=True)

        context.session_factory.assert_not_called()
        self.assertTrue(result.dry_run)
        self.assertEqual(result.platform_org_count, len(module.P1_PLATFORM_ORGS))
        self.assertEqual(result.user_count, len(module.P1_USERS))
        self.assertEqual(result.tenant_count, len(module.P1_TENANTS))
        self.assertEqual(result.tenant_attachment_count, len(module.P1_TENANT_ATTACHMENTS))
        self.assertEqual(result.health_profile_count, len(module.P1_HEALTH_PROFILES))
        self.assertEqual(result.health_indicator_count, len(module.P1_HEALTH_INDICATORS))
        self.assertEqual(result.tenant_binding_count, len(module.P1_TENANT_BINDINGS))

    async def test_seed_upserts_p1_identity_tenant_and_health_data(self):
        module = load_seed_module()
        session = FakeAsyncSession()
        context = Mock()
        context.session_factory = Mock(return_value=session)
        context.password_hash = "pbkdf2_sha256$hash"

        result = await module.run_identity_seed(context, dry_run=False, reset=False)

        sql = "\n".join(statement for statement, _ in session.statements)
        self.assertIn("INSERT INTO platform_org", sql)
        self.assertIn('INSERT INTO "user"', sql)
        self.assertIn("INSERT INTO tenant", sql)
        self.assertIn("INSERT INTO tenant_attachment", sql)
        self.assertIn("INSERT INTO health_profile", sql)
        self.assertIn("INSERT INTO health_indicator", sql)
        self.assertIn("UPDATE \"user\"", sql)
        self.assertIn("P1 tenant_binding seed", sql)
        self.assertIn("ON CONFLICT (org_code) DO UPDATE", sql)
        self.assertIn("ON CONFLICT (phone) DO UPDATE", sql)
        self.assertIn("ON CONFLICT (tenant_code) DO UPDATE", sql)
        self.assertIn("ON CONFLICT (user_id) DO UPDATE", sql)
        self.assertNotIn("INSERT INTO tenant_binding", sql)
        self.assertTrue(session.committed)
        self.assertFalse(session.rolled_back)
        self.assertFalse(result.dry_run)
        self.assertEqual(result.platform_org_count, len(module.P1_PLATFORM_ORGS))
        self.assertEqual(result.user_count, len(module.P1_USERS))
        self.assertEqual(result.tenant_count, len(module.P1_TENANTS))
        self.assertEqual(result.tenant_attachment_count, len(module.P1_TENANT_ATTACHMENTS))
        self.assertEqual(result.health_profile_count, len(module.P1_HEALTH_PROFILES))
        self.assertEqual(result.health_indicator_count, len(module.P1_HEALTH_INDICATORS))
        self.assertEqual(result.tenant_binding_count, len(module.P1_TENANT_BINDINGS))

    async def test_seed_reset_deletes_p1_data_in_fk_safe_order_before_upsert(self):
        module = load_seed_module()
        session = FakeAsyncSession()
        context = Mock()
        context.session_factory = Mock(return_value=session)
        context.password_hash = "pbkdf2_sha256$hash"

        await module.run_identity_seed(context, dry_run=False, reset=True)

        sql = "\n".join(statement for statement, _ in session.statements)
        self.assertLess(sql.index("DELETE FROM health_indicator"), sql.index("DELETE FROM health_profile"))
        self.assertLess(sql.index("DELETE FROM health_profile"), sql.index('DELETE FROM "user"'))
        self.assertLess(sql.index("P1 tenant_binding seed reset"), sql.index("DELETE FROM tenant\n"))
        self.assertLess(sql.index("DELETE FROM operation_log"), sql.index('DELETE FROM "user"'))
        self.assertLess(sql.index("DELETE FROM tenant_review_log"), sql.index("DELETE FROM tenant\n"))
        self.assertLess(sql.index("DELETE FROM tenant_attachment"), sql.index("DELETE FROM tenant\n"))
        self.assertLess(sql.index("DELETE FROM tenant\n"), sql.index('DELETE FROM "user"'))
        operation_log_sql, operation_log_params = next(
            (statement, params)
            for statement, params in session.statements
            if "DELETE FROM operation_log" in statement
        )
        review_log_sql, review_log_params = next(
            (statement, params)
            for statement, params in session.statements
            if "DELETE FROM tenant_review_log" in statement
        )
        tenant_ids = [tenant["id"] for tenant in module.P1_TENANTS]
        self.assertIn("module = 'tenant'", operation_log_sql)
        self.assertIn("object_type = 'tenant'", operation_log_sql)
        self.assertIn("object_id IN", operation_log_sql)
        self.assertEqual(operation_log_params, {"tenant_ids": tenant_ids})
        self.assertIn("tenant_id IN", review_log_sql)
        self.assertEqual(review_log_params, {"tenant_ids": tenant_ids})
        self.assertIn('DELETE FROM "user"', sql)
        self.assertIn("DELETE FROM platform_org", sql)
        self.assertIn("P1 health seed reset", sql)
        self.assertIn("P1 tenant_binding seed reset", sql)
        self.assertIn("P1 tenant seed reset", sql)
        self.assertIn("P1 platform_org/user seed reset", sql)
        self.assertNotIn("INSERT INTO tenant_binding", sql)

    async def test_tenant_attachment_seed_is_idempotent_without_full_reset(self):
        module = load_seed_module()
        session = FakeAsyncSession()
        context = Mock()
        context.session_factory = Mock(return_value=session)
        context.password_hash = "pbkdf2_sha256$hash"

        await module.run_identity_seed(context, dry_run=False, reset=False)

        sql = "\n".join(statement for statement, _ in session.statements)
        self.assertIn("DELETE FROM tenant_attachment", sql)
        self.assertLess(sql.index("DELETE FROM tenant_attachment"), sql.index("INSERT INTO tenant_attachment"))

    async def test_health_indicator_seed_has_three_time_points_per_indicator_type(self):
        module = load_seed_module()

        by_type = {}
        for indicator in module.P1_HEALTH_INDICATORS:
            by_type.setdefault(indicator["indicator_type"], set()).add(indicator["recorded_at"])

        self.assertEqual(set(by_type), {"systolic_bp", "diastolic_bp", "heart_rate", "weight"})
        self.assertTrue(all(len(time_points) >= 3 for time_points in by_type.values()))

    async def test_health_indicator_seed_is_idempotent_without_full_reset(self):
        module = load_seed_module()
        session = FakeAsyncSession()
        context = Mock()
        context.session_factory = Mock(return_value=session)
        context.password_hash = "pbkdf2_sha256$hash"

        await module.run_identity_seed(context, dry_run=False, reset=False)

        sql = "\n".join(statement for statement, _ in session.statements)
        self.assertIn("DELETE FROM health_indicator", sql)
        self.assertLess(sql.index("DELETE FROM health_indicator"), sql.index("INSERT INTO health_indicator"))

    async def test_tenant_binding_seed_maps_members_to_active_tenants_only(self):
        module = load_seed_module()

        self.assertEqual(
            module.P1_TENANT_BINDINGS,
            (
                {"user_id": 9201, "tenant_id": 8102},
                {"user_id": 9202, "tenant_id": 8202},
            ),
        )
        active_tenant_ids = {tenant["id"] for tenant in module.P1_TENANTS if tenant["status"] == "active"}
        self.assertTrue(all(binding["tenant_id"] in active_tenant_ids for binding in module.P1_TENANT_BINDINGS))

    async def test_tenant_binding_seed_is_idempotent_without_full_reset(self):
        module = load_seed_module()
        session = FakeAsyncSession()
        context = Mock()
        context.session_factory = Mock(return_value=session)
        context.password_hash = "pbkdf2_sha256$hash"

        await module.run_identity_seed(context, dry_run=False, reset=False)

        sql = "\n".join(statement for statement, _ in session.statements)
        self.assertIn("P1 tenant_binding seed", sql)
        self.assertIn("tenant_id = :tenant_id", sql)
        self.assertNotIn("INSERT INTO tenant_binding", sql)

    async def test_identity_seed_rolls_back_on_failure(self):
        module = load_seed_module()
        session = FakeAsyncSession()

        async def fail_execute(statement, params=None):
            raise RuntimeError("boom")

        session.execute = fail_execute
        context = Mock()
        context.session_factory = Mock(return_value=session)
        context.password_hash = "pbkdf2_sha256$hash"

        with self.assertRaises(RuntimeError):
            await module.run_identity_seed(context, dry_run=False, reset=False)

        self.assertTrue(session.rolled_back)
