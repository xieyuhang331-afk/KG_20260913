from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import and_, func, insert, or_, select, text, update

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User
from app.modules.organization.models import PlatformOrg
from app.modules.system.models import OperationLog
from app.modules.tenant.models import Tenant


ADMIN_CANDIDATE_USER_COLUMNS = (
    "id",
    "real_name",
    "role",
    "status",
    "tenant_id",
    "updated_at",
)

ORGANIZATION_COLUMNS = (
    "id",
    "parent_id",
    "org_name",
    "org_code",
    "org_type",
    "org_path",
    "sort_order",
    "status",
    "admin_id",
    "version",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)


def _ensure_mapped() -> None:
    map_core_model_classes()


def _namespace(row):
    return SimpleNamespace(**row) if row is not None else None


class OrganizationRepository:
    def __init__(self, session):
        _ensure_mapped()
        self.session = session
        self.org = PlatformOrg.__table__
        self.user = User.__table__
        self.tenant = Tenant.__table__
        self.audit = OperationLog.__table__

    def _organization_columns(self):
        return tuple(getattr(self.org.c, name) for name in ORGANIZATION_COLUMNS)

    async def get_actor_state(self, user_id: int):
        columns = (
            self.user.c.id,
            self.user.c.role,
            self.user.c.status,
            self.user.c.tenant_id,
            self.user.c.updated_at,
        )
        row = (
            await self.session.execute(
                select(*columns).where(self.user.c.id == user_id).limit(1)
            )
        ).mappings().one_or_none()
        return _namespace(row)

    async def get_scope_chain(self, user_id: int):
        bound = (
            await self.session.execute(
                select(*self._organization_columns()).where(self.org.c.admin_id == user_id).limit(2)
            )
        ).mappings().all()
        if len(bound) != 1:
            return []
        chain = [dict(bound[0])]
        while chain[-1]["parent_id"] is not None and len(chain) <= 4:
            parent = (
                await self.session.execute(
                    select(*self._organization_columns())
                    .where(self.org.c.id == chain[-1]["parent_id"])
                    .limit(1)
                )
            ).mappings().one_or_none()
            if parent is None:
                return []
            chain.append(dict(parent))
        chain.reverse()
        return chain

    async def list_organizations(self):
        rows = (
            await self.session.execute(
                select(*self._organization_columns()).order_by(
                    self.org.c.parent_id.asc().nullsfirst(),
                    self.org.c.sort_order.asc(),
                    self.org.c.id.asc(),
                )
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    async def get_organization(self, organization_id: int, *, for_update: bool = False):
        statement = select(*self._organization_columns()).where(
            self.org.c.id == organization_id
        ).limit(1)
        if for_update:
            statement = statement.with_for_update()
        return _namespace((await self.session.execute(statement)).mappings().one_or_none())

    async def get_organization_by_code(self, org_code: str):
        row = (
            await self.session.execute(
                select(*self._organization_columns()).where(
                    self.org.c.org_code == org_code
                ).limit(1)
            )
        ).mappings().one_or_none()
        return _namespace(row)

    async def acquire_sibling_lock(self, parent_id: int) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"organization_siblings:{parent_id}"},
        )

    async def lock_children(self, parent_id: int):
        rows = (
            await self.session.execute(
                select(*self._organization_columns())
                .where(self.org.c.parent_id == parent_id)
                .order_by(self.org.c.id.asc())
                .with_for_update()
            )
        ).mappings().all()
        return [_namespace(row) for row in rows]

    async def list_children(self, parent_id: int):
        rows = (
            await self.session.execute(
                select(*self._organization_columns())
                .where(self.org.c.parent_id == parent_id)
                .order_by(self.org.c.id.asc())
            )
        ).mappings().all()
        return [_namespace(row) for row in rows]

    async def insert_organization(self, values: dict):
        row = (
            await self.session.execute(
                insert(self.org).values(**values).returning(*self._organization_columns())
            )
        ).mappings().one()
        return _namespace(row)

    async def update_organization(self, organization_id: int, values: dict):
        row = (
            await self.session.execute(
                update(self.org)
                .where(self.org.c.id == organization_id)
                .values(**values)
                .returning(*self._organization_columns())
            )
        ).mappings().one_or_none()
        return _namespace(row)

    async def update_organization_if_version(
        self,
        organization_id: int,
        expected_version: int,
        values: dict,
    ):
        row = (
            await self.session.execute(
                update(self.org)
                .where(
                    self.org.c.id == organization_id,
                    self.org.c.version == expected_version,
                )
                .values(**values)
                .returning(*self._organization_columns())
            )
        ).mappings().one_or_none()
        return _namespace(row)

    async def set_child_order(
        self,
        organization_id: int,
        *,
        sort_order: int,
        version: int,
        updated_by: int,
    ):
        return await self.update_organization(
            organization_id,
            {
                "sort_order": sort_order,
                "version": version,
                "updated_by": updated_by,
                "updated_at": func.now(),
            },
        )

    async def list_tenants_for_organizations(
        self,
        organization_ids: list[int],
        *,
        cursor_code: str | None,
        cursor_id: int | None,
        limit: int,
    ):
        columns = (
            self.tenant.c.id.label("tenant_id"),
            self.tenant.c.tenant_code,
            self.tenant.c.name,
            self.tenant.c.type,
            self.tenant.c.province,
            self.tenant.c.city,
            self.tenant.c.district,
            self.tenant.c.grade,
            self.tenant.c.status,
            self.tenant.c.org_id.label("organization_id"),
        )
        statement = select(*columns).where(self.tenant.c.org_id.in_(organization_ids))
        if cursor_code is not None and cursor_id is not None:
            statement = statement.where(
                or_(
                    self.tenant.c.tenant_code > cursor_code,
                    and_(
                        self.tenant.c.tenant_code == cursor_code,
                        self.tenant.c.id > cursor_id,
                    ),
                )
            )
        rows = (
            await self.session.execute(
                statement.order_by(self.tenant.c.tenant_code.asc(), self.tenant.c.id.asc()).limit(limit)
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    async def list_admin_candidates(
        self,
        *,
        role: str,
        current_admin_id: int | None,
        cursor_id: int | None,
        limit: int,
    ):
        columns = tuple(getattr(self.user.c, name) for name in ADMIN_CANDIDATE_USER_COLUMNS)
        assigned_elsewhere = select(self.org.c.admin_id).where(
            self.org.c.admin_id.is_not(None),
            self.org.c.admin_id != current_admin_id if current_admin_id is not None else text("TRUE"),
        )
        statement = select(*columns).where(
            self.user.c.role == role,
            self.user.c.status == "active",
            self.user.c.tenant_id.is_(None),
            self.user.c.real_name.is_not(None),
            self.user.c.id.not_in(assigned_elsewhere),
        )
        if cursor_id is not None:
            statement = statement.where(self.user.c.id > cursor_id)
        rows = (
            await self.session.execute(statement.order_by(self.user.c.id.asc()).limit(limit))
        ).mappings().all()
        return [dict(row) for row in rows]

    async def get_admin_candidate(self, user_id: int):
        columns = tuple(getattr(self.user.c, name) for name in ADMIN_CANDIDATE_USER_COLUMNS)
        row = (
            await self.session.execute(select(*columns).where(self.user.c.id == user_id).limit(1))
        ).mappings().one_or_none()
        return _namespace(row)

    async def get_admin_assignment(self, user_id: int):
        return (
            await self.session.execute(
                select(self.org.c.id).where(self.org.c.admin_id == user_id).limit(1)
            )
        ).scalar_one_or_none()

    async def insert_audit(
        self,
        *,
        operator_id: int,
        object_id: int,
        action: str,
        payload: dict,
    ) -> None:
        await self.session.execute(
            insert(self.audit).values(
                operator_id=operator_id,
                module="organization",
                object_type="platform_org",
                object_id=object_id,
                action=action,
                payload=payload,
            )
        )

    async def get_audit(self, object_id: int, action: str):
        row = (
            await self.session.execute(
                select(
                    self.audit.c.operator_id,
                    self.audit.c.payload,
                )
                .where(
                    self.audit.c.module == "organization",
                    self.audit.c.object_type == "platform_org",
                    self.audit.c.object_id == object_id,
                    self.audit.c.action == action,
                )
                .order_by(self.audit.c.id.desc())
                .limit(1)
            )
        ).mappings().one_or_none()
        return _namespace(row)

    async def get_tenant_for_user(self, tenant_id: int):
        row = (
            await self.session.execute(
                select(
                    self.tenant.c.id.label("tenant_id"),
                    self.tenant.c.tenant_code,
                    self.tenant.c.name.label("tenant_name"),
                    self.tenant.c.type.label("tenant_type"),
                    self.tenant.c.status.label("tenant_status"),
                    self.tenant.c.org_id.label("organization_id"),
                ).where(self.tenant.c.id == tenant_id).limit(1)
            )
        ).mappings().one_or_none()
        return _namespace(row)


class OrganizationUnitOfWork:
    def __init__(self, session):
        self.session = session
        self.repository = OrganizationRepository(session)

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
