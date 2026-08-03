import asyncio
import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.composition.identity_persistence import (
    IdentityPersistenceComposition,
    create_identity_session_factory,
)
from tests.integration.conftest import (
    _get_application_database_url,
    _get_test_database_target,
)
from tests.integration.database_safety import validate_database_sentinel


pytestmark = pytest.mark.integration


def test_p2_identity_session_composition_lifecycle_in_ephemeral_ci_only():
    if os.getenv("KG_TEST_ENVIRONMENT") != "ci_ephemeral":
        pytest.skip("P2 Identity IT0 is restricted to ephemeral CI")

    database_url = _get_application_database_url()
    _, target = _get_test_database_target()

    async def exercise_lifecycle() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        pool_events = {"checkout": 0, "checkin": 0}

        @event.listens_for(engine.sync_engine, "checkout")
        def record_checkout(*_args) -> None:
            pool_events["checkout"] += 1

        @event.listens_for(engine.sync_engine, "checkin")
        def record_checkin(*_args) -> None:
            pool_events["checkin"] += 1

        session_factory = create_identity_session_factory(engine)
        composition = IdentityPersistenceComposition(
            session_factory=session_factory,
            clock=lambda: datetime.now(timezone.utc),
        )

        sessions = []
        repositories = []
        try:
            first = composition.unit_of_work()
            async with first:
                first_repository = first.members
                first_session = first_repository._session
                repositories.append(first_repository)
                sessions.append(first_session)
                result = await first_session.execute(
                    text(
                        "SELECT shobj_description(oid, 'pg_database') "
                        "FROM pg_database WHERE datname = current_database()"
                    )
                )
                validate_database_sentinel(result.scalar_one(), target)
                await first.rollback()

            second = composition.unit_of_work()
            async with second:
                second_repository = second.members
                second_session = second_repository._session
                repositories.append(second_repository)
                sessions.append(second_session)
                result = await second_session.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
                await second.commit()

            assert sessions[0] is not sessions[1]
            assert repositories[0] is not repositories[1]
            assert all(not session.in_transaction() for session in sessions)
            assert pool_events == {"checkout": 2, "checkin": 2}
        finally:
            await engine.dispose()

    asyncio.run(exercise_lifecycle())
