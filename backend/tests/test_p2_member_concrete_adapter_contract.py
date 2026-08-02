import importlib
import inspect

import pytest

from app.modules.member.repository import MemberRepository


ADAPTER_MODULE = "app.modules.member.infrastructure.sqlalchemy_repository"
ADAPTER_CLASS = "SqlAlchemyMemberRepository"
EXPECTED_RED = "Concrete MemberRepository Adapter is not implemented"
OPERATIONS = (
    "get_by_id",
    "get_by_member_no",
    "is_member_no_available",
    "add",
    "save",
)


def _load_adapter_module():
    try:
        return importlib.import_module(ADAPTER_MODULE)
    except ModuleNotFoundError as exc:
        target_is_missing = (
            exc.name == ADAPTER_MODULE
            or ADAPTER_MODULE.startswith(f"{exc.name}.")
        )
        if target_is_missing:
            pytest.fail(EXPECTED_RED)
        raise


def test_ct1_concrete_adapter_preserves_async_repository_contract():
    module = _load_adapter_module()
    adapter = getattr(module, ADAPTER_CLASS, None)

    assert inspect.isclass(adapter)
    assert adapter is not MemberRepository
    assert all(
        inspect.iscoroutinefunction(getattr(adapter, operation, None))
        for operation in OPERATIONS
    )
    assert {
        f"{operation}_async" for operation in OPERATIONS
    }.isdisjoint(vars(adapter))
