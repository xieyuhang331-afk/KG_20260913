import importlib
import inspect
import uuid
from datetime import datetime, timezone
from unittest import TestCase

import pytest

from app.modules.member.infrastructure.mapper import MemberPersistenceState
from app.modules.member.infrastructure.models import MemberOrmModel


INTEGRATION_MODULE = "app.modules.member.infrastructure.orm_state_mapper"
EXPECTED_RED = "Member ORM State Mapper is not implemented"
VALID_MEMBER_ID = uuid.UUID("01890f5d-6d12-7cc4-98c4-dc0c0c07398f")
UTC_INSTANT = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)
INTEGRATION_SCENARIOS = (
    "AUDIT-001",
    "BOUNDARY-001",
    "ERROR-001",
    "LEAK-001",
    "MODEL-TO-STATE",
    "PURITY-001",
    "STATE-TO-MODEL",
    "UUID-001",
    "VERSION-001",
)


def _load_integration_module():
    try:
        return importlib.import_module(INTEGRATION_MODULE)
    except ModuleNotFoundError as exc:
        target_is_missing = (
            exc.name == INTEGRATION_MODULE
            or INTEGRATION_MODULE.startswith(f"{exc.name}.")
        )
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


def _new_state() -> MemberPersistenceState:
    return MemberPersistenceState(
        member_id=VALID_MEMBER_ID,
        member_no="M-P2-INTEGRATION-0001",
        creation_source="registration",
        status="created",
        version=None,
    )


class TestMemberOrmIntegrationContract(TestCase):
    def test_member_orm_state_mapper_contract(self):
        module = _load_integration_module()
        mapper_type = getattr(module, "MemberOrmStateMapper", None)
        error_type = getattr(module, "MemberOrmIntegrationError", None)

        self.assertTrue(inspect.isclass(mapper_type))
        self.assertTrue(inspect.isclass(error_type))
        self.assertTrue(issubclass(error_type, ValueError))
        self.assertFalse(inspect.iscoroutinefunction(mapper_type.to_new_model))
        self.assertFalse(inspect.iscoroutinefunction(mapper_type.to_state))

        mapper = mapper_type()
        state = _new_state()

        with self.subTest(scenario="STATE-TO-MODEL/AUDIT-001/VERSION-001"):
            model = mapper.to_new_model(
                state,
                initial_version=1,
                created_at=UTC_INSTANT,
                updated_at=UTC_INSTANT,
            )

            self.assertIs(type(model), MemberOrmModel)
            self.assertIsNot(model, state)
            self.assertIs(model.member_id, state.member_id)
            self.assertEqual(model.member_no, state.member_no)
            self.assertEqual(model.creation_source, state.creation_source)
            self.assertEqual(model.status, state.status)
            self.assertEqual(model.version, 1)
            self.assertIs(model.created_at, UTC_INSTANT)
            self.assertIs(model.updated_at, UTC_INSTANT)

        with self.subTest(scenario="MODEL-TO-STATE/UUID-001"):
            restored = mapper.to_state(model)

            self.assertIs(type(restored), MemberPersistenceState)
            self.assertIsNot(restored, model)
            self.assertIs(type(restored.member_id), uuid.UUID)
            self.assertIs(restored.member_id, model.member_id)
            self.assertEqual(restored.member_id.version, 7)
            self.assertEqual(restored.member_id.variant, uuid.RFC_4122)
            self.assertEqual(restored.member_no, model.member_no)
            self.assertEqual(restored.creation_source, model.creation_source)
            self.assertEqual(restored.status, model.status)
            self.assertEqual(restored.version, model.version)

        with self.subTest(scenario="ERROR-001/state"):
            with self.assertRaises(error_type) as caught:
                mapper.to_new_model(
                    object(),
                    initial_version=1,
                    created_at=UTC_INSTANT,
                    updated_at=UTC_INSTANT,
                )
            self.assertEqual(
                str(caught.exception), "member persistence state is invalid"
            )

        with self.subTest(scenario="ERROR-001/model"):
            with self.assertRaises(error_type) as caught:
                mapper.to_state(object())
            self.assertEqual(str(caught.exception), "member orm model is invalid")

        with self.subTest(scenario="VERSION-001/invalid-state"):
            persisted_state = MemberPersistenceState(
                member_id=VALID_MEMBER_ID,
                member_no="M-P2-INTEGRATION-0001",
                creation_source="registration",
                status="created",
                version=1,
            )
            with self.assertRaises(error_type) as caught:
                mapper.to_new_model(
                    persisted_state,
                    initial_version=1,
                    created_at=UTC_INSTANT,
                    updated_at=UTC_INSTANT,
                )
            self.assertEqual(
                str(caught.exception), "member persistence version is invalid"
            )

        with self.subTest(scenario="VERSION-001/invalid-initial"):
            for invalid_version in (None, True, 0, 2, "1"):
                with self.assertRaises(error_type) as caught:
                    mapper.to_new_model(
                        state,
                        initial_version=invalid_version,
                        created_at=UTC_INSTANT,
                        updated_at=UTC_INSTANT,
                    )
                self.assertEqual(
                    str(caught.exception),
                    "member persistence version is invalid",
                )

        with self.subTest(scenario="AUDIT-001/invalid"):
            invalid_pairs = (
                (UTC_INSTANT.replace(tzinfo=None), UTC_INSTANT),
                (UTC_INSTANT, UTC_INSTANT.replace(tzinfo=None)),
                (
                    UTC_INSTANT,
                    UTC_INSTANT.replace(minute=UTC_INSTANT.minute + 1),
                ),
            )
            for created_at, updated_at in invalid_pairs:
                with self.assertRaises(error_type) as caught:
                    mapper.to_new_model(
                        state,
                        initial_version=1,
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                self.assertEqual(
                    str(caught.exception), "member audit timestamps are invalid"
                )

        with self.subTest(scenario="BOUNDARY-001/LEAK-001/PURITY-001"):
            forbidden_attributes = {
                "connection",
                "engine",
                "repository",
                "result",
                "row",
                "session",
                "transaction",
                "unit_of_work",
            }

            self.assertTrue(forbidden_attributes.isdisjoint(vars(mapper)))
            self.assertIsNone(state.version)
            self.assertEqual(state.member_no, "M-P2-INTEGRATION-0001")
