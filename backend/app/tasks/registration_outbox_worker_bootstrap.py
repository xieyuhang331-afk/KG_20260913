from datetime import datetime, timezone
import os

from sqlalchemy.engine import make_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.composition.registration_outbox_runtime import (
    RegistrationOutboxDeliveryRuntime,
    RegistrationOutboxRuntimeComposition,
)
from app.core.uuid_generator import Uuid7Generator


def _utc_now():
    return datetime.now(timezone.utc)


class RegistrationOutboxWorkerBootstrap:
    """Own role-isolated resources for exactly one Celery task."""

    def __init__(
        self,
        *,
        identity_database_url,
        worker_database_url,
        engine_factory=create_async_engine,
        session_factory_builder=async_sessionmaker,
        composition_factory=RegistrationOutboxRuntimeComposition,
        runtime_factory=RegistrationOutboxDeliveryRuntime,
        clock=_utc_now,
        uuid_generator=None,
        expected_database_name=None,
        expected_database_sentinel=None,
        expected_identity_role=None,
        expected_worker_role=None,
    ) -> None:
        self._identity_database_url = identity_database_url
        self._worker_database_url = worker_database_url
        self._engine_factory = engine_factory
        self._session_factory_builder = session_factory_builder
        self._composition_factory = composition_factory
        self._runtime_factory = runtime_factory
        self._clock = clock
        self._uuid_generator = uuid_generator or Uuid7Generator()
        self._expected_database_name = expected_database_name
        self._expected_database_sentinel = expected_database_sentinel
        self._expected_identity_role = expected_identity_role
        self._expected_worker_role = expected_worker_role
        self._identity_engine = None
        self._worker_engine = None

    @classmethod
    def from_environment(cls):
        if os.getenv("KG_TEST_ENVIRONMENT") not in {
            "ci_ephemeral",
            "local_disposable",
        }:
            raise RuntimeError(
                "registration worker database target is not disposable"
            )
        identity_url = os.getenv("KG_IDENTITY_APPLICATION_DATABASE_URL")
        worker_url = os.getenv("KG_DELIVERY_WORKER_DATABASE_URL")
        run_id = os.getenv("KG_TEST_RUN_ID")
        identity_role = os.getenv("KG_TEST_APPLICATION_ROLE")
        worker_role = os.getenv("KG_TEST_DELIVERY_WORKER_ROLE")
        if not all(
            (identity_url, worker_url, run_id, identity_role, worker_role)
        ):
            raise RuntimeError(
                "registration worker database secret is unavailable"
            )
        expected_database_name = f"kg_it_{run_id}"
        expected_sentinel = f"kg-test-disposable:{run_id}"
        _validate_database_targets(
            identity_url,
            worker_url,
            expected_database_name=expected_database_name,
            expected_identity_role=identity_role,
            expected_worker_role=worker_role,
        )
        return cls(
            identity_database_url=identity_url,
            worker_database_url=worker_url,
            expected_database_name=expected_database_name,
            expected_database_sentinel=expected_sentinel,
            expected_identity_role=identity_role,
            expected_worker_role=worker_role,
        )

    async def __aenter__(self):
        try:
            self._identity_engine = self._engine_factory(
                self._identity_database_url,
                pool_pre_ping=True,
            )
            self._worker_engine = self._engine_factory(
                self._worker_database_url,
                pool_pre_ping=True,
            )
            if self._expected_database_name is not None:
                await _validate_connected_target(
                    self._identity_engine,
                    expected_database_name=self._expected_database_name,
                    expected_sentinel=self._expected_database_sentinel,
                    expected_role=self._expected_identity_role,
                )
                await _validate_connected_target(
                    self._worker_engine,
                    expected_database_name=self._expected_database_name,
                    expected_sentinel=self._expected_database_sentinel,
                    expected_role=self._expected_worker_role,
                )
            identity_session_factory = self._session_factory_builder(
                bind=self._identity_engine,
                expire_on_commit=False,
                autoflush=False,
            )
            worker_session_factory = self._session_factory_builder(
                bind=self._worker_engine,
                expire_on_commit=False,
                autoflush=False,
            )
            composition = self._composition_factory(
                identity_session_factory=identity_session_factory,
                worker_session_factory=worker_session_factory,
                clock=self._clock,
                uuid_generator=self._uuid_generator,
            )
            return self._runtime_factory(composition)
        except BaseException:
            await self._dispose()
            raise

    async def __aexit__(self, exc_type, exc, traceback):
        await self._dispose()
        return False

    async def _dispose(self):
        cleanup_error = None
        for engine_name in ("_worker_engine", "_identity_engine"):
            engine = getattr(self, engine_name)
            setattr(self, engine_name, None)
            if engine is None:
                continue
            try:
                await engine.dispose()
            except BaseException as caught:
                if cleanup_error is None:
                    cleanup_error = caught
        if cleanup_error is not None:
            raise cleanup_error


def _validate_database_targets(
    identity_url: str,
    worker_url: str,
    *,
    expected_database_name: str,
    expected_identity_role: str,
    expected_worker_role: str,
) -> None:
    try:
        identity = make_url(identity_url)
        worker = make_url(worker_url)
        valid = (
            identity.drivername == "postgresql+asyncpg"
            and worker.drivername == "postgresql+asyncpg"
            and identity.host in {"127.0.0.1", "localhost"}
            and worker.host == identity.host
            and worker.port == identity.port
            and worker.database == identity.database
            and identity.database == expected_database_name
            and identity.username == expected_identity_role
            and worker.username == expected_worker_role
            and identity.username != worker.username
            and identity.password
            and worker.password
        )
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(
            "registration worker database target is not disposable"
        ) from None


async def _validate_connected_target(
    engine,
    *,
    expected_database_name: str,
    expected_sentinel: str,
    expected_role: str,
) -> None:
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT current_user, current_database(), "
                    "shobj_description(oid, 'pg_database') "
                    "FROM pg_database WHERE datname = current_database()"
                )
            )
            current_role, database_name, sentinel = result.one()
    except BaseException:
        raise RuntimeError(
            "registration worker database identity is unverified"
        ) from None
    if (
        current_role != expected_role
        or database_name != expected_database_name
        or sentinel != expected_sentinel
    ):
        raise RuntimeError(
            "registration worker database identity is unverified"
        ) from None
