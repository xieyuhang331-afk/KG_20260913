"""Add P3-BP-D internal READY projection read boundary.

Revision ID: 20260815_0019
Revises: 20260814_0018
"""
import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260815_0019"
down_revision = "20260814_0018"
branch_labels = None
depends_on = None

_LOCK_KEY = 4341875042858344256
_VIEWS = (
    "organization_ready_projection_generation_v1",
    "organization_ready_projection_v1",
    "health_ready_projection_generation_v1",
    "health_ready_projection_fact_v1",
    "health_ready_projection_window_selection_v1",
)
_VIEW_COLUMNS = {
    _VIEWS[0]: ("generation_id", "projection_version", "ready_at"),
    _VIEWS[1]: ("generation_id", "projection_version", "ready_at", "organization_id", "parent_id", "org_code", "org_name", "org_type", "status", "sort_order", "path_ids", "path_codes", "scope_eligible"),
    _VIEWS[2]: ("generation_id", "projection_version", "ready_at"),
    _VIEWS[3]: ("generation_id", "projection_version", "ready_at", "fact_id", "subject_user_id", "indicator_code", "numeric_value", "unit", "measured_at", "received_at", "source_type", "business_day"),
    _VIEWS[4]: ("generation_id", "projection_version", "ready_at", "subject_user_id", "indicator_code", "business_day", "winner_fact_id", "rule_version"),
}

_RUNTIME_IDENTITIES = (
    ("application", "KG_DATABASE_USER", "KG_IDENTITY_APPLICATION_DATABASE_URL"),
    ("readonly", "KG_READONLY_ROLE", "KG_READONLY_DATABASE_URL"),
    ("verification_writer", "KG_VERIFICATION_WRITER_ROLE", "KG_VERIFICATION_WRITER_DATABASE_URL"),
    ("delivery_worker", "KG_DELIVERY_WORKER_ROLE", "KG_DELIVERY_WORKER_DATABASE_URL"),
    ("outbox_audit", "KG_OUTBOX_AUDIT_ROLE", "KG_OUTBOX_AUDIT_DATABASE_URL"),
    ("health_fact_writer", "KG_HEALTH_FACT_WRITER_ROLE", "KG_HEALTH_FACT_WRITER_DATABASE_URL"),
    ("organization_mapping_writer", "KG_ORGANIZATION_MAPPING_WRITER_ROLE", "KG_ORGANIZATION_MAPPING_WRITER_DATABASE_URL"),
    ("health_mapping_writer", "KG_HEALTH_MAPPING_WRITER_ROLE", "KG_HEALTH_MAPPING_WRITER_DATABASE_URL"),
    ("mapping_audit", "KG_MAPPING_AUDIT_ROLE", "KG_MAPPING_AUDIT_DATABASE_URL"),
    ("mapping_shadow", "KG_MAPPING_SHADOW_ROLE", "KG_MAPPING_SHADOW_DATABASE_URL"),
    ("organization_builder", "KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", "KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
    ("health_builder", "KG_HEALTH_PROJECTION_BUILDER_ROLE", "KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
    ("projection_confirmation", "KG_PROJECTION_CONFIRMATION_ROLE", "KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
    ("organization_shadow", "KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
    ("health_shadow", "KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
    ("ready_gate", "KG_PROJECTION_READY_GATE_ROLE", "KG_PROJECTION_READY_GATE_DATABASE_URL"),
    ("shadow_confirmation", "KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", "KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
    ("organization", "KG_ORGANIZATION_PROJECTION_READER_ROLE", "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
    ("health", "KG_HEALTH_PROJECTION_READER_ROLE", "KG_HEALTH_PROJECTION_READER_DATABASE_URL"),
)


def _configuration_error():
    raise RuntimeError("projection reader runtime role configuration is invalid") from None


def _configured_roles():
    roles = {}
    for key, role_name, url_name in _RUNTIME_IDENTITIES:
        role = os.environ.get(role_name, "")
        raw_url = os.environ.get(url_name, "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
            _configuration_error()
        try:
            url_role = make_url(raw_url).username
        except Exception:
            _configuration_error()
        if url_role != role:
            _configuration_error()
        roles[key] = role
    if len(set(roles.values())) != len(roles):
        _configuration_error()
    return roles


def _membership_is_unsafe(connection, readers, isolated):
    role_oids = dict(
        connection.execute(
            sa.text("SELECT rolname,oid FROM pg_roles WHERE rolname=ANY(:roles)"),
            {"roles": sorted(set(readers) | set(isolated))},
        ).all()
    )
    if set(role_oids) != set(readers) | set(isolated):
        _configuration_error()
    reader_oids = [role_oids[role] for role in readers]
    return bool(
        connection.execute(
            sa.text(
                "WITH RECURSIVE role_paths(source_oid,target_oid,path) AS ("
                "SELECT membership.member,membership.roleid,ARRAY[membership.member,membership.roleid] "
                "FROM pg_auth_members AS membership UNION ALL "
                "SELECT role_paths.source_oid,membership.roleid,role_paths.path||membership.roleid "
                "FROM role_paths JOIN pg_auth_members AS membership "
                "ON membership.member=role_paths.target_oid "
                "WHERE NOT membership.roleid = ANY(role_paths.path)) "
                "SELECT EXISTS (SELECT 1 FROM role_paths WHERE "
                "source_oid=ANY(:readers) OR target_oid=ANY(:readers))"
            ),
            {"readers": reader_oids},
        ).scalar_one()
    )


def _roles(connection):
    configured = _configured_roles()
    values = {key: configured[key] for key in ("organization", "health")}
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    protected = set(configured.values()) - set(values.values())
    protected.add(current_user)
    if current_user in configured.values() or _membership_is_unsafe(
        connection, tuple(values.values()), tuple(protected | set(values.values()))
    ):
        _configuration_error()
    unsafe = connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=ANY(:roles) AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit))"), {"roles": list(values.values())}).scalar_one()
    if unsafe:
        _configuration_error()
    return (
        {key: f'"{value}"' for key, value in values.items()},
        tuple(f'"{value}"' for value in sorted(protected - {current_user})),
    )


def _grant_columns(role, view):
    op.execute(f"GRANT SELECT ({','.join(_VIEW_COLUMNS[view])}) ON TABLE public.{view} TO {role}")


def _create_views():
    op.execute("""CREATE VIEW public.organization_ready_projection_generation_v1 WITH (security_barrier=true, security_invoker=false) AS
SELECT g.id AS generation_id,g.projection_version,g.ready_at FROM public.organization_projection_generation AS g WHERE g.status='READY'""")
    op.execute("""CREATE VIEW public.organization_ready_projection_v1 WITH (security_barrier=true, security_invoker=false) AS
SELECT p.generation_id,g.projection_version,g.ready_at,p.organization_id,p.parent_id,p.org_code,p.org_name,p.org_type,p.status,p.sort_order,p.path_ids,p.path_codes,p.scope_eligible
FROM public.organization_projection AS p JOIN public.organization_projection_generation AS g ON g.id=p.generation_id WHERE g.status='READY'""")
    op.execute("""CREATE VIEW public.health_ready_projection_generation_v1 WITH (security_barrier=true, security_invoker=false) AS
SELECT g.id AS generation_id,g.projection_version,g.ready_at FROM public.health_projection_generation AS g WHERE g.status='READY'""")
    op.execute("""CREATE VIEW public.health_ready_projection_fact_v1 WITH (security_barrier=true, security_invoker=false) AS
SELECT p.generation_id,g.projection_version,g.ready_at,p.fact_id,p.subject_user_id,p.indicator_code,p.numeric_value,p.unit,p.measured_at,p.received_at,p.source_type,p.business_day
FROM public.health_projection_fact AS p JOIN public.health_projection_generation AS g ON g.id=p.generation_id WHERE g.status='READY'""")
    op.execute("""CREATE VIEW public.health_ready_projection_window_selection_v1 WITH (security_barrier=true, security_invoker=false) AS
SELECT p.generation_id,g.projection_version,g.ready_at,p.subject_user_id,p.indicator_code,p.business_day,p.winner_fact_id,p.rule_version
FROM public.health_projection_window_selection AS p JOIN public.health_projection_generation AS g ON g.id=p.generation_id WHERE g.status='READY'""")


def upgrade():
    connection = op.get_bind(); roles, protected = _roles(connection)
    _create_views()
    all_roles = ", ".join(roles.values())
    op.execute(f"GRANT USAGE ON SCHEMA public TO {all_roles}")
    for view in _VIEWS:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{view} FROM PUBLIC, {all_roles}")
        if protected:
            op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{view} FROM {', '.join(protected)}")
    base_relations = (
        "organization_projection_generation", "organization_projection",
        "health_projection_generation", "health_projection_fact",
        "health_projection_window_selection", "organization_projection_checkpoint",
        "health_projection_checkpoint", "organization_projection_shadow_run",
        "health_projection_shadow_run", "organization_projection_shadow_audit",
        "health_projection_shadow_audit", "canonical_health_fact",
        "organization_legacy_mapping", "health_indicator_legacy_mapping", "operation_log",
    )
    for relation in base_relations:
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{relation} FROM {all_roles}")
    for view in _VIEWS[:2]: _grant_columns(roles["organization"], view)
    for view in _VIEWS[2:]: _grant_columns(roles["health"], view)


def downgrade():
    connection = op.get_bind(); (roles, protected) = _roles(connection)
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    all_roles = ", ".join(roles.values())
    for view in reversed(_VIEWS):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{view} FROM PUBLIC, {all_roles}")
        if protected:
            op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{view} FROM {', '.join(protected)}")
    op.execute("DROP VIEW public.health_ready_projection_window_selection_v1")
    op.execute("DROP VIEW public.health_ready_projection_fact_v1")
    op.execute("DROP VIEW public.health_ready_projection_generation_v1")
    op.execute("DROP VIEW public.organization_ready_projection_v1")
    op.execute("DROP VIEW public.organization_ready_projection_generation_v1")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {all_roles}")
