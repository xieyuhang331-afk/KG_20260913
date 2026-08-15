from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, select

from app.modules.private_file.models import PrivateFileModel


class PrivateFileRepository:
    def __init__(self, session):
        self.session = session

    async def get(self, file_id: str, *, for_update: bool = False):
        statement = select(PrivateFileModel).where(PrivateFileModel.file_id == file_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def add(self, value: PrivateFileModel) -> None:
        self.session.add(value)
        await self.session.flush()

    async def metadata(self, file_id: str):
        result = await self.session.execute(select(
            PrivateFileModel.file_id, PrivateFileModel.purpose, PrivateFileModel.owner_user_id,
            PrivateFileModel.declared_size, PrivateFileModel.declared_mime_type,
            PrivateFileModel.status, PrivateFileModel.bound_application_id,
            PrivateFileModel.created_at, PrivateFileModel.expires_at,
            PrivateFileModel.scanned_at, PrivateFileModel.bound_at,
        ).where(PrivateFileModel.file_id == file_id))
        return result.mappings().one_or_none()

    async def access_snapshot(self, file_id: str):
        result = await self.session.execute(select(
            PrivateFileModel.file_id, PrivateFileModel.owner_user_id,
            PrivateFileModel.status, PrivateFileModel.actual_size,
            PrivateFileModel.actual_sha256, PrivateFileModel.bound_application_id,
            PrivateFileModel.created_at,
        ).where(PrivateFileModel.file_id == file_id))
        return result.mappings().one_or_none()

    async def persistence_snapshot(self, file_id: str):
        result = await self.session.execute(select(
            PrivateFileModel.file_id, PrivateFileModel.status,
            PrivateFileModel.actual_size, PrivateFileModel.actual_mime_type,
            PrivateFileModel.actual_sha256, PrivateFileModel.scanned_at,
            PrivateFileModel.deleted_at,
        ).where(PrivateFileModel.file_id == file_id))
        return result.mappings().one_or_none()

    async def pending_scan_ids(self, *, limit: int = 100) -> tuple[str, ...]:
        result = await self.session.execute(
            select(PrivateFileModel.file_id)
            .where(PrivateFileModel.status == "PENDING_SCAN")
            .order_by(PrivateFileModel.created_at, PrivateFileModel.file_id)
            .limit(limit)
        )
        return tuple(result.scalars())

    async def expired_orphan_ids(
        self, *, now: datetime, limit: int = 100
    ) -> tuple[str, ...]:
        result = await self.session.execute(
            select(PrivateFileModel.file_id)
            .where(
                PrivateFileModel.expires_at <= now,
                PrivateFileModel.bound_application_id.is_(None),
            )
            .order_by(
                case((PrivateFileModel.status == "DELETED", 0), else_=1),
                PrivateFileModel.expires_at,
                PrivateFileModel.file_id,
            )
            .limit(limit)
        )
        return tuple(result.scalars())
