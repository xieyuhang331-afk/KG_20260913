from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import and_, bindparam, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID

from app.modules.member_enrollment.models import (
    ConsentDocumentRenditionModel,
    ConsentDocumentVersionModel,
    ConsentRecordModel,
    ControlledMemberBootstrapModel,
    IdentitySubjectClaimRegistryModel,
    MemberEnrollmentAuditModel,
    MemberEnrollmentDeliveryModel,
    MemberEnrollmentIdempotencyModel,
    MemberEnrollmentOutboxModel,
    MemberIdentityRevisionModel,
    MemberIdentityPiiAccessModel,
    MemberIdentityReviewDecisionModel,
    MemberIdentityVerificationModel,
    MemberServiceInvitationModel,
    PrimaryTherapistAssignmentModel,
    ProxyGrantModel,
    ServiceCaseModel,
    ServiceEnrollmentModel,
)


class MemberEnrollmentRepository:
    """Explicit-column persistence boundary for the five Slice 3 identities."""

    _COLLECTION_SNAPSHOT_SPECS = {
        "member_identity_revision": (
            "IDENTITY_REVISION", "verification_id", "revision_id"
        ),
        "member_identity_review_decision": (
            "REVIEW_DECISION", "verification_id", "decision_id"
        ),
        "member_identity_pii_access": (
            "PII_ACCESS", "verification_id", "access_id"
        ),
        "identity_subject_claim_registry": (
            "IDENTITY_REGISTRY", "slice3_revision_id", "claim_id"
        ),
        "consent_document_rendition": (
            "CONSENT_RENDITION", "document_version_id", "rendition_id"
        ),
        "consent_record": (
            "CONSENT_RECORD_SET", "enrollment_id", "consent_record_id"
        ),
    }

    def __init__(self, session):
        self.session = session

    @staticmethod
    def _json_value(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, bytes):
            return "\\x" + value.hex()
        if isinstance(value, datetime):
            if value.microsecond == 0:
                return value.isoformat(timespec="seconds")
            rendered = value.isoformat(timespec="microseconds")
            fraction_start = rendered.index(".") + 1
            fraction = rendered[fraction_start : fraction_start + 6].rstrip("0")
            return rendered[:fraction_start] + fraction + rendered[fraction_start + 6 :]
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, dict):
            return {
                str(key): MemberEnrollmentRepository._json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [MemberEnrollmentRepository._json_value(item) for item in value]
        return value

    def _plan(self) -> dict[str, object] | None:
        value = self.session.info.get("slice3-mutation-plan")
        return value if type(value) is dict else None

    def _record_expected_row(
        self, table, values: dict[str, object], *, key: dict[str, object]
    ) -> None:
        self._record_expected_named_row(table.name, values, key=key)

    def _record_expected_named_row(
        self, table_name: str, values: dict[str, object], *, key: dict[str, object]
    ) -> None:
        plan = self._plan()
        if plan is None:
            return
        rows = plan.setdefault("rows", [])
        normalized_key = self._json_value(key)
        normalized_value = self._json_value(values)
        for row in rows:
            if row["table"] == table_name and row["key"] == normalized_key:
                row["value"] = normalized_value
                return
        rows.append(
            {"table": table_name, "key": normalized_key, "value": normalized_value}
        )

    def _record_expected_insert(
        self, model, values: dict[str, object], *, key_fields: tuple[str, ...]
    ) -> None:
        table = model.__table__
        plan = self._plan()
        key = {name: values[name] for name in key_fields}
        if plan is not None:
            plan.setdefault("pre_rows", []).append(
                {"table": table.name, "key": self._json_value(key), "value": None}
            )
        expected = {
            column.name: values.get(column.name)
            for column in table.columns
            if not (column.primary_key and column.autoincrement is True and column.name not in values)
        }
        self._record_expected_row(
            table,
            expected,
            key=key,
        )

    async def _record_expected_update(
        self,
        model,
        *,
        key_field: str,
        key_value: object,
        values: dict[str, object],
    ) -> None:
        plan = self._plan()
        if plan is None:
            return
        table = model.__table__
        normalized_key = self._json_value({key_field: key_value})
        for row in plan.setdefault("rows", []):
            if row["table"] == table.name and row["key"] == normalized_key:
                expected = dict(row["value"])
                expected.update(self._json_value(values))
                row["value"] = expected
                return
        result = await self.session.execute(
            select(*table.c).where(table.c[key_field] == key_value).with_for_update()
        )
        current = result.mappings().one_or_none()
        if current is None:
            return
        plan.setdefault("pre_rows", []).append(
            {
                "table": table.name,
                "key": normalized_key,
                "value": self._json_value(dict(current)),
            }
        )
        expected = dict(current)
        expected.update(values)
        self._record_expected_row(
            table, expected, key={key_field: key_value}
        )

    async def _record_expected_collection(
        self,
        model,
        *,
        scope_field: str,
        scope_value: object,
        key_field: str,
        added_key: object | None = None,
    ) -> None:
        table = model.__table__
        spec = self._COLLECTION_SNAPSHOT_SPECS.get(table.name)
        if spec is None or spec[1:] != (scope_field, key_field):
            raise RuntimeError("Slice 3 collection snapshot specification is invalid")
        family = spec[0]
        scope = self._json_value({scope_field: scope_value})
        result = await self.session.execute(
            text(
                "SELECT public.slice3_collection_snapshot_v1("
                ":family,CAST(:scope AS jsonb))"
            ),
            {
                "family": family,
                "scope": json.dumps(scope, sort_keys=True, separators=(",", ":")),
            },
        )
        existing = sorted(str(value) for value in (result.scalar_one() or ()))
        plan = self._plan()
        if plan is None:
            return
        collections = plan.setdefault("collections", [])
        collection = next(
            (
                item
                for item in collections
                if item["table"] == table.name
                and item["scope"] == scope
                and item["key_field"] == key_field
            ),
            None,
        )
        if collection is None:
            collection = {
                "table": table.name,
                "scope": scope,
                "key_field": key_field,
                "pre_keys": existing,
                "post_keys": list(existing),
            }
            collections.append(collection)
        if added_key is not None:
            rendered = str(added_key)
            if rendered not in collection["post_keys"]:
                collection["post_keys"].append(rendered)
                collection["post_keys"].sort()

    async def lock_operation(self, actor_scope: str, operation: str, target_id: UUID, key: str) -> None:
        payload = f"slice3\x1f{actor_scope}\x1f{operation}\x1f{target_id}\x1f{key}".encode()
        lock_key = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=True)
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    async def platform_reviewer_write_currentness(self, reviewer_user_id: int):
        result = await self.session.execute(
            text(
                "SELECT * FROM public."
                "slice3_platform_reviewer_write_currentness_v1(:reviewer_user_id)"
            ),
            {"reviewer_user_id": reviewer_user_id},
        )
        return result.mappings().one_or_none()

    async def lock_active_enrollment_boundary(self, subject_member_id: UUID) -> None:
        payload = f"slice3-active-enrollment-boundary\x1f{subject_member_id}".encode()
        lock_key = int.from_bytes(
            hashlib.sha256(payload).digest()[:8], "big", signed=True
        )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    async def lock_identity_review_boundary(self, verification_id: UUID) -> None:
        payload = f"slice3-identity-review-boundary\x1f{verification_id}".encode()
        lock_key = int.from_bytes(
            hashlib.sha256(payload).digest()[:8], "big", signed=True
        )
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    async def lock_consent_boundary(self, enrollment_id: UUID) -> None:
        payload = f"slice3-consent-boundary\x1f{enrollment_id}".encode()
        lock_key = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=True)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    async def lock_identity_fingerprint(self, fingerprint: str) -> None:
        await self.session.execute(
            text("SELECT public.lock_slice3_identity_fingerprint_v1(:fingerprint)"),
            {"fingerprint": fingerprint},
        )

    async def lock_invitation_phone(self, tenant_id: int, coordination_digest: str) -> None:
        payload = f"slice3-invitation-phone\x1f{tenant_id}\x1f{coordination_digest}".encode()
        lock_key = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=True)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    async def lock_proxy_slot_boundary(self, proxy_member_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:value,31))"),
            {"value": str(proxy_member_id)},
        )

    async def invitation_for_update(self, invitation_id: UUID):
        statement = select(*MemberServiceInvitationModel.__table__.c).where(
            MemberServiceInvitationModel.invitation_id == invitation_id
        ).with_for_update()
        result = await self.session.execute(statement)
        return result.mappings().one_or_none()

    async def open_invitation_candidates(self, tenant_id: int, digests: tuple[tuple[str, str], ...]):
        if not digests:
            return ()
        clauses = [
            and_(
                MemberServiceInvitationModel.phone_digest_key_id == key_id,
                MemberServiceInvitationModel.phone_digest == digest,
            )
            for key_id, digest in digests
        ]
        result = await self.session.execute(
            select(
                MemberServiceInvitationModel.invitation_id,
                MemberServiceInvitationModel.phone_ciphertext,
                MemberServiceInvitationModel.phone_key_id,
                MemberServiceInvitationModel.phone_digest,
                MemberServiceInvitationModel.phone_digest_key_id,
            )
            .where(
                MemberServiceInvitationModel.tenant_id == tenant_id,
                MemberServiceInvitationModel.status == "INVITED",
                or_(*clauses),
            )
            .with_for_update()
        )
        return tuple(result.mappings())

    async def add_invitation(self, **values) -> None:
        self._record_expected_insert(
            MemberServiceInvitationModel, values, key_fields=("invitation_id",)
        )
        await self.session.execute(insert(MemberServiceInvitationModel).inline().values(**values))

    async def update_invitation(self, invitation_id: UUID, **values) -> None:
        await self._record_expected_update(
            MemberServiceInvitationModel,
            key_field="invitation_id",
            key_value=invitation_id,
            values=values,
        )
        await self.session.execute(
            update(MemberServiceInvitationModel)
            .where(MemberServiceInvitationModel.invitation_id == invitation_id)
            .values(**values)
        )

    async def enrollment_for_update(self, enrollment_id: UUID):
        result = await self.session.execute(
            select(*ServiceEnrollmentModel.__table__.c)
            .where(ServiceEnrollmentModel.enrollment_id == enrollment_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def review_enrollment_for_update(self, enrollment_id: UUID):
        result = await self.session.execute(
            select(
                ServiceEnrollmentModel.enrollment_id,
                ServiceEnrollmentModel.status,
                ServiceEnrollmentModel.version,
            )
            .where(ServiceEnrollmentModel.enrollment_id == enrollment_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def review_enrollment_preimage_for_update(
        self,
        *,
        verification_id: UUID,
        enrollment_id: UUID,
        reviewer_user_id: int,
    ):
        result = await self.session.execute(
            text(
                "SELECT public.slice3_review_enrollment_preimage_authority_v1("
                ":verification_id,:enrollment_id,:reviewer_user_id)"
            ).bindparams(
                bindparam("verification_id", type_=PostgreSQLUUID(as_uuid=True)),
                bindparam("enrollment_id", type_=PostgreSQLUUID(as_uuid=True)),
            ),
            {
                "verification_id": verification_id,
                "enrollment_id": enrollment_id,
                "reviewer_user_id": reviewer_user_id,
            },
        )
        preimage = result.scalar_one_or_none()
        if preimage is None:
            return None
        expected_columns = {
            column.name for column in ServiceEnrollmentModel.__table__.columns
        }
        if type(preimage) is not dict or set(preimage) != expected_columns:
            raise RuntimeError("Slice 3 review enrollment preimage is invalid")
        plan = self._plan()
        if plan is not None:
            key = self._json_value({"enrollment_id": enrollment_id})
            value = self._json_value(preimage)
            matching = [
                row
                for row in plan.setdefault("pre_rows", [])
                if row["table"] == ServiceEnrollmentModel.__table__.name
                and row["key"] == key
            ]
            if matching and any(row["value"] != value for row in matching):
                raise RuntimeError("Slice 3 review enrollment preimage changed")
            if not matching:
                plan["pre_rows"].append(
                    {
                        "table": ServiceEnrollmentModel.__table__.name,
                        "key": key,
                        "value": value,
                    }
                )
            self._record_expected_row(
                ServiceEnrollmentModel.__table__,
                preimage,
                key={"enrollment_id": enrollment_id},
            )
        return preimage

    async def case_enrollment_preimage_for_update(
        self,
        *,
        assignment_id: UUID,
        enrollment_id: UUID,
        therapist_id: UUID,
        actor_user_id: int,
    ):
        assignment_uuid = UUID(str(assignment_id))
        enrollment_uuid = UUID(str(enrollment_id))
        therapist_uuid = UUID(str(therapist_id))
        result = await self.session.execute(
            text(
                "SELECT public.slice3_case_enrollment_preimage_authority_v1("
                ":assignment_id,:enrollment_id,:therapist_id,:actor_user_id)"
            ).bindparams(
                bindparam("assignment_id", type_=PostgreSQLUUID(as_uuid=True)),
                bindparam("enrollment_id", type_=PostgreSQLUUID(as_uuid=True)),
                bindparam("therapist_id", type_=PostgreSQLUUID(as_uuid=True)),
            ),
            {
                "assignment_id": assignment_uuid,
                "enrollment_id": enrollment_uuid,
                "therapist_id": therapist_uuid,
                "actor_user_id": actor_user_id,
            },
        )
        preimage = result.scalar_one_or_none()
        if preimage is None:
            return None
        expected_columns = {
            column.name for column in ServiceEnrollmentModel.__table__.columns
        }
        if type(preimage) is not dict or set(preimage) != expected_columns:
            raise RuntimeError("Slice 3 case enrollment preimage is invalid")
        plan = self._plan()
        if plan is not None:
            key = self._json_value({"enrollment_id": enrollment_uuid})
            value = self._json_value(preimage)
            matching = [
                row
                for row in plan.setdefault("pre_rows", [])
                if row["table"] == ServiceEnrollmentModel.__table__.name
                and row["key"] == key
            ]
            if matching and any(row["value"] != value for row in matching):
                raise RuntimeError("Slice 3 case enrollment preimage changed")
            if not matching:
                plan["pre_rows"].append(
                    {
                        "table": ServiceEnrollmentModel.__table__.name,
                        "key": key,
                        "value": value,
                    }
                )
            self._record_expected_row(
                ServiceEnrollmentModel.__table__,
                preimage,
                key={"enrollment_id": enrollment_uuid},
            )
        return preimage

    async def case_enrollment_for_update(self, enrollment_id: UUID):
        result = await self.session.execute(
            select(
                ServiceEnrollmentModel.enrollment_id,
                ServiceEnrollmentModel.tenant_id,
                ServiceEnrollmentModel.subject_member_id,
                ServiceEnrollmentModel.proxy_member_id,
                ServiceEnrollmentModel.mode,
                ServiceEnrollmentModel.status,
                ServiceEnrollmentModel.service_scope_tags,
                ServiceEnrollmentModel.current_identity_verification_id,
                ServiceEnrollmentModel.current_assignment_id,
                ServiceEnrollmentModel.service_case_id,
                ServiceEnrollmentModel.version,
            )
            .where(ServiceEnrollmentModel.enrollment_id == enrollment_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def active_enrollments_for_subject(self, subject_member_id: UUID):
        result = await self.session.execute(
            select(ServiceEnrollmentModel.enrollment_id, ServiceEnrollmentModel.tenant_id)
            .where(
                ServiceEnrollmentModel.subject_member_id == subject_member_id,
                ServiceEnrollmentModel.status.in_((
                    "ACCEPTED", "IDENTITY_SUBMITTED", "INSTITUTION_CHECKED",
                    "PLATFORM_REVIEWING", "NEEDS_CORRECTION", "RESUBMITTED",
                    "IDENTITY_VERIFIED", "CONSENT_PENDING", "THERAPIST_PENDING",
                    "CASE_CREATED",
                )),
            )
            .with_for_update()
        )
        return tuple(result.mappings())

    async def add_enrollment(self, **values) -> None:
        self._record_expected_insert(
            ServiceEnrollmentModel, values, key_fields=("enrollment_id",)
        )
        await self.session.execute(insert(ServiceEnrollmentModel).inline().values(**values))

    async def update_enrollment(self, enrollment_id: UUID, **values) -> None:
        await self._record_expected_update(
            ServiceEnrollmentModel,
            key_field="enrollment_id",
            key_value=enrollment_id,
            values=values,
        )
        await self.session.execute(
            update(ServiceEnrollmentModel)
            .where(ServiceEnrollmentModel.enrollment_id == enrollment_id)
            .values(**values)
        )

    async def active_proxy_slots(self, proxy_member_id: UUID):
        result = await self.session.execute(
            select(ProxyGrantModel.slot_no)
            .where(
                ProxyGrantModel.proxy_member_id == proxy_member_id,
                ProxyGrantModel.status.in_(("CONSENT_PENDING", "ACTIVE")),
            )
            .order_by(ProxyGrantModel.slot_no)
            .with_for_update()
        )
        return tuple(result.scalars())

    async def expired_invitations_for_update(self, *, now, limit: int = 100):
        result = await self.session.execute(
            select(*MemberServiceInvitationModel.__table__.c)
            .where(
                MemberServiceInvitationModel.status == "INVITED",
                MemberServiceInvitationModel.expires_at <= now,
            )
            .order_by(MemberServiceInvitationModel.expires_at, MemberServiceInvitationModel.invitation_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return tuple(result.mappings())

    async def add_controlled_member(self, **values) -> None:
        plan = self._plan()
        if plan is not None:
            plan.setdefault("pre_rows", []).append(
                {
                    "table": "member",
                    "key": self._json_value({"member_id": values["member_id"]}),
                    "value": None,
                }
            )
        self._record_expected_named_row(
            "member",
            {
                "member_id": values["member_id"],
                "member_no": values["member_no"],
                "creation_source": "controlled_proxy_enrollment",
                "status": "created",
                "version": 1,
                "created_at": values["created_at"],
                "updated_at": values["created_at"],
            },
            key={"member_id": values["member_id"]},
        )
        await self.session.execute(
            text(
                "INSERT INTO identity.member "
                "(member_id,member_no,creation_source,status,version,created_at,updated_at) "
                "VALUES (:member_id,:member_no,'controlled_proxy_enrollment','created',1,:created_at,:created_at)"
            ),
            values,
        )

    async def add_bootstrap(self, **values) -> None:
        self._record_expected_insert(
            ControlledMemberBootstrapModel, values, key_fields=("bootstrap_id",)
        )
        await self.session.execute(insert(ControlledMemberBootstrapModel).inline().values(**values))

    async def add_proxy_grant(self, **values) -> None:
        self._record_expected_insert(ProxyGrantModel, values, key_fields=("grant_id",))
        await self.session.execute(insert(ProxyGrantModel).inline().values(**values))

    async def proxy_grant_for_update(self, grant_id: UUID):
        result = await self.session.execute(
            select(*ProxyGrantModel.__table__.c)
            .where(ProxyGrantModel.grant_id == grant_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def proxy_grant_by_enrollment_for_update(self, enrollment_id: UUID):
        result = await self.session.execute(
            select(*ProxyGrantModel.__table__.c)
            .where(ProxyGrantModel.enrollment_id == enrollment_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def update_proxy_grant(self, grant_id: UUID, **values) -> None:
        await self._record_expected_update(
            ProxyGrantModel,
            key_field="grant_id",
            key_value=grant_id,
            values=values,
        )
        await self.session.execute(
            update(ProxyGrantModel).where(ProxyGrantModel.grant_id == grant_id).values(**values)
        )

    async def verification_for_update(self, verification_id: UUID):
        result = await self.session.execute(
            select(*MemberIdentityVerificationModel.__table__.c)
            .where(MemberIdentityVerificationModel.verification_id == verification_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def verification_by_enrollment_for_update(self, enrollment_id: UUID):
        result = await self.session.execute(
            select(*MemberIdentityVerificationModel.__table__.c)
            .where(MemberIdentityVerificationModel.enrollment_id == enrollment_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def case_verification(self, enrollment_id: UUID):
        result = await self.session.execute(
            select(
                MemberIdentityVerificationModel.verification_id,
                MemberIdentityVerificationModel.enrollment_id,
                MemberIdentityVerificationModel.current_revision_id,
                MemberIdentityVerificationModel.status,
                MemberIdentityVerificationModel.version,
            ).where(
                MemberIdentityVerificationModel.enrollment_id == enrollment_id
            )
        )
        return result.mappings().one_or_none()

    async def enrollment_by_verification_for_update(self, verification_id: UUID):
        result = await self.session.execute(
            select(*ServiceEnrollmentModel.__table__.c)
            .join(
                MemberIdentityVerificationModel,
                MemberIdentityVerificationModel.enrollment_id == ServiceEnrollmentModel.enrollment_id,
            )
            .where(MemberIdentityVerificationModel.verification_id == verification_id)
            .with_for_update(of=ServiceEnrollmentModel)
        )
        return result.mappings().one_or_none()

    async def add_verification(self, **values) -> None:
        self._record_expected_insert(
            MemberIdentityVerificationModel, values, key_fields=("verification_id",)
        )
        await self.session.execute(insert(MemberIdentityVerificationModel).inline().values(**values))

    async def update_verification(self, verification_id: UUID, **values) -> None:
        await self._record_expected_update(
            MemberIdentityVerificationModel,
            key_field="verification_id",
            key_value=verification_id,
            values=values,
        )
        await self.session.execute(
            update(MemberIdentityVerificationModel)
            .where(MemberIdentityVerificationModel.verification_id == verification_id)
            .values(**values)
        )

    async def add_identity_revision(self, **values) -> None:
        await self._record_expected_collection(
            MemberIdentityRevisionModel,
            scope_field="verification_id",
            scope_value=values["verification_id"],
            key_field="revision_id",
            added_key=values["revision_id"],
        )
        self._record_expected_insert(
            MemberIdentityRevisionModel, values, key_fields=("revision_id",)
        )
        await self.session.execute(insert(MemberIdentityRevisionModel).inline().values(**values))

    async def current_identity_revision(self, verification_id: UUID, revision_id: UUID):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_identity_revision_summary_v1("
                ":verification_id,:revision_id)"
            ),
            {"verification_id": verification_id, "revision_id": revision_id},
        )
        return result.mappings().one_or_none()

    async def identity_revision_for_update(self, verification_id: UUID, revision_id: UUID):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_identity_revision_correction_v1("
                ":verification_id,:revision_id)"
            ),
            {"verification_id": verification_id, "revision_id": revision_id},
        )
        return result.mappings().one_or_none()

    async def latest_correction_decision(self, verification_id: UUID):
        result = await self.session.execute(
            select(
                MemberIdentityReviewDecisionModel.decision_id,
                MemberIdentityReviewDecisionModel.correction_fields,
                MemberIdentityReviewDecisionModel.created_at,
            ).where(
                MemberIdentityReviewDecisionModel.verification_id == verification_id,
                MemberIdentityReviewDecisionModel.decision == "NEEDS_CORRECTION",
            ).order_by(MemberIdentityReviewDecisionModel.created_at.desc()).limit(1)
        )
        return result.mappings().one_or_none()

    async def add_review_decision(self, **values) -> None:
        await self._record_expected_collection(
            MemberIdentityReviewDecisionModel,
            scope_field="verification_id",
            scope_value=values["verification_id"],
            key_field="decision_id",
            added_key=values["decision_id"],
        )
        self._record_expected_insert(
            MemberIdentityReviewDecisionModel, values, key_fields=("decision_id",)
        )
        await self.session.execute(insert(MemberIdentityReviewDecisionModel).inline().values(**values))

    async def add_pii_access(self, **values) -> None:
        await self._record_expected_collection(
            MemberIdentityPiiAccessModel,
            scope_field="verification_id",
            scope_value=values["verification_id"],
            key_field="access_id",
            added_key=values["access_id"],
        )
        self._record_expected_insert(
            MemberIdentityPiiAccessModel, values, key_fields=("access_id",)
        )
        await self.session.execute(insert(MemberIdentityPiiAccessModel).inline().values(**values))

    async def failed_step_up_count(self, reviewer_user_id: int, *, since) -> int:
        result = await self.session.execute(
            select(text("count(*)"))
            .select_from(MemberIdentityPiiAccessModel)
            .where(
                MemberIdentityPiiAccessModel.reviewer_user_id == reviewer_user_id,
                MemberIdentityPiiAccessModel.status == "ISSUED",
                MemberIdentityPiiAccessModel.consumed_at.is_(None),
                MemberIdentityPiiAccessModel.issued_at >= since,
            )
        )
        return int(result.scalar_one())

    async def consume_pii_access(self, access_id: UUID, *, consumed_at, postimage_digest: str) -> bool:
        result = await self.session.execute(
            update(MemberIdentityPiiAccessModel)
            .where(
                MemberIdentityPiiAccessModel.access_id == access_id,
                MemberIdentityPiiAccessModel.status == "ISSUED",
                MemberIdentityPiiAccessModel.version == 1,
            )
            .values(
                status="CONSUMED",
                consumed_at=consumed_at,
                postimage_digest=postimage_digest,
                version=2,
            )
        )
        return result.rowcount == 1

    async def step_up_access_for_update(self, access_id: UUID):
        result = await self.session.execute(
            select(*MemberIdentityPiiAccessModel.__table__.c)
            .where(MemberIdentityPiiAccessModel.access_id == access_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def canonical_step_up_access_for_update(
        self,
        *,
        verification_id: UUID,
        revision_id: UUID,
        reviewer_user_id: int,
        access_token_digest: str,
        currentness_digest: str,
        now,
    ):
        result = await self.session.execute(
            select(*MemberIdentityPiiAccessModel.__table__.c)
            .where(
                MemberIdentityPiiAccessModel.verification_id == verification_id,
                MemberIdentityPiiAccessModel.current_revision_id == revision_id,
                MemberIdentityPiiAccessModel.reviewer_user_id == reviewer_user_id,
                MemberIdentityPiiAccessModel.status == "CONSUMED",
                MemberIdentityPiiAccessModel.access_token_digest == access_token_digest,
                MemberIdentityPiiAccessModel.currentness_digest == currentness_digest,
                MemberIdentityPiiAccessModel.expires_at >= now,
            )
            .order_by(MemberIdentityPiiAccessModel.consumed_at.desc())
            .limit(2)
            .with_for_update()
        )
        rows = result.mappings().all()
        return rows[0] if len(rows) == 1 else None

    async def reviewer_step_up_budget(
        self, *, proof_values, credential_proof_digest: str
    ):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_reviewer_step_up_budget_v1("
                ":verification_id,:current_revision_id,:reviewer_user_id,:actor_scope,"
                ":idempotency_key,:request_id,:request_digest,:access_token_digest,"
                ":currentness_digest,:reason_code,:password_valid,:user_version,"
                ":user_updated_at,:proof_issued_at,:proof_expires_at,"
                ":credential_proof_digest)"
            ),
            {**dict(proof_values), "credential_proof_digest": credential_proof_digest},
        )
        row = result.mappings().one_or_none()
        if row is not None and row["audit_action"] is not None:
            await self.add_audit(
                actor_scope=proof_values["actor_scope"],
                action=row["audit_action"],
                object_id=proof_values["verification_id"],
                result="REJECTED",
                reason_code=row["error_code"],
                request_id=proof_values["request_id"],
                preimage_digest=row["credential_proof_marker"],
                postimage_digest=row["audit_postimage_digest"],
                created_at=row["audit_created_at"],
            )
        return row

    async def reviewer_claim_is_current(
        self, verification_id: UUID, reviewer_user_id: int
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.slice3_reviewer_claim_authority_v1("
                ":verification_id,:reviewer_user_id)"
            ),
            {
                "verification_id": verification_id,
                "reviewer_user_id": reviewer_user_id,
            },
        )
        return result.scalar_one() is True

    async def reviewer_pii(
        self,
        *,
        access_id: UUID,
        nonce: UUID,
        verification_id: UUID,
        reviewer_user_id: int,
        revision_id: UUID,
        access_token_digest: str,
        currentness_digest: str,
        request_id: UUID,
        actor_scope: str,
        request_digest: str,
        reason_code: str,
        preimage_digest: str,
        credential_proof_marker: str,
        consumed_at,
        postimage_digest: str,
    ):
        await self._record_expected_update(
            MemberIdentityPiiAccessModel,
            key_field="access_id",
            key_value=access_id,
            values={
                "status": "CONSUMED",
                "consumed_at": consumed_at,
                "postimage_digest": postimage_digest,
                "version": 2,
            },
        )
        plan = self._plan()
        audit_key = {
            "request_id": request_id,
            "action": "IDENTITY_PII_ACCESSED",
            "object_id": verification_id,
        }
        if plan is not None:
            plan.setdefault("pre_rows", []).append(
                {
                    "table": "member_enrollment_audit",
                    "key": self._json_value(audit_key),
                    "value": None,
                }
            )
        self._record_expected_named_row(
            "member_enrollment_audit",
            {
                "actor_scope": actor_scope,
                "action": "IDENTITY_PII_ACCESSED",
                "object_id": verification_id,
                "result": "SUCCESS",
                "reason_code": reason_code,
                "request_id": request_id,
                "preimage_digest": credential_proof_marker,
                "postimage_digest": postimage_digest,
                "created_at": consumed_at,
            },
            key=audit_key,
        )
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_reviewer_pii_v1("
                ":access_id,:nonce,:verification_id,:reviewer_user_id,:revision_id,"
                ":access_token_digest,:currentness_digest,:request_id,:actor_scope,"
                ":request_digest,:preimage_digest,:credential_proof_marker,"
                ":postimage_digest,:consumed_at)"
            ),
            {
                "access_id": access_id,
                "nonce": nonce,
                "verification_id": verification_id,
                "reviewer_user_id": reviewer_user_id,
                "revision_id": revision_id,
                "access_token_digest": access_token_digest,
                "currentness_digest": currentness_digest,
                "request_id": request_id,
                "actor_scope": actor_scope,
                "request_digest": request_digest,
                "preimage_digest": preimage_digest,
                "credential_proof_marker": credential_proof_marker,
                "postimage_digest": postimage_digest,
                "consumed_at": consumed_at,
            },
        )
        return result.mappings().one_or_none()

    async def institution_decision(self, decision_id: UUID):
        result = await self.session.execute(
            select(
                MemberIdentityReviewDecisionModel.decision_id,
                MemberIdentityReviewDecisionModel.verification_id,
                MemberIdentityReviewDecisionModel.revision_id,
                MemberIdentityReviewDecisionModel.phase,
                MemberIdentityReviewDecisionModel.decision,
                MemberIdentityReviewDecisionModel.attestation_code,
                MemberIdentityReviewDecisionModel.evidence_digest,
            ).where(MemberIdentityReviewDecisionModel.decision_id == decision_id)
        )
        return result.mappings().one_or_none()

    async def claim_or_reuse_identity_subject(self, **values) -> tuple[str, UUID | None]:
        result = await self.session.execute(
            text(
                "SELECT outcome,resolved_claim_id,resolved_claim FROM "
                "identity.slice7_identity_claim_reuse_v2("
                ":claim_id,:member_id,:identity_fingerprint,:fingerprint_key_id,"
                ":slice3_revision_id,:slice3_decision_id,:source_facts_version,"
                ":source_evidence_digest,:represented_elder_eligible,:claimed_at)"
            ),
            values,
        )
        row = result.mappings().one()
        outcome = str(row["outcome"])
        resolved_claim_id = row["resolved_claim_id"]
        if outcome == "CREATE_ALLOWED":
            await self.claim_identity_subject(**values)
            return "CREATED", values["claim_id"]
        if outcome == "REUSED":
            resolved_claim = row["resolved_claim"]
            expected_fields = {
                column.name for column in IdentitySubjectClaimRegistryModel.__table__.columns
            }
            if not isinstance(resolved_claim, dict) or set(resolved_claim) != expected_fields:
                raise RuntimeError("Slice 7 identity claim preimage is invalid")
            existing_revision_id = resolved_claim.get("slice3_revision_id")
            if existing_revision_id is None:
                raise RuntimeError("Slice 7 identity claim preimage is invalid")
            await self._record_expected_collection(
                IdentitySubjectClaimRegistryModel,
                scope_field="slice3_revision_id",
                scope_value=UUID(str(existing_revision_id)),
                key_field="claim_id",
            )
            plan = self._plan()
            key = self._json_value({"claim_id": resolved_claim_id})
            claim_value = self._json_value(resolved_claim)
            if plan is not None:
                plan.setdefault("pre_rows", []).append(
                    {
                        "table": "identity_subject_claim_registry",
                        "key": key,
                        "value": claim_value,
                    }
                )
            self._record_expected_named_row(
                "identity_subject_claim_registry",
                resolved_claim,
                key={"claim_id": resolved_claim_id},
            )
            return outcome, resolved_claim_id
        return outcome, None

    async def claim_identity_subject(self, **values) -> None:
        await self._record_expected_collection(
            IdentitySubjectClaimRegistryModel,
            scope_field="slice3_revision_id",
            scope_value=values["slice3_revision_id"],
            key_field="claim_id",
            added_key=values["claim_id"],
        )
        plan = self._plan()
        if plan is not None:
            plan.setdefault("pre_rows", []).append(
                {
                    "table": "identity_subject_claim_registry",
                    "key": self._json_value({"claim_id": values["claim_id"]}),
                    "value": None,
                }
            )
        self._record_expected_named_row(
            "identity_subject_claim_registry",
            {
                **values,
                "claimed_at": values.get("claimed_at"),
                "version": 1,
            },
            key={"claim_id": values["claim_id"]},
        )
        await self.session.execute(
            text(
                "SELECT identity.claim_identity_subject_v1("
                ":claim_id,:user_ref,:member_id,:identity_fingerprint,:fingerprint_key_id,"
                ":source_kind,:p1_submission_id,:p1_decision_ref,:slice3_revision_id,"
                ":slice3_decision_id,:source_facts_version,:source_evidence_digest,"
                ":adult_eligible,:represented_elder_eligible,:claimed_at)"
            ),
            values,
        )

    async def consent_for_update(self, consent_record_id: UUID):
        result = await self.session.execute(
            select(*ConsentRecordModel.__table__.c)
            .where(ConsentRecordModel.consent_record_id == consent_record_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def add_consent(self, **values) -> None:
        await self._record_expected_collection(
            ConsentRecordModel,
            scope_field="enrollment_id",
            scope_value=values["enrollment_id"],
            key_field="consent_record_id",
            added_key=values["consent_record_id"],
        )
        self._record_expected_insert(
            ConsentRecordModel, values, key_fields=("consent_record_id",)
        )
        await self.session.execute(insert(ConsentRecordModel).inline().values(**values))

    async def update_consent(self, consent_record_id: UUID, **values) -> None:
        scope_result = await self.session.execute(
            select(ConsentRecordModel.enrollment_id)
            .where(ConsentRecordModel.consent_record_id == consent_record_id)
            .with_for_update()
        )
        enrollment_id = scope_result.scalar_one_or_none()
        if enrollment_id is not None:
            await self._record_expected_collection(
                ConsentRecordModel,
                scope_field="enrollment_id",
                scope_value=enrollment_id,
                key_field="consent_record_id",
            )
        await self._record_expected_update(
            ConsentRecordModel,
            key_field="consent_record_id",
            key_value=consent_record_id,
            values=values,
        )
        await self.session.execute(
            update(ConsentRecordModel)
            .where(ConsentRecordModel.consent_record_id == consent_record_id)
            .values(**values)
        )

    async def current_consent_documents(self, document_types: tuple[str, ...], locale: str):
        result = await self.session.execute(
            select(
                ConsentDocumentVersionModel.document_version_id,
                ConsentDocumentVersionModel.document_type,
                ConsentDocumentVersionModel.semantic_version,
                ConsentDocumentVersionModel.effective_at,
                ConsentDocumentVersionModel.requires_reconsent,
                ConsentDocumentRenditionModel.rendition_id,
                ConsentDocumentRenditionModel.locale,
                ConsentDocumentRenditionModel.title,
                ConsentDocumentRenditionModel.body,
                ConsentDocumentRenditionModel.content_sha256,
            )
            .join(
                ConsentDocumentRenditionModel,
                ConsentDocumentRenditionModel.document_version_id == ConsentDocumentVersionModel.document_version_id,
            )
            .where(
                ConsentDocumentVersionModel.status == "PUBLISHED",
                ConsentDocumentVersionModel.document_type.in_(document_types),
                ConsentDocumentRenditionModel.locale == locale,
            )
            .order_by(ConsentDocumentVersionModel.document_type)
        )
        return tuple(result.mappings())

    async def document_for_update(self, document_version_id: UUID):
        result = await self.session.execute(
            select(*ConsentDocumentVersionModel.__table__.c)
            .where(ConsentDocumentVersionModel.document_version_id == document_version_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def published_documents_for_update(self, document_type: str):
        result = await self.session.execute(
            select(*ConsentDocumentVersionModel.__table__.c)
            .where(
                ConsentDocumentVersionModel.document_type == document_type,
                ConsentDocumentVersionModel.status == "PUBLISHED",
            )
            .with_for_update()
        )
        return tuple(result.mappings())

    async def add_consent_document(self, **values) -> None:
        self._record_expected_insert(
            ConsentDocumentVersionModel, values, key_fields=("document_version_id",)
        )
        await self.session.execute(insert(ConsentDocumentVersionModel).inline().values(**values))

    async def update_consent_document(self, document_version_id: UUID, **values) -> None:
        await self._record_expected_update(
            ConsentDocumentVersionModel,
            key_field="document_version_id",
            key_value=document_version_id,
            values=values,
        )
        await self.session.execute(
            update(ConsentDocumentVersionModel)
            .where(ConsentDocumentVersionModel.document_version_id == document_version_id)
            .values(**values)
        )

    async def add_consent_rendition(self, **values) -> None:
        await self._record_expected_collection(
            ConsentDocumentRenditionModel,
            scope_field="document_version_id",
            scope_value=values["document_version_id"],
            key_field="rendition_id",
            added_key=values["rendition_id"],
        )
        self._record_expected_insert(
            ConsentDocumentRenditionModel, values, key_fields=("rendition_id",)
        )
        await self.session.execute(insert(ConsentDocumentRenditionModel).inline().values(**values))

    async def document_renditions(self, document_version_id: UUID):
        result = await self.session.execute(
            select(
                ConsentDocumentRenditionModel.rendition_id,
                ConsentDocumentRenditionModel.locale,
                ConsentDocumentRenditionModel.title,
                ConsentDocumentRenditionModel.content_sha256,
            ).where(ConsentDocumentRenditionModel.document_version_id == document_version_id)
        )
        return tuple(result.mappings())

    async def consent_with_locale(self, consent_record_id: UUID):
        result = await self.session.execute(
            select(
                ConsentRecordModel.consent_record_id,
                ConsentRecordModel.enrollment_id,
                ConsentRecordModel.document_type,
                ConsentRecordModel.document_version_id,
                ConsentRecordModel.rendition_id,
                ConsentDocumentRenditionModel.locale,
                ConsentRecordModel.choice,
                ConsentRecordModel.status,
                ConsentRecordModel.presented_at,
                ConsentRecordModel.accepted_at,
                ConsentRecordModel.withdrawn_at,
                ConsentRecordModel.version,
            ).join(
                ConsentDocumentRenditionModel,
                ConsentDocumentRenditionModel.rendition_id == ConsentRecordModel.rendition_id,
            ).where(ConsentRecordModel.consent_record_id == consent_record_id)
        )
        return result.mappings().one_or_none()

    async def accepted_consents(self, enrollment_id: UUID):
        result = await self.session.execute(
            select(
                ConsentRecordModel.document_type,
                ConsentRecordModel.document_version_id,
                ConsentRecordModel.rendition_id,
            ).where(
                ConsentRecordModel.enrollment_id == enrollment_id,
                ConsentRecordModel.status == "ACCEPTED",
            )
        )
        return tuple(result.mappings())

    async def accepted_consents_for_update(self, enrollment_id: UUID, document_type: str):
        result = await self.session.execute(
            select(*ConsentRecordModel.__table__.c)
            .where(
                ConsentRecordModel.enrollment_id == enrollment_id,
                ConsentRecordModel.document_type == document_type,
                ConsentRecordModel.status == "ACCEPTED",
            )
            .order_by(ConsentRecordModel.accepted_at, ConsentRecordModel.consent_record_id)
            .with_for_update()
        )
        return tuple(result.mappings())

    async def consent_enrollment_id(self, consent_record_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(ConsentRecordModel.enrollment_id).where(
                ConsentRecordModel.consent_record_id == consent_record_id
            )
        )

    async def add_assignment(self, **values) -> None:
        self._record_expected_insert(
            PrimaryTherapistAssignmentModel, values, key_fields=("assignment_id",)
        )
        await self.session.execute(insert(PrimaryTherapistAssignmentModel).inline().values(**values))

    async def assignment_for_update(self, assignment_id: UUID):
        result = await self.session.execute(
            select(*PrimaryTherapistAssignmentModel.__table__.c)
            .where(PrimaryTherapistAssignmentModel.assignment_id == assignment_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def therapist_for_case_update(self, therapist_id: UUID):
        result = await self.session.execute(
            text(
                "SELECT therapist_id,tenant_id,status,service_tags,capacity_limit,"
                "active_case_count,current_qualification_version_id,qualification_valid_until,version "
                "FROM public.therapist_profile WHERE therapist_id=:therapist_id FOR UPDATE"
            ),
            {"therapist_id": therapist_id},
        )
        current = result.mappings().one_or_none()
        plan = self._plan()
        if current is not None and plan is not None:
            key = self._json_value({"therapist_id": therapist_id})
            value = self._json_value(dict(current))
            matching = [
                row
                for row in plan.setdefault("pre_rows", [])
                if row["table"] == "therapist_profile" and row["key"] == key
            ]
            if matching and any(row["value"] != value for row in matching):
                raise RuntimeError("Slice 3 case therapist preimage changed")
            if not matching:
                plan["pre_rows"].append(
                    {"table": "therapist_profile", "key": key, "value": value}
                )
            self._record_expected_named_row(
                "therapist_profile",
                dict(current),
                key={"therapist_id": therapist_id},
            )
        return current

    async def assignment_candidate_guard(
        self, therapist_id: UUID, tenant_id: int, service_scope_tags: tuple[str, ...]
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.slice3_assignment_candidate_guard_v1("
                ":therapist_id,:tenant_id,CAST(:service_scope_tags AS jsonb))"
            ),
            {
                "therapist_id": therapist_id,
                "tenant_id": tenant_id,
                "service_scope_tags": json.dumps(list(service_scope_tags)),
            },
        )
        return result.scalar_one() is True

    async def digest_algorithm_guard(self, payload: dict[str, str]) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.slice3_digest_algorithm_guard_v1("
                "CAST(:payload AS jsonb))"
            ),
            {"payload": json.dumps(payload, sort_keys=True, separators=(",", ":"))},
        )
        return result.scalar_one() is True

    async def confirm_mutation_outcome(
        self,
        kind: str,
        *,
        actor_scope: str,
        operation: str,
        target_id: UUID,
        idempotency_key: str,
        request_digest: str,
        expected_postimage: dict[str, object],
    ):
        function = {
            "enrollment_writer": "slice3_enrollment_mutation_confirm_v1",
            "identity_review_writer": "slice3_identity_review_mutation_confirm_v1",
            "case_writer": "slice3_case_mutation_confirm_v1",
        }.get(kind)
        if function is None:
            raise RuntimeError("MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE") from None
        result = await self.session.execute(
            text(
                f"SELECT outcome,confirmed_postimage_digest FROM public.{function}("
                ":actor_scope,:operation,:target_id,:idempotency_key,:request_digest,"
                "CAST(:expected_postimage AS jsonb))"
            ),
            {
                "actor_scope": actor_scope,
                "operation": operation,
                "target_id": target_id,
                "idempotency_key": idempotency_key,
                "request_digest": request_digest,
                "expected_postimage": json.dumps(
                    self._json_value(expected_postimage),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )
        return result.mappings().one()

    async def expected_mutation_postimage(
        self,
        kind: str,
        *,
        actor_scope: str,
        operation: str,
        target_id: UUID,
        idempotency_key: str,
        request_digest: str,
        expected_postimage: dict[str, object],
    ) -> str:
        function = {
            "enrollment_writer": "slice3_enrollment_mutation_expected_v1",
            "identity_review_writer": "slice3_identity_review_mutation_expected_v1",
            "case_writer": "slice3_case_mutation_expected_v1",
        }.get(kind)
        if function is None:
            raise RuntimeError("MEMBER_ENROLLMENT_DEPENDENCY_UNAVAILABLE") from None
        result = await self.session.execute(
            text(
                f"SELECT expected_confirmed_digest FROM public.{function}("
                ":actor_scope,:operation,:target_id,:idempotency_key,:request_digest,"
                "CAST(:expected_postimage AS jsonb))"
            ),
            {
                "actor_scope": actor_scope,
                "operation": operation,
                "target_id": target_id,
                "idempotency_key": idempotency_key,
                "request_digest": request_digest,
                "expected_postimage": json.dumps(
                    self._json_value(expected_postimage),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )
        return result.scalar_one()

    async def update_therapist_case_count(self, therapist_id: UUID, *, expected_version: int, now) -> bool:
        plan = self._plan()
        if plan is not None:
            key = self._json_value({"therapist_id": therapist_id})
            expected_columns = {
                "therapist_id", "tenant_id", "status", "service_tags",
                "capacity_limit", "active_case_count",
                "current_qualification_version_id", "qualification_valid_until",
                "version",
            }
            current = next(
                (
                    row["value"]
                    for row in plan.setdefault("rows", [])
                    if row["table"] == "therapist_profile" and row["key"] == key
                ),
                None,
            )
            if type(current) is not dict or set(current) != expected_columns:
                raise RuntimeError("Slice 3 case therapist preimage is invalid")
            expected = dict(current)
            expected.update(
                active_case_count=current["active_case_count"] + 1,
                updated_at=now,
                version=current["version"] + 1,
            )
            self._record_expected_named_row(
                "therapist_profile",
                expected,
                key={"therapist_id": therapist_id},
            )
        result = await self.session.execute(
            text(
                "UPDATE public.therapist_profile SET active_case_count=active_case_count+1,"
                "updated_at=:now,version=version+1 WHERE therapist_id=:therapist_id "
                "AND version=:expected_version AND active_case_count<capacity_limit"
            ),
            {"therapist_id": therapist_id, "expected_version": expected_version, "now": now},
        )
        return result.rowcount == 1

    async def readiness_guard(self, tenant_id: int):
        result = await self.session.execute(
            text("SELECT evidence_version,result_digest FROM public.slice3_readiness_guard_v1(:tenant_id)"),
            {"tenant_id": tenant_id},
        )
        return result.mappings().one_or_none()

    async def verified_adult_authority(self, user_id: int, member_id: UUID):
        result = await self.session.execute(
            text(
                "SELECT eligible,as_of_date,submission_id,decision_ref,evidence_digest "
                "FROM public.slice3_verified_adult_authority_v1(:user_id,:member_id)"
            ),
            {"user_id": user_id, "member_id": member_id},
        )
        return result.mappings().one_or_none()

    async def family_enrollment_rows(
        self, member_id: UUID, *, cursor_id: UUID | None = None, limit: int = 101
    ):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_family_enrollment_read_v1 "
                "WHERE (subject_member_id=:member_id OR "
                "(proxy_member_id=:member_id AND proxy IS NOT NULL)) "
                "AND (:cursor_id IS NULL OR enrollment_id>:cursor_id) "
                "ORDER BY enrollment_id LIMIT :limit"
            ).bindparams(
                bindparam(
                    "cursor_id",
                    type_=PostgreSQLUUID(as_uuid=True),
                )
            ),
            {"member_id": member_id, "cursor_id": cursor_id, "limit": limit},
        )
        return tuple(result.mappings())

    async def family_enrollment_detail(
        self, member_id: UUID, enrollment_id: UUID, *, limit: int = 2
    ):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_family_enrollment_read_v1 "
                "WHERE enrollment_id=:enrollment_id AND "
                "(subject_member_id=:member_id OR "
                "(proxy_member_id=:member_id AND proxy IS NOT NULL)) LIMIT :limit"
            ),
            {"member_id":member_id,"enrollment_id":enrollment_id,"limit":limit},
        )
        rows = result.mappings().all()
        return rows[0] if len(rows) == 1 else None

    async def assignment_subject_guard(self, assignment_id: UUID):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_assignment_subject_v1(:assignment_id)"
            ),
            {"assignment_id": assignment_id},
        )
        return result.mappings().one_or_none()

    async def update_assignment(self, assignment_id: UUID, **values) -> None:
        await self._record_expected_update(
            PrimaryTherapistAssignmentModel,
            key_field="assignment_id",
            key_value=assignment_id,
            values=values,
        )
        await self.session.execute(
            update(PrimaryTherapistAssignmentModel)
            .where(PrimaryTherapistAssignmentModel.assignment_id == assignment_id)
            .values(**values)
        )

    async def add_service_case(self, **values) -> None:
        self._record_expected_insert(ServiceCaseModel, values, key_fields=("case_id",))
        await self.session.execute(insert(ServiceCaseModel).inline().values(**values))

    async def service_case_for_update(self, case_id: UUID):
        result = await self.session.execute(
            select(*ServiceCaseModel.__table__.c)
            .where(ServiceCaseModel.case_id == case_id)
            .with_for_update()
        )
        return result.mappings().one_or_none()

    async def service_case_after_create(self, case_id: UUID):
        result = await self.session.execute(
            select(*ServiceCaseModel.__table__.c).where(
                ServiceCaseModel.case_id == case_id
            )
        )
        return result.mappings().one_or_none()

    async def add_audit(self, **values) -> None:
        self._record_expected_insert(
            MemberEnrollmentAuditModel, values, key_fields=("request_id", "action", "object_id")
        )
        await self.session.execute(insert(MemberEnrollmentAuditModel).inline().values(**values))

    async def add_outbox(self, **values) -> None:
        self._record_expected_insert(
            MemberEnrollmentOutboxModel, values, key_fields=("event_id",)
        )
        await self.session.execute(insert(MemberEnrollmentOutboxModel).inline().values(**values))

    async def idempotency(self, actor_scope: str, operation: str, target_id: UUID, key: str):
        result = await self.session.execute(
            select(*MemberEnrollmentIdempotencyModel.__table__.c).where(
                MemberEnrollmentIdempotencyModel.actor_scope == actor_scope,
                MemberEnrollmentIdempotencyModel.operation == operation,
                MemberEnrollmentIdempotencyModel.target_id == target_id,
                MemberEnrollmentIdempotencyModel.idempotency_key == key,
            )
        )
        return result.mappings().one_or_none()

    async def replay_idempotency(
        self, actor_scope: str, operation: str, target_id: UUID, key: str,
        request_digest: str,
    ):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_idempotency_replay_v1("
                ":actor_scope,:operation,:target_id,:key,:request_digest)"
            ),
            {
                "actor_scope": actor_scope, "operation": operation,
                "target_id": target_id, "key": key,
                "request_digest": request_digest,
            },
        )
        return result.mappings().one()

    async def record_idempotency(
        self, actor_scope: str, operation: str, target_id: UUID, key: str,
        request_digest: str, response_ciphertext: bytes, response_key_id: str,
        postimage_digest: str, created_at,
    ):
        result = await self.session.execute(
            text(
                "SELECT * FROM public.slice3_idempotency_record_v1("
                ":actor_scope,:operation,:target_id,:key,:request_digest,"
                ":response_ciphertext,:response_key_id,:postimage_digest,:created_at)"
            ),
            {
                "actor_scope": actor_scope, "operation": operation,
                "target_id": target_id, "key": key,
                "request_digest": request_digest,
                "response_ciphertext": response_ciphertext,
                "response_key_id": response_key_id,
                "postimage_digest": postimage_digest,
                "created_at": created_at,
            },
        )
        return result.mappings().one()

    async def add_idempotency(self, **values) -> None:
        await self.session.execute(insert(MemberEnrollmentIdempotencyModel).inline().values(**values))

    async def add_delivery(self, **values) -> None:
        await self.session.execute(insert(MemberEnrollmentDeliveryModel).inline().values(**values))

    async def safe_view_rows(
        self,
        view: str,
        *,
        predicates: dict[str, object],
        order: str,
        cursor_id: UUID | None = None,
        limit: int = 101,
    ):
        allowed = {
            "slice3_institution_enrollment_read_v1",
            "slice3_family_enrollment_read_v1",
            "slice3_platform_identity_review_read_v1",
            "slice3_therapist_assignment_read_v1",
            "slice3_service_case_read_v1",
        }
        if view not in allowed or not re_full_identifier(order):
            raise RuntimeError("MEMBER_ENROLLMENT_QUERY_INVALID") from None
        clauses = []
        parameters = {"limit": limit}
        for index, (column, value) in enumerate(predicates.items()):
            if not re_full_identifier(column):
                raise RuntimeError("MEMBER_ENROLLMENT_QUERY_INVALID") from None
            name = f"p{index}"
            clauses.append(f'"{column}"=:{name}')
            parameters[name] = value
        where = " AND ".join(clauses) if clauses else "TRUE"
        if cursor_id is not None:
            where = f'({where}) AND "{order}">:cursor_id'
            parameters["cursor_id"] = cursor_id
        result = await self.session.execute(
            text(f'SELECT * FROM public."{view}" WHERE {where} ORDER BY "{order}" LIMIT :limit'),
            parameters,
        )
        return tuple(result.mappings())


def re_full_identifier(value: str) -> bool:
    return bool(value) and value.replace("_", "").isalnum() and value[0].isalpha()
