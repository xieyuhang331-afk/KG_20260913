import asyncio
import weakref
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
_PROJECTION_RUNTIMES = {}
_PROJECTION_RUNTIME_LOCKS = {}
_PROJECTION_RUNTIME_ERROR = "Projection database runtime is unavailable"
_SLICE1_RUNTIMES = {}
_SLICE1_RUNTIME_ERROR = "Institution onboarding database runtime is unavailable"
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
                try:
                    await dispose_mapping_writer_runtimes()
                finally:
                    for kind in ("organization", "health", "confirmation", "organization_shadow", "health_shadow", "ready_gate", "shadow_confirmation", "organization_reader", "health_reader"):
                        await dispose_projection_runtime(kind)
                    if not invalidate_orphaned_projection_runtimes():
                        raise RuntimeError(_PROJECTION_RUNTIME_ERROR) from None
                    engines = tuple(_SLICE1_RUNTIMES.values())
                    _SLICE1_RUNTIMES.clear()
                    for engine, _ in engines:
                        await engine.dispose()


def _projection_url(settings: Settings, kind: str) -> str:
    raw = {
        "organization": settings.organization_projection_builder_database_url,
        "health": settings.health_projection_builder_database_url,
        "confirmation": settings.projection_confirmation_database_url,
        "organization_shadow": settings.organization_projection_shadow_database_url,
        "health_shadow": settings.health_projection_shadow_database_url,
        "ready_gate": settings.projection_ready_gate_database_url,
        "shadow_confirmation": settings.projection_shadow_confirmation_database_url,
        "organization_reader": settings.organization_projection_reader_database_url,
        "health_reader": settings.health_projection_reader_database_url,
    }[kind]
    try:
        urls = [settings.organization_projection_builder_database_url, settings.health_projection_builder_database_url, settings.projection_confirmation_database_url]
        if kind in {"organization_shadow", "health_shadow", "ready_gate", "shadow_confirmation", "organization_reader", "health_reader"}:
            urls += [settings.organization_projection_shadow_database_url, settings.health_projection_shadow_database_url, settings.projection_ready_gate_database_url, settings.projection_shadow_confirmation_database_url]
        if kind in {"organization_reader", "health_reader"}:
            urls += [settings.organization_projection_reader_database_url, settings.health_projection_reader_database_url]
        parsed_urls = [make_url(value) for value in urls if value]
        parsed = make_url(raw) if raw else None
        users = [value.username for value in parsed_urls]
        targets = {(value.host, value.port, value.database) for value in parsed_urls}
        expected_count = 9 if kind in {"organization_reader", "health_reader"} else (7 if kind in {"organization_shadow", "health_shadow", "ready_gate", "shadow_confirmation"} else 3)
        valid = parsed is not None and parsed.drivername == "postgresql+asyncpg" and parsed.username and parsed.password and len(users) == expected_count and len(set(users)) == expected_count and len(targets) == 1 and next(iter(targets)) == (settings.database_host, settings.database_port, settings.database_name) and parsed.username not in {settings.database_user, "postgres"}
        if kind == "organization_reader":
            valid = valid and parsed.username == settings.organization_projection_reader_role
        elif kind == "health_reader":
            valid = valid and parsed.username == settings.health_projection_reader_role
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(_PROJECTION_RUNTIME_ERROR) from None
    return raw


async def get_projection_session_factory(kind: str):
    if kind not in {"organization", "health", "confirmation", "organization_shadow", "health_shadow", "ready_gate", "shadow_confirmation", "organization_reader", "health_reader"}:
        raise RuntimeError(_PROJECTION_RUNTIME_ERROR) from None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(_PROJECTION_RUNTIME_ERROR) from None
    key = (id(loop), kind)
    lock = _PROJECTION_RUNTIME_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        entry = _PROJECTION_RUNTIMES.get(key)
        if entry is None:
            try:
                engine = create_async_engine(_projection_url(get_settings(), kind), pool_pre_ping=True)
                entry = (weakref.ref(loop), engine, create_session_factory(engine))
            except Exception:
                raise RuntimeError(_PROJECTION_RUNTIME_ERROR) from None
            _PROJECTION_RUNTIMES[key] = entry
        return entry[2]


async def dispose_projection_runtime(kind: str) -> None:
    import asyncio
    loop = asyncio.get_running_loop()
    entry = _PROJECTION_RUNTIMES.pop((id(loop), kind), None)
    _PROJECTION_RUNTIME_LOCKS.pop((id(loop), kind), None)
    if entry is not None:
        await entry[1].dispose()


def invalidate_orphaned_projection_runtimes() -> bool:
    orphaned = []
    for key, (loop_ref, engine, _) in tuple(_PROJECTION_RUNTIMES.items()):
        loop = loop_ref()
        if loop is None or loop.is_closed():
            orphaned.append((key, engine))
    for key, engine in orphaned:
        _PROJECTION_RUNTIMES.pop(key, None)
        _PROJECTION_RUNTIME_LOCKS.pop(key, None)
        engine.sync_engine.dispose(close=False)
    return not orphaned


async def get_db_session():
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


def _slice1_url(settings: Settings, kind: str) -> str:
    urls = {
        "onboarding_writer": settings.institution_onboarding_writer_database_url,
        "review_writer": settings.institution_review_writer_database_url,
        "file_writer": settings.private_file_writer_database_url,
        "reader": settings.institution_onboarding_reader_database_url,
    }
    roles = {
        "onboarding_writer": settings.institution_onboarding_writer_role,
        "review_writer": settings.institution_review_writer_role,
        "file_writer": settings.private_file_writer_role,
        "reader": settings.institution_onboarding_reader_role,
    }
    try:
        parsed = {name: make_url(value) for name, value in urls.items() if value}
        users = [value.username for value in parsed.values()]
        valid = (
            kind in urls and len(parsed) == 4 and len(users) == 4 and len(set(users)) == 4
            and all(value.drivername == "postgresql+asyncpg" and value.username and value.password for value in parsed.values())
            and all((value.host, value.port, value.database) == (settings.database_host, settings.database_port, settings.database_name) for value in parsed.values())
            and all(parsed[name].username == roles[name] for name in urls)
            and settings.database_user not in users and "postgres" not in users
        )
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(_SLICE1_RUNTIME_ERROR) from None
    return urls[kind]  # type: ignore[return-value]


def get_slice1_session_factory(kind: str):
    if kind not in {"onboarding_writer", "review_writer", "file_writer", "reader"}:
        raise RuntimeError(_SLICE1_RUNTIME_ERROR) from None
    entry = _SLICE1_RUNTIMES.get(kind)
    if entry is None:
        try:
            engine = create_async_engine(_slice1_url(get_settings(), kind), pool_pre_ping=True)
            entry = (engine, create_session_factory(engine))
        except Exception:
            raise RuntimeError(_SLICE1_RUNTIME_ERROR) from None
        _SLICE1_RUNTIMES[kind] = entry
    return entry[1]


async def dispose_slice1_runtime(kind: str) -> None:
    if kind not in {"onboarding_writer", "review_writer", "file_writer", "reader"}:
        raise RuntimeError(_SLICE1_RUNTIME_ERROR) from None
    entry = _SLICE1_RUNTIMES.pop(kind, None)
    if entry is not None:
        await entry[0].dispose()


async def _slice1_session(kind: str):
    async with get_slice1_session_factory(kind)() as session:
        yield session


async def get_institution_onboarding_writer_session():
    async for session in _slice1_session("onboarding_writer"):
        yield session


async def get_institution_review_writer_session():
    async for session in _slice1_session("review_writer"):
        yield session


async def get_private_file_writer_session():
    async for session in _slice1_session("file_writer"):
        yield session


async def get_institution_onboarding_reader_session():
    async for session in _slice1_session("reader"):
        yield session
