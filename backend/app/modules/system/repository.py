from __future__ import annotations

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.system.models import OperationLog


def _ensure_mapped() -> None:
    map_core_model_classes()


async def create_operation_log(
    session,
    *,
    operator_id: int,
    module: str,
    object_type: str,
    object_id: int,
    action: str,
    payload: dict | None,
):
    _ensure_mapped()
    log = OperationLog()
    log.operator_id = operator_id
    log.module = module
    log.object_type = object_type
    log.object_id = object_id
    log.action = action
    log.payload = payload
    session.add(log)
    await session.flush()
    return log
