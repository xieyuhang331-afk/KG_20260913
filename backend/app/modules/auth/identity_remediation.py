from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PRIMARY_CLASSES = tuple(f"H{index}" for index in range(8))
PRIMARY_PRIORITY = ("H7", "H2", "H3", "H1", "H6", "H4", "H5", "H0")
ALLOWED_SECONDARY_LABELS = frozenset(
    {
        "IDENTITY_FACTS_PRESENT",
        "TENANT_FACTS_PRESENT",
        "UNKNOWN_IDENTITY",
        "UNKNOWN_TENANT_RELATION",
    }
)
PUBLIC_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "classification_version",
        "status",
        "batch_ref",
        "snapshot_at",
        "snapshot_ceiling_present",
        "total_count",
        "member_count",
        "excluded_non_member_count",
        "class_counts",
        "secondary_label_counts",
        "multi_primary_match_count",
        "unclassified_count",
        "page_count",
        "processed_count",
        "batch_status",
        "anonymous_watermark_present",
        "small_count_present",
        "classification_rule_hash",
        "code_sha256",
        "migration_head",
        "evidence_hash",
    }
)
_SCHEMA_VERSION = "A2.2-I-V1"
_CLASSIFICATION_VERSION = "H0-H7-V1"
_MIGRATION_HEAD = "20260903_0036"
_SMALL_COUNT_LIMIT = 5
_SMALL_COUNT = "SMALL_COUNT"
_SENSITIVE_KEY_PARTS = (
    "user_id",
    "user_ref",
    "member_id",
    "tenant_id",
    "real_name",
    "id_card",
    "ciphertext",
    "digest",
    "fingerprint",
    "key_id",
    "phone",
    "token",
    "secret",
    "credential",
)


class InventoryContractError(RuntimeError):
    """Raised when anonymous inventory invariants cannot be proven."""


@dataclass(frozen=True, slots=True)
class IdentitySnapshotFacts:
    role: str | None
    legacy_pii_present: bool
    identity_authority_signal: bool | None
    formal_chain_complete: bool | None
    tenant_present: bool | None
    tenant_relation_known: bool
    self_link_count: int | None
    enrollment_count: int | None
    current_enrollment_count: int | None
    tenant_matches_unique_current: bool | None
    enrollment_scope_complete: bool | None


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    primary_class: str
    secondary_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InventoryReport:
    schema_version: str
    classification_version: str
    status: str
    input_count: int
    class_counts: dict[str, int]
    secondary_label_counts: dict[str, int]
    multi_primary_match_count: int
    unclassified_count: int
    batch_ref: str
    snapshot_at: str
    snapshot_ceiling_present: bool
    member_count: int
    excluded_non_member_count: int
    page_count: int
    processed_count: int
    batch_status: str
    anonymous_watermark_present: bool
    small_count_present: bool
    classification_rule_hash: str
    code_sha256: str
    migration_head: str
    evidence_hash: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification_version": self.classification_version,
            "status": self.status,
            "batch_ref": self.batch_ref,
            "snapshot_at": self.snapshot_at,
            "snapshot_ceiling_present": self.snapshot_ceiling_present,
            "total_count": self.input_count,
            "member_count": self.member_count,
            "excluded_non_member_count": self.excluded_non_member_count,
            "class_counts": _suppress_small_counts(self.class_counts),
            "secondary_label_counts": _suppress_small_counts(
                self.secondary_label_counts
            ),
            "multi_primary_match_count": self.multi_primary_match_count,
            "unclassified_count": self.unclassified_count,
            "page_count": self.page_count,
            "processed_count": self.processed_count,
            "batch_status": self.batch_status,
            "anonymous_watermark_present": self.anonymous_watermark_present,
            "small_count_present": self.small_count_present,
            "classification_rule_hash": self.classification_rule_hash,
            "code_sha256": self.code_sha256,
            "migration_head": self.migration_head,
            "evidence_hash": self.evidence_hash,
        }


def _suppress_small_counts(counts: Mapping[str, int]) -> dict[str, int | str]:
    return {
        name: _SMALL_COUNT if 0 < value < _SMALL_COUNT_LIMIT else value
        for name, value in counts.items()
    }


def _sha256_json(value: Mapping[str, object]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest().upper()


def _classification_rule_hash() -> str:
    return _sha256_json(
        {
            "schema_version": _SCHEMA_VERSION,
            "classification_version": _CLASSIFICATION_VERSION,
            "primary_classes": PRIMARY_CLASSES,
            "primary_priority": PRIMARY_PRIORITY,
            "secondary_labels": sorted(ALLOWED_SECONDARY_LABELS),
            "small_count_limit": _SMALL_COUNT_LIMIT,
        }
    )


def _code_sha256() -> str:
    source = Path(__file__).read_text(encoding="utf-8")
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper()


def _is_unknown_identity(facts: IdentitySnapshotFacts) -> bool:
    return (
        facts.identity_authority_signal is None
        or facts.formal_chain_complete is None
    )


def _is_unknown_tenant(facts: IdentitySnapshotFacts) -> bool:
    if facts.tenant_present is None or not facts.tenant_relation_known:
        return True
    if not facts.tenant_present:
        return False
    return (
        facts.self_link_count is None
        or facts.enrollment_count is None
        or facts.current_enrollment_count is None
        or facts.tenant_matches_unique_current is None
        or facts.enrollment_scope_complete is None
    )


def _validate_facts(facts: IdentitySnapshotFacts) -> None:
    counts = (
        facts.self_link_count,
        facts.enrollment_count,
        facts.current_enrollment_count,
    )
    if any(value is not None and value < 0 for value in counts):
        raise InventoryContractError("INVENTORY_FACTS_INCONSISTENT")
    if facts.formal_chain_complete is True and facts.identity_authority_signal is not True:
        raise InventoryContractError("INVENTORY_FACTS_INCONSISTENT")
    if (
        facts.enrollment_count is not None
        and facts.current_enrollment_count is not None
        and facts.current_enrollment_count > facts.enrollment_count
    ):
        raise InventoryContractError("INVENTORY_FACTS_INCONSISTENT")


def _candidate_classes(facts: IdentitySnapshotFacts) -> set[str]:
    _validate_facts(facts)
    if facts.role != "member":
        return {"H7"}

    candidates: set[str] = set()
    identity_unknown = _is_unknown_identity(facts)
    tenant_unknown = _is_unknown_tenant(facts)
    if identity_unknown or (
        facts.identity_authority_signal is True
        and facts.formal_chain_complete is not True
    ):
        candidates.add("H2")
    if facts.formal_chain_complete is True and facts.legacy_pii_present:
        candidates.add("H3")
    if facts.legacy_pii_present and facts.identity_authority_signal is False:
        candidates.add("H1")
    if tenant_unknown or (
        facts.tenant_present is True
        and (
            facts.self_link_count != 1
            or facts.enrollment_count is None
            or facts.current_enrollment_count is None
            or (
                facts.enrollment_count > 0
                and facts.current_enrollment_count != 1
            )
            or (
                facts.current_enrollment_count == 1
                and (
                    facts.tenant_matches_unique_current is not True
                    or facts.enrollment_scope_complete is not True
                )
            )
        )
    ):
        candidates.add("H6")
    if (
        facts.tenant_present is True
        and facts.tenant_relation_known
        and facts.self_link_count == 1
        and facts.enrollment_count == 0
    ):
        candidates.add("H4")
    if (
        facts.tenant_present is True
        and facts.tenant_relation_known
        and facts.self_link_count == 1
        and facts.enrollment_count is not None
        and facts.enrollment_count >= 1
        and facts.current_enrollment_count == 1
        and facts.tenant_matches_unique_current is True
        and facts.enrollment_scope_complete is True
    ):
        candidates.add("H5")
    if (
        not identity_unknown
        and not tenant_unknown
        and not candidates
        and facts.tenant_present is False
        and facts.legacy_pii_present is False
        and facts.formal_chain_complete in {False, True}
    ):
        candidates.add("H0")
    return candidates


def _secondary_labels(facts: IdentitySnapshotFacts) -> tuple[str, ...]:
    labels: set[str] = set()
    if facts.legacy_pii_present or facts.identity_authority_signal is True:
        labels.add("IDENTITY_FACTS_PRESENT")
    if facts.tenant_present is True:
        labels.add("TENANT_FACTS_PRESENT")
    if _is_unknown_identity(facts):
        labels.add("UNKNOWN_IDENTITY")
    if _is_unknown_tenant(facts):
        labels.add("UNKNOWN_TENANT_RELATION")
    return tuple(sorted(labels))


def classify_snapshot(facts: IdentitySnapshotFacts) -> ClassificationResult:
    candidates = _candidate_classes(facts)
    primary = next((name for name in PRIMARY_PRIORITY if name in candidates), None)
    if primary is None:
        raise InventoryContractError("INVENTORY_UNCLASSIFIED")
    return ClassificationResult(
        primary_class=primary,
        secondary_labels=_secondary_labels(facts),
    )


def _canonical_report_payload(
    *,
    input_count: int,
    class_counts: Mapping[str, int],
    secondary_label_counts: Mapping[str, int],
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "classification_version": _CLASSIFICATION_VERSION,
        "input_count": input_count,
        "class_counts": {name: class_counts.get(name, 0) for name in PRIMARY_CLASSES},
        "secondary_label_counts": {
            name: secondary_label_counts.get(name, 0)
            for name in sorted(ALLOWED_SECONDARY_LABELS)
        },
        "multi_primary_match_count": 0,
        "unclassified_count": 0,
    }


def _report_evidence_payload(report: InventoryReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "classification_version": report.classification_version,
        "status": report.status,
        "total_count": report.input_count,
        "member_count": report.member_count,
        "excluded_non_member_count": report.excluded_non_member_count,
        "class_counts": _suppress_small_counts(report.class_counts),
        "secondary_label_counts": _suppress_small_counts(
            report.secondary_label_counts
        ),
        "multi_primary_match_count": report.multi_primary_match_count,
        "unclassified_count": report.unclassified_count,
        "page_count": report.page_count,
        "processed_count": report.processed_count,
        "batch_status": report.batch_status,
        "snapshot_ceiling_present": report.snapshot_ceiling_present,
        "anonymous_watermark_present": report.anonymous_watermark_present,
        "small_count_present": report.small_count_present,
        "classification_rule_hash": report.classification_rule_hash,
        "code_sha256": report.code_sha256,
        "migration_head": report.migration_head,
    }


def build_inventory_report(facts: Iterable[IdentitySnapshotFacts]) -> InventoryReport:
    fact_items = tuple(facts)
    results = [classify_snapshot(item) for item in fact_items]
    class_counts = Counter(result.primary_class for result in results)
    secondary_counts = Counter(
        label for result in results for label in result.secondary_labels
    )
    payload = _canonical_report_payload(
        input_count=len(results),
        class_counts=class_counts,
        secondary_label_counts=secondary_counts,
    )
    classification_rule_hash = _classification_rule_hash()
    code_sha256 = _code_sha256()
    member_count = sum(item.role == "member" for item in fact_items)
    small_count_present = any(
        0 < value < _SMALL_COUNT_LIMIT
        for value in (*class_counts.values(), *secondary_counts.values())
    )
    report = InventoryReport(
        status="PASS",
        batch_ref=uuid4().hex,
        snapshot_at=datetime.now(UTC).isoformat(),
        snapshot_ceiling_present=False,
        member_count=member_count,
        excluded_non_member_count=len(results) - member_count,
        page_count=1 if results else 0,
        processed_count=len(results),
        batch_status="COMPLETED",
        anonymous_watermark_present=False,
        small_count_present=small_count_present,
        classification_rule_hash=classification_rule_hash,
        code_sha256=code_sha256,
        migration_head=_MIGRATION_HEAD,
        evidence_hash="",
        **payload,
    )
    report = replace(
        report,
        evidence_hash=_sha256_json(_report_evidence_payload(report)),
    )
    validate_inventory_report(report)
    return report


def validate_inventory_report(report: InventoryReport) -> None:
    if set(report.class_counts) != set(PRIMARY_CLASSES):
        raise InventoryContractError("INVENTORY_CLASS_SET_MISMATCH")
    if set(report.secondary_label_counts) != set(ALLOWED_SECONDARY_LABELS):
        raise InventoryContractError("INVENTORY_REPORT_INCONSISTENT")
    if any(value < 0 for value in report.class_counts.values()) or any(
        value < 0 for value in report.secondary_label_counts.values()
    ):
        raise InventoryContractError("INVENTORY_REPORT_INCONSISTENT")
    if report.input_count != sum(report.class_counts.values()):
        raise InventoryContractError("INVENTORY_COUNT_MISMATCH")
    if report.multi_primary_match_count != 0:
        raise InventoryContractError("INVENTORY_MULTI_PRIMARY")
    if report.unclassified_count != 0:
        raise InventoryContractError("INVENTORY_UNCLASSIFIED")
    expected_member_count = sum(
        report.class_counts[name] for name in PRIMARY_CLASSES if name != "H7"
    )
    expected_small_count_present = any(
        0 < value < _SMALL_COUNT_LIMIT
        for value in (
            *report.class_counts.values(),
            *report.secondary_label_counts.values(),
        )
    )
    try:
        snapshot_at = datetime.fromisoformat(report.snapshot_at)
    except ValueError as exc:
        raise InventoryContractError("INVENTORY_REPORT_INCONSISTENT") from exc
    report_consistent = (
        report.schema_version == _SCHEMA_VERSION
        and report.classification_version == _CLASSIFICATION_VERSION
        and report.status == "PASS"
        and report.member_count == expected_member_count
        and report.excluded_non_member_count == report.class_counts["H7"]
        and report.member_count + report.excluded_non_member_count
        == report.input_count
        and report.processed_count == report.input_count
        and report.page_count == (1 if report.input_count else 0)
        and report.batch_status == "COMPLETED"
        and report.snapshot_ceiling_present is False
        and report.anonymous_watermark_present is False
        and report.small_count_present is expected_small_count_present
        and report.classification_rule_hash == _classification_rule_hash()
        and report.code_sha256 == _code_sha256()
        and report.migration_head == _MIGRATION_HEAD
        and len(report.batch_ref) == 32
        and all(character in "0123456789abcdef" for character in report.batch_ref)
        and snapshot_at.tzinfo is not None
        and report.evidence_hash == _sha256_json(_report_evidence_payload(report))
    )
    if not report_consistent:
        raise InventoryContractError("INVENTORY_REPORT_INCONSISTENT")
    public_document = report.to_public_dict()
    if set(public_document) != PUBLIC_REPORT_KEYS:
        raise InventoryContractError("INVENTORY_PUBLIC_SCHEMA_MISMATCH")
    if find_sensitive_output_keys(public_document):
        raise InventoryContractError("INVENTORY_SENSITIVE_OUTPUT")


def find_sensitive_output_keys(value: object, *, path: str = "$") -> tuple[str, ...]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                findings.append(f"{path}.{key}")
            findings.extend(find_sensitive_output_keys(child, path=f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            findings.extend(find_sensitive_output_keys(child, path=f"{path}[{index}]"))
    return tuple(findings)
