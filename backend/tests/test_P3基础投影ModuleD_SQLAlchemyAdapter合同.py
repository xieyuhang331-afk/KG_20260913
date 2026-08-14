from pathlib import Path

import asyncio
import pytest

from app.modules.projection_read.domain import ProjectionReadUnavailable
from app.modules.projection_read import service


ROOT = Path(__file__).resolve().parents[1]


def test_Adapter只使用READY视图显式列与授权谓词():
    text = (ROOT / "app/modules/projection_read/repository.py").read_text("utf-8")
    for view in (
        "organization_ready_projection_generation_v1", "organization_ready_projection_v1",
        "health_ready_projection_generation_v1", "health_ready_projection_fact_v1",
        "health_ready_projection_window_selection_v1",
    ):
        assert view in text
    assert "select(*)" not in text.lower()
    for token in ("generation_id", "subject_user_id", "indicator_code", "scope_eligible", "status"):
        assert token in text


def test_Adapter没有ORM和lazyload入口():
    text = (ROOT / "app/modules/projection_read/repository.py").read_text("utf-8")
    assert "relationship(" not in text
    assert ".query(" not in text
    assert "selectinload" not in text


class _AsyncContext:
    def __init__(self, value):
        self.value = value
        self.exited = False

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        self.exited = True


class _Session:
    def __init__(self, failure=None):
        self.statements = []
        self.failure = failure
        self.session_context = _AsyncContext(self)
        self.transaction_context = _AsyncContext(self)

    async def __aenter__(self):
        return await self.session_context.__aenter__()

    async def __aexit__(self, *args):
        return await self.session_context.__aexit__(*args)

    def begin(self):
        return self.transaction_context

    async def execute(self, statement, parameters=None):
        self.statements.append((str(statement), parameters))
        if self.failure is not None and len(self.statements) == 2:
            raise self.failure


def test_Reader单短RR事务先取得shared_lock再允许Repository查询(monkeypatch):
    session = _Session()

    async def factory(_kind):
        return lambda: session

    monkeypatch.setattr(service, "get_projection_session_factory", factory)

    async def verify():
        async with service._reader("organization_reader", lambda value: value) as repository:
            assert repository is session
            assert len(session.statements) == 2

    asyncio.run(verify())
    assert session.statements[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert "pg_advisory_xact_lock_shared" in session.statements[1][0]
    assert session.transaction_context.exited and session.session_context.exited


@pytest.mark.parametrize(
    ("failure", "expected"),
    ((asyncio.CancelledError(), asyncio.CancelledError), (RuntimeError("vendor"), ProjectionReadUnavailable)),
)
def test_Reader锁阶段Cancellation原样传播且vendor文本被固定映射(monkeypatch, failure, expected):
    session = _Session(failure)

    async def factory(_kind):
        return lambda: session

    monkeypatch.setattr(service, "get_projection_session_factory", factory)

    async def verify():
        with pytest.raises(expected) as rejected:
            async with service._reader("organization_reader", lambda value: value):
                raise AssertionError("repository must not be reached")
        if expected is ProjectionReadUnavailable:
            assert rejected.value.__cause__ is None

    asyncio.run(verify())
    assert session.transaction_context.exited and session.session_context.exited
