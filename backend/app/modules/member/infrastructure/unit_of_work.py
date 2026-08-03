from typing import NoReturn

from ..application.unit_of_work import (
    IdentityUnitOfWorkError,
    IdentityUnitOfWorkStateError,
    IdentityUnitOfWorkUnavailableError,
)


class SqlAlchemyIdentityUnitOfWork:
    def __init__(self, session_factory, repository_factory) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._session = None
        self._members = None
        self._entered = False
        self._finalized = False
        self._exited = False

    @property
    def members(self):
        self._require_active()
        return self._members

    async def __aenter__(self):
        if self._entered or self._exited:
            self._raise_state_error()
        self._entered = True

        try:
            self._session = self._session_factory()
            await self._session.begin()
            self._members = self._repository_factory(self._session)
        except BaseException as exc:
            await self._cleanup_enter_failure()
            self._exited = True
            if not isinstance(exc, Exception):
                raise
            self._raise_translated(exc)

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
            if cleanup_failure is None:
                cleanup_failure = close_failure
        finally:
            self._finalized = True
            self._exited = True
            self._session = None
            self._members = None

        if exc is not None:
            return False
        if cleanup_failure is not None:
            if not isinstance(cleanup_failure, Exception):
                raise cleanup_failure
            self._raise_translated(cleanup_failure)
        return False

    async def commit(self) -> None:
        self._require_active_transaction()
        try:
            await self._session.commit()
        except Exception as exc:
            self._raise_translated(exc)
        self._finalized = True

    async def rollback(self) -> None:
        self._require_active_transaction()
        try:
            await self._session.rollback()
        except Exception as exc:
            self._raise_translated(exc)
        self._finalized = True

    async def _cleanup_enter_failure(self) -> None:
        if self._session is None:
            return
        try:
            await self._session.rollback()
        except BaseException:
            pass
        try:
            await self._session.close()
        except BaseException:
            pass
        finally:
            self._session = None
            self._members = None

    def _require_active(self) -> None:
        if not self._entered or self._exited or self._session is None:
            self._raise_state_error()

    def _require_active_transaction(self) -> None:
        self._require_active()
        if self._finalized:
            self._raise_state_error()

    @staticmethod
    def _raise_state_error() -> NoReturn:
        raise IdentityUnitOfWorkStateError(
            "identity transaction state is invalid"
        )

    @staticmethod
    def _raise_translated(exc: Exception) -> NoReturn:
        if getattr(exc, "category", None) == "unavailable":
            error = IdentityUnitOfWorkUnavailableError(
                "identity persistence is unavailable"
            )
        else:
            error = IdentityUnitOfWorkError(
                "identity transaction operation failed"
            )
        raise error from exc
