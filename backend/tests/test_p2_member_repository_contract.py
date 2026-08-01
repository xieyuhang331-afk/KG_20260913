import importlib
import inspect
from typing import get_type_hints
from uuid import UUID

import pytest

from app.modules.member import Member, MemberNo


def _load_repository_contract():
    try:
        return importlib.import_module("app.modules.member.repository")
    except ModuleNotFoundError as exc:
        if exc.name == "app.modules.member.repository":
            pytest.fail(
                "MemberRepository contract is not implemented; "
                "this is the expected R0 RED"
            )
        raise


def test_member_repository_contract_defines_foundation_responsibilities():
    repository = _load_repository_contract()
    contract = getattr(repository, "MemberRepository", None)

    assert inspect.isclass(contract)

    operations = {
        "get_by_id": ("member_id", UUID),
        "get_by_member_no": ("member_no", MemberNo),
        "is_member_no_available": ("member_no", MemberNo),
        "add": ("member", Member),
        "save": ("member", Member),
    }
    for operation, (parameter, expected_type) in operations.items():
        method = getattr(contract, operation, None)
        assert callable(method), f"MemberRepository must define {operation}"
        signature = inspect.signature(method)
        assert parameter in signature.parameters
        assert get_type_hints(method)[parameter] is expected_type

    save_signature = inspect.signature(contract.save)
    assert "expected_version" in save_signature.parameters
    assert save_signature.parameters["expected_version"].annotation is not inspect.Signature.empty

    forbidden_operations = {
        "delete",
        "generate_id",
        "generate_uuid",
        "save_claim",
        "save_legacy_mapping",
        "save_self_member_link",
        "upsert",
    }
    assert forbidden_operations.isdisjoint(vars(contract))

    error_names = {
        "MemberNotFoundError",
        "MemberPersistenceUnavailableError",
        "MemberRepositoryError",
        "MemberUniquenessConflictError",
        "MemberVersionConflictError",
    }
    errors = {name: getattr(repository, name, None) for name in error_names}
    assert all(inspect.isclass(error) for error in errors.values())
    assert all(issubclass(error, Exception) for error in errors.values())
    assert len(set(errors.values())) == len(errors)
