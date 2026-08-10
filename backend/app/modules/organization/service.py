from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.modules.organization.domain import (
    CANONICAL_TYPES,
    OrganizationCurrentnessInvalid,
    OrganizationDataCorrupted,
    OrganizationError,
    OrganizationNotFound,
    OrganizationRequestInvalid,
    OrganizationScopeForbidden,
    OrganizationVersionConflict,
    candidate_projection,
    classify_compatibility,
    normalize_claim,
    normalize_org_code,
    normalize_org_name,
    validate_parent_type,
    validate_scope_chain,
)
from app.modules.organization.repository import OrganizationRepository, OrganizationUnitOfWork


def calculate_reorder_versions(rows: list[dict], target_orders: dict[int, int]) -> list[dict]:
    return [
        {
            "id": row["id"],
            "sort_order": target_orders[row["id"]],
            "version": row["version"] + (target_orders[row["id"]] != row["sort_order"]),
        }
        for row in rows
    ]


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc, "orig", None), "diag", None) and getattr(
        exc.orig.diag, "constraint_name", None
    )


def _integrity_error(exc: IntegrityError) -> OrganizationError:
    constraint = _constraint_name(exc)
    if constraint in {"uq_platform_org_org_code", "platform_org_org_code_key"}:
        return OrganizationError("ORGANIZATION_CODE_CONFLICT", 409)
    if constraint in {"uq_platform_org_admin_id", "platform_org_admin_id_key"}:
        return OrganizationError("ORGANIZATION_ADMIN_CONFLICT", 409)
    return OrganizationError("ORGANIZATION_DATABASE_UNAVAILABLE", 503)


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str | None, fields: tuple[str, ...]):
    if cursor is None:
        return (None,) * len(fields)
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        values = tuple(payload[field] for field in fields)
        if any(value is None for value in values):
            raise ValueError
        return values
    except Exception:
        raise OrganizationRequestInvalid("ORGANIZATION_CURSOR_INVALID", 400) from None


async def _actor_context(repo: OrganizationRepository, current_user, *, platform: bool = True):
    state = await repo.get_actor_state(current_user.id)
    if state is None or state.status != "active" or state.role != current_user.role:
        raise OrganizationCurrentnessInvalid()
    if current_user.role == "super_admin":
        if state.tenant_id is not None:
            raise OrganizationCurrentnessInvalid()
        return SimpleNamespace(state=state, chain=[], bound_id=None)
    if current_user.role not in {"province_admin", "city_admin"}:
        if platform:
            raise OrganizationScopeForbidden()
        return SimpleNamespace(state=state, chain=[], bound_id=None)
    if state.tenant_id is not None:
        raise OrganizationCurrentnessInvalid()
    chain = await repo.get_scope_chain(current_user.id)
    bound_type = "province" if current_user.role == "province_admin" else "city"
    validate_scope_chain(chain, bound_type=bound_type)
    bound = chain[-1]
    province_node = chain[1]
    province_claim = normalize_claim(current_user.province)
    if province_claim is None or not _claim_matches_node(province_claim, province_node):
        raise OrganizationCurrentnessInvalid()
    if current_user.role == "city_admin":
        city_claim = normalize_claim(current_user.city)
        if city_claim is None or not _claim_matches_node(city_claim, bound):
            raise OrganizationCurrentnessInvalid()
    return SimpleNamespace(state=state, chain=chain, bound_id=bound["id"])


def _claim_matches_node(claim: str, node: dict) -> bool:
    return claim == normalize_claim(node["org_name"]) or claim.upper() == normalize_org_code(
        node["org_code"]
    )


async def _require_super(repo, current_user):
    context = await _actor_context(repo, current_user)
    if current_user.role != "super_admin":
        raise OrganizationScopeForbidden()
    return context


def _row_dict(row) -> dict:
    if isinstance(row, dict):
        return dict(row)
    return {key: value for key, value in vars(row).items() if not key.startswith("_")}


def _descendant_ids(rows: list[dict], root_id: int) -> set[int]:
    allowed = {root_id}
    changed = True
    while changed:
        changed = False
        for row in rows:
            if row["parent_id"] in allowed and row["id"] not in allowed:
                allowed.add(row["id"])
                changed = True
    return allowed


def _path(rows_by_id: dict[int, dict], row: dict) -> list[dict]:
    classify_compatibility(row["org_type"], row["status"])
    result = [row]
    seen = {row["id"]}
    while result[-1]["parent_id"] is not None:
        parent = rows_by_id.get(result[-1]["parent_id"])
        if parent is None or parent["id"] in seen:
            raise OrganizationDataCorrupted()
        classify_compatibility(parent["org_type"], parent["status"])
        seen.add(parent["id"])
        result.append(parent)
    result.reverse()
    return result


def _tenant_scope_ids(rows: list[dict], *, root_id: int, super_admin: bool) -> set[int]:
    descendant_ids = _descendant_ids(rows, root_id)
    allowed: set[int] = set()
    for row in rows:
        if row["id"] not in descendant_ids:
            continue
        mode = classify_compatibility(row["org_type"], row["status"])
        if super_admin or (mode == "canonical" and row["status"] != "archived"):
            allowed.add(row["id"])
    return allowed


def _tree_node(row: dict, children: list[dict]) -> dict:
    mode = classify_compatibility(row["org_type"], row["status"])
    return {
        "id": row["id"],
        "parent_id": row["parent_id"],
        "org_code": row["org_code"],
        "org_name": row["org_name"],
        "org_type": row["org_type"],
        "status": row["status"],
        "compatibility_mode": mode,
        "sort_order": row["sort_order"],
        "version": row["version"],
        "has_children": bool(children),
        "children": children,
    }


async def list_tree(session, *, current_user, include_archived: bool) -> dict:
    repo = OrganizationRepository(session)
    context = await _actor_context(repo, current_user)
    if include_archived and current_user.role != "super_admin":
        raise OrganizationScopeForbidden()
    rows = await repo.list_organizations()
    for row in rows:
        classify_compatibility(row["org_type"], row["status"])
    if current_user.role == "super_admin":
        visible = [row for row in rows if include_archived or row["status"] != "archived"]
    else:
        allowed = _descendant_ids(rows, context.bound_id)
        visible = [
            row for row in rows
            if row["id"] in allowed
            and classify_compatibility(row["org_type"], row["status"]) == "canonical"
            and row["status"] != "archived"
        ]
    visible_ids = {row["id"] for row in visible}
    by_parent: dict[int | None, list[dict]] = {}
    for row in visible:
        key = row["parent_id"] if row["parent_id"] in visible_ids else None
        by_parent.setdefault(key, []).append(row)
    for items in by_parent.values():
        items.sort(key=lambda item: (item["sort_order"], item["id"]))

    def build(parent_id):
        return [_tree_node(row, build(row["id"])) for row in by_parent.get(parent_id, [])]

    return {"items": build(None)}


async def get_detail(session, *, current_user, organization_id: int) -> dict:
    repo = OrganizationRepository(session)
    context = await _actor_context(repo, current_user)
    rows = await repo.list_organizations()
    by_id = {row["id"]: row for row in rows}
    row = by_id.get(organization_id)
    if row is None:
        raise OrganizationNotFound()
    mode = classify_compatibility(row["org_type"], row["status"])
    if current_user.role != "super_admin":
        allowed = _descendant_ids(rows, context.bound_id)
        if row["id"] not in allowed or mode != "canonical" or row["status"] == "archived":
            raise OrganizationNotFound()
    path = _path(by_id, row)
    return {
        "id": row["id"], "parent_id": row["parent_id"], "org_code": row["org_code"],
        "org_name": row["org_name"], "org_type": row["org_type"], "status": row["status"],
        "compatibility_mode": mode, "path_codes": [item["org_code"] for item in path],
        "path_names": [item["org_name"] for item in path], "sort_order": row["sort_order"],
        "version": row["version"], "admin_user_id": row["admin_id"] if current_user.role == "super_admin" else None,
    }


async def list_tenants(
    session, *, current_user, organization_id: int, include_descendants: bool,
    cursor: str | None, page_size: int,
) -> dict:
    repo = OrganizationRepository(session)
    context = await _actor_context(repo, current_user)
    rows = await repo.list_organizations()
    by_id = {row["id"]: row for row in rows}
    target = by_id.get(organization_id)
    if target is None:
        raise OrganizationNotFound()
    mode = classify_compatibility(target["org_type"], target["status"])
    if current_user.role != "super_admin":
        allowed = _descendant_ids(rows, context.bound_id)
        if target["id"] not in allowed or mode != "canonical" or target["status"] == "archived":
            raise OrganizationNotFound()
    ids = (
        _tenant_scope_ids(
            rows,
            root_id=organization_id,
            super_admin=current_user.role == "super_admin",
        )
        if include_descendants
        else {organization_id}
    )
    cursor_code, cursor_id = _decode_cursor(cursor, ("tenant_code", "tenant_id"))
    try:
        cursor_id = int(cursor_id) if cursor_id is not None else None
    except (TypeError, ValueError):
        raise OrganizationRequestInvalid("ORGANIZATION_CURSOR_INVALID", 400) from None
    tenant_rows = await repo.list_tenants_for_organizations(
        sorted(ids), cursor_code=cursor_code, cursor_id=cursor_id, limit=page_size + 1,
    )
    visible = tenant_rows[:page_size]
    items = []
    for item in visible:
        org_path = _path(by_id, by_id[item["organization_id"]])
        item["organization_path_codes"] = [part["org_code"] for part in org_path]
        items.append(item)
    next_cursor = None
    if len(tenant_rows) > page_size:
        last = visible[-1]
        next_cursor = _encode_cursor({"tenant_code": last["tenant_code"], "tenant_id": last["tenant_id"]})
    return {"items": items, "next_cursor": next_cursor}


async def list_admin_candidates(
    session, *, current_user, organization_id: int, cursor: str | None, page_size: int,
) -> dict:
    repo = OrganizationRepository(session)
    await _require_super(repo, current_user)
    target = await repo.get_organization(organization_id)
    if target is None:
        raise OrganizationNotFound()
    mode = classify_compatibility(target.org_type, target.status)
    if mode == "legacy":
        raise OrganizationError("ORGANIZATION_COMPATIBILITY_READ_ONLY", 409)
    if target.status == "archived":
        raise OrganizationError("ORGANIZATION_LIFECYCLE_NOT_OPEN", 409)
    if target.status != "active":
        raise OrganizationError("ORGANIZATION_STATE_CONFLICT", 409)
    role = {"province": "province_admin", "city": "city_admin"}.get(target.org_type)
    if role is None:
        return {"items": [], "next_cursor": None}
    (cursor_id,) = _decode_cursor(cursor, ("user_id",))
    try:
        cursor_id = int(cursor_id) if cursor_id is not None else None
    except (TypeError, ValueError):
        raise OrganizationRequestInvalid("ORGANIZATION_CURSOR_INVALID", 400) from None
    async def fetch(scan_cursor_id, limit):
        return await repo.list_admin_candidates(
            role=role,
            current_admin_id=target.admin_id,
            cursor_id=scan_cursor_id,
            limit=limit,
        )

    items, next_cursor_id = await _collect_admin_candidate_page(
        fetch,
        page_size=page_size,
        current_admin_id=target.admin_id,
        cursor_id=cursor_id,
    )
    next_cursor = _encode_cursor({"user_id": next_cursor_id}) if next_cursor_id is not None else None
    return {"items": items, "next_cursor": next_cursor}


async def _collect_admin_candidate_page(
    fetch,
    *,
    page_size: int,
    current_admin_id: int | None,
    cursor_id: int | None,
) -> tuple[list[dict], int | None]:
    items: list[dict] = []
    scan_cursor_id = cursor_id
    fetch_limit = page_size + 1
    exhausted = False
    while len(items) <= page_size and not exhausted:
        rows = await fetch(scan_cursor_id, fetch_limit)
        if not rows:
            break
        for row in rows:
            scan_cursor_id = row["id"]
            try:
                item = candidate_projection(
                    row,
                    assigned_to_current=row["id"] == current_admin_id,
                )
            except OrganizationError:
                continue
            items.append(item)
            if len(items) > page_size:
                break
        exhausted = len(rows) < fetch_limit
    next_cursor_id = items[page_size - 1]["user_id"] if len(items) > page_size else None
    return items[:page_size], next_cursor_id


async def _validate_candidate(repo, target, user_id: int) -> None:
    role = {"province": "province_admin", "city": "city_admin"}.get(target.org_type)
    if role is None:
        raise OrganizationError("ORGANIZATION_ADMIN_CONFLICT", 409)
    candidate = await repo.get_admin_candidate(user_id)
    if (
        candidate is None or candidate.role != role or candidate.status != "active"
        or candidate.tenant_id is not None or candidate_projection(vars(candidate), assigned_to_current=False) is None
    ):
        raise OrganizationError("ORGANIZATION_ADMIN_CONFLICT", 409)
    assigned = await repo.get_admin_assignment(user_id)
    if assigned is not None and assigned != target.id:
        raise OrganizationError("ORGANIZATION_ADMIN_CONFLICT", 409)


async def _commit(uow, confirmation_factory_provider=None, confirm=None):
    try:
        await uow.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        try:
            await uow.rollback()
        except Exception:
            pass
        if confirmation_factory_provider is not None and confirm is not None:
            try:
                factory = confirmation_factory_provider()
                async with factory() as session:
                    await confirm(OrganizationRepository(session))
            except Exception:
                pass
        raise OrganizationError("ORGANIZATION_COMMIT_OUTCOME_UNKNOWN", 503) from None


def _mutation_response(row) -> dict:
    return {
        "id": row.id, "parent_id": row.parent_id, "org_code": row.org_code,
        "org_name": row.org_name, "org_type": row.org_type, "status": row.status,
        "compatibility_mode": "canonical", "sort_order": row.sort_order,
        "version": row.version, "admin_user_id": row.admin_id,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _matches_response(row, response: dict) -> bool:
    return row is not None and _mutation_response(row) == response


def _matches_audit(audit, *, operator_id: int, payload: dict) -> bool:
    return (
        audit is not None
        and audit.operator_id == operator_id
        and audit.payload == payload
    )


async def _confirm_create_outcome(
    repo,
    *,
    response: dict,
    parent_id: int,
    parent_version: int,
    operator_id: int,
) -> bool:
    child = await repo.get_organization_by_code(response["org_code"])
    parent = await repo.get_organization(parent_id)
    audit = await repo.get_audit(response["id"], "organization_created")
    return (
        _matches_response(child, response)
        and parent is not None
        and parent.version == parent_version
        and _matches_audit(
            audit,
            operator_id=operator_id,
            payload={
                "org_code": response["org_code"],
                "parent_id": parent_id,
                "version": response["version"],
            },
        )
    )


async def _confirm_patch_outcome(
    repo,
    *,
    response: dict,
    fields: list[str],
    operator_id: int,
) -> bool:
    row = await repo.get_organization(response["id"])
    audit = await repo.get_audit(response["id"], "organization_updated")
    return _matches_response(row, response) and _matches_audit(
        audit,
        operator_id=operator_id,
        payload={"version": response["version"], "fields": sorted(fields)},
    )


async def _confirm_reorder_outcome(
    repo,
    *,
    parent_id: int,
    parent_version: int,
    items: list[dict],
    operator_id: int,
) -> bool:
    parent = await repo.get_organization(parent_id)
    children = await repo.list_children(parent_id)
    actual_items = [
        {
            "organization_id": row.id,
            "sort_order": row.sort_order,
            "version": row.version,
        }
        for row in sorted(children, key=lambda value: (value.sort_order, value.id))
    ]
    expected_audit_children = [
        {"id": item["organization_id"], "sort_order": item["sort_order"], "version": item["version"]}
        for item in items
    ]
    audit = await repo.get_audit(parent_id, "organization_children_reordered")
    audit_matches = (
        audit is not None
        and audit.operator_id == operator_id
        and audit.payload.get("parent_version") == parent_version
        and sorted(audit.payload.get("children", []), key=lambda item: item["id"])
        == sorted(expected_audit_children, key=lambda item: item["id"])
    )
    return (
        parent is not None
        and parent.version == parent_version
        and actual_items == items
        and audit_matches
    )


async def _confirm_status_outcome(
    repo,
    *,
    organization_id: int,
    status: str,
    version: int,
    reason_code: str,
    operator_id: int,
) -> bool:
    row = await repo.get_organization(organization_id)
    audit = await repo.get_audit(organization_id, f"organization_{status}")
    return (
        row is not None
        and row.status == status
        and row.version == version
        and _matches_audit(
            audit,
            operator_id=operator_id,
            payload={"status": status, "version": version, "reason_code": reason_code},
        )
    )


async def create_organization(
    session, *, current_user, payload, confirmation_factory_provider=None,
) -> dict:
    uow = OrganizationUnitOfWork(session)
    repo = uow.repository
    await _require_super(repo, current_user)
    org_name = normalize_org_name(payload.org_name)
    org_code = normalize_org_code(payload.org_code)
    try:
        await repo.acquire_sibling_lock(payload.parent_id)
        parent = await repo.get_organization(payload.parent_id, for_update=True)
        if parent is None:
            raise OrganizationError("ORGANIZATION_PARENT_INVALID", 409)
        if classify_compatibility(parent.org_type, parent.status) != "canonical" or parent.status != "active":
            raise OrganizationError("ORGANIZATION_PARENT_INVALID", 409)
        validate_parent_type(parent.org_type, payload.org_type)
        if parent.version != payload.parent_expected_version:
            raise OrganizationVersionConflict()
        children = await repo.lock_children(parent.id)
        target = SimpleNamespace(id=-1, org_type=payload.org_type)
        if payload.admin_user_id is not None:
            await _validate_candidate(repo, target, payload.admin_user_id)
        now = datetime.now(timezone.utc)
        child = await repo.insert_organization({
            "parent_id": parent.id, "org_name": org_name, "org_code": org_code,
            "org_type": payload.org_type, "sort_order": max((row.sort_order for row in children), default=-1) + 1,
            "status": "active", "admin_id": payload.admin_user_id, "version": 1,
            "created_by": current_user.id, "updated_by": current_user.id,
            "created_at": now, "updated_at": now,
        })
        await repo.update_organization(parent.id, {
            "version": parent.version + 1, "updated_by": current_user.id, "updated_at": func.now(),
        })
        await repo.insert_audit(
            operator_id=current_user.id, object_id=child.id, action="organization_created",
            payload={"org_code": org_code, "parent_id": parent.id, "version": 1},
        )
    except IntegrityError as exc:
        await uow.rollback()
        raise _integrity_error(exc) from None
    except asyncio.CancelledError:
        raise
    except OrganizationError:
        await uow.rollback()
        raise
    except Exception:
        await uow.rollback()
        raise OrganizationError("ORGANIZATION_DATABASE_UNAVAILABLE", 503) from None

    response = _mutation_response(child)

    async def confirm(fresh_repo):
        return await _confirm_create_outcome(
            fresh_repo,
            response=response,
            parent_id=parent.id,
            parent_version=parent.version + 1,
            operator_id=current_user.id,
        )

    await _commit(uow, confirmation_factory_provider, confirm)
    return response


async def patch_organization(
    session, *, current_user, organization_id: int, payload,
    confirmation_factory_provider=None,
) -> dict:
    uow = OrganizationUnitOfWork(session)
    repo = uow.repository
    await _require_super(repo, current_user)
    fields = payload.model_fields_set - {"expected_version"}
    if not fields:
        raise OrganizationRequestInvalid()
    try:
        target = await repo.get_organization(organization_id, for_update=True)
        if target is None:
            raise OrganizationNotFound()
        mode = classify_compatibility(target.org_type, target.status)
        if mode == "legacy":
            raise OrganizationError("ORGANIZATION_COMPATIBILITY_READ_ONLY", 409)
        if target.status == "archived":
            raise OrganizationError("ORGANIZATION_LIFECYCLE_NOT_OPEN", 409)
        if target.version != payload.expected_version:
            raise OrganizationVersionConflict()
        if "admin_user_id" in fields and target.status != "active":
            raise OrganizationError("ORGANIZATION_STATE_CONFLICT", 409)
        values = {"version": target.version + 1, "updated_by": current_user.id, "updated_at": func.now()}
        if "org_name" in fields:
            if payload.org_name is None:
                raise OrganizationRequestInvalid()
            values["org_name"] = normalize_org_name(payload.org_name)
        if "admin_user_id" in fields:
            if payload.admin_user_id is not None:
                await _validate_candidate(repo, target, payload.admin_user_id)
            values["admin_id"] = payload.admin_user_id
        updated = await repo.update_organization_if_version(target.id, target.version, values)
        if updated is None:
            raise OrganizationVersionConflict()
        await repo.insert_audit(
            operator_id=current_user.id, object_id=target.id, action="organization_updated",
            payload={"version": updated.version, "fields": sorted(fields)},
        )
    except IntegrityError as exc:
        await uow.rollback()
        raise _integrity_error(exc) from None
    except asyncio.CancelledError:
        raise
    except OrganizationError:
        await uow.rollback()
        raise
    except Exception:
        await uow.rollback()
        raise OrganizationError("ORGANIZATION_DATABASE_UNAVAILABLE", 503) from None
    response = _mutation_response(updated)

    async def confirm(fresh_repo):
        return await _confirm_patch_outcome(
            fresh_repo,
            response=response,
            fields=sorted(fields),
            operator_id=current_user.id,
        )

    await _commit(uow, confirmation_factory_provider, confirm)
    return response


async def reorder_children(
    session, *, current_user, parent_id: int, payload,
    confirmation_factory_provider=None,
) -> dict:
    uow = OrganizationUnitOfWork(session)
    repo = uow.repository
    await _require_super(repo, current_user)
    try:
        await repo.acquire_sibling_lock(parent_id)
        parent = await repo.get_organization(parent_id, for_update=True)
        if parent is None:
            raise OrganizationNotFound()
        if classify_compatibility(parent.org_type, parent.status) != "canonical" or parent.status != "active":
            raise OrganizationError("ORGANIZATION_PARENT_INVALID", 409)
        if parent.version != payload.parent_expected_version:
            raise OrganizationVersionConflict()
        children = await repo.lock_children(parent_id)
        current_ids = {row.id for row in children}
        requested = {item.organization_id: item for item in payload.items}
        if set(requested) != current_ids:
            raise OrganizationError("ORGANIZATION_ORDER_SET_CONFLICT", 409)
        if {item.sort_order for item in payload.items} != set(range(len(children))):
            raise OrganizationError("ORGANIZATION_ORDER_SET_CONFLICT", 409)
        if any(requested[row.id].expected_version != row.version for row in children):
            raise OrganizationError("ORGANIZATION_ORDER_SET_CONFLICT", 409)
        calculated = calculate_reorder_versions(
            [_row_dict(row) for row in children],
            {item.organization_id: item.sort_order for item in payload.items},
        )
        for item in calculated:
            if next(row for row in children if row.id == item["id"]).sort_order != item["sort_order"]:
                await repo.set_child_order(
                    item["id"], sort_order=item["sort_order"], version=item["version"], updated_by=current_user.id,
                )
        parent = await repo.update_organization(parent.id, {
            "version": parent.version + 1, "updated_by": current_user.id, "updated_at": func.now(),
        })
        await repo.insert_audit(
            operator_id=current_user.id, object_id=parent.id, action="organization_children_reordered",
            payload={"parent_version": parent.version, "children": calculated},
        )
    except asyncio.CancelledError:
        raise
    except OrganizationError:
        await uow.rollback()
        raise
    except Exception:
        await uow.rollback()
        raise OrganizationError("ORGANIZATION_DATABASE_UNAVAILABLE", 503) from None
    response = {
        "parent_id": parent.id,
        "parent_version": parent.version,
        "items": [
            {"organization_id": item["id"], "sort_order": item["sort_order"], "version": item["version"]}
            for item in sorted(calculated, key=lambda value: (value["sort_order"], value["id"]))
        ],
    }

    async def confirm(fresh_repo):
        return await _confirm_reorder_outcome(
            fresh_repo,
            parent_id=parent.id,
            parent_version=parent.version,
            items=response["items"],
            operator_id=current_user.id,
        )

    await _commit(uow, confirmation_factory_provider, confirm)
    return response


async def change_status(
    session, *, current_user, organization_id: int, payload, target_status: str,
    confirmation_factory_provider=None,
) -> dict:
    uow = OrganizationUnitOfWork(session)
    repo = uow.repository
    await _require_super(repo, current_user)
    allowed_reasons = {
        "inactive": {"PLATFORM_GOVERNANCE", "COMPLIANCE_HOLD"},
        "active": {"GOVERNANCE_RESTORED"},
    }
    if payload.reason_code not in allowed_reasons[target_status]:
        raise OrganizationRequestInvalid()
    source = "active" if target_status == "inactive" else "inactive"
    try:
        target = await repo.get_organization(organization_id, for_update=True)
        if target is None:
            raise OrganizationNotFound()
        if classify_compatibility(target.org_type, target.status) != "canonical":
            raise OrganizationError("ORGANIZATION_COMPATIBILITY_READ_ONLY", 409)
        if target.org_type == "headquarter" or target.status != source:
            raise OrganizationError("ORGANIZATION_STATE_CONFLICT", 409)
        if target.version != payload.expected_version:
            raise OrganizationVersionConflict()
        updated = await repo.update_organization_if_version(target.id, target.version, {
            "status": target_status, "version": target.version + 1,
            "updated_by": current_user.id, "updated_at": func.now(),
        })
        if updated is None:
            raise OrganizationVersionConflict()
        await repo.insert_audit(
            operator_id=current_user.id, object_id=target.id, action=f"organization_{target_status}",
            payload={"status": target_status, "version": updated.version, "reason_code": payload.reason_code},
        )
    except asyncio.CancelledError:
        raise
    except OrganizationError:
        await uow.rollback()
        raise
    except Exception:
        await uow.rollback()
        raise OrganizationError("ORGANIZATION_DATABASE_UNAVAILABLE", 503) from None
    response = {"id": updated.id, "status": updated.status, "version": updated.version, "updated_at": updated.updated_at}

    async def confirm(fresh_repo):
        return await _confirm_status_outcome(
            fresh_repo,
            organization_id=updated.id,
            status=updated.status,
            version=updated.version,
            reason_code=payload.reason_code,
            operator_id=current_user.id,
        )

    await _commit(uow, confirmation_factory_provider, confirm)
    return response


async def get_my_organization(session, *, current_user) -> dict:
    repo = OrganizationRepository(session)
    state = await repo.get_actor_state(current_user.id)
    if state is None or state.status != "active" or state.role != "org_admin":
        raise OrganizationCurrentnessInvalid()
    if state.tenant_id is None:
        raise OrganizationCurrentnessInvalid()
    tenant = await repo.get_tenant_for_user(state.tenant_id)
    if tenant is None:
        raise OrganizationCurrentnessInvalid()
    base = {
        "tenant_id": tenant.tenant_id, "tenant_code": tenant.tenant_code,
        "tenant_name": tenant.tenant_name, "tenant_type": tenant.tenant_type,
        "tenant_status": tenant.tenant_status,
    }
    if tenant.organization_id is None:
        return {**base, "assignment_status": "unassigned", "organization_id": None,
                "organization_path": [], "compatibility_mode": None}
    rows = await repo.list_organizations()
    by_id = {row["id"]: row for row in rows}
    target = by_id.get(tenant.organization_id)
    if target is None or target["status"] == "archived":
        raise OrganizationNotFound()
    mode = classify_compatibility(target["org_type"], target["status"])
    path = _path(by_id, target)
    return {
        **base, "assignment_status": "assigned", "organization_id": target["id"],
        "organization_path": [
            {"id": row["id"], "org_code": row["org_code"], "org_name": row["org_name"],
             "org_type": row["org_type"], "status": row["status"],
             "compatibility_mode": classify_compatibility(row["org_type"], row["status"])}
            for row in path
        ],
        "compatibility_mode": mode,
    }
