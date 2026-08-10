from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Mapping, Sequence


CANONICAL_LEVELS = ("headquarter", "province", "city", "county")
CANONICAL_TYPES = frozenset(CANONICAL_LEVELS)
CANONICAL_STATUSES = frozenset({"active", "inactive", "archived"})
LEGACY_TYPES = frozenset({"platform", "tenant_org"})
LEGACY_STATUSES = frozenset({"disabled"})
ORG_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{1,49}$")


class OrganizationError(Exception):
    status_code = 409
    code = "ORGANIZATION_STATE_CONFLICT"

    def __init__(self, code: str | None = None, status_code: int | None = None):
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        super().__init__(self.code)


class OrganizationDataCorrupted(OrganizationError):
    status_code = 503
    code = "ORGANIZATION_DATA_CORRUPTED"


class OrganizationCurrentnessInvalid(OrganizationError):
    status_code = 403
    code = "ORGANIZATION_CURRENTNESS_INVALID"


class OrganizationRequestInvalid(OrganizationError):
    status_code = 400
    code = "ORGANIZATION_REQUEST_INVALID"


class OrganizationNotFound(OrganizationError):
    status_code = 404
    code = "ORGANIZATION_NOT_FOUND"


class OrganizationVersionConflict(OrganizationError):
    status_code = 409
    code = "ORGANIZATION_VERSION_CONFLICT"


class OrganizationScopeForbidden(OrganizationError):
    status_code = 403
    code = "ORGANIZATION_SCOPE_FORBIDDEN"


def classify_compatibility(org_type: str, status: str) -> str:
    if org_type in CANONICAL_TYPES and status in CANONICAL_STATUSES:
        return "canonical"
    if (
        org_type in LEGACY_TYPES
        and status in CANONICAL_STATUSES | LEGACY_STATUSES
    ) or (
        status in LEGACY_STATUSES
        and org_type in CANONICAL_TYPES | LEGACY_TYPES
    ):
        return "legacy"
    raise OrganizationDataCorrupted()


def validate_parent_type(parent_type: str, child_type: str) -> None:
    try:
        expected = CANONICAL_LEVELS[CANONICAL_LEVELS.index(parent_type) + 1]
    except (ValueError, IndexError):
        raise OrganizationRequestInvalid("ORGANIZATION_PARENT_INVALID", 409) from None
    if child_type != expected:
        raise OrganizationRequestInvalid("ORGANIZATION_PARENT_INVALID", 409)


def validate_scope_chain(
    chain: Sequence[Mapping[str, object]],
    *,
    bound_type: str,
) -> None:
    expected_length = CANONICAL_LEVELS.index(bound_type) + 1 if bound_type in CANONICAL_TYPES else 0
    if len(chain) != expected_length or not chain:
        raise OrganizationCurrentnessInvalid()
    seen: set[int] = set()
    for index, item in enumerate(chain):
        row_id = item.get("id")
        if type(row_id) is not int or row_id <= 0 or row_id in seen:
            raise OrganizationCurrentnessInvalid()
        seen.add(row_id)
        if item.get("org_type") != CANONICAL_LEVELS[index] or item.get("status") != "active":
            raise OrganizationCurrentnessInvalid()
        expected_parent = None if index == 0 else chain[index - 1].get("id")
        if item.get("parent_id") != expected_parent:
            raise OrganizationCurrentnessInvalid()


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def normalize_display_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value)
    if not normalized or any(unicodedata.category(char).startswith("C") for char in normalized):
        return None
    return normalized


def candidate_projection(user: Mapping[str, object], *, assigned_to_current: bool) -> dict:
    display_name = normalize_display_name(user.get("real_name"))
    if display_name is None:
        raise OrganizationRequestInvalid("ORGANIZATION_ADMIN_CONFLICT", 409)
    return {
        "user_id": user["id"],
        "display_name": display_name,
        "role": user["role"],
        "assignment_status": "assigned_to_current" if assigned_to_current else "unassigned",
    }


def normalize_org_name(value: str) -> str:
    normalized = normalize_text(value)
    if not 2 <= len(normalized) <= 50 or any(
        unicodedata.category(char).startswith("C") for char in normalized
    ):
        raise OrganizationRequestInvalid()
    return normalized


def normalize_org_code(value: str) -> str:
    normalized = normalize_text(value).upper()
    if not ORG_CODE_PATTERN.fullmatch(normalized):
        raise OrganizationRequestInvalid()
    return normalized


def normalize_claim(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_text(value)
    if not normalized or any(unicodedata.category(char).startswith("C") for char in normalized):
        return None
    return normalized


@dataclass(frozen=True)
class OrganizationActor:
    user_id: int
    role: str
    tenant_id: int | None
    province: str | None = None
    city: str | None = None
