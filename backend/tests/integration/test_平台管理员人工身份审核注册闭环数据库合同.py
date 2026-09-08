import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import UUID

import pytest


pytestmark = pytest.mark.integration

REVIEWER_REF = 92401
USER_REF = 92402
CLASSIFICATION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29401")
SUBMISSION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29402")
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _headers(user_ref=REVIEWER_REF, role="super_admin", *, province=None, city=None):
    from app.core.security import create_access_token

    token = create_access_token(
        {"sub": str(user_ref), "role": role, "tenant_id": None, "org_id": None,
         "province": province, "city": city}
    )
    return {"Authorization": f"Bearer {token}"}


def _seed(pg_database):
    pg_database.execute(
        'INSERT INTO public."user" '
        "(id, phone, password_hash, role, status, verify_status, created_at, updated_at) "
        "VALUES "
        f"({REVIEWER_REF}, '13900092401', 'synthetic', 'super_admin', "
        f"'active', 'verified', '{NOW.isoformat()}', '{NOW.isoformat()}'), "
        f"({USER_REF}, '13900092402', 'synthetic', 'member', "
        f"'active', 'pending', '{NOW.isoformat()}', '{NOW.isoformat()}')"
    )
    pg_database.execute(
        "INSERT INTO public.identity_verification_submission "
        "(submission_id, user_ref, version, status, real_name_ciphertext, "
        "real_name_nonce, id_card_ciphertext, id_card_nonce, id_card_masked, "
        "encryption_key_id, content_digest, id_card_digest, "
        "idempotency_key_digest, consent_version, submitted_at) VALUES "
        f"('{SUBMISSION_REF}', {USER_REF}, 1, 'submitted', "
        "decode('01', 'hex'), decode('000000000000000000000001', 'hex'), "
        "decode('02', 'hex'), decode('000000000000000000000002', 'hex'), "
        f"'110101********1234', '{os.environ['KG_IDENTITY_PII_KEY_ID']}', "
        f"'{'c' * 64}', '{'d' * 64}', '{'e' * 64}', "
        f"'identity-consent-v1', '{NOW.isoformat()}')"
    )
    pg_database.execute(
        "INSERT INTO public.user_account_classification_decision "
        "(decision_ref, user_ref, facts_version, classification_version, "
        "account_class, decision_basis_code, decided_at) VALUES "
        f"('{CLASSIFICATION_REF}', {USER_REF}, 17, 1, 'natural_person', "
        f"'trusted_provisioning', '{NOW.isoformat()}')"
    )


async def _dispatch_once():
    from app.tasks.registration_outbox_worker_bootstrap import (
        RegistrationOutboxWorkerBootstrap,
    )

    async with RegistrationOutboxWorkerBootstrap.from_environment() as runtime:
        return await runtime.dispatch_once(
            lease_owner="registration-api-contract-worker",
            limit=1,
        )


def test_平台人工审核经持久发件箱完成注册闭环且稳定重放(
    real_db_client, pg_database
):
    _seed(pg_database)
    payload = {
        "idempotency_key": "platform-manual-review-92402-v1",
        "submission_version": 1,
        "decision_basis_code": "APPROVED_OFFLINE_IDENTITY_CHECK",
    }

    created = real_db_client.post(
        f"/api/v1/reviews/users/{USER_REF}/identity/approve",
        headers=_headers(),
        json=payload,
    )
    assert created.status_code == 200
    created_result = created.json()["data"]
    assert created_result["user_id"] == USER_REF
    assert created_result["status"] == "verified"
    assert created_result["replayed"] is False

    delivered = asyncio.run(_dispatch_once())
    assert (delivered.claimed, delivered.delivered) == (1, 1)

    replayed = real_db_client.post(
        f"/api/v1/reviews/users/{USER_REF}/identity/approve",
        headers=_headers(),
        json=payload,
    )
    assert replayed.status_code == 200
    replayed_result = replayed.json()["data"]
    assert replayed_result["replayed"] is True
    assert replayed_result["decision_ref"] == created_result["decision_ref"]

    counts = pg_database.fetch_rows(
        "SELECT "
        f"(SELECT count(*) FROM identity.member_no_allocation WHERE source_ref={USER_REF}) AS allocations, "
        f"(SELECT count(*) FROM identity.user_member_self_link WHERE user_ref={USER_REF}) AS links, "
        f"(SELECT count(*) FROM identity.registration_bootstrap_record WHERE user_ref={USER_REF}) AS bootstraps, "
        f"(SELECT count(*) FROM public.registration_verified_outbox WHERE source_ref={USER_REF}) AS outboxes"
    )[0]
    assert counts == {
        "allocations": 1,
        "links": 1,
        "bootstraps": 1,
        "outboxes": 1,
    }

    empty = asyncio.run(_dispatch_once())
    assert (empty.claimed, empty.delivered) == (0, 0)


@pytest.mark.parametrize(
    ("reviewer_ref", "role"),
    ((92403, "province_admin"), (92404, "city_admin")),
)
def test_无权角色在WriterSession创建前拒绝(
    real_db_client, pg_database, monkeypatch, reviewer_ref, role
):
    from app.core import database
    from app.core.config import get_settings

    pg_database.execute(
        'INSERT INTO public."user" (id, phone, password_hash, role, status) '
        "VALUES (92403, '13900092403', 'synthetic', 'province_admin', 'active'), "
        "(92404, '13900092404', 'synthetic', 'city_admin', 'active') "
        "ON CONFLICT (id) DO NOTHING"
    )

    monkeypatch.setattr(
        database,
        "get_verification_writer_session_factory",
        lambda: pytest.fail("Writer Session Factory must not be created"),
    )
    context = {
        "92403": {"province": "Zhejiang"},
        "92404": {"province": "Zhejiang", "city": "Hangzhou"},
    }
    with monkeypatch.context() as scoped:
        scoped.setenv("KG_AUTH_CONTEXT_MAP", json.dumps(context))
        get_settings.cache_clear()
        try:
            response = real_db_client.post(
                f"/api/v1/reviews/users/{USER_REF}/identity/approve",
                headers=_headers(
                    reviewer_ref, role, province="Zhejiang",
                    city="Hangzhou" if role == "city_admin" else None,
                ),
                json={
                    "idempotency_key": "forbidden-review",
                    "submission_version": 1,
                    "decision_basis_code": "APPROVED_OFFLINE_IDENTITY_CHECK",
                },
            )
            assert response.status_code == 403
            body = response.json()
            assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
            assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
                "FORBIDDEN", "request rejected", [], False,
            )
            assert response.headers["X-Request-ID"] == body["request_id"]
            assert response.headers["Cache-Control"] == "no-store, private"
            assert response.headers["Pragma"] == "no-cache"
        finally:
            scoped.undo()
            get_settings.cache_clear()


def test_自我审核由应用服务拒绝且不产生数据库写入(
    real_db_client, pg_database, monkeypatch
):
    from app.modules.auth.manual_identity_review_application import (
        PlatformAdminManualIdentityReviewService,
        PlatformIdentitySubmissionReviewService,
    )

    pg_database.execute(
        'INSERT INTO public."user" (id,phone,password_hash,role,status) VALUES '
        "(92405,'13900092405','synthetic','super_admin','active')"
    )
    calls = {"submission_guard": 0, "transition_service": 0}
    original_guard = PlatformIdentitySubmissionReviewService._require_reviewer
    original_execute = PlatformAdminManualIdentityReviewService.execute

    def observe_guard(current_user, user_ref=None):
        calls["submission_guard"] += 1
        return original_guard(current_user, user_ref)

    async def observe_execute(self, **kwargs):
        calls["transition_service"] += 1
        return await original_execute(self, **kwargs)

    monkeypatch.setattr(
        PlatformIdentitySubmissionReviewService, "_require_reviewer",
        staticmethod(observe_guard),
    )
    monkeypatch.setattr(PlatformAdminManualIdentityReviewService, "execute", observe_execute)
    before = pg_database.fetch_value(
        "SELECT count(*) FROM public.registration_verified_outbox"
    )
    response = real_db_client.post(
        "/api/v1/reviews/users/92405/identity/approve",
        headers=_headers(92405),
        json={
            "idempotency_key": "self-review",
            "submission_version": 1,
            "decision_basis_code": "APPROVED_OFFLINE_IDENTITY_CHECK",
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
    assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
        "FORBIDDEN", "request rejected", [], False,
    )
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["Pragma"] == "no-cache"
    # The formal submission service rejects self-review before transition execution.
    assert calls == {"submission_guard": 1, "transition_service": 0}
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.registration_verified_outbox"
    ) == before
