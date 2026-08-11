from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DOMAIN = b"kg:p3:organization-legacy-mapping:source-fingerprint:v1\0"
_CANONICAL_TYPES = {"headquarter", "province", "city", "county"}
_KNOWN_LEGACY_TYPES = {"platform", "tenant_org"}


class OrganizationMappingError(Exception):
    pass


class OrganizationMappingUnavailable(OrganizationMappingError):
    pass


class OrganizationMappingConflict(OrganizationMappingError):
    pass


@dataclass(frozen=True, slots=True)
class OrganizationMappingDigestKeyring:
    current_key_id: str
    keys: Mapping[str, bytes]

    @classmethod
    def from_json(
        cls, *, current_key_id: str | None, keyring_json: str | None
    ) -> "OrganizationMappingDigestKeyring":
        try:
            values = json.loads(keyring_json or "", object_pairs_hook=_unique_object)
            if not isinstance(values, dict):
                raise ValueError
            decoded: dict[str, bytes] = {}
            for key_id, encoded in values.items():
                if not isinstance(key_id, str) or not isinstance(encoded, str):
                    raise ValueError
                if not _KEY_ID.fullmatch(key_id) or key_id in decoded:
                    raise ValueError
                key = base64.b64decode(encoded, validate=True)
                if len(key) < 32:
                    raise ValueError
                decoded[key_id] = key
            if not current_key_id or current_key_id not in decoded:
                raise ValueError
            return cls(current_key_id, MappingProxyType(decoded))
        except Exception:
            raise OrganizationMappingUnavailable(
                "Organization mapping keyring is unavailable"
            ) from None

    def require_key(self, key_id: str) -> bytes:
        key = self.keys.get(key_id)
        if key is None:
            raise OrganizationMappingUnavailable(
                "Organization mapping key is unavailable"
            )
        return key


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class OrganizationSourceSnapshot:
    legacy_tenant_id: int
    legacy_org_id: int | None
    target: dict | None
    ancestors: tuple[dict | None, ...] = ()


@dataclass(frozen=True, slots=True)
class OrganizationLegacyMapping:
    legacy_tenant_id: int
    legacy_org_id: int | None
    canonical_organization_id: int | None
    mapping_version: int
    batch_id: str
    source_fingerprint: str
    digest_key_id: str
    disposition: str
    reason_code: str
    id: int | None = None


def build_organization_mapping(
    *,
    source: OrganizationSourceSnapshot,
    mapping_version: int,
    batch_id: str,
    keyring: OrganizationMappingDigestKeyring,
) -> OrganizationLegacyMapping:
    if source.legacy_tenant_id < 1 or mapping_version != 1 or not batch_id:
        raise OrganizationMappingUnavailable("Organization mapping request is invalid")
    disposition, reason, canonical_id = _classify(source)
    fingerprint = organization_source_fingerprint(
        source=source,
        mapping_version=mapping_version,
        keyring=keyring,
        key_id=keyring.current_key_id,
    )
    return OrganizationLegacyMapping(
        legacy_tenant_id=source.legacy_tenant_id,
        legacy_org_id=source.legacy_org_id,
        canonical_organization_id=canonical_id,
        mapping_version=mapping_version,
        batch_id=batch_id,
        source_fingerprint=fingerprint,
        digest_key_id=keyring.current_key_id,
        disposition=disposition,
        reason_code=reason,
    )


def _classify(source: OrganizationSourceSnapshot) -> tuple[str, str, int | None]:
    if source.legacy_org_id is None:
        return "UNMAPPED", "SOURCE_ORG_MISSING", None
    target = source.target
    if target is None:
        return "UNMAPPED", "TARGET_NOT_FOUND", None
    if not _valid_node_shape(target) or target["id"] != source.legacy_org_id:
        return "BLOCKED", "SOURCE_CORRUPTED", None
    chain = (target, *source.ancestors)
    if any(node is None for node in chain):
        return "BLOCKED", "TARGET_CHAIN_INVALID", None
    if any(not _valid_node_shape(node) for node in chain):
        return "BLOCKED", "SOURCE_CORRUPTED", None
    if any(
        node["org_type"] in _KNOWN_LEGACY_TYPES or node["status"] == "disabled"
        for node in chain
    ):
        return "REVIEW_REQUIRED", "TARGET_LEGACY", None
    if any(node["status"] == "archived" for node in chain):
        return "REVIEW_REQUIRED", "TARGET_ARCHIVED", None
    if any(
        node["org_type"] not in _CANONICAL_TYPES
        or node["status"] not in {"active", "inactive"}
        for node in chain
    ):
        return "BLOCKED", "SOURCE_CORRUPTED", None
    expected_types = {
        "headquarter": ("headquarter",),
        "province": ("province", "headquarter"),
        "city": ("city", "province", "headquarter"),
        "county": ("county", "city", "province", "headquarter"),
    }[target["org_type"]]
    if tuple(node["org_type"] for node in chain) != expected_types:
        return "BLOCKED", "TARGET_CHAIN_INVALID", None
    ids = [node["id"] for node in chain]
    if len(ids) != len(set(ids)):
        return "BLOCKED", "TARGET_CHAIN_INVALID", None
    for node, parent in zip(chain, chain[1:]):
        if node["parent_id"] != parent["id"]:
            return "BLOCKED", "TARGET_CHAIN_INVALID", None
    if chain[-1]["parent_id"] is not None:
        return "BLOCKED", "TARGET_CHAIN_INVALID", None
    return "MAPPED", "MAPPED_EXACT", int(target["id"])


def _valid_node_shape(node) -> bool:
    required = {"id", "parent_id", "org_type", "status", "version"}
    return (
        isinstance(node, dict)
        and set(node) == required
        and isinstance(node["id"], int)
        and not isinstance(node["id"], bool)
        and node["id"] >= 1
        and (node["parent_id"] is None or (
            isinstance(node["parent_id"], int)
            and not isinstance(node["parent_id"], bool)
            and node["parent_id"] >= 1
        ))
        and isinstance(node["org_type"], str)
        and isinstance(node["status"], str)
        and isinstance(node["version"], int)
        and not isinstance(node["version"], bool)
        and node["version"] >= 1
    )


def normalize_organization_high_watermark(value) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"max_tenant_id"}:
        raise OrganizationMappingUnavailable(
            "Organization mapping high-watermark is invalid"
        )
    source_id = value["max_tenant_id"]
    if not isinstance(source_id, int) or isinstance(source_id, bool) or source_id < 1:
        raise OrganizationMappingUnavailable(
            "Organization mapping high-watermark is invalid"
        )
    return {"max_tenant_id": source_id}


def canonical_organization_high_watermark_bytes(value) -> bytes:
    normalized = normalize_organization_high_watermark(value)
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def parse_organization_high_watermark_json(encoded: str) -> dict[str, int]:
    try:
        value = json.loads(encoded, object_pairs_hook=_unique_object)
    except Exception:
        raise OrganizationMappingUnavailable(
            "Organization mapping high-watermark is invalid"
        ) from None
    return normalize_organization_high_watermark(value)


def organization_source_fingerprint(
    *,
    source: OrganizationSourceSnapshot,
    mapping_version: int,
    keyring: OrganizationMappingDigestKeyring,
    key_id: str,
) -> str:
    target = None
    if source.target is not None:
        target = {
            "id": source.target.get("id"),
            "org_type": _nfc(source.target.get("org_type")),
            "parent_id": source.target.get("parent_id"),
            "status": _nfc(source.target.get("status")),
            "version": source.target.get("version"),
        }
    payload = {
        "ancestors": [
            None if ancestor is None else {
                "id": ancestor.get("id"),
                "org_type": _nfc(ancestor.get("org_type")),
                "parent_id": ancestor.get("parent_id"),
                "status": _nfc(ancestor.get("status")),
                "version": ancestor.get("version"),
            }
            for ancestor in source.ancestors
        ],
        "legacy_org_id": source.legacy_org_id,
        "legacy_tenant_id": source.legacy_tenant_id,
        "mapping_version": mapping_version,
        "target": target,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(keyring.require_key(key_id), _DOMAIN + encoded, hashlib.sha256).hexdigest()


def organization_mapping_lock_key(legacy_tenant_id: int, mapping_version: int) -> int:
    raw = f"organization-legacy:{legacy_tenant_id}:v{mapping_version}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


def _nfc(value):
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else value
