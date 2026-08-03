from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.member.infrastructure.mapper import MemberMapper
from app.modules.member.infrastructure.orm_state_mapper import MemberOrmStateMapper
from app.modules.member.infrastructure.sqlalchemy_repository import (
    SqlAlchemyMemberRepository,
)
from app.modules.member.infrastructure.unit_of_work import (
    SqlAlchemyIdentityUnitOfWork,
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
        )

    def _member_repository(self, session) -> SqlAlchemyMemberRepository:
        return SqlAlchemyMemberRepository(
            session=session,
            mapper=self._member_mapper,
            orm_mapper=self._orm_mapper,
            clock=self._clock,
        )
