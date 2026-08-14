from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CHAR, CheckConstraint, DateTime, ForeignKeyConstraint,
    Index, Integer, SmallInteger, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


PROJECTION_BUILD_STATUSES = {"BUILDING", "BUILD_COMPLETE", "FAILED", "SUPERSEDED"}
_FAILURES = (
    "'PROJECTION_SOURCE_INVALID','PROJECTION_DIGEST_KEY_UNAVAILABLE',"
    "'PROJECTION_CHECKPOINT_CONFLICT','PROJECTION_LEASE_CONFLICT','PROJECTION_UNAVAILABLE'"
)


def _module_c_generation_state() -> str:
    terminal = "completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL"
    return f"(status='BUILDING' AND completed_at IS NULL AND failure_code IS NULL AND builder_id IS NOT NULL AND lease_expires_at IS NOT NULL AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='BUILD_COMPLETE' AND {terminal} AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='FAILED' AND completed_at IS NOT NULL AND failure_code IN ({_FAILURES}) AND builder_id IS NULL AND lease_expires_at IS NULL AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SUPERSEDED' AND {terminal} AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SHADOW_RUNNING' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count IN (0,1) AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SHADOW_PASSED' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count IN (1,2) AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SHADOW_FAILED' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='READY' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count=2 AND ready_at IS NOT NULL AND ready_operation_id IS NOT NULL)"


_ORG_CATEGORIES = (
    "ORG_SOURCE_MISSING", "ORG_SOURCE_DUPLICATE", "ORG_CHAIN_INVALID", "ORG_ROOT_INVALID",
    "ORG_JSONB_INVALID", "ORG_ROW_MISMATCH", "ORG_ROW_DIGEST_MISMATCH", "ORG_MAPPING_CORRUPT",
    "ORG_MAPPING_TARGET_INVALID", "ORG_MAPPING_SCOPE_MISMATCH", "ORG_COVERAGE_MISMATCH",
    "ORG_GENERATION_IDENTITY_MISMATCH", "ORG_UNPROVEN_SOURCE_DRIFT",
    "ORG_MAPPING_REVIEW_REQUIRED", "ORG_MAPPING_CONFLICT_UNRESOLVED",
    "ORG_POST_HWM_NEW_SOURCE", "ORG_POST_BUILD_VALID_VERSION_ADVANCE",
)
_HEALTH_CATEGORIES = (
    "HEALTH_FACT_MISSING", "HEALTH_FACT_DUPLICATE", "HEALTH_FACT_FIELD_MISMATCH",
    "HEALTH_FACT_DIGEST_MISMATCH", "HEALTH_WINDOW_INVALID", "HEALTH_SELECTION_MISSING",
    "HEALTH_SELECTION_DUPLICATE", "HEALTH_WINNER_MISMATCH", "HEALTH_RULE_VERSION_MISMATCH",
    "HEALTH_MAPPING_TARGET_MISMATCH", "HEALTH_COVERAGE_MISMATCH",
    "HEALTH_GENERATION_IDENTITY_MISMATCH", "HEALTH_SOURCE_UNKNOWN",
    "HEALTH_CURRENTNESS_UNPROVEN", "HEALTH_MAPPING_REVIEW_REQUIRED",
    "HEALTH_P2_PRIORITY_EXPECTED_DIFFERENCE", "HEALTH_POST_HWM_NEW_FACT",
)
_CATEGORY_CLASSES = {
    "organization": (_ORG_CATEGORIES[:13], _ORG_CATEGORIES[13:15], _ORG_CATEGORIES[15:]),
    "health": (_HEALTH_CATEGORIES[:12], _HEALTH_CATEGORIES[12:16], _HEALTH_CATEGORIES[16:]),
}


def _shadow_run_checks(domain: str):
    health = domain == "health"
    result = ["source_digest", "mapping_digest", "projection_digest", "coverage_digest", "evidence_digest", "blocker_count", "review_required_count", "informational_count", "category_counts", "source_count"]
    result += (["currentness_digest", "selection_digest", "current_fact_count", "projection_fact_count", "expected_selection_count", "actual_selection_count", "fact_coverage_numerator", "fact_coverage_denominator", "selection_coverage_numerator", "selection_coverage_denominator"] if health else ["eligible_count", "projection_count", "coverage_numerator", "coverage_denominator"])
    counts = [value for value in result if value.endswith("count") or value.endswith("numerator") or value.endswith("denominator")]
    digests = ["source_digest", "mapping_digest", "projection_digest", "coverage_digest", "evidence_digest"] + (["currentness_digest", "selection_digest"] if health else [])
    categories = _HEALTH_CATEGORIES if health else _ORG_CATEGORIES
    allowed = ",".join(f"'{key}'" for key in categories)
    values = " AND ".join(f"(NOT category_counts ? '{key}' OR (jsonb_typeof(category_counts->'{key}')='number' AND (category_counts->>'{key}') ~ '^(0|[1-9][0-9]*)$'))" for key in categories)
    sums = [
        "+".join(f"COALESCE((category_counts->>'{key}')::bigint,0)" for key in group)
        for group in _CATEGORY_CLASSES[domain]
    ]
    running = " AND ".join(f"{column} IS NULL" for column in result + ["complete_operation_id", "completed_at"])
    terminal = " AND ".join(f"{column} IS NOT NULL" for column in result + ["complete_operation_id", "completed_at"])
    coverage = ("(status<>'PASSED') OR (blocker_count=0 AND review_required_count=0 AND fact_coverage_numerator=fact_coverage_denominator AND selection_coverage_numerator=selection_coverage_denominator)" if health else "(status<>'PASSED') OR (blocker_count=0 AND review_required_count=0 AND coverage_numerator=coverage_denominator)")
    bounds = ("fact_coverage_numerator IS NULL OR (fact_coverage_numerator<=fact_coverage_denominator AND selection_coverage_numerator<=selection_coverage_denominator)" if health else "coverage_numerator IS NULL OR coverage_numerator<=coverage_denominator")
    return (
        CheckConstraint("status IN ('RUNNING','PASSED','FAILED')", name="status"),
        CheckConstraint("projection_version=1 AND run_sequence>=1 AND lease_epoch>=0 AND version>=1", name="identity"),
        CheckConstraint("high_watermark_digest ~ '^[0-9A-F]{64}$' AND generation_input_digest ~ '^[0-9a-f]{64}$'", name="digest"),
        CheckConstraint(" AND ".join(f"{column} IS NULL OR {column}>=0" for column in counts) + f" AND ({bounds})", name="counts"),
        CheckConstraint(f"category_counts IS NULL OR (jsonb_typeof(category_counts)='object' AND category_counts - ARRAY[{allowed}] = '{{}}'::jsonb AND {values} AND ({sums[0]})=blocker_count AND ({sums[1]})=review_required_count AND ({sums[2]})=informational_count)", name="category_json"),
        CheckConstraint(" AND ".join(f"{column} IS NULL OR {column} ~ '^[0-9A-F]{{64}}$'" for column in digests), name="result_digest"),
        CheckConstraint(coverage, name="coverage"),
        CheckConstraint(f"(status='RUNNING' AND {running} AND lease_expires_at IS NOT NULL) OR (status IN ('PASSED','FAILED') AND {terminal} AND lease_expires_at IS NULL)", name="state"),
    )


class OrganizationProjectionGeneration(Base):
    __tablename__ = "organization_projection_generation"
    __table_args__ = (
        UniqueConstraint("projection_version", "generation_no", name="uq_organization_projection_generation_version_no"),
        UniqueConstraint("start_operation_id", name="uq_organization_projection_generation_start_operation"),
        UniqueConstraint("id", "digest_key_id", name="uq_organization_projection_generation_id_digest_key"),
        CheckConstraint("projection_version=1 AND generation_no>=1 AND lease_epoch>=0 AND version>=1", name="version"),
        CheckConstraint("input_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name="digest"),
        CheckConstraint("jsonb_typeof(high_watermark)='object' AND high_watermark=jsonb_build_object('max_organization_id',high_watermark->'max_organization_id') AND jsonb_typeof(high_watermark->'max_organization_id')='number' AND (high_watermark->>'max_organization_id') ~ '^(0|[1-9][0-9]*)$'", name="high_watermark"),
        CheckConstraint(_module_c_generation_state(), name="state"),
        ForeignKeyConstraint(("current_shadow_run_id",), ("public.organization_projection_shadow_run.run_id",), name="fk_organization_projection_generation_current_shadow_run", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
        Index("idx_organization_projection_generation_status", "status", "projection_version", "generation_no"),
        Index("uq_organization_projection_generation_ready_operation", "ready_operation_id", unique=True, postgresql_where=text("ready_operation_id IS NOT NULL")),
        {"schema": "public"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    projection_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    generation_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    high_watermark: Mapped[dict] = mapped_column(JSONB, nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    start_operation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    builder_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    current_shadow_run_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    shadow_success_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_operation_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))


class OrganizationProjectionShadowRun(Base):
    __tablename__ = "organization_projection_shadow_run"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id",), ("public.organization_projection_generation.id",), name="fk_organization_projection_shadow_run_generation", ondelete="RESTRICT"),
        UniqueConstraint("generation_id", "run_sequence", name="uq_organization_projection_shadow_run_generation_sequence"),
        UniqueConstraint("start_operation_id", name="uq_organization_projection_shadow_run_start_operation"),
        *_shadow_run_checks("organization"),
        Index("idx_organization_projection_shadow_run_generation_status", "generation_id", "status", "run_sequence"),
        Index("idx_organization_projection_shadow_run_generation_completed", "generation_id", "completed_at"),
        Index("uq_organization_projection_shadow_run_complete_operation", "complete_operation_id", unique=True, postgresql_where=text("complete_operation_id IS NOT NULL")),
        {"schema": "public"},
    )
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    generation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    projection_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    high_watermark: Mapped[dict] = mapped_column(JSONB, nullable=False)
    high_watermark_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    source_digest: Mapped[str | None] = mapped_column(CHAR(64))
    mapping_digest: Mapped[str | None] = mapped_column(CHAR(64))
    projection_digest: Mapped[str | None] = mapped_column(CHAR(64))
    coverage_digest: Mapped[str | None] = mapped_column(CHAR(64))
    evidence_digest: Mapped[str | None] = mapped_column(CHAR(64))
    blocker_count: Mapped[int | None] = mapped_column(BigInteger)
    review_required_count: Mapped[int | None] = mapped_column(BigInteger)
    informational_count: Mapped[int | None] = mapped_column(BigInteger)
    category_counts: Mapped[dict | None] = mapped_column(JSONB)
    source_count: Mapped[int | None] = mapped_column(BigInteger)
    eligible_count: Mapped[int | None] = mapped_column(BigInteger)
    projection_count: Mapped[int | None] = mapped_column(BigInteger)
    coverage_numerator: Mapped[int | None] = mapped_column(BigInteger)
    coverage_denominator: Mapped[int | None] = mapped_column(BigInteger)
    start_operation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    complete_operation_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    validator_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class OrganizationProjectionShadowAudit(Base):
    __tablename__ = "organization_projection_shadow_audit"
    __table_args__ = (
        ForeignKeyConstraint(("run_id",), ("public.organization_projection_shadow_run.run_id",), name="fk_organization_projection_shadow_audit_run", ondelete="RESTRICT"),
        ForeignKeyConstraint(("generation_id",), ("public.organization_projection_generation.id",), name="fk_organization_projection_shadow_audit_generation", ondelete="RESTRICT"),
        UniqueConstraint("operation_id", name="uq_organization_projection_shadow_audit_operation"),
        CheckConstraint("action IN ('SHADOW_START','SHADOW_HEARTBEAT','SHADOW_TAKEOVER','SHADOW_COMPLETE','SHADOW_FAIL','GENERATION_READY')", name="action"),
        Index("idx_organization_projection_shadow_audit_generation_created", "generation_id", "created_at"),
        {"schema":"public"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    generation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    preimage_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    postimage_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    evidence_digest: Mapped[str | None] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class OrganizationProjectionCheckpoint(Base):
    __tablename__ = "organization_projection_checkpoint"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id",), ("public.organization_projection_generation.id",), name="fk_organization_projection_checkpoint_generation", ondelete="CASCADE"),
        CheckConstraint("processed_count>=0 AND projected_count>=0 AND skipped_count>=0 AND remaining_count>=0 AND projected_count+skipped_count=processed_count", name="counts"),
        CheckConstraint("last_source_id IS NULL OR last_source_id>=1", name="cursor"),
        CheckConstraint("checkpoint_digest ~ '^[0-9a-f]{64}$' AND version>=1", name="digest"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_source_id: Mapped[int | None] = mapped_column(BigInteger)
    processed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    projected_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    remaining_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_operation_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    checkpoint_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class OrganizationProjectionModel(Base):
    __tablename__ = "organization_projection"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id", "digest_key_id"), ("public.organization_projection_generation.id", "public.organization_projection_generation.digest_key_id"), name="fk_organization_projection_generation_key", ondelete="CASCADE"),
        UniqueConstraint("generation_id", "org_code", name="uq_organization_projection_generation_code"),
        CheckConstraint("org_type IN ('headquarter','province','city','county')", name="type"),
        CheckConstraint("status IN ('active','inactive','archived')", name="status"),
        CheckConstraint("source_version>=1 AND organization_id>=1 AND sort_order>=0", name="source_version"),
        CheckConstraint("status='active' OR scope_eligible=false", name="scope"),
        CheckConstraint("row_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name="digest"),
        CheckConstraint("compatibility_mode='canonical' AND jsonb_typeof(path_ids)='array' AND jsonb_typeof(path_codes)='array' AND jsonb_typeof(path_versions)='array' AND jsonb_array_length(path_ids)=4 AND jsonb_array_length(path_codes)=4 AND jsonb_array_length(path_versions)=4 AND NOT jsonb_path_exists(path_versions, '$[*] ? (@.type() != \"number\" || @ < 1 || @ % 1 != 0)')", name="compatibility"),
        Index("idx_organization_projection_parent_order", "generation_id", "parent_id", "sort_order", "organization_id"),
        Index("idx_organization_projection_type_status", "generation_id", "org_type", "status"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    org_code: Mapped[str] = mapped_column(String(50), nullable=False)
    org_name: Mapped[str] = mapped_column(String(100), nullable=False)
    org_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    path_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    path_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    path_versions: Mapped[list] = mapped_column(JSONB, nullable=False)
    compatibility_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'canonical'"))
    scope_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
