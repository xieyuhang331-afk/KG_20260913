import importlib
import inspect
import uuid
from unittest import TestCase

import pytest

from app.modules.member import CreationSource, Member, MemberNo, MemberStatus
from app.modules.member.errors import (
    InvalidCreationSourceError,
    InvalidMemberIdError,
    InvalidMemberNoError,
    InvalidMemberStatusError,
)


MAPPER_MODULE = "app.modules.member.infrastructure.mapper"
EXPECTED_RED = "Member Mapper is not implemented"
VALID_MEMBER_ID = uuid.UUID("01890f5d-6d12-7cc4-98c4-dc0c0c07398f")
MAPPER_SCENARIOS = (
    "ENUM-001",
    "ENUM-002",
    "ERROR-001",
    "IDENTITY-001",
    "LEAK-001",
    "MAP-D2P",
    "MAP-P2D",
    "MEMBER-NO",
    "OPTIONAL-001",
    "PURITY-001",
    "UUID-001",
    "UUID-002",
    "VERSION-001",
    "VERSION-002",
)


def _load_mapper_module():
    try:
        return importlib.import_module(MAPPER_MODULE)
    except ModuleNotFoundError as exc:
        target_is_missing = (
            exc.name == MAPPER_MODULE
            or MAPPER_MODULE.startswith(f"{exc.name}.")
        )
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


def _member() -> Member:
    return Member(
        member_id=VALID_MEMBER_ID,
        member_no=MemberNo("M-P2-MAPPER-0001"),
        creation_source=CreationSource.REGISTRATION,
        status=MemberStatus.CREATED,
    )


class TestMemberMapperContract(TestCase):
    def test_member_mapper_contract(self):
        module = _load_mapper_module()
        mapper_type = getattr(module, "MemberMapper", None)
        state_type = getattr(module, "MemberPersistenceState", None)
        error_type = getattr(module, "MemberMappingError", None)

        self.assertTrue(inspect.isclass(mapper_type))
        self.assertTrue(inspect.isclass(state_type))
        self.assertTrue(inspect.isclass(error_type))
        self.assertTrue(issubclass(error_type, ValueError))
        self.assertFalse(inspect.iscoroutinefunction(mapper_type.to_domain))
        self.assertFalse(
            inspect.iscoroutinefunction(mapper_type.to_persistence)
        )

        mapper = mapper_type()
        member = _member()

        with self.subTest(scenario="MAP-D2P/VERSION-001"):
            state = mapper.to_persistence(member, 7)

            self.assertIs(type(state), state_type)
            self.assertIsNot(state, member)
            self.assertIs(type(state.member_id), uuid.UUID)
            self.assertIs(state.member_id, member.member_id)
            self.assertEqual(state.member_id.version, 7)
            self.assertEqual(state.member_id.variant, uuid.RFC_4122)
            self.assertEqual(state.member_no, member.member_no.value)
            self.assertEqual(
                state.creation_source, member.creation_source.value
            )
            self.assertEqual(state.status, member.status.value)
            self.assertEqual(state.version, 7)

        with self.subTest(scenario="OPTIONAL-001"):
            state = mapper.to_persistence(member, None)
            self.assertIsNone(state.version)

        with self.subTest(scenario="MAP-P2D/MEMBER-NO/ENUM-001/ENUM-002"):
            state = state_type(
                member_id=VALID_MEMBER_ID,
                member_no="M-P2-MAPPER-0001",
                creation_source="registration",
                status="created",
                version=3,
            )
            restored = mapper.to_domain(state)

            self.assertIs(type(restored), Member)
            self.assertIsNot(restored, state)
            self.assertIs(type(restored.member_id), uuid.UUID)
            self.assertIs(restored.member_id, state.member_id)
            self.assertEqual(restored.member_id.version, 7)
            self.assertEqual(restored.member_id.variant, uuid.RFC_4122)
            self.assertIs(type(restored.member_no), MemberNo)
            self.assertEqual(restored.member_no.value, state.member_no)
            self.assertIs(
                restored.creation_source, CreationSource.REGISTRATION
            )
            self.assertIs(restored.status, MemberStatus.CREATED)
            self.assertFalse(hasattr(restored, "version"))

        with self.subTest(scenario="VERSION-002"):
            invalid_versions = (True, -1, "1", 1.0, object())
            for invalid_version in invalid_versions:
                with self.assertRaises(error_type) as caught:
                    mapper.to_persistence(member, invalid_version)
                self.assertEqual(
                    str(caught.exception),
                    "member persistence version is invalid",
                )

        with self.subTest(scenario="ERROR-001/domain-input"):
            with self.assertRaises(error_type) as caught:
                mapper.to_persistence(object(), 1)
            self.assertEqual(
                str(caught.exception), "member mapping input is invalid"
            )

        with self.subTest(scenario="ERROR-001/state-input"):
            with self.assertRaises(error_type) as caught:
                mapper.to_domain(object())
            self.assertEqual(
                str(caught.exception),
                "member persistence state is invalid",
            )

        with self.subTest(scenario="UUID-002"):
            invalid_ids = (uuid.uuid4(), str(VALID_MEMBER_ID), None)
            for invalid_id in invalid_ids:
                state = state_type(
                    member_id=invalid_id,
                    member_no="M-P2-MAPPER-0001",
                    creation_source="registration",
                    status="created",
                    version=1,
                )
                with self.assertRaises(InvalidMemberIdError):
                    mapper.to_domain(state)

        with self.subTest(scenario="MEMBER-NO/invalid"):
            state = state_type(
                member_id=VALID_MEMBER_ID,
                member_no=" ",
                creation_source="registration",
                status="created",
                version=1,
            )
            with self.assertRaises(InvalidMemberNoError):
                mapper.to_domain(state)

        with self.subTest(scenario="ENUM-001/invalid"):
            state = state_type(
                member_id=VALID_MEMBER_ID,
                member_no="M-P2-MAPPER-0001",
                creation_source="unsupported",
                status="created",
                version=1,
            )
            with self.assertRaises(InvalidCreationSourceError):
                mapper.to_domain(state)

        with self.subTest(scenario="ENUM-002/invalid"):
            state = state_type(
                member_id=VALID_MEMBER_ID,
                member_no="M-P2-MAPPER-0001",
                creation_source="registration",
                status="unsupported",
                version=1,
            )
            with self.assertRaises(InvalidMemberStatusError):
                mapper.to_domain(state)

        with self.subTest(scenario="VERSION-002/state"):
            state = state_type(
                member_id=VALID_MEMBER_ID,
                member_no="M-P2-MAPPER-0001",
                creation_source="registration",
                status="created",
                version=-1,
            )
            with self.assertRaises(error_type) as caught:
                mapper.to_domain(state)
            self.assertEqual(
                str(caught.exception),
                "member persistence version is invalid",
            )

        with self.subTest(scenario="IDENTITY-001/LEAK-001/PURITY-001"):
            state = mapper.to_persistence(member, 4)
            restored = mapper.to_domain(state)
            forbidden_attributes = {
                "connection",
                "engine",
                "result",
                "row",
                "session",
                "transaction",
                "version",
            }

            self.assertIsNot(state, member)
            self.assertIsNot(restored, state)
            self.assertTrue(forbidden_attributes.isdisjoint(dir(restored)))
            self.assertFalse(hasattr(mapper, "session"))
            self.assertFalse(hasattr(mapper, "engine"))
