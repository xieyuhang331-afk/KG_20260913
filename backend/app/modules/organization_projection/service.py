import asyncio
import base64
import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from sqlalchemy import text

from .domain import build_organization_projection_row
from .models import OrganizationProjectionCheckpoint, OrganizationProjectionGeneration


class ProjectionError(Exception): pass
class ProjectionCheckpointConflict(ProjectionError): pass
class ProjectionDigestKeyUnavailable(ProjectionError): pass
class ProjectionLeaseConflict(ProjectionError): pass
class ProjectionCommitOutcomeUnknown(ProjectionError): pass
class ProjectionUnavailable(ProjectionError): pass


class BuildState(str, Enum):
    BUILDING = "BUILDING"
    BUILD_COMPLETE = "BUILD_COMPLETE"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class ShadowState(str, Enum):
    SHADOW_RUNNING = "SHADOW_RUNNING"
    SHADOW_PASSED = "SHADOW_PASSED"
    SHADOW_FAILED = "SHADOW_FAILED"
    READY = "READY"


_SHADOW_COMPONENTS = {
    "organization": {"source", "mapping", "projection", "coverage", "evidence"},
    "health": {"source", "mapping", "projection", "coverage", "currentness", "selection", "evidence"},
}

_ORG_BLOCKERS = {
    "ORG_SOURCE_MISSING", "ORG_SOURCE_DUPLICATE", "ORG_CHAIN_INVALID",
    "ORG_ROOT_INVALID", "ORG_JSONB_INVALID", "ORG_ROW_MISMATCH",
    "ORG_ROW_DIGEST_MISMATCH", "ORG_MAPPING_CORRUPT",
    "ORG_MAPPING_TARGET_INVALID", "ORG_MAPPING_SCOPE_MISMATCH",
    "ORG_COVERAGE_MISMATCH", "ORG_GENERATION_IDENTITY_MISMATCH",
    "ORG_UNPROVEN_SOURCE_DRIFT",
}
_ORG_REVIEW = {"ORG_MAPPING_REVIEW_REQUIRED", "ORG_MAPPING_CONFLICT_UNRESOLVED"}
_ORG_INFO = {"ORG_POST_HWM_NEW_SOURCE", "ORG_POST_BUILD_VALID_VERSION_ADVANCE"}

_SHADOW_GENERATION_POSTIMAGE_FIELDS = (
    "id", "projection_version", "status", "high_watermark",
    "digest_key_id", "input_digest", "updated_at", "completed_at",
    "version", "current_shadow_run_id", "shadow_success_count",
    "ready_at", "ready_operation_id",
)


@dataclass(frozen=True, slots=True)
class ShadowEvidence:
    domain: str
    generation_id: int
    projection_version: int
    rule_version: str
    high_watermark: dict
    high_watermark_digest: str
    digest_key_id: str
    generation_input_digest: str
    source_digest: str
    mapping_digest: str
    projection_digest: str
    coverage_digest: str
    currentness_digest: str | None
    selection_digest: str | None
    blocker_count: int
    review_required_count: int
    informational_count: int
    category_counts: dict[str, int]
    counts: dict[str, int]
    evidence_digest: str


def _component_payload(*, generation_id, high_watermark_digest, items, projection_version, rule_version):
    return {
        "generation_id": generation_id,
        "high_watermark_digest": high_watermark_digest,
        "item_count": len(items),
        "items": list(items),
        "projection_version": projection_version,
        "rule_version": rule_version,
    }


def _count_categories(categories: list[str], allowed: set[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for category in categories:
        if category not in allowed:
            raise ProjectionUnavailable("Projection shadow evidence is unavailable") from None
        result[category] = result.get(category, 0) + 1
    return dict(sorted(result.items()))


def _evidence_payload(evidence: dict) -> dict:
    keys = {
        "domain", "generation_id", "projection_version", "rule_version",
        "high_watermark", "high_watermark_digest", "digest_key_id",
        "generation_input_digest", "source_digest", "mapping_digest",
        "projection_digest", "coverage_digest", "currentness_digest",
        "selection_digest", "blocker_count", "review_required_count",
        "informational_count", "category_counts",
    }
    if set(evidence) != keys:
        raise ProjectionUnavailable("Projection shadow evidence is unavailable") from None
    return evidence


def build_organization_shadow_evidence(
    *, generation_id: int, projection_version: int, high_watermark: dict,
    digest_key_id: str, generation_input_digest: str, source_chains,
    mappings, projected_rows, digest_key: bytes, post_hwm_sources=(),
) -> ShadowEvidence:
    if set(high_watermark) != {"max_organization_id"}:
        raise ProjectionUnavailable("Projection shadow evidence is unavailable") from None
    rule_version = "organization-projection-v1"
    high_watermark_digest = hashlib.sha256(_canonical_shadow_payload(high_watermark)).hexdigest().upper()
    categories: list[str] = []
    projected = {}
    for row in projected_rows:
        identity = row.organization_id
        if identity in projected:
            categories.append("ORG_ROW_MISMATCH")
        projected[identity] = row
    advanced_sources = {
        source.id: source for source in post_hwm_sources
        if source.id <= high_watermark["max_organization_id"]
    }
    advanced = set()
    advanced_sources_by_leaf = {}
    valid_current_rows = {}
    for chain in source_chains:
        projected_row = projected.get(chain[0].id)
        if projected_row is None:
            continue
        try:
            valid_current_rows[chain[0].id] = build_organization_projection_row(
                chain=chain, digest_key=digest_key,
            )
        except Exception:
            categories.append("ORG_CHAIN_INVALID")
            continue
        if tuple(node.id for node in reversed(chain)) != tuple(projected_row.path_ids):
            categories.append("ORG_UNPROVEN_SOURCE_DRIFT")
            continue
        baseline = dict(zip(projected_row.path_ids, projected_row.path_versions))
        if len(projected_row.path_ids) != 4 or len(projected_row.path_versions) != 4:
            categories.append("ORG_UNPROVEN_SOURCE_DRIFT")
            continue
        changed_ids = []
        invalid_change = False
        for source in chain:
            changed = advanced_sources.get(source.id)
            baseline_version = baseline.get(source.id)
            if changed is not None and baseline_version is not None and source.version > baseline_version:
                changed_ids.append(source.id)
            elif changed is not None:
                categories.append("ORG_UNPROVEN_SOURCE_DRIFT")
                invalid_change = True
        if changed_ids and not invalid_change:
            advanced.add(projected_row.organization_id)
            advanced_sources_by_leaf[projected_row.organization_id] = tuple(changed_ids)
    expected = {}
    for chain in source_chains:
        row = valid_current_rows.get(chain[0].id)
        if row is None:
            continue
        if row.organization_id in advanced:
            continue
        if row.organization_id in expected:
            categories.append("ORG_SOURCE_DUPLICATE")
        expected[row.organization_id] = row
    for identity in advanced:
        actual = projected[identity]
        payload = {
            "organization_id": actual.organization_id, "parent_id": actual.parent_id,
            "org_code": actual.org_code, "org_name": actual.org_name,
            "org_type": actual.org_type, "status": actual.status,
            "sort_order": actual.sort_order, "source_version": actual.source_version,
            "path_ids": list(actual.path_ids), "path_codes": list(actual.path_codes),
            "path_versions": list(actual.path_versions),
            "compatibility_mode": actual.compatibility_mode,
            "scope_eligible": actual.scope_eligible,
        }
        expected_digest = hmac.new(
            digest_key, b"kg:projection:organization:core-row:v1\0" + _canonical_shadow_payload(payload),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_digest, actual.row_digest):
            categories.append("ORG_ROW_DIGEST_MISMATCH")
        expected[identity] = actual
    for identity in sorted(set(expected) | set(projected)):
        source = expected.get(identity)
        actual = projected.get(identity)
        if source is None:
            categories.append("ORG_SOURCE_MISSING")
        elif actual is None:
            categories.append("ORG_ROW_MISMATCH")
        else:
            source_value = asdict(source)
            actual_value = asdict(actual) if hasattr(actual, "__dataclass_fields__") else {
                key: getattr(actual, key) for key in source_value
            }
            actual_value["path_ids"] = tuple(actual_value["path_ids"])
            actual_value["path_codes"] = tuple(actual_value["path_codes"])
            if source_value != actual_value:
                categories.append("ORG_ROW_DIGEST_MISMATCH" if source.row_digest != actual.row_digest else "ORG_ROW_MISMATCH")
    mapping_items = []
    for mapping in mappings:
        payload = {key: getattr(mapping, key) for key in (
            "legacy_tenant_id", "legacy_org_id", "canonical_organization_id",
            "mapping_version", "source_fingerprint", "digest_key_id",
            "disposition", "reason_code",
        )}
        if mapping.disposition == "MAPPED":
            target = expected.get(mapping.canonical_organization_id)
            if target is None:
                categories.append("ORG_MAPPING_TARGET_INVALID")
            elif not target.scope_eligible:
                categories.append("ORG_MAPPING_SCOPE_MISMATCH")
        elif mapping.disposition == "REVIEW_REQUIRED":
            categories.append("ORG_MAPPING_REVIEW_REQUIRED")
        elif mapping.disposition == "CONFLICT":
            categories.append("ORG_MAPPING_CONFLICT_UNRESOLVED")
        mapping_items.append(hmac.new(digest_key, b"kg:projection:shadow:organization:mapping-item:v1\0" + _canonical_shadow_payload(payload), hashlib.sha256).hexdigest())
    classified_advances = set()
    for source in post_hwm_sources:
        if any(source.id in values for values in advanced_sources_by_leaf.values()):
            category = "ORG_POST_BUILD_VALID_VERSION_ADVANCE"
        else:
            projected_version = projected[source.id].source_version if source.id in projected else None
            category = classify_organization_post_hwm(
                source_id=source.id, max_id=high_watermark["max_organization_id"],
                source_version=source.version,
                projected_version=projected_version,
                updated_at=source.updated_at,
            )
        if category and (category, source.id) not in classified_advances:
            categories.append(category)
            classified_advances.add((category, source.id))
    source_items = [expected[key].row_digest for key in sorted(expected)]
    projection_items = [projected[key].row_digest for key in sorted(projected)]
    counts = {
        "source_count": len(source_chains), "eligible_count": len(expected),
        "projection_count": len(projected),
        "coverage_numerator": len(set(expected) & set(projected)),
        "coverage_denominator": len(expected),
    }
    if counts["coverage_numerator"] != counts["coverage_denominator"]:
        categories.append("ORG_COVERAGE_MISMATCH")
    common = dict(generation_id=generation_id, high_watermark_digest=high_watermark_digest, projection_version=projection_version, rule_version=rule_version)
    source_digest = shadow_component_digest(domain="organization", component="source", payload=_component_payload(items=source_items, **common), key=digest_key)
    mapping_digest = shadow_component_digest(domain="organization", component="mapping", payload=_component_payload(items=sorted(mapping_items), **common), key=digest_key)
    projection_digest = shadow_component_digest(domain="organization", component="projection", payload=_component_payload(items=projection_items, **common), key=digest_key)
    coverage_digest = shadow_component_digest(domain="organization", component="coverage", payload={**common, "counts": counts}, key=digest_key)
    category_counts = _count_categories(categories, _ORG_BLOCKERS | _ORG_REVIEW | _ORG_INFO)
    evidence_values = {
        "domain": "organization", "generation_id": generation_id,
        "projection_version": projection_version, "rule_version": rule_version,
        "high_watermark": high_watermark, "high_watermark_digest": high_watermark_digest,
        "digest_key_id": digest_key_id, "generation_input_digest": generation_input_digest,
        "source_digest": source_digest, "mapping_digest": mapping_digest,
        "projection_digest": projection_digest, "coverage_digest": coverage_digest,
        "currentness_digest": None, "selection_digest": None,
        "blocker_count": sum(category_counts.get(x, 0) for x in _ORG_BLOCKERS),
        "review_required_count": sum(category_counts.get(x, 0) for x in _ORG_REVIEW),
        "informational_count": sum(category_counts.get(x, 0) for x in _ORG_INFO),
        "category_counts": category_counts,
    }
    evidence_digest = shadow_component_digest(domain="organization", component="evidence", payload=_evidence_payload(evidence_values), key=digest_key)
    return ShadowEvidence(**evidence_values, counts=counts, evidence_digest=evidence_digest)


def _canonical_shadow_payload(payload: dict) -> bytes:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ProjectionUnavailable("Projection shadow evidence is unavailable") from None
    return encoded.encode("utf-8")


def shadow_component_digest(*, domain: str, component: str, payload: dict, key: bytes) -> str:
    if domain not in _SHADOW_COMPONENTS or component not in _SHADOW_COMPONENTS[domain] or not isinstance(key, bytes) or len(key) != 32 or not isinstance(payload, dict):
        raise ProjectionUnavailable("Projection shadow evidence is unavailable") from None
    separator = f"kg:projection:shadow:{domain}:{component}:v1\0".encode("ascii")
    return hmac.new(key, separator + _canonical_shadow_payload(payload), hashlib.sha256).hexdigest().upper()


def require_ready_evidence(*, first: dict, second: dict) -> None:
    required = {"run_id", "evidence_digest", "blocker_count", "review_required_count"}
    if set(first) != required or set(second) != required or first["run_id"] == second["run_id"]:
        raise ProjectionCheckpointConflict("Projection shadow evidence conflicts")
    if first["evidence_digest"] != second["evidence_digest"] or any(value.get("blocker_count") != 0 or value.get("review_required_count") != 0 for value in (first, second)):
        raise ProjectionCheckpointConflict("Projection shadow evidence conflicts")


def _persisted_evidence_payload(run, *, domain: str) -> dict:
    return {
        "domain": domain, "generation_id": run.generation_id,
        "projection_version": run.projection_version, "rule_version": run.rule_version,
        "high_watermark": run.high_watermark,
        "high_watermark_digest": run.high_watermark_digest,
        "digest_key_id": run.digest_key_id,
        "generation_input_digest": run.generation_input_digest,
        "source_digest": run.source_digest, "mapping_digest": run.mapping_digest,
        "projection_digest": run.projection_digest,
        "coverage_digest": run.coverage_digest,
        "currentness_digest": getattr(run, "currentness_digest", None),
        "selection_digest": getattr(run, "selection_digest", None),
        "blocker_count": run.blocker_count,
        "review_required_count": run.review_required_count,
        "informational_count": run.informational_count,
        "category_counts": run.category_counts,
    }


def verify_persisted_shadow_evidence(*, run, domain: str, key: bytes) -> None:
    expected = shadow_component_digest(
        domain=domain, component="evidence",
        payload=_evidence_payload(_persisted_evidence_payload(run, domain=domain)),
        key=key,
    )
    if not hmac.compare_digest(expected, run.evidence_digest or ""):
        raise ProjectionCheckpointConflict("Projection shadow evidence conflicts")


def classify_organization_post_hwm(*, source_id: int, max_id: int, source_version: int, projected_version: int | None, updated_at: datetime) -> str | None:
    if source_id > max_id:
        return "ORG_POST_HWM_NEW_SOURCE"
    if projected_version is not None and source_version > projected_version:
        return "ORG_POST_BUILD_VALID_VERSION_ADVANCE"
    if projected_version is not None and source_version == projected_version:
        return "ORG_UNPROVEN_SOURCE_DRIFT"
    return None


class ShadowOperationLedger:
    def __init__(self, *, status: str = "BUILD_COMPLETE"):
        self.status = status
        self._operations = {}

    def record(self, operation_id: str, preimage: dict, postimage: dict) -> dict:
        existing = self._operations.get(operation_id)
        if existing is not None:
            if existing[0] != preimage:
                raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
            return existing[1]
        self._operations[operation_id] = (dict(preimage), dict(postimage))
        return postimage

    def transition(self, target: str) -> None:
        allowed = {
            "BUILD_COMPLETE": {"SHADOW_RUNNING"},
            "SHADOW_RUNNING": {"SHADOW_PASSED", "SHADOW_FAILED"},
            "SHADOW_FAILED": {"SHADOW_RUNNING"},
            "SHADOW_PASSED": {"SHADOW_RUNNING", "READY"},
            "READY": set(),
        }
        if target not in allowed.get(self.status, set()):
            raise ProjectionCheckpointConflict("Projection shadow state conflicts")
        self.status = target


def _shadow_digest(value: dict) -> str:
    return hashlib.sha256(_canonical_shadow_payload(value)).hexdigest().upper()


_SHADOW_OPERATION_FIELDS = {
    "SHADOW_START": ("generation_id", "run_id", "validator_id"),
    "SHADOW_HEARTBEAT": ("generation_id", "run_id", "validator_id", "lease_epoch"),
    "SHADOW_TAKEOVER": ("generation_id", "run_id", "validator_id", "lease_epoch"),
    "SHADOW_COMPLETE": ("generation_id", "run_id"),
    "SHADOW_FAIL": ("generation_id", "run_id"),
    "GENERATION_READY": ("generation_id", "expected_version"),
}


def shadow_operation_preimage(action: str, **values) -> dict:
    fields = _SHADOW_OPERATION_FIELDS.get(action)
    if fields is None or set(values) != set(fields):
        raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
    return {"action": action, **{field: values[field] for field in fields}}


def verify_shadow_replay(*, audit, action: str, preimage: dict, postimage: dict | None = None) -> None:
    if (
        audit.action != action
        or not hmac.compare_digest(audit.preimage_digest, _shadow_digest(preimage))
        or not isinstance(audit.postimage_digest, str)
        or not __import__("re").fullmatch(r"[0-9A-F]{64}", audit.postimage_digest)
        or (postimage is not None and not hmac.compare_digest(audit.postimage_digest, _shadow_digest(postimage)))
    ):
        raise ProjectionCheckpointConflict("Projection shadow operation conflicts")


_EVIDENCE_IDENTITY_FIELDS = (
    "generation_id", "high_watermark", "high_watermark_digest",
    "projection_version", "rule_version", "digest_key_id",
    "generation_input_digest", "source_digest", "mapping_digest",
    "projection_digest", "coverage_digest", "currentness_digest",
    "selection_digest", "evidence_digest", "category_counts",
    "blocker_count", "review_required_count", "informational_count",
)

_EVIDENCE_COUNT_FIELDS = (
    "source_count", "eligible_count", "projection_count", "coverage_numerator",
    "coverage_denominator", "current_fact_count", "projection_fact_count",
    "expected_selection_count", "actual_selection_count",
    "fact_coverage_numerator", "fact_coverage_denominator",
    "selection_coverage_numerator", "selection_coverage_denominator",
)


def _same_shadow_evidence(first, second) -> bool:
    return all(
        getattr(first, field, None) == getattr(second, field, None)
        for field in _EVIDENCE_IDENTITY_FIELDS + _EVIDENCE_COUNT_FIELDS
    )


def next_shadow_success_count(*, passed: bool, previous_count: int, previous_run, current_run) -> int:
    if not passed:
        return 0
    if (
        previous_run is not None
        and previous_run.run_sequence + 1 == current_run.run_sequence
        and _same_shadow_evidence(previous_run, current_run)
    ):
        return min(previous_count + 1, 2)
    return 1


def _shadow_postimage(*, generation, run) -> dict:
    def timestamp(value):
        return None if value is None else value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    values = {
        "generation_id": generation.id,
        "generation_projection_version": generation.projection_version,
        "generation_high_watermark": generation.high_watermark,
        "generation_digest_key_id": generation.digest_key_id,
        "generation_input_digest": generation.input_digest,
        "generation_updated_at": timestamp(generation.updated_at),
        "generation_completed_at": timestamp(generation.completed_at),
        "generation_status": generation.status,
        "generation_version": generation.version,
        "current_shadow_run_id": generation.current_shadow_run_id,
        "shadow_success_count": generation.shadow_success_count,
        "ready_at": timestamp(generation.ready_at),
        "ready_operation_id": generation.ready_operation_id,
        "run_id": run.run_id,
        "run_sequence": run.run_sequence,
        "run_status": run.status,
        "run_version": run.version,
        "rule_version": run.rule_version,
        "high_watermark": run.high_watermark,
        "high_watermark_digest": run.high_watermark_digest,
        "run_digest_key_id": run.digest_key_id,
        "run_generation_input_digest": run.generation_input_digest,
        "validator_id": run.validator_id,
        "lease_epoch": run.lease_epoch,
        "lease_expires_at": timestamp(run.lease_expires_at),
        "started_at": timestamp(run.started_at),
        "completed_at": timestamp(run.completed_at),
        "complete_operation_id": run.complete_operation_id,
        "start_operation_id": run.start_operation_id,
        "evidence_digest": run.evidence_digest,
    }
    for field in _EVIDENCE_IDENTITY_FIELDS + _EVIDENCE_COUNT_FIELDS:
        if field != "generation_id" and hasattr(run, field):
            values[field] = getattr(run, field)
    return values


class ProjectionShadowService:
    def __init__(self, *, domain, uow_factory, confirmation_uow_factory, keyring, lock_connection_factory):
        if domain not in {"organization", "health"}:
            raise ProjectionUnavailable("Projection shadow is unavailable")
        self.domain = domain
        self.uow_factory = uow_factory
        self.confirmation_uow_factory = confirmation_uow_factory
        self.keyring = keyring
        self.lock_connection_factory = lock_connection_factory

    def _lock(self, generation_id):
        return ProjectionSessionLock(
            self.lock_connection_factory,
            _builder_lock_key(f"{self.domain}:shadow:{generation_id}"),
        )

    async def shadow_start(self, *, generation_id, run_id, validator_id, operation_id):
        request = shadow_operation_preimage(
            "SHADOW_START", generation_id=generation_id, run_id=run_id,
            validator_id=validator_id,
        )
        async with self._lock(generation_id), self.uow_factory() as uow:
            existing = await uow.repository.get_shadow_audit(operation_id)
            if existing is not None:
                run = await uow.repository.get_shadow_run(existing.run_id)
                generation = await uow.repository.get_shadow_generation(generation_id)
                if run is None or generation is None:
                    raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
                verify_shadow_replay(audit=existing, action="SHADOW_START", preimage=request)
                return run.run_id
            generation = await uow.repository.get_shadow_generation(generation_id, lock=True)
            if generation is None or generation.status not in {"BUILD_COMPLETE", "SHADOW_FAILED", "SHADOW_PASSED"}:
                raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
            sequence = await uow.repository.next_shadow_sequence(generation_id)
            now = datetime.now(UTC)
            model = __import__(
                f"app.modules.{self.domain + '_projection' if self.domain == 'organization' else 'health_projection'}.models",
                fromlist=["x"],
            )
            run_class = getattr(model, f"{self.domain.title()}ProjectionShadowRun")
            audit_class = getattr(model, f"{self.domain.title()}ProjectionShadowAudit")
            rule_version = "organization-projection-v1" if self.domain == "organization" else "health-daily-selection-v1"
            run = run_class(
                run_id=run_id, generation_id=generation_id, run_sequence=sequence,
                status="RUNNING", projection_version=generation.projection_version,
                rule_version=rule_version, high_watermark=generation.high_watermark,
                high_watermark_digest=hashlib.sha256(_canonical_shadow_payload(generation.high_watermark)).hexdigest().upper(),
                digest_key_id=generation.digest_key_id,
                generation_input_digest=generation.input_digest,
                start_operation_id=operation_id, validator_id=validator_id,
                lease_epoch=0, lease_expires_at=now + timedelta(seconds=60), version=1,
            )
            generation.status = "SHADOW_RUNNING"
            generation.current_shadow_run_id = run_id
            generation.updated_at = now
            generation.version += 1
            postimage = _shadow_postimage(generation=generation, run=run)
            audit = audit_class(
                run_id=run_id, generation_id=generation_id, operation_id=operation_id,
                action="SHADOW_START", preimage_digest=_shadow_digest(request),
                postimage_digest=_shadow_digest(postimage), evidence_digest=None,
            )
            await uow.repository.add_shadow_run(run)
            await uow.repository.add_shadow_audit(audit)
            await uow.commit()
            return run_id

    async def shadow_complete(self, *, generation_id, run_id, operation_id):
        async with self._lock(generation_id), self.uow_factory() as uow:
            existing = await uow.repository.get_shadow_audit(operation_id)
            if existing is not None:
                run = await uow.repository.get_shadow_run(existing.run_id)
                generation = await uow.repository.get_shadow_generation(generation_id)
                if generation is None or run is None or run.complete_operation_id != operation_id:
                    raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
                action = "SHADOW_COMPLETE" if run.status == "PASSED" else "SHADOW_FAIL"
                verify_shadow_replay(
                    audit=existing, action=action,
                    preimage=shadow_operation_preimage(action, generation_id=generation_id, run_id=run_id),
                )
                return run.status
            generation = await uow.repository.get_shadow_generation(generation_id, lock=True)
            run = await uow.repository.get_shadow_run(run_id, lock=True)
            if generation is None or run is None or generation.status != "SHADOW_RUNNING" or generation.current_shadow_run_id != run_id or run.status != "RUNNING":
                raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
            key = self.keyring.stored(generation.digest_key_id)
            inputs = await uow.repository.load_shadow_inputs(generation)
            if self.domain == "organization":
                evidence = build_organization_shadow_evidence(
                    generation_id=generation.id, projection_version=generation.projection_version,
                    high_watermark=generation.high_watermark, digest_key_id=generation.digest_key_id,
                    generation_input_digest=generation.input_digest, source_chains=inputs[0],
                    mappings=inputs[1], projected_rows=inputs[2], post_hwm_sources=inputs[3], digest_key=key,
                )
            else:
                from app.modules.health_projection.service import build_health_shadow_evidence
                evidence = build_health_shadow_evidence(
                    generation_id=generation.id, projection_version=generation.projection_version,
                    high_watermark=generation.high_watermark, digest_key_id=generation.digest_key_id,
                    generation_input_digest=generation.input_digest, current_facts=inputs[0],
                    mappings=inputs[1], projected_rows=inputs[2], selections=inputs[3],
                    visibility_rows=inputs[4], post_hwm_facts=inputs[5], digest_key=key,
                )
            for field, value in asdict(evidence).items():
                if field in {"domain", "generation_id", "projection_version", "rule_version", "high_watermark", "high_watermark_digest", "digest_key_id", "generation_input_digest", "counts"}:
                    continue
                setattr(run, field, value)
            for field, value in evidence.counts.items():
                setattr(run, field, value)
            passed = evidence.blocker_count == 0 and evidence.review_required_count == 0
            now = datetime.now(UTC)
            run.status = "PASSED" if passed else "FAILED"
            run.complete_operation_id = operation_id
            run.completed_at = now
            run.lease_expires_at = None
            run.version += 1
            previous = generation.shadow_success_count
            previous_runs = await uow.repository.passed_shadow_runs(generation_id)
            previous_run = next((item for item in reversed(previous_runs) if item.run_id != run_id), None)
            generation.status = "SHADOW_PASSED" if passed else "SHADOW_FAILED"
            generation.shadow_success_count = next_shadow_success_count(
                passed=passed, previous_count=previous,
                previous_run=previous_run, current_run=run,
            )
            generation.updated_at = now
            generation.version += 1
            model = __import__(
                f"app.modules.{self.domain + '_projection' if self.domain == 'organization' else 'health_projection'}.models",
                fromlist=["x"],
            )
            audit_class = getattr(model, f"{self.domain.title()}ProjectionShadowAudit")
            action = "SHADOW_COMPLETE" if passed else "SHADOW_FAIL"
            preimage = shadow_operation_preimage(action, generation_id=generation_id, run_id=run_id)
            postimage = _shadow_postimage(generation=generation, run=run)
            await uow.repository.add_shadow_audit(audit_class(
                run_id=run_id, generation_id=generation_id, operation_id=operation_id,
                action=action,
                preimage_digest=_shadow_digest(preimage), postimage_digest=_shadow_digest(postimage),
                evidence_digest=evidence.evidence_digest,
            ))
            await uow.commit()
            return run.status

    async def heartbeat_shadow(self, *, generation_id, run_id, validator_id, lease_epoch, operation_id):
        preimage = shadow_operation_preimage(
            "SHADOW_HEARTBEAT", generation_id=generation_id, run_id=run_id,
            validator_id=validator_id, lease_epoch=lease_epoch,
        )
        async with self.uow_factory() as uow:
            existing = await uow.repository.get_shadow_audit(operation_id)
            if existing is not None:
                run = await uow.repository.get_shadow_run(existing.run_id)
                generation = await uow.repository.get_shadow_generation(generation_id)
                if run is None or generation is None:
                    raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
                verify_shadow_replay(audit=existing, action="SHADOW_HEARTBEAT", preimage=preimage)
                return
            run = await uow.repository.get_shadow_run(run_id)
            generation = await uow.repository.get_shadow_generation(generation_id)
            if generation is None or run is None or run.generation_id != generation_id:
                raise ProjectionLeaseConflict("Projection shadow lease conflicts")
            expires_at = datetime.now(UTC) + timedelta(seconds=60)
            if not await uow.repository.heartbeat_shadow(
                run_id=run_id, validator_id=validator_id,
                lease_epoch=lease_epoch, expires_at=expires_at,
            ):
                raise ProjectionLeaseConflict("Projection shadow lease conflicts")
            run = await uow.repository.get_shadow_run(run_id)
            model = __import__(f"app.modules.{self.domain + '_projection' if self.domain == 'organization' else 'health_projection'}.models", fromlist=["x"])
            audit_class = getattr(model, f"{self.domain.title()}ProjectionShadowAudit")
            postimage = _shadow_postimage(generation=generation, run=run)
            await uow.repository.add_shadow_audit(audit_class(
                run_id=run_id, generation_id=generation_id, operation_id=operation_id,
                action="SHADOW_HEARTBEAT", preimage_digest=_shadow_digest(preimage),
                postimage_digest=_shadow_digest(postimage), evidence_digest=None,
            ))
            await uow.commit()

    async def shadow_takeover(self, *, generation_id, run_id, validator_id, expected_lease_epoch, operation_id):
        preimage = shadow_operation_preimage(
            "SHADOW_TAKEOVER", generation_id=generation_id, run_id=run_id,
            validator_id=validator_id, lease_epoch=expected_lease_epoch,
        )
        async with self._lock(generation_id), self.uow_factory() as uow:
            existing = await uow.repository.get_shadow_audit(operation_id)
            if existing is not None:
                run = await uow.repository.get_shadow_run(existing.run_id)
                generation = await uow.repository.get_shadow_generation(generation_id)
                if run is None or generation is None:
                    raise ProjectionCheckpointConflict("Projection shadow operation conflicts")
                verify_shadow_replay(audit=existing, action="SHADOW_TAKEOVER", preimage=preimage)
                return expected_lease_epoch + 1
            generation = await uow.repository.get_shadow_generation(generation_id, lock=True)
            run = await uow.repository.get_shadow_run(run_id, lock=True)
            if generation is None or run is None or generation.current_shadow_run_id != run_id or generation.status != "SHADOW_RUNNING" or run.status != "RUNNING" or run.lease_epoch != expected_lease_epoch or run.lease_expires_at >= datetime.now(UTC):
                raise ProjectionLeaseConflict("Projection shadow lease conflicts")
            self.keyring.stored(generation.digest_key_id)
            run.validator_id = validator_id
            run.lease_epoch += 1
            run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
            run.version += 1
            model = __import__(f"app.modules.{self.domain + '_projection' if self.domain == 'organization' else 'health_projection'}.models", fromlist=["x"])
            audit_class = getattr(model, f"{self.domain.title()}ProjectionShadowAudit")
            await uow.repository.add_shadow_audit(audit_class(
                run_id=run_id, generation_id=generation_id, operation_id=operation_id,
                action="SHADOW_TAKEOVER", preimage_digest=_shadow_digest(preimage),
                postimage_digest=_shadow_digest(_shadow_postimage(generation=generation, run=run)),
                evidence_digest=None,
            ))
            await uow.commit()
            return run.lease_epoch

    async def confirm(self, *, generation_id, run_id, operation_id, action, preimage, postimage):
        try:
            async with self.confirmation_uow_factory() as uow:
                generation = await uow.repository.get_shadow_generation(generation_id)
                run = await uow.repository.get_shadow_run(run_id)
                audit = await uow.repository.get_shadow_audit(operation_id)
                if audit is None:
                    return ConfirmationResult.ROLLED_BACK if run is None else ConfirmationResult.UNKNOWN
                if generation is None or run is None or run.generation_id != generation_id or audit.run_id != run_id:
                    return ConfirmationResult.UNKNOWN
                try:
                    verify_shadow_replay(
                        audit=audit, action=action, preimage=preimage,
                        postimage=postimage,
                    )
                except ProjectionCheckpointConflict:
                    return ConfirmationResult.UNKNOWN
                if _shadow_postimage(generation=generation, run=run) != postimage:
                    return ConfirmationResult.UNKNOWN
                if audit.evidence_digest != run.evidence_digest:
                    return ConfirmationResult.UNKNOWN
                return ConfirmationResult.COMMITTED
        except asyncio.CancelledError:
            raise
        except Exception:
            return ConfirmationResult.UNKNOWN


class ProjectionReadyGate:
    def __init__(self, *, domain, uow_factory, confirmation_uow_factory, keyring, lock_connection_factory):
        self.domain = domain
        self.uow_factory = uow_factory
        self.confirmation_uow_factory = confirmation_uow_factory
        self.keyring = keyring
        self.lock_connection_factory = lock_connection_factory

    async def mark_ready(self, *, generation_id, expected_version, operation_id):
        preimage = shadow_operation_preimage(
            "GENERATION_READY", generation_id=generation_id,
            expected_version=expected_version,
        )
        lock = ProjectionSessionLock(
            self.lock_connection_factory,
            _builder_lock_key(f"{self.domain}:ready:{generation_id}"),
        )
        async with lock, self.uow_factory() as uow:
            generation = await uow.repository.get_shadow_generation(generation_id, lock=True)
            existing = await uow.repository.get_shadow_audit(operation_id)
            if existing is not None:
                if generation is not None and generation.status == "READY" and generation.ready_operation_id == operation_id:
                    run = await uow.repository.get_shadow_run(generation.current_shadow_run_id)
                    if run is None:
                        raise ProjectionCheckpointConflict("Projection READY evidence conflicts")
                    verify_shadow_replay(audit=existing, action="GENERATION_READY", preimage=preimage)
                    return
                raise ProjectionCheckpointConflict("Projection READY evidence conflicts")
            if generation is None or generation.status != "SHADOW_PASSED" or generation.shadow_success_count != 2 or generation.version != expected_version:
                raise ProjectionCheckpointConflict("Projection READY evidence conflicts")
            runs = await uow.repository.passed_shadow_runs(generation_id)
            if len(runs) != 2 or runs[1].run_sequence != runs[0].run_sequence + 1:
                raise ProjectionCheckpointConflict("Projection READY evidence conflicts")
            key = self.keyring.stored(generation.digest_key_id)
            verify_persisted_shadow_evidence(run=runs[0], domain=self.domain, key=key)
            verify_persisted_shadow_evidence(run=runs[1], domain=self.domain, key=key)
            first = {"run_id": runs[0].run_id, "evidence_digest": runs[0].evidence_digest, "blocker_count": runs[0].blocker_count, "review_required_count": runs[0].review_required_count}
            second = {"run_id": runs[1].run_id, "evidence_digest": runs[1].evidence_digest, "blocker_count": runs[1].blocker_count, "review_required_count": runs[1].review_required_count}
            require_ready_evidence(first=first, second=second)
            if not _same_shadow_evidence(runs[0], runs[1]):
                raise ProjectionCheckpointConflict("Projection READY evidence conflicts")
            now = datetime.now(UTC)
            generation.status = "READY"
            generation.ready_at = now
            generation.ready_operation_id = operation_id
            generation.updated_at = now
            generation.version += 1
            model = __import__(
                f"app.modules.{self.domain + '_projection' if self.domain == 'organization' else 'health_projection'}.models",
                fromlist=["x"],
            )
            audit_class = getattr(model, f"{self.domain.title()}ProjectionShadowAudit")
            postimage = _shadow_postimage(generation=generation, run=runs[1])
            await uow.repository.add_shadow_audit(audit_class(
                run_id=runs[1].run_id, generation_id=generation_id,
                operation_id=operation_id, action="GENERATION_READY",
                preimage_digest=_shadow_digest(preimage),
                postimage_digest=_shadow_digest(postimage),
                evidence_digest=runs[1].evidence_digest,
            ))
            await uow.commit()

    async def confirm_ready(self, *, generation_id, operation_id, preimage, postimage):
        try:
            async with self.confirmation_uow_factory() as uow:
                generation = await uow.repository.get_shadow_generation(generation_id)
                audit = await uow.repository.get_shadow_audit(operation_id)
                if audit is None:
                    return ConfirmationResult.ROLLED_BACK
                if generation is None or generation.status != "READY":
                    return ConfirmationResult.UNKNOWN
                run = await uow.repository.get_shadow_run(generation.current_shadow_run_id)
                if run is None or generation.shadow_success_count != 2:
                    return ConfirmationResult.UNKNOWN
                try:
                    verify_shadow_replay(
                        audit=audit, action="GENERATION_READY",
                        preimage=preimage, postimage=postimage,
                    )
                except ProjectionCheckpointConflict:
                    return ConfirmationResult.UNKNOWN
                if _shadow_postimage(generation=generation, run=run) != postimage:
                    return ConfirmationResult.UNKNOWN
                return ConfirmationResult.COMMITTED
        except asyncio.CancelledError:
            raise
        except Exception:
            return ConfirmationResult.UNKNOWN
class ConfirmationResult(str, Enum):
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class OperationPostimage:
    status: str
    generation_version: int
    high_watermark: dict
    digest_key_id: str
    checkpoint_digest: str
    checkpoint_version: int
    processed_count: int
    projected_count: int
    skipped_count: int
    remaining_count: int
    row_count: int
    selection_count: int
    completion_evidence: str
    builder_id: str | None = None
    lease_epoch: int = 0
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None
    last_operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class BuildCheckpoint:
    last_source_id: int | None
    processed_count: int
    projected_count: int
    skipped_count: int
    remaining_count: int

    def advance(self, *, source_ids, projected):
        ids = tuple(source_ids)
        invalid = (
            not ids or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in ids)
            or tuple(sorted(set(ids))) != ids
            or (self.last_source_id is not None and ids[0] <= self.last_source_id)
            or projected < 0 or projected > len(ids) or self.remaining_count < len(ids)
        )
        if invalid:
            raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
        return replace(self, last_source_id=ids[-1], processed_count=self.processed_count + len(ids), projected_count=self.projected_count + projected, skipped_count=self.skipped_count + len(ids) - projected, remaining_count=self.remaining_count - len(ids))

    def complete(self):
        if self.remaining_count != 0 or self.processed_count != self.projected_count + self.skipped_count:
            raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
        return self


class ProjectionDigestKeyring:
    def __init__(self, *, current_key_id, keys):
        if not current_key_id or current_key_id not in keys or any(not isinstance(key, str) or not key or not isinstance(value, bytes) or len(value) != 32 for key, value in keys.items()):
            raise ProjectionDigestKeyUnavailable("Projection digest key is unavailable")
        self.current_key_id = current_key_id
        self._keys = dict(keys)

    def stored(self, key_id):
        try: return self._keys[key_id]
        except KeyError: raise ProjectionDigestKeyUnavailable("Projection digest key is unavailable") from None

    @classmethod
    def from_json(cls, current_key_id, raw):
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError
                result[key] = value
            return result
        try:
            values = json.loads(raw, object_pairs_hook=pairs)
            keys = {key: base64.b64decode(value, validate=True) for key, value in values.items()}
            return cls(current_key_id=current_key_id, keys=keys)
        except Exception:
            raise ProjectionDigestKeyUnavailable("Projection digest key is unavailable") from None


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ProjectionHeartbeat:
    def __init__(self, uow_factory): self._uow_factory = uow_factory
    async def beat(self, *, generation_id, builder_id, lease_epoch, operation_id):
        async with self._uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id)
            preimage = {"operation": "heartbeat", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None: return replay
            expires_at = datetime.now(UTC) + timedelta(seconds=60)
            ok = await uow.repository.heartbeat(generation_id=generation_id, builder_id=builder_id, lease_epoch=lease_epoch, expires_at=expires_at, operation_id=operation_id)
            if not ok: raise ProjectionLeaseConflict("Projection lease conflicts")
            postimage = {"generation_id": generation_id, "generation_version": generation.version + 1, "builder_id": builder_id, "lease_epoch": lease_epoch, "lease_expires_at": expires_at.isoformat()}
            await uow.repository.add_audit(generation_id=generation_id, action="projection_heartbeat", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage})
            await uow.commit()
            return postimage


class ProjectionUnitOfWork:
    def __init__(self, session_factory, repository_type, *, isolation_level="REPEATABLE READ"):
        self.factory, self.repository_type, self.isolation_level = session_factory, repository_type, isolation_level
        self.session = None
        self.final = False
    async def __aenter__(self):
        self.session = self.factory()
        await self.session.connection(execution_options={"isolation_level": self.isolation_level})
        self.repository = self.repository_type(self.session)
        return self
    async def __aexit__(self, *_):
        try:
            if not self.final: await self.session.rollback()
        finally: await self.session.close()
    async def commit(self):
        try: await self.session.commit()
        except asyncio.CancelledError: raise
        except Exception: raise ProjectionCommitOutcomeUnknown("Projection commit outcome is unknown") from None
        self.final = True


class ProjectionSessionLock:
    def __init__(self, connection_factory, lock_key: int):
        self.connection_factory, self.lock_key, self.connection = connection_factory, lock_key, None
    async def __aenter__(self):
        self.connection = await self.connection_factory()
        try:
            result = await self.connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.lock_key})
            acquired = bool(result.scalar_one())
            if not acquired: raise ProjectionLeaseConflict("Projection lease conflicts")
            return self
        except asyncio.CancelledError:
            await self._finish_cleanup(); raise
        except ProjectionLeaseConflict:
            await self.connection.close(); self.connection = None; raise
        except Exception:
            await self._invalidate(); raise ProjectionUnavailable("Projection lock is unavailable") from None
    async def __aexit__(self, *_):
        if self.connection is None: return
        try:
            result = await self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.lock_key})
            released = bool(result.scalar_one())
            if not released: raise ProjectionUnavailable("Projection lock is unavailable")
            await self.connection.close(); self.connection = None
        except asyncio.CancelledError:
            await self._finish_cleanup(); raise
        except Exception:
            await self._invalidate(); raise ProjectionUnavailable("Projection lock is unavailable") from None
    async def _invalidate(self):
        connection, self.connection = self.connection, None
        if connection is not None:
            try: await connection.invalidate()
            except Exception:
                try: await connection.close()
                except Exception: pass

    async def _finish_cleanup(self):
        task = asyncio.create_task(self._invalidate())
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        await task


def _builder_lock_key(domain: str, projection_version: int = 1) -> int:
    value = f"basic-projection:{domain}:{projection_version}".encode("ascii")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big", signed=True)


def _validate_checkpoint(generation, checkpoint) -> None:
    if checkpoint is None:
        raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
    if checkpoint.processed_count != checkpoint.projected_count + checkpoint.skipped_count:
        raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
    expected = _digest({
        "last_source_id": checkpoint.last_source_id,
        "processed_count": checkpoint.processed_count,
        "remaining_count": checkpoint.remaining_count,
    })
    if checkpoint.checkpoint_digest != expected:
        raise ProjectionCheckpointConflict("Projection checkpoint conflicts")


def _require_live_lease(generation, *, builder_id, lease_epoch, now=None) -> None:
    current = now or datetime.now(UTC)
    if (
        generation is None
        or generation.status != "BUILDING"
        or generation.builder_id != builder_id
        or generation.lease_epoch != lease_epoch
        or generation.lease_expires_at is None
        or generation.lease_expires_at <= current
    ):
        raise ProjectionLeaseConflict("Projection lease conflicts")


async def _replay_operation(repository, operation_id, preimage):
    existing = await repository.audit_payload(operation_id)
    if existing is None:
        return None
    if existing.get("preimage") != preimage:
        raise ProjectionCheckpointConflict("Projection operation conflicts")
    return existing.get("postimage", {})


def _checkpoint_postimage(checkpoint):
    return {
        "last_source_id": checkpoint.last_source_id,
        "processed_count": checkpoint.processed_count,
        "projected_count": checkpoint.projected_count,
        "skipped_count": checkpoint.skipped_count,
        "remaining_count": checkpoint.remaining_count,
        "checkpoint_digest": checkpoint.checkpoint_digest,
        "checkpoint_version": checkpoint.version,
    }


async def _replay_before_heartbeat(confirmation_uow_factory, operation_id, preimage):
    async with confirmation_uow_factory() as uow:
        return await _replay_operation(uow.repository, operation_id, preimage)


class OrganizationProjectionBuilder:
    def __init__(self, uow_factory, confirmation_uow_factory, keyring: ProjectionDigestKeyring, lock_connection_factory=None):
        self.uow_factory, self.confirmation_uow_factory, self.keyring = uow_factory, confirmation_uow_factory, keyring
        self.lock_connection_factory = lock_connection_factory
        self.heartbeat = ProjectionHeartbeat(uow_factory)

    def _session_lock(self):
        if self.lock_connection_factory is None:
            raise ProjectionUnavailable("Projection lock is unavailable")
        return ProjectionSessionLock(self.lock_connection_factory, _builder_lock_key("organization"))

    async def start(self, *, generation_no: int, builder_id: str, operation_id: str) -> int:
        async with self._session_lock(), self.uow_factory() as uow:
            preimage = {"operation": "start", "generation_no": generation_no, "builder_id": builder_id, "projection_version": 1}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None:
                return int(replay["generation_id"])
            max_id = await uow.repository.max_source_id()
            remaining = await uow.repository.count_sources(max_id)
            hwm = {"max_organization_id": max_id}
            generation = OrganizationProjectionGeneration(projection_version=1, generation_no=generation_no, status="BUILDING", high_watermark=hwm, digest_key_id=self.keyring.current_key_id, input_digest=_digest(hwm), start_operation_id=operation_id, builder_id=builder_id, lease_epoch=0, lease_expires_at=datetime.now(UTC) + timedelta(seconds=60))
            checkpoint = OrganizationProjectionCheckpoint(last_source_id=None, processed_count=0, projected_count=0, skipped_count=0, remaining_count=remaining, checkpoint_digest=_digest({"last_source_id": None, "processed_count": 0, "remaining_count": remaining}), version=1)
            await uow.repository.add_generation(generation, checkpoint)
            postimage = {"generation_id": generation.id, "generation_version": generation.version, "checkpoint_version": checkpoint.version, "high_watermark": hwm, "digest_key_id": generation.digest_key_id}
            await uow.repository.add_audit(generation_id=generation.id, action="projection_generation_started", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage})
            await uow.commit()
            return generation.id

    async def build_page(self, *, generation_id: int, builder_id: str, lease_epoch: int, expected_checkpoint_digest: str, operation_id: str, heartbeat_operation_id: str, page_size: int = 100) -> BuildCheckpoint:
        preimage = {"operation": "build_page", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "checkpoint_digest": expected_checkpoint_digest, "page_size": page_size}
        replay = await _replay_before_heartbeat(self.confirmation_uow_factory, operation_id, preimage)
        if replay is not None:
            return BuildCheckpoint(replay["last_source_id"], replay["processed_count"], replay["projected_count"], replay["skipped_count"], replay["remaining_count"])
        await self.heartbeat.beat(generation_id=generation_id, builder_id=builder_id, lease_epoch=lease_epoch, operation_id=heartbeat_operation_id)
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None:
                return BuildCheckpoint(replay["last_source_id"], replay["processed_count"], replay["projected_count"], replay["skipped_count"], replay["remaining_count"])
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest: raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            key = self.keyring.stored(generation.digest_key_id)
            ids = await uow.repository.list_leaf_ids(after_id=checkpoint.last_source_id, max_id=generation.high_watermark["max_organization_id"], limit=page_size)
            state = BuildCheckpoint(checkpoint.last_source_id, checkpoint.processed_count, checkpoint.projected_count, checkpoint.skipped_count, checkpoint.remaining_count)
            if not ids:
                return state.complete()
            rows = []
            for source_id in ids:
                chain = await uow.repository.load_chain(source_id)
                rows.append(build_organization_projection_row(chain=chain, digest_key=key))
            rows = tuple(rows)
            await uow.repository.add_rows(generation_id, generation.digest_key_id, rows)
            state = state.advance(source_ids=ids, projected=len(rows))
            for name in ("last_source_id", "processed_count", "projected_count", "skipped_count", "remaining_count"):
                setattr(checkpoint, name, getattr(state, name))
            checkpoint.last_operation_id = operation_id
            checkpoint.version += 1
            checkpoint.checkpoint_digest = _digest({"last_source_id": state.last_source_id, "processed_count": state.processed_count, "remaining_count": state.remaining_count})
            generation.version += 1
            generation.updated_at = datetime.now(UTC)
            await uow.repository.add_audit(generation_id=generation_id, action="projection_batch_committed", operation_id=operation_id, payload={"preimage": preimage, "postimage": _checkpoint_postimage(checkpoint)})
            await uow.commit()
            return state

    async def complete(self, *, generation_id: int, builder_id: str, lease_epoch: int, expected_generation_version: int, expected_checkpoint_digest: str, operation_id: str) -> None:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "complete", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "checkpoint_digest": expected_checkpoint_digest, "generation_version": expected_generation_version}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None:
                return
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest or generation.version != expected_generation_version: raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            _validate_checkpoint(generation, checkpoint)
            if await uow.repository.has_remaining_sources(checkpoint.last_source_id, generation.high_watermark["max_organization_id"]):
                raise ProjectionCheckpointConflict("Projection checkpoint conflicts")
            completion_evidence = await uow.repository.validate_completion(generation_id, checkpoint, generation.digest_key_id, self.keyring.stored(generation.digest_key_id), generation.high_watermark)
            generation.status = "BUILD_COMPLETE"; generation.builder_id = None; generation.lease_expires_at = None; generation.completed_at = datetime.now(UTC); generation.version += 1
            postimage = {"status": generation.status, "generation_version": generation.version, "checkpoint_digest": checkpoint.checkpoint_digest, "checkpoint_version": checkpoint.version, "completion_evidence": completion_evidence}
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_build_complete", operation_id=operation_id, payload={"preimage": preimage, "postimage": postimage, "completion_evidence": completion_evidence})
            await uow.commit()

    async def takeover(self, *, generation_id: int, builder_id: str, expected_lease_epoch: int, expected_checkpoint_digest: str, operation_id: str) -> int:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "takeover", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": expected_lease_epoch, "checkpoint_digest": expected_checkpoint_digest}
            replay = await _replay_operation(uow.repository, operation_id, preimage)
            if replay is not None:
                return int(replay["lease_epoch"])
            if generation is None or generation.status != "BUILDING" or generation.lease_epoch != expected_lease_epoch or checkpoint is None or checkpoint.checkpoint_digest != expected_checkpoint_digest or generation.lease_expires_at >= datetime.now(UTC):
                raise ProjectionLeaseConflict("Projection lease conflicts")
            _validate_checkpoint(generation, checkpoint)
            self.keyring.stored(generation.digest_key_id)
            generation.builder_id = builder_id; generation.lease_epoch += 1; generation.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60); generation.version += 1
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_takeover", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"lease_epoch": generation.lease_epoch, "builder_id": builder_id, "generation_version": generation.version}})
            await uow.commit()
            return generation.lease_epoch

    async def fail(self, *, generation_id: int, builder_id: str, lease_epoch: int, expected_generation_version: int, failure_code: str, operation_id: str) -> None:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True); checkpoint = await uow.repository.get_checkpoint(generation_id, lock=True)
            preimage = {"operation": "fail", "generation_id": generation_id, "builder_id": builder_id, "lease_epoch": lease_epoch, "failure_code": failure_code, "generation_version": expected_generation_version}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None: return
            _require_live_lease(generation, builder_id=builder_id, lease_epoch=lease_epoch)
            if generation.version != expected_generation_version: raise ProjectionCheckpointConflict("Projection operation conflicts")
            if failure_code not in {"PROJECTION_SOURCE_INVALID", "PROJECTION_DIGEST_KEY_UNAVAILABLE", "PROJECTION_CHECKPOINT_CONFLICT", "PROJECTION_LEASE_CONFLICT", "PROJECTION_UNAVAILABLE"}: raise ProjectionCheckpointConflict("Projection operation conflicts")
            generation.status = "FAILED"; generation.failure_code = failure_code; generation.completed_at = datetime.now(UTC); generation.builder_id = None; generation.lease_expires_at = None; generation.version += 1
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_failed", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"status": "FAILED", "failure_code": failure_code, "generation_version": generation.version}}); await uow.commit()

    async def supersede(self, *, generation_id: int, expected_version: int, operation_id: str) -> None:
        async with self._session_lock(), self.uow_factory() as uow:
            generation = await uow.repository.get_generation(generation_id, lock=True)
            preimage = {"operation": "supersede", "generation_id": generation_id, "generation_version": expected_version, "status": "BUILD_COMPLETE"}
            if await _replay_operation(uow.repository, operation_id, preimage) is not None: return
            if generation is None or generation.status != "BUILD_COMPLETE" or generation.version != expected_version or not await uow.repository.has_newer_complete_generation(generation): raise ProjectionCheckpointConflict("Projection operation conflicts")
            generation.status = "SUPERSEDED"; generation.version += 1; generation.updated_at = datetime.now(UTC)
            await uow.repository.add_audit(generation_id=generation_id, action="projection_generation_superseded", operation_id=operation_id, payload={"preimage": preimage, "postimage": {"status": "SUPERSEDED", "generation_version": generation.version}}); await uow.commit()

    async def confirm_operation(self, *, generation_id: int | None = None, operation_id: str, expected_preimage: dict, expected_postimage: OperationPostimage) -> ConfirmationResult:
        try:
            async with self.confirmation_uow_factory() as uow:
                audit_record = await uow.repository.audit_record(operation_id)
                if generation_id is None and audit_record is not None:
                    generation_id = audit_record[0]
                generation = None if generation_id is None else await uow.repository.get_generation(generation_id); checkpoint = None if generation_id is None else await uow.repository.get_checkpoint(generation_id); audit = None if audit_record is None else audit_record[1]
                if audit is None:
                    actual_preimage = await uow.repository.operation_preimage(generation, checkpoint, expected_preimage)
                    return ConfirmationResult.ROLLED_BACK if actual_preimage == expected_preimage else ConfirmationResult.UNKNOWN
                if generation is None or checkpoint is None or audit.get("preimage") != expected_preimage: return ConfirmationResult.UNKNOWN
                actual = await uow.repository.operation_postimage(generation, checkpoint, audit)
                if actual == expected_postimage: return ConfirmationResult.COMMITTED
                return ConfirmationResult.UNKNOWN
        except asyncio.CancelledError: raise
        except Exception: return ConfirmationResult.UNKNOWN
