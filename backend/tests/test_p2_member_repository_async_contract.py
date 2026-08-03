import inspect
from typing import get_type_hints
from uuid import UUID

from app.modules.member import Member, MemberNo
from app.modules.member import repository
from app.modules.member.infrastructure.sqlalchemy_repository import (
    SqlAlchemyMemberRepository,
)


OPERATIONS = (
    "get_by_id",
    "get_by_member_no",
    "is_member_no_available",
    "add",
    "save",
)


def test_member_repository_io_operations_are_async():
    contract = repository.MemberRepository
    sync_operations = [
        operation
        for operation in OPERATIONS
        if not inspect.iscoroutinefunction(getattr(contract, operation))
    ]

    assert not sync_operations, (
        "MemberRepository five I/O operations are still sync def: "
        + ", ".join(sync_operations)
    )


def test_concrete_repository_satisfies_public_protocol():
    contract = repository.MemberRepository
    adapter = SqlAlchemyMemberRepository(
        session=object(),
        mapper=object(),
        orm_mapper=object(),
        clock=object(),
    )

    assert getattr(contract, "_is_runtime_protocol", False)
    assert isinstance(adapter, contract)


def test_member_repository_annotations_describe_awaited_results():
    contract = repository.MemberRepository
    expected_returns = {
        "get_by_id": Member,
        "get_by_member_no": Member,
        "is_member_no_available": bool,
        "add": type(None),
        "save": type(None),
    }

    for operation, expected_return in expected_returns.items():
        hints = get_type_hints(getattr(contract, operation))
        assert hints["return"] is expected_return
        assert all(
            marker not in repr(hints["return"])
            for marker in ("Awaitable", "Coroutine", "Future")
        )


def test_member_repository_async_contract_preserves_parameter_types():
    contract = repository.MemberRepository
    expected_parameters = {
        "get_by_id": {"member_id": UUID},
        "get_by_member_no": {"member_no": MemberNo},
        "is_member_no_available": {"member_no": MemberNo},
        "add": {"member": Member},
        "save": {"member": Member},
    }

    for operation, expected in expected_parameters.items():
        method = getattr(contract, operation)
        hints = get_type_hints(method)
        for parameter, expected_type in expected.items():
            assert hints[parameter] is expected_type

    save_signature = inspect.signature(contract.save)
    assert "expected_version" in save_signature.parameters
    assert (
        save_signature.parameters["expected_version"].annotation
        is not inspect.Signature.empty
    )


def test_member_repository_async_contract_preserves_error_contract():
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


def test_member_repository_async_contract_has_no_dual_mode_or_sync_bridge():
    contract = repository.MemberRepository
    forbidden_operations = {
        "add_async",
        "get_by_id_async",
        "get_by_member_no_async",
        "is_member_no_available_async",
        "save_async",
    }
    forbidden_source_markers = {
        "asyncio.run",
        "run_in_executor",
        "run_until_complete",
        "to_thread",
    }

    assert forbidden_operations.isdisjoint(vars(contract))
    source = inspect.getsource(repository)
    assert all(marker not in source for marker in forbidden_source_markers)
