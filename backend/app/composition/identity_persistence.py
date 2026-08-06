from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.uuid_generator import UuidGenerator
from app.modules.member.application.create_registration_member import (
    CreateRegistrationMemberService,
)
from app.modules.member.infrastructure.mapper import MemberMapper
from app.modules.member.infrastructure.orm_state_mapper import MemberOrmStateMapper
from app.modules.member.infrastructure.sqlalchemy_repository import (
    SqlAlchemyMemberRepository,
)
from app.modules.member.infrastructure.unit_of_work import (
    SqlAlchemyIdentityUnitOfWork,
)


_UNAVAILABLE_ERRORS = (
    OperationalError,
    InterfaceError,
    SqlAlchemyTimeoutError,
    DisconnectionError,
)


def _is_identity_persistence_unavailable(exc: Exception) -> bool:
    return isinstance(exc, _UNAVAILABLE_ERRORS) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    )


def create_identity_session_factory(engine):
    """Create the dedicated P2 Identity AsyncSession factory."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


class IdentityPersistenceComposition:
    """Compose Identity persistence collaborators without owning an engine."""

    def __init__(self, session_factory, clock) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._member_mapper = MemberMapper()
        self._orm_mapper = MemberOrmStateMapper()

    def unit_of_work(self) -> SqlAlchemyIdentityUnitOfWork:
        return SqlAlchemyIdentityUnitOfWork(
            session_factory=self._session_factory,
            repository_factory=self._member_repository,
            unavailable_classifier=_is_identity_persistence_unavailable,
        )

    def _member_repository(self, session) -> SqlAlchemyMemberRepository:
        return SqlAlchemyMemberRepository(
            session=session,
            mapper=self._member_mapper,
            orm_mapper=self._orm_mapper,
            clock=self._clock,
        )


def create_registration_member_service(
    *,
    identity_persistence: IdentityPersistenceComposition,
    uuid_generator: UuidGenerator,
) -> CreateRegistrationMemberService:
    return CreateRegistrationMemberService(
        unit_of_work_factory=identity_persistence.unit_of_work,
        uuid_generator=uuid_generator,
    )
