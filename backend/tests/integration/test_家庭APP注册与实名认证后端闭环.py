from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import UUID

import pytest

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
REVIEWER_ID = 94801
CLASSIFICATION_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29801")


@pytest.fixture(scope="module", autouse=True)
def _enforce_step_up_operation_log_permissions(pg_database, application_database):
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    pg_database.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
        f'ON TABLE public.operation_log FROM "{application_role}"'
    )
    privileges = application_database.fetch_rows(
        "SELECT "
        "has_table_privilege(current_user, 'public.operation_log', 'SELECT') AS can_select, "
        "has_table_privilege(current_user, 'public.operation_log', 'INSERT') AS can_insert, "
        "has_sequence_privilege(current_user, 'public.operation_log_id_seq', 'USAGE') AS can_sequence, "
        "has_table_privilege(current_user, 'public.operation_log', 'UPDATE') AS can_update, "
        "has_table_privilege(current_user, 'public.operation_log', 'DELETE') AS can_delete, "
        "has_table_privilege(current_user, 'public.operation_log', 'TRUNCATE') AS can_truncate, "
        "has_table_privilege(current_user, 'public.operation_log', 'REFERENCES') AS can_references, "
        "has_table_privilege(current_user, 'public.operation_log', 'TRIGGER') AS can_trigger, "
        "pg_try_advisory_xact_lock(94801001) AS can_advisory_lock"
    )[0]
    assert privileges == {
        "can_select": True,
        "can_insert": True,
        "can_sequence": True,
        "can_update": False,
        "can_delete": False,
        "can_truncate": False,
        "can_references": False,
        "can_trigger": False,
        "can_advisory_lock": True,
    }, "STAGE_STEP_UP_PERMISSION_PREFLIGHT"


@pytest.fixture(autouse=True)
def _bind_isolated_runtime_urls(monkeypatch):
    aliases = {
        "KG_IDENTITY_APPLICATION_DATABASE_URL": "KG_TEST_DATABASE_URL",
        "KG_VERIFICATION_WRITER_DATABASE_URL": (
            "KG_TEST_VERIFICATION_WRITER_DATABASE_URL"
        ),
        "KG_DELIVERY_WORKER_DATABASE_URL": "KG_TEST_DELIVERY_WORKER_DATABASE_URL",
    }
    values = {alias: os.getenv(source) for alias, source in aliases.items()}
    configured = [value for value in values.values() if value]
    if configured and len(configured) != len(set(configured)):
        pytest.fail("STAGE_DATABASE_ROLE_ISOLATION", pytrace=False)
    for alias, value in values.items():
        if value:
            monkeypatch.setenv(alias, value)
    application_url = values["KG_IDENTITY_APPLICATION_DATABASE_URL"]
    if application_url:
        from sqlalchemy.engine import make_url

        parsed = make_url(application_url)
        settings = {
            "KG_DATABASE_HOST": parsed.host,
            "KG_DATABASE_PORT": str(parsed.port),
            "KG_DATABASE_NAME": parsed.database,
            "KG_DATABASE_USER": parsed.username,
            "KG_DATABASE_PASSWORD": parsed.password,
        }
        for name, value in settings.items():
            if value:
                monkeypatch.setenv(name, value)


def _headers(user_id: int, role: str) -> dict[str, str]:
    from app.core.security import create_access_token
    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _submission(key="family-app-identity-submit-v1"):
    return {
        "real_name": "测试会员甲",
        "id_card": "11010519491231002X",
        "idempotency_key": key,
        "consent_version": "identity-consent-v1",
    }


def _stage_call(stage: str, operation):
    try:
        return operation()
    except BaseException as exc:
        pytest.fail(f"{stage}_{type(exc).__name__.upper()}", pytrace=False)


def _require_status(response, expected: int, stage: str) -> None:
    if response.status_code != expected:
        pytest.fail(stage, pytrace=False)


def _require_equal(actual, expected, stage: str) -> None:
    if actual != expected:
        pytest.fail(stage, pytrace=False)


def _require_absent(text: str, forbidden: str, stage: str) -> None:
    if forbidden in text:
        pytest.fail(stage, pytrace=False)


def _seed_reviewer_and_classification(pg_database, user_id: int) -> None:
    from app.modules.auth.service import hash_password

    reviewer_password_hash = hash_password("Secret12345")
    _stage_call(
        "STAGE_SEED_REVIEWER",
        lambda: pg_database.execute(
            'INSERT INTO public."user" '
            '(id, phone, password_hash, role, status, verify_status, created_at, updated_at) '
            f"VALUES ({REVIEWER_ID}, '13900094801', '{reviewer_password_hash}', 'super_admin', "
            f"'active', 'verified', '{NOW.isoformat()}', '{NOW.isoformat()}')"
        ),
    )
    _stage_call(
        "STAGE_SEED_CLASSIFICATION",
        lambda: pg_database.execute(
            "INSERT INTO public.user_account_classification_decision "
            "(decision_ref, user_ref, facts_version, classification_version, account_class, decision_basis_code, decided_at) "
            f"VALUES ('{CLASSIFICATION_ID}', {user_id}, 1, 1, 'natural_person', "
            f"'trusted_provisioning', '{NOW.isoformat()}')"
        ),
    )


def _step_up_headers(real_db_client, admin_headers, user_id: int) -> dict[str, str]:
    response = _stage_call(
        "STAGE_STEP_UP_CALL",
        lambda: real_db_client.post(
            f"/api/v1/reviews/users/{user_id}/identity/step-up",
            headers=admin_headers,
            json={"password": "Secret12345"},
        ),
    )
    _require_status(response, 200, "STAGE_STEP_UP_STATUS")
    token = response.json()["data"]["step_up_token"]
    return {**admin_headers, "X-Identity-Review-Step-Up": token}


async def _dispatch_once():
    from app.tasks.registration_outbox_worker_bootstrap import (
        RegistrationOutboxWorkerBootstrap,
    )

    async with RegistrationOutboxWorkerBootstrap.from_environment() as runtime:
        return await runtime.dispatch_once(
            lease_owner="family-identity-contract-worker",
            limit=1,
        )


def test_本人提交查询管理员详情审计审核通过与幂等闭环(
    real_db_client, application_database, outbox_audit_database
) -> None:
    registered = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": "13800139801", "password": "Secret12345"},
    )
    _require_status(registered, 200, "STAGE_REGISTER_FIRST")
    user_id = registered.json()["data"]["id"]
    member_headers = _headers(user_id, "member")

    created = real_db_client.put(
        "/api/v1/users/me/identity-verification",
        json=_submission(), headers=member_headers,
    )
    _require_status(created, 200, "STAGE_SUBMIT_FIRST")
    _require_equal(created.json()["data"], {
        "status": "submitted", "submission_version": 1,
        "id_card_masked": "110105********002X",
        "submitted_at": created.json()["data"]["submitted_at"],
        "outcome": "CREATED",
    }, "STAGE_SUBMIT_RESPONSE")
    _require_absent(created.text, _submission()["id_card"], "STAGE_SUBMIT_REDACTION")
    account_projection = application_database.fetch_rows(
        'SELECT real_name, id_card, verify_status, tenant_id FROM public."user" '
        f"WHERE id={user_id}"
    )[0]
    assert account_projection == {
        "real_name": None,
        "id_card": None,
        "verify_status": "submitted",
        "tenant_id": None,
    }, "STAGE_FORMAL_SUBMISSION_NO_LEGACY_PII"

    replay = real_db_client.put(
        "/api/v1/users/me/identity-verification",
        json=_submission(), headers=member_headers,
    )
    _require_status(replay, 200, "STAGE_REPLAY_FIRST")
    _require_equal(replay.json()["data"]["outcome"], "REPLAYED", "STAGE_REPLAY_OUTCOME")
    assert application_database.fetch_value(
        "SELECT count(*) FROM public.identity_verification_submission "
        f"WHERE user_ref={user_id}"
    ) == 1, "STAGE_CANONICAL_COUNT"

    _stage_call(
        "STAGE_SEED_REVIEWER_CLASSIFICATION",
        lambda: _seed_reviewer_and_classification(application_database, user_id),
    )
    admin_headers = _headers(REVIEWER_ID, "super_admin")
    queue = _stage_call(
        "STAGE_QUEUE_CALL",
        lambda: real_db_client.get(
            "/api/v1/reviews/identity?status=submitted", headers=admin_headers
        ),
    )
    _require_status(queue, 200, "STAGE_QUEUE_STATUS")
    assert any(item["user_id"] == user_id for item in queue.json()["data"]["items"]), "STAGE_QUEUE_MEMBER"
    _require_absent(queue.text, _submission()["id_card"], "STAGE_QUEUE_REDACTION")

    detail_headers = _step_up_headers(real_db_client, admin_headers, user_id)
    detail = _stage_call(
        "STAGE_DETAIL_CALL",
        lambda: real_db_client.get(
            f"/api/v1/reviews/users/{user_id}/identity?purpose_code=MANUAL_REVIEW",
            headers=detail_headers,
        ),
    )
    _require_status(detail, 200, "STAGE_DETAIL_STATUS")
    _require_equal(detail.json()["data"]["real_name"], _submission()["real_name"], "STAGE_DETAIL_PAYLOAD")
    assert application_database.fetch_value(
        "SELECT count(*) FROM public.operation_log "
        f"WHERE object_id={user_id} AND action='identity_sensitive_detail_read'"
    ) == 1, "STAGE_DETAIL_AUDIT"

    approved = _stage_call(
        "STAGE_APPROVE_CALL",
        lambda: real_db_client.post(
            f"/api/v1/reviews/users/{user_id}/identity/approve",
            headers=admin_headers,
            json={
                "idempotency_key": "manual-offline-review-v1",
                "submission_version": 1,
                "decision_basis_code": "APPROVED_OFFLINE_IDENTITY_CHECK",
            },
        ),
    )
    if approved.status_code != 200:
        outbox_exists = outbox_audit_database.fetch_value(
            "SELECT count(*) = 1 FROM public.registration_verified_outbox "
            f"WHERE source_ref={user_id}"
        )
        safe_status = {
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "INVALID_CONTRACT",
            503: "UNAVAILABLE",
        }.get(approved.status_code, "OTHER")
        pytest.fail(
            (
                f"STAGE_APPROVE_AFTER_WRITER_{safe_status}"
                if outbox_exists
                else f"STAGE_APPROVE_BEFORE_WRITER_{safe_status}"
            ),
            pytrace=False,
        )
    assert approved.json()["data"]["status"] == "verified", "STAGE_APPROVE_RESPONSE"
    verified_status = real_db_client.get(
        "/api/v1/users/me/identity-verification", headers=member_headers
    )
    _require_status(verified_status, 200, "STAGE_VERIFIED_STATUS_READ")
    _require_equal(
        verified_status.json()["data"]["status"],
        "verified",
        "STAGE_VERIFIED_STATUS_VALUE",
    )
    verified_resubmit = real_db_client.put(
        "/api/v1/users/me/identity-verification",
        json=_submission("verified-resubmit-must-fail"),
        headers=member_headers,
    )
    _require_status(verified_resubmit, 403, "STAGE_VERIFIED_RESUBMIT_FORBIDDEN")
    assert application_database.fetch_value(
        "SELECT count(*) FROM public.identity_verification_submission "
        f"WHERE user_ref={user_id} AND status='verified'"
    ) == 1, "STAGE_VERIFIED_COUNT"
    assert outbox_audit_database.fetch_value(
        "SELECT count(*) FROM public.registration_verified_outbox "
        f"WHERE source_ref={user_id}"
    ) == 1, "STAGE_OUTBOX_COUNT"
    delivered = _stage_call(
        "STAGE_WORKER_DISPATCH", lambda: asyncio.run(_dispatch_once())
    )
    assert (delivered.claimed, delivered.delivered) == (1, 1), "STAGE_WORKER_RESULT"
    counts = _stage_call(
        "STAGE_IDENTITY_BOOTSTRAP_QUERY",
        lambda: application_database.fetch_rows(
            "SELECT "
            f"(SELECT count(*) FROM identity.member_no_allocation WHERE source_ref={user_id}) AS allocations, "
            "(SELECT count(*) FROM identity.member AS m JOIN identity.user_member_self_link AS l "
            f"ON l.member_id=m.member_id WHERE l.user_ref={user_id}) AS members, "
            f"(SELECT count(*) FROM identity.user_member_self_link WHERE user_ref={user_id}) AS links, "
            f"(SELECT count(*) FROM identity.registration_bootstrap_record WHERE user_ref={user_id}) AS bootstraps"
        ),
    )[0]
    assert counts == {
        "allocations": 1,
        "members": 1,
        "links": 1,
        "bootstraps": 1,
    }, "STAGE_IDENTITY_BOOTSTRAP_COUNTS"
    assert outbox_audit_database.fetch_value(
        "SELECT count(*) FROM public.registration_verified_outbox "
        f"WHERE source_ref={user_id} AND status='delivered'"
    ) == 1, "STAGE_OUTBOX_DELIVERED"


def test_拒绝冷却期和权限矩阵保持fail_closed(
    real_db_client, application_database
) -> None:
    registered = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": "13800139802", "password": "Secret12345"},
    )
    _require_status(registered, 200, "STAGE_REGISTER_SECOND")
    user_id = registered.json()["data"]["id"]
    headers = _headers(user_id, "member")
    submitted = real_db_client.put(
        "/api/v1/users/me/identity-verification", json=_submission("second-user-submit-v1"), headers=headers
    )
    _require_status(submitted, 200, "STAGE_SUBMIT_SECOND")
    assert _stage_call(
        "STAGE_REJECT_SUBMISSION_QUERY",
        lambda: application_database.fetch_value(
            "SELECT status = 'submitted' FROM public.identity_verification_submission "
            f"WHERE user_ref={user_id} AND version=1"
        ),
    ), "STAGE_REJECT_SUBMISSION_STATE"
    assert _stage_call(
        "STAGE_REJECT_USER_QUERY",
        lambda: application_database.fetch_value(
            'SELECT verify_status = \'submitted\' FROM public."user" '
            f"WHERE id={user_id}"
        ),
    ), "STAGE_REJECT_USER_STATE"
    rejected = _stage_call(
        "STAGE_REJECT_CALL",
        lambda: real_db_client.post(
            f"/api/v1/reviews/users/{user_id}/identity/reject",
            headers=_headers(REVIEWER_ID, "super_admin"),
            json={
                "submission_version": 1, "idempotency_key": "reject-second-user-v1",
                "reason_code": "OFFLINE_CHECK_FAILED",
            },
        ),
    )
    _require_status(rejected, 200, "STAGE_REJECT_STATUS")
    cooldown = real_db_client.put(
        "/api/v1/users/me/identity-verification", json=_submission("second-user-submit-v2"), headers=headers
    )
    _require_status(cooldown, 429, "STAGE_COOLDOWN")
    forbidden_detail = real_db_client.get(
        f"/api/v1/reviews/users/{user_id}/identity?purpose_code=MANUAL_REVIEW",
        headers={
            **_headers(user_id, "member"),
            "X-Identity-Review-Step-Up": "invalid",
        },
    )
    _require_status(forbidden_detail, 403, "STAGE_FORBIDDEN_DETAIL")


def test_Integration失败诊断只输出固定安全阶段() -> None:
    class _Response:
        status_code = 503

    with pytest.raises(pytest.fail.Exception) as failure:
        _require_status(_Response(), 200, "STAGE_FIXED_SAFE_FAILURE")
    assert str(failure.value) == "STAGE_FIXED_SAFE_FAILURE"


def test_旧JWT声明super_admin但reviewer数据库漂移时全路径fail_closed(
    real_db_client, application_database
) -> None:
    reviewer_id = 94802
    target = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": "13800139803", "password": "Secret12345"},
    )
    _require_status(target, 200, "STAGE_CURRENTNESS_TARGET_REGISTER")
    target_id = target.json()["data"]["id"]
    submitted = real_db_client.put(
        "/api/v1/users/me/identity-verification",
        json=_submission("reviewer-currentness-target-v1"),
        headers=_headers(target_id, "member"),
    )
    _require_status(submitted, 200, "STAGE_CURRENTNESS_TARGET_SUBMIT")

    application_database.execute(
        'INSERT INTO public."user" '
        '(id, phone, password_hash, role, status, verify_status, created_at, updated_at) '
        f"VALUES ({reviewer_id}, '13900094802', 'synthetic', 'super_admin', "
        f"'active', 'verified', '{NOW.isoformat()}', '{NOW.isoformat()}')"
    )
    application_database.execute(
        "INSERT INTO public.platform_org "
        "(id, org_name, org_code, org_type, status, created_at, updated_at) "
        f"VALUES (94811, 'synthetic-org', 'synthetic-org-94811', 'platform', "
        f"'active', '{NOW.isoformat()}', '{NOW.isoformat()}')"
    )
    application_database.execute(
        "INSERT INTO public.tenant "
        "(id, org_id, tenant_code, name, type, province, city, status, created_at, updated_at) "
        f"VALUES (94812, NULL, 'synthetic-tenant-94812', 'synthetic-tenant', "
        f"'institution', 'synthetic', 'synthetic', 'active', "
        f"'{NOW.isoformat()}', '{NOW.isoformat()}'), "
        f"(94813, 94811, 'synthetic-tenant-94813', 'synthetic-org-tenant', "
        f"'institution', 'synthetic', 'synthetic', 'active', "
        f"'{NOW.isoformat()}', '{NOW.isoformat()}')"
    )
    stale_headers = _headers(reviewer_id, "super_admin")
    invalid_states = (
        ("disabled", "super_admin", "NULL"),
        ("active", "province_admin", "NULL"),
        ("active", "super_admin", "94812"),
        ("active", "super_admin", "94813"),
    )
    assert application_database.fetch_value(
        'SELECT status=\'active\' AND role=\'super_admin\' AND tenant_id IS NULL '
        f'FROM public."user" WHERE id={reviewer_id}'
    ), "STAGE_CURRENTNESS_SEED_STATE"
    try:
        for index, (status, role, tenant_id) in enumerate(invalid_states, start=1):
            application_database.execute(
                'UPDATE public."user" '
                f"SET status='{status}', role='{role}', tenant_id={tenant_id}, "
                f"updated_at='{NOW.isoformat()}' WHERE id={reviewer_id}"
            )
            responses = (
                real_db_client.get(
                    "/api/v1/reviews/identity?status=submitted", headers=stale_headers
                ),
                real_db_client.get(
                    f"/api/v1/reviews/users/{target_id}/identity?purpose_code=MANUAL_REVIEW",
                    headers={
                        **stale_headers,
                        "X-Identity-Review-Step-Up": "invalid",
                    },
                ),
                real_db_client.post(
                    f"/api/v1/reviews/users/{target_id}/identity/reject",
                    headers=stale_headers,
                    json={
                        "submission_version": 1,
                        "idempotency_key": f"stale-reviewer-reject-{index}",
                        "reason_code": "OFFLINE_CHECK_FAILED",
                    },
                ),
            )
            for response in responses:
                _require_status(response, 401, f"STAGE_CURRENTNESS_STATE_{index}")
                assert response.json() == {"detail": "ACCESS_TOKEN_STALE"}
                assert response.headers["WWW-Authenticate"] == "Bearer"
                assert response.headers["Cache-Control"] == "no-store"
            assert application_database.fetch_value(
                "SELECT status = 'submitted' FROM public.identity_verification_submission "
                f"WHERE user_ref={target_id} AND version=1"
            ), f"STAGE_CURRENTNESS_ZERO_REJECT_{index}"
            assert application_database.fetch_value(
                "SELECT count(*) FROM public.operation_log "
                f"WHERE object_id={target_id} AND action='identity_sensitive_detail_read'"
            ) == 0, f"STAGE_CURRENTNESS_ZERO_AUDIT_{index}"
    finally:
        # Restore only this independently seeded reviewer, including its original scope.
        application_database.execute(
            'UPDATE public."user" SET status=\'active\', role=\'super_admin\', '
            f"tenant_id=NULL, updated_at='{NOW.isoformat()}' WHERE id={reviewer_id}"
        )
