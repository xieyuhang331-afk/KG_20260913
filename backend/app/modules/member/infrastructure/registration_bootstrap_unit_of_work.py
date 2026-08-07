from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from ..application.registration_bootstrap_ports import (
    RegistrationBootstrapCommitOutcomeUnknown,
    RegistrationBootstrapPersistenceError,
    RegistrationBootstrapUnavailable,
)
from .mapper import MemberMapper
from .orm_state_mapper import MemberOrmStateMapper
from .sqlalchemy_registration_bootstrap_repository import (
    SqlAlchemyRegistrationBootstrapRecordStore,
    SqlAlchemyRegistrationMemberStore,
    SqlAlchemyUserMemberSelfLinkStore,
)
from .sqlalchemy_repository import SqlAlchemyMemberRepository


_UNAVAILABLE_ERRORS = (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    SqlAlchemyTimeoutError,
)


class SqlAlchemyRegistrationBootstrapUnitOfWork:
    def __init__(self, session_factory, clock) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._session = None
        self.members = None
        self.self_links = None
        self.bootstrap_records = None
        self._finalized = False

    async def __aenter__(self):
        try:
            self._session = self._session_factory()
            await self._session.begin()
            member_repository = SqlAlchemyMemberRepository(
                session=self._session,
                mapper=MemberMapper(),
                orm_mapper=MemberOrmStateMapper(),
                clock=self._clock,
            )
            self.members = SqlAlchemyRegistrationMemberStore(member_repository)
            self.self_links = SqlAlchemyUserMemberSelfLinkStore(
                self._session, self._clock
            )
            self.bootstrap_records = SqlAlchemyRegistrationBootstrapRecordStore(
                self._session, self._clock
            )
            return self
        except BaseException as exc:
            await self._cleanup()
            if not isinstance(exc, Exception):
                raise
            self._raise_transaction_error(exc)

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        cleanup_error = None
        if self._session is not None and not self._finalized:
            try:
                await self._session.rollback()
            except BaseException as caught:
                cleanup_error = caught
        if self._session is not None:
            try:
                await self._session.close()
            except BaseException as caught:
                if cleanup_error is None:
                    cleanup_error = caught
        self._clear()
        if exc is not None:
            if cleanup_error is not None and not isinstance(
                cleanup_error, Exception
            ):
                raise cleanup_error
            return False
        if cleanup_error is not None:
            if not isinstance(cleanup_error, Exception):
                raise cleanup_error
            self._raise_transaction_error(cleanup_error)
        return False

    async def commit(self) -> None:
        if self._session is None or self._finalized:
            raise RegistrationBootstrapPersistenceError(
                "registration bootstrap transaction state is invalid"
            )
        try:
            await self._session.commit()
        except Exception as exc:
            if self._is_unavailable(exc):
                raise RegistrationBootstrapCommitOutcomeUnknown(
                    "registration bootstrap commit outcome is unknown"
                ) from None
            raise RegistrationBootstrapPersistenceError(
                "registration bootstrap transaction operation failed"
            ) from None
        self._finalized = True

    async def _cleanup(self) -> None:
        if self._session is None:
            return
        try:
            await self._session.rollback()
        except BaseException:
            pass
        try:
            await self._session.close()
        finally:
            self._clear()

    def _clear(self) -> None:
        self._session = None
        self.members = None
        self.self_links = None
        self.bootstrap_records = None
        self._finalized = True

    @staticmethod
    def _raise_transaction_error(exc: Exception):
        if SqlAlchemyRegistrationBootstrapUnitOfWork._is_unavailable(exc):
            raise RegistrationBootstrapUnavailable(
                "registration bootstrap transaction is unavailable"
            ) from None
        raise RegistrationBootstrapPersistenceError(
            "registration bootstrap transaction operation failed"
        ) from None

    @staticmethod
    def _is_unavailable(exc: Exception) -> bool:
        return isinstance(exc, _UNAVAILABLE_ERRORS) or (
            isinstance(exc, DBAPIError) and exc.connection_invalidated
        )
