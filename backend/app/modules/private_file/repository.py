from __future__ import annotations

import json
from datetime import datetime
from typing import Mapping

from sqlalchemy import case, select, text

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
        row = result.mappings().one_or_none()
        if row is None:
            return None
        relation = await self.qualification_relation(file_id)
        return {**row, **relation}

    async def access_snapshot(self, file_id: str):
        result = await self.session.execute(select(
            PrivateFileModel.file_id, PrivateFileModel.purpose,
            PrivateFileModel.owner_user_id,
            PrivateFileModel.status, PrivateFileModel.actual_size,
            PrivateFileModel.actual_sha256, PrivateFileModel.bound_application_id,
            PrivateFileModel.created_at,
        ).where(PrivateFileModel.file_id == file_id))
        row = result.mappings().one_or_none()
        if row is None:
            return None
        relation = await self.qualification_relation(file_id)
        return {**row, **relation}

    async def report_file_authority(
        self,
        *,
        file_id: str,
        actor_user_id: int,
        context: str,
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.slice4_report_file_authority_v1("
                ":file_id,NULL,:actor_user_id,:context)"
            ),
            {
                "file_id": file_id,
                "actor_user_id": actor_user_id,
                "context": context,
            },
        )
        return bool(result.scalar_one())

    async def consume_export_download_access(
        self, payload: Mapping[str, object]
    ) -> bool:
        value = json.dumps(
            dict(payload),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            default=lambda item: item.isoformat(),
        )
        result = await self.session.execute(
            text(
                "SELECT public.slice7_export_download_consume_v1("
                "CAST(:value AS jsonb))"
            ),
            {"value": value},
        )
        return bool(result.scalar_one())

    async def register_generated_export_archive(
        self, payload: Mapping[str, object]
    ) -> dict:
        value = json.dumps(
            dict(payload),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
            default=lambda item: item.isoformat(),
        )
        result = await self.session.execute(
            text(
                "SELECT public.slice7_export_private_file_register_v1("
                "CAST(:value AS jsonb)) AS value"
            ),
            {"value": value},
        )
        return dict(result.scalar_one())

    async def generated_export_archive_snapshot(
        self, *, export_id: str, file_id: str
    ) -> dict | None:
        result = await self.session.execute(
            text(
                "SELECT public.slice7_export_private_file_snapshot_v1("
                "CAST(:export_id AS uuid),CAST(:file_id AS uuid)) AS value"
            ),
            {"export_id": export_id, "file_id": file_id},
        )
        value = result.scalar_one_or_none()
        return None if value is None else dict(value)

    async def qualification_relation(self, file_id: str):
        result = await self.session.execute(
            text(
                "SELECT qualification_bound,reviewer_access "
                "FROM public.therapist_qualification_file_relation_v1(:file_id)"
            ),
            {"file_id": file_id},
        )
        row = result.mappings().one()
        return {
            "qualification_bound": bool(row["qualification_bound"]),
            "reviewer_access": bool(row["reviewer_access"]),
        }

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
        candidates = tuple(result.scalars())
        safe: list[str] = []
        for file_id in candidates:
            if not (await self.qualification_relation(file_id))["qualification_bound"]:
                safe.append(file_id)
        return tuple(safe)
