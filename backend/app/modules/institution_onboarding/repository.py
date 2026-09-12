from __future__ import annotations

import hashlib

from fastapi import HTTPException
from sqlalchemy import BigInteger, and_, bindparam, insert, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only

from app.modules.auth.models import User
from app.modules.institution_onboarding.models import (
    InstitutionApplicationModel,
    InstitutionApplicationRevisionModel,
    InstitutionInvitationModel,
    InstitutionLicenseModel,
    InstitutionOnboardingAccountModel,
    InstitutionOnboardingAuditModel,
    InstitutionOnboardingDeliveryModel,
    InstitutionOnboardingIdempotencyModel,
    InstitutionOnboardingOutboxModel,
)
from app.modules.tenant.models import Tenant


class InstitutionOnboardingRepository:
    def __init__(self, session):
        self.session = session

    async def lock_operation(self, actor_scope: str, operation: str, key: str) -> None:
        identity = f"{actor_scope}\x1f{operation}\x1f{key}".encode()
        lock_key = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=True)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    async def get_invitation_for_update(self, invitation_id: str):
        result = await self.session.execute(
            select(InstitutionInvitationModel)
            .where(InstitutionInvitationModel.invitation_id == invitation_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_invitation(self, invitation_id: str):
        result = await self.session.execute(
            select(InstitutionInvitationModel).where(
                InstitutionInvitationModel.invitation_id == invitation_id
            )
        )
        return result.scalar_one_or_none()

    async def invitation_for_review(self, invitation_id: str):
        result = await self.session.execute(
            select(
                InstitutionInvitationModel.invitation_id,
                InstitutionInvitationModel.institution_name,
                InstitutionInvitationModel.institution_type,
                InstitutionInvitationModel.administrative_region_id,
            ).where(InstitutionInvitationModel.invitation_id == invitation_id)
        )
        return result.mappings().one_or_none()

    async def active_canonical_county(self, region_id: int):
        expected_types = ("county", "city", "province", "headquarter")
        current_id = region_id
        path: list[int] = []
        for expected_type in expected_types:
            result = await self.session.execute(
                select(
                    text("id"),
                    text("parent_id"),
                    text("org_type"),
                    text("status"),
                )
                .select_from(text("public.platform_org"))
                .where(text("id=:region_id"))
                .with_for_update(read=True),
                {"region_id": current_id},
            )
            row = result.mappings().one_or_none()
            if (
                row is None
                or row["org_type"] != expected_type
                or row["status"] != "active"
            ):
                return None
            path.append(row["id"])
            current_id = row["parent_id"]
        return tuple(path) if current_id is None else None

    async def get_application_for_user(self, user_id: int, *, for_update: bool = False):
        statement = select(InstitutionApplicationModel).where(
            InstitutionApplicationModel.applicant_user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_application_for_review(self, application_id: str, *, for_update: bool = False):
        statement = select(InstitutionApplicationModel).where(
            InstitutionApplicationModel.application_id == application_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_invitations(self):
        result = await self.session.execute(
            select(
                InstitutionInvitationModel.invitation_id, InstitutionInvitationModel.institution_name,
                InstitutionInvitationModel.institution_type, InstitutionInvitationModel.status,
                InstitutionInvitationModel.expires_at, InstitutionInvitationModel.version,
            ).order_by(InstitutionInvitationModel.issued_at.desc())
        )
        return tuple(result.mappings())

    async def list_reviews(self):
        result = await self.session.execute(
            select(InstitutionApplicationModel)
            .where(InstitutionApplicationModel.status.in_(("SUBMITTED", "UNDER_REVIEW")))
            .order_by(InstitutionApplicationModel.submitted_at)
        )
        return tuple(result.scalars())

    async def list_revisions(self, application_id: str):
        result = await self.session.execute(
            select(InstitutionApplicationRevisionModel)
            .where(InstitutionApplicationRevisionModel.application_id == application_id)
            .order_by(InstitutionApplicationRevisionModel.revision_no)
        )
        return tuple(result.scalars())

    async def require_current_revision(self, application_id: str, revision_no: int):
        result = await self.session.execute(
            select(InstitutionApplicationRevisionModel).where(
                InstitutionApplicationRevisionModel.application_id == application_id,
                InstitutionApplicationRevisionModel.revision_no == revision_no,
            )
        )
        return result.scalar_one_or_none()

    async def require_clean_bound_files(self, application_id: str):
        from app.modules.private_file.models import PrivateFileModel

        result = await self.session.execute(
            select(
                InstitutionLicenseModel.license_type,
                PrivateFileModel.file_id,
                PrivateFileModel.purpose,
                PrivateFileModel.status,
                PrivateFileModel.bound_application_id,
            )
            .join(
                PrivateFileModel,
                PrivateFileModel.file_id == InstitutionLicenseModel.private_file_id,
            )
            .where(InstitutionLicenseModel.application_id == application_id)
            .order_by(InstitutionLicenseModel.license_type)
        )
        return tuple(result.mappings())

    async def review_materials(self, application_id: str):
        from app.modules.private_file.models import PrivateFileModel

        result = await self.session.execute(
            select(
                InstitutionLicenseModel.license_type,
                InstitutionLicenseModel.private_file_id,
                InstitutionLicenseModel.valid_from,
                InstitutionLicenseModel.valid_until,
                PrivateFileModel.status,
                PrivateFileModel.scanned_at,
            )
            .join(
                PrivateFileModel,
                PrivateFileModel.file_id == InstitutionLicenseModel.private_file_id,
            )
            .where(InstitutionLicenseModel.application_id == application_id)
            .order_by(InstitutionLicenseModel.license_type)
        )
        return tuple(result.mappings())

    async def lock_files_for_binding(self, file_ids: tuple[str, ...]):
        from app.modules.private_file.models import PrivateFileModel

        result = await self.session.execute(
            select(PrivateFileModel)
            .options(
                load_only(
                    PrivateFileModel.file_id,
                    PrivateFileModel.purpose,
                    PrivateFileModel.owner_user_id,
                    PrivateFileModel.status,
                    PrivateFileModel.bound_application_id,
                    PrivateFileModel.bound_at,
                )
            )
            .where(PrivateFileModel.file_id.in_(file_ids))
            .order_by(PrivateFileModel.file_id).with_for_update()
        )
        return tuple(result.scalars())

    async def licenses_for_update(self, application_id: str):
        result = await self.session.execute(
            select(InstitutionLicenseModel)
            .options(
                load_only(
                    InstitutionLicenseModel.license_id,
                    InstitutionLicenseModel.application_id,
                    InstitutionLicenseModel.license_type,
                    InstitutionLicenseModel.license_no_ciphertext,
                    InstitutionLicenseModel.license_no_digest,
                    InstitutionLicenseModel.private_file_id,
                    InstitutionLicenseModel.valid_from,
                    InstitutionLicenseModel.valid_until,
                )
            )
            .where(InstitutionLicenseModel.application_id == application_id)
            .order_by(InstitutionLicenseModel.license_type)
            .with_for_update(read=True)
        )
        return tuple(result.scalars())

    async def licenses_for_application(self, application_id: str):
        result = await self.session.execute(
            select(
                InstitutionLicenseModel.license_type,
                InstitutionLicenseModel.private_file_id,
                InstitutionLicenseModel.valid_from,
                InstitutionLicenseModel.valid_until,
            )
            .where(InstitutionLicenseModel.application_id == application_id)
            .order_by(InstitutionLicenseModel.license_type)
        )
        return tuple(result.all())

    async def reviewer_currentness(self, user_id: int):
        statement = text(
            "SELECT id,role,status,tenant_id "
            "FROM public.institution_onboarding_reviewer_currentness_v1(:user_id)"
        ).bindparams(bindparam("user_id", value=user_id, type_=BigInteger))
        try:
            result = await self.session.execute(statement)
        except SQLAlchemyError:
            raise HTTPException(503, "DEPENDENCY_UNAVAILABLE") from None
        current = result.mappings().one_or_none()
        if current is None:
            return None
        return {
            "id": current["id"],
            "role": current["role"],
            "status": current["status"],
            "tenant_id": current["tenant_id"],
        }

    async def add(self, value) -> None:
        self.session.add(value)
        await self.session.flush()

    async def add_audit(self, value: InstitutionOnboardingAuditModel) -> None:
        await self.session.execute(
            insert(InstitutionOnboardingAuditModel)
            .inline()
            .values(
                actor_user_id=value.actor_user_id,
                actor_role=value.actor_role,
                action=value.action,
                object_type=value.object_type,
                object_id=value.object_id,
                result=value.result,
                reason_code=value.reason_code,
                request_id=value.request_id,
                created_at=value.created_at,
            )
        )

    async def add_onboarding_user(self, value: User) -> None:
        result = await self.session.execute(
            insert(User)
            .values(
                phone=value.phone,
                password_hash=value.password_hash,
                role=value.role,
                status=value.status,
                tenant_id=value.tenant_id,
            )
            .returning(User.id)
        )
        value.id = result.scalar_one()

    async def add_approved_tenant(self, value: Tenant) -> None:
        result = await self.session.execute(
            insert(Tenant)
            .values(
                org_id=value.org_id,
                tenant_code=value.tenant_code,
                name=value.name,
                type=value.type,
                credit_code=value.credit_code,
                legal_person_name=value.legal_person_name,
                province=value.province,
                city=value.city,
                district=value.district,
                address=value.address,
                contact_name=value.contact_name,
                contact_phone=value.contact_phone,
                contact_email=value.contact_email,
                status=value.status,
                reviewed_by=value.reviewed_by,
                reviewed_at=value.reviewed_at,
                approved_at=value.approved_at,
            )
            .returning(Tenant.id)
        )
        value.id = result.scalar_one()

    async def bind_user_tenant(self, *, user_id: int, tenant_id: int) -> None:
        result = await self.session.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id.is_(None))
            .values(tenant_id=tenant_id)
        )
        if result.rowcount != 1:
            raise RuntimeError("ONBOARDING_USER_TENANT_BIND_CONFLICT")

    async def add_idempotency(
        self, value: InstitutionOnboardingIdempotencyModel
    ) -> None:
        await self.session.execute(
            insert(InstitutionOnboardingIdempotencyModel)
            .inline()
            .values(
                actor_scope=value.actor_scope,
                operation=value.operation,
                idempotency_key=value.idempotency_key,
                request_digest=value.request_digest,
                response_payload=value.response_payload,
                created_at=value.created_at,
            )
        )

    async def idempotency(self, actor_scope: str, operation: str, key: str):
        result = await self.session.execute(
            select(InstitutionOnboardingIdempotencyModel).where(
                InstitutionOnboardingIdempotencyModel.actor_scope == actor_scope,
                InstitutionOnboardingIdempotencyModel.operation == operation,
                InstitutionOnboardingIdempotencyModel.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def account_for_user(self, user_id: int):
        result = await self.session.execute(
            select(InstitutionOnboardingAccountModel).where(
                InstitutionOnboardingAccountModel.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def claim_dispatchable_outbox(self, *, stale_before):
        result = await self.session.execute(
            select(InstitutionOnboardingOutboxModel)
            .where(
                or_(
                    InstitutionOnboardingOutboxModel.status == "PENDING",
                    and_(
                        InstitutionOnboardingOutboxModel.status == "PROCESSING",
                        InstitutionOnboardingOutboxModel.processing_at <= stale_before,
                    ),
                )
            )
            .order_by(
                InstitutionOnboardingOutboxModel.created_at,
                InstitutionOnboardingOutboxModel.event_id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def get_outbox(self, event_id: str, *, for_update: bool = False):
        statement = select(InstitutionOnboardingOutboxModel).where(
            InstitutionOnboardingOutboxModel.event_id == event_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_delivery(self, event_id: str):
        result = await self.session.execute(
            select(InstitutionOnboardingDeliveryModel).where(
                InstitutionOnboardingDeliveryModel.event_id == event_id
            )
        )
        return result.scalar_one_or_none()

    async def deliver(
        self,
        *,
        event_id: str,
        recipient_user_id: int,
        payload: dict,
        payload_digest: str,
        now,
    ):
        existing = await self.get_delivery(event_id)
        if existing is not None:
            return existing
        await self.session.execute(
            insert(InstitutionOnboardingDeliveryModel).inline().values(
                event_id=event_id,
                event_type="INSTITUTION_APPROVED",
                recipient_user_id=recipient_user_id,
                recipient_scope="INSTITUTION_ADMIN",
                payload=payload,
                payload_digest=payload_digest,
                created_at=now,
            )
        )
        return await self.get_delivery(event_id)


__all__ = [
    "InstitutionOnboardingRepository",
    "InstitutionApplicationModel",
    "InstitutionApplicationRevisionModel",
    "InstitutionInvitationModel",
    "InstitutionLicenseModel",
    "InstitutionOnboardingAccountModel",
    "InstitutionOnboardingAuditModel",
    "InstitutionOnboardingIdempotencyModel",
    "InstitutionOnboardingDeliveryModel",
    "InstitutionOnboardingOutboxModel",
]
