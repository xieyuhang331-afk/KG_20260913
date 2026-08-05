from uuid import RFC_4122, UUID

import asyncpg
from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect

from app.modules.member.infrastructure.models import MemberOrmModel


EXPECTED_RED = (
    "成员 ORM UUID 数据库边界未将 asyncpg UUID 子类规范化为标准库 uuid.UUID"
)
DRIVER_UUID_TEXT = "018f47e9-4b9a-7abc-8def-0123456789ab"


def test_成员ORM类型边界将asyncpg_UUID子类规范化为标准库UUID():
    driver_value = asyncpg.pgproto.pgproto.UUID(DRIVER_UUID_TEXT)
    assert isinstance(driver_value, UUID)
    assert type(driver_value) is not UUID

    member_id_type = MemberOrmModel.__table__.c.member_id.type
    processor = member_id_type.result_processor(asyncpg_dialect(), None)
    normalized_value = processor(driver_value) if processor else driver_value

    assert type(normalized_value) is UUID, EXPECTED_RED
    assert normalized_value.int == driver_value.int
    assert normalized_value.bytes == driver_value.bytes
    assert normalized_value.version == 7
    assert normalized_value.variant == RFC_4122
