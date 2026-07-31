from urllib.parse import quote_plus

from app.core.config import Settings, get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

try:
    from sqlalchemy import MetaData
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
    from sqlalchemy.orm import DeclarativeBase

    class Base(DeclarativeBase):
        metadata = MetaData(naming_convention=NAMING_CONVENTION)

    SQLALCHEMY_AVAILABLE = True
except ModuleNotFoundError:
    AsyncEngine = object

    class _FallbackMetadata:
        naming_convention = NAMING_CONVENTION

    class Base:
        metadata = _FallbackMetadata()

    SQLALCHEMY_AVAILABLE = False


def build_database_url(settings: Settings) -> str:
    if settings.database_driver != "postgresql+asyncpg":
        raise ValueError("database_driver must be postgresql+asyncpg")
    user = quote_plus(settings.database_user)
    password = quote_plus(settings.database_password)
    host = settings.database_host
    port = settings.database_port
    name = settings.database_name
    return f"{settings.database_driver}://{user}:{password}@{host}:{port}/{name}"


def is_sqlalchemy_available() -> bool:
    return SQLALCHEMY_AVAILABLE


def get_database_metadata():
    return Base.metadata


def create_async_engine_from_settings(settings: Settings) -> AsyncEngine:
    if not SQLALCHEMY_AVAILABLE:
        raise RuntimeError("Install backend dependencies before creating the SQLAlchemy async engine.")
    return create_async_engine(build_database_url(settings), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine):
    if not SQLALCHEMY_AVAILABLE:
        raise RuntimeError("Install backend dependencies before creating the SQLAlchemy session factory.")
    return async_sessionmaker(engine, expire_on_commit=False)


_ASYNC_ENGINE = None
_SESSION_FACTORY = None


def get_session_factory():
    global _ASYNC_ENGINE, _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _ASYNC_ENGINE = create_async_engine_from_settings(get_settings())
        _SESSION_FACTORY = create_session_factory(_ASYNC_ENGINE)
    return _SESSION_FACTORY


async def get_db_session():
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
