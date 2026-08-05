from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.core.database import Base


class _StandardLibraryUuid(TypeDecorator[UUID]):
    impl = PostgreSQLUUID
    cache_ok = True

    @property
    def python_type(self) -> type[UUID]:
        return UUID

    def load_dialect_impl(self, dialect: Dialect):
        return dialect.type_descriptor(PostgreSQLUUID(as_uuid=True))

    def process_result_value(
        self, value: object, dialect: Dialect
    ) -> UUID | None:
        if value is None:
            return None
        if type(value) is UUID:
            return value
        if isinstance(value, UUID):
            return UUID(int=value.int)
        raise ValueError("member id database value is invalid")


class MemberOrmModel(Base):
    __tablename__ = "member"
    __table_args__ = (
        UniqueConstraint("member_no"),
        {"schema": "identity"},
    )

    member_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(),
        primary_key=True,
        nullable=False,
    )
    member_no: Mapped[str] = mapped_column(String(64), nullable=False)
    creation_source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
