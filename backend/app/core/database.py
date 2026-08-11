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
    from sqlalchemy.engine import make_url
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
_VERIFICATION_WRITER_ASYNC_ENGINE = None
_VERIFICATION_WRITER_SESSION_FACTORY = None
_HEALTH_FACT_WRITER_ASYNC_ENGINE = None
_HEALTH_FACT_WRITER_SESSION_FACTORY = None
_ORGANIZATION_MAPPING_WRITER_ASYNC_ENGINE = None
_ORGANIZATION_MAPPING_WRITER_SESSION_FACTORY = None
_HEALTH_MAPPING_WRITER_ASYNC_ENGINE = None
_HEALTH_MAPPING_WRITER_SESSION_FACTORY = None
_VERIFICATION_WRITER_RUNTIME_ERROR = (
    "Verification writer database runtime is unavailable"
)
_HEALTH_FACT_WRITER_RUNTIME_ERROR = "Health fact writer database runtime is unavailable"
_ORGANIZATION_MAPPING_WRITER_RUNTIME_ERROR = "Organization mapping writer database runtime is unavailable"
_HEALTH_MAPPING_WRITER_RUNTIME_ERROR = "Health mapping writer database runtime is unavailable"


def get_session_factory():
    global _ASYNC_ENGINE, _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _ASYNC_ENGINE = create_async_engine_from_settings(get_settings())
        _SESSION_FACTORY = create_session_factory(_ASYNC_ENGINE)
    return _SESSION_FACTORY


def _get_verification_writer_database_url(settings: Settings) -> str:
    raw_url = settings.verification_writer_database_url
    valid = False
    try:
        url = make_url(raw_url) if raw_url else None
        valid = bool(
            url is not None
            and url.drivername == "postgresql+asyncpg"
            and url.username
            and url.password
            and url.username not in {settings.database_user, "postgres"}
            and url.host == settings.database_host
            and url.port == settings.database_port
            and url.database == settings.database_name
        )
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(_VERIFICATION_WRITER_RUNTIME_ERROR) from None
    return raw_url


def get_verification_writer_session_factory():
    global _VERIFICATION_WRITER_ASYNC_ENGINE
    global _VERIFICATION_WRITER_SESSION_FACTORY
    if _VERIFICATION_WRITER_SESSION_FACTORY is None:
        settings = get_settings()
        url = _get_verification_writer_database_url(settings)
        try:
            engine = create_async_engine(url, pool_pre_ping=True)
            session_factory = create_session_factory(engine)
        except Exception:
            raise RuntimeError(_VERIFICATION_WRITER_RUNTIME_ERROR) from None
        _VERIFICATION_WRITER_ASYNC_ENGINE = engine
        _VERIFICATION_WRITER_SESSION_FACTORY = session_factory
    return _VERIFICATION_WRITER_SESSION_FACTORY


def _get_health_fact_writer_database_url(settings: Settings) -> str:
    raw_url = settings.health_fact_writer_database_url
    valid = False
    try:
        url = make_url(raw_url) if raw_url else None
        verification_url = (
            make_url(settings.verification_writer_database_url)
            if settings.verification_writer_database_url
            else None
        )
        forbidden_users = {settings.database_user, "postgres"}
        if verification_url is not None and verification_url.username:
            forbidden_users.add(verification_url.username)
        valid = bool(
            url is not None
            and url.drivername == "postgresql+asyncpg"
            and url.username
            and url.password
            and url.username not in forbidden_users
            and url.host == settings.database_host
            and url.port == settings.database_port
            and url.database == settings.database_name
        )
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(_HEALTH_FACT_WRITER_RUNTIME_ERROR) from None
    return raw_url


def get_health_fact_writer_session_factory():
    global _HEALTH_FACT_WRITER_ASYNC_ENGINE
    global _HEALTH_FACT_WRITER_SESSION_FACTORY
    if _HEALTH_FACT_WRITER_SESSION_FACTORY is None:
        settings = get_settings()
        url = _get_health_fact_writer_database_url(settings)
        try:
            engine = create_async_engine(url, pool_pre_ping=True)
            session_factory = create_session_factory(engine)
        except Exception:
            raise RuntimeError(_HEALTH_FACT_WRITER_RUNTIME_ERROR) from None
        _HEALTH_FACT_WRITER_ASYNC_ENGINE = engine
        _HEALTH_FACT_WRITER_SESSION_FACTORY = session_factory
    return _HEALTH_FACT_WRITER_SESSION_FACTORY


def _get_mapping_writer_database_url(settings: Settings, *, kind: str) -> str:
    raw_url = (
        settings.organization_mapping_writer_database_url
        if kind == "organization"
        else settings.health_mapping_writer_database_url
    )
    error = (
        _ORGANIZATION_MAPPING_WRITER_RUNTIME_ERROR
        if kind == "organization"
        else _HEALTH_MAPPING_WRITER_RUNTIME_ERROR
    )
    try:
        if (
            settings.organization_mapping_writer_database_url
            and settings.organization_mapping_writer_database_url
            == settings.health_mapping_writer_database_url
        ):
            raise ValueError
        url = make_url(raw_url) if raw_url else None
        other_urls = (
            settings.verification_writer_database_url,
            settings.health_fact_writer_database_url,
            settings.organization_mapping_writer_database_url,
            settings.health_mapping_writer_database_url,
        )
        forbidden_users = {settings.database_user, "postgres"}
        for candidate in other_urls:
            if candidate and candidate != raw_url:
                parsed = make_url(candidate)
                if parsed.username:
                    forbidden_users.add(parsed.username)
        valid = bool(
            url is not None
            and url.drivername == "postgresql+asyncpg"
            and url.username
            and url.password
            and url.username not in forbidden_users
            and url.host == settings.database_host
            and url.port == settings.database_port
            and url.database == settings.database_name
        )
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(error) from None
    return raw_url


def get_organization_mapping_writer_session_factory():
    global _ORGANIZATION_MAPPING_WRITER_ASYNC_ENGINE
    global _ORGANIZATION_MAPPING_WRITER_SESSION_FACTORY
    if _ORGANIZATION_MAPPING_WRITER_SESSION_FACTORY is None:
        url = _get_mapping_writer_database_url(get_settings(), kind="organization")
        try:
            engine = create_async_engine(url, pool_pre_ping=True)
            factory = create_session_factory(engine)
        except Exception:
            raise RuntimeError(_ORGANIZATION_MAPPING_WRITER_RUNTIME_ERROR) from None
        _ORGANIZATION_MAPPING_WRITER_ASYNC_ENGINE = engine
        _ORGANIZATION_MAPPING_WRITER_SESSION_FACTORY = factory
    return _ORGANIZATION_MAPPING_WRITER_SESSION_FACTORY


def get_health_mapping_writer_session_factory():
    global _HEALTH_MAPPING_WRITER_ASYNC_ENGINE
    global _HEALTH_MAPPING_WRITER_SESSION_FACTORY
    if _HEALTH_MAPPING_WRITER_SESSION_FACTORY is None:
        url = _get_mapping_writer_database_url(get_settings(), kind="health")
        try:
            engine = create_async_engine(url, pool_pre_ping=True)
            factory = create_session_factory(engine)
        except Exception:
            raise RuntimeError(_HEALTH_MAPPING_WRITER_RUNTIME_ERROR) from None
        _HEALTH_MAPPING_WRITER_ASYNC_ENGINE = engine
        _HEALTH_MAPPING_WRITER_SESSION_FACTORY = factory
    return _HEALTH_MAPPING_WRITER_SESSION_FACTORY


async def dispose_verification_writer_runtime() -> None:
    global _VERIFICATION_WRITER_ASYNC_ENGINE
    global _VERIFICATION_WRITER_SESSION_FACTORY
    engine = _VERIFICATION_WRITER_ASYNC_ENGINE
    _VERIFICATION_WRITER_ASYNC_ENGINE = None
    _VERIFICATION_WRITER_SESSION_FACTORY = None
    if engine is not None:
        await engine.dispose()


async def dispose_health_fact_writer_runtime() -> None:
    global _HEALTH_FACT_WRITER_ASYNC_ENGINE
    global _HEALTH_FACT_WRITER_SESSION_FACTORY
    engine = _HEALTH_FACT_WRITER_ASYNC_ENGINE
    _HEALTH_FACT_WRITER_ASYNC_ENGINE = None
    _HEALTH_FACT_WRITER_SESSION_FACTORY = None
    if engine is not None:
        await engine.dispose()


async def dispose_mapping_writer_runtimes() -> None:
    global _ORGANIZATION_MAPPING_WRITER_ASYNC_ENGINE
    global _ORGANIZATION_MAPPING_WRITER_SESSION_FACTORY
    global _HEALTH_MAPPING_WRITER_ASYNC_ENGINE
    global _HEALTH_MAPPING_WRITER_SESSION_FACTORY
    engines = (
        _ORGANIZATION_MAPPING_WRITER_ASYNC_ENGINE,
        _HEALTH_MAPPING_WRITER_ASYNC_ENGINE,
    )
    _ORGANIZATION_MAPPING_WRITER_ASYNC_ENGINE = None
    _ORGANIZATION_MAPPING_WRITER_SESSION_FACTORY = None
    _HEALTH_MAPPING_WRITER_ASYNC_ENGINE = None
    _HEALTH_MAPPING_WRITER_SESSION_FACTORY = None
    for engine in engines:
        if engine is not None:
            await engine.dispose()


async def dispose_database_runtimes() -> None:
    global _ASYNC_ENGINE
    global _SESSION_FACTORY
    engine = _ASYNC_ENGINE
    _ASYNC_ENGINE = None
    _SESSION_FACTORY = None
    try:
        if engine is not None:
            await engine.dispose()
    finally:
        try:
            await dispose_verification_writer_runtime()
        finally:
            try:
                await dispose_health_fact_writer_runtime()
            finally:
                await dispose_mapping_writer_runtimes()


async def get_db_session():
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
