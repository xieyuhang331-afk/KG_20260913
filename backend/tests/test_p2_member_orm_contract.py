import importlib
import uuid
from unittest import TestCase

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.modules.member import Member
from app.modules.member.infrastructure.mapper import MemberPersistenceState


MODEL_MODULE = "app.modules.member.infrastructure.models"
EXPECTED_RED = "Member ORM Model is not implemented"
ORM_SCENARIOS = (
    "AUDIT-001",
    "BOUNDARY-001",
    "ENUM-001",
    "INDEX-001",
    "LEAK-001",
    "MEMBER-NO-001",
    "NULL-001",
    "TABLE-001",
    "UUID-001",
    "UUID-002",
    "VERSION-001",
    "VERSION-002",
)


def _load_model_module():
    try:
        return importlib.import_module(MODEL_MODULE)
    except ModuleNotFoundError as exc:
        target_is_missing = (
            exc.name == MODEL_MODULE or MODEL_MODULE.startswith(f"{exc.name}.")
        )
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


class TestMemberOrmContract(TestCase):
    def test_member_orm_model_contract(self):
        module = _load_model_module()
        model_type = getattr(module, "MemberOrmModel", None)

        self.assertTrue(isinstance(model_type, type))
        self.assertFalse(issubclass(model_type, Member))
        self.assertIsNot(model_type, MemberPersistenceState)

        inspection = sa.inspect(model_type)
        table = inspection.local_table
        columns = table.c

        with self.subTest(scenario="TABLE-001/BOUNDARY-001"):
            self.assertEqual(table.schema, "identity")
            self.assertEqual(table.name, "member")
            self.assertEqual(
                set(columns.keys()),
                {
                    "created_at",
                    "creation_source",
                    "member_id",
                    "member_no",
                    "status",
                    "updated_at",
                    "version",
                },
            )
            self.assertEqual(len(inspection.relationships), 0)
            self.assertEqual(len(table.foreign_keys), 0)

        with self.subTest(scenario="UUID-001/UUID-002"):
            member_id = columns.member_id
            self.assertTrue(member_id.primary_key)
            self.assertFalse(member_id.nullable)
            self.assertIsInstance(member_id.type, postgresql.UUID)
            self.assertTrue(member_id.type.as_uuid)
            self.assertIsNone(member_id.default)
            self.assertIsNone(member_id.server_default)

        with self.subTest(scenario="MEMBER-NO-001/INDEX-001"):
            member_no = columns.member_no
            self.assertIsInstance(member_no.type, sa.String)
            self.assertEqual(member_no.type.length, 64)
            self.assertFalse(member_no.nullable)

            unique_column_sets = {
                tuple(column.name for column in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, sa.UniqueConstraint)
            }
            self.assertIn(("member_no",), unique_column_sets)
            self.assertEqual(len(table.indexes), 0)

        with self.subTest(scenario="ENUM-001/NULL-001"):
            for name in ("creation_source", "status"):
                column = columns[name]
                self.assertIsInstance(column.type, sa.String)
                self.assertNotIsInstance(column.type, sa.Enum)
                self.assertEqual(column.type.length, 32)
                self.assertFalse(column.nullable)
                self.assertIsNone(column.default)
                self.assertIsNone(column.server_default)

        with self.subTest(scenario="VERSION-001/VERSION-002"):
            version = columns.version
            self.assertIsInstance(version.type, sa.BigInteger)
            self.assertFalse(version.nullable)
            self.assertIsNone(version.default)
            self.assertIsNone(version.server_default)
            self.assertIsNone(version.onupdate)
            self.assertIsNone(version.server_onupdate)

        with self.subTest(scenario="AUDIT-001"):
            for name in ("created_at", "updated_at"):
                column = columns[name]
                self.assertIsInstance(column.type, sa.DateTime)
                self.assertTrue(column.type.timezone)
                self.assertFalse(column.nullable)
                self.assertIsNone(column.default)
                self.assertIsNone(column.server_default)
                self.assertIsNone(column.onupdate)
                self.assertIsNone(column.server_onupdate)

        with self.subTest(scenario="LEAK-001"):
            forbidden_attributes = {
                "connection",
                "engine",
                "query",
                "result",
                "row",
                "session",
                "transaction",
            }
            self.assertTrue(forbidden_attributes.isdisjoint(vars(model_type)))
            for column in columns:
                self.assertFalse(callable(column.default))

        with self.subTest(scenario="UUID-001/python-boundary"):
            member_id_type = columns.member_id.type.python_type
            self.assertIs(member_id_type, uuid.UUID)

