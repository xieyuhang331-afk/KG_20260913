from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock, patch

from tests.integration import conftest
from tests.integration.database_safety import (
    DisposableDatabaseTarget,
    validate_database_sentinel,
    validate_test_database_target,
)


class IntegrationDatabaseSafetyTests(TestCase):
    def setUp(self):
        self.run_id = "run_123456"
        self.database_url = f"postgresql+asyncpg://kg_app:secret@localhost:55432/kg_it_{self.run_id}"
        self.context = {
            "integration_enabled": "1",
            "destructive_enabled": "1",
            "environment": "local_ephemeral",
            "run_id": self.run_id,
        }

    def validate(self, database_url: str | None = None, **overrides):
        context = self.context | overrides
        return validate_test_database_target(database_url or self.database_url, **context)

    def test_current_ephemeral_database_target_is_allowed(self):
        target = self.validate()

        self.assertEqual(target.database_name, f"kg_it_{self.run_id}")
        self.assertEqual(target.expected_sentinel, f"kg-test-disposable:{self.run_id}")

    def test_both_execution_switches_are_required(self):
        with self.assertRaisesRegex(RuntimeError, "KG_RUN_PG_INTEGRATION"):
            self.validate(integration_enabled="0")
        with self.assertRaisesRegex(RuntimeError, "KG_ALLOW_DESTRUCTIVE_TEST_DATABASE"):
            self.validate(destructive_enabled=None)

    def test_environment_and_run_id_are_required(self):
        with self.assertRaisesRegex(RuntimeError, "KG_TEST_ENVIRONMENT"):
            self.validate(environment="development")
        with self.assertRaisesRegex(RuntimeError, "KG_TEST_RUN_ID"):
            self.validate(run_id="short")

    def test_database_name_must_match_run_id(self):
        with self.assertRaisesRegex(RuntimeError, "current ephemeral test run"):
            self.validate(self.database_url.replace(self.run_id, "other_123456"))

    def test_uat_database_is_rejected_even_with_all_switches(self):
        uat_url = "postgresql+asyncpg://kg_app:secret@localhost:15432/kg_f003_timescale_test"

        with self.assertRaisesRegex(RuntimeError, "never a disposable test target"):
            self.validate(uat_url)

    def test_local_uat_endpoint_is_rejected_regardless_of_database_name(self):
        url = f"postgresql+asyncpg://kg_app:secret@localhost:15432/kg_it_{self.run_id}"

        with self.assertRaisesRegex(RuntimeError, "local UAT database endpoint"):
            self.validate(url)

    def test_test_substring_without_exact_prefix_is_rejected(self):
        url = f"postgresql+asyncpg://kg_app:secret@localhost:55432/project_test_{self.run_id}"

        with self.assertRaisesRegex(RuntimeError, "current ephemeral test run"):
            self.validate(url)

    def test_non_postgresql_and_implicit_port_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL driver"):
            self.validate(f"sqlite:///{self.run_id}")
        with self.assertRaisesRegex(RuntimeError, "explicit host and port"):
            self.validate(f"postgresql+asyncpg://kg_app:secret@localhost/kg_it_{self.run_id}")

    def test_sentinel_must_match_current_run_exactly(self):
        target = DisposableDatabaseTarget(
            database_name=f"kg_it_{self.run_id}",
            run_id=self.run_id,
            expected_sentinel=f"kg-test-disposable:{self.run_id}",
        )

        validate_database_sentinel(f"kg-test-disposable:{self.run_id}", target)
        with self.assertRaisesRegex(RuntimeError, "sentinel"):
            validate_database_sentinel("kg-test-disposable:other_run", target)
        with self.assertRaisesRegex(RuntimeError, "sentinel"):
            validate_database_sentinel(None, target)

    def test_error_does_not_expose_database_password(self):
        password = "do-not-leak-this-password"
        url = f"postgresql+asyncpg://kg_app:{password}@localhost:55432/wrong_name"

        with self.assertRaises(RuntimeError) as context:
            self.validate(url)

        self.assertNotIn(password, str(context.exception))

    def test_fixture_checks_sentinel_before_destructive_schema_reset(self):
        target = DisposableDatabaseTarget(
            database_name=f"kg_it_{self.run_id}",
            run_id=self.run_id,
            expected_sentinel=f"kg-test-disposable:{self.run_id}",
        )
        database = Mock()
        database.fetch_value.return_value = "kg-test-disposable:wrong_run"
        fixture = conftest.pg_database.__wrapped__

        with (
            patch.object(conftest, "_get_test_database_target", return_value=(self.database_url, target)),
            patch.object(conftest, "PgDatabase", return_value=database),
            self.assertRaisesRegex(RuntimeError, "sentinel"),
        ):
            next(fixture())

        database.execute.assert_not_called()
