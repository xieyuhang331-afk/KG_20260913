from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token
from app.core.uuid_generator import Uuid7Generator
from app.modules.direct_institution_onboarding import api as direct_api
from app.modules.direct_institution_onboarding.repository import (
    DirectInstitutionOnboardingRepository,
    DirectInstitutionOnboardingRepositoryError,
)
from app.modules.direct_institution_onboarding.schemas import (
    DirectActivationRequest,
    DirectCreateRequest,
)
from app.modules.direct_institution_onboarding.service import (
    account_phone_claim_digest,
    account_phone_claim_digest_candidates,
    build_direct_activation_mutation,
    build_direct_create_mutation,
    credential_digest_candidates,
    direct_activation_request_digest_candidates,
    platform_admin_totp_aad,
    seal_totp_secret,
)
from app.modules.institution_onboarding.domain import generate_totp
from tests.integration.conftest import (
    _build_alembic_config,
    _get_test_database_url,
    _grant_test_role_permissions,
    _to_asyncpg_dsn,
)

pytestmark = pytest.mark.integration

_DIRECT_TABLES = (
    "direct_institution_onboarding",
    "identity_phone_claim",
    "institution_tenant_origin",
    "direct_institution_activation_credential",
    "platform_admin_security_profile",
    "direct_institution_admin_account",
    "direct_institution_compliance_revision",
    "direct_institution_license",
    "institution_admin_handoff",
    "institution_admin_handoff_credential",
    "direct_onboarding_receipt",
    "direct_onboarding_audit",
    "direct_onboarding_outbox",
)

_TOTP_MUTATION_STATE_TABLES = (
    'public."user"',
    "public.tenant",
    "public.direct_institution_onboarding",
    "public.identity_phone_claim",
    "public.institution_tenant_origin",
    "public.direct_institution_activation_credential",
    "public.platform_admin_security_profile",
    "public.direct_institution_admin_account",
    "public.direct_institution_compliance_revision",
    "public.direct_institution_license",
    "public.institution_admin_handoff",
    "public.institution_admin_handoff_credential",
    "public.direct_onboarding_receipt",
    "public.direct_onboarding_audit",
    "public.direct_onboarding_outbox",
)


async def _totp_mutation_state(connection) -> tuple[str, ...]:
    state = []
    for table in _TOTP_MUTATION_STATE_TABLES:
        state.append(
            await connection.fetchval(
                "SELECT COALESCE(jsonb_agg(to_jsonb(row_value) "
                "ORDER BY to_jsonb(row_value)::text),'[]'::jsonb)::text "
                f"FROM {table} AS row_value"
            )
        )
    return tuple(state)


def _seed_super_admin_and_region(
    pg_database,
    *,
    actor_user_id: int,
    phone: str,
    code_suffix: str,
    totp_secret: str,
) -> tuple[int, str]:
    hierarchy = [actor_user_id + offset for offset in (10, 11, 12, 13)]
    key_id, secret_ciphertext = seal_totp_secret(
        totp_secret, aad=platform_admin_totp_aad(actor_user_id)
    )
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({actor_user_id},'{phone}','synthetic','super_admin','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES"
        f"({hierarchy[0]},NULL,'合成总部{code_suffix}','D44-HQ-{code_suffix}','headquarter','active',1),"
        f"({hierarchy[1]},{hierarchy[0]},'合成省{code_suffix}','D44-P-{code_suffix}','province','active',1),"
        f"({hierarchy[2]},{hierarchy[1]},'合成市{code_suffix}','D44-C-{code_suffix}','city','active',1),"
        f"({hierarchy[3]},{hierarchy[2]},'合成区县{code_suffix}','D44-D-{code_suffix}','county','active',1);"
        "INSERT INTO public.platform_admin_security_profile("
        "user_id,secret_ciphertext,key_id,enabled,profile_version,failed_attempts) VALUES("
        f"{actor_user_id},decode('{secret_ciphertext.hex()}','hex'),'{key_id}',true,1,0);"
    )
    return hierarchy[3], create_access_token(
        {"sub": str(actor_user_id), "role": "super_admin"}
    )


def _direct_create_payload(
    *, institution_name: str, admin_phone: str, region_id: int, totp_secret: str
) -> dict:
    return {
        "institution_name": institution_name,
        "institution_type": "HEALTH_STORE",
        "admin_phone": admin_phone,
        "administrative_region_id": region_id,
        "duplicate_acknowledged": False,
        "reason_code": "SYNTHETIC_ACCEPTANCE",
        "totp_code": generate_totp(totp_secret, at=datetime.now(UTC)),
    }


def _rotate_keyring(monkeypatch, current_name: str, keyring_name: str, suffix: str) -> str:
    source = json.loads(os.environ[keyring_name])
    new_key_id = f"rotation-{suffix}-0044"
    source[new_key_id] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.setenv(current_name, new_key_id)
    monkeypatch.setenv(keyring_name, json.dumps(source, separators=(",", ":")))
    return new_key_id


def _install_totp_step_invalid_matrix_probe(
    monkeypatch,
    *,
    method_names: tuple[str, ...],
    observed: set[str],
) -> None:
    onboarding_methods = {"activate", "activate_handoff"}
    database_functions = {
        "create": "direct_institution_create_v1",
        "regenerate": "direct_activation_regenerate_v1",
        "revoke": "direct_institution_revoke_v1",
        "decide_compliance": "direct_compliance_decide_v1",
        "create_handoff": "institution_admin_handoff_create_v1",
        "regenerate_handoff": "institution_admin_handoff_regenerate_v1",
        "activate": "direct_institution_activate_v1",
        "activate_handoff": "admin_handoff_activation_v1",
    }
    for method_name in method_names:
        original = getattr(DirectInstitutionOnboardingRepository, method_name)
        database_function = database_functions[method_name]
        environment_name = (
            "KG_TEST_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"
            if method_name in onboarding_methods
            else "KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"
        )

        async def guarded(
            self,
            envelope,
            *,
            _method_name=method_name,
            _original=original,
            _environment_name=environment_name,
            _database_function=database_function,
        ):
            observer = await asyncpg.connect(
                _to_asyncpg_dsn(os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"])
            )
            try:
                invalid_steps = (
                    ("missing", None),
                    ("json_null", None),
                    ("string", "1"),
                    ("boolean", True),
                    ("fraction", 1.5),
                    ("negative", -1),
                    ("overflow", 9223372036854775808),
                )
                for label, invalid_step in invalid_steps:
                    before = await _totp_mutation_state(observer)
                    invalid = dict(envelope)
                    if label == "missing":
                        invalid.pop("accepted_totp_step")
                    else:
                        invalid["accepted_totp_step"] = invalid_step
                    connection = await asyncpg.connect(
                        _to_asyncpg_dsn(os.environ[_environment_name])
                    )
                    transaction = connection.transaction()
                    await transaction.start()
                    try:
                        with pytest.raises(
                            asyncpg.InvalidParameterValueError
                        ) as captured:
                            await connection.fetchval(
                                f"SELECT public.{_database_function}($1::jsonb)",
                                json.dumps(invalid),
                            )
                        assert captured.value.sqlstate == "22023"
                    finally:
                        await transaction.rollback()
                        await connection.close()
                    after = await _totp_mutation_state(observer)
                    if after != before:
                        raise AssertionError("TOTP_GUARD_SIDE_EFFECT")
            finally:
                await observer.close()
            observed.add(_method_name)
            return await _original(self, envelope)

        monkeypatch.setattr(
            DirectInstitutionOnboardingRepository, method_name, guarded
        )


class _CommitThenDisconnect:
    def __init__(self, session) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()
        raise ConnectionError("synthetic post-commit disconnect")

    async def rollback(self) -> None:
        await self._session.rollback()


class _DisconnectBeforeCommit:
    def __init__(self, session) -> None:
        self._session = session

    async def commit(self) -> None:
        raise ConnectionError("synthetic pre-commit disconnect")

    async def rollback(self) -> None:
        await self._session.rollback()


class _CancelAtCommit:
    async def commit(self) -> None:
        raise asyncio.CancelledError

    async def rollback(self) -> None:
        raise AssertionError("cancellation must propagate before rollback")


async def _assert_direct_tables_denied(environment_name: str) -> None:
    connection = await asyncpg.connect(
        os.environ[environment_name].replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
    )
    try:
        for table in _DIRECT_TABLES:
            with pytest.raises(asyncpg.InsufficientPrivilegeError) as captured:
                await connection.fetchval(f"SELECT count(*) FROM public.{table}")
            assert captured.value.sqlstate == "42501"
    finally:
        await connection.close()


def test_0045原生ACL及Harness收紧后均拒绝通用Application与Readonly直表访问(
    pg_database,
) -> None:
    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260912_0043")
    command.upgrade(config, "20260913_0045")

    for environment_name in (
        "KG_TEST_DATABASE_URL",
        "KG_TEST_READONLY_DATABASE_URL",
    ):
        asyncio.run(_assert_direct_tables_denied(environment_name))

    _grant_test_role_permissions(pg_database)
    for environment_name in (
        "KG_TEST_DATABASE_URL",
        "KG_TEST_READONLY_DATABASE_URL",
    ):
        asyncio.run(_assert_direct_tables_denied(environment_name))


async def _create_and_confirm(envelope: dict, confirmation: dict) -> tuple[dict, dict]:
    engine = create_async_engine(
        os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"],
        poolclass=NullPool,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            result = await DirectInstitutionOnboardingRepository(session).create(envelope)
            await session.commit()
        async with sessions() as session:
            confirmed = await DirectInstitutionOnboardingRepository(
                session
            ).review_commit_confirm(confirmation)
            await session.commit()
        return result or {}, confirmed or {}
    finally:
        await engine.dispose()


async def _read_direct_list(actor_user_id: int) -> list[dict]:
    engine = create_async_engine(
        os.environ["KG_TEST_INSTITUTION_ONBOARDING_READER_DATABASE_URL"],
        poolclass=NullPool,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            return await DirectInstitutionOnboardingRepository(session).read_rows(
                actor_user_id=actor_user_id,
                resource="LIST",
                limit=50,
            )
    finally:
        await engine.dispose()


async def _read_activation_authority(payload: DirectActivationRequest) -> dict:
    engine = create_async_engine(
        os.environ["KG_TEST_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"],
        poolclass=NullPool,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            value = await DirectInstitutionOnboardingRepository(
                session
            ).activation_authority(
                onboarding_id=payload.onboarding_id,
                credential_id=payload.credential_id,
                credential_digests=credential_digest_candidates(
                    payload.activation_code
                ),
            )
            return value or {}
    finally:
        await engine.dispose()


async def _insert_receipt_response(
    operation: str, response_payload: dict
) -> None:
    connection = await asyncpg.connect(
        os.environ["KG_TEST_MIGRATION_DATABASE_URL"].replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
    )
    transaction = connection.transaction()
    await transaction.start()
    try:
        await connection.execute(
            "INSERT INTO public.direct_onboarding_receipt("
            "receipt_id,actor_scope,operation,idempotency_key,digest_key_id,request_digest,"
            "postimage_digest,response_payload) VALUES("
            "gen_random_uuid(),'SYNTHETIC:RECEIPT-CHECK',$1,gen_random_uuid()::text,"
            "'synthetic-digest-v1',repeat('a',64),repeat('b',64),$2::jsonb)",
            operation,
            json.dumps(response_payload),
        )
    finally:
        await transaction.rollback()
        await connection.close()


def test_0045Receipt按Operation闭合校验公开响应并拒绝非法形状() -> None:
    valid_activation = {
        "onboarding_id": "01900000-0000-7000-8000-000000000001",
        "tenant_id": "01900000-0000-7000-8000-000000000002",
        "institution_code": "01900000000070008000000000000001",
        "institution_name": "合成机构",
        "institution_type": "HEALTH_STORE",
        "administrative_region_id": 440300,
        "status": "ACTIVE_COMPLIANCE_PENDING",
        "compliance_due_at": "2026-10-11T16:00:00+00:00",
        "current_revision_id": None,
        "version": 2,
    }
    asyncio.run(_insert_receipt_response("ACTIVATE", valid_activation))
    asyncio.run(
        _insert_receipt_response(
            "HANDOFF_ACTIVATE",
            {
                "handoff_id": "01900000-0000-7000-8000-000000000003",
                "onboarding_id": valid_activation["onboarding_id"],
                "tenant_id": valid_activation["tenant_id"],
                "status": "ACTIVATED",
                "version": 2,
            },
        )
    )
    invalid_payloads = (
        {**valid_activation, "activation_code": "forbidden"},
        {key: value for key, value in valid_activation.items() if key != "status"},
        {**valid_activation, "version": "2"},
        {**valid_activation, "status": "PENDING_ACTIVATION"},
    )
    for payload in invalid_payloads:
        with pytest.raises(asyncpg.CheckViolationError) as captured:
            asyncio.run(_insert_receipt_response("ACTIVATE", payload))
        assert captured.value.sqlstate == "23514"


def test_正式ReviewWriter创建直开机构并由新事务确认(pg_database) -> None:
    actor_user_id = 9_944_400_001
    headquarter_id = 9_944_400_010
    province_id = 9_944_400_011
    city_id = 9_944_400_012
    county_id = 9_944_400_013
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({actor_user_id},'13900004401','synthetic','super_admin','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES"
        f"({headquarter_id},NULL,'合成总部','D44-HQ','headquarter','active',1),"
        f"({province_id},{headquarter_id},'合成省','D44-P','province','active',1),"
        f"({city_id},{province_id},'合成市','D44-C','city','active',1),"
        f"({county_id},{city_id},'合成区县','D44-D','county','active',1);"
        "INSERT INTO public.platform_admin_security_profile("
        "user_id,secret_ciphertext,key_id,enabled,profile_version,failed_attempts) VALUES"
        f"({actor_user_id},decode('00','hex'),'synthetic-key',true,1,0);"
    )
    request = DirectCreateRequest(
        institution_name="合成直开机构",
        institution_type="HEALTH_STORE",
        admin_phone="13900004402",
        administrative_region_id=county_id,
        duplicate_acknowledged=False,
        reason_code="SYNTHETIC_ACCEPTANCE",
        totp_code="000000",
    )
    generator = Uuid7Generator()
    envelope, confirmation, _ = build_direct_create_mutation(
        request=request,
        actor_user_id=actor_user_id,
        idempotency_key="synthetic-direct-create-0044",
        accepted_totp_step=int(datetime.now(UTC).timestamp()) // 30,
        id_factory=generator.generate,
        activation_code="synthetic-one-time-code-0044",
    )

    result, confirmed = asyncio.run(_create_and_confirm(envelope, confirmation))

    assert result["onboarding_id"] == envelope["onboarding_id"]
    assert result["tenant_id"] == envelope["tenant_public_id"]
    assert result["credential_id"] == envelope["credential_id"]
    assert result["status"] == "PENDING_ACTIVATION"
    assert confirmed["outcome"] == "COMMITTED"
    assert pg_database.fetch_value(
        "SELECT institution_code=upper(replace(onboarding_id::text,'-','')) "
        "FROM public.direct_institution_onboarding"
    ) is True
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt WHERE operation='CREATE'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_audit "
        "WHERE action='DIRECT_INSTITUTION_CREATE'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_outbox "
        "WHERE event_type='DIRECT_INSTITUTION_CREATED'"
    ) == 1


def test_真实ASGI超级管理员创建201且一次性凭据重放拒绝无重复副作用(
    real_db_client, pg_database, monkeypatch
) -> None:
    totp_guarded_methods: set[str] = set()
    expected_totp_guarded_methods = {
        "create",
        "regenerate",
        "activate",
        "decide_compliance",
        "create_handoff",
        "regenerate_handoff",
        "activate_handoff",
    }
    _install_totp_step_invalid_matrix_probe(
        monkeypatch,
        method_names=tuple(sorted(expected_totp_guarded_methods)),
        observed=totp_guarded_methods,
    )
    actor_user_id = 9_944_401_001
    headquarter_id = 9_944_401_010
    province_id = 9_944_401_011
    city_id = 9_944_401_012
    county_id = 9_944_401_013
    totp_secret = "JBSWY3DPEHPK3PXP"
    key_id, secret_ciphertext = seal_totp_secret(
        totp_secret, aad=platform_admin_totp_aad(actor_user_id)
    )
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({actor_user_id},'13900004411','synthetic','super_admin','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES"
        f"({headquarter_id},NULL,'合成总部二','D44-HQ2','headquarter','active',1),"
        f"({province_id},{headquarter_id},'合成省二','D44-P2','province','active',1),"
        f"({city_id},{province_id},'合成市二','D44-C2','city','active',1),"
        f"({county_id},{city_id},'合成区县二','D44-D2','county','active',1);"
        "INSERT INTO public.platform_admin_security_profile("
        "user_id,secret_ciphertext,key_id,enabled,profile_version,failed_attempts) VALUES("
        f"{actor_user_id},decode('{secret_ciphertext.hex()}','hex'),'{key_id}',true,1,0);"
    )
    token = create_access_token({"sub": str(actor_user_id), "role": "super_admin"})
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "synthetic-direct-http-0044",
    }
    payload = {
        "institution_name": "合成直开机构二",
        "institution_type": "HEALTH_STORE",
        "admin_phone": "13900004412",
        "administrative_region_id": county_id,
        "duplicate_acknowledged": False,
        "reason_code": "SYNTHETIC_ACCEPTANCE",
        "totp_code": generate_totp(totp_secret, at=datetime.now(UTC)),
    }

    first = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers=headers,
        json=payload,
    )
    replay = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers=headers,
        json=payload,
    )
    conflicting_replay = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers=headers,
        json={**payload, "institution_name": "合成冲突请求不得重放"},
    )

    assert first.status_code == 201
    assert first.headers["Cache-Control"] == "no-store"
    created = first.json()
    assert set(created) >= {
        "onboarding_id",
        "tenant_id",
        "institution_code",
        "credential_id",
        "activation_code",
        "credential_expires_at",
    }
    assert len(created["institution_code"]) == 32
    direct_rows = asyncio.run(_read_direct_list(actor_user_id))
    assert sum(
        str(row["onboarding_id"]) == created["onboarding_id"]
        for row in direct_rows
    ) == 1
    list_response = real_db_client.get(
        "/api/v1/platform/direct-institution-onboardings",
        headers={"Authorization": f"Bearer {token}"},
    )
    detail_response = real_db_client.get(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert list_response.status_code == 200
    listed = list_response.json()
    expected_public = {
        key: value
        for key, value in created.items()
        if key not in {"credential_id", "activation_code", "credential_expires_at"}
    }
    matching_items = [
        item
        for item in listed["items"]
        if item["onboarding_id"] == created["onboarding_id"]
    ]
    assert matching_items == [expected_public]
    assert listed["next_cursor"] is None
    assert detail_response.status_code == 200
    assert detail_response.json() == expected_public
    assert replay.status_code == 409
    assert replay.json()["code"] == "ONE_TIME_CREDENTIAL_ALREADY_ISSUED"
    assert conflicting_replay.status_code == 409
    assert conflicting_replay.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert real_db_client.get(
        "/api/v1/platform/direct-institution-onboardings"
    ).status_code == 401
    assert real_db_client.get(
        "/api/v1/platform/direct-institution-onboardings",
        headers={"Authorization": "Bearer malformed"},
    ).status_code == 401
    forbidden_actor_id = 9_944_401_099
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({forbidden_actor_id},'13900004419','synthetic','member','active',NULL);"
    )
    assert real_db_client.get(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": "Bearer "
            + create_access_token({"sub": str(forbidden_actor_id), "role": "member"})
        },
    ).status_code == 403
    missing_response = real_db_client.get(
        "/api/v1/platform/direct-institution-onboardings/01900000-0000-7000-8000-000000000099",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["code"] == "DIRECT_ONBOARDING_NOT_FOUND"
    regenerate_headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "synthetic-direct-regenerate-0044",
    }
    regenerate_payload = {
        "expected_version": 1,
        "totp_code": generate_totp(
            totp_secret, at=datetime.now(UTC) + timedelta(seconds=30)
        ),
        "reason_code": "SYNTHETIC_REGENERATE",
    }
    regenerated = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}"
        "/activation-credential:regenerate",
        headers=regenerate_headers,
        json=regenerate_payload,
    )
    regenerate_replay = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}"
        "/activation-credential:regenerate",
        headers=regenerate_headers,
        json=regenerate_payload,
    )
    assert regenerated.status_code == 201
    assert regenerated.headers["Cache-Control"] == "no-store"
    regenerated_value = regenerated.json()
    assert regenerated_value["version"] == 2
    assert regenerated_value["credential_id"] != created["credential_id"]
    assert regenerated_value["activation_code"] != created["activation_code"]
    assert regenerate_replay.status_code == 409
    assert regenerate_replay.json()["code"] == "ONE_TIME_CREDENTIAL_ALREADY_ISSUED"
    activation_secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    activation_payload = {
        "onboarding_id": created["onboarding_id"],
        "credential_id": regenerated_value["credential_id"],
        "activation_code": regenerated_value["activation_code"],
        "phone": payload["admin_phone"],
        "password": "SyntheticPassword123!",
        "totp_secret": activation_secret,
        "totp_code": generate_totp(activation_secret, at=datetime.now(UTC)),
        "expected_version": 2,
    }
    activation_headers = {"Idempotency-Key": "synthetic-direct-activate-0044"}
    parsed_activation = DirectActivationRequest.model_validate(activation_payload)
    activation_authority = asyncio.run(
        _read_activation_authority(parsed_activation)
    )
    assert set(activation_authority) == {
        "tenant_public_id",
        "onboarding_id",
        "credential_id",
        "source_kind",
        "root_version",
        "phone_digest_key_id",
        "credential_digest_key_id",
    }
    activated = real_db_client.post(
        "/api/v1/institution-onboarding/direct-activate",
        headers=activation_headers,
        json=activation_payload,
    )
    activation_replay = real_db_client.post(
        "/api/v1/institution-onboarding/direct-activate",
        headers=activation_headers,
        json=activation_payload,
    )
    assert activated.status_code == 200, activated.json().get("code")
    assert activated.json()["status"] == "ACTIVE_COMPLIANCE_PENDING"
    assert activation_replay.status_code == 200
    assert activation_replay.json() == activated.json()
    login = real_db_client.post(
        "/api/v1/auth/login",
        json={
            "phone": payload["admin_phone"],
            "password": activation_payload["password"],
            "totp_code": generate_totp(
                activation_secret, at=datetime.now(UTC) + timedelta(seconds=30)
            ),
        },
    )
    assert login.status_code == 200
    assert login.json()["data"]["user"]["role"] == "org_admin"
    org_headers = {
        "Authorization": f"Bearer {login.json()['data']['access_token']}"
    }
    org_admin_id = int(login.json()["data"]["user"]["id"])
    license_id = Uuid7Generator().generate()
    private_file_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO public.private_file("
        "file_id,purpose,owner_user_id,declared_size,declared_mime_type,"
        "declared_sha256,actual_size,actual_mime_type,actual_sha256,object_key,"
        "status,created_at,expires_at,scanned_at) VALUES ("
        f"'{private_file_id}','BUSINESS_LICENSE',{org_admin_id},1,"
        "'application/pdf',repeat('d',64),1,'application/pdf',repeat('d',64),"
        f"'synthetic/direct-0044/{private_file_id}','CLEAN',now(),"
        "now()+interval '1 day',now());"
    )
    compliance_payload = {
        "expected_version": 3,
        "institution_name": payload["institution_name"],
        "institution_type": payload["institution_type"],
        "administrative_region_id": county_id,
        "institution_code": created["institution_code"],
        "legal_representative_name": "合成法定代表人",
        "unified_social_credit_code": "91330000SYNTH0044X",
        "contact_name": "合成联系人",
        "contact_phone": payload["admin_phone"],
        "address": "合成地址",
        "service_tags": ["HYPERTENSION"],
        "licenses": [
            {
                "license_id": str(license_id),
                "license_type": "BUSINESS_LICENSE",
                "license_no": "SYNTHETIC-LICENSE-0044",
                "private_file_id": str(private_file_id),
                "valid_from": "2026-01-01",
                "valid_until": "2027-01-01",
            }
        ],
    }
    saved = real_db_client.put(
        "/api/v1/institution-onboarding/direct-compliance",
        headers={
            **org_headers,
            "Idempotency-Key": "synthetic-direct-compliance-save-0044",
        },
        json=compliance_payload,
    )
    assert saved.status_code == 200
    saved_value = saved.json()
    assert saved_value["status"] == "ACTIVE_COMPLIANCE_PENDING"
    assert saved_value["version"] == 4
    assert saved_value["licenses"][0]["license_no"] == "SYNTHETIC-LICENSE-0044"

    submit_payload = {**compliance_payload, "expected_version": 4}
    submitted = real_db_client.post(
        "/api/v1/institution-onboarding/direct-compliance:submit",
        headers={
            **org_headers,
            "Idempotency-Key": "synthetic-direct-compliance-submit-0044",
        },
        json=submit_payload,
    )
    assert submitted.status_code == 200
    submitted_value = submitted.json()
    assert submitted_value["status"] == "COMPLIANCE_UNDER_REVIEW"
    assert submitted_value["version"] == 5

    reviewer_user_id = 9_944_401_002
    reviewer_totp_secret = "JBSWY3DPEHPK3PXP"
    reviewer_key_id, reviewer_secret_ciphertext = seal_totp_secret(
        reviewer_totp_secret, aad=platform_admin_totp_aad(reviewer_user_id)
    )
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({reviewer_user_id},'13900004413','synthetic','super_admin','active',NULL);"
        "INSERT INTO public.platform_admin_security_profile("
        "user_id,secret_ciphertext,key_id,enabled,profile_version,failed_attempts) VALUES("
        f"{reviewer_user_id},decode('{reviewer_secret_ciphertext.hex()}','hex'),"
        f"'{reviewer_key_id}',true,1,0);"
    )
    reviewer_token = create_access_token(
        {"sub": str(reviewer_user_id), "role": "super_admin"}
    )
    decision_headers = {
        "Authorization": f"Bearer {reviewer_token}",
        "Idempotency-Key": "synthetic-direct-compliance-decide-0044",
    }
    decision_payload = {
        "expected_version": 5,
        "revision_id": submitted_value["revision_id"],
        "decision": "APPROVE",
        "reason_code": "SYNTHETIC_APPROVAL",
        "correction_fields": [],
        "totp_code": generate_totp(reviewer_totp_secret, at=datetime.now(UTC)),
    }
    decision = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}"
        "/compliance-decision",
        headers=decision_headers,
        json=decision_payload,
    )
    decision_replay = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}"
        "/compliance-decision",
        headers=decision_headers,
        json=decision_payload,
    )
    assert decision.status_code == 200
    decision_value = decision.json()
    assert decision_value["status"] == "COMPLIANCE_APPROVED"
    assert decision_value["version"] == 6
    assert decision_value["licenses"][0]["license_no"] is None
    assert decision_replay.status_code == 200
    assert decision_replay.json() == decision_value
    current_compliance = real_db_client.get(
        "/api/v1/institution-onboarding/direct-compliance", headers=org_headers
    )
    assert current_compliance.status_code == 200
    assert current_compliance.json()["licenses"][0]["license_no"] == (
        "SYNTHETIC-LICENSE-0044"
    )
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE idempotency_key='synthetic-direct-compliance-decide-0044'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_audit "
        "WHERE action='DIRECT_COMPLIANCE_DECIDE'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_outbox "
        "WHERE event_type='DIRECT_COMPLIANCE_DECIDED'"
    ) == 1

    handoff_response = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}"
        "/admin-handoffs",
        headers={
            "Authorization": f"Bearer {reviewer_token}",
            "Idempotency-Key": "synthetic-admin-handoff-create-0044",
        },
        json={
            "new_phone": "13900004414",
            "expected_version": 6,
            "reason_code": "SYNTHETIC_ADMIN_HANDOFF",
            "totp_code": generate_totp(
                reviewer_totp_secret, at=datetime.now(UTC) + timedelta(seconds=30)
            ),
        },
    )
    assert handoff_response.status_code == 201
    assert handoff_response.headers["cache-control"] == "no-store"
    handoff = handoff_response.json()
    assert handoff["status"] == "ISSUED"
    assert handoff["version"] == 1

    regenerate_reviewer_id = 9_944_401_003
    regenerate_secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    regenerate_key_id, regenerate_ciphertext = seal_totp_secret(
        regenerate_secret, aad=platform_admin_totp_aad(regenerate_reviewer_id)
    )
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({regenerate_reviewer_id},'13900004415','synthetic','super_admin','active',NULL);"
        "INSERT INTO public.platform_admin_security_profile("
        "user_id,secret_ciphertext,key_id,enabled,profile_version,failed_attempts) VALUES("
        f"{regenerate_reviewer_id},decode('{regenerate_ciphertext.hex()}','hex'),"
        f"'{regenerate_key_id}',true,1,0);"
    )
    regenerate_token = create_access_token(
        {"sub": str(regenerate_reviewer_id), "role": "super_admin"}
    )
    regenerate_response = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}"
        f"/admin-handoffs/{handoff['handoff_id']}:regenerate",
        headers={
            "Authorization": f"Bearer {regenerate_token}",
            "Idempotency-Key": "synthetic-admin-handoff-regenerate-0044",
        },
        json={
            "expected_version": 1,
            "reason_code": "SYNTHETIC_ADMIN_HANDOFF_REGENERATE",
            "totp_code": generate_totp(regenerate_secret, at=datetime.now(UTC)),
        },
    )
    assert regenerate_response.status_code == 201
    assert regenerate_response.headers["cache-control"] == "no-store"
    regenerated = regenerate_response.json()
    assert regenerated["handoff_id"] == handoff["handoff_id"]
    assert regenerated["credential_id"] != handoff["credential_id"]
    assert regenerated["version"] == 2

    revoke_reviewer_id = 9_944_401_004
    revoke_key_id, revoke_ciphertext = seal_totp_secret(
        regenerate_secret, aad=platform_admin_totp_aad(revoke_reviewer_id)
    )
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({revoke_reviewer_id},'13900004416','synthetic','super_admin','active',NULL);"
        "INSERT INTO public.platform_admin_security_profile("
        "user_id,secret_ciphertext,key_id,enabled,profile_version,failed_attempts) VALUES("
        f"{revoke_reviewer_id},decode('{revoke_ciphertext.hex()}','hex'),"
        f"'{revoke_key_id}',true,1,0);"
    )
    phone_key_id, phone_digest = account_phone_claim_digest("13900004414")
    revoke_ids = [str(Uuid7Generator().generate()) for _ in range(4)]
    handoff_revoke_envelope = {
        "operation_id": revoke_ids[0],
        "actor_user_id": revoke_reviewer_id,
        "actor_role": "super_admin",
        "actor_scope": f"SUPER_ADMIN:{revoke_reviewer_id}",
        "idempotency_key": "synthetic-handoff-revoke-guard-0044",
        "request_digest": "a" * 64,
        "digest_key_id": "synthetic-digest-v1",
        "expected_version": 2,
        "audit_id": revoke_ids[1],
        "event_id": revoke_ids[2],
        "receipt_id": revoke_ids[3],
        "expected_postimage_digest": "b" * 64,
        "accepted_totp_step": int(datetime.now(UTC).timestamp()) // 30,
        "handoff_id": regenerated["handoff_id"],
        "onboarding_id": created["onboarding_id"],
        "active_credential_id": regenerated["credential_id"],
        "new_phone_digest_key_id": phone_key_id,
        "new_phone_digest": phone_digest,
        "reason_code": "SYNTHETIC_HANDOFF_REVOKE_GUARD",
    }

    async def verify_handoff_revoke_guard() -> None:
        dsn = os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"].replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )
        observer = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"])
        )
        invalid_steps = (
            ("missing", None),
            ("json_null", None),
            ("string", "1"),
            ("boolean", True),
            ("fraction", 1.5),
            ("negative", -1),
            ("overflow", 9223372036854775808),
        )
        try:
            for label, invalid_step in invalid_steps:
                before = await _totp_mutation_state(observer)
                connection = await asyncpg.connect(dsn)
                transaction = connection.transaction()
                await transaction.start()
                try:
                    invalid = dict(handoff_revoke_envelope)
                    if label == "missing":
                        invalid.pop("accepted_totp_step")
                    else:
                        invalid["accepted_totp_step"] = invalid_step
                    with pytest.raises(
                        asyncpg.InvalidParameterValueError
                    ) as captured:
                        await connection.fetchval(
                            "SELECT public.institution_admin_handoff_revoke_v1($1::jsonb)",
                            json.dumps(invalid),
                        )
                    assert captured.value.sqlstate == "22023"
                finally:
                    await transaction.rollback()
                    await connection.close()
                after = await _totp_mutation_state(observer)
                if after != before:
                    raise AssertionError("TOTP_GUARD_SIDE_EFFECT")

            connection = await asyncpg.connect(dsn)
            transaction = connection.transaction()
            await transaction.start()
            try:
                valid = await connection.fetchval(
                    "SELECT public.institution_admin_handoff_revoke_v1($1::jsonb)",
                    json.dumps(handoff_revoke_envelope),
                )
                assert json.loads(valid)["status"] == "REVOKED"
            finally:
                await transaction.rollback()
                await connection.close()
        finally:
            await observer.close()

    asyncio.run(verify_handoff_revoke_guard())

    _rotate_keyring(
        monkeypatch,
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
        "handoff-phone",
    )
    _rotate_keyring(
        monkeypatch,
        "KG_DIRECT_INSTITUTION_CODE_CURRENT_KEY_ID",
        "KG_DIRECT_INSTITUTION_CODE_KEYRING_JSON",
        "handoff-code",
    )

    handoff_totp_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    handoff_activation = real_db_client.post(
        "/api/v1/institution-onboarding/admin-handoffs/activate",
        headers={"Idempotency-Key": "synthetic-admin-handoff-activate-0044"},
        json={
            "onboarding_id": created["onboarding_id"],
            "handoff_id": regenerated["handoff_id"],
            "credential_id": regenerated["credential_id"],
            "activation_code": regenerated["activation_code"],
            "phone": "13900004414",
            "password": "Synthetic-handoff-password-0044!",
            "totp_secret": handoff_totp_secret,
            "totp_code": generate_totp(handoff_totp_secret, at=datetime.now(UTC)),
            "expected_version": 2,
        },
    )
    assert handoff_activation.status_code == 200
    assert handoff_activation.json()["status"] == "ACTIVATED"
    assert handoff_activation.json()["version"] == 3
    _rotate_keyring(
        monkeypatch,
        "KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID",
        "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
        "handoff-replay",
    )
    handoff_replay = real_db_client.post(
        "/api/v1/institution-onboarding/admin-handoffs/activate",
        headers={"Idempotency-Key": "synthetic-admin-handoff-activate-0044"},
        json={
            "onboarding_id": created["onboarding_id"],
            "handoff_id": regenerated["handoff_id"],
            "credential_id": regenerated["credential_id"],
            "activation_code": regenerated["activation_code"],
            "phone": "13900004414",
            "password": "Synthetic-handoff-password-0044!",
            "totp_secret": handoff_totp_secret,
            "totp_code": generate_totp(handoff_totp_secret, at=datetime.now(UTC)),
            "expected_version": 2,
        },
    )
    assert handoff_replay.status_code == 200
    assert handoff_replay.json() == handoff_activation.json()
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_audit "
        "WHERE action IN ('ADMIN_HANDOFF_CREATE','ADMIN_HANDOFF_REGENERATE','ADMIN_HANDOFF_ACTIVATE')"
    ) == 3
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE operation IN ('HANDOFF_CREATE','HANDOFF_REGENERATE','HANDOFF_ACTIVATE')"
    ) == 3
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_institution_onboarding "
        "WHERE institution_name='合成直开机构二'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE idempotency_key='synthetic-direct-http-0044'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE idempotency_key='synthetic-direct-activate-0044'"
    ) == 1
    assert totp_guarded_methods == expected_totp_guarded_methods


def test_真实ASGI预激活撤销释放手机号且旧凭据失效并可安全重放(
    real_db_client, pg_database, monkeypatch
) -> None:
    totp_guarded_methods: set[str] = set()
    _install_totp_step_invalid_matrix_probe(
        monkeypatch,
        method_names=("revoke",),
        observed=totp_guarded_methods,
    )
    actor_user_id = 9_944_402_001
    hierarchy = [9_944_402_010, 9_944_402_011, 9_944_402_012, 9_944_402_013]
    totp_secret = "JBSWY3DPEHPK3PXP"
    key_id, secret_ciphertext = seal_totp_secret(
        totp_secret, aad=platform_admin_totp_aad(actor_user_id)
    )
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({actor_user_id},'13900004421','synthetic','super_admin','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES"
        f"({hierarchy[0]},NULL,'合成总部三','D44-HQ3','headquarter','active',1),"
        f"({hierarchy[1]},{hierarchy[0]},'合成省三','D44-P3','province','active',1),"
        f"({hierarchy[2]},{hierarchy[1]},'合成市三','D44-C3','city','active',1),"
        f"({hierarchy[3]},{hierarchy[2]},'合成区县三','D44-D3','county','active',1);"
        "INSERT INTO public.platform_admin_security_profile("
        "user_id,secret_ciphertext,key_id,enabled,profile_version,failed_attempts) VALUES("
        f"{actor_user_id},decode('{secret_ciphertext.hex()}','hex'),'{key_id}',true,1,0);"
    )
    token = create_access_token({"sub": str(actor_user_id), "role": "super_admin"})
    create_headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "synthetic-direct-revoke-create-0044",
    }
    create_payload = {
        "institution_name": "合成待撤销机构",
        "institution_type": "HEALTH_STORE",
        "admin_phone": "13900004422",
        "administrative_region_id": hierarchy[3],
        "duplicate_acknowledged": False,
        "reason_code": "SYNTHETIC_CREATE_FOR_REVOKE",
        "totp_code": generate_totp(totp_secret, at=datetime.now(UTC)),
    }
    created_response = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers=create_headers,
        json=create_payload,
    )
    assert created_response.status_code == 201
    created = created_response.json()

    revoke_headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "synthetic-direct-revoke-0044",
    }
    revoke_payload = {
        "expected_version": 1,
        "totp_code": generate_totp(
            totp_secret, at=datetime.now(UTC) + timedelta(seconds=30)
        ),
        "reason_code": "SYNTHETIC_REVOKE",
    }
    revoked_response = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}:revoke",
        headers=revoke_headers,
        json=revoke_payload,
    )
    replay_response = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}:revoke",
        headers=revoke_headers,
        json=revoke_payload,
    )
    assert revoked_response.status_code == 200
    assert revoked_response.json()["status"] == "REVOKED_BEFORE_ACTIVATION"
    assert revoked_response.json()["version"] == 2
    assert replay_response.status_code == 200
    assert replay_response.json() == revoked_response.json()

    rejected_activation = real_db_client.post(
        "/api/v1/institution-onboarding/direct-activate",
        headers={"Idempotency-Key": "synthetic-revoked-activate-0044"},
        json={
            "onboarding_id": created["onboarding_id"],
            "credential_id": created["credential_id"],
            "activation_code": created["activation_code"],
            "phone": create_payload["admin_phone"],
            "password": "SyntheticPassword123!",
            "totp_secret": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
            "totp_code": "000000",
            "expected_version": 2,
        },
    )
    assert rejected_activation.status_code == 409
    assert rejected_activation.json()["code"] == "DIRECT_ONBOARDING_STATE_CONFLICT"
    replacement_actor_id = 9_944_402_101
    replacement_region_id, replacement_token = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=replacement_actor_id,
        phone="13900004423",
        code_suffix="REUSE",
        totp_secret=totp_secret,
    )
    replacement_response = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {replacement_token}",
            "Idempotency-Key": "synthetic-direct-released-phone-reuse-0044",
        },
        json={
            **create_payload,
            "institution_name": "合成释放手机号复用机构",
            "administrative_region_id": replacement_region_id,
            "reason_code": "SYNTHETIC_RELEASED_PHONE_REUSE",
            "totp_code": generate_totp(totp_secret, at=datetime.now(UTC)),
        },
    )
    assert replacement_response.status_code == 201
    assert replacement_response.json()["status"] == "PENDING_ACTIVATION"
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE idempotency_key='synthetic-direct-revoke-0044'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_audit "
        "WHERE action='DIRECT_INSTITUTION_REVOKE'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_outbox "
        "WHERE event_type='DIRECT_INSTITUTION_REVOKED'"
    ) == 1
    assert totp_guarded_methods == {"revoke"}


def test_真实手机号摘要轮换后同手机号创建仍必须全局拒绝(
    real_db_client, pg_database, monkeypatch
) -> None:
    totp_secret = "JBSWY3DPEHPK3PXP"
    first_region_id, first_token = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=9_944_402_301,
        phone="13900004461",
        code_suffix="CROSSKEY1",
        totp_secret=totp_secret,
    )
    phone = "13900004462"
    first = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {first_token}",
            "Idempotency-Key": "synthetic-cross-key-first-0044",
        },
        json=_direct_create_payload(
            institution_name="合成跨Key占用机构一",
            admin_phone=phone,
            region_id=first_region_id,
            totp_secret=totp_secret,
        ),
    )
    assert first.status_code == 201

    _rotate_keyring(
        monkeypatch,
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
        "cross-key-claim",
    )
    second_region_id, second_token = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=9_944_402_401,
        phone="13900004463",
        code_suffix="CROSSKEY2",
        totp_secret=totp_secret,
    )
    second = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {second_token}",
            "Idempotency-Key": "synthetic-cross-key-second-0044",
        },
        json=_direct_create_payload(
            institution_name="合成跨Key占用机构二",
            admin_phone=phone,
            region_id=second_region_id,
            totp_secret=totp_secret,
        ),
    )
    active_claims = pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND') AND claim_ref IN ("
        "SELECT onboarding_id FROM public.direct_institution_onboarding "
        "WHERE institution_name IN ('合成跨Key占用机构一','合成跨Key占用机构二'))"
    )
    assert (second.status_code, second.json().get("code"), active_claims) == (
        409,
        "ADMIN_PHONE_OCCUPIED",
        1,
    )


def test_真实注册占用手机号在摘要轮换后仍拒绝直开重复占用(
    real_db_client, pg_database, monkeypatch
) -> None:
    phone = "13900004479"
    before_claims = pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    )
    registered = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": phone, "password": "Synthetic-member-password-0044!"},
    )
    assert registered.status_code == 200
    _rotate_keyring(
        monkeypatch,
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
        "member-direct",
    )
    region_id, token = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=9_944_402_801,
        phone="13900004468",
        code_suffix="MEMBERDIRECT",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    duplicate = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "synthetic-member-direct-cross-key-0044",
        },
        json=_direct_create_payload(
            institution_name="合成注册跨Key占用机构",
            admin_phone=phone,
            region_id=region_id,
            totp_secret="JBSWY3DPEHPK3PXP",
        ),
    )
    assert (duplicate.status_code, duplicate.json().get("code")) == (
        409,
        "ADMIN_PHONE_OCCUPIED",
    )
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    ) == before_claims + 1


def test_受控机构与健管师手机号入口拒绝旧信封并共享跨Key占用协议(
    pg_database, monkeypatch
) -> None:
    phone = "13900004471"
    before_claims = pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    )
    old_key_id, old_digest = account_phone_claim_digest(phone)
    old_candidates = account_phone_claim_digest_candidates(phone)

    async def scenario() -> None:
        review = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"])
        )
        therapist = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"])
        )
        try:
            claim_id = Uuid7Generator().generate()
            claim_ref = Uuid7Generator().generate()
            old_shape = {
                "claim_id": str(claim_id),
                "claim_ref": str(claim_ref),
                "phone_digest_key_id": old_key_id,
                "phone_digest": old_digest,
                "expected_state": "NO_ACTIVE_CLAIM",
            }
            with pytest.raises(asyncpg.InvalidParameterValueError) as invalid:
                await review.fetchval(
                    "SELECT public.controlled_org_admin_phone_reserve_v1($1::jsonb)",
                    json.dumps(old_shape, separators=(",", ":")),
                )
            assert invalid.value.sqlstate == "22023"

            envelope = {**old_shape, "phone_digest_candidates": old_candidates}
            await review.fetchval(
                "SELECT public.controlled_org_admin_phone_reserve_v1($1::jsonb)",
                json.dumps(envelope, separators=(",", ":")),
            )
            _rotate_keyring(
                monkeypatch,
                "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
                "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
                "controlled-therapist",
            )
            new_key_id, new_digest = account_phone_claim_digest(phone)
            duplicate = {
                "claim_id": str(Uuid7Generator().generate()),
                "claim_ref": str(Uuid7Generator().generate()),
                "phone_digest_key_id": new_key_id,
                "phone_digest": new_digest,
                "phone_digest_candidates": account_phone_claim_digest_candidates(phone),
                "expected_state": "NO_ACTIVE_CLAIM",
            }
            with pytest.raises(asyncpg.UniqueViolationError) as occupied:
                await therapist.fetchval(
                    "SELECT public.therapist_account_phone_reserve_v1($1::jsonb)",
                    json.dumps(duplicate, separators=(",", ":")),
                )
            assert occupied.value.sqlstate == "23505"
        finally:
            await review.close()
            await therapist.close()

    asyncio.run(scenario())
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    ) == before_claims + 1


def test_手机号候选JSON空值与错误类型在数据库原生边界FailClosed(
    pg_database, monkeypatch
) -> None:
    phone = "13900004473"
    before_claims = pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    )
    old_key_id, old_digest = account_phone_claim_digest(phone)
    old_candidates = account_phone_claim_digest_candidates(phone)

    async def scenario() -> tuple[list[str], list[str]]:
        review = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"])
        )
        therapist = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"])
        )
        try:
            await review.fetchval(
                "SELECT public.controlled_org_admin_phone_reserve_v1($1::jsonb)",
                json.dumps(
                    {
                        "claim_id": str(Uuid7Generator().generate()),
                        "claim_ref": str(Uuid7Generator().generate()),
                        "phone_digest_key_id": old_key_id,
                        "phone_digest": old_digest,
                        "phone_digest_candidates": old_candidates,
                        "expected_state": "NO_ACTIVE_CLAIM",
                    },
                    separators=(",", ":"),
                ),
            )
            _rotate_keyring(
                monkeypatch,
                "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
                "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
                "json-null-boundary",
            )
            new_key_id, new_digest = account_phone_claim_digest(phone)
            valid_candidates = account_phone_claim_digest_candidates(phone)

            async def call(envelope: object) -> str:
                try:
                    await therapist.fetchval(
                        "SELECT public.therapist_account_phone_reserve_v1($1::jsonb)",
                        json.dumps(envelope, separators=(",", ":")),
                    )
                except asyncpg.PostgresError as exc:
                    return exc.sqlstate or type(exc).__name__
                return "SUCCESS"

            def envelope(candidates: object = valid_candidates) -> dict:
                return {
                    "claim_id": str(Uuid7Generator().generate()),
                    "claim_ref": str(Uuid7Generator().generate()),
                    "phone_digest_key_id": new_key_id,
                    "phone_digest": new_digest,
                    "phone_digest_candidates": candidates,
                    "expected_state": "NO_ACTIVE_CLAIM",
                }

            invalid_candidates = (
                {"key_id": None, "digest": None},
                {"key_id": None, "digest": "f" * 64},
                {"key_id": "zzzz-null-digest-0044", "digest": None},
                {"key_id": [], "digest": {}},
            )
            candidate_outcomes = [
                await call(
                    envelope(
                        [
                            {"key_id": new_key_id, "digest": new_digest},
                            invalid_candidate,
                        ]
                    )
                )
                for invalid_candidate in invalid_candidates
            ]
            top_level_envelopes: list[object] = []
            missing_expected_state = envelope()
            del missing_expected_state["expected_state"]
            top_level_envelopes.append(missing_expected_state)
            top_level_envelopes.append(None)
            for field, value in (
                ("expected_state", None),
                ("claim_id", []),
                ("phone_digest_key_id", None),
                ("phone_digest", {}),
            ):
                invalid_envelope = envelope()
                invalid_envelope[field] = value
                top_level_envelopes.append(invalid_envelope)
            top_level_envelopes.extend(
                (
                    envelope([]),
                    envelope([valid_candidates[0], valid_candidates[0]]),
                    envelope(list(reversed(valid_candidates))),
                    envelope([valid_candidates[0]]),
                )
            )
            top_level_outcomes = [
                await call(invalid_envelope)
                for invalid_envelope in top_level_envelopes
            ]
            coverage_outcome = await call(
                envelope([{"key_id": new_key_id, "digest": new_digest}])
            )
            occupied_outcome = await call(envelope())
            top_level_outcomes.extend((coverage_outcome, occupied_outcome))
            return candidate_outcomes, top_level_outcomes
        finally:
            await review.close()
            await therapist.close()

    candidate_outcomes, top_level_outcomes = asyncio.run(scenario())
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    ) == before_claims + 1
    assert candidate_outcomes == ["22023", "22023", "22023", "22023"], (
        f"safe_candidate_sqlstate_outcomes={candidate_outcomes}"
    )
    assert top_level_outcomes == [
        "22023",
        "22023",
        "22023",
        "22023",
        "22023",
        "22023",
        "22023",
        "22023",
        "22023",
        "22023",
        "55000",
        "23505",
    ], (
        f"safe_top_level_sqlstate_outcomes={top_level_outcomes}"
    )


def test_直开创建TOTP步JSON空值在正式ReviewWriter边界FailClosed且零副作用(
    pg_database,
) -> None:
    actor_user_id = 9_944_407_501
    region_id, _ = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=actor_user_id,
        phone="13900004751",
        code_suffix="JSON-NULL-TOTP",
        totp_secret="JBSWY3DPEHPK3PXP",
    )
    request = DirectCreateRequest(
        institution_name="合成空TOTP步拒绝机构",
        institution_type="HEALTH_STORE",
        admin_phone="13900004752",
        administrative_region_id=region_id,
        duplicate_acknowledged=False,
        reason_code="SYNTHETIC_ACCEPTANCE",
        totp_code="000000",
    )
    envelope, _, _ = build_direct_create_mutation(
        request=request,
        actor_user_id=actor_user_id,
        idempotency_key="synthetic-null-totp-step-0044",
        accepted_totp_step=int(datetime.now(UTC).timestamp()) // 30,
        id_factory=Uuid7Generator().generate,
        activation_code="synthetic-null-totp-code-0044",
    )
    counted_tables = (
        "tenant",
        "direct_institution_onboarding",
        "identity_phone_claim",
        "direct_onboarding_audit",
        "direct_onboarding_outbox",
        "direct_onboarding_receipt",
    )

    invalid_steps = (
        ("missing", object()),
        ("json_null", None),
        ("string", "1"),
        ("boolean", True),
        ("fraction", 1.5),
        ("negative", -1),
        ("overflow", 9223372036854775808),
    )

    async def scenario() -> list[tuple[str, str, list[str], bool, bool]]:
        connection = await asyncpg.connect(
            _to_asyncpg_dsn(
                os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"]
            )
        )
        try:
            review_role = os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_ROLE"]
            assert review_role.replace("_", "").isalnum()
            outcomes = []
            for label, invalid_step in invalid_steps:
                invalid = dict(envelope)
                if label == "missing":
                    invalid.pop("accepted_totp_step")
                else:
                    invalid["accepted_totp_step"] = invalid_step
                before_counts = {
                    table: await connection.fetchval(
                        f"SELECT count(*) FROM public.{table}"
                    )
                    for table in counted_tables
                }
                before_profile = await connection.fetchrow(
                    "SELECT last_accepted_time_step,failed_attempts,locked_until,"
                    "profile_version,updated_at FROM public.platform_admin_security_profile "
                    "WHERE user_id=$1",
                    actor_user_id,
                )
                transaction = connection.transaction()
                await transaction.start()
                outcome = "SUCCESS"
                observed_counts = before_counts
                observed_profile = before_profile
                try:
                    await connection.execute(
                        f'SET SESSION AUTHORIZATION "{review_role}"'
                    )
                    await connection.fetchval(
                        "SELECT public.direct_institution_create_v1($1::jsonb)",
                        json.dumps(invalid, separators=(",", ":")),
                    )
                    await connection.execute("RESET SESSION AUTHORIZATION")
                    observed_counts = {
                        table: await connection.fetchval(
                            f"SELECT count(*) FROM public.{table}"
                        )
                        for table in counted_tables
                    }
                    observed_profile = await connection.fetchrow(
                        "SELECT last_accepted_time_step,failed_attempts,locked_until,"
                        "profile_version,updated_at FROM public.platform_admin_security_profile "
                        "WHERE user_id=$1",
                        actor_user_id,
                    )
                except asyncpg.PostgresError as exc:
                    outcome = exc.sqlstate or type(exc).__name__
                finally:
                    await transaction.rollback()
                after_counts = {
                    table: await connection.fetchval(
                        f"SELECT count(*) FROM public.{table}"
                    )
                    for table in counted_tables
                }
                after_profile = await connection.fetchrow(
                    "SELECT last_accepted_time_step,failed_attempts,locked_until,"
                    "profile_version,updated_at FROM public.platform_admin_security_profile "
                    "WHERE user_id=$1",
                    actor_user_id,
                )
                outcomes.append(
                    (
                        label,
                        outcome,
                        sorted(
                            table
                            for table in counted_tables
                            if observed_counts[table] != before_counts[table]
                        ),
                        observed_profile != before_profile,
                        after_counts == before_counts and after_profile == before_profile,
                    )
                )
            return outcomes
        finally:
            await connection.close()

    outcomes = asyncio.run(scenario())
    assert all(rollback_isolated for *_, rollback_isolated in outcomes)
    assert outcomes == [
        (label, "22023", [], False, True) for label, _ in invalid_steps
    ], f"safe_totp_step_invalid_outcomes={outcomes}"


def test_只读摘要候选函数JSON空值必须稳定作为输入错误() -> None:
    async def scenario() -> list[str]:
        admin = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"])
        )
        try:
            outcomes: list[str] = []
            for candidates in (
                [{"key_id": None, "digest": None}],
                [{"key_id": "target-key-0044", "digest": None}],
                [{"key_id": None, "digest": "f" * 64}],
            ):
                try:
                    await admin.fetchval(
                        "SELECT public.direct_keyed_digest_candidate_v1($1::jsonb,$2)",
                        json.dumps(candidates, separators=(",", ":")),
                        "target-key-0044",
                    )
                except asyncpg.PostgresError as exc:
                    outcomes.append(exc.sqlstate or type(exc).__name__)
                else:
                    outcomes.append("SUCCESS")
            return outcomes
        finally:
            await admin.close()

    outcomes = asyncio.run(scenario())
    assert outcomes == ["22023", "22023", "22023"], (
        f"safe_digest_helper_sqlstate_outcomes={outcomes}"
    )


def test_受控机构与健管师不相交Key配置并发时全局锁使第二写入安全失败(
    pg_database
) -> None:
    before_claims = pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    )
    base_key_id, base_digest = account_phone_claim_digest("13900004472")

    async def scenario() -> list[object]:
        review = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"])
        )
        therapist = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"])
        )
        ready = asyncio.Event()
        entered = 0

        async def reserve(connection, function_name: str, key_id: str, digest: str):
            nonlocal entered
            candidates = sorted(
                (
                    {"key_id": base_key_id, "digest": base_digest},
                    {"key_id": key_id, "digest": digest},
                ),
                key=lambda value: value["key_id"],
            )
            envelope = {
                "claim_id": str(Uuid7Generator().generate()),
                "claim_ref": str(Uuid7Generator().generate()),
                "phone_digest_key_id": key_id,
                "phone_digest": digest,
                "phone_digest_candidates": candidates,
                "expected_state": "NO_ACTIVE_CLAIM",
            }
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            return await connection.fetchval(
                f"SELECT public.{function_name}($1::jsonb)",
                json.dumps(envelope, separators=(",", ":")),
            )

        try:
            return await asyncio.gather(
                reserve(
                    review,
                    "controlled_org_admin_phone_reserve_v1",
                    "disjoint-controlled-0044",
                    "a" * 64,
                ),
                reserve(
                    therapist,
                    "therapist_account_phone_reserve_v1",
                    "disjoint-therapist-0044",
                    "b" * 64,
                ),
                return_exceptions=True,
            )
        finally:
            await review.close()
            await therapist.close()

    outcomes = asyncio.run(scenario())
    failures = [value for value in outcomes if isinstance(value, BaseException)]
    assert len(failures) == 1
    assert getattr(failures[0], "sqlstate", None) == "55000"
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    ) == before_claims + 1
    pg_database.execute(
        "DELETE FROM public.identity_phone_claim WHERE phone_digest_key_id IN "
        "('disjoint-controlled-0044','disjoint-therapist-0044')"
    )
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim "
        "WHERE state IN ('PENDING','BOUND')"
    ) == before_claims


def test_真实请求摘要Key轮换后同幂等请求语义保持稳定(
    real_db_client, pg_database, monkeypatch
) -> None:
    totp_secret = "JBSWY3DPEHPK3PXP"
    region_id, token = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=9_944_402_601,
        phone="13900004464",
        code_suffix="DIGESTROTATE",
        totp_secret=totp_secret,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "synthetic-digest-rotation-replay-0044",
    }
    payload = _direct_create_payload(
        institution_name="合成摘要轮换幂等机构",
        admin_phone="13900004465",
        region_id=region_id,
        totp_secret=totp_secret,
    )
    first = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers=headers,
        json=payload,
    )
    assert first.status_code == 201
    stored_digest_key_id = os.environ[
        "KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID"
    ]
    new_digest_key_id = _rotate_keyring(
        monkeypatch,
        "KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID",
        "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
        "request-digest",
    )
    replay = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers=headers,
        json=payload,
    )
    assert (replay.status_code, replay.json().get("code")) == (
        409,
        "ONE_TIME_CREDENTIAL_ALREADY_ISSUED",
    )
    assert pg_database.fetch_value(
        "SELECT digest_key_id FROM public.direct_onboarding_receipt "
        "WHERE actor_scope LIKE 'user:%' AND operation='CREATE' "
        "AND idempotency_key='synthetic-digest-rotation-replay-0044'"
    ) == stored_digest_key_id
    retained = json.loads(os.environ["KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON"])
    monkeypatch.setenv(
        "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
        json.dumps(
            {new_digest_key_id: retained[new_digest_key_id]},
            separators=(",", ":"),
        ),
    )
    unavailable = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers=headers,
        json=payload,
    )
    assert (unavailable.status_code, unavailable.json().get("code")) == (
        503,
        "DEPENDENCY_UNAVAILABLE",
    )
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_institution_onboarding "
        "WHERE institution_name='合成摘要轮换幂等机构'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE operation='CREATE' "
        "AND idempotency_key='synthetic-digest-rotation-replay-0044'"
    ) == 1


def test_真实平台TOTP旧Key保留时可继续且移除后安全失败(
    real_db_client, pg_database, monkeypatch
) -> None:
    totp_secret = "JBSWY3DPEHPK3PXP"
    region_id, token = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=9_944_402_701,
        phone="13900004466",
        code_suffix="OLDKEYREMOVE",
        totp_secret=totp_secret,
    )
    first = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "synthetic-old-key-removal-create-0044",
        },
        json=_direct_create_payload(
            institution_name="合成旧TOTP Key保留机构",
            admin_phone="13900004467",
            region_id=region_id,
            totp_secret=totp_secret,
        ),
    )
    assert first.status_code == 201
    new_key_id = _rotate_keyring(
        monkeypatch,
        "KG_PLATFORM_ADMIN_TOTP_CURRENT_KEY_ID",
        "KG_PLATFORM_ADMIN_TOTP_KEYRING_JSON",
        "totp-removal",
    )
    retained_old_key = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "synthetic-old-totp-retained-create-0044",
        },
        json={
            **_direct_create_payload(
                institution_name="合成旧TOTP Key轮换保留机构",
                admin_phone="13900004468",
                region_id=region_id,
                totp_secret=totp_secret,
            ),
            "totp_code": generate_totp(
                totp_secret, at=datetime.now(UTC) + timedelta(seconds=30)
            ),
        },
    )
    assert retained_old_key.status_code == 201
    retained = json.loads(os.environ["KG_PLATFORM_ADMIN_TOTP_KEYRING_JSON"])
    monkeypatch.setenv(
        "KG_PLATFORM_ADMIN_TOTP_KEYRING_JSON",
        json.dumps({new_key_id: retained[new_key_id]}, separators=(",", ":")),
    )
    missing_old_key = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "synthetic-old-totp-removed-create-0044",
        },
        json={
            **_direct_create_payload(
                institution_name="合成旧TOTP Key移除机构",
                admin_phone="13900004469",
                region_id=region_id,
                totp_secret=totp_secret,
            ),
            "totp_code": generate_totp(
                totp_secret, at=datetime.now(UTC) + timedelta(seconds=60)
            ),
        },
    )
    assert missing_old_key.status_code == 503
    assert missing_old_key.json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_真实PostgreSQL旧Key记录在轮换保留旧Key后可继续并且新写入使用新Key(
    real_db_client, pg_database, monkeypatch
) -> None:
    actor_user_id = 9_944_402_501
    totp_secret = "JBSWY3DPEHPK3PXP"
    region_id, token = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=actor_user_id,
        phone="13900004451",
        code_suffix="ROTATE",
        totp_secret=totp_secret,
    )
    old_key_ids = {
        "pii": os.environ["KG_DIRECT_INSTITUTION_PII_CURRENT_KEY_ID"],
        "code": os.environ["KG_DIRECT_INSTITUTION_CODE_CURRENT_KEY_ID"],
        "digest": os.environ["KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID"],
        "totp": os.environ["KG_PLATFORM_ADMIN_TOTP_CURRENT_KEY_ID"],
        "phone": os.environ["KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID"],
    }
    created_response = real_db_client.post(
        "/api/v1/platform/direct-institution-onboardings",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "synthetic-direct-rotation-create-0044",
        },
        json=_direct_create_payload(
            institution_name="合成密钥轮换机构",
            admin_phone="13900004452",
            region_id=region_id,
            totp_secret=totp_secret,
        ),
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert pg_database.fetch_value(
        "SELECT admin_phone_key_id=$$" + old_key_ids["pii"]
        + "$$ FROM public.direct_institution_onboarding WHERE onboarding_id=$$"
        + created["onboarding_id"] + "$$::uuid"
    ) is True
    assert pg_database.fetch_value(
        "SELECT credential_digest_key_id=$$" + old_key_ids["code"]
        + "$$ FROM public.direct_institution_activation_credential WHERE credential_id=$$"
        + created["credential_id"] + "$$::uuid"
    ) is True
    assert pg_database.fetch_value(
        "SELECT phone_digest_key_id=$$" + old_key_ids["phone"]
        + "$$ FROM public.identity_phone_claim WHERE claim_ref=$$"
        + created["onboarding_id"] + "$$::uuid"
    ) is True

    new_key_ids = {
        "pii": _rotate_keyring(
            monkeypatch,
            "KG_DIRECT_INSTITUTION_PII_CURRENT_KEY_ID",
            "KG_DIRECT_INSTITUTION_PII_KEYRING_JSON",
            "pii",
        ),
        "code": _rotate_keyring(
            monkeypatch,
            "KG_DIRECT_INSTITUTION_CODE_CURRENT_KEY_ID",
            "KG_DIRECT_INSTITUTION_CODE_KEYRING_JSON",
            "code",
        ),
        "digest": _rotate_keyring(
            monkeypatch,
            "KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID",
            "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
            "digest",
        ),
        "totp": _rotate_keyring(
            monkeypatch,
            "KG_PLATFORM_ADMIN_TOTP_CURRENT_KEY_ID",
            "KG_PLATFORM_ADMIN_TOTP_KEYRING_JSON",
            "totp",
        ),
        "phone": _rotate_keyring(
            monkeypatch,
            "KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID",
            "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
            "phone",
        ),
    }
    assert len(set(old_key_ids.values()) | set(new_key_ids.values())) == 10
    assert real_db_client.get(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200
    regenerated = real_db_client.post(
        f"/api/v1/platform/direct-institution-onboardings/{created['onboarding_id']}"
        "/activation-credential:regenerate",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "synthetic-direct-rotation-regenerate-0044",
        },
        json={
            "expected_version": 1,
            "totp_code": generate_totp(
                totp_secret, at=datetime.now(UTC) + timedelta(seconds=30)
            ),
            "reason_code": "SYNTHETIC_KEY_ROTATION",
        },
    )
    assert regenerated.status_code == 201
    regenerated_value = regenerated.json()
    assert pg_database.fetch_value(
        "SELECT credential_digest_key_id=$$" + new_key_ids["code"]
        + "$$ FROM public.direct_institution_activation_credential WHERE credential_id=$$"
        + regenerated_value["credential_id"] + "$$::uuid"
    ) is True
    activation_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    activated = real_db_client.post(
        "/api/v1/institution-onboarding/direct-activate",
        headers={"Idempotency-Key": "synthetic-direct-rotation-activate-0044"},
        json={
            "onboarding_id": created["onboarding_id"],
            "credential_id": regenerated_value["credential_id"],
            "activation_code": regenerated_value["activation_code"],
            "phone": "13900004452",
            "password": "Synthetic-rotation-password-0044!",
            "totp_secret": activation_secret,
            "totp_code": generate_totp(activation_secret, at=datetime.now(UTC)),
            "expected_version": 2,
        },
    )
    assert activated.status_code == 200
    assert pg_database.fetch_value(
        "SELECT a.totp_key_id=$$" + new_key_ids["totp"]
        + "$$ FROM public.direct_institution_admin_account a "
        "JOIN public.direct_institution_onboarding root ON root.activated_user_id=a.user_id "
        "WHERE root.onboarding_id=$$" + created["onboarding_id"] + "$$::uuid"
    ) is True


def test_真实双连接并发创建与激活保持单一赢家(
    pg_database,
) -> None:
    first_actor_user_id = 9_944_403_001
    second_actor_user_id = 9_944_403_101
    totp_secret = "JBSWY3DPEHPK3PXP"
    first_region_id, _ = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=first_actor_user_id,
        phone="13900004431",
        code_suffix="RACE1",
        totp_secret=totp_secret,
    )
    second_region_id, _ = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=second_actor_user_id,
        phone="13900004434",
        code_suffix="RACE2",
        totp_secret=totp_secret,
    )
    phone = "13900004432"
    generator = Uuid7Generator()
    accepted_step = int(datetime.now(UTC).timestamp()) // 30
    mutations = []
    for index, actor_user_id, region_id in (
        (1, first_actor_user_id, first_region_id),
        (2, second_actor_user_id, second_region_id),
    ):
        request = DirectCreateRequest.model_validate(
            _direct_create_payload(
                institution_name=f"合成并发直开机构{index}",
                admin_phone=phone,
                region_id=region_id,
                totp_secret=totp_secret,
            )
        )
        mutations.append(
            build_direct_create_mutation(
                request=request,
                actor_user_id=actor_user_id,
                idempotency_key=f"synthetic-direct-race-create-{index}",
                accepted_totp_step=accepted_step,
                id_factory=generator.generate,
                activation_code=f"synthetic-race-activation-{index}-0044",
            )
        )

    async def scenario():
        review_engine = create_async_engine(
            os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"],
            poolclass=NullPool,
        )
        review_sessions = async_sessionmaker(review_engine, expire_on_commit=False)
        try:
            create_barrier = asyncio.Barrier(2)

            async def create(index: int):
                envelope, _, _ = mutations[index]
                async with review_sessions() as session:
                    await create_barrier.wait()
                    try:
                        result = await DirectInstitutionOnboardingRepository(
                            session
                        ).create(envelope)
                        await session.commit()
                        return result, None
                    except DirectInstitutionOnboardingRepositoryError as error:
                        await session.rollback()
                        return None, error.args[0]

            create_results = await asyncio.wait_for(
                asyncio.gather(create(0), create(1)), timeout=15
            )
        finally:
            await review_engine.dispose()

        winners = [result for result, error in create_results if result is not None]
        errors = [error for result, error in create_results if result is None]
        assert len(winners) == 1
        assert errors == ["ADMIN_PHONE_OCCUPIED"]
        created = winners[0]
        winning_index = next(
            index
            for index, (envelope, _, _) in enumerate(mutations)
            if envelope["onboarding_id"] == str(created["onboarding_id"])
        )
        winning_envelope, _, activation_code = mutations[winning_index]

        onboarding_engine = create_async_engine(
            os.environ["KG_TEST_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"],
            poolclass=NullPool,
        )
        onboarding_sessions = async_sessionmaker(
            onboarding_engine, expire_on_commit=False
        )
        try:
            activation_request = DirectActivationRequest(
                onboarding_id=UUID(winning_envelope["onboarding_id"]),
                credential_id=UUID(winning_envelope["credential_id"]),
                activation_code=activation_code,
                phone=phone,
                password="Synthetic-race-password-0044!",
                totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
                totp_code="000000",
                expected_version=1,
            )
            async with onboarding_sessions() as authority_session:
                authority = await DirectInstitutionOnboardingRepository(
                    authority_session
                ).activation_authority(
                    onboarding_id=activation_request.onboarding_id,
                    credential_id=activation_request.credential_id,
                    credential_digests=credential_digest_candidates(
                        activation_code
                    ),
                )
            assert authority is not None
            activation_mutations = [
                build_direct_activation_mutation(
                    request=activation_request,
                    authority=authority,
                    idempotency_key=f"synthetic-direct-race-activate-{index}",
                    accepted_totp_step=accepted_step,
                    id_factory=generator.generate,
                )[0]
                for index in (1, 2)
            ]
            activation_barrier = asyncio.Barrier(2)

            async def activate(envelope):
                async with onboarding_sessions() as session:
                    await activation_barrier.wait()
                    try:
                        result = await DirectInstitutionOnboardingRepository(
                            session
                        ).activate(envelope)
                        await session.commit()
                        return result, None
                    except DirectInstitutionOnboardingRepositoryError as error:
                        await session.rollback()
                        return None, error.args[0]

            activation_results = await asyncio.wait_for(
                asyncio.gather(*(activate(value) for value in activation_mutations)),
                timeout=15,
            )
        finally:
            await onboarding_engine.dispose()
        return created, activation_results

    created, activation_results = asyncio.run(scenario())
    winning_index = next(
        index
        for index, (envelope, _, _) in enumerate(mutations)
        if envelope["onboarding_id"] == str(created["onboarding_id"])
    )
    losing_envelope = mutations[1 - winning_index][0]
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_institution_onboarding "
        "WHERE institution_name LIKE '合成并发直开机构%'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE idempotency_key LIKE 'synthetic-direct-race-create-%'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_audit "
        "WHERE action='DIRECT_INSTITUTION_CREATE' "
        "AND object_id=$$" + created["onboarding_id"] + "$$::uuid"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_outbox "
        "WHERE event_type='DIRECT_INSTITUTION_CREATED' "
        "AND aggregate_id=$$" + created["onboarding_id"] + "$$::uuid"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_institution_onboarding WHERE onboarding_id=$$"
        + losing_envelope["onboarding_id"]
        + "$$::uuid"
    ) == 0
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.identity_phone_claim WHERE claim_id=$$"
        + losing_envelope["operation_id"]
        + "$$::uuid"
    ) == 0
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt WHERE receipt_id=$$"
        + losing_envelope["receipt_id"]
        + "$$::uuid"
    ) == 0
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_audit WHERE audit_id=$$"
        + losing_envelope["audit_id"]
        + "$$::uuid"
    ) == 0
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_outbox WHERE event_id=$$"
        + losing_envelope["event_id"]
        + "$$::uuid"
    ) == 0
    assert sum(result is not None for result, _ in activation_results) == 1
    assert pg_database.fetch_value(
        'SELECT count(*) FROM public."user" '
        f"WHERE tenant_id=(SELECT tenant_id FROM public.direct_institution_onboarding "
        f"WHERE onboarding_id='{created['onboarding_id']}') AND role='org_admin' AND status='active'"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE idempotency_key LIKE 'synthetic-direct-race-activate-%'"
    ) == 1


def test_真实提交结果确认区分已提交未提交未知且不重复副作用(
    pg_database, monkeypatch
) -> None:
    actor_user_id = 9_944_404_001
    totp_secret = "JBSWY3DPEHPK3PXP"
    region_id, _ = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=actor_user_id,
        phone="13900004441",
        code_suffix="COMMIT",
        totp_secret=totp_secret,
    )
    absent_actor_user_id = 9_944_404_101
    absent_region_id, _ = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=absent_actor_user_id,
        phone="13900004445",
        code_suffix="NOTCOMMIT",
        totp_secret=totp_secret,
    )
    unknown_actor_user_id = 9_944_404_201
    unknown_region_id, _ = _seed_super_admin_and_region(
        pg_database,
        actor_user_id=unknown_actor_user_id,
        phone="13900004446",
        code_suffix="UNKNOWN",
        totp_secret=totp_secret,
    )
    generator = Uuid7Generator()
    review_engine = create_async_engine(
        os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_DATABASE_URL"],
        poolclass=NullPool,
    )
    review_sessions = async_sessionmaker(review_engine, expire_on_commit=False)
    onboarding_engine = create_async_engine(
        os.environ["KG_TEST_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"],
        poolclass=NullPool,
    )
    onboarding_sessions = async_sessionmaker(onboarding_engine, expire_on_commit=False)
    accepted_step = int(datetime.now(UTC).timestamp()) // 30

    def create_mutation(
        suffix: str,
        phone: str,
        *,
        operation_actor_user_id: int,
        operation_region_id: int,
    ):
        request = DirectCreateRequest(
            institution_name=f"合成提交确认机构{suffix}",
            institution_type="HEALTH_STORE",
            admin_phone=phone,
            administrative_region_id=operation_region_id,
            duplicate_acknowledged=False,
            reason_code="SYNTHETIC_COMMIT_CONFIRMATION",
            totp_code="000000",
        )
        return build_direct_create_mutation(
            request=request,
            actor_user_id=operation_actor_user_id,
            idempotency_key=f"synthetic-direct-commit-{suffix}",
            accepted_totp_step=accepted_step,
            id_factory=generator.generate,
            activation_code=f"synthetic-commit-code-{suffix}-0044",
        )

    async def scenario() -> None:
        monkeypatch.setattr(
            direct_api,
            "get_slice1_session_factory",
            lambda kind: review_sessions if kind == "review_writer" else onboarding_sessions,
        )

        committed_envelope, committed_confirmation, _ = create_mutation(
            "committed",
            "13900004442",
            operation_actor_user_id=actor_user_id,
            operation_region_id=region_id,
        )
        async with review_sessions() as session:
            await DirectInstitutionOnboardingRepository(session).create(
                committed_envelope
            )
            with pytest.raises(direct_api.DirectOnboardingError) as captured:
                await direct_api._commit_review(
                    _CommitThenDisconnect(session),
                    confirmation=committed_confirmation,
                    credential_delivery=True,
                )
        assert captured.value.args == ("ONE_TIME_CREDENTIAL_ALREADY_ISSUED",)
        async with review_sessions() as confirmation_session:
            confirmed = await DirectInstitutionOnboardingRepository(
                confirmation_session
            ).review_commit_confirm(committed_confirmation)
        assert confirmed and confirmed["outcome"] == "COMMITTED"

        replayed_step_envelope, _, _ = create_mutation(
            "same-step-rejected",
            "13900004447",
            operation_actor_user_id=actor_user_id,
            operation_region_id=region_id,
        )
        async with review_sessions() as session:
            with pytest.raises(
                DirectInstitutionOnboardingRepositoryError,
                match="STEP_UP_FORBIDDEN",
            ):
                await DirectInstitutionOnboardingRepository(session).create(
                    replayed_step_envelope
                )
            await session.rollback()

        absent_envelope, absent_confirmation, _ = create_mutation(
            "not-committed",
            "13900004443",
            operation_actor_user_id=absent_actor_user_id,
            operation_region_id=absent_region_id,
        )
        async with review_sessions() as session:
            await DirectInstitutionOnboardingRepository(session).create(absent_envelope)
            with pytest.raises(direct_api.DirectOnboardingError) as captured:
                await direct_api._commit_review(
                    _DisconnectBeforeCommit(session),
                    confirmation=absent_confirmation,
                    credential_delivery=False,
                )
        assert captured.value.args == ("DEPENDENCY_UNAVAILABLE",)
        async with review_sessions() as confirmation_session:
            confirmed = await DirectInstitutionOnboardingRepository(
                confirmation_session
            ).review_commit_confirm(absent_confirmation)
        assert confirmed and confirmed["outcome"] == "NOT_COMMITTED"

        unknown_envelope, unknown_confirmation, _ = create_mutation(
            "unknown",
            "13900004444",
            operation_actor_user_id=unknown_actor_user_id,
            operation_region_id=unknown_region_id,
        )
        unavailable_engine = create_async_engine(
            "postgresql+asyncpg://synthetic:synthetic@127.0.0.1:1/synthetic",
            poolclass=NullPool,
            connect_args={"timeout": 1},
        )
        unavailable_sessions = async_sessionmaker(
            unavailable_engine, expire_on_commit=False
        )
        async with review_sessions() as session:
            await DirectInstitutionOnboardingRepository(session).create(unknown_envelope)
            monkeypatch.setattr(
                direct_api,
                "get_slice1_session_factory",
                lambda kind: unavailable_sessions,
            )
            with pytest.raises(direct_api.DirectOnboardingError) as captured:
                await direct_api._commit_review(
                    _CommitThenDisconnect(session),
                    confirmation=unknown_confirmation,
                    credential_delivery=False,
                )
        assert captured.value.args == ("COMMIT_OUTCOME_UNKNOWN",)
        await unavailable_engine.dispose()
        async with review_sessions() as confirmation_session:
            confirmed = await DirectInstitutionOnboardingRepository(
                confirmation_session
            ).review_commit_confirm(unknown_confirmation)
        assert confirmed and confirmed["outcome"] == "COMMITTED"

        activation_request = DirectActivationRequest(
            onboarding_id=UUID(committed_envelope["onboarding_id"]),
            credential_id=UUID(committed_envelope["credential_id"]),
            activation_code="synthetic-commit-code-committed-0044",
            phone="13900004442",
            password="Synthetic-commit-password-0044!",
            totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            totp_code="000000",
            expected_version=1,
        )
        async with onboarding_sessions() as authority_session:
            authority = await DirectInstitutionOnboardingRepository(
                authority_session
            ).activation_authority(
                onboarding_id=activation_request.onboarding_id,
                credential_id=activation_request.credential_id,
                credential_digests=credential_digest_candidates(
                    activation_request.activation_code
                ),
            )
        assert authority is not None
        activation_envelope, activation_confirmation = build_direct_activation_mutation(
            request=activation_request,
            authority=authority,
            idempotency_key="synthetic-direct-commit-activation",
            accepted_totp_step=int(datetime.now(UTC).timestamp()) // 30,
            id_factory=generator.generate,
        )
        monkeypatch.setattr(
            direct_api,
            "get_slice1_session_factory",
            lambda kind: onboarding_sessions,
        )
        async with onboarding_sessions() as activation_session:
            await DirectInstitutionOnboardingRepository(activation_session).activate(
                activation_envelope
            )
            await direct_api._commit_activation(
                _CommitThenDisconnect(activation_session),
                confirmation=activation_confirmation,
            )
        async with onboarding_sessions() as replay_session:
            replay = await DirectInstitutionOnboardingRepository(
                replay_session
            ).activation_replay(
                credential_id=activation_request.credential_id,
                idempotency_key="synthetic-direct-commit-activation",
                request_digest=activation_envelope["request_digest"],
                request_digest_candidates=direct_activation_request_digest_candidates(
                    activation_request
                ),
            )
        assert replay and replay["status"] == "ACTIVE_COMPLIANCE_PENDING"

        with pytest.raises(asyncio.CancelledError):
            await direct_api._commit_review(
                _CancelAtCommit(),
                confirmation=committed_confirmation,
                credential_delivery=False,
            )

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(review_engine.dispose())
        asyncio.run(onboarding_engine.dispose())

    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_institution_onboarding "
        "WHERE institution_name LIKE '合成提交确认机构%'"
    ) == 2
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.direct_onboarding_receipt "
        "WHERE idempotency_key LIKE 'synthetic-direct-commit-%'"
    ) == 3
