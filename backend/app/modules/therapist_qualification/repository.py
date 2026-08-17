from __future__ import annotations

import hashlib

from sqlalchemy import and_, insert, or_, select, text
from sqlalchemy.orm import load_only

from app.modules.therapist_qualification.models import (
    InstitutionServiceReadinessModel,
    ReadinessEvidenceModel,
    TherapistInvitationModel,
    TherapistProfileModel,
    TherapistProfileRevisionModel,
    TherapistQualificationAttachmentModel,
    TherapistQualificationVersionModel,
    TherapistReviewDecisionModel,
    TherapistReviewItemModel,
    TherapistRevisionQualificationModel,
    TherapistStatusDecisionModel,
    TherapistWorkflowAuditModel,
    TherapistWorkflowDeliveryModel,
    TherapistWorkflowIdempotencyModel,
    TherapistWorkflowOutboxModel,
)


class TherapistQualificationRepository:
    def __init__(self, session):
        self.session = session

    async def lock_operation(self, actor_scope: str, operation: str, key: str) -> None:
        raw = f"{actor_scope}\x1f{operation}\x1f{key}".encode()
        lock_key = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    async def lock_tenant(self, tenant_id: int) -> None:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": tenant_id})

    async def idempotency(self, actor_scope: str, operation: str, key: str):
        result = await self.session.execute(
            select(TherapistWorkflowIdempotencyModel).where(
                TherapistWorkflowIdempotencyModel.actor_scope == actor_scope,
                TherapistWorkflowIdempotencyModel.operation == operation,
                TherapistWorkflowIdempotencyModel.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def invitation_for_update(self, invitation_id: str):
        result = await self.session.execute(
            select(TherapistInvitationModel)
            .options(load_only(
                TherapistInvitationModel.invitation_id,
                TherapistInvitationModel.tenant_id,
                TherapistInvitationModel.phone_digest,
                TherapistInvitationModel.phone_digest_key_id,
                TherapistInvitationModel.phone_masked,
                TherapistInvitationModel.code_digest,
                TherapistInvitationModel.code_digest_key_id,
                TherapistInvitationModel.expires_at,
                TherapistInvitationModel.status,
                TherapistInvitationModel.failed_attempts,
                TherapistInvitationModel.issued_by,
                TherapistInvitationModel.issued_at,
                TherapistInvitationModel.activated_at,
                TherapistInvitationModel.revoked_at,
                TherapistInvitationModel.version,
            ))
            .where(TherapistInvitationModel.invitation_id == invitation_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def invitation_tenant_id(self, invitation_id: str) -> int | None:
        result = await self.session.execute(
            select(TherapistInvitationModel.tenant_id).where(
                TherapistInvitationModel.invitation_id == invitation_id
            )
        )
        return result.scalar_one_or_none()

    async def add_invitation(self, value: TherapistInvitationModel) -> None:
        await self.session.execute(
            insert(TherapistInvitationModel).inline().values(
                invitation_id=value.invitation_id,
                tenant_id=value.tenant_id,
                phone_ciphertext=value.phone_ciphertext,
                phone_encryption_key_id=value.phone_encryption_key_id,
                phone_digest=value.phone_digest,
                phone_digest_key_id=value.phone_digest_key_id,
                phone_masked=value.phone_masked,
                code_digest=value.code_digest,
                code_digest_key_id=value.code_digest_key_id,
                expires_at=value.expires_at,
                status=value.status,
                failed_attempts=value.failed_attempts,
                issued_by=value.issued_by,
                issued_at=value.issued_at,
                version=value.version,
            )
        )

    async def add_therapist_user(
        self,
        *,
        phone: str,
        password_hash: str,
        tenant_id: int,
    ) -> int:
        result = await self.session.execute(
            text(
                'INSERT INTO public."user"(phone,password_hash,role,status,tenant_id) '
                "VALUES(:phone,:password_hash,'therapist','active',:tenant_id) RETURNING id"
            ),
            {
                "phone": phone,
                "password_hash": password_hash,
                "tenant_id": tenant_id,
            },
        )
        return int(result.scalar_one())

    async def add_profile(self, value: TherapistProfileModel) -> None:
        await self.session.execute(
            insert(TherapistProfileModel).inline().values(
                therapist_id=value.therapist_id,
                user_id=value.user_id,
                tenant_id=value.tenant_id,
                invitation_id=value.invitation_id,
                status=value.status,
                capacity_limit=value.capacity_limit,
                active_case_count=value.active_case_count,
                current_revision_no=value.current_revision_no,
                totp_secret_ciphertext=value.totp_secret_ciphertext,
                totp_encryption_key_id=value.totp_encryption_key_id,
                totp_enabled=value.totp_enabled,
                activated_at=value.activated_at,
                created_at=value.created_at,
                updated_at=value.updated_at,
                version=value.version,
            )
        )

    async def invitation_by_phone_candidates(self, tenant_id: int, digests: tuple[tuple[str, str], ...]):
        if not digests:
            return ()
        clauses = [
            (TherapistInvitationModel.phone_digest_key_id == key_id)
            & (TherapistInvitationModel.phone_digest == digest)
            for key_id, digest in digests
        ]
        statement = select(TherapistInvitationModel.invitation_id).where(
            TherapistInvitationModel.tenant_id == tenant_id,
            TherapistInvitationModel.status == "INVITED",
            clauses[0] if len(clauses) == 1 else __import__("sqlalchemy").or_(*clauses),
        )
        result = await self.session.execute(statement.with_for_update())
        return tuple(result.scalars())

    async def list_invitations(self, tenant_id: int, *, status: str | None = None, cursor=None, limit: int = 20):
        statement = select(
            TherapistInvitationModel.invitation_id,
            TherapistInvitationModel.phone_masked,
            TherapistInvitationModel.status,
            TherapistInvitationModel.expires_at,
            TherapistInvitationModel.issued_at,
            TherapistInvitationModel.activated_at,
            TherapistInvitationModel.revoked_at,
            TherapistInvitationModel.version,
        ).where(TherapistInvitationModel.tenant_id == tenant_id)
        if status:
            statement = statement.where(TherapistInvitationModel.status == status)
        if cursor is not None:
            issued_at, invitation_id = cursor
            statement = statement.where(or_(
                TherapistInvitationModel.issued_at < issued_at,
                and_(
                    TherapistInvitationModel.issued_at == issued_at,
                    TherapistInvitationModel.invitation_id > invitation_id,
                ),
            ))
        result = await self.session.execute(
            statement.order_by(TherapistInvitationModel.issued_at.desc(), TherapistInvitationModel.invitation_id).limit(limit + 1)
        )
        return tuple(result.mappings())

    async def profile_for_update(self, therapist_id: str):
        result = await self.session.execute(
            select(TherapistProfileModel)
            .options(load_only(*self._profile_business_columns()))
            .where(TherapistProfileModel.therapist_id == therapist_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def current_profile_for_user(self, user_id: int, *, for_update: bool = False):
        statement = (
            select(TherapistProfileModel)
            .options(load_only(*self._profile_business_columns()))
            .where(TherapistProfileModel.user_id == user_id)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    @staticmethod
    def _profile_business_columns():
        excluded = {
            "totp_secret_ciphertext",
            "totp_encryption_key_id",
            "totp_enabled",
        }
        return tuple(
            getattr(TherapistProfileModel, column.name)
            for column in TherapistProfileModel.__table__.columns
            if column.name not in excluded
        )

    async def profile_for_user_summary(self, user_id: int):
        result = await self.session.execute(
            select(
                TherapistProfileModel.therapist_id,
                TherapistProfileModel.tenant_id,
                TherapistProfileModel.display_name,
                TherapistProfileModel.practice_summary,
                TherapistProfileModel.service_tags,
                TherapistProfileModel.status,
                TherapistProfileModel.capacity_limit,
                TherapistProfileModel.active_case_count,
                TherapistProfileModel.qualification_valid_until,
                TherapistProfileModel.current_revision_no,
                TherapistProfileModel.version,
                TherapistProfileModel.updated_at,
            ).where(TherapistProfileModel.user_id == user_id)
        )
        return result.mappings().one_or_none()

    async def self_profile_private(self, user_id: int):
        result = await self.session.execute(
            select(
                TherapistProfileModel.therapist_id,
                TherapistProfileModel.tenant_id,
                TherapistProfileModel.real_name_ciphertext,
                TherapistProfileModel.real_name_encryption_key_id,
                TherapistProfileModel.display_name,
                TherapistProfileModel.practice_summary,
                TherapistProfileModel.service_tags,
                TherapistProfileModel.status,
                TherapistProfileModel.capacity_limit,
                TherapistProfileModel.active_case_count,
                TherapistProfileModel.qualification_valid_until,
                TherapistProfileModel.current_revision_no,
                TherapistProfileModel.version,
                TherapistProfileModel.updated_at,
            ).where(TherapistProfileModel.user_id == user_id)
        )
        return result.mappings().one_or_none()

    async def profile_summary(self, therapist_id: str):
        result = await self.session.execute(
            select(
                TherapistProfileModel.therapist_id,
                TherapistProfileModel.tenant_id,
                TherapistProfileModel.display_name,
                TherapistProfileModel.practice_summary,
                TherapistProfileModel.service_tags,
                TherapistProfileModel.status,
                TherapistProfileModel.capacity_limit,
                TherapistProfileModel.active_case_count,
                TherapistProfileModel.qualification_valid_until,
                TherapistProfileModel.current_revision_no,
                TherapistProfileModel.version,
                TherapistProfileModel.updated_at,
            ).where(TherapistProfileModel.therapist_id == therapist_id)
        )
        return result.mappings().one_or_none()

    async def list_profiles(self, tenant_id: int, *, status: str | None = None, cursor: str | None = None, limit: int = 20):
        statement = select(
            TherapistProfileModel.therapist_id,
            TherapistProfileModel.tenant_id,
            TherapistProfileModel.display_name,
            TherapistProfileModel.practice_summary,
            TherapistProfileModel.status,
            TherapistProfileModel.service_tags,
            TherapistProfileModel.capacity_limit,
            TherapistProfileModel.active_case_count,
            TherapistProfileModel.qualification_valid_until,
            TherapistProfileModel.current_revision_no,
            TherapistProfileModel.version,
            TherapistProfileModel.updated_at,
        ).where(TherapistProfileModel.tenant_id == tenant_id)
        if status:
            statement = statement.where(TherapistProfileModel.status == status)
        if cursor is not None:
            statement = statement.where(TherapistProfileModel.therapist_id > cursor)
        result = await self.session.execute(statement.order_by(TherapistProfileModel.therapist_id).limit(limit + 1))
        return tuple(result.mappings())

    async def review_item_for_update(self, review_item_id: str):
        result = await self.session.execute(
            select(TherapistReviewItemModel)
            .where(TherapistReviewItemModel.review_item_id == review_item_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def review_items(self, *, kind: str | None = None, status: str | None = None, cursor=None, limit: int = 20):
        statement = select(
            TherapistReviewItemModel.review_item_id,
            TherapistReviewItemModel.therapist_id,
            TherapistReviewItemModel.revision_id,
            TherapistReviewItemModel.qualification_version_id,
            TherapistReviewItemModel.review_kind,
            TherapistReviewItemModel.status,
            TherapistReviewItemModel.created_at,
            TherapistReviewItemModel.claimed_at,
            TherapistReviewItemModel.version,
        )
        if kind:
            statement = statement.where(TherapistReviewItemModel.review_kind == kind)
        if status:
            statement = statement.where(TherapistReviewItemModel.status == status)
        if cursor is not None:
            created_at, review_item_id = cursor
            statement = statement.where(or_(
                TherapistReviewItemModel.created_at > created_at,
                and_(
                    TherapistReviewItemModel.created_at == created_at,
                    TherapistReviewItemModel.review_item_id > review_item_id,
                ),
            ))
        result = await self.session.execute(statement.order_by(TherapistReviewItemModel.created_at, TherapistReviewItemModel.review_item_id).limit(limit + 1))
        return tuple(result.mappings())

    async def current_qualification(self, therapist_id: str):
        result = await self.session.execute(
            select(TherapistQualificationVersionModel)
            .where(TherapistQualificationVersionModel.therapist_id == therapist_id)
            .order_by(TherapistQualificationVersionModel.version_no.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def certificate_digest_candidates(
        self,
        therapist_id: str,
        digests: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        if not digests:
            return ()
        clauses = [
            (TherapistQualificationVersionModel.certificate_digest_key_id == key_id)
            & (TherapistQualificationVersionModel.certificate_no_digest == digest)
            for key_id, digest in digests
        ]
        result = await self.session.execute(
            select(TherapistQualificationVersionModel.qualification_version_id).where(
                TherapistQualificationVersionModel.therapist_id == therapist_id,
                clauses[0]
                if len(clauses) == 1
                else __import__("sqlalchemy").or_(*clauses),
            )
        )
        return tuple(result.scalars())

    async def qualification_for_revision(self, therapist_id: str, revision_id: str):
        result = await self.session.execute(
            select(TherapistQualificationVersionModel)
            .join(
                TherapistRevisionQualificationModel,
                (TherapistRevisionQualificationModel.therapist_id == TherapistQualificationVersionModel.therapist_id)
                & (TherapistRevisionQualificationModel.qualification_version_id == TherapistQualificationVersionModel.qualification_version_id),
            )
            .where(
                TherapistRevisionQualificationModel.therapist_id == therapist_id,
                TherapistRevisionQualificationModel.revision_id == revision_id,
                TherapistRevisionQualificationModel.position == 1,
            )
        )
        return result.scalar_one_or_none()

    async def qualification_attachment_ids(self, qualification_version_id: str) -> tuple[str, ...]:
        result = await self.session.execute(
            select(TherapistQualificationAttachmentModel.private_file_id)
            .where(TherapistQualificationAttachmentModel.qualification_version_id == qualification_version_id)
            .order_by(TherapistQualificationAttachmentModel.slot)
        )
        return tuple(result.scalars())

    async def qualification_files_are_clean(
        self,
        qualification_version_id: str,
        expected_count: int,
    ) -> bool:
        value = await self.session.scalar(
            text(
                "SELECT public.lock_therapist_qualification_review_files_v1("
                ":qualification_version_id,:expected_count)"
            ),
            {
                "qualification_version_id": qualification_version_id,
                "expected_count": expected_count,
            },
        )
        return value is True

    async def has_approved_renewal_since(self, therapist_id: str, since) -> bool:
        result = await self.session.execute(
            select(TherapistReviewDecisionModel.decision_id)
            .join(
                TherapistReviewItemModel,
                TherapistReviewItemModel.review_item_id == TherapistReviewDecisionModel.review_item_id,
            )
            .where(
                TherapistReviewDecisionModel.therapist_id == therapist_id,
                TherapistReviewDecisionModel.decision == "APPROVED",
                TherapistReviewDecisionModel.created_at > since,
                TherapistReviewItemModel.review_kind == "RENEWAL",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def qualifications(self, therapist_id: str):
        result = await self.session.execute(text(
            "SELECT q.qualification_version_id,q.qualification_type,"
            "q.certificate_no_masked AS masked_certificate_no,q.issuer_name,"
            "q.valid_from,q.valid_until,q.attachment_count,q.version_no,"
            "CASE "
            "WHEN p.current_qualification_version_id=q.qualification_version_id "
            "AND p.status IN ('APPROVED_ACTIVE','SUSPENDED','EXITED') THEN 'APPROVED' "
            "WHEN EXISTS(SELECT 1 FROM public.therapist_qualification_version successor "
            "WHERE successor.previous_version_id=q.qualification_version_id "
            "AND p.current_qualification_version_id=successor.qualification_version_id) THEN 'SUPERSEDED' "
            "WHEN EXISTS(SELECT 1 FROM public.therapist_review_decision d "
            "WHERE d.therapist_id=q.therapist_id "
            "AND d.qualification_outcomes->>q.qualification_version_id::text='REJECTED') THEN 'REJECTED' "
            "ELSE 'SUBMITTED' END AS derived_review_status "
            "FROM public.therapist_qualification_version q "
            "JOIN public.therapist_profile p ON p.therapist_id=q.therapist_id "
            "WHERE q.therapist_id=:therapist_id ORDER BY q.version_no"
        ), {"therapist_id": therapist_id})
        return tuple(result.mappings())

    async def qualification_ids_for_revision(self, therapist_id: str, revision_id: str):
        result = await self.session.execute(
            select(TherapistRevisionQualificationModel.qualification_version_id)
            .where(
                TherapistRevisionQualificationModel.therapist_id == therapist_id,
                TherapistRevisionQualificationModel.revision_id == revision_id,
            )
            .order_by(TherapistRevisionQualificationModel.position)
        )
        return tuple(result.scalars())

    async def correction(self, therapist_id: str):
        result = await self.session.execute(text(
            "SELECT d.decision_id,d.review_item_id,d.correction_fields,d.reason_code,"
            "i.version AS review_version "
            "FROM public.therapist_review_decision d "
            "JOIN public.therapist_review_item i ON i.review_item_id=d.review_item_id "
            "WHERE d.therapist_id=:therapist_id AND d.decision='NEEDS_CORRECTION' "
            "ORDER BY d.created_at DESC,d.decision_id DESC LIMIT 1"
        ), {"therapist_id": therapist_id})
        row = result.mappings().one_or_none()
        if row is None:
            return None
        fields = tuple(row["correction_fields"] or ())
        return {
            "decision_id": row["decision_id"],
            "review_item_id": row["review_item_id"],
            "profile_fields": tuple(value for value in fields if not value.startswith("qualification:")),
            "qualification_targets": tuple(value.split(":", 1)[1] for value in fields if value.startswith("qualification:")),
            "reason_code": row["reason_code"],
            "review_version": row["review_version"],
        }

    async def revisions(self, therapist_id: str):
        result = await self.session.execute(
            select(
                TherapistProfileRevisionModel.revision_id,
                TherapistProfileRevisionModel.revision_no,
                TherapistProfileRevisionModel.created_at,
            )
            .where(TherapistProfileRevisionModel.therapist_id == therapist_id)
            .order_by(TherapistProfileRevisionModel.revision_no)
        )
        return tuple(result.mappings())

    async def current_review_item(self, therapist_id: str):
        result = await self.session.execute(
            select(
                TherapistReviewItemModel.review_item_id,
                TherapistReviewItemModel.therapist_id,
                TherapistReviewItemModel.revision_id,
                TherapistReviewItemModel.qualification_version_id,
                TherapistReviewItemModel.review_kind,
                TherapistReviewItemModel.status,
                TherapistReviewItemModel.created_at,
                TherapistReviewItemModel.claimed_at,
                TherapistReviewItemModel.version,
            )
            .where(TherapistReviewItemModel.therapist_id == therapist_id)
            .order_by(TherapistReviewItemModel.created_at.desc(), TherapistReviewItemModel.review_item_id.desc())
            .limit(1)
        )
        return result.mappings().one_or_none()

    async def readiness(self, tenant_id: int, *, for_update: bool = False):
        statement = select(InstitutionServiceReadinessModel).where(
            InstitutionServiceReadinessModel.tenant_id == tenant_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def readiness_evidence(self, tenant_id: int, *, cursor: int | None = None, limit: int = 20):
        statement = select(
            ReadinessEvidenceModel.tenant_id,
            ReadinessEvidenceModel.evidence_version,
            ReadinessEvidenceModel.readiness_status,
            ReadinessEvidenceModel.reason_codes,
            ReadinessEvidenceModel.qualified_therapist_count,
            ReadinessEvidenceModel.computed_at,
            ReadinessEvidenceModel.input_digest,
            ReadinessEvidenceModel.result_digest,
        ).where(ReadinessEvidenceModel.tenant_id == tenant_id)
        if cursor is not None:
            statement = statement.where(ReadinessEvidenceModel.evidence_version < cursor)
        result = await self.session.execute(
            statement.order_by(ReadinessEvidenceModel.evidence_version.desc()).limit(limit + 1)
        )
        return tuple(result.mappings())

    async def readiness_evidence_for_trigger(self, trigger_event_id: str):
        result = await self.session.execute(
            select(ReadinessEvidenceModel).where(
                ReadinessEvidenceModel.trigger_event_id == trigger_event_id
            )
        )
        return result.scalar_one_or_none()

    async def readiness_guard(self, tenant_id: int):
        result = await self.session.execute(
            text(
                "SELECT tenant_id,tenant_public_id,tenant_status,application_id,"
                "application_version,institution_type,service_tags,license_versions,"
                "current_therapist_versions,next_expiry_at "
                "FROM public.institution_readiness_guard_v1 WHERE tenant_id=:tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
        return result.mappings().one_or_none()

    async def readiness_guard_by_public_id(self, tenant_public_id: str):
        result = await self.session.execute(
            text(
                "SELECT tenant_id,tenant_public_id,tenant_status,application_id,"
                "application_version,institution_type,service_tags,license_versions,"
                "current_therapist_versions,next_expiry_at "
                "FROM public.institution_readiness_guard_v1 "
                "WHERE tenant_public_id=:tenant_public_id"
            ),
            {"tenant_public_id": tenant_public_id},
        )
        return result.mappings().one_or_none()

    async def lock_clean_files(self, *, file_ids: tuple[str, ...], owner_user_id: int):
        result = await self.session.execute(
            text(
                "SELECT file_id,bound_application_id "
                "FROM public.lock_therapist_qualification_files_v1("
                ":file_ids,:owner_user_id) ORDER BY file_id"
            ),
            {"file_ids": list(file_ids), "owner_user_id": owner_user_id},
        )
        return tuple(result.all())

    async def outbox_for_update(self, event_id: str):
        result = await self.session.execute(
            select(TherapistWorkflowOutboxModel)
            .where(TherapistWorkflowOutboxModel.event_id == event_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def claim_outbox(self, *, now, lease_owner: str):
        result = await self.session.execute(
            select(TherapistWorkflowOutboxModel)
            .where(TherapistWorkflowOutboxModel.status == "PENDING")
            .order_by(TherapistWorkflowOutboxModel.created_at, TherapistWorkflowOutboxModel.event_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.status = "PROCESSING"
            row.attempts += 1
            row.processing_at = now
            row.lease_owner = lease_owner
            row.version += 1
        return row

    async def add(self, value) -> None:
        self.session.add(value)
        await self.session.flush()

    async def add_audit(self, value: TherapistWorkflowAuditModel) -> None:
        await self.session.execute(insert(TherapistWorkflowAuditModel).inline().values(
            actor_scope=value.actor_scope,
            action=value.action,
            object_id=value.object_id,
            result=value.result,
            reason_code=value.reason_code,
            request_id=value.request_id,
            preimage_digest=value.preimage_digest,
            postimage_digest=value.postimage_digest,
            created_at=value.created_at,
        ))

    async def add_all(self, values) -> None:
        self.session.add_all(list(values))
        await self.session.flush()

    async def add_revision_bundle(
        self,
        revision,
        qualification,
        link,
        attachments,
    ) -> None:
        self.session.add(revision)
        await self.session.flush()
        self.session.add(qualification)
        await self.session.flush()
        self.session.add_all([link, *attachments])
        await self.session.flush()


__all__ = [
    "TherapistQualificationRepository",
    "TherapistInvitationModel",
    "TherapistProfileModel",
    "TherapistProfileRevisionModel",
    "TherapistRevisionQualificationModel",
    "TherapistQualificationVersionModel",
    "TherapistQualificationAttachmentModel",
    "TherapistReviewItemModel",
    "TherapistReviewDecisionModel",
    "TherapistStatusDecisionModel",
    "InstitutionServiceReadinessModel",
    "ReadinessEvidenceModel",
    "TherapistWorkflowIdempotencyModel",
    "TherapistWorkflowAuditModel",
    "TherapistWorkflowOutboxModel",
    "TherapistWorkflowDeliveryModel",
]
