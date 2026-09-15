from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.health_fact.repository import SqlAlchemyHealthFactRepository


class UserHealthRepositoryError(RuntimeError):
    pass


_DATABASE_ERRORS = {
    "CREATE_PROFILE_ROOT": {
        "SLICE4_HEALTH_PROFILE_ROOT_FORBIDDEN": "ACTOR_CURRENTNESS_FORBIDDEN",
        "SLICE4_VERSION_CONFLICT": "VERSION_CONFLICT",
        "SLICE4_COMMIT_OUTCOME_UNKNOWN": "COMMIT_OUTCOME_UNKNOWN",
    },
    "CREATE_DETECTION_REPORT": {
        "SLICE4_DETECTION_REPORT_INVALID": "INVALID_REQUEST",
        "SLICE4_DETECTION_REPORT_FORBIDDEN": "ACTOR_CURRENTNESS_FORBIDDEN",
        "SLICE4_PRIVATE_FILE_BIND_CONFLICT": "PRIVATE_FILE_BIND_CONFLICT",
        "SLICE4_COMMIT_OUTCOME_UNKNOWN": "COMMIT_OUTCOME_UNKNOWN",
    },
    "HEALTH_FACT_STATE_TRANSITION": {
        "SLICE4_HEALTH_FACT_STATE_INVALID": "INVALID_REQUEST",
        "SLICE4_HEALTH_FACT_NOT_FOUND": "HEALTH_FACT_NOT_FOUND",
        "SLICE4_HEALTH_FACT_STATE_CONFLICT": "STATE_CONFLICT",
        "SLICE4_HEALTH_FACT_CORRECTION_CONFLICT": "HEALTH_FACT_CORRECTION_CONFLICT",
        "SLICE4_HEALTH_FACT_STATE_FORBIDDEN": "ACTOR_CURRENTNESS_FORBIDDEN",
        "SLICE4_COMMIT_OUTCOME_UNKNOWN": "COMMIT_OUTCOME_UNKNOWN",
    },
}

_R4_LEGACY_ERRORS = {
    ("P0001", "R4_MEMBER_HEALTH_CURRENTNESS_INVALID"): "MEMBER_HEALTH_CURRENTNESS_INVALID",
    ("42501", "R4_MEMBER_HEALTH_SCOPE_FORBIDDEN"): "MEMBER_HEALTH_SCOPE_FORBIDDEN",
    ("P0001", "R4_HEALTH_PROFILE_REQUIRED"): "HEALTH_PROFILE_REQUIRED",
}


def _jsonb_parameter(value):
    return None if value is None else json.dumps(value)


def _database_error(exc: DBAPIError, callpoint: str) -> str | None:
    original = exc.orig
    direct_cause = getattr(original, "__cause__", None)
    driver_error = next(
        (
            candidate
            for candidate in (original, direct_cause)
            if isinstance(candidate, asyncpg.PostgresError)
        ),
        None,
    )
    if driver_error is None or driver_error.sqlstate != "P0001":
        return None
    if len(driver_error.args) != 1 or type(driver_error.args[0]) is not str:
        return None
    return _DATABASE_ERRORS.get(callpoint, {}).get(driver_error.args[0])


async def _execute_registered(session, statement, parameters, callpoint: str):
    try:
        return await session.execute(statement, parameters)
    except DBAPIError as exc:
        code = _database_error(exc, callpoint)
        if code is None:
            raise
        raise UserHealthRepositoryError(code) from None


async def _execute_r4_legacy(session, statement):
    try:
        return await session.execute(statement)
    except DBAPIError as exc:
        original = exc.orig
        direct_cause = getattr(original, "__cause__", None)
        driver_error = next(
            (
                candidate
                for candidate in (original, direct_cause)
                if isinstance(candidate, asyncpg.PostgresError)
            ),
            None,
        )
        if driver_error is None:
            raise
        if (
            len(driver_error.args) == 1
            and type(driver_error.args[0]) is str
        ):
            code = _R4_LEGACY_ERRORS.get(
                (driver_error.sqlstate, driver_error.args[0])
            )
            if code is not None:
                raise UserHealthRepositoryError(code) from None
        if driver_error.sqlstate in {"42501", "42883"}:
            raise UserHealthRepositoryError("DEPENDENCY_UNAVAILABLE") from None
        raise


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
        result = await _execute_registered(
            self._session,
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
            "CREATE_PROFILE_ROOT",
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
        result = await _execute_registered(
            self._session,
            text(
                "SELECT * FROM public.slice4_detection_report_create_v1("
                ":actor_user_id,:actor_context,:subject_member_id,:service_case_id,"
                ":enrollment_id,:requested_report_id,:report_type,:measured_at,"
                ":source_type,:private_file_ids,:idempotency_key,:request_digest,"
                ":expected_postimage_digest)"
            ),
            values,
            "CREATE_DETECTION_REPORT",
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
        result = await _execute_registered(
            self._session,
            text(
                "SELECT * FROM public.slice4_health_fact_state_transition_v1("
                ":fact_ref,:target_state,:expected_state,:actor_user_id,:service_case_id,"
                ":expected_version,:reason_code,:event_digest)"
            ),
            values,
            "HEALTH_FACT_STATE_TRANSITION",
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
    statement = text(
        "SELECT * FROM public.r4_member_health_currentness_v1(:actor_user_id)"
    ).bindparams(actor_user_id=user_id)
    row = (await _execute_r4_legacy(session, statement)).mappings().one_or_none()
    if row is None:
        return None
    return SimpleNamespace(
        id=row["id"],
        role=row["role"],
        status=row["status"],
        verify_status=row["verify_status"],
    )


async def get_health_profile_by_user_id(session, user_id: int):
    statement = text(
        "SELECT * FROM public.r4_member_legacy_health_profile_read_v1("
        ":actor_user_id,:target_user_id)"
    ).bindparams(actor_user_id=user_id, target_user_id=user_id)
    result = await _execute_r4_legacy(session, statement)
    if not hasattr(result, "mappings"):
        return result.scalar_one_or_none()
    row = result.mappings().one_or_none()
    return SimpleNamespace(**row) if row is not None else None


async def create_health_profile_record(session, *, profile_data: dict):
    values = {
        "actor_user_id": profile_data["user_id"],
        "target_user_id": profile_data["user_id"],
        "gender": profile_data["gender"],
        "birth_date": profile_data["birth_date"],
        "height": profile_data.get("height"),
        "weight": profile_data.get("weight"),
        "blood_type": profile_data.get("blood_type"),
        "medical_history": _jsonb_parameter(profile_data.get("medical_history")),
        "allergy_history": _jsonb_parameter(profile_data.get("allergy_history")),
        "family_history": _jsonb_parameter(profile_data.get("family_history")),
        "smoking": profile_data.get("smoking"),
        "drinking": profile_data.get("drinking"),
        "symptoms": _jsonb_parameter(profile_data.get("symptoms")),
        "sleep_quality": profile_data.get("sleep_quality"),
        "bowel_urination": profile_data.get("bowel_urination"),
        "updated_at": profile_data.get("updated_at"),
    }
    if values["updated_at"] is None:
        values["updated_at"] = datetime.now(UTC)
    statement = text(
        "SELECT * FROM public.r4_member_legacy_health_profile_create_v1("
        ":actor_user_id,:target_user_id,:gender,:birth_date,:height,:weight,"
        ":blood_type,CAST(:medical_history AS jsonb),CAST(:allergy_history AS jsonb),"
        "CAST(:family_history AS jsonb),:smoking,:drinking,CAST(:symptoms AS jsonb),"
        ":sleep_quality,:bowel_urination,:updated_at)"
    ).bindparams(**values)
    row = (await _execute_r4_legacy(session, statement)).mappings().one()
    return SimpleNamespace(**row)


async def update_health_profile_record(
    session,
    *,
    user_id: int,
    expected_updated_at,
    profile_data: dict,
    updated_at,
):
    statement = text(
        "SELECT * FROM public.r4_member_legacy_health_profile_update_v1("
        ":actor_user_id,:target_user_id,:expected_updated_at,:gender,:birth_date,"
        ":height,:weight,:blood_type,:updated_at)"
    ).bindparams(
        actor_user_id=user_id,
        target_user_id=user_id,
        expected_updated_at=expected_updated_at,
        gender=profile_data["gender"],
        birth_date=profile_data["birth_date"],
        height=profile_data.get("height"),
        weight=profile_data.get("weight"),
        blood_type=profile_data.get("blood_type"),
        updated_at=updated_at,
    )
    row = (await _execute_r4_legacy(session, statement)).mappings().one_or_none()
    return SimpleNamespace(**row) if row is not None else None


async def create_health_indicator_records(session, *, records: list[dict]):
    indicators = []
    for record in records:
        statement = text(
            "SELECT * FROM public.r4_member_legacy_health_indicator_create_v1("
            ":actor_user_id,:target_user_id,:batch_id,:indicator_type,:value,"
            ":unit,:source,:recorded_at)"
        ).bindparams(
            actor_user_id=record["user_id"],
            target_user_id=record["user_id"],
            batch_id=record.get("batch_id"),
            indicator_type=record["indicator_type"],
            value=record["value"],
            unit=record["unit"],
            source=record["source"],
            recorded_at=record["recorded_at"],
        )
        row = (await _execute_r4_legacy(session, statement)).mappings().one()
        indicators.append(SimpleNamespace(**row))
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
    statement = text(
        "SELECT * FROM public.r4_member_legacy_health_indicator_history_v1("
        ":actor_user_id,:target_user_id,:indicator_type,:start_at,:end_at,:limit)"
    ).bindparams(
        actor_user_id=user_id,
        target_user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        limit=limit,
    )
    rows = (await _execute_r4_legacy(session, statement)).mappings().all()
    return [SimpleNamespace(**row) for row in rows]


async def list_latest_health_indicators_by_user(session, *, user_id: int):
    statement = text(
        "SELECT * FROM public.r4_member_legacy_health_indicator_latest_v1("
        ":actor_user_id,:target_user_id)"
    ).bindparams(actor_user_id=user_id, target_user_id=user_id)
    rows = (await _execute_r4_legacy(session, statement)).mappings().all()
    return [SimpleNamespace(**row) for row in rows]


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
    statement = text(
        "SELECT * FROM public.r4_member_self_health_indicator_history_v1("
        ":actor_user_id,:indicator_type,:start_at,:end_at,:cursor_recorded_at,"
        ":cursor_id,:limit)"
    ).bindparams(
        actor_user_id=user_id,
        indicator_type=indicator_type,
        start_at=start_at,
        end_at=end_at,
        cursor_recorded_at=cursor_recorded_at,
        cursor_id=cursor_id,
        limit=limit,
    )
    return (await _execute_r4_legacy(session, statement)).mappings().all()


async def list_member_latest_health_indicators(session, *, user_id: int):
    statement = text(
        "SELECT id,batch_id,indicator_type,value,unit,source,recorded_at "
        "FROM public.r4_member_legacy_health_indicator_latest_v1("
        ":actor_user_id,:target_user_id)"
    ).bindparams(actor_user_id=user_id, target_user_id=user_id)
    return (await _execute_r4_legacy(session, statement)).mappings().all()


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
    statement = text(
        "SELECT * FROM public.r4_member_self_detection_report_history_v1("
        ":actor_user_id,:report_type,:start_at,:end_at,:cursor_detection_time,"
        ":cursor_id,:limit)"
    ).bindparams(
        actor_user_id=user_id,
        report_type=report_type,
        start_at=start_at,
        end_at=end_at,
        cursor_detection_time=cursor_detection_time,
        cursor_id=cursor_id,
        limit=limit,
    )
    return (await _execute_r4_legacy(session, statement)).mappings().all()


async def get_member_detection_report(session, *, user_id: int, report_id: int):
    statement = text(
        "SELECT * FROM public.r4_member_self_detection_report_read_v1("
        ":actor_user_id,:report_id)"
    ).bindparams(actor_user_id=user_id, report_id=report_id)
    return (await _execute_r4_legacy(session, statement)).mappings().one_or_none()
