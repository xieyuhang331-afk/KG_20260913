from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Sequence


_DOMAIN = b"kg:projection:organization:core-row:v1\0"
_TYPES = ("headquarter", "province", "city", "county")
_STATUSES = {"active", "inactive", "archived"}


class ProjectionError(Exception):
    pass


class ProjectionUnavailable(ProjectionError):
    pass


class ProjectionSourceInvalid(ProjectionError):
    pass


@dataclass(frozen=True, slots=True)
class OrganizationSourceNode:
    id: int
    parent_id: int | None
    org_code: str
    org_name: str
    org_type: str
    status: str
    sort_order: int
    version: int


@dataclass(frozen=True, slots=True)
class OrganizationProjectionRow:
    organization_id: int
    parent_id: int | None
    org_code: str
    org_name: str
    org_type: str
    status: str
    sort_order: int
    source_version: int
    path_ids: tuple[int, ...]
    path_codes: tuple[str, ...]
    compatibility_mode: str
    scope_eligible: bool
    row_digest: str


def _digest_key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ProjectionUnavailable("Projection digest is unavailable") from None
    return value


def build_organization_projection_row(
    *, chain: Sequence[OrganizationSourceNode], digest_key: bytes
) -> OrganizationProjectionRow:
    if not chain:
        raise ProjectionSourceInvalid("Projection source is invalid")
    if len(chain) != 4 or tuple(node.org_type for node in chain) != tuple(reversed(_TYPES)):
        raise ProjectionSourceInvalid("Projection source is invalid")
    leaf = chain[0]
    if len({node.id for node in chain}) != len(chain) or chain[-1].parent_id is not None:
        raise ProjectionSourceInvalid("Projection source is invalid")
    if any(node.parent_id != parent.id for node, parent in zip(chain, chain[1:])):
        raise ProjectionSourceInvalid("Projection source is invalid")
    if any(
        not isinstance(node.id, int)
        or isinstance(node.id, bool)
        or node.id < 1
        or not isinstance(node.version, int)
        or isinstance(node.version, bool)
        or node.version < 1
        or node.status not in _STATUSES
        or not isinstance(node.org_code, str)
        or not node.org_code
        or not isinstance(node.org_name, str)
        or not node.org_name
        for node in chain
    ):
        raise ProjectionSourceInvalid("Projection source is invalid")

    path = tuple(reversed(chain))
    payload = {
        "organization_id": leaf.id,
        "parent_id": leaf.parent_id,
        "org_code": leaf.org_code,
        "org_name": leaf.org_name,
        "org_type": leaf.org_type,
        "status": leaf.status,
        "sort_order": leaf.sort_order,
        "source_version": leaf.version,
        "path_ids": [node.id for node in path],
        "path_codes": [node.org_code for node in path],
        "compatibility_mode": "canonical",
        "scope_eligible": all(node.status == "active" for node in chain),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hmac.new(_digest_key(digest_key), _DOMAIN + encoded, hashlib.sha256).hexdigest()
    return OrganizationProjectionRow(
        organization_id=leaf.id,
        parent_id=leaf.parent_id,
        org_code=leaf.org_code,
        org_name=leaf.org_name,
        org_type=leaf.org_type,
        status=leaf.status,
        sort_order=leaf.sort_order,
        source_version=leaf.version,
        path_ids=tuple(payload["path_ids"]),
        path_codes=tuple(payload["path_codes"]),
        compatibility_mode="canonical",
        scope_eligible=payload["scope_eligible"],
        row_digest=digest,
    )
