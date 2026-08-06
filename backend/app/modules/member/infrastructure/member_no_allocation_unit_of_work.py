from typing import NoReturn

from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from ..application.member_no_allocator import (
    MemberNoAllocationCommitOutcomeUnknownError,
    MemberNoAllocationTransactionError,
    MemberNoAllocationTransactionUnavailableError,
)


_UNAVAILABLE_ERRORS = (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    SqlAlchemyTimeoutError,
)


class SqlAlchemyMemberNoAllocationUnitOfWork:
    def __init__(self, session_factory, ledger_factory) -> None:
        self._session_factory = session_factory
        self._ledger_factory = ledger_factory
        self._session = None
        self._allocations = None
        self._entered = False
        self._finalized = False
        self._exited = False

    @property
    def allocations(self):
        self._require_active()
        return self._allocations

    async def __aenter__(self):
        if self._entered or self._exited:
            self._raise_state_error()
        self._entered = True
        try:
            self._session = self._session_factory()
            await self._session.begin()
            self._allocations = self._ledger_factory(self._session)
        except BaseException as exc:
            await self._cleanup_enter_failure()
            self._exited = True
            if not isinstance(exc, Exception):
                raise
            self._raise_transaction_error(exc)
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self._require_active()
        cleanup_failure = None
        if not self._finalized:
            try:
                await self._session.rollback()
            except BaseException as rollback_failure:
                cleanup_failure = rollback_failure
        try:
            await self._session.close()
        except BaseException as close_failure:
            if cleanup_failure is None or (
                isinstance(cleanup_failure, Exception)
                and not isinstance(close_failure, Exception)
            ):
                cleanup_failure = close_failure
        finally:
            self._finalized = True
            self._exited = True
            self._session = None
            self._allocations = None
        if exc is not None:
            if (
                isinstance(exc, Exception)
                and cleanup_failure is not None
                and not isinstance(cleanup_failure, Exception)
            ):
                raise cleanup_failure
            return False
        if cleanup_failure is not None:
            if not isinstance(cleanup_failure, Exception):
                raise cleanup_failure
            self._raise_transaction_error(cleanup_failure)
        return False

    async def commit(self) -> None:
        self._require_active_transaction()
        try:
            await self._session.commit()
        except Exception as exc:
            if self._is_unavailable(exc):
                raise MemberNoAllocationCommitOutcomeUnknownError(
                    "member number allocation commit outcome is unknown"
                ) from exc
            raise MemberNoAllocationTransactionError(
                "member number allocation transaction operation failed"
            ) from exc
        self._finalized = True

    async def rollback(self) -> None:
        self._require_active_transaction()
        try:
            await self._session.rollback()
        except Exception as exc:
            self._raise_transaction_error(exc)
        self._finalized = True

    async def _cleanup_enter_failure(self) -> None:
        if self._session is None:
            return
        cleanup_failure = None
        try:
            await self._session.rollback()
        except BaseException as rollback_failure:
            cleanup_failure = rollback_failure
        try:
            await self._session.close()
        except BaseException as close_failure:
            if cleanup_failure is None or (
                isinstance(cleanup_failure, Exception)
                and not isinstance(close_failure, Exception)
            ):
                cleanup_failure = close_failure
        finally:
            self._session = None
            self._allocations = None
        if cleanup_failure is not None and not isinstance(
            cleanup_failure, Exception
        ):
            raise cleanup_failure

    def _require_active(self) -> None:
        if not self._entered or self._exited or self._session is None:
            self._raise_state_error()

    def _require_active_transaction(self) -> None:
        self._require_active()
        if self._finalized:
            self._raise_state_error()

    @staticmethod
    def _raise_state_error() -> NoReturn:
        raise MemberNoAllocationTransactionError(
            "member number allocation transaction state is invalid"
        )

    @staticmethod
    def _raise_transaction_error(exc: Exception) -> NoReturn:
        if SqlAlchemyMemberNoAllocationUnitOfWork._is_unavailable(exc):
            error = MemberNoAllocationTransactionUnavailableError(
                "member number allocation transaction is unavailable"
            )
        else:
            error = MemberNoAllocationTransactionError(
                "member number allocation transaction operation failed"
            )
        raise error from exc

    @staticmethod
    def _is_unavailable(exc: Exception) -> bool:
        return isinstance(exc, _UNAVAILABLE_ERRORS) or (
            isinstance(exc, DBAPIError) and exc.connection_invalidated
        )
