import ast
import importlib
import uuid
from pathlib import Path

import pytest

from app.core.uuid_generator import Uuid7Generator


def _member_contract():
    try:
        entities = importlib.import_module("app.modules.member.entities")
        value_objects = importlib.import_module("app.modules.member.value_objects")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Member Domain is not implemented: {exc}")

    return (
        entities.Member,
        value_objects.CreationSource,
        value_objects.MemberNo,
        value_objects.MemberStatus,
    )


def _create_member(member_id=None):
    Member, CreationSource, MemberNo, _ = _member_contract()
    return Member.create(
        member_id=member_id or Uuid7Generator().generate(),
        member_no=MemberNo("M-P2-0001"),
        creation_source=CreationSource.REGISTRATION,
    )


def test_member_identity_accepts_only_uuid7():
    member = _create_member()

    assert member.member_id.version == 7
    with pytest.raises((TypeError, ValueError)):
        _create_member(uuid.uuid4())
    with pytest.raises((TypeError, ValueError)):
        _create_member(str(Uuid7Generator().generate()))


def test_member_identity_is_exact_standard_uuid():
    member = _create_member()

    assert type(member.member_id) is uuid.UUID


def test_member_creation_requires_only_foundation_inputs():
    _, _, _, MemberStatus = _member_contract()
    member = _create_member()

    assert member.status is MemberStatus.CREATED
    assert not hasattr(member, "user_id")
    assert not hasattr(member, "tenant_id")
    assert not hasattr(member, "store_id")
    assert not hasattr(member, "institution_id")


def test_member_number_value_object_is_non_blank_and_value_based():
    _, _, MemberNo, _ = _member_contract()

    assert MemberNo("M-P2-0001") == MemberNo("M-P2-0001")
    assert hash(MemberNo("M-P2-0001")) == hash(MemberNo("M-P2-0001"))
    assert MemberNo("M-P2-0001").value == "M-P2-0001"
    with pytest.raises(ValueError):
        MemberNo("")
    with pytest.raises(ValueError):
        MemberNo("   ")


def test_member_status_rejects_unknown_values():
    _, _, _, MemberStatus = _member_contract()

    with pytest.raises(ValueError):
        MemberStatus("unknown")


def test_member_domain_does_not_define_blocked_decision_objects():
    member_dir = Path(__file__).resolve().parents[1] / "app" / "modules" / "member"
    forbidden = {
        "Delegation",
        "FamilyRelationship",
        "HealthDataAuthorization",
        "HealthFactCorrection",
        "ServiceRelationship",
        "Supersession",
        "TherapistAssignment",
        "Withdrawal",
    }
    declared = set()

    for path in member_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declared.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        )

    assert forbidden.isdisjoint(declared)


def test_member_domain_contains_no_persistence_logic():
    member_dir = Path(__file__).resolve().parents[1] / "app" / "modules" / "member"
    forbidden_modules = {"alembic", "asyncpg", "sqlalchemy"}
    forbidden_methods = {"commit", "delete", "execute", "flush", "save"}

    for path in member_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = {alias.name.split(".", 1)[0] for alias in node.names}
                assert forbidden_modules.isdisjoint(imports)
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".", 1)[0] not in forbidden_modules
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in forbidden_methods
