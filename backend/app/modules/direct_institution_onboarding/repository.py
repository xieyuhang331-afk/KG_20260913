from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg
from sqlalchemy import BigInteger, SmallInteger, String, bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import DBAPIError


class DirectInstitutionOnboardingRepositoryError(RuntimeError):
    pass


_DATABASE_ERRORS = {
    "direct_review_replay_v1": {
        "IDEMPOTENCY_CONFLICT": "IDEMPOTENCY_CONFLICT",
    },
    "direct_create_step_up_begin_v1": {
        "DIRECT_STEP_UP_FORBIDDEN": "STEP_UP_FORBIDDEN",
    },
    "direct_create_step_up_failure_v1": {
        "DIRECT_CREATE_STEP_UP_FAILURE_FORBIDDEN": "STEP_UP_FORBIDDEN",
        "DIRECT_CREATE_STEP_UP_FAILURE_INVALID": "INVALID_REQUEST",
    },
    "direct_institution_create_v1": {
        "DIRECT_CREATE_FORBIDDEN": "ROLE_FORBIDDEN",
        "DIRECT_CREATE_ENVELOPE_INVALID": "INVALID_REQUEST",
        "DIRECT_CREATE_ACTOR_NOT_CURRENT": "ROLE_FORBIDDEN",
        "DIRECT_CREATE_STEP_UP_FORBIDDEN": "STEP_UP_FORBIDDEN",
        "DIRECT_CREATE_REGION_INVALID": "INVALID_REQUEST",
        "DIRECT_CREATE_DUPLICATE_ACK_REQUIRED": "DIRECT_ONBOARDING_STATE_CONFLICT",
        "PHONE_CLAIM_OCCUPIED": "ADMIN_PHONE_OCCUPIED",
    },
    "direct_compliance_save_v1": {
        "DIRECT_COMPLIANCE_SAVE_FORBIDDEN": "ROLE_FORBIDDEN",
        "DIRECT_COMPLIANCE_SAVE_INVALID": "INVALID_REQUEST",
        "DIRECT_COMPLIANCE_LICENSE_INVALID": "INVALID_REQUEST",
        "DIRECT_COMPLIANCE_FILE_INVALID": "DIRECT_ONBOARDING_STATE_CONFLICT",
        "DIRECT_COMPLIANCE_SAVE_CONFLICT": "DIRECT_ONBOARDING_STATE_CONFLICT",
    },
    "direct_compliance_submit_v1": {
        "DIRECT_COMPLIANCE_SUBMIT_FORBIDDEN": "ROLE_FORBIDDEN",
        "DIRECT_COMPLIANCE_SUBMIT_INVALID": "INVALID_REQUEST",
        "DIRECT_COMPLIANCE_SUBMIT_CONFLICT": "DIRECT_ONBOARDING_STATE_CONFLICT",
    },
    "direct_compliance_save_replay_v1": {
        "IDEMPOTENCY_CONFLICT": "IDEMPOTENCY_CONFLICT",
    },
    "direct_compliance_submit_replay_v1": {
        "IDEMPOTENCY_CONFLICT": "IDEMPOTENCY_CONFLICT",
    },
    "direct_activation_replay_v1": {
        "IDEMPOTENCY_CONFLICT": "IDEMPOTENCY_CONFLICT",
        "DIRECT_ACTIVATION_REPLAY_INVALID": "DEPENDENCY_UNAVAILABLE",
    },
    "direct_institution_activate_v1": {
        "DIRECT_ACTIVATION_ENVELOPE_INVALID": "INVALID_REQUEST",
    },
    "direct_activation_regenerate_v1": {
        "DIRECT_REGENERATE_FORBIDDEN": "ROLE_FORBIDDEN",
        "DIRECT_REGENERATE_ENVELOPE_INVALID": "INVALID_REQUEST",
    },
    "direct_revoke_step_up_begin_v1": {
        "DIRECT_REVOKE_STEP_UP_FORBIDDEN": "STEP_UP_FORBIDDEN",
    },
    "direct_revoke_step_up_failure_v1": {
        "DIRECT_REVOKE_STEP_UP_FAILURE_FORBIDDEN": "STEP_UP_FORBIDDEN",
        "DIRECT_REVOKE_STEP_UP_FAILURE_INVALID": "INVALID_REQUEST",
    },
    "direct_institution_revoke_v1": {
        "DIRECT_REVOKE_FORBIDDEN": "ROLE_FORBIDDEN",
        "DIRECT_REVOKE_ENVELOPE_INVALID": "INVALID_REQUEST",
    },
    "direct_compliance_decide_step_up_begin_v1": {
        "DIRECT_COMPLIANCE_DECIDE_STEP_UP_FORBIDDEN": "STEP_UP_FORBIDDEN",
    },
    "direct_compliance_decide_step_up_failure_v1": {
        "DIRECT_COMPLIANCE_DECIDE_STEP_UP_FAILURE_FORBIDDEN": "STEP_UP_FORBIDDEN",
        "DIRECT_COMPLIANCE_DECIDE_STEP_UP_FAILURE_INVALID": "INVALID_REQUEST",
    },
    "direct_compliance_decide_v1": {
        "DIRECT_COMPLIANCE_DECIDE_FORBIDDEN": "ROLE_FORBIDDEN",
        "DIRECT_COMPLIANCE_DECIDE_STEP_UP_FORBIDDEN": "STEP_UP_FORBIDDEN",
        "DIRECT_COMPLIANCE_DECIDE_INVALID": "INVALID_REQUEST",
        "DIRECT_COMPLIANCE_DECIDE_CONFLICT": "DIRECT_ONBOARDING_STATE_CONFLICT",
        "DIRECT_CREDIT_CODE_CONFLICT": "DIRECT_ONBOARDING_STATE_CONFLICT",
    },
    "admin_handoff_create_step_up_begin_v1": {
        "HANDOFF_CREATE_STEP_UP_FORBIDDEN": "STEP_UP_FORBIDDEN",
    },
    "admin_handoff_create_step_up_failure_v1": {
        "HANDOFF_CREATE_STEP_UP_FAILURE_FORBIDDEN": "STEP_UP_FORBIDDEN",
        "HANDOFF_CREATE_STEP_UP_FAILURE_INVALID": "INVALID_REQUEST",
    },
    "admin_handoff_regenerate_step_up_begin_v1": {
        "HANDOFF_REGENERATE_STEP_UP_FORBIDDEN": "STEP_UP_FORBIDDEN",
    },
    "admin_handoff_regenerate_step_up_failure_v1": {
        "HANDOFF_REGENERATE_STEP_UP_FAILURE_FORBIDDEN": "STEP_UP_FORBIDDEN",
        "HANDOFF_REGENERATE_STEP_UP_FAILURE_INVALID": "INVALID_REQUEST",
    },
    "institution_admin_handoff_create_v1": {
        "HANDOFF_CREATE_FORBIDDEN": "ROLE_FORBIDDEN",
        "HANDOFF_CREATE_ENVELOPE_INVALID": "INVALID_REQUEST",
        "PHONE_CLAIM_OCCUPIED": "ADMIN_PHONE_OCCUPIED",
    },
    "institution_admin_handoff_regenerate_v1": {
        "HANDOFF_REGENERATE_FORBIDDEN": "ROLE_FORBIDDEN",
        "HANDOFF_REGENERATE_ENVELOPE_INVALID": "INVALID_REQUEST",
    },
    "admin_handoff_activation_authority_v1": {
        "HANDOFF_ACTIVATION_AUTHORITY_FORBIDDEN": "ROLE_FORBIDDEN",
        "HANDOFF_ACTIVATION_AUTHORITY_INVALID": "INVALID_REQUEST",
    },
    "admin_handoff_activation_replay_v1": {
        "IDEMPOTENCY_CONFLICT": "IDEMPOTENCY_CONFLICT",
        "HANDOFF_ACTIVATION_REPLAY_INVALID": "DEPENDENCY_UNAVAILABLE",
    },
    "admin_handoff_activation_v1": {
        "ADMIN_HANDOFF_ACTIVATION_FORBIDDEN": "ROLE_FORBIDDEN",
        "ADMIN_HANDOFF_ENVELOPE_INVALID": "INVALID_REQUEST",
    },
}


def _database_error(exc: DBAPIError, function_name: str) -> str | None:
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
    if driver_error is None or len(driver_error.args) != 1:
        return None
    code = driver_error.args[0]
    if type(code) is not str:
        return None
    return _DATABASE_ERRORS.get(function_name, {}).get(code)


async def _execute(session, statement, parameters, function_name: str):
    try:
        return await session.execute(statement, parameters)
    except DBAPIError as exc:
        code = _database_error(exc, function_name)
        if code is None:
            raise
        raise DirectInstitutionOnboardingRepositoryError(code) from None


class DirectInstitutionOnboardingRepository:
    def __init__(self, session) -> None:
        self.session = session

    async def _json_function(self, name: str, envelope: dict[str, Any]) -> dict | None:
        statement = text(f"SELECT public.{name}(:envelope) AS value").bindparams(
            bindparam("envelope", type_=JSONB)
        )
        value = (
            await _execute(
                self.session, statement, {"envelope": envelope}, name
            )
        ).scalar_one()
        if value is None:
            return None
        return dict(value) if isinstance(value, dict) else json.loads(value)

    async def create(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_institution_create_v1", envelope)

    async def create_step_up_begin(
        self,
        *,
        actor_user_id: int,
        actor_scope: str,
        idempotency_key: str,
        request_digest: str,
        phone_digest_key_id: str,
        phone_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_create_step_up_begin_v1("
            ":actor_user_id,:actor_scope,:idempotency_key,:request_digest,"
            ":phone_digest_key_id,:phone_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
            bindparam("phone_digest_key_id", type_=String(64)),
            bindparam("phone_digest", type_=String(64)),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "actor_scope": actor_scope,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "phone_digest_key_id": phone_digest_key_id,
                    "phone_digest": phone_digest,
                },
                "direct_create_step_up_begin_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def create_step_up_failure(
        self,
        *,
        actor_user_id: int,
        actor_scope: str,
        idempotency_key: str,
        operation_id: UUID,
        request_digest: str,
        observed_profile_version: int,
        failed_time_step: int,
        failure_id: UUID,
        phone_digest_key_id: str,
        phone_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_create_step_up_failure_v1("
            ":actor_user_id,:actor_scope,:idempotency_key,:operation_id,"
            ":request_digest,:observed_profile_version,:failed_time_step,"
            ":failure_id,:phone_digest_key_id,:phone_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("operation_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("request_digest", type_=String(64)),
            bindparam("observed_profile_version", type_=BigInteger()),
            bindparam("failed_time_step", type_=BigInteger()),
            bindparam("failure_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("phone_digest_key_id", type_=String(64)),
            bindparam("phone_digest", type_=String(64)),
        )
        parameters = {
            "actor_user_id": actor_user_id,
            "actor_scope": actor_scope,
            "idempotency_key": idempotency_key,
            "operation_id": operation_id,
            "request_digest": request_digest,
            "observed_profile_version": observed_profile_version,
            "failed_time_step": failed_time_step,
            "failure_id": failure_id,
            "phone_digest_key_id": phone_digest_key_id,
            "phone_digest": phone_digest,
        }
        value = (
            await _execute(
                self.session,
                statement,
                parameters,
                "direct_create_step_up_failure_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def step_up_failure_commit_confirm(
        self,
        *,
        failure_id: UUID,
        actor_user_id: int,
        operation_id: UUID,
        request_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.step_up_failure_commit_confirm_v1("
            ":failure_id,:actor_user_id,:operation_id,:request_digest) AS value"
        ).bindparams(
            bindparam("failure_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("operation_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("request_digest", type_=String(64)),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "failure_id": failure_id,
                    "actor_user_id": actor_user_id,
                    "operation_id": operation_id,
                    "request_digest": request_digest,
                },
                "step_up_failure_commit_confirm_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def regenerate(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_activation_regenerate_v1", envelope)

    async def regenerate_step_up_begin(
        self,
        *,
        actor_user_id: int,
        onboarding_id: UUID,
        actor_scope: str,
        idempotency_key: str,
        request_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_regenerate_step_up_begin_v1("
            ":actor_user_id,:onboarding_id,:actor_scope,:idempotency_key,"
            ":request_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "onboarding_id": onboarding_id,
                    "actor_scope": actor_scope,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                },
                "direct_regenerate_step_up_begin_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def regenerate_step_up_failure(
        self,
        *,
        actor_user_id: int,
        onboarding_id: UUID,
        actor_scope: str,
        idempotency_key: str,
        operation_id: UUID,
        request_digest: str,
        observed_profile_version: int,
        failed_time_step: int,
        failure_id: UUID,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_regenerate_step_up_failure_v1("
            ":actor_user_id,:onboarding_id,:actor_scope,:idempotency_key,"
            ":operation_id,:request_digest,:observed_profile_version,"
            ":failed_time_step,:failure_id) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("operation_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("request_digest", type_=String(64)),
            bindparam("observed_profile_version", type_=BigInteger()),
            bindparam("failed_time_step", type_=BigInteger()),
            bindparam("failure_id", type_=PostgreSQLUUID(as_uuid=True)),
        )
        parameters = {
            "actor_user_id": actor_user_id,
            "onboarding_id": onboarding_id,
            "actor_scope": actor_scope,
            "idempotency_key": idempotency_key,
            "operation_id": operation_id,
            "request_digest": request_digest,
            "observed_profile_version": observed_profile_version,
            "failed_time_step": failed_time_step,
            "failure_id": failure_id,
        }
        value = (
            await _execute(
                self.session,
                statement,
                parameters,
                "direct_regenerate_step_up_failure_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def revoke(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_institution_revoke_v1", envelope)

    async def revoke_step_up_begin(
        self,
        *,
        actor_user_id: int,
        onboarding_id: UUID,
        actor_scope: str,
        idempotency_key: str,
        request_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_revoke_step_up_begin_v1("
            ":actor_user_id,:onboarding_id,:actor_scope,:idempotency_key,"
            ":request_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "onboarding_id": onboarding_id,
                    "actor_scope": actor_scope,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                },
                "direct_revoke_step_up_begin_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def revoke_step_up_failure(
        self,
        *,
        actor_user_id: int,
        onboarding_id: UUID,
        actor_scope: str,
        idempotency_key: str,
        operation_id: UUID,
        request_digest: str,
        observed_profile_version: int,
        failed_time_step: int,
        failure_id: UUID,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_revoke_step_up_failure_v1("
            ":actor_user_id,:onboarding_id,:actor_scope,:idempotency_key,"
            ":operation_id,:request_digest,:observed_profile_version,"
            ":failed_time_step,:failure_id) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("operation_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("request_digest", type_=String(64)),
            bindparam("observed_profile_version", type_=BigInteger()),
            bindparam("failed_time_step", type_=BigInteger()),
            bindparam("failure_id", type_=PostgreSQLUUID(as_uuid=True)),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "onboarding_id": onboarding_id,
                    "actor_scope": actor_scope,
                    "idempotency_key": idempotency_key,
                    "operation_id": operation_id,
                    "request_digest": request_digest,
                    "observed_profile_version": observed_profile_version,
                    "failed_time_step": failed_time_step,
                    "failure_id": failure_id,
                },
                "direct_revoke_step_up_failure_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def activate(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_institution_activate_v1", envelope)

    async def activation_authority(
        self,
        *,
        onboarding_id: UUID,
        credential_id: UUID,
        credential_digests: list[dict[str, str]],
    ) -> dict | None:
        statement = text(
            "SELECT * FROM public.direct_activation_authority_v1("
            ":onboarding_id,:credential_id,:credential_digests)"
        ).bindparams(
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("credential_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("credential_digests", type_=JSONB),
        )
        row = (
            await self.session.execute(
                statement,
                {
                    "onboarding_id": onboarding_id,
                    "credential_id": credential_id,
                    "credential_digests": credential_digests,
                },
            )
        ).mappings().one_or_none()
        return None if row is None else dict(row)

    async def save_compliance(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_compliance_save_v1", envelope)

    async def submit_compliance(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_compliance_submit_v1", envelope)

    async def decide_compliance(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_compliance_decide_v1", envelope)

    async def compliance_decide_step_up_begin(
        self, *, actor_user_id: int, onboarding_id: UUID, revision_id: UUID,
        actor_scope: str, idempotency_key: str, request_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_compliance_decide_step_up_begin_v1("
            ":actor_user_id,:onboarding_id,:revision_id,:actor_scope,"
            ":idempotency_key,:request_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("revision_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
        )
        value = (await _execute(self.session, statement, {
            "actor_user_id": actor_user_id, "onboarding_id": onboarding_id,
            "revision_id": revision_id, "actor_scope": actor_scope,
            "idempotency_key": idempotency_key, "request_digest": request_digest,
        }, "direct_compliance_decide_step_up_begin_v1")).scalar_one()
        return self._mapping(value)

    async def compliance_decide_step_up_failure(
        self, *, actor_user_id: int, onboarding_id: UUID, revision_id: UUID,
        actor_scope: str, idempotency_key: str, operation_id: UUID,
        request_digest: str, observed_profile_version: int,
        failed_time_step: int, failure_id: UUID,
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_compliance_decide_step_up_failure_v1("
            ":actor_user_id,:onboarding_id,:revision_id,:actor_scope,:idempotency_key,"
            ":operation_id,:request_digest,:observed_profile_version,:failed_time_step,"
            ":failure_id) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("revision_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("operation_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("request_digest", type_=String(64)),
            bindparam("observed_profile_version", type_=BigInteger()),
            bindparam("failed_time_step", type_=BigInteger()),
            bindparam("failure_id", type_=PostgreSQLUUID(as_uuid=True)),
        )
        value = (await _execute(self.session, statement, {
            "actor_user_id": actor_user_id, "onboarding_id": onboarding_id,
            "revision_id": revision_id, "actor_scope": actor_scope,
            "idempotency_key": idempotency_key, "operation_id": operation_id,
            "request_digest": request_digest,
            "observed_profile_version": observed_profile_version,
            "failed_time_step": failed_time_step, "failure_id": failure_id,
        }, "direct_compliance_decide_step_up_failure_v1")).scalar_one()
        return self._mapping(value)

    async def create_handoff(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("institution_admin_handoff_create_v1", envelope)

    async def handoff_create_step_up_begin(
        self, *, actor_user_id: int, onboarding_id: UUID, actor_scope: str,
        idempotency_key: str, request_digest: str,
        phone_digest_key_id: str, phone_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.admin_handoff_create_step_up_begin_v1("
            ":actor_user_id,:onboarding_id,:actor_scope,:idempotency_key,"
            ":request_digest,:phone_digest_key_id,:phone_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
            bindparam("phone_digest_key_id", type_=String(64)),
            bindparam("phone_digest", type_=String(64)),
        )
        parameters = {
            key: value
            for key, value in locals().items()
            if key not in {"self", "statement"}
        }
        value = (
            await _execute(
                self.session,
                statement,
                parameters,
                "admin_handoff_create_step_up_begin_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def handoff_create_step_up_failure(
        self, *, actor_user_id: int, onboarding_id: UUID, actor_scope: str,
        idempotency_key: str, operation_id: UUID, request_digest: str,
        observed_profile_version: int, failed_time_step: int, failure_id: UUID,
        phone_digest_key_id: str, phone_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.admin_handoff_create_step_up_failure_v1("
            ":actor_user_id,:onboarding_id,:actor_scope,:idempotency_key,:operation_id,"
            ":request_digest,:observed_profile_version,:failed_time_step,:failure_id,"
            ":phone_digest_key_id,:phone_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("operation_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("request_digest", type_=String(64)),
            bindparam("observed_profile_version", type_=BigInteger()),
            bindparam("failed_time_step", type_=BigInteger()),
            bindparam("failure_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("phone_digest_key_id", type_=String(64)),
            bindparam("phone_digest", type_=String(64)),
        )
        parameters = {key: value for key, value in locals().items() if key not in {"self", "statement"}}
        value = (await _execute(self.session, statement, parameters, "admin_handoff_create_step_up_failure_v1")).scalar_one()
        return self._mapping(value)

    async def regenerate_handoff(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function(
            "institution_admin_handoff_regenerate_v1", envelope
        )

    async def handoff_regenerate_step_up_begin(
        self, *, actor_user_id: int, handoff_id: UUID, actor_scope: str,
        idempotency_key: str, request_digest: str,
    ) -> dict | None:
        statement = text(
            "SELECT public.admin_handoff_regenerate_step_up_begin_v1("
            ":actor_user_id,:handoff_id,:actor_scope,:idempotency_key,:request_digest) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("handoff_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
        )
        parameters = {key: value for key, value in locals().items() if key not in {"self", "statement"}}
        value = (await _execute(self.session, statement, parameters, "admin_handoff_regenerate_step_up_begin_v1")).scalar_one()
        return self._mapping(value)

    async def handoff_regenerate_step_up_failure(
        self, *, actor_user_id: int, handoff_id: UUID, actor_scope: str,
        idempotency_key: str, operation_id: UUID, request_digest: str,
        observed_profile_version: int, failed_time_step: int, failure_id: UUID,
    ) -> dict | None:
        statement = text(
            "SELECT public.admin_handoff_regenerate_step_up_failure_v1("
            ":actor_user_id,:handoff_id,:actor_scope,:idempotency_key,:operation_id,"
            ":request_digest,:observed_profile_version,:failed_time_step,:failure_id) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("handoff_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("operation_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("request_digest", type_=String(64)),
            bindparam("observed_profile_version", type_=BigInteger()),
            bindparam("failed_time_step", type_=BigInteger()),
            bindparam("failure_id", type_=PostgreSQLUUID(as_uuid=True)),
        )
        parameters = {key: value for key, value in locals().items() if key not in {"self", "statement"}}
        value = (await _execute(self.session, statement, parameters, "admin_handoff_regenerate_step_up_failure_v1")).scalar_one()
        return self._mapping(value)

    async def revoke_handoff(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("institution_admin_handoff_revoke_v1", envelope)

    async def activate_handoff(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("admin_handoff_activation_v1", envelope)

    async def handoff_activation_authority(
        self, *, onboarding_id: UUID, handoff_id: UUID, credential_id: UUID,
        credential_digests: list[dict[str, str]],
    ) -> dict | None:
        statement = text(
            "SELECT * FROM public.admin_handoff_activation_authority_v1("
            ":onboarding_id,:handoff_id,:credential_id,:credential_digests)"
        ).bindparams(
            bindparam("onboarding_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("handoff_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("credential_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("credential_digests", type_=JSONB),
        )
        parameters = {key: value for key, value in locals().items() if key not in {"self", "statement"}}
        row = (await _execute(self.session, statement, parameters, "admin_handoff_activation_authority_v1")).mappings().one_or_none()
        return None if row is None else dict(row)

    async def review_replay(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_review_replay_v1", envelope)

    async def review_commit_confirm(self, envelope: dict[str, Any]) -> dict | None:
        return await self._json_function("direct_review_commit_confirm_v1", envelope)

    async def activation_commit_confirm(
        self, envelope: dict[str, Any]
    ) -> dict | None:
        return await self._json_function(
            "direct_activation_commit_confirm_v1", envelope
        )

    async def compliance_submit_commit_confirm(
        self, envelope: dict[str, Any]
    ) -> dict | None:
        return await self._json_function(
            "direct_compliance_submit_commit_confirm_v1", envelope
        )

    async def compliance_save_commit_confirm(
        self, envelope: dict[str, Any]
    ) -> dict | None:
        return await self._json_function(
            "direct_compliance_save_commit_confirm_v1", envelope
        )

    async def handoff_activation_commit_confirm(
        self, envelope: dict[str, Any]
    ) -> dict | None:
        return await self._json_function(
            "admin_handoff_activation_commit_confirm_v1", envelope
        )

    async def activation_replay(
        self, *, credential_id: UUID, idempotency_key: str, request_digest: str,
        request_digest_candidates: list[dict[str, str]],
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_activation_replay_v1("
            ":credential_id,:idempotency_key,:request_digest,:request_digest_candidates) AS value"
        ).bindparams(
            bindparam("credential_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
            bindparam("request_digest_candidates", type_=JSONB),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "credential_id": credential_id,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "request_digest_candidates": request_digest_candidates,
                },
                "direct_activation_replay_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def handoff_activation_replay(
        self, *, credential_id: UUID, idempotency_key: str, request_digest: str,
        request_digest_candidates: list[dict[str, str]],
    ) -> dict | None:
        statement = text(
            "SELECT public.admin_handoff_activation_replay_v1("
            ":credential_id,:idempotency_key,:request_digest,:request_digest_candidates) AS value"
        ).bindparams(
            bindparam("credential_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
            bindparam("request_digest_candidates", type_=JSONB),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "credential_id": credential_id,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "request_digest_candidates": request_digest_candidates,
                },
                "admin_handoff_activation_replay_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def compliance_submit_replay(
        self,
        *,
        actor_user_id: int,
        actor_scope: str,
        idempotency_key: str,
        request_digest: str,
        request_digest_candidates: list[dict[str, str]],
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_compliance_submit_replay_v1("
            ":actor_user_id,:actor_scope,:idempotency_key,:request_digest,"
            ":request_digest_candidates) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
            bindparam("request_digest_candidates", type_=JSONB),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "actor_scope": actor_scope,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "request_digest_candidates": request_digest_candidates,
                },
                "direct_compliance_submit_replay_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    async def compliance_save_replay(
        self,
        *,
        actor_user_id: int,
        actor_scope: str,
        idempotency_key: str,
        request_digest: str,
        request_digest_candidates: list[dict[str, str]],
    ) -> dict | None:
        statement = text(
            "SELECT public.direct_compliance_save_replay_v1("
            ":actor_user_id,:actor_scope,:idempotency_key,:request_digest,"
            ":request_digest_candidates) AS value"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("actor_scope", type_=String(128)),
            bindparam("idempotency_key", type_=String(128)),
            bindparam("request_digest", type_=String(64)),
            bindparam("request_digest_candidates", type_=JSONB),
        )
        value = (
            await _execute(
                self.session,
                statement,
                {
                    "actor_user_id": actor_user_id,
                    "actor_scope": actor_scope,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "request_digest_candidates": request_digest_candidates,
                },
                "direct_compliance_save_replay_v1",
            )
        ).scalar_one()
        return self._mapping(value)

    @staticmethod
    def _mapping(value: Any) -> dict | None:
        if value is None:
            return None
        return dict(value) if isinstance(value, dict) else json.loads(value)

    async def read_rows(
        self,
        *,
        actor_user_id: int,
        resource: str,
        target_id: UUID | None = None,
        cursor_id: UUID | None = None,
        ceiling_id: UUID | None = None,
        limit: int = 50,
        status: str | None = None,
    ) -> list[dict]:
        statement = text(
            "SELECT * FROM public.direct_institution_read_v1("
            ":actor_user_id,:resource,:target_id,:cursor_id,:ceiling_id,:limit,:status)"
        ).bindparams(
            bindparam("actor_user_id", type_=BigInteger()),
            bindparam("resource", type_=String(32)),
            bindparam("target_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("cursor_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("ceiling_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("limit", type_=SmallInteger()),
            bindparam("status", type_=String(32)),
        )
        rows = await self.session.execute(
            statement,
            {
                "actor_user_id": actor_user_id,
                "resource": resource,
                "target_id": target_id,
                "cursor_id": cursor_id,
                "ceiling_id": ceiling_id,
                "limit": limit,
                "status": status,
            },
        )
        return [dict(row) for row in rows.mappings().all()]

    async def read_current_compliance(self, *, actor_user_id: int) -> list[dict]:
        statement = text(
            "SELECT * FROM public.direct_compliance_current_v1(:actor_user_id)"
        ).bindparams(bindparam("actor_user_id", type_=BigInteger())).params(
            actor_user_id=actor_user_id
        )
        rows = await self.session.execute(statement)
        return [dict(row) for row in rows.mappings().all()]

    async def outbox_claim(
        self, *, ceiling_id: UUID, limit: int, worker_id: str
    ) -> list[dict]:
        statement = text(
            "SELECT value FROM public.direct_outbox_claim_v1("
            ":ceiling_id,:limit,:worker_id) AS value"
        ).bindparams(
            bindparam("ceiling_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("limit", type_=SmallInteger()),
            bindparam("worker_id", type_=String(64)),
        )
        rows = await self.session.execute(
            statement,
            {"ceiling_id": ceiling_id, "limit": limit, "worker_id": worker_id},
        )
        return [self._mapping(row[0]) or {} for row in rows.all()]

    async def outbox_consume(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        lease_token: str,
        expected_version: int,
    ) -> dict | None:
        return await self._worker_event(
            "direct_outbox_consume_v1",
            event_id=event_id,
            worker_id=worker_id,
            lease_token=lease_token,
            expected_version=expected_version,
        )

    async def outbox_reopen(
        self,
        *,
        event_id: UUID,
        worker_id: str,
        lease_token: str,
        expected_version: int,
        reason_code: str,
    ) -> dict | None:
        return await self._worker_event(
            "direct_outbox_reopen_v1",
            event_id=event_id,
            worker_id=worker_id,
            lease_token=lease_token,
            expected_version=expected_version,
            reason_code=reason_code,
        )

    async def _worker_event(
        self,
        function_name: str,
        *,
        event_id: UUID,
        worker_id: str,
        lease_token: str,
        expected_version: int,
        reason_code: str | None = None,
    ) -> dict | None:
        parameters = ":event_id,:worker_id,:lease_token,:expected_version"
        if reason_code is not None:
            parameters += ",:reason_code"
        statement = text(
            f"SELECT public.{function_name}({parameters}) AS value"
        ).bindparams(
            bindparam("event_id", type_=PostgreSQLUUID(as_uuid=True)),
            bindparam("worker_id", type_=String(64)),
            bindparam("lease_token", type_=String(128)),
            bindparam("expected_version", type_=BigInteger()),
            *(
                (bindparam("reason_code", type_=String(64)),)
                if reason_code is not None
                else ()
            ),
        )
        values = {
            "event_id": event_id,
            "worker_id": worker_id,
            "lease_token": lease_token,
            "expected_version": expected_version,
        }
        if reason_code is not None:
            values["reason_code"] = reason_code
        value = (await self.session.execute(statement, values)).scalar_one()
        return self._mapping(value)

    async def recovery_claim(self, *, limit: int, worker_id: str) -> list[dict]:
        statement = text(
            "SELECT value FROM public.direct_recovery_claim_v1(:limit,:worker_id) AS value"
        ).bindparams(
            bindparam("limit", type_=SmallInteger()),
            bindparam("worker_id", type_=String(64)),
        )
        rows = await self.session.execute(
            statement, {"limit": limit, "worker_id": worker_id}
        )
        return [self._mapping(row[0]) or {} for row in rows.all()]
