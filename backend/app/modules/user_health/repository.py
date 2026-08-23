from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import and_, or_, select, text, update

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User
from app.modules.health_fact.repository import SqlAlchemyHealthFactRepository
from app.modules.user_health.models import DetectionReport, HealthIndicator, HealthProfile


def _ensure_mapped() -> None:
    map_core_model_classes()


class Slice4HealthRecordRepository:
    """Only the bounded Slice 4 database interfaces are reachable here."""

    def __init__(self, session) -> None:
        self._session = session

    async def require_identity_summary_current(
        self,
        *,
        subject_member_id,
        service_case_id,
        identity_revision_ref,
        identity_source_version: int,
        tenant_public_id,
    ) -> None:
        current = await self._session.scalar(
            select(
                text(
                    "public.slice4_identity_summary_current_v1("
                    ":subject_member_id,:service_case_id,:revision_ref,"
                    ":source_version,:tenant_public_id)"
                )
            ).params(
                subject_member_id=subject_member_id,
                service_case_id=service_case_id,
                revision_ref=identity_revision_ref,
                source_version=identity_source_version,
                tenant_public_id=tenant_public_id,
            )
        )
        if current is not True:
            raise RuntimeError("ACTOR_CURRENTNESS_FORBIDDEN")

    async def create_profile_root(self, **values) -> dict:
        values = dict(values)
        values["changed_fields"] = json.dumps(
            values["changed_fields"], separators=(",", ":"), sort_keys=True
        )
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_health_profile_root_create_v1("
                ":actor_user_id,:actor_context,:subject_member_id,:service_case_id,"
                ":enrollment_id,:profile_public_id,:profile_revision_id,"
                ":tenant_public_id,:identity_revision_ref,:identity_source_version,"
                ":snapshot_ciphertext,:snapshot_key_id,:snapshot_digest,:digest_key_id,"
                ":reconfirmed_at,:source_type,CAST(:changed_fields AS jsonb),"
                ":expected_version,:idempotency_key,:request_digest,:expected_postimage_digest)"
            ),
            values,
        )
        return dict(result.mappings().one())

    async def profile_preimage(self, subject_member_id) -> dict | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT profile_public_id,current_revision_id,version FROM public.health_profile "
                    "WHERE subject_member_id=:subject_member_id FOR UPDATE"
                ),
                {"subject_member_id": subject_member_id},
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def latest_profile_metrics(self, **scope) -> tuple[dict, ...]:
        return await self.clinical_facts(
            **scope,
            page={"limit": 100},
        )

    async def create_detection_report(self, **values) -> dict:
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_detection_report_create_v1("
                ":actor_user_id,:actor_context,:subject_member_id,:service_case_id,"
                ":enrollment_id,:requested_report_id,:report_type,:measured_at,"
                ":source_type,:private_file_ids,:idempotency_key,:request_digest,"
                ":expected_postimage_digest)"
            ),
            values,
        )
        return dict(result.mappings().one())

    async def acquire_health_fact_lock(self, lock_key: int) -> None:
        await SqlAlchemyHealthFactRepository(self._session).acquire_semantic_lock(lock_key)

    async def require_report_scope(self, **values) -> bool:
        return await SqlAlchemyHealthFactRepository(self._session).require_report_scope(
            **values
        )

    async def find_health_fact(self, **values):
        return await SqlAlchemyHealthFactRepository(self._session).find_by_semantic_identity(
            **values
        )

    async def add_health_fact(self, fact):
        return await SqlAlchemyHealthFactRepository(self._session).add(fact)

    async def health_fact_by_ref(self, fact_ref):
        return await SqlAlchemyHealthFactRepository(self._session).get_by_ref(fact_ref)

    async def health_fact_state_transition(self, **values) -> dict:
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_health_fact_state_transition_v1("
                ":fact_ref,:target_state,:expected_state,:actor_user_id,:service_case_id,"
                ":expected_version,:reason_code,:event_digest)"
            ),
            values,
        )
        return dict(result.mappings().one())

    async def clinical_facts(
        self,
        *,
        actor_user_id,
        actor_context,
        subject_member_id,
        service_case_id,
        enrollment_id,
        page,
    ) -> tuple[dict, ...]:
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_clinical_fact_read_v1("
                ":actor_user_id,:actor_context,:subject_member_id,:service_case_id,"
                ":enrollment_id,CAST(:page AS jsonb))"
            ),
            {
                "actor_user_id": actor_user_id,
                "actor_context": actor_context,
                "subject_member_id": subject_member_id,
                "service_case_id": service_case_id,
                "enrollment_id": enrollment_id,
                "page": json.dumps(page, separators=(",", ":"), sort_keys=True),
            },
        )
        return tuple(dict(row) for row in result.mappings())

    async def subject_authority(
        self,
        *,
        actor_user_id,
        actor_context,
        enrollment_id=None,
        service_case_id=None,
    ) -> dict | None:
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_subject_authority_v1("
                ":enrollment_id,:service_case_id,:actor_user_id,:actor_context)"
            ),
            {
                "enrollment_id": enrollment_id,
                "service_case_id": service_case_id,
                "actor_user_id": actor_user_id,
                "actor_context": actor_context,
            },
        )
        rows = result.mappings().all()
        return dict(rows[0]) if len(rows) == 1 else None

    async def clinical_profile(self, *, actor_user_id, actor_context, subject_member_id,
                               service_case_id, enrollment_id) -> dict | None:
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_clinical_profile_read_v1("
                ":actor_user_id,:actor_context,:subject_member_id,:service_case_id,"
                ":enrollment_id)"
            ),
            {
                "actor_user_id": actor_user_id,
                "actor_context": actor_context,
                "subject_member_id": subject_member_id,
                "service_case_id": service_case_id,
                "enrollment_id": enrollment_id,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def clinical_reports(
        self,
        *,
        actor_user_id,
        actor_context,
        subject_member_id,
        service_case_id,
        enrollment_id,
        page,
    ) -> tuple[dict, ...]:
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_clinical_report_read_v1("
                ":actor_user_id,:actor_context,:subject_member_id,:service_case_id,"
                ":enrollment_id,CAST(:page AS jsonb))"
            ),
            {
                "actor_user_id": actor_user_id,
                "actor_context": actor_context,
                "subject_member_id": subject_member_id,
                "service_case_id": service_case_id,
                "enrollment_id": enrollment_id,
                "page": json.dumps(page, separators=(",", ":"), sort_keys=True),
            },
        )
        return tuple(dict(row) for row in result.mappings())

    async def institution_health(self, *, actor_user_id, service_case_id, resource, page):
        result = await self._session.execute(
            text(
                "SELECT * FROM public.slice4_institution_health_read_v1("
                ":actor_user_id,:service_case_id,:resource,CAST(:page AS jsonb))"
            ),
            {
                "actor_user_id": actor_user_id,
                "service_case_id": service_case_id,
                "resource": resource,
                "page": json.dumps(page, separators=(",", ":"), sort_keys=True),
            },
        )
        return tuple(dict(row) for row in result.mappings())


async def get_member_profile_user_state(session, user_id: int):
    _ensure_mapped()
    statement = (
        select(
            User.id,
            User.role,
            User.status,
            User.verify_status,
        )
        .where(User.id == user_id)
        .limit(1)
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None
    return SimpleNamespace(
        id=row.id,
        role=row.role,
        status=row.status,
        verify_status=row.verify_status,
    )


async def get_health_profile_by_user_id(session, user_id: int):
    _ensure_mapped()
    result = await session.execute(select(HealthProfile).where(HealthProfile.user_id == user_id).limit(1))
    return result.scalar_one_or_none()


async def create_health_profile_record(session, *, profile_data: dict):
    _ensure_mapped()
    profile = HealthProfile()
    for key, value in profile_data.items():
        setattr(profile, key, value)

    session.add(profile)
    await session.flush()
    return profile


async def update_health_profile_record(
    session,
    *,
    user_id: int,
    expected_updated_at,
    profile_data: dict,
    updated_at,
):
    _ensure_mapped()
    table = HealthProfile.__table__
    statement = (
        update(table)
        .where(
            table.c.user_id == user_id,
            table.c.updated_at == expected_updated_at,
        )
        .values(**profile_data, updated_at=updated_at)
        .returning(*table.c)
    )
    row = (await session.execute(statement)).mappings().one_or_none()
    return SimpleNamespace(**row) if row is not None else None


async def create_health_indicator_records(session, *, records: list[dict]):
    _ensure_mapped()
    indicators = []
    for record in records:
        indicator = HealthIndicator()
        for key, value in record.items():
            setattr(indicator, key, value)
        indicators.append(indicator)

    session.add_all(indicators)
    await session.flush()
    return indicators


async def list_health_indicators_by_user(
    session,
    *,
    user_id: int,
    indicator_type: str | None = None,
    start_at=None,
    end_at=None,
    limit: int = 50,
):
    _ensure_mapped()
    statement = select(HealthIndicator).where(HealthIndicator.user_id == user_id)

    if indicator_type is not None:
        statement = statement.where(HealthIndicator.indicator_type == indicator_type)
    if start_at is not None:
        statement = statement.where(HealthIndicator.recorded_at >= start_at)
    if end_at is not None:
        statement = statement.where(HealthIndicator.recorded_at <= end_at)

    statement = statement.order_by(HealthIndicator.recorded_at.desc()).limit(limit)
    result = await session.execute(statement)
    return result.scalars().all()


async def list_latest_health_indicators_by_user(session, *, user_id: int):
    _ensure_mapped()
    statement = (
        select(HealthIndicator)
        .distinct(HealthIndicator.indicator_type)
        .where(HealthIndicator.user_id == user_id)
        .order_by(HealthIndicator.indicator_type, HealthIndicator.recorded_at.desc(), HealthIndicator.id.desc())
    )
    result = await session.execute(statement)
    return result.scalars().all()


def _member_indicator_projection():
    table = HealthIndicator.__table__
    return (
        table.c.id,
        table.c.batch_id,
        table.c.indicator_type,
        table.c.value,
        table.c.unit,
        table.c.source,
        table.c.recorded_at,
    )


async def list_member_health_indicator_history(
    session,
    *,
    user_id: int,
    indicator_type: str | None,
    start_at,
    end_at,
    cursor_recorded_at,
    cursor_id: int | None,
    limit: int,
):
    _ensure_mapped()
    table = HealthIndicator.__table__
    statement = select(*_member_indicator_projection()).where(table.c.user_id == user_id)
    if indicator_type is not None:
        statement = statement.where(table.c.indicator_type == indicator_type)
    if start_at is not None:
        statement = statement.where(table.c.recorded_at >= start_at)
    if end_at is not None:
        statement = statement.where(table.c.recorded_at <= end_at)
    if cursor_recorded_at is not None and cursor_id is not None:
        statement = statement.where(
            or_(
                table.c.recorded_at < cursor_recorded_at,
                and_(table.c.recorded_at == cursor_recorded_at, table.c.id < cursor_id),
            )
        )
    statement = statement.order_by(table.c.recorded_at.desc(), table.c.id.desc()).limit(limit)
    return (await session.execute(statement)).mappings().all()


async def list_member_latest_health_indicators(session, *, user_id: int):
    _ensure_mapped()
    table = HealthIndicator.__table__
    statement = (
        select(*_member_indicator_projection())
        .distinct(table.c.indicator_type)
        .where(table.c.user_id == user_id)
        .order_by(table.c.indicator_type, table.c.recorded_at.desc(), table.c.id.desc())
    )
    return (await session.execute(statement)).mappings().all()


def _detection_report_projection(*, include_data: bool):
    table = DetectionReport.__table__
    candidate = table.alias("first_detection_report")
    first_report_id = (
        select(candidate.c.id)
        .where(candidate.c.user_id == table.c.user_id)
        .order_by(candidate.c.detection_time.asc(), candidate.c.id.asc())
        .limit(1)
        .correlate(table)
        .scalar_subquery()
    )
    columns = [
        table.c.id,
        table.c.report_type,
        table.c.detection_time,
        table.c.view_status,
        table.c.summary,
        table.c.report_schema_version,
        (table.c.id == first_report_id).label("is_initial_baseline"),
    ]
    if include_data:
        columns.append(table.c.report_data)
    return tuple(columns)


async def list_member_detection_reports(
    session,
    *,
    user_id: int,
    report_type: str | None,
    start_at,
    end_at,
    cursor_detection_time,
    cursor_id: int | None,
    limit: int,
):
    _ensure_mapped()
    table = DetectionReport.__table__
    statement = select(*_detection_report_projection(include_data=False)).where(
        table.c.user_id == user_id
    )
    if report_type is not None:
        statement = statement.where(table.c.report_type == report_type)
    if start_at is not None:
        statement = statement.where(table.c.detection_time >= start_at)
    if end_at is not None:
        statement = statement.where(table.c.detection_time <= end_at)
    if cursor_detection_time is not None and cursor_id is not None:
        statement = statement.where(
            or_(
                table.c.detection_time < cursor_detection_time,
                and_(table.c.detection_time == cursor_detection_time, table.c.id < cursor_id),
            )
        )
    statement = statement.order_by(table.c.detection_time.desc(), table.c.id.desc()).limit(limit)
    return (await session.execute(statement)).mappings().all()


async def get_member_detection_report(session, *, user_id: int, report_id: int):
    _ensure_mapped()
    table = DetectionReport.__table__
    statement = (
        select(*_detection_report_projection(include_data=True))
        .where(table.c.user_id == user_id, table.c.id == report_id)
        .limit(1)
    )
    return (await session.execute(statement)).mappings().one_or_none()
