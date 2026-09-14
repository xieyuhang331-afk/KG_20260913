from __future__ import annotations

import asyncio

from sqlalchemy.dialects import postgresql


class _MappingsResult:
    def mappings(self):
        return self

    def all(self):
        return []

    def one_or_none(self):
        return None


class _Session:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _MappingsResult()


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_列表使用owner过滤稳定keyset且只读() -> None:
    from app.modules.user_health.repository import list_member_detection_reports

    session = _Session()
    asyncio.run(
        list_member_detection_reports(
            session,
            user_id=7,
            report_type="store_retest",
            start_at=None,
            end_at=None,
            cursor_detection_time=None,
            cursor_id=None,
            limit=21,
        )
    )
    sql = _sql(session.statements[0])
    assert "r4_member_self_detection_report_history_v1" in sql
    assert "(7,'store_retest'" in sql
    assert "INSERT" not in sql and "UPDATE" not in sql and "DELETE" not in sql


def test_详情同时过滤report_id和owner_user_id() -> None:
    from app.modules.user_health.repository import get_member_detection_report

    session = _Session()
    asyncio.run(get_member_detection_report(session, user_id=7, report_id=11))
    sql = _sql(session.statements[0])
    assert "r4_member_self_detection_report_read_v1(7,11)" in sql
    assert "FROM user" not in sql
