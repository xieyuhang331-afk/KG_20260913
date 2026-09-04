from __future__ import annotations

import json
from datetime import datetime
from typing import Mapping

from sqlalchemy import and_, case, or_, select, text

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
            PrivateFileModel.scan_attempt_count,
            PrivateFileModel.scan_last_error_code,
            PrivateFileModel.scan_next_retry_at,
        ).where(PrivateFileModel.file_id == file_id))
        row = result.mappings().one_or_none()
        if row is None:
            return None
        relation = await self.qualification_relation(file_id)
        return {**row, **relation}

    async def writer_file_snapshot(self, file_id: str):
        result = await self.session.execute(select(
            PrivateFileModel.file_id, PrivateFileModel.purpose,
            PrivateFileModel.owner_user_id,
            PrivateFileModel.status, PrivateFileModel.actual_size,
            PrivateFileModel.actual_mime_type, PrivateFileModel.actual_sha256,
            PrivateFileModel.object_key, PrivateFileModel.bound_application_id,
            PrivateFileModel.created_at,
        ).where(PrivateFileModel.file_id == file_id))
        row = result.mappings().one_or_none()
        if row is None:
            return None
        relation = await self.qualification_relation(file_id)
        return {**row, **relation}

    async def closed_access_snapshot(
        self,
        *,
        file_id: str,
        actor_user_id: int,
        context: str,
    ) -> dict | None:
        result = await self.session.execute(
            text(
                "SELECT file_id,purpose,owner_user_id,status,actual_size,"
                "actual_mime_type,actual_sha256,object_key,bound_application_id,"
                "qualification_bound,reviewer_access "
                "FROM public.batch_b_private_file_access_snapshot_v1("
                "CAST(:file_id AS uuid),:actor_user_id,:context)"
            ),
            {
                "file_id": file_id,
                "actor_user_id": actor_user_id,
                "context": context,
            },
        )
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

    async def generated_export_file_record(self, file_id: str) -> dict | None:
        result = await self.session.execute(select(
            PrivateFileModel.file_id,
            PrivateFileModel.purpose,
            PrivateFileModel.owner_user_id,
            PrivateFileModel.status,
            PrivateFileModel.actual_size,
            PrivateFileModel.actual_sha256,
            PrivateFileModel.created_at,
        ).where(PrivateFileModel.file_id == file_id))
        row = result.mappings().one_or_none()
        return None if row is None else dict(row)

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
            PrivateFileModel.scan_attempt_count,
            PrivateFileModel.scan_last_error_code,
            PrivateFileModel.scan_next_retry_at,
            PrivateFileModel.scan_lease_token,
            PrivateFileModel.scan_lease_until,
            PrivateFileModel.scan_operation_ref_digest,
            PrivateFileModel.scan_version,
            PrivateFileModel.upload_lease_token,
            PrivateFileModel.upload_lease_until,
            PrivateFileModel.upload_operation_ref_digest,
            PrivateFileModel.upload_version,
        ).where(PrivateFileModel.file_id == file_id))
        return result.mappings().one_or_none()

    async def claim_upload(
        self,
        file_id: str,
        *,
        owner_user_id: int,
        lease_token: str,
        lease_until: datetime,
        operation_ref_digest: str,
        now: datetime,
    ):
        result = await self.session.execute(
            select(PrivateFileModel)
            .where(PrivateFileModel.file_id == file_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None or row.owner_user_id != owner_user_id:
            return None
        if row.status != "UPLOAD_INITIATED" or row.actual_size is not None:
            return False
        if row.upload_lease_until is not None and row.upload_lease_until > now:
            return False
        row.upload_lease_token = lease_token
        row.upload_lease_until = lease_until
        row.upload_operation_ref_digest = operation_ref_digest
        row.upload_version += 1
        await self.session.flush()
        return row

    async def claimed_upload(
        self, file_id: str, *, lease_token: str, expected_version: int
    ):
        result = await self.session.execute(
            select(PrivateFileModel)
            .where(
                PrivateFileModel.file_id == file_id,
                PrivateFileModel.status == "UPLOAD_INITIATED",
                PrivateFileModel.upload_lease_token == lease_token,
                PrivateFileModel.upload_version == expected_version,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def issue_access(self, values: Mapping[str, object]) -> dict[str, object]:
        result = await self.session.execute(
            text(
                "SELECT result_code,access_version,result_digest FROM "
                "public.batch_b_private_file_access_issue_v1("
                "CAST(:access_id AS uuid),CAST(:private_file_id AS uuid),"
                ":actor_user_id,:access_scope,:reason_code,:credential_digest,"
                ":authority_digest,:content_evidence_digest,:issued_at,:expires_at)"
            ),
            dict(values),
        )
        return dict(result.mappings().one())

    async def consume_access(self, values: Mapping[str, object]) -> dict[str, object]:
        result = await self.session.execute(
            text(
                "SELECT result_code,access_version,result_digest FROM "
                "public.batch_b_private_file_access_consume_v1("
                "CAST(:access_id AS uuid),CAST(:private_file_id AS uuid),"
                ":actor_user_id,:credential_digest,:authority_digest,"
                ":consumed_at,:expected_version)"
            ),
            dict(values),
        )
        return dict(result.mappings().one())

    async def confirm_access(self, values: Mapping[str, object]) -> dict[str, object]:
        result = await self.session.execute(
            text(
                "SELECT result_code,access_version,result_digest FROM "
                "public.batch_b_private_file_access_confirm_v1("
                "CAST(:access_id AS uuid),CAST(:private_file_id AS uuid),"
                ":actor_user_id,:credential_digest,:authority_digest,"
                ":consumed_at,:expected_version)"
            ),
            dict(values),
        )
        return dict(result.mappings().one())

    async def claim_scan_attempt(
        self,
        file_id: str,
        *,
        lease_token: str,
        operation_ref_digest: str,
        now: datetime,
        lease_until: datetime,
    ):
        statement = (
            select(PrivateFileModel)
            .where(PrivateFileModel.file_id == file_id)
            .with_for_update()
        )
        result = await self.session.execute(statement)
        row = result.scalar_one_or_none()
        if (
            row is None
            or row.status != "PENDING_SCAN"
            or row.scan_attempt_count >= 4
            or row.scan_last_error_code == "COMMIT_OUTCOME_UNKNOWN"
            or (
                row.scan_next_retry_at is not None
                and row.scan_next_retry_at > now
            )
            or (row.scan_lease_until is not None and row.scan_lease_until > now)
        ):
            return None
        row.scan_last_error_code = "COMMIT_OUTCOME_UNKNOWN"
        row.scan_next_retry_at = None
        row.scan_lease_token = lease_token
        row.scan_lease_until = lease_until
        row.scan_operation_ref_digest = operation_ref_digest
        row.scan_attempt_count += 1
        row.scan_version += 1
        await self.session.flush()
        return row

    async def get_claimed_scan(
        self, file_id: str, *, lease_token: str, expected_version: int
    ):
        result = await self.session.execute(
            select(PrivateFileModel)
            .where(
                PrivateFileModel.file_id == file_id,
                PrivateFileModel.status == "PENDING_SCAN",
                PrivateFileModel.scan_lease_token == lease_token,
                PrivateFileModel.scan_version == expected_version,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def isolate_scan_commit_unknown(self, file_id: str, *, now: datetime):
        result = await self.session.execute(
            select(PrivateFileModel)
            .where(PrivateFileModel.file_id == file_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if (
            row.status == "SCAN_FAILED"
            and row.scan_last_error_code == "COMMIT_OUTCOME_UNKNOWN"
        ):
            return row
        if row.status != "PENDING_SCAN":
            return None
        row.status = "SCAN_FAILED"
        row.scan_last_error_code = "COMMIT_OUTCOME_UNKNOWN"
        row.scan_next_retry_at = None
        row.scan_lease_token = None
        row.scan_lease_until = None
        row.scan_operation_ref_digest = None
        row.scanned_at = now
        row.scan_version += 1
        await self.session.flush()
        return row

    async def recover_pending_scan_ids(
        self, *, now: datetime, limit: int = 100
    ) -> tuple[str, ...]:
        result = await self.session.execute(
            select(PrivateFileModel)
            .where(
                PrivateFileModel.status == "PENDING_SCAN",
                or_(
                    and_(
                        PrivateFileModel.scan_lease_token.is_(None),
                        or_(
                            PrivateFileModel.scan_next_retry_at.is_(None),
                            PrivateFileModel.scan_next_retry_at <= now,
                        ),
                    ),
                    PrivateFileModel.scan_lease_until <= now,
                ),
            )
            .order_by(PrivateFileModel.created_at, PrivateFileModel.file_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = tuple(result.scalars())
        recoverable: list[str] = []
        for row in rows:
            if row.scan_last_error_code == "COMMIT_OUTCOME_UNKNOWN":
                row.status = "SCAN_FAILED"
                row.scan_next_retry_at = None
                row.scan_lease_token = None
                row.scan_lease_until = None
                row.scan_operation_ref_digest = None
                row.scanned_at = now
                row.scan_version += 1
                continue
            if row.scan_lease_token is not None:
                row.scan_lease_token = None
                row.scan_lease_until = None
                row.scan_version += 1
            if row.scan_attempt_count >= 4:
                row.status = "SCAN_FAILED"
                row.scan_last_error_code = "WORKER_LOST"
                row.scan_next_retry_at = None
                row.scanned_at = now
            else:
                recoverable.append(row.file_id)
        await self.session.flush()
        return tuple(recoverable)

    async def private_file_referenced(self, file_id: str) -> bool:
        result = await self.session.execute(
            text("SELECT public.a3_private_file_referenced_v1(CAST(:file_id AS uuid))"),
            {"file_id": file_id},
        )
        return bool(result.scalar_one())

    async def expired_orphan_ids(
        self, *, now: datetime, limit: int = 100
    ) -> tuple[str, ...]:
        result = await self.session.execute(
            select(PrivateFileModel.file_id)
            .where(
                PrivateFileModel.expires_at <= now,
                PrivateFileModel.bound_application_id.is_(None),
                PrivateFileModel.status != "PENDING_SCAN",
                PrivateFileModel.status.in_(
                    ("UPLOAD_INITIATED", "REJECTED", "SCAN_FAILED", "DELETED")
                ),
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
            if not await self.private_file_referenced(file_id):
                safe.append(file_id)
        return tuple(safe)

    async def active_upload_leases(self, *, now: datetime) -> set[str]:
        result = await self.session.execute(
            select(PrivateFileModel.upload_lease_token).where(
                PrivateFileModel.upload_lease_token.is_not(None),
                PrivateFileModel.upload_lease_until > now,
            )
        )
        return {str(value) for value in result.scalars() if value is not None}
