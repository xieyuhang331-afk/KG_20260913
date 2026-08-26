from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, LargeBinary, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as UUIDType
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ServiceCycleScheduleModel(Base):
    __tablename__ = "service_cycle_schedule"
    __table_args__ = (
        UniqueConstraint("service_case_id", "version_no", name="uq_service_cycle_schedule_version"),
        UniqueConstraint("active_plan_id", name="uq_service_cycle_schedule_plan"),
        CheckConstraint("version_no>=1 AND version>=1", name="ck_service_cycle_schedule_version"),
        Index("uq_service_cycle_schedule_current", "service_case_id", unique=True, postgresql_where=text("is_current")),
        {"schema": "public"},
    )
    schedule_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active_plan_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cycle_anchor_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceMilestoneModel(Base):
    __tablename__ = "service_milestone"
    __table_args__ = (
        UniqueConstraint("schedule_id", "code", name="uq_service_milestone_code"),
        ForeignKeyConstraint(("schedule_id",), ("public.service_cycle_schedule.schedule_id",), name="fk_service_milestone_schedule"),
        CheckConstraint("code IN ('D0','D7','D14','D21','D28')", name="ck_service_milestone_code"),
        CheckConstraint("status IN ('PENDING','DUE','COMPLETED','MISSED','INVALIDATED') AND version>=1", name="ck_service_milestone_status"),
        {"schema": "public"},
    )
    milestone_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    schedule_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(String(4), nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_summary: Mapped[dict | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceMilestoneRevisionModel(Base):
    __tablename__ = "service_milestone_revision"
    __table_args__ = (
        UniqueConstraint("milestone_id", "version_no", name="uq_service_milestone_revision"),
        {"schema": "public"},
    )
    revision_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    milestone_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    body_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceCaseLifecycleEventModel(Base):
    __tablename__ = "service_case_lifecycle_event"
    __table_args__ = ({"schema": "public"},)
    lifecycle_event_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceClosingAssessmentModel(Base):
    __tablename__ = "service_closing_assessment"
    __table_args__ = (UniqueConstraint("service_case_id", "version_no", name="uq_service_closing_assessment"), {"schema": "public"})
    closing_assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    final_retest_evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceSummaryModel(Base):
    __tablename__ = "service_summary"
    __table_args__ = (UniqueConstraint("service_case_id", "version_no", name="uq_service_summary_version"), {"schema": "public"})
    summary_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceSummaryAcknowledgementModel(Base):
    __tablename__ = "service_summary_acknowledgement"
    __table_args__ = (UniqueConstraint("summary_id", "subject_member_id", name="uq_service_summary_ack"), {"schema": "public"})
    acknowledgement_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    summary_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proxy_grant_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceTransferRequestModel(Base):
    __tablename__ = "service_transfer_request"
    __table_args__ = (
        CheckConstraint("source_tenant_id<>target_tenant_id", name="ck_service_transfer_distinct_tenants"),
        CheckConstraint("status IN ('REQUESTED_BY_USER','NEW_INSTITUTION_REVIEWING','ACCEPTED','OLD_INSTITUTION_CLOSING','USER_SCOPE_CONFIRMED','TRANSFERRED','REJECTED_BY_NEW_INSTITUTION','CANCELLED_BY_USER') AND version>=1", name="ck_service_transfer_status"),
        Index("uq_service_transfer_open", "source_service_case_id", unique=True, postgresql_where=text("status NOT IN ('TRANSFERRED','REJECTED_BY_NEW_INSTITUTION','CANCELLED_BY_USER')")),
        {"schema": "public"},
    )
    transfer_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    source_service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    source_tenant_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_tenant_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    target_decision: Mapped[str | None] = mapped_column(String(64))
    source_closure_status: Mapped[str | None] = mapped_column(String(32))
    scope_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceTransferScopeRevisionModel(Base):
    __tablename__ = "service_transfer_scope_revision"
    __table_args__ = (UniqueConstraint("transfer_id", "version_no", name="uq_service_transfer_scope_revision"), {"schema": "public"})
    scope_revision_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    transfer_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    scope_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceTransferContinuationHandoffModel(Base):
    __tablename__ = "service_transfer_continuation_handoff"
    __table_args__ = (
        UniqueConstraint("transfer_id", name="uq_service_transfer_continuation_handoff_transfer"),
        CheckConstraint(
            "status IN ('PENDING_TARGET_ENROLLMENT','ENROLLMENT_CREATED','ASSIGNMENT_PENDING','CONTINUATION_CASE_LINKED') AND version>=1",
            name="ck_service_transfer_continuation_handoff_status",
        ),
        {"schema": "public"},
    )
    handoff_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    transfer_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    source_service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    source_tenant_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_tenant_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    authorized_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    scope_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    linked_enrollment_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    linked_service_case_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ProxyMajorAuthorizationModel(Base):
    __tablename__ = "proxy_major_authorization"
    __table_args__ = (
        UniqueConstraint("authorization_id", "version", name="uq_proxy_major_authorization_version"),
        CheckConstraint(
            "version>=1 AND ((version=1 AND revoked_at IS NULL) OR (version>1 AND revoked_at IS NOT NULL))",
            name="ck_proxy_major_authorization_append_only",
        ),
        {"schema": "public"},
    )
    authorization_revision_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    authorization_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    proxy_grant_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    principal_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    proxy_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    authorization_document_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    witness_decision_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    permission_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    granted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PersonalDataExportRequestModel(Base):
    __tablename__ = "personal_data_export_request"
    __table_args__ = (
        CheckConstraint("status IN ('REQUESTED','GENERATING','READY','DOWNLOADED','EXPIRED','FAILED','CANCELLED') AND version>=1 AND ((status='GENERATING' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) OR (status<>'GENERATING' AND lease_owner IS NULL AND lease_until IS NULL))", name="ck_personal_data_export_status"),
        {"schema": "public"},
    )
    export_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    requested_scope: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PersonalDataExportArtifactModel(Base):
    __tablename__ = "personal_data_export_artifact"
    __table_args__ = (
        UniqueConstraint("export_id", name="uq_personal_data_export_artifact"),
        ForeignKeyConstraint(["export_id"], ["public.personal_data_export_request.export_id"], name="fk_personal_data_export_artifact_export"),
        ForeignKeyConstraint(["private_file_id"], ["public.private_file.file_id"], name="fk_personal_data_export_artifact_file"),
        {"schema": "public"},
    )
    artifact_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    export_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    private_file_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    manifest_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    artifact_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PersonalDataExportDownloadAccessModel(Base):
    __tablename__ = "personal_data_export_download_access"
    __table_args__ = (
        ForeignKeyConstraint(["export_id"], ["public.personal_data_export_request.export_id"], name="fk_personal_data_export_access_export"),
        ForeignKeyConstraint(["private_file_id"], ["public.private_file.file_id"], name="fk_personal_data_export_access_file"),
        CheckConstraint("expires_at>created_at AND version>=1", name="ck_personal_data_export_access"),
        Index("uq_personal_data_export_access_current", "export_id", unique=True, postgresql_where=text("consumed_at IS NULL")),
        {"schema": "public"},
    )
    access_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    export_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    private_file_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceFulfillmentReceiptModel(Base):
    __tablename__ = "service_fulfillment_receipt"
    __table_args__ = (UniqueConstraint("actor_scope", "operation", "idempotency_key", name="uq_service_fulfillment_receipt"), {"schema": "public"})
    receipt_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    actor_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    postimage_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceFulfillmentAuditModel(Base):
    __tablename__ = "service_fulfillment_audit"
    __table_args__ = ({"schema": "public"},)
    audit_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    evidence_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceFulfillmentOutboxModel(Base):
    __tablename__ = "service_fulfillment_outbox"
    __table_args__ = (CheckConstraint("status IN ('PENDING','PROCESSING','DELIVERED','FAILED') AND attempts>=0 AND version>=1", name="ck_service_fulfillment_outbox"), {"schema": "public"})
    event_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    aggregate_ref: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceFulfillmentDeliveryModel(Base):
    __tablename__ = "service_fulfillment_delivery"
    __table_args__ = (UniqueConstraint("event_id", "target_type", "target_ref", name="uq_service_fulfillment_delivery"), {"schema": "public"})
    delivery_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ref: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
